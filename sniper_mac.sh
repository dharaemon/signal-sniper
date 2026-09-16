#!/bin/zsh

set -u

ROOT="$HOME/Desktop/signal-sniper-main"
cd "$ROOT" || exit 1

mkdir -p runtime

if [ -x "$ROOT/venv/bin/python" ]; then
    PY="$ROOT/venv/bin/python"
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="python3"
fi

echo "======================================"
echo "🎯 SIGNAL SNIPER"
echo "======================================"

# Kill stale Signal Sniper processes first.
pkill -f "native_mt5_executor.py" 2>/dev/null || true
pkill -f "telegram_listener.py" 2>/dev/null || true
pkill -f "telegram_control_bot.py" 2>/dev/null || true
pkill -f "mt5_portal.py" 2>/dev/null || true

sleep 1

echo "Starting MT5 portal..."
nohup "$PY" -u mt5_portal.py \
    > runtime/mt5_portal.log 2>&1 &
echo $! > runtime/mt5_portal.pid

# Give Flask a moment to bind to 8080.
sleep 2

if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "✅ MT5 Portal"
else
    echo "❌ MT5 Portal failed"
    echo "Check: tail -50 $ROOT/runtime/mt5_portal.log"
    exit 1
fi

# Start Cloudflare only if this tunnel is not already running.
if pgrep -f "cloudflared.*tunnel.*signal-sniper" >/dev/null 2>&1; then
    echo "✅ Cloudflare already running"
else
    echo "Starting Cloudflare tunnel..."
    nohup cloudflared tunnel run signal-sniper \
        > runtime/cloudflared.log 2>&1 &
    echo $! > runtime/cloudflared.pid
fi

sleep 2

echo "Starting native MT5 executor..."
nohup "$PY" -u native_mt5_executor.py \
    > runtime/native_mt5_executor.log 2>&1 &
echo $! > runtime/native_mt5_executor.pid

echo "Starting Telegram listener..."
nohup "$PY" -u telegram_listener.py \
    > runtime/telegram_listener.log 2>&1 &
echo $! > runtime/telegram_listener.pid

echo "Starting Telegram control bot..."
nohup "$PY" -u telegram_control_bot.py \
    > runtime/telegram_control_bot.log 2>&1 &
echo $! > runtime/telegram_control_bot.pid

sleep 3

echo
echo "======================================"
echo "SIGNAL SNIPER STATUS"
echo "======================================"

for service in \
    mt5_portal.py \
    native_mt5_executor.py \
    telegram_listener.py \
    telegram_control_bot.py
do
    if pgrep -f "$service" >/dev/null; then
        echo "✅ $service"
    else
        echo "❌ $service"
    fi
done

if pgrep -f "cloudflared.*tunnel.*signal-sniper" >/dev/null; then
    echo "✅ Cloudflare tunnel"
else
    echo "❌ Cloudflare tunnel"
fi

echo
echo "Testing portal..."

if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "✅ Local portal"
else
    echo "❌ Local portal"
fi

if curl -fsS https://login.emsignal.in/health >/dev/null 2>&1; then
    echo "✅ login.emsignal.in"
else
    echo "⚠️ login.emsignal.in not responding"
fi

echo
echo "🚀 Signal Sniper startup complete."
echo
echo "Logs: sniper-logs"
echo "Stop: sniper-stop"
echo "Restart: sniper-restart"
