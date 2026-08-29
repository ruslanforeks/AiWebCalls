#!/usr/bin/env bash
#
# Развёртывание голосового агента на чистый Ubuntu 22.04/24.04.
#
#   ./deploy/setup.sh root@5.129.224.101
#
# Скрипт идемпотентный: повторный запуск обновляет код и зависимости,
# ничего не ломая. Требует доступа по SSH-ключу — пароль не запрашивается.

set -euo pipefail

HOST="${1:?Укажите хост: ./deploy/setup.sh root@IP}"
REMOTE_DIR="/opt/aiwebcalls"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Ключ задаётся переменной, чтобы скрипт не зависел от ~/.ssh/config.
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prizma_vps}"
export GIT_SSH_COMMAND="ssh -i $SSH_KEY"
ssh() { command ssh -i "$SSH_KEY" -o BatchMode=yes "$@"; }
rsync() { command rsync -e "command ssh -i $SSH_KEY -o BatchMode=yes" "$@"; }

say() { printf '\n\033[1m>>> %s\033[0m\n' "$1"; }

say "Проверяю доступ к $HOST"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" 'echo OK' >/dev/null

say "Проверяю подкачку"
# На VPS с ~1 ГБ памяти голосовой конвейер (модель VAD, аудиобуферы, WebRTC)
# подходит к границе. Без swap нехватка памяти означает убитый процесс
# посреди разговора, а не замедление.
ssh "$HOST" 'bash -se' <<'REMOTE'
set -euo pipefail
if [ -n "$(swapon --show --noheadings 2>/dev/null)" ]; then
    echo "swap уже настроен:"; swapon --show
else
    echo "swap отсутствует, создаю 2 ГБ"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap -q /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    # Подкачку трогаем только при реальной нехватке: на диске она медленная.
    sysctl -q -w vm.swappiness=10
    grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
    free -h | head -3
fi
REMOTE

say "Ставлю системные пакеты"
# python3.12-venv     — Ubuntu ставит venv отдельным пакетом
# build-essential     — сборка колёс, для которых нет готовых
# libopus0, libvpx    — кодеки, нужны aiortc для WebRTC
# ffmpeg              — обработка аудио
ssh "$HOST" 'bash -se' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    python3 python3-venv python3-dev \
    build-essential pkg-config \
    libopus0 libvpx-dev libsrtp2-dev libffi-dev libssl-dev \
    ffmpeg rsync curl ca-certificates
python3 --version
REMOTE

say "Копирую проект в $REMOTE_DIR"
ssh "$HOST" "mkdir -p $REMOTE_DIR"
# .env не копируем: секреты заполняются на сервере отдельно.
rsync -az --delete \
    --exclude '.venv*' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '.env' \
    --exclude '*.csv' \
    --exclude '*.xlsx' \
    "$LOCAL_DIR/voice-agent/" "$HOST:$REMOTE_DIR/voice-agent/"
rsync -az "$LOCAL_DIR/.env.example" "$HOST:$REMOTE_DIR/.env.example"

say "Ставлю зависимости Python"
ssh "$HOST" "bash -se" <<REMOTE
set -euo pipefail
cd $REMOTE_DIR/voice-agent
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"
REMOTE

say "Прогоняю тесты"
ssh "$HOST" "cd $REMOTE_DIR/voice-agent && .venv/bin/python -m pytest -q --no-header"

say "Готово"
cat <<EOF

Дальше — заполнить ключи на сервере:

    ssh $HOST
    cp $REMOTE_DIR/.env.example $REMOTE_DIR/.env
    nano $REMOTE_DIR/.env

Затем запустить агента:

    cd $REMOTE_DIR/voice-agent && .venv/bin/python -m grainvoice.bot

EOF
