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

- **מגבלת אחסון אמיתית (hard quota)**: `/app` בכל קונטיינר הוא לא סתם
  תיקייה על דיסק המארח - זו מערכת קבצים ext4 בגודל קבוע (`FREE_DISK_MB`,
  ברירת מחדל 4GB) על loop device ייעודי (`app/services/deploy.py`,
  `_ensure_app_volume`), נוצר עם `mkfs.ext4 -m 0` בהרצה הראשונה ומחובר
  מחדש (בלי לאבד נתונים) בכל הרצה נוספת. כשהאפליקציה מנסה לכתוב מעבר
  למכסה, מערכת ההפעלה עצמה מחזירה "No space left on device" - זו לא
  בדיקה תקופתית, אלא הגבלה אמיתית ברמת הקרנל.

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

## אפליקציות עם אתר, לא רק בוטים

בכל אפליקציה מוזרק אוטומטית משתנה סביבה שמור בשם `PORT` (ברירת מחדל
`8080`, ניתן לשינוי עם `APP_PORT` ב-`.env`). אם קוד המשתמש הוא לא רק
בוט טלגרם אלא גם מריץ שרת אינטרנט (Flask/FastAPI/כל דבר אחר), עליו
להאזין על הפורט הזה **ועל `0.0.0.0`** (לא רק `127.0.0.1`) - למשל
ב-Flask: `app.run(host="0.0.0.0", port=int(os.environ["PORT"]))`.

בעמוד כל אפליקציה יש כפתור "פתח אפליקציה" שמזרים תעבורת HTTP ישירות
לקונטיינר של האפליקציה - נתיב **ציבורי** (בלי צורך בהתחברות, בדיוק כמו
אתר רגיל). ה-slug נבנה אוטומטית מהשם שבחרתם (למשל `my-cool-bot`) - נקי
בלי סיומת מספרים, ורק אם השם כבר תפוס ע"י אפליקציה אחרת מתווסף מזהה
(`my-cool-bot-42`). שמות אפליקציות חייבים להיות ייחודיים גלובלית (לא רק
מול האפליקציות שלכם, מול כולם), כדי שאף אחד לא "יגנוב" כתובת של מישהו
אחר. אם האפליקציה לא רצה, או שהיא רצה אבל לא מאזינה בכלל על הפורט הזה
(למשל כי היא "רק" בוט טלגרם בלי אתר), מוצג דף מיתוג של Serves במקום
שגיאה - ואם זוהה גם טוקן טלגרם, יש שם גם קישור ישיר לבוט.

**שני מצבי כתובת** (בוחר אוטומטית, אין צורך להגדיר):
- **ברירת מחדל, בלי הגדרה נוספת**: `/open/<slug>` - תת-נתיב תחת הדומיין
  הראשי. עובד תמיד, לא דורש שום DNS נוסף. מגבלה: מסגרות עבודה שמניחות
  שהן רצות בשורש (`/`) עלולות לשבור קישורי assets יחסיים, וזו הזרמת
  HTTP רגיל בלבד (לא WebSocket).
- **סאב-דומיין לכל אפליקציה** (`my-cool-bot.teleboss.online`): נדלק
  אוטומטית ברגע ש-`PUBLIC_BASE_URL` מוגדר ב-`.env` (זהה למשתנה שכבר
  משמש את בוט התשלומים) **וגם** יש בפועל רשומת DNS wildcard + תעודת
  SSL wildcard בשרת - אחרת הבקשות לסאב-דומיין כזה פשוט לא מגיעות לשרת
  ו-`/open/<slug>` ממשיך לעבוד כרגיל. כדי להפעיל בפועל:
  1. **DNS**: הוסיפו רשומת `A` נוספת אצל ספק ה-DNS שלכם:
     `*.teleboss.online` → אותה כתובת IP כמו `teleboss.online`.
  2. **תעודת SSL wildcard**: `certbot --nginx -d teleboss.online` (מה
     שכבר רצתם) **לא** מכסה סאב-דומיינים. צריך תעודה נפרדת עם אימות
     DNS-01 (החובה עבור wildcard, HTTP-01 לא תומך בזה):
     ```bash
     sudo certbot certonly --manual --preferred-challenges dns \
       -d teleboss.online -d '*.teleboss.online'
     ```
     certbot יבקש להוסיף רשומת `TXT` זמנית אצל ספק ה-DNS לאימות (יש
     ספקים עם plugin שעושה את זה אוטומטית, למשל `certbot-dns-cloudflare`
     - תלוי איפה מנוהל ה-DNS של teleboss.online). התעודה החדשה נשמרת
     תחת `/etc/letsencrypt/live/teleboss.online-0001/` (או דומה) - צריך
     לעדכן את `ssl_certificate`/`ssl_certificate_key` ב-nginx לתעודה
     הזו במקום זו שיצר `certbot --nginx`, ואז `sudo systemctl reload nginx`.
     תעודת wildcard עם אימות ידני לא מתחדשת אוטומטית - יש לחזור על השלב
     הזה כל ~90 יום (או לעבור ל-plugin DNS אוטומטי של הספק שלכם).
  3. `sudo systemctl restart serves` (כדי ש-`PUBLIC_BASE_URL` ייטען).

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

מאז שנוספה מיגרציה אוטומטית (`app/database.py`), עדכון קוד **לא** דורש
יותר למחוק את ה-DB - עמודות חדשות שנוספות למודלים נוצרות אוטומטית
ב-startup (`ALTER TABLE ... ADD COLUMN`, additive בלבד):

```bash
cd ~/serves && git pull
sudo bash scripts/install.sh   # מעתיק את הקוד המעודכן ל-/opt/serves ומפעיל מחדש
```

## תוכניות בתשלום (Telegram Stars)

שלוש תוכניות, כל אחת עם מספר אפליקציות ומשאבים משלה (הכל ניתן לשינוי
ב-`.env`, ראו `.env.example`):

| תוכנית | אפליקציות | זיכרון | CPU | דיסק לכל אפליקציה | מחיר |
|---|---|---|---|---|---|
| Free | `FREE_MAX_APPS`=1 | `FREE_MEMORY_MB`=256MB | `FREE_CPU_CORES`=0.5 | `FREE_DISK_MB`=2GB | חינם |
| Pro | `PRO_MAX_APPS`=3 | כמו Free | כמו Free | כמו Free | `PRO_PLAN_STARS`=1000⭐ |
| Plus | `PLUS_MAX_APPS`=5 | `PLUS_MEMORY_MB`=1024MB | `PLUS_CPU_CORES`=1.0 | `PLUS_DISK_MB`=8GB | `PLUS_PLAN_STARS`=2500⭐ |

`PRO_MEMORY_MB`/`PRO_CPU_CORES`/`PRO_DISK_MB` קיימים גם הם אם רוצים
להפריד את המשאבים של Pro מ-Free בעתיד - כרגע הם פשוט יורשים את אותם
ערכים. שינוי מגבלת דיסק לאפליקציה קיימת נכנס לתוקף רק אחרי "פריסה
מחדש" (יצירת loop device חדש בגודל הנכון) - לא רק restart.

זה עובד עם **בוט טלגרם נפרד** (לא אחד מהאפליקציות שמתארחות בפלטפורמה)
שמטפל בתשלומים - צריך ליצור בוט חדש דרך [@BotFather](https://t.me/BotFather)
ולשים את הטוקן וה-username שלו ב-`.env`:
```
PAYMENT_BOT_TOKEN=<הטוקן מ-BotFather>
PAYMENT_BOT_USERNAME=<שם המשתמש של הבוט, בלי @>
PUBLIC_BASE_URL=https://teleboss.online
```

הבוט הזה **לא צריך שירות נפרד** - הוא רץ כ-background task בתוך אותו
תהליך של הפלטפורמה (long polling מול Telegram), ועולה אוטומטית עם
`sudo systemctl restart serves`.

**זרימת התשלום:**
1. באתר, `/billing` → כפתור שדרוג → נוצר קוד רכישה אקראי ובלתי-ניתן-לניחוש
   (`PlanPurchase.pay_code`), והדפדפן מועבר לעמוד עם קישור עומק לבוט:
   `https://t.me/<username>?start=pay_<code>`.
2. הבוט מזהה את הקוד, בודק שהוא עדיין תקף (`PAYMENT_LINK_TTL_MINUTES`,
   ברירת מחדל 30 דקות), ושולח חשבונית עם `sendInvoice` (currency=`XTR`).
3. Telegram שולח `pre_checkout_query` - הבוט מאשר רק אם הרכישה עדיין
   ממתינה ולא פגה.
4. **רק אחרי** `successful_payment` אמיתי מ-Telegram (לא לפני, ולא סתם
   כי המשתמש חזר לאתר) - הבוט מסמן את הרכישה כ"שולם" ומעדכן את
   `user.plan` ב-DB. עמוד ה-`/billing/pay/<code>` באתר עושה polling
   ל-`/billing/status/<code>` כל 3 שניות ומעביר אוטומטית ל-dashboard
   ברגע שזה קורה.

אם `PAYMENT_BOT_TOKEN` ריק, כל מנגנון התשלומים פשוט כבוי (הבוט לא עולה,
וכפתורי השדרוג לא מוצגים).

## חשבון משתמש

מהתפריט (☰) → "החשבון שלי" אפשר לשנות סיסמא, לשנות כתובת מייל (אם
אימות מייל דלוק, זה ישלח קוד אימות חדש לכתובת החדשה ויסמן את המשתמש
כלא-מאומת עד שיאמת), ולמחוק את החשבון לצמיתות (כולל עצירה ומחיקה של
כל האפליקציות שלו).

## הפעלה/עצירה

עצירת אפליקציה שולחת `SIGTERM`/`SIGKILL` לקונטיינר בפועל (לא רק מסמנת
סטטוס). לאחר עצירה מוצג כפתור "הפעלה" שמריץ מחדש את הקוד הקיים בלי
לשכפל את הריפו מחדש (מהיר יותר מ"פריסה מחדש", שכן עושה `git clone`
נקי). היה כאן באג שבו thread הרקע שעוקב אחרי הלוגים היה דורס בטעות
סטטוס STOPPED בחזרה ל-FAILED - תוקן.

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

- Migrations אמיתיות (Alembic) במקום ה-auto-migrate הפשוט הנוכחי.
- סאב-דומיין ייעודי לכל אפליקציה במקום `/open/<slug>` (דורש DNS/SSL wildcard).
- הגבלת דיסק שונה לפי תוכנית (כרגע `FREE_DISK_MB` גלובלי לכל האפליקציות).
