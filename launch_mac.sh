#!/usr/bin/env bash
# Launch the complete local Signal Sniper stack in one terminal.
# Ctrl-C stops all child processes cleanly.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

python_bin=".venv/bin/python"
# Existing local installs may still use venv; setup_mac.sh standardizes new
# installs on .venv.
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
"$python_bin" -B -m py_compile native_mt5_executor.py telegram_listener.py telegram_control_bot.py

declare -a child_pids=()

start_service() {
  local name="$1"
  local script="$2"
  "$python_bin" -u -B "$script" >"runtime/${name}.log" 2>&1 &
  child_pids+=("$!")
  echo "Started ${name} (log: runtime/${name}.log)"
}

shutdown() {
  echo
  echo "Stopping Signal Sniper services..."
  for pid in "${child_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap shutdown INT TERM EXIT

start_service "native_mt5_executor" "native_mt5_executor.py"
start_service "telegram_listener" "telegram_listener.py"
start_service "telegram_control_bot" "telegram_control_bot.py"

echo
echo "Signal Sniper is running. Press Ctrl-C to stop all services."
echo "Execution safety is controlled separately by execution_settings.json and MT5 terminal permissions."

# Keep the launcher alive. A child failure ends the stack so it cannot quietly
# appear healthy while a required service is gone.
while true; do
  for pid in "${child_pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A Signal Sniper service exited. Check runtime/*.log."
      exit 1
    fi
  done
  sleep 1
done
