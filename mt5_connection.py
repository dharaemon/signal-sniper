import json
import os
import tempfile
import time
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
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(
        prefix=".mt5_connection_",
        suffix=".tmp",
        dir=BASE_DIR,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        # On Windows another process can briefly have the destination open,
        # which makes os.replace() raise WinError 5. Retry the atomic replace
        # for a short period before falling back to an in-place write.
        last_error = None
        for _ in range(20):
            try:
                os.replace(tmp, STATE_FILE)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05)

        # Fallback keeps the executor alive if Windows file locking prevents
        # an atomic rename for longer than expected.
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            return
        except Exception:
            if last_error is not None:
                raise last_error
            raise
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
