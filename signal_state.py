from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class TradeState:
    trade_id: int

    symbol: str = "XAUUSD"
    direction: Optional[str] = None

    # Provider signal entry
    entry_type: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None

    # Actual executed MT5 entry
    actual_entry_price: Optional[float] = None

    stop_loss: Optional[float] = None

    take_profit_values: list[str] = field(default_factory=list)
    tp1: Optional[str] = None
    tp2: Optional[str] = None
    tp3: Optional[str] = None
    calculated_take_profit_levels: list[float] = field(default_factory=list)
    final_take_profit: Optional[float] = None

    # Execution
    execution_type: Optional[str] = None
    execution_status: str = "NOT_EXECUTED"
    entry_locked: bool = False
    execution_command_id: Optional[str] = None
    order_ticket: Optional[int] = None
    position_ticket: Optional[int] = None
    deal_ticket: Optional[int] = None
    execution_error: Optional[str] = None

    status: str = "WAITING_FOR_ENTRY"

    management_action: Optional[str] = None
    profit_update: Optional[str] = None
    last_event: Optional[str] = None

    activation_message_id: Optional[int] = None
    latest_message_id: Optional[int] = None

    source_chat_name: Optional[str] = None
    source_chat_id: Optional[int] = None


class SignalStateManager:

    def __init__(self):
        self.active_trade: Optional[TradeState] = None
        # Execution results live beyond a listener restart. A process-local
        # counter beginning at 1 caused an old failed result to be applied to
        # the next trade after every restart.
        self.next_trade_id = int(time.time() * 1_000_000)

    # ========================================================
    # TRADE CREATION
    # ========================================================

    def create_trade(
        self,
        direction: str,
        message_id: Optional[int] = None,
        chat_name: Optional[str] = None,
        chat_id: Optional[int] = None,
        symbol: str = "XAUUSD",
    ) -> TradeState:

        trade = TradeState(
            trade_id=self.next_trade_id,
            symbol=symbol,
            direction=direction,
            activation_message_id=message_id,
            latest_message_id=message_id,
            source_chat_name=chat_name,
            source_chat_id=chat_id,
        )

        self.next_trade_id += 1
        self.active_trade = trade

        return trade

    # ========================================================
    # ACTIVE TRADE
    # ========================================================

    def has_active_trade(self) -> bool:
        return self.active_trade is not None

    def get_trade(self) -> Optional[TradeState]:
        return self.active_trade

    # ========================================================
    # PROVIDER ENTRY
    # ========================================================

    def update_entry(
        self,
        entry_type: str,
        entry_low: float,
        entry_high: Optional[float] = None,
        message_id: Optional[int] = None,
    ):

        if not self.active_trade:
            return

        trade = self.active_trade

        if trade.entry_locked:
            return

        trade.entry_type = entry_type
        trade.entry_low = entry_low
        trade.entry_high = (
            entry_high
            if entry_high is not None
            else entry_low
        )

        if message_id is not None:
            trade.latest_message_id = message_id

        trade.last_event = "ENTRY_UPDATE"

    # ========================================================
    # EXACT PROVIDER PRICE
    # ========================================================

        # ========================================================
    # BACKWARD COMPATIBILITY
    # ========================================================

    def lock_entry(
        self,
        execution_type: str,
    ):
        """
        Compatibility method for older tests/code.

        The new system uses:
            lock_exact_entry()
            lock_market_entry()

        This method keeps the old API working.
        """

        if not self.active_trade:
            return

        trade = self.active_trade

        if trade.entry_locked:
            return

        if execution_type == "PROVIDER_ENTRY":

            if trade.entry_low is None:
                return

            self.lock_exact_entry(
                actual_entry_price=trade.entry_low
            )

        elif execution_type == "MARKET_ENTRY":

            self.lock_market_entry()

        else:

            trade.execution_type = execution_type
            trade.entry_locked = True
            trade.status = "OPEN"
            trade.last_event = "ENTRY_LOCKED"

    def lock_exact_entry(
        self,
        actual_entry_price: float,
    ):

        if not self.active_trade:
            return

        trade = self.active_trade

        if trade.entry_locked:
            return

        trade.actual_entry_price = actual_entry_price

        trade.execution_type = "PROVIDER_ENTRY"
        trade.execution_status = "EXECUTED"

        trade.entry_locked = True
        trade.status = "OPEN"

        trade.last_event = "ENTRY_EXECUTED"

    def request_exact_entry(self):
        """Record an exact-price execution intent before the broker confirms it."""
        if not self.active_trade or self.active_trade.entry_locked:
            return
        trade = self.active_trade
        trade.execution_type = "PROVIDER_ENTRY"
        trade.execution_status = "PENDING_SUBMISSION"
        trade.status = "SUBMITTING_ENTRY"
        trade.last_event = "ENTRY_SUBMITTED"

    def request_market_entry(self):
        """Record a market execution intent before the broker confirms it."""
        if not self.active_trade or self.active_trade.entry_locked:
            return
        trade = self.active_trade
        trade.execution_type = "MARKET_ENTRY"
        trade.execution_status = "PENDING_SUBMISSION"
        trade.status = "SUBMITTING_ENTRY"
        trade.last_event = "MARKET_SUBMITTED"

    def apply_execution_result(self, result: dict):
        """Apply a normalized executor result without turning management results into entry fills."""
        if not self.active_trade:
            return
        trade = self.active_trade
        if int(result.get("trade_id", -1)) != trade.trade_id:
            return

        trade.execution_command_id = result.get("command_id") or trade.execution_command_id
        trade.order_ticket = result.get("order_ticket") or trade.order_ticket
        trade.position_ticket = result.get("position_ticket") or trade.position_ticket
        trade.deal_ticket = result.get("deal_ticket") or trade.deal_ticket
        if result.get("take_profit_levels"):
            trade.calculated_take_profit_levels = list(result["take_profit_levels"])
        if result.get("final_take_profit") is not None:
            trade.final_take_profit = result.get("final_take_profit")

        status = result.get("status")
        action = result.get("action")
        entry_actions = {"OPEN_EXACT", "OPEN_MARKET"}

        if action in entry_actions:
            if status == "FILLED":
                trade.actual_entry_price = result.get("fill_price") or trade.actual_entry_price
                trade.execution_status = "EXECUTED"
                trade.entry_locked = True
                trade.status = "OPEN"
                trade.execution_error = None
                trade.last_event = "ENTRY_EXECUTED"
            elif status in {"REJECTED", "FAILED", "CANCELLED"}:
                trade.execution_error = result.get("message") or "MT5 execution was not accepted."
                trade.execution_status = status
                trade.status = "ENTRY_FAILED"
                trade.last_event = "ENTRY_FAILED"
            return

        if action == "CLOSE_ALL":
            if status == "FILLED":
                trade.execution_status = "CLOSED"
                trade.status = "CLOSED"
                trade.management_action = "CLOSE_ALL"
                trade.execution_error = None
                trade.last_event = "CLOSE_ALL_CONFIRMED"
            elif status in {"REJECTED", "FAILED", "CANCELLED"}:
                trade.execution_error = result.get("message") or "Close-all was not accepted."
                trade.execution_status = "CLOSE_FAILED"
                trade.status = "OPEN"
                trade.last_event = "CLOSE_ALL_FAILED"
            return

        if action == "MODIFY_STOP_LOSS":
            if status == "FILLED":
                trade.execution_error = None
                trade.last_event = "SL_CONFIRMED"
            else:
                trade.execution_error = result.get("message") or "Stop-loss update failed."
                trade.last_event = "MANAGEMENT_FAILED"
            return

        if action == "MODIFY_TAKE_PROFIT":
            if status == "FILLED":
                trade.execution_error = None
                trade.last_event = "TP_CONFIRMED"
            else:
                trade.execution_error = result.get("message") or "Take-profit update failed."
                trade.last_event = "MANAGEMENT_FAILED"
            return

        if action == "MOVE_TO_BREAKEVEN":
            if status == "FILLED":
                trade.execution_error = None
                trade.management_action = "BREAKEVEN"
                trade.last_event = "BREAKEVEN_CONFIRMED"
            else:
                trade.execution_error = result.get("message") or "Breakeven update failed."
                trade.last_event = "MANAGEMENT_FAILED"

    # ========================================================
    # MARKET ENTRY
    # ========================================================

    def lock_market_entry(
        self,
        actual_entry_price: Optional[float] = None,
    ):

        if not self.active_trade:
            return

        trade = self.active_trade

        if trade.entry_locked:
            return

        trade.actual_entry_price = actual_entry_price

        trade.execution_type = "MARKET_ENTRY"

        if actual_entry_price is None:
            trade.execution_status = "PENDING_MT5"
        else:
            trade.execution_status = "EXECUTED"

        trade.entry_locked = True
        trade.status = "OPEN"

        trade.last_event = "MARKET_ENTRY"

    # ========================================================
    # WAIT FOR ZONE
    # ========================================================

    def wait_for_zone(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        if trade.entry_locked:
            return

        trade.execution_type = "PROVIDER_ZONE"
        trade.execution_status = "WAITING_FOR_PRICE"
        trade.status = "WAITING_FOR_ZONE"

        trade.last_event = "ZONE_WAIT"

    # ========================================================
    # STOP LOSS
    # ========================================================

    def update_stop_loss(
        self,
        stop_loss: float,
        message_id: Optional[int] = None,
    ):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.stop_loss = stop_loss

        if message_id is not None:
            trade.latest_message_id = message_id

        trade.last_event = "SL_UPDATE"

    # ========================================================
    # TAKE PROFITS
    # ========================================================

    def update_take_profits(
        self,
        take_profit_values: list[str],
        message_id: Optional[int] = None,
    ):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.take_profit_values = take_profit_values

        trade.tp1 = (
            take_profit_values[0]
            if len(take_profit_values) > 0
            else None
        )

        trade.tp2 = (
            take_profit_values[1]
            if len(take_profit_values) > 1
            else None
        )

        trade.tp3 = (
            take_profit_values[2]
            if len(take_profit_values) > 2
            else None
        )

        if message_id is not None:
            trade.latest_message_id = message_id

        trade.last_event = "TP_UPDATE"

    # ========================================================
    # MANAGEMENT
    # ========================================================

    def hold(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.management_action = "HOLD"

        if trade.entry_locked:
            trade.status = "OPEN"

        trade.last_event = "HOLD"

    def update_profit(self, profit_text: str):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.profit_update = profit_text
        trade.last_event = "PROFIT_UPDATE"

    def close_partial(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.management_action = "CLOSE_PARTIAL"
        trade.status = "PARTIALLY_CLOSED"
        trade.last_event = "CLOSE_PARTIAL"

    def breakeven(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.management_action = "BREAKEVEN"
        trade.last_event = "BREAKEVEN"

    def close_all(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.management_action = "CLOSE_ALL"
        if trade.entry_locked:
            trade.status = "CLOSING"
            trade.execution_status = "CLOSING"
            trade.last_event = "CLOSE_ALL_SUBMITTED"
        else:
            trade.status = "CLOSED"
            trade.execution_status = "CLOSED"
            trade.last_event = "CLOSE_ALL"

    def cancel(self):

        if not self.active_trade:
            return

        trade = self.active_trade

        trade.management_action = "CANCEL"
        trade.status = "CANCELLED"
        trade.execution_status = "CANCELLED"
        trade.last_event = "CANCEL"

    # ========================================================
    # CLEAR
    # ========================================================

    def clear_trade(self):
        self.active_trade = None

    # ========================================================
    # DISPLAY
    # ========================================================

    def display(self):

        print("\n" + "=" * 70)
        print("📊 SIGNAL STATE")
        print("=" * 70)

        if not self.active_trade:
            print("No active trade.")
            print("=" * 70)
            return

        trade = self.active_trade

        print(f"Trade ID          : {trade.trade_id}")
        print(f"Symbol            : {trade.symbol}")
        print(f"Direction         : {trade.direction}")

        print(f"Entry Type        : {trade.entry_type}")
        print(f"Entry Low         : {trade.entry_low}")
        print(f"Entry High        : {trade.entry_high}")

        print(f"Actual Entry      : {trade.actual_entry_price}")

        print(f"Stop Loss         : {trade.stop_loss}")

        print(f"TP1 DEFAULT       : {trade.tp1}")
        print(f"TP2               : {trade.tp2}")
        print(f"TP3               : {trade.tp3}")

        print(f"Execution Type    : {trade.execution_type}")
        print(f"Execution Status  : {trade.execution_status}")
        print(f"Entry Locked      : {trade.entry_locked}")

        print(f"Status            : {trade.status}")

        print(f"Management        : {trade.management_action}")
        print(f"Profit Update     : {trade.profit_update}")
        print(f"Last Event        : {trade.last_event}")

        print(f"Source            : {trade.source_chat_name}")
        print(f"Chat ID           : {trade.source_chat_id}")

        print(f"Activation ID     : {trade.activation_message_id}")
        print(f"Latest ID         : {trade.latest_message_id}")

        print("=" * 70)
