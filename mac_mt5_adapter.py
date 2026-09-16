"""macOS development adapter using MT5.app's bundled Wine runtime.

It intentionally does not depend on a third-party trading bridge.  It starts
the repository-owned worker in Wine, where the official MetaTrader5 package
communicates locally with the MT5 terminal.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from execution_contract import ExecutionAction, ExecutionCommand, ExecutionResult, ExecutionStatus
from execution_settings import get_execution_settings
from credential_store import CredentialStore

MT5_APP = Path("/Applications/MetaTrader 5.app")
DEFAULT_WINE = MT5_APP / "Contents/SharedSupport/wine/bin/wine64"
DEFAULT_PREFIX = Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5"
WINE_PYTHON = r"C:\Python39\python.exe"


class MacMT5Adapter:
    def __init__(self):
        self.settings = get_execution_settings()
        self.wine = Path(os.getenv("SIGNAL_SNIPER_WINE", str(DEFAULT_WINE)))
        self.prefix = Path(os.getenv("SIGNAL_SNIPER_WINEPREFIX", str(DEFAULT_PREFIX)))
        self.worker = Path(__file__).with_name("mac_mt5_worker.py").resolve()
        self._lock = threading.Lock()
        if not self.wine.is_file():
            raise RuntimeError("MT5.app bundled Wine runtime was not found.")
        worker_path = "Z:" + str(self.worker).replace("/", "\\")
        self.process = subprocess.Popen(
            [str(self.wine), WINE_PYTHON, worker_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env={**os.environ, "WINEPREFIX": str(self.prefix)},
        )

    def _call(self, operation: str, command: ExecutionCommand | None = None,
              symbol: str | None = None) -> dict[str, Any]:
        config = dict(self.settings)
        login, server = config.get("login"), config.get("server")
        if login and server:
            # This password only traverses the local subprocess pipe in memory.
            # It is never written to settings, output, or logs.
            config["password"] = CredentialStore().load(f"{login}@{server}")
        request: dict[str, Any] = {"operation": operation, "config": config}
        if command:
            request["command"] = command.to_dict()
        if symbol:
            request["symbol"] = symbol
        with self._lock:
            if self.process.poll() is not None or not self.process.stdin or not self.process.stdout:
                raise RuntimeError("Mac MT5 worker is not running.")
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
            response = self.process.stdout.readline()
        if not response:
            raise RuntimeError("Mac MT5 worker did not return a response.")
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Mac MT5 worker returned an invalid response.") from exc

    def health(self) -> dict[str, Any]:
        return self._call("health")

    def quote(self, symbol: str) -> dict[str, float] | None:
        return self._call("quote", symbol=symbol)

    def execute(self, command: ExecutionCommand) -> ExecutionResult:
        raw = self._call("execute", command)
        try:
            return ExecutionResult(
                command_id=command.command_id, trade_id=command.trade_id, action=ExecutionAction(raw["action"]),
                status=ExecutionStatus(raw["status"]), message=raw.get("message", ""),
                mt5_retcode=raw.get("mt5_retcode"), order_ticket=raw.get("order_ticket"),
                position_ticket=raw.get("position_ticket"), deal_ticket=raw.get("deal_ticket"),
                requested_price=raw.get("requested_price"), fill_price=raw.get("fill_price"),
                requested_volume=raw.get("requested_volume"), filled_volume=raw.get("filled_volume"),
                take_profit_levels=raw.get("take_profit_levels") or [],
                final_take_profit=raw.get("final_take_profit"),
            )
        except (KeyError, ValueError) as exc:
            return ExecutionResult(command.command_id, command.trade_id, command.action,
                                   ExecutionStatus.FAILED, "Mac adapter received an invalid result.")

    def shutdown(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
