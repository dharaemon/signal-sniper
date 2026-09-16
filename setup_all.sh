#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "======================================"
echo "🎯 SIGNAL SNIPER — SETUP"
echo "======================================"

mkdir -p runtime

# Create venv if missing
if [ ! -d "venv" ]; then
    echo "🐍 Creating Python environment..."
    python3 -m venv venv
fi

PYTHON="./venv/bin/python3"
PIP="./venv/bin/pip"

echo "📦 Installing dependencies..."
"$PIP" install --upgrade pip

if [ -f requirements.txt ]; then
    "$PIP" install -r requirements.txt
fi

echo "🔍 Checking core files..."

FILES=(
    "native_mt5_executor.py"
    "telegram_listener.py"
    "telegram_control_bot.py"
    "mt5_portal.py"
)

for file in "${FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Missing: $file"
        exit 1
    fi
done

echo "🧪 Compiling Python files..."
"$PYTHON" -m py_compile \
    native_mt5_executor.py \
    telegram_listener.py \
    telegram_control_bot.py \
    mt5_portal.py

if command -v cloudflared >/dev/null 2>&1; then
    echo "☁️ Cloudflare: installed"
else
    echo "⚠️ cloudflared is not installed."
fi

echo
echo "======================================"
echo "✅ SIGNAL SNIPER SETUP COMPLETE"
echo "======================================"
echo
echo "Start everything with:"
echo "./start_all.sh"
