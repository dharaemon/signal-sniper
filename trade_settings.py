import json
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "trade_settings.json")

DEFAULTS = {
    "lot_size": 0.01,
    "local_take_profit_pips": None,
    "local_stop_loss_pips": None,
    "default_rr": "1:1",
}

def _read():
    if not os.path.exists(SETTINGS_FILE):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = dict(DEFAULTS)
        if isinstance(data, dict):
            result.update(data)
        return result
    except Exception:
        return dict(DEFAULTS)

def _write(data):
    fd, tmp = tempfile.mkstemp(prefix=".trade_settings_", suffix=".tmp", dir=BASE_DIR, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise

def get_lot_size():
    return float(_read().get("lot_size", DEFAULTS["lot_size"]))

def set_lot_size(value):
    value = float(value)
    if value <= 0:
        raise ValueError("Lot size must be greater than 0.")
    data = _read()
    data["lot_size"] = value
    _write(data)
    return value



def get_default_rr():
    value = str(_read().get("default_rr", DEFAULTS["default_rr"])).strip()
    return value or DEFAULTS["default_rr"]


def get_local_take_profit_pips():
    value = _read().get("local_take_profit_pips")
    return float(value) if value is not None else None


def get_local_stop_loss_pips():
    value = _read().get("local_stop_loss_pips")
    return float(value) if value is not None else None


def set_local_take_profit_pips(value):
    data = _read()
    data["local_take_profit_pips"] = _validate_pips(value)
    _write(data)
    return data["local_take_profit_pips"]


def set_local_stop_loss_pips(value):
    data = _read()
    data["local_stop_loss_pips"] = _validate_pips(value)
    _write(data)
    return data["local_stop_loss_pips"]


def _validate_pips(value):
    if value is None:
        return None
    value = float(value)
    if not 0 < value <= 100000:
        raise ValueError("Pips must be greater than 0 and no more than 100000.")
    return value
