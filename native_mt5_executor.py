"""Single-owner local MT5 executor service.

Run this process alongside Signal Sniper. It is the only process allowed to
touch the platform adapter or MT5 API.  Cloudflare is never on this path.
"""
from __future__ import annotations

import platform
import time

from execution_contract import ExecutionResult, ExecutionStatus
from execution_settings import get_execution_settings
from execution_store import claim_next, enqueue, new_command, pending_zone_watches, record_result
from execution_contract import ExecutionAction
from bot_settings import get_settings
from mt5_connection import set_connected, set_disconnected, set_error


def build_adapter():
    settings = get_execution_settings()
    selected = settings["adapter"]
    system = platform.system()
    if selected == "auto":
        selected = "windows" if system == "Windows" else "mac" if system == "Darwin" else "unsupported"
    if selected == "windows":
        from windows_mt5_adapter import WindowsMT5Adapter
        return WindowsMT5Adapter()
    if selected == "mac":
        from mac_mt5_adapter import MacMT5Adapter
        return MacMT5Adapter()
    raise RuntimeError(f"No supported MT5 adapter for platform {system}.")


def update_connection(adapter) -> None:
    health = adapter.health()
    if health.get("status") != "CONNECTED":
        set_disconnected(health.get("last_error", "Executor cannot reach MT5 terminal."))
        return
    set_connected(health["login"], health["server"], health)


def process_zone_watches(adapter) -> None:
    """Trigger a local market order only after an executable quote enters a zone."""
    for watch in pending_zone_watches():
        if watch.zone_low is None or watch.zone_high is None:
            continue
        quote = adapter.quote(watch.symbol)
        if not quote:
            continue
        executable_price = quote["ask"] if watch.direction == "BUY" else quote["bid"]
        if watch.zone_low <= executable_price <= watch.zone_high:
            enqueue(new_command(
                trade_id=watch.trade_id, action=ExecutionAction.OPEN_MARKET,
                idempotency_key=f"{watch.idempotency_key}:zone-trigger",
                symbol=watch.symbol, direction=watch.direction, volume=watch.volume,
                provider_price=executable_price, stop_loss=watch.stop_loss,
                take_profit=watch.take_profit,
                metadata={**watch.metadata, "zone_command_id": watch.command_id},
            ))


def run() -> None:
    adapter = build_adapter()
    print("Signal Sniper native MT5 executor started (local-only).", flush=True)
    try:
        while True:
            try:
                update_connection(adapter)
                process_zone_watches(adapter)
                command = claim_next()
                if command:
                    settings = get_execution_settings()
                    terminal = adapter.health()
                    if not settings["execution_enabled"]:
                        result = ExecutionResult(command.command_id, command.trade_id, command.action,
                                                 ExecutionStatus.REJECTED,
                                                 "Execution is disabled in execution settings.")
                    elif (
                        command.action in {ExecutionAction.OPEN_EXACT, ExecutionAction.OPEN_MARKET}
                        and not terminal.get("trade_allowed", False)
                    ):
                        result = ExecutionResult(command.command_id, command.trade_id, command.action,
                                                 ExecutionStatus.REJECTED,
                                                 "MT5 automated trading is disabled in the terminal.")
                    elif command.action in {ExecutionAction.OPEN_EXACT, ExecutionAction.OPEN_MARKET} and (
                        get_settings()["emergency_stop"] or not get_settings()["trading_enabled"]
                    ):
                        result = ExecutionResult(command.command_id, command.trade_id, command.action,
                                                 ExecutionStatus.REJECTED,
                                                 "Trading is disabled or Emergency Stop is active.")
                    else:
                        result = adapter.execute(command)
                    record_result(result)
            except Exception as exc:
                set_error(f"Executor error: {type(exc).__name__}")
            time.sleep(float(get_execution_settings()["poll_seconds"]))
    finally:
        adapter.shutdown()


if __name__ == "__main__":
    run()
