"""Non-secret executor settings shared by setup scripts and adapters."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SETTINGS_FILE = Path(__file__).with_name("execution_settings.json")
DEFAULTS = {
    "adapter": "auto",  # auto | mac | windows
    "execution_enabled": False,
    "symbol": "XAUUSD",
    "magic_number": 5142026,
    "deviation_points": 50,
    "poll_seconds": 0.5,
    "terminal_path": "",
    "login": None,
    "server": "",
}


def get_execution_settings() -> dict:
    result = dict(DEFAULTS)
    if not SETTINGS_FILE.exists():
        return result
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            result.update(value)
    except Exception:
        pass
    # Secrets are expressly not supported in this file.
    result.pop("password", None)
    return result


def write_execution_settings(changes: dict) -> dict:
    data = get_execution_settings()
    data.update(changes)
    data.pop("password", None)
    fd, temporary = tempfile.mkstemp(prefix=".execution_settings_", suffix=".tmp", dir=SETTINGS_FILE.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.replace(temporary, SETTINGS_FILE)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return data
