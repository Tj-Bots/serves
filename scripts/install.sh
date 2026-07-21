#!/bin/bash
# סקריפט התקנה לפלטפורמת Serves על שרת Ubuntu 22.04 (VM 100 / Proxmox).
#
# הרצה (מתוך תיקיית הריפו אחרי git clone):
#   sudo bash scripts/install.sh [domain.example.com]
#
# אם מעבירים דומיין, הסקריפט גם יגדיר nginx reverse proxy + הנחיה ל-certbot.
# בלי דומיין, השרת יאזין על פורט 8000 בלבד (אפשר לגשת דרך http://IP:8000).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "יש להריץ עם sudo/root" >&2
    exit 1
fi

DOMAIN="${1:-}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/serves"

echo "==> [1/8] מתקין תלויות מערכת בסיסיות"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx iptables-persistent ca-certificates curl gnupg

echo "==> [2/8] מתקין Docker Engine (אם עוד לא מותקן)"
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
else
    echo "    Docker כבר מותקן, מדלג."
fi

echo "==> [3/8] מעתיק את הקוד ל-$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete --exclude='.git' --exclude='data' --exclude='venv' --exclude='.env' "$REPO_DIR/" "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/data/apps" "$INSTALL_DIR/data/logs"

echo "==> [4/8] יוצר סביבת Python וירטואלית ומתקין תלויות"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

echo "==> [5/8] מכין קובץ קונפיגורציה (.env)"
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    SECRET=$(openssl rand -hex 32)
    sed -i "s#^SECRET_KEY=.*#SECRET_KEY=$SECRET#" "$INSTALL_DIR/.env"
    sed -i "s#^DATABASE_URL=.*#DATABASE_URL=sqlite:////$INSTALL_DIR/data/serves.db#" "$INSTALL_DIR/.env"
    sed -i "s#^APPS_DIR=.*#APPS_DIR=$INSTALL_DIR/data/apps#" "$INSTALL_DIR/.env"
    sed -i "s#^LOGS_DIR=.*#LOGS_DIR=$INSTALL_DIR/data/logs#" "$INSTALL_DIR/.env"
    echo "    נוצר $INSTALL_DIR/.env עם SECRET_KEY אקראי."
    echo "    ערוך אותו אם צריך לשנות את TERMS_URL או מגבלות התוכנית החינמית."
else
    echo "    $INSTALL_DIR/.env כבר קיים, לא נוגע בו."
fi

echo "==> [6/8] בונה את תמונת ה-Docker הבסיסית להרצת בוטים"
docker build -t serves-python-base:3.11 -f "$INSTALL_DIR/docker/base.Dockerfile" "$INSTALL_DIR/docker"

echo "==> [7/8] מגדיר firewall (חסימת פורטי BitTorrent מרשת ה-sandbox)"
SANDBOX_SUBNET=$(grep '^SANDBOX_SUBNET=' "$INSTALL_DIR/.env" | cut -d= -f2)
bash "$INSTALL_DIR/scripts/setup_firewall.sh" "${SANDBOX_SUBNET:-172.30.0.0/24}"

echo "==> [8/8] מתקין ומפעיל את שירות ה-systemd"
cp "$INSTALL_DIR/scripts/serves.service" /etc/systemd/system/serves.service
systemctl daemon-reload
systemctl enable --now serves

if [ -n "$DOMAIN" ]; then
    echo "==> מגדיר nginx reverse proxy עבור $DOMAIN"
    sed "s#__DOMAIN__#$DOMAIN#g" "$INSTALL_DIR/scripts/nginx-serves.conf.template" > /etc/nginx/sites-available/serves
    ln -sf /etc/nginx/sites-available/serves /etc/nginx/sites-enabled/serves
    nginx -t && systemctl reload nginx
    echo "    כדי לקבל HTTPS, הריצו כעת:"
    echo "    apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d $DOMAIN"
fi

echo ""
echo "=== הותקן בהצלחה ==="
systemctl status serves --no-pager -l | head -n 8
echo ""
if [ -n "$DOMAIN" ]; then
    echo "האתר יעלה בכתובת: http://$DOMAIN (אחרי certbot: https://$DOMAIN)"
else
    echo "האתר זמין בכתובת: http://<כתובת-ה-IP-של-השרת>:8000"
fi
echo "לוגים: journalctl -u serves -f"
