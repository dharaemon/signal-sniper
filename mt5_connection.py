import json
import os
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "mt5_connection.json")

DEFAULT_STATE = {
    "status": "DISCONNECTED",
    "login": None,
    "server": None,
    "balance": None,
    "equity": None,
    "profit": None,
    "currency": None,
    "trade_allowed": False,
    "position_count": 0,
    "position_tickets": [],
    "connected_at": None,
    "last_error": None,
}


def _read():
    if not os.path.exists(STATE_FILE):
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = dict(DEFAULT_STATE)
        if isinstance(data, dict):
            result.update(data)
        return result
    except Exception:
        return dict(DEFAULT_STATE)


def _write(data):
    fd, tmp = tempfile.mkstemp(prefix=".mt5_connection_", suffix=".tmp", dir=BASE_DIR, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def get_connection():
    return _read()


def set_connected(login, server, account=None):
    state = _read()
    state.update({
        "status": "CONNECTED",
        "login": int(login),
        "server": server,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "last_error": None,
    })
    if account:
        for key in ("balance", "equity", "profit", "currency", "trade_allowed", "position_count", "position_tickets"):
            if key in account:
                state[key] = account[key]
    _write(state)
    return state


def set_disconnected(reason=None):
    state = _read()
    state.update({
        "status": "DISCONNECTED",
        "login": None,
        "server": None,
        "balance": None,
        "equity": None,
        "profit": None,
        "currency": None,
        "trade_allowed": False,
        "position_count": 0,
        "position_tickets": [],
        "connected_at": None,
        "last_error": reason,
    })
    _write(state)
    return state


def set_error(message):
    state = _read()
    state["status"] = "ERROR"
    state["last_error"] = str(message)
    state["trade_allowed"] = False
    _write(state)
    return state
