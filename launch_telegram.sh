#!/usr/bin/env bash
# Run only Telegram services. Useful while MT5 is intentionally disconnected.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

python_bin=".venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
  echo "Python virtual environment missing. Run ./setup_mac.sh first."
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing .env. Configure Telegram credentials locally before launch."
  exit 1
fi

mkdir -p runtime
"$python_bin" -B -m py_compile telegram_listener.py telegram_control_bot.py

child_pids=()
shutdown() {
  echo
  echo "Stopping Telegram services..."
  for pid in "${child_pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap shutdown INT TERM EXIT

"$python_bin" -u -B telegram_listener.py >runtime/telegram_listener.log 2>&1 &
child_pids+=("$!")
"$python_bin" -u -B telegram_control_bot.py >runtime/telegram_control_bot.log 2>&1 &
child_pids+=("$!")

echo "Telegram listener and control bot are running. Press Ctrl-C to stop them."
while true; do
  for pid in "${child_pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A Telegram service exited. Check runtime/*.log."
      exit 1
    fi
  done
  sleep 1
done
