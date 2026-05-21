#!/usr/bin/env bash
# Setup-скрипт для Callisto. Запускается ОДИН раз после первого clone.
# Идемпотентен -- можно перезапустить без вреда.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/tos_bots/tos-stats}"
DATA_DIR="${DATA_DIR:-/root/tos_bots/data}"
SYSTEMD_DIR="/etc/systemd/system"

echo "==> tos-stats setup на $(hostname)"

cd "$REPO_DIR"

# 1. Python venv.
if [ ! -d .venv ]; then
  echo "==> Создаю Python venv"
  python3 -m venv .venv
fi
echo "==> Устанавливаю requirements"
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt --quiet

# 2. Каталоги.
echo "==> Готовлю каталоги"
mkdir -p logs
mkdir -p "$DATA_DIR"

# 3. .env проверка.
if [ ! -f .env ]; then
  echo "!! .env НЕ найден. Скопируй .env.example -> .env и заполни перед запуском"
  echo "   cp .env.example .env && nano .env"
  exit 1
fi
chmod 600 .env

# 4. Init DB.
echo "==> Init SQLite"
./.venv/bin/python -c "from ingest.db import init_db; init_db()"

# 5. Systemd unit/timer.
echo "==> Устанавливаю systemd unit + timer"
cp deploy/tos-stats.service "$SYSTEMD_DIR/tos-stats.service"
cp deploy/tos-stats.timer "$SYSTEMD_DIR/tos-stats.timer"
systemctl daemon-reload
systemctl enable tos-stats.timer
systemctl start tos-stats.timer

echo ""
echo "==> Готово."
echo ""
echo "Проверка:"
echo "  systemctl status tos-stats.timer        # timer запущен"
echo "  systemctl list-timers tos-stats.timer   # когда следующий запуск"
echo "  systemctl start tos-stats.service       # ручной запуск pipeline"
echo "  tail -f $REPO_DIR/logs/weekly.log       # лог в реалтайме"
