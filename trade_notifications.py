import json
import os
import tempfile
from datetime import datetime, timezone


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENT_FILE = os.path.join(BASE_DIR, "trade_notifications.json")
APPROVAL_FILE = os.path.join(BASE_DIR, "trade_approvals.json")


def _read_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def _write_file(path, data):
    directory = os.path.dirname(path)
    fd, temp_path = tempfile.mkstemp(
        prefix=".signal_sniper_",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise


def _read():
    data = _read_file(EVENT_FILE, [])
    return data if isinstance(data, list) else []


def _write(events):
    _write_file(EVENT_FILE, events)


def _read_approvals():
    data = _read_file(APPROVAL_FILE, [])
    return data if isinstance(data, list) else []


def _write_approvals(requests):
    _write_file(APPROVAL_FILE, requests[-100:])


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_event(
    trade_id,
    event_type,
    symbol="XAUUSD",
    direction=None,
    entry_type=None,
    entry_low=None,
    entry_high=None,
    actual_entry_price=None,
    stop_loss=None,
    tp1=None,
    tp2=None,
    tp3=None,
    execution_type=None,
    execution_status=None,
    status=None,
    volume=None,
    lot_size=None,
    ticket=None,
    profit=None,
    reason=None,
    source_chat_name=None,
    source_chat_id=None,
    activation_message_id=None,
    latest_message_id=None,
    management_action=None,
):
    events = _read()

    event = {
        "event_id": f"{trade_id}-{int(datetime.now().timestamp() * 1000)}",
        "created_at": _now(),
        "trade_id": trade_id,
        "event_type": event_type,
        "symbol": symbol,
        "direction": direction,
        "entry_type": entry_type,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "actual_entry_price": actual_entry_price,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "execution_type": execution_type,
        "execution_status": execution_status,
        "status": status,
        "volume": volume,
        "lot_size": lot_size,
        "ticket": ticket,
        "profit": profit,
        "reason": reason,
        "source_chat_name": source_chat_name,
        "source_chat_id": source_chat_id,
        "activation_message_id": activation_message_id,
        "latest_message_id": latest_message_id,
        "management_action": management_action,
        "notified": False,
    }

    events.append(event)
    _write(events[-500:])
    return event


def get_unnotified_events():
    return [event for event in _read() if not event.get("notified", False)]


def mark_notified(event_id):
    events = _read()
    changed = False
    for event in events:
        if event.get("event_id") == event_id:
            event["notified"] = True
            changed = True
            break
    if changed:
        _write(events)


def get_recent_events(limit=20):
    return _read()[-limit:]


def clear_events():
    if os.path.exists(EVENT_FILE):
        os.remove(EVENT_FILE)


# ============================================================
# TRADE APPROVAL SHARED STATE
# ============================================================


def create_approval_request(trade_id, **details):
    requests = _read_approvals()

    # Only one live request per trade.
    requests = [r for r in requests if r.get("trade_id") != trade_id]

    request = {
        "request_id": f"approval-{trade_id}-{int(datetime.now().timestamp() * 1000)}",
        "created_at": _now(),
        "updated_at": _now(),
        "trade_id": trade_id,
        "decision": None,
        "sent": False,
        "message_id": None,
        "chat_id": None,
        "last_rendered": None,
        **details,
    }

    requests.append(request)
    _write_approvals(requests)
    return request


def get_approval_request(trade_id):
    for request in reversed(_read_approvals()):
        if request.get("trade_id") == trade_id:
            return request
    return None


def get_pending_approval_requests():
    return [
        request
        for request in _read_approvals()
        if request.get("decision") is None
    ]


def update_approval_request(trade_id, **details):
    requests = _read_approvals()
    changed = False

    for request in requests:
        if request.get("trade_id") != trade_id:
            continue

        if request.get("decision") is not None:
            return request

        for key, value in details.items():
            if request.get(key) != value:
                request[key] = value
                changed = True

        if changed:
            request["updated_at"] = _now()
        _write_approvals(requests)
        return request

    return None


def set_approval_message(trade_id, chat_id, message_id):
    requests = _read_approvals()
    for request in requests:
        if request.get("trade_id") == trade_id:
            request["sent"] = True
            request["chat_id"] = chat_id
            request["message_id"] = message_id
            request["updated_at"] = _now()
            _write_approvals(requests)
            return request
    return None


def set_approval_rendered(trade_id, rendered_text):
    requests = _read_approvals()
    for request in requests:
        if request.get("trade_id") == trade_id:
            request["last_rendered"] = rendered_text
            request["updated_at"] = _now()
            _write_approvals(requests)
            return request
    return None


def set_approval_decision(trade_id, decision):
    decision = str(decision).upper()
    if decision not in ("EXECUTE", "IGNORE"):
        raise ValueError("decision must be EXECUTE or IGNORE")

    requests = _read_approvals()
    for request in requests:
        if request.get("trade_id") == trade_id:
            if request.get("decision") is not None:
                return request
            request["decision"] = decision
            request["updated_at"] = _now()
            _write_approvals(requests)
            return request

    return None


def clear_approval_request(trade_id):
    requests = [
        request
        for request in _read_approvals()
        if request.get("trade_id") != trade_id
    ]
    _write_approvals(requests)
