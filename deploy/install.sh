#!/usr/bin/env bash
# ============================================================
#  VPS'ga o'rnatish skripti (Ubuntu 22.04+)
#
#  Ishlatish (VPS'da, root sifatida):
#    curl -o install.sh https://raw.githubusercontent.com/<USER>/<REPO>/main/deploy/install.sh
#    bash install.sh https://github.com/<USER>/<REPO>.git
# ============================================================
set -euo pipefail

REPO_URL="${1:?Repo manzilini bering: bash install.sh https://github.com/USER/REPO.git}"
APP_DIR=/opt/bizitoys_bot
APP_USER=bizitoys

echo "▶ Paketlarni yangilash..."
apt update -y
apt install -y python3 python3-venv python3-pip git

echo "▶ Foydalanuvchi yaratish..."
id -u "$APP_USER" &>/dev/null || useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"

echo "▶ Kodni GitHub'dan olish..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR" && sudo -u "$APP_USER" git pull
else
    rm -rf "$APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

echo "▶ Virtual muhit va kutubxonalar..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "▶ .env sozlamalari..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp .env.example .env
    echo "  [!] .env yaratildi — TOKENLARNI KIRITISHNI unutmang:"
    echo "      nano $APP_DIR/.env"
fi

echo "▶ Ruxsatlar..."
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "▶ systemd xizmati..."
cp deploy/bizitoys-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bizitoys-bot

echo ""
echo "✅ O'rnatildi. Keyingi qadam:"
echo "   1) nano $APP_DIR/.env         — tokenlarni kiriting"
echo "   2) systemctl start bizitoys-bot"
echo "   3) systemctl status bizitoys-bot"
echo "   4) journalctl -u bizitoys-bot -f   — loglarni ko'rish"
