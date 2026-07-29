#!/usr/bin/env bash
# ============================================================
#  SERVERDA YANGILASH — bitta buyruq.
#
#  Endi arxiv ko'chirish shart emas: kod GitHub'da, server undan
#  git pull bilan yangilanadi.
#
#  Ishlatish (VPS'da):
#    sudo bash /opt/bizitoys_bot/deploy/update.sh
# ============================================================
set -euo pipefail

APP_DIR=/opt/bizitoys_bot
APP_USER=bizitoys

cd "$APP_DIR"

echo "▶ .env zaxiralanmoqda..."
cp .env .env.zaxira

echo "▶ GitHub'dan yangi kod olinmoqda..."
sudo -u "$APP_USER" git fetch origin
sudo -u "$APP_USER" git reset --hard origin/main

echo "▶ .env qaytarilmoqda..."
cp .env.zaxira .env
rm .env.zaxira
chmod 600 .env

echo "▶ Kutubxonalar tekshirilmoqda..."
.venv/bin/pip install -q -r requirements.txt

echo "▶ Tekshiruv..."
.venv/bin/python tekshir.py

echo ""
echo "▶ Bot qayta ishga tushirilmoqda..."
systemctl restart bizitoys-bot
sleep 2
systemctl status bizitoys-bot --no-pager -l | head -10

echo ""
echo "✅ Yangilandi. Loglar:  journalctl -u bizitoys-bot -f"
