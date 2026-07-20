# Serves

פלטפורמת אחסון לבוטי טלגרם, בסגנון Heroku/Render/Koyeb - עם תוכנית חינמית
שבה למשתמשים **אין הרשאות root**, אי אפשר להוריד טורנטים, ואי אפשר להתקין
ffmpeg או כל חבילת מערכת אחרת. אפשר רק להתקין ספריות פייתון (pip).

## איך זה עובד

1. משתמש נרשם עם מייל+סיסמא ומאשר תקנון (מקושר מ-`TERMS_URL`, כרגע
   `https://boss-server-bot.online/תקנון.html`).
2. בתוכנית החינמית אפשר ליצור אפליקציה אחת (בוט טלגרם).
3. יוצרים אפליקציה עם: קישור לריפו ב-GitHub, שם קובץ הספריות (ברירת מחדל
   `requirements.txt`), ופקודת הרצה (למשל `python bot.py`).
4. הפלטפורמה משכפלת את הריפו, מריצה אותו בתוך קונטיינר Docker מבודד,
   ומזרימה את הלוגים בזמן אמת למסך כהה בסגנון טרמינל (WebSocket).
5. אפשר לעצור, לפרוס מחדש, למחוק, ולהגדיר משתני סביבה (כמו `.env`) מתוך
   העמוד של האפליקציה.

## מודל האבטחה - איך אין root ואי אפשר ffmpeg/טורנטים

כל אפליקציה רצה בקונטיינר Docker נפרד עם:

- **משתמש לא-root קבוע** בתוך הקונטיינר (`botuser`, uid 1000) - אין
  `sudo`/`apt` בכלל, כך שפיזית אי אפשר להתקין ffmpeg או כל בינארי מערכת.
  אפשר להתקין רק ספריות פייתון עם `pip` (לתוך `/app/.local`).
- **מערכת קבצים read-only** מלבד `/app` (קוד הבוט) ו-`/tmp` - אין דרך
  לשנות קבצי מערכת גם אם היה באג.
- **`cap_drop=ALL` + `no-new-privileges`** - אין אפשרות להעלות הרשאות.
- **הגבלות משאבים** (תוכנית חינמית): 256MB זיכרון, חצי ליבת CPU, עד
  100 תהליכים (`pids_limit`). מוגדר לפי `FREE_MEMORY_MB`/`FREE_CPU_CORES`
  ב-`.env`.
- **רשת sandbox נפרדת** (`serves_sandbox`, subnet קבוע) עם כללי
  `iptables` (`scripts/setup_firewall.sh`) שחוסמים פורטי BitTorrent
  נפוצים ביציאה. זו שכבת הגנה נוספת - ההגנה העיקרית היא חוסר הרשאות
  המערכת כאמור למעלה.
- **רשימת חסימה נוספת** (`app/security_policy.py`) שדוחה דיפלוי שמנסה
  להתקין ספריות pip שעוטפות ffmpeg/BitTorrent (למשל `imageio-ffmpeg`,
  `libtorrent`), או פקודת הרצה עם `apt`/`sudo`/קישורי `magnet:`.

⚠️ **מגבלה ידועה בפריסה הנוכחית**: מגבלת האחסון (4GB, `FREE_DISK_MB`)
עדיין לא נאכפת כ"hard quota" ברמת מערכת הקבצים (למשל loopback device
עם `mkfs.ext4` בגודל קבוע) - כרגע היא רק ערך תצורה. אם רוצים אכיפה
אמיתית ברמת דיסק, זה השלב הבא (ראה TODO בקוד `docker_manager.py`).

## דרישות

שרת Ubuntu 22.04 (בדיוק כמו ה-VM שתיארת: 16 ליבות, 24GB RAM, דיסק
100GB+32GB - יותר ממספיק). נדרש Docker (הסקריפט מתקין אוטומטית).

## התקנה על השרת (VM 100)

```bash
git clone <כתובת-הריפו-הזה> serves
cd serves
sudo bash scripts/install.sh            # בלי דומיין - זמין ב-http://IP:8000
# או, אם יש דומיין/סאבדומיין לפלטפורמה:
sudo bash scripts/install.sh panel.example.com
```

הסקריפט:
1. מתקין Docker, Python, nginx, iptables-persistent.
2. מעתיק את הקוד ל-`/opt/serves`, יוצר virtualenv ומתקין תלויות.
3. יוצר `/opt/serves/.env` עם `SECRET_KEY` אקראי (ערכו אם צריך לשנות
   את `TERMS_URL` או את מגבלות התוכנית החינמית).
4. בונה את תמונת ה-Docker הבסיסית להרצת בוטים (`docker/base.Dockerfile`).
5. מגדיר firewall לחסימת פורטי טורנט (`scripts/setup_firewall.sh`).
6. מתקין ומפעיל שירות systemd בשם `serves` (`systemctl status serves`).
7. אם ניתן דומיין - מגדיר nginx reverse proxy (כולל תמיכה ב-WebSocket
   ללוגים). להשלמת HTTPS יש להריץ בעצמכם:
   ```bash
   apt-get install -y certbot python3-certbot-nginx
   certbot --nginx -d panel.example.com
   ```

לוגים של הפלטפורמה עצמה: `journalctl -u serves -f`

## פיתוח מקומי (בלי Docker)

לצורך פיתוח בלבד, אפשר להריץ בלי Docker דרך `LocalProcessRuntime` (כל
אפליקציה רצה ב-virtualenv נפרד על המחשב המקומי, **ללא בידוד אמיתי** -
לא לשימוש בפרודקשן):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
DISABLE_DOCKER=1 SECRET_KEY=dev uvicorn app.main:app --reload
```

## מבנה הקוד

```
app/
  main.py               נקודת הכניסה של FastAPI
  models.py             User, BotApp (SQLAlchemy)
  auth.py                סשן/סיסמאות
  security_policy.py       רשימות חסימה (ffmpeg/torrent)
  services/
    docker_manager.py       הרצת קונטיינרים מבודדים (או LocalProcessRuntime לפיתוח)
    deploy.py                 clone + פריסה + מעקב סטטוס
    log_broadcaster.py         הזרמת לוגים בזמן אמת + היסטוריה בדיסק
  routers/                  auth, apps (dashboard/CRUD), logs_ws (WebSocket)
  templates/, static/         העיצוב הכהה
docker/
  base.Dockerfile, entrypoint.sh    תמונת הריצה של בוטי המשתמשים
scripts/
  install.sh, setup_firewall.sh, serves.service, nginx-serves.conf.template
```

## מה עוד לא בפנים (הצעות להמשך)

- אכיפת quota קשיח לדיסק (4GB) ברמת קונטיינר/loop device.
- אימות מייל בהרשמה, שחזור סיסמא.
- תוכניות בתשלום מעבר לחינמית.
- הגבלת קצב (rate limiting) על יצירת/פריסת אפליקציות.
