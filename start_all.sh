#!/bin/bash

cd "$(dirname "$0")" || exit 1

mkdir -p runtime

PYTHON="./venv/bin/python3"

echo "======================================"
echo "🎯 SIGNAL SNIPER"
echo "🚀 STARTING ALL SYSTEMS"
echo "======================================"

# Prevent duplicate Signal Sniper processes
pkill -f "native_mt5_executor.py" 2>/dev/null || true
pkill -f "telegram_listener.py" 2>/dev/null || true
pkill -f "telegram_control_bot.py" 2>/dev/null || true
pkill -f "mt5_portal.py" 2>/dev/null || true

sleep 1

echo "⚙️ Starting MT5 executor..."
nohup "$PYTHON" -u native_mt5_executor.py \
    > runtime/native_mt5_executor.log 2>&1 &

echo "📡 Starting Telegram listener..."
nohup "$PYTHON" -u telegram_listener.py \
    > runtime/telegram_listener.log 2>&1 &

echo "🤖 Starting Telegram control bot..."
nohup "$PYTHON" -u telegram_control_bot.py \
    > runtime/telegram_control_bot.log 2>&1 &

echo "🔐 Starting MT5 login portal..."
nohup "$PYTHON" -u mt5_portal.py \
    > runtime/mt5_portal.log 2>&1 &

sleep 2

echo "☁️ Starting Cloudflare tunnel..."

pkill -f "cloudflared tunnel run signal-sniper" 2>/dev/null || true

nohup cloudflared tunnel run signal-sniper \
    > runtime/cloudflared.log 2>&1 &

sleep 3

echo
echo "======================================"
echo "🔍 SYSTEM CHECK"
echo "======================================"

check_process() {
    if pgrep -f "$1" >/dev/null; then
        echo "🟢 $2"
    else
        echo "🔴 $2"
    fi
}

check_process "native_mt5_executor.py" "MT5 Executor"
check_process "telegram_listener.py" "Telegram Listener"
check_process "telegram_control_bot.py" "Telegram Control Bot"
check_process "mt5_portal.py" "MT5 Login Portal"
check_process "cloudflared tunnel run signal-sniper" "Cloudflare Tunnel"

echo

if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "🟢 Local Portal"
else
    echo "🔴 Local Portal"
fi

if curl -fsS https://login.emsignal.in/health >/dev/null 2>&1; then
    echo "🟢 login.emsignal.in"
else
    echo "🔴 login.emsignal.in"
fi

echo
echo "======================================"
echo "✅ SIGNAL SNIPER START SEQUENCE COMPLETE"
echo "======================================"
