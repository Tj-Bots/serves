#!/bin/bash
# חוסם פורטי BitTorrent הידועים ביציאה (egress) מרשת ה-sandbox של Docker
# שבה רצות אפליקציות המשתמשים, בלי לפגוע בשאר התעבורה של השרת/רשתות אחרות.
#
# הרצה: sudo bash scripts/setup_firewall.sh [subnet]
# ברירת מחדל ל-subnet: 172.30.0.0/24 (ראה SANDBOX_SUBNET ב-.env)
#
# הערה: זו שכבת הגנה נוספת. ההגנה העיקרית היא שלמשתמשים אין הרשאות
# מערכת בכלל בתוך הקונטיינר (ללא sudo/apt), כך שאי אפשר להתקין קליינטי
# טורנט/ffmpeg מלכתחילה - זה חוסם רק כלים שכבר קיימים כספריית pip טהורה.

set -euo pipefail

SUBNET="${1:-172.30.0.0/24}"
CHAIN="SERVES-SANDBOX"

if [ "$(id -u)" -ne 0 ]; then
    echo "יש להריץ עם sudo/root" >&2
    exit 1
fi

echo "[serves] מגדיר firewall עבור subnet: $SUBNET"

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"

# פורטי BitTorrent נפוצים (TCP+UDP) - טווח קליינטים סטנדרטי + DHT + חלק מהטראקרים
for PORT_RANGE in 6881:6999 4662 4672 51413 6969; do
    iptables -A "$CHAIN" -p tcp --dport "$PORT_RANGE" -j DROP
    iptables -A "$CHAIN" -p udp --dport "$PORT_RANGE" -j DROP
done

# חוזרים לזרימה הרגילה עבור כל השאר
iptables -A "$CHAIN" -j RETURN

# מחברים את השרשרת לתעבורה היוצאת מתת-הרשת של ה-sandbox בלבד
if ! iptables -C DOCKER-USER -s "$SUBNET" -j "$CHAIN" 2>/dev/null; then
    iptables -I DOCKER-USER -s "$SUBNET" -j "$CHAIN"
fi

echo "[serves] הכללים הוגדרו. שומר עם netfilter-persistent (אם מותקן) ..."
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save
else
    echo "[serves] netfilter-persistent לא מותקן - הכללים לא ישרדו ריבוט."
    echo "         להתקנה: apt-get install -y iptables-persistent"
fi

echo "[serves] סיום."
