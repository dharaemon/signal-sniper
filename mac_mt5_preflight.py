"""Read-only Mac adapter preflight. It cannot submit an order."""
from __future__ import annotations

import json

from mac_mt5_adapter import MacMT5Adapter


if __name__ == "__main__":
    adapter = MacMT5Adapter()
    try:
        health = adapter.health()
        print(json.dumps({
            "status": health.get("status"),
            "trade_allowed": health.get("trade_allowed", False),
            "has_account": bool(health.get("login")),
            "has_server": bool(health.get("server")),
            "error_type": health.get("last_error"),
        }))
    finally:
        adapter.shutdown()
