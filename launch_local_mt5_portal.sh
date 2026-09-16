#!/usr/bin/env bash
# Start the MT5 credential portal for this Mac only. It deliberately binds to
# loopback and does not start Cloudflare, a public tunnel, MT5, or the executor.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

python_bin=".venv/bin/python"
if [[ ! -x "$python_bin" ]]; then python_bin="venv/bin/python"; fi
if [[ ! -x "$python_bin" ]]; then
  echo "Python virtual environment missing. Run ./setup_mac.sh first."
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing .env. Configure local Telegram settings first."
  exit 1
fi

"$python_bin" -B -m py_compile mt5_portal.py
portal_port="$("$python_bin" -B -c 'import os; from dotenv import load_dotenv; load_dotenv(".env"); print(os.getenv("MT5_PORTAL_PORT", "8080"))')"
echo "Local MT5 portal: http://127.0.0.1:${portal_port}/local-login"
echo "Open this URL in a browser on this Mac only. Press Ctrl-C to stop."
MT5_PORTAL_HOST=127.0.0.1 exec "$python_bin" -u -B mt5_portal.py
