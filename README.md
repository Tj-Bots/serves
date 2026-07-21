# Serves

פלטפורמת אחסון לבוטי טלגרם, בסגנון Heroku/Render/Koyeb - עם תוכנית חינמית
שבה למשתמשים **אין הרשאות root**, אי אפשר להוריד טורנטים, ואי אפשר להתקין
ffmpeg או כל חבילת מערכת אחרת. אפשר רק להתקין ספריות פייתון (pip).

## איך זה עובד

1. משתמש נרשם עם מייל+סיסמא ומאשר תקנון (מקושר מ-`TERMS_URL`, כרגע
   `https://boss-server-bot.online/תקנון.html`), ומאמת את המייל עם קוד בן
   6 ספרות שנשלח אליו (ראו "אימות אימייל" למטה).
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
  אפשר להתקין רק ספריות פייתון עם `pip` (לתוך `/app/.local`). הפלטפורמה
  עצמה משכפלת את הריפו כ-root על המארח ולכן עושה `chown` לתיקיית הקוד
  ל-uid/gid 1000 (`SANDBOX_UID`/`SANDBOX_GID` ב-`.env`, חייב להתאים
  ל-uid של `botuser` ב-`docker/base.Dockerfile`) לפני הרצת הקונטיינר -
  אחרת ה-pip install נכשל עם permission denied.
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

## אימות אימייל (SMTP)

בהרשמה נשלח קוד בן 6 ספרות שתקף ל-10 דקות (`VERIFICATION_CODE_TTL_MINUTES`),
ובלעדיו אי אפשר להיכנס לדשבורד או ליצור אפליקציות. כדי שהמייל *באמת* יישלח
יש להגדיר ב-`/opt/serves/.env`:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<App Password - לא הסיסמא הרגילה של Gmail>
SMTP_FROM=Serves <no-reply@boss-server-bot.online>
SMTP_USE_TLS=true
```

ל-Gmail חובה ליצור "App Password" ייעודי (לא הסיסמא הרגילה) דרך הגדרות
האבטחה של החשבון. אחרי שינוי `.env`: `sudo systemctl restart serves`.

**אם 587 לא עובד** (חלק מספקי אחסון חוסמים אותו יוצא כברירת מחדל), נסו
465 עם SSL מוצפן-מהתחלה במקום STARTTLS:
```
SMTP_PORT=465
SMTP_USE_TLS=false
SMTP_USE_SSL=true
```

אם `SMTP_HOST` נשאר ריק, הקוד לא נשלח בפועל אלא רק נכתב ללוגים של השרת
(`journalctl -u serves -f`) - שימושי לבדיקות, לא לפרודקשן.

**אם ה-SMTP עוד לא עובד** ורוצים לבדוק את שאר האתר בלי שהאימות יחסום -
אפשר להגדיר זמנית ב-`.env`:
```
REQUIRE_EMAIL_VERIFICATION=false
```
ואז `sudo systemctl restart serves`. כל משתמש (גם קיימים) ייחשב מאומת
אוטומטית. חשוב להחזיר ל-`true` לפני שנותנים למשתמשים אמיתיים להירשם.

## שפה ומצב תצוגה

לוחצים על אייקון ה-☰ בתפריט העליון ונפתח תפריט עם: מעבר בין אפליקציות,
מצב כהה/בהיר, מעבר עברית/אנגלית, והתנתקות. הבחירה נשמרת בסשן (שפה)
וב-localStorage של הדפדפן (ערכת נושא), בלי צורך בהגדרה נוספת בשרת.
הטרמינל של הלוגים נשאר תמיד כהה גם במצב בהיר (כמו כל טרמינל אמיתי).
העיצוב כולו (כפתורים, אייקונים, צבעי badge לפי סטטוס) מתאים את עצמו
לשני המצבים - זה לא רק צבע רקע שמתחלף.

## משתני סביבה

אפשר להגדיר משתני סביבה (כמו קובץ `.env`) כבר בטופס יצירת האפליקציה
(שדה מתקפל, אופציונלי), או בכל שלב מאוחר יותר מתוך עמוד האפליקציה -
בשני המקרים צריך "פריסה מחדש" אחרי שינוי כדי שהערכים ייכנסו לתוקף.

## העתקת לוגים

בעמוד האפליקציה, ליד "לוגים בזמן אמת" יש כפתור העתקה שמעתיק את כל הלוג
המוצג ללוח (clipboard) בלחיצה אחת.

## קישור לבוט בטלגרם

כשמפרסים אפליקציה, הפלטפורמה סורקת את משתני הסביבה שהוגדרו ומחפשת ערך
שנראה כמו טוקן של בוט טלגרם (`מספרים:אותיות/מספרים`, בפורמט הרגיל של
BotFather). אם נמצא טוקן תקין, נשלחת בקשת `getMe` ל-Telegram API כדי
לגלות את שם המשתמש של הבוט - ואז מופיע קישור "פתח בטלגרם" בעמוד
האפליקציה שמעביר ישירות ל-`https://t.me/<username>`. זה קורה אוטומטית,
בלי צורך לקרוא למשתנה דווקא `TELEGRAM_BOT_TOKEN`.

## חיבור דומיין (nginx reverse proxy)

אם כבר התקנתם בלי דומיין (`sudo bash scripts/install.sh` בלי פרמטר),
האתר עלה על פורט 8000 בלבד. כדי לחבר דומיין ולהעלים את `:8000` מהכתובת:

1. **בדקו אם כבר יש קונפיגורציית nginx/apache לדומיין** (למשל אם השרת
   מגיע עם דף ברירת מחדל בתיקייה כמו `/var/www/<domain>/html`):
   ```bash
   sudo grep -rl "הדומיין-שלכם" /etc/nginx/sites-enabled/ /etc/nginx/sites-available/ 2>/dev/null
   sudo ls /etc/apache2/sites-enabled/ 2>/dev/null   # אם יש בכלל Apache מותקן
   ```
   אם יש קובץ קיים שמצביע לתיקיית ה-`html` הסטטית, צריך לנטרל אותו כדי
   שלא יתנגש עם ה-proxy החדש (nginx לא תמיד "יודע" איזה מהשניים לבחור):
   ```bash
   sudo rm /etc/nginx/sites-enabled/<שם-הקובץ-הקיים>   # ה-sites-available נשאר כגיבוי
   ```
2. **הריצו את install.sh עם הדומיין** - זה יוצר קונפיגורציית nginx חדשה
   שמפנה (`proxy_pass`) לאפליקציה על פורט 8000, כולל תמיכה ב-WebSocket
   ללוגים:
   ```bash
   cd ~/serves && git pull
   sudo bash scripts/install.sh teleboss.online
   ```
3. **HTTPS** (מומלץ מאוד - אחרת סיסמאות/עוגיות עוברות בטקסט גלוי):
   ```bash
   sudo apt-get install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d teleboss.online
   ```
   Certbot גם יגדיר הפניה אוטומטית מ-HTTP ל-HTTPS.

אחרי זה `https://teleboss.online` יציג את Serves ישירות, בלי `:8000`.

## עדכון מותקנת קיימת

אם כבר התקנת גרסה קודמת (בלי אימות אימייל) ויש לך `/opt/serves/data/serves.db`
ישן - הטבלאות החדשות (`is_verified` וכו') לא ייווצרו אוטומטית בתוך טבלה
קיימת (אין כאן migrations, רק `create_all`). הכי פשוט בשלב הזה (לפני
שיש משתמשים אמיתיים):

```bash
cd ~/serves && git pull        # למשוך את השינויים האחרונים לתיקיית המקור
sudo systemctl stop serves
sudo rm /opt/serves/data/serves.db
sudo bash scripts/install.sh   # מעתיק את הקוד המעודכן ל-/opt/serves ומפעיל מחדש
```

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
