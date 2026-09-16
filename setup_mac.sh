#!/usr/bin/env bash
# Idempotent macOS development setup. It never enables live trading.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
python_bin="${PYTHON_BIN:-python3}"

"$python_bin" --version
if [[ ! -d .venv ]]; then
  "$python_bin" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

mkdir -p runtime logs
if [[ ! -f execution_settings.json ]]; then
  cp execution_settings.example.json execution_settings.json
fi

.venv/bin/python -B -m py_compile *.py

if [[ -x "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine64" ]]; then
  echo "MT5.app Wine runtime detected."
  echo "Run the Mac MT5 preflight after MT5 is logged in: .venv/bin/python mac_mt5_preflight.py"
else
  echo "WARNING: MT5.app Wine runtime was not found; real Mac MT5 execution is unavailable until MT5 is installed."
fi
echo "macOS setup complete. Live execution remains disabled in execution_settings.json."
