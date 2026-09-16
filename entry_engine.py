from dataclasses import dataclass
from typing import Optional

from signal_state import SignalStateManager
from bot_settings import get_settings


@dataclass
class EntryDecision:
    action: str
    entry_type: Optional[str] = None
    signal_low: Optional[float] = None
    signal_high: Optional[float] = None
    execution_price: Optional[float] = None
    reason: str = ""


class EntryDecisionEngine:
    """Determines provider-price, provider-zone, or market/no-trade behavior."""

    def process_provider_entry(self, state: SignalStateManager) -> Optional[EntryDecision]:
        if not state.has_active_trade():
            return None
        trade = state.get_trade()
        if trade.entry_locked:
            return EntryDecision(action="IGNORED", reason="Entry is already locked.")
        if trade.entry_low is None:
            return EntryDecision(action="WAIT", reason="No provider entry received yet.")

        if trade.entry_type == "PRICE":
            price = trade.entry_low
            state.request_exact_entry()
            return EntryDecision("EXECUTE_PROVIDER_PRICE", "PRICE", price, price, price,
                                 "Provider supplied an exact entry price.")

        if trade.entry_type == "ZONE":
            state.wait_for_zone()
            return EntryDecision("WAIT_FOR_ZONE", "ZONE", trade.entry_low, trade.entry_high, None,
                                 "Provider supplied an entry zone. Wait for XAUUSD price to enter the zone.")

        return EntryDecision("WAIT", reason=f"Unknown entry type: {trade.entry_type}")

    def process_entry_timeout(self, state: SignalStateManager) -> Optional[EntryDecision]:
        if not state.has_active_trade():
            return None
        trade = state.get_trade()
        if trade.entry_locked:
            return EntryDecision(action="IGNORED", reason="Entry is already locked.")

        if trade.entry_type == "PRICE" and trade.entry_low is not None:
            price = trade.entry_low
            state.request_exact_entry()
            return EntryDecision("EXECUTE_PROVIDER_PRICE", "PRICE", price, price, price,
                                 "Provider supplied an exact entry price.")

        if trade.entry_type == "ZONE" and trade.entry_low is not None:
            state.wait_for_zone()
            return EntryDecision("WAIT_FOR_ZONE", "ZONE", trade.entry_low, trade.entry_high, None,
                                 "Provider supplied a zone. Wait for market price to enter the zone.")

        settings = get_settings()
        if settings["wait_for_entry"]:
            # WAIT mode means a missing provider entry is a NO-TRADE outcome.
            state.cancel()
            return EntryDecision("NO_TRADE", "NONE", reason=
                                 f"No provider entry received during the {settings['entry_wait_seconds']}-second wait window.")

        if not settings["trading_enabled"] or settings["emergency_stop"]:
            state.cancel()
            return EntryDecision("NO_TRADE", "NONE", reason="Trading is disabled or Emergency Stop is active.")

        state.request_market_entry()
        return EntryDecision("EXECUTE_MARKET", "MARKET", reason="Entry Wait is OFF; execute market entry immediately.")
