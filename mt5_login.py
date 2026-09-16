import json
import os
import secrets
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REQUEST_FILE = BASE_DIR / "mt5_login_requests.json"
TOKEN_TTL = int(os.getenv("MT5_LOGIN_TOKEN_TTL", "600"))


def _read_requests():
    if not REQUEST_FILE.exists():
        return {}
    try:
        return json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_requests(data):
    fd, tmp = tempfile.mkstemp(prefix=".mt5_login_requests_", suffix=".tmp", dir=str(BASE_DIR), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, REQUEST_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def create_login_request(chat_id):
    token = secrets.token_urlsafe(36)
    now = int(time.time())
    data = _read_requests()
    data = {
        k: v for k, v in data.items()
        if not v.get("used") and v.get("expires_at", 0) > now
    }
    data[token] = {
        "chat_id": int(chat_id),
        "created_at": now,
        "expires_at": now + TOKEN_TTL,
        "used": False,
    }
    _write_requests(data)
    return token


def get_login_request(token):
    item = _read_requests().get(token)
    if not item or item.get("used") or item.get("expires_at", 0) < int(time.time()):
        return None
    return item


def consume_login_request(token):
    data = _read_requests()
    item = data.get(token)
    if not item or item.get("used") or item.get("expires_at", 0) < int(time.time()):
        return None
    item["used"] = True
    _write_requests(data)
    return item
