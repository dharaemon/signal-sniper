#!/usr/bin/env python3
"""
Signal Sniper <-> MetaTrader 5 local bridge.

The bridge intentionally does NOT log into MT5 or store broker passwords.
The MT5 terminal must already be logged into the desired broker account.

Protocol:
  EA -> GET /next?token=...
  Bridge -> one pipe-delimited command or NONE

  EA -> POST /result
         token=...&id=...&status=...&retcode=...&ticket=...&price=...&message=...

The bridge also exposes:
  GET /health
  GET /state
  POST /command
"""

from __future__ import annotations
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = os.getenv("MT5_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("MT5_BRIDGE_PORT", "8765"))
TOKEN = os.getenv("MT5_BRIDGE_TOKEN", "CHANGE_ME_LOCAL_TOKEN")

_lock = threading.Lock()
_commands: list[dict] = []
_results: list[dict] = []
_state = {
    "ea_online": False,
    "last_heartbeat": None,
    "account": None,
    "server": None,
    "balance": None,
    "equity": None,
    "currency": None,
    "trade_allowed": None,
    "symbol": None,
    "bid": None,
    "ask": None,
}


def now():
    return time.time()


def clean(v):
    return str(v).replace("|", "/").replace("\r", " ").replace("\n", " ")


def auth(query):
    return query.get("token", [""])[0] == TOKEN


def json_bytes(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "SignalSniperMT5Bridge/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def send_text(self, code, text, content_type="text/plain; charset=utf-8"):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)

        if parsed.path == "/health":
            self.send_text(200, json_bytes({"ok": True, "service": "signal-sniper-mt5-bridge"}).decode(),
                           "application/json")
            return

        if not auth(q):
            self.send_text(403, "FORBIDDEN")
            return

        if parsed.path == "/next":
            with _lock:
                _state["ea_online"] = True
                _state["last_heartbeat"] = now()
                if _commands:
                    cmd = _commands.pop(0)
                else:
                    cmd = None
            self.send_text(200, "NONE" if cmd is None else cmd["wire"])
            return

        if parsed.path == "/state":
            with _lock:
                snapshot = dict(_state)
            self.send_text(200, json_bytes(snapshot).decode(), "application/json")
            return

        self.send_text(404, "NOT_FOUND")

    def do_POST(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = parse_qs(raw)

        token = form.get("token", q.get("token", [""]))[0]
        if token != TOKEN:
            self.send_text(403, "FORBIDDEN")
            return

        if parsed.path == "/command":
            command = form.get("command", [""])[0].upper()
            if command not in {
                "ACCOUNT_INFO", "PRICE", "POSITIONS",
                "OPEN_BUY", "OPEN_SELL", "CLOSE_ALL",
                "CLOSE_PARTIAL", "MODIFY_SL", "MODIFY_TP",
                "EMERGENCY_CLOSE_ALL",
            }:
                self.send_text(400, "BAD_COMMAND")
                return

            command_id = secrets.token_hex(8)
            symbol = form.get("symbol", ["XAUUSD"])[0]
            volume = form.get("volume", ["0"])[0]
            sl = form.get("sl", ["0"])[0]
            tp = form.get("tp", ["0"])[0]
            position = form.get("position", ["0"])[0]

            # Fixed-width pipe protocol. Keep fields numeric/simple.
            wire = "|".join([
                command_id, command, clean(symbol), clean(volume),
                clean(sl), clean(tp), clean(position)
            ])

            with _lock:
                _commands.append({"id": command_id, "wire": wire, "created": now()})

            self.send_text(200, json_bytes({"ok": True, "id": command_id}).decode(),
                           "application/json")
            return

        if parsed.path == "/result":
            result = {k: v[0] for k, v in form.items()}
            with _lock:
                _results.append(result)
                if len(_results) > 100:
                    del _results[:-100]

                status = result.get("status", "")
                if status == "HEARTBEAT":
                    _state["ea_online"] = True
                    _state["last_heartbeat"] = now()
                elif status == "ACCOUNT":
                    _state["ea_online"] = True
                    _state["last_heartbeat"] = now()
                    for key in ("account", "server", "balance", "equity",
                                "currency", "trade_allowed"):
                        if key in result:
                            _state[key] = result[key]
                elif status == "PRICE":
                    _state["symbol"] = result.get("symbol")
                    _state["bid"] = result.get("bid")
                    _state["ask"] = result.get("ask")
                    _state["last_heartbeat"] = now()

            print("MT5 RESULT:", result, flush=True)
            self.send_text(200, "OK")
            return

        self.send_text(404, "NOT_FOUND")


def main():
    if TOKEN == "CHANGE_ME_LOCAL_TOKEN":
        print("WARNING: set MT5_BRIDGE_TOKEN to a random local secret.", flush=True)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Signal Sniper MT5 bridge listening on http://{HOST}:{PORT}", flush=True)
    print("MT5 terminal must already be logged into the desired broker account.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
