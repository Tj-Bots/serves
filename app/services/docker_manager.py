"""
שכבת ה-runtime שמריצה את בוטי המשתמשים בבידוד.

היישום האמיתי (production) הוא DockerRuntime: קונטיינר Docker פר-אפליקציה,
ללא root, cap_drop=ALL, מגבלות זיכרון/CPU/pids, ורשת sandbox נפרדת שעליה
חוסמים פורטי BitTorrent ב-iptables (ראה scripts/setup_firewall.sh). מכיוון
שלמשתמש אין sudo/apt בתוך הקונטיינר, פיזית אי אפשר להתקין ffmpeg או כלים
ברמת מערכת - רק pip install לספריות פייתון.

כשאין דוקר זמין (למשל בסביבת פיתוח/בדיקה של הפלטפורמה עצמה) נופלים ל-
LocalProcessRuntime שמריץ כל אפליקציה בתוך virtualenv נפרד - נוח לפיתוח,
אבל *ללא* בידוד אמיתי (אין הגבלת משאבים/רשת/הרשאות). אסור להשתמש בו
בפרודקשן - ראו אזהרה למטה וב-DISABLE_DOCKER ב-.env.
"""

from __future__ import annotations

import abc
import asyncio
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

from app.config import settings

CONTAINER_PREFIX = "serves-app-"


def container_name(app_id: int) -> str:
    return f"{CONTAINER_PREFIX}{app_id}"


class RuntimeError_(Exception):
    pass


class Runtime(abc.ABC):
    @abc.abstractmethod
    def ensure_ready(self) -> None: ...

    @abc.abstractmethod
    def start(
        self, app_id: int, code_dir: Path, requirements_file: str, run_command: str, env_vars: dict,
        memory_mb: int, cpu_cores: float, bandwidth_mbps: float = 0, use_dockerfile: bool = False,
    ) -> str: ...

    @abc.abstractmethod
    def stop(self, handle: str) -> None: ...

    @abc.abstractmethod
    def remove(self, handle: str) -> None: ...

    @abc.abstractmethod
    def is_running(self, handle: str) -> bool: ...

    @abc.abstractmethod
    def stream_logs(self, handle: str, on_line: Callable[[str], None], on_exit: Callable[[int], None]) -> None:
        """חוסם - יש להריץ ב-thread נפרד. קורא ל-on_line לכל שורת פלט,
        ול-on_exit(code) כשהתהליך מסתיים."""
        ...

    @abc.abstractmethod
    def get_internal_address(self, handle: str) -> tuple[str, int] | None:
        """כתובת (host, port) שממנה אפשר להזרים תעבורת HTTP לאפליקציה,
        אם היא בכלל מריצה שרת אינטרנט על settings.APP_PORT. None אם
        התהליך/קונטיינר לא רץ."""
        ...


class DockerRuntime(Runtime):
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def ensure_ready(self) -> None:
        import docker

        # רשת מבודדת לכל אפליקציות ה-sandbox, עם subnet קבוע כדי ש-iptables
        # יוכל לחסום פורטי טורנט מולה בוודאות (ראה scripts/setup_firewall.sh)
        try:
            self.client.networks.get(settings.SANDBOX_NETWORK)
        except docker.errors.NotFound:
            import docker as docker_module

            ipam_pool = docker_module.types.IPAMPool(subnet=settings.SANDBOX_SUBNET)
            ipam_config = docker_module.types.IPAMConfig(pool_configs=[ipam_pool])
            self.client.networks.create(
                settings.SANDBOX_NETWORK, driver="bridge", ipam=ipam_config
            )

        # בניית תמונת הבסיס אם היא לא קיימת עדיין
        try:
            self.client.images.get(settings.BASE_IMAGE)
        except docker.errors.ImageNotFound:
            docker_dir = Path(__file__).resolve().parent.parent.parent / "docker"
            self.client.images.build(path=str(docker_dir), dockerfile="base.Dockerfile", tag=settings.BASE_IMAGE)

    def start(
        self, app_id: int, code_dir: Path, requirements_file: str, run_command: str, env_vars: dict,
        memory_mb: int, cpu_cores: float, bandwidth_mbps: float = 0, use_dockerfile: bool = False,
    ) -> str:
        import docker

        # code_dir הוא כבר mount point של loop device בגודל קבוע (נאכף
        # ב-app/services/deploy.py::_ensure_app_volume).
        name = container_name(app_id)
        try:
            old = self.client.containers.get(name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        env = dict(env_vars or {})
        # שמור - אם קוד המשתמש מריץ שרת אינטרנט, עליו להאזין כאן (על
        # 0.0.0.0) כדי שיהיה נגיש דרך /open/<id>. דורס כל PORT שהמשתמש
        # הגדיר בעצמו כדי שהפרוקסי תמיד ידע לאן לפנות.
        env["PORT"] = str(settings.APP_PORT)

        run_kwargs = dict(
            name=name,
            detach=True,
            environment=env,
            network=settings.SANDBOX_NETWORK,
            mem_limit=f"{memory_mb}m",
            memswap_limit=f"{memory_mb}m",
            nano_cpus=int(cpu_cores * 1_000_000_000),
            pids_limit=100,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            restart_policy={"Name": "no"},
        )

        if use_dockerfile:
            # מצב מתקדם: בונים תמונה משלה מה-Dockerfile שבשורש קוד המקור
            # (במקום תמונת הבסיס המשותפת) - נועד למי שצריך חבילות מערכת
            # שהתמונה המשותפת לא כוללת (למשל ffmpeg לצרכים לגיטימיים).
            # שים לב: זו הרחבה מכוונת של המדיניות - Dockerfile מותאם-אישית
            # לא נסרק ע"י security_policy.py (זה לא ישים ל-Dockerfile),
            # וגם לא נאכף עליה read_only/uid קבוע כמו על תמונת הבסיס, כי
            # תמונות מותאמות-אישית מנהלות את זה בעצמן. שאר ההגנות (ללא
            # capabilities, ללא הרשאות חדשות, מגבלות משאבים, רשת מבודדת
            # עם חסימת פורטי BitTorrent ב-iptables) עדיין נאכפות במלואן.
            image_tag = f"serves-app-{app_id}:latest"
            self.client.images.build(path=str(code_dir), tag=image_tag, rm=True)
            container = self.client.containers.run(image_tag, **run_kwargs)
        else:
            env["REQUIREMENTS_FILE"] = requirements_file or "requirements.txt"
            env["RUN_COMMAND"] = run_command
            container = self.client.containers.run(
                settings.BASE_IMAGE,
                working_dir="/app",
                volumes={str(code_dir): {"bind": "/app", "mode": "rw"}},
                user="botuser",
                read_only=True,
                tmpfs={"/tmp": "size=256m,uid=1000,gid=1000"},
                **run_kwargs,
            )

        if bandwidth_mbps:
            from app.services import bandwidth

            bandwidth.apply_limit(app_id, container.id, bandwidth_mbps)

        return container.id

    def stop(self, handle: str) -> None:
        import docker

        try:
            self.client.containers.get(handle).stop(timeout=10)
        except docker.errors.NotFound:
            pass

    def remove(self, handle: str) -> None:
        import docker

        try:
            self.client.containers.get(handle).remove(force=True)
        except docker.errors.NotFound:
            pass

    def is_running(self, handle: str) -> bool:
        import docker

        try:
            c = self.client.containers.get(handle)
            c.reload()
            return c.status == "running"
        except docker.errors.NotFound:
            return False

    def stream_logs(self, handle: str, on_line, on_exit) -> None:
        import docker

        try:
            container = self.client.containers.get(handle)
        except docker.errors.NotFound:
            on_exit(-1)
            return

        try:
            for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=True):
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    on_line(line)
        except Exception as exc:  # noqa: BLE001 - זרם לוגים שנקטע, נדווח כשגיאה בלוג
            on_line(f"[serves] log stream error: {exc}")

        try:
            container.reload()
            exit_code = container.attrs.get("State", {}).get("ExitCode", -1)
        except docker.errors.NotFound:
            exit_code = -1
        on_exit(exit_code)

    def get_internal_address(self, handle: str) -> tuple[str, int] | None:
        import docker

        try:
            container = self.client.containers.get(handle)
            container.reload()
        except docker.errors.NotFound:
            return None
        if container.status != "running":
            return None

        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        net = networks.get(settings.SANDBOX_NETWORK)
        ip = net.get("IPAddress") if net else None
        if not ip:
            return None
        return (ip, settings.APP_PORT)


class LocalProcessRuntime(Runtime):
    """DEV ONLY - אין בידוד, אין הגבלת משאבים/רשת. לא לשימוש בפרודקשן."""

    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}

    def ensure_ready(self) -> None:
        return

    def start(
        self, app_id: int, code_dir: Path, requirements_file: str, run_command: str, env_vars: dict,
        memory_mb: int, cpu_cores: float, bandwidth_mbps: float = 0, use_dockerfile: bool = False,
    ) -> str:
        # DEV ONLY - memory_mb/cpu_cores/bandwidth_mbps לא נאכפים כאן, ראו אזהרת המחלקה למעלה.
        if use_dockerfile:
            raise RuntimeError(
                "Dockerfile-based apps require real Docker (DISABLE_DOCKER must be off) - "
                "not supported by the LocalProcessRuntime dev fallback."
            )
        import os

        venv_dir = code_dir.parent / "venv"
        if not venv_dir.exists():
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

        pip = str(venv_dir / "bin" / "pip")
        req_path = code_dir / requirements_file
        if req_path.exists():
            subprocess.run([pip, "install", "--no-cache-dir", "-r", str(req_path)], cwd=code_dir, check=False)

        env = os.environ.copy()
        env.update({k: str(v) for k, v in (env_vars or {}).items()})
        env["PATH"] = f"{venv_dir / 'bin'}:{env['PATH']}"
        env["PORT"] = str(settings.APP_PORT)

        proc = subprocess.Popen(
            shlex.split(run_command) if not any(c in run_command for c in "|&;<>") else run_command,
            shell=any(c in run_command for c in "|&;<>"),
            cwd=code_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        handle = str(proc.pid)
        self._procs[handle] = proc
        return handle

    def stop(self, handle: str) -> None:
        proc = self._procs.get(handle)
        if proc and proc.poll() is None:
            proc.terminate()

    def remove(self, handle: str) -> None:
        proc = self._procs.pop(handle, None)
        if proc and proc.poll() is None:
            proc.kill()

    def is_running(self, handle: str) -> bool:
        proc = self._procs.get(handle)
        return bool(proc and proc.poll() is None)

    def stream_logs(self, handle: str, on_line, on_exit) -> None:
        proc = self._procs.get(handle)
        if not proc:
            on_exit(-1)
            return
        if proc.stdout:
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
        exit_code = proc.wait()
        on_exit(exit_code)

    def get_internal_address(self, handle: str) -> tuple[str, int] | None:
        if not self.is_running(handle):
            return None
        return ("127.0.0.1", settings.APP_PORT)


runtime: Runtime = LocalProcessRuntime() if settings.DISABLE_DOCKER else DockerRuntime()
