"""Durable, local-only command and result queue for the executor.

SQLite avoids the race-prone collection of JSON files formerly proposed for
execution commands.  It contains no credentials.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from execution_contract import ExecutionAction, ExecutionCommand, ExecutionResult

DB_PATH = Path(__file__).with_name("signal_sniper.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS execution_commands (
            command_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            trade_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS execution_results (
            result_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE REFERENCES execution_commands(command_id),
            trade_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)

        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def enqueue(command: ExecutionCommand) -> ExecutionCommand:
    with _connect() as connection:
        existing = connection.execute(
            "SELECT payload FROM execution_commands WHERE idempotency_key = ?",
            (command.idempotency_key,),
        ).fetchone()
        if existing:
            payload = json.loads(existing["payload"])
            return ExecutionCommand(
                command_id=payload["command_id"], idempotency_key=payload["idempotency_key"],
                trade_id=payload["trade_id"], action=ExecutionAction(payload["action"]),
                symbol=payload.get("symbol", "XAUUSD"), direction=payload.get("direction"),
                volume=payload.get("volume"), provider_price=payload.get("provider_price"),
                zone_low=payload.get("zone_low"), zone_high=payload.get("zone_high"),
                stop_loss=payload.get("stop_loss"), take_profit=payload.get("take_profit"),
                position_ticket=payload.get("position_ticket"), metadata=payload.get("metadata", {}),
            )
        connection.execute(
            "INSERT INTO execution_commands(command_id,idempotency_key,trade_id,action,payload,created_at) VALUES(?,?,?,?,?,?)",
            (command.command_id, command.idempotency_key, command.trade_id, command.action.value,
             json.dumps(command.to_dict(), separators=(",", ":")), _now()),
        )
    return command


def new_command(*, trade_id: int, action: ExecutionAction, idempotency_key: str, **kwargs) -> ExecutionCommand:
    return ExecutionCommand(command_id=uuid.uuid4().hex, idempotency_key=idempotency_key,
                            trade_id=trade_id, action=action, **kwargs)


def claim_next() -> ExecutionCommand | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT command_id, payload FROM execution_commands WHERE status='PENDING' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        changed = connection.execute(
            "UPDATE execution_commands SET status='RUNNING', claimed_at=? WHERE command_id=? AND status='PENDING'",
            (_now(), row["command_id"]),
        ).rowcount
        if not changed:
            return None
        payload = json.loads(row["payload"])
    return ExecutionCommand(
        command_id=payload["command_id"], idempotency_key=payload["idempotency_key"],
        trade_id=payload["trade_id"], action=ExecutionAction(payload["action"]),
        symbol=payload.get("symbol", "XAUUSD"), direction=payload.get("direction"),
        volume=payload.get("volume"), provider_price=payload.get("provider_price"),
        zone_low=payload.get("zone_low"), zone_high=payload.get("zone_high"),
        stop_loss=payload.get("stop_loss"), take_profit=payload.get("take_profit"),
        position_ticket=payload.get("position_ticket"), metadata=payload.get("metadata", {}),
    )


def record_result(result: ExecutionResult) -> None:
    payload = json.dumps(result.to_dict(), separators=(",", ":"))
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO execution_results(result_id,command_id,trade_id,payload,created_at) VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, result.command_id, result.trade_id, payload, _now()),
        )
        connection.execute(
            "UPDATE execution_commands SET status=?, completed_at=? WHERE command_id=?",
            (result.status.value, _now(), result.command_id),
        )


def results_for_trade(trade_id: int) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM execution_results WHERE trade_id=? ORDER BY created_at", (trade_id,)
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def pending_zone_watches() -> list[ExecutionCommand]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM execution_commands WHERE action=? AND status='PENDING' ORDER BY created_at",
            (ExecutionAction.WATCH_ZONE.value,),
        ).fetchall()
    commands = []
    for row in rows:
        payload = json.loads(row["payload"])
        commands.append(ExecutionCommand(
            command_id=payload["command_id"], idempotency_key=payload["idempotency_key"],
            trade_id=payload["trade_id"], action=ExecutionAction(payload["action"]),
            symbol=payload.get("symbol", "XAUUSD"), direction=payload.get("direction"),
            volume=payload.get("volume"), provider_price=payload.get("provider_price"),
            zone_low=payload.get("zone_low"), zone_high=payload.get("zone_high"),
            stop_loss=payload.get("stop_loss"), take_profit=payload.get("take_profit"),
            position_ticket=payload.get("position_ticket"), metadata=payload.get("metadata", {}),
        ))
    return commands


def cancel_zone_watches(trade_id: int) -> int:
    """Cancel untriggered local zone watches for a trade (no broker order)."""
    with _connect() as connection:
        return connection.execute(
            "UPDATE execution_commands SET status='CANCELLED', completed_at=? WHERE trade_id=? AND action=? AND status='PENDING'",
            (_now(), trade_id, ExecutionAction.WATCH_ZONE.value),
        ).rowcount


def cancel_pending_entries() -> int:
    """Cancel queued entries/watches before the executor can submit them."""
    with _connect() as connection:
        placeholders = ",".join("?" for _ in (ExecutionAction.OPEN_EXACT, ExecutionAction.OPEN_MARKET, ExecutionAction.WATCH_ZONE))
        return connection.execute(
            f"UPDATE execution_commands SET status='CANCELLED', completed_at=? WHERE action IN ({placeholders}) AND status='PENDING'",
            (_now(), ExecutionAction.OPEN_EXACT.value, ExecutionAction.OPEN_MARKET.value, ExecutionAction.WATCH_ZONE.value),
        ).rowcount
