"""Platform-neutral contract between Signal Sniper and an MT5 executor.

The Signal Sniper core deals in these data structures only.  Platform adapters
are the sole place where MT5, Wine, macOS, or Windows details are allowed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ExecutionAction(StrEnum):
    OPEN_EXACT = "OPEN_EXACT"
    OPEN_MARKET = "OPEN_MARKET"
    WATCH_ZONE = "WATCH_ZONE"
    CANCEL_PENDING_ENTRY = "CANCEL_PENDING_ENTRY"
    MODIFY_STOP_LOSS = "MODIFY_STOP_LOSS"
    MODIFY_TAKE_PROFIT = "MODIFY_TAKE_PROFIT"
    MOVE_TO_BREAKEVEN = "MOVE_TO_BREAKEVEN"
    CLOSE_PARTIAL = "CLOSE_PARTIAL"
    CLOSE_ALL = "CLOSE_ALL"


class ExecutionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ExecutionCommand:
    command_id: str
    idempotency_key: str
    trade_id: int
    action: ExecutionAction
    symbol: str = "XAUUSD"
    direction: str | None = None
    volume: float | None = None
    provider_price: float | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    position_ticket: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        return data


@dataclass(frozen=True)
class ExecutionResult:
    command_id: str
    trade_id: int
    action: ExecutionAction
    status: ExecutionStatus
    message: str = ""
    mt5_retcode: int | None = None
    order_ticket: int | None = None
    position_ticket: int | None = None
    deal_ticket: int | None = None
    requested_price: float | None = None
    fill_price: float | None = None
    requested_volume: float | None = None
    filled_volume: float | None = None
    take_profit_levels: list[float] = field(default_factory=list)
    final_take_profit: float | None = None
    account: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["status"] = self.status.value
        return data


class ExecutionAdapter(Protocol):
    """Implemented by the macOS and Windows MT5 adapters."""

    def health(self) -> dict[str, Any]: ...

    def execute(self, command: ExecutionCommand) -> ExecutionResult: ...

    def shutdown(self) -> None: ...
