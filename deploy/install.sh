#!/usr/bin/env bash
# ============================================================
#  VPS'ga o'rnatish skripti (Ubuntu 22.04+)
#  Ishlatish:  sudo bash deploy/install.sh
# ============================================================
set -euo pipefail

APP_DIR=/opt/bizitoys_bot
APP_USER=bizitoys

echo "▶ Paketlarni yangilash..."
apt update -y
apt install -y python3 python3-venv python3-pip git

echo "▶ Foydalanuvchi yaratish..."
id -u "$APP_USER" &>/dev/null || useradd -r -m -d "$APP_DIR" -s /bin/bash "$APP_USER"

echo "▶ Virtual muhit va kutubxonalar..."
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "▶ Ruxsatlar..."
mkdir -p "$APP_DIR/data" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env" 2>/dev/null || true

echo "▶ systemd xizmati..."
cp deploy/bizitoys-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bizitoys-bot

echo "✅ Tayyor! Holatni ko'rish:  systemctl status bizitoys-bot"
