"""
הגבלת רוחב פס פר-אפליקציה, לפי התוכנית של הבעלים (bandwidth_mbps ב-PLANS,
app/config.py). ל-Docker אין הגבלת רשת מובנית (בניגוד ל-mem_limit/nano_cpus),
אז מיישמים את זה ברמת המארח עם tc (traffic control) על ה-veth של הקונטיינר
בתוך רשת ה-sandbox המבודדת:

- הורדה (traffic שזורם *לתוך* הקונטיינר): qdisc htb ישירות על ה-veth בצד
  המארח - ה"egress" (root qdisc) של אותו veth הוא בדיוק מה שהמארח שולח
  לכיוון הקונטיינר.
- העלאה (traffic שיוצא *מהקונטיינר*): tc לא תומך בהגבלת egress ישירה על
  ingress, אז מפנים (tc mirred) את ה-ingress של אותו veth למכשיר ifb ייעודי
  ומגבילים שם ברגיל htb (הטריק הסטנדרטי להגבלת upload).

זה best-effort: אם tc/ifb/nsenter לא זמינים או נכשלים (הרשאות, מודול ליבה
לא טעון וכו') - רק נרשמת אזהרה בלוג, בלי להפיל את הפריסה עצמה. ניתן
לכיבוי מלא עם BANDWIDTH_LIMIT_ENABLED=0 ב-.env.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import settings

logger = logging.getLogger("serves.bandwidth")

# כמות מכשירי ifb שנטענים מראש (modprobe ifb numifbs=N) - כל אפליקציה
# משתמשת ב-ifb{app_id % NUM_IFB} להגבלת ה-upload שלה. בפלטפורמה אישית
# בקנה מידה קטן (VM יחיד) זה מספיק בלי לדרוש רישום דינמי - אם יותר מ-
# NUM_IFB אפליקציות רצות בו-זמנית, אפליקציות שמתנגשות על אותו מכשיר
# ifb "חולקות" את מגבלת ה-upload ביניהן (עדיין טוב יותר מבלי הגבלה בכלל).
NUM_IFB = 64

_ifb_ready = False


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _ensure_ifb_module() -> bool:
    global _ifb_ready
    if _ifb_ready:
        return True
    _run(["modprobe", "ifb", f"numifbs={NUM_IFB}"])
    for i in range(NUM_IFB):
        _run(["ip", "link", "set", "dev", f"ifb{i}", "up"])
    if _run(["ip", "link", "show", "ifb0"]).returncode != 0:
        logger.warning("ifb kernel module not available - upload bandwidth limiting disabled")
        return False
    _ifb_ready = True
    return True


def _host_veth_for_container(container_id: str) -> str | None:
    """מוצא את שם ממשק ה-veth בצד המארח שמתאים ל-eth0 בתוך הקונטיינר, ע"י
    התאמת ה-iflink (ifindex של הצד השני) שנקרא מבפנים ל-ifindex של ממשקי
    המארח - מזהי ifindex גלובליים למארח (לא לכל netns), אז ההתאמה חד-ערכית."""
    pid_result = _run(["docker", "inspect", "-f", "{{.State.Pid}}", container_id])
    pid = pid_result.stdout.strip()
    if pid_result.returncode != 0 or not pid or pid == "0":
        return None

    iflink_result = _run(["nsenter", "-t", pid, "-n", "cat", "/sys/class/net/eth0/iflink"])
    if iflink_result.returncode != 0:
        return None
    target_ifindex = iflink_result.stdout.strip()

    for ifindex_path in Path("/sys/class/net").glob("*/ifindex"):
        try:
            if ifindex_path.read_text().strip() == target_ifindex:
                return ifindex_path.parent.name
        except OSError:
            continue
    return None


def _mbit(mbps: float) -> str:
    return f"{max(int(mbps * 1000), 8)}kbit"


def _shape_download(veth: str, mbps: float) -> None:
    rate = _mbit(mbps)
    _run(["tc", "qdisc", "del", "dev", veth, "root"])
    _run(["tc", "qdisc", "add", "dev", veth, "root", "handle", "1:", "htb", "default", "10"], check=True)
    _run(
        ["tc", "class", "add", "dev", veth, "parent", "1:", "classid", "1:10", "htb", "rate", rate, "ceil", rate],
        check=True,
    )


def _shape_upload(veth: str, ifb: str, mbps: float) -> None:
    rate = _mbit(mbps)
    _run(["tc", "qdisc", "del", "dev", veth, "ingress"])
    _run(["tc", "qdisc", "add", "dev", veth, "ingress"], check=True)
    _run(
        [
            "tc", "filter", "add", "dev", veth, "parent", "ffff:", "protocol", "ip",
            "u32", "match", "u32", "0", "0", "action", "mirred", "egress", "redirect", "dev", ifb,
        ],
        check=True,
    )
    _run(["tc", "qdisc", "del", "dev", ifb, "root"])
    _run(["tc", "qdisc", "add", "dev", ifb, "root", "handle", "1:", "htb", "default", "10"], check=True)
    _run(
        ["tc", "class", "add", "dev", ifb, "parent", "1:", "classid", "1:10", "htb", "rate", rate, "ceil", rate],
        check=True,
    )


def apply_limit(app_id: int, container_id: str, mbps: float) -> None:
    """נקראת אחרי שקונטיינר עולה (DockerRuntime.start) - best-effort, לא
    זורקת exception (כשל בהגבלת רוחב פס לא אמור להפיל פריסה של בוט)."""
    if not settings.BANDWIDTH_LIMIT_ENABLED or settings.DISABLE_DOCKER or mbps <= 0:
        return
    try:
        veth = _host_veth_for_container(container_id)
        if not veth:
            logger.warning("could not find host veth for container %s - bandwidth limit skipped", container_id)
            return
        _shape_download(veth, mbps)
        if _ensure_ifb_module():
            _shape_upload(veth, f"ifb{app_id % NUM_IFB}", mbps)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to apply bandwidth limit for app %s: %s", app_id, exc)
