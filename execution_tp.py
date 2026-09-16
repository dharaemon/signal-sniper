"""Platform-independent take-profit parsing and price calculation."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Tuple

_NUMBER = r"\d+(?:\.\d+)?"


def parse_take_profit(value: Any) -> Optional[Tuple[str, float]]:
    """Return (kind, value) where kind is price, pips, or rr."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"", "OPEN"}:
        return None
    match = re.search(rf"\b({_NUMBER})\s*PIPS?\b", text)
    if match:
        return "pips", float(match.group(1))
    match = re.search(rf"\b1\s*:\s*({_NUMBER})\b", text)
    if match:
        multiplier = float(match.group(1))
        if multiplier <= 0:
            raise ValueError("Risk/reward multiplier must be positive.")
        return "rr", multiplier
    match = re.fullmatch(rf"({_NUMBER})", text)
    if match:
        return "price", float(match.group(1))
    return None


def normalize_price(value: float, digits: int) -> float:
    return round(float(value), int(digits))


def calculate_rr_price(entry_price: float, stop_loss: Optional[float],
                       direction: str, multiplier: float, digits: int) -> float:
    if stop_loss is None:
        raise ValueError("A stop-loss is required for risk/reward take profit.")
    entry, stop = float(entry_price), float(stop_loss)
    if entry == stop:
        raise ValueError("Risk distance cannot be zero.")
    if direction == "BUY":
        if stop >= entry:
            raise ValueError("BUY stop-loss must be below entry.")
        target = entry + (entry - stop) * float(multiplier)
    elif direction == "SELL":
        if stop <= entry:
            raise ValueError("SELL stop-loss must be above entry.")
        target = entry - (stop - entry) * float(multiplier)
    else:
        raise ValueError("Direction must be BUY or SELL.")
    return normalize_price(target, digits)


def resolve_take_profits(values: Iterable[Any], *, entry_price: float,
                         stop_loss: Optional[float], direction: str,
                         pip_size: float, digits: int) -> list[float]:
    """Resolve provider targets to broker prices, preserving target order."""
    if direction not in {"BUY", "SELL"}:
        raise ValueError("Direction must be BUY or SELL.")
    resolved: list[float] = []
    for raw in values:
        parsed = parse_take_profit(raw)
        if parsed is None:
            continue
        kind, value = parsed
        if kind == "price":
            target = normalize_price(value, digits)
        elif kind == "pips":
            distance = value * float(pip_size)
            target = normalize_price(entry_price + distance if direction == "BUY" else entry_price - distance, digits)
        else:
            target = calculate_rr_price(entry_price, stop_loss, direction, value, digits)
        resolved.append(target)
    return resolved


def command_take_profit_levels(command: dict[str, Any], *, entry_price: float,
                               pip_size: float, digits: int) -> list[float]:
    """Resolve execution intent with the configured local override taking precedence."""
    metadata = command.get("metadata") or {}
    local_pips = metadata.get("local_take_profit_pips")
    if local_pips is not None:
        raw_targets: list[Any] = [f"{local_pips} pips"]
    else:
        raw_targets = list(metadata.get("provider_take_profit_targets") or [])
        if not raw_targets:
            provider_pips = metadata.get("provider_take_profit_pips")
            if provider_pips is not None:
                raw_targets = [f"{provider_pips} pips"]
            elif command.get("take_profit") is not None:
                raw_targets = [command.get("take_profit")]
            elif command.get("stop_loss") is not None:
                raw_targets = [metadata.get("default_take_profit_rr") or "1:1"]
            else:
                raw_targets = []
    return resolve_take_profits(raw_targets, entry_price=float(entry_price),
                                stop_loss=command.get("stop_loss"), direction=command.get("direction"),
                                pip_size=pip_size, digits=digits)
