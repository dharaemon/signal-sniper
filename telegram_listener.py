import asyncio
import os
import re
import time
from collections import deque

from dotenv import load_dotenv
from telethon import TelegramClient, events
from trade_notifications import (
    create_event,
    create_approval_request,
    update_approval_request,
    get_approval_request,
)

from entry_engine import EntryDecisionEngine
from signal_analyzer import analyze_messages
from signal_state import SignalStateManager
from bot_settings import get_settings
from dashboard_state import write_state
from mt5_connection import get_connection
from trade_settings import get_lot_size, get_local_stop_loss_pips, get_local_take_profit_pips, get_default_rr
from mt5_commands import get_pending_commands, mark_listener_ack
from execution_contract import ExecutionAction
from execution_settings import get_execution_settings
from execution_store import new_command, enqueue, results_for_trade


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")

SESSION_NAME = "signal_sniper"

CONTEXT_SIZE = 3
DEFAULT_ENTRY_WAIT_SECONDS = 10


# ============================================================
# TELEGRAM
# ============================================================

client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH,
)


# ============================================================
# STATE
# ============================================================

state = SignalStateManager()

entry_engine = EntryDecisionEngine()

recent_messages = deque(
    maxlen=CONTEXT_SIZE
)

entry_timer_task = None
approval_watch_task = None
processed_execution_results = set()


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize(text: str) -> str:
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    return re.sub(
        r"\s+",
        " ",
        text.strip()
    )


def _gold_direction(text: str):
    """Find one unambiguous BUY/SELL close to GOLD/XAUUSD in either order."""
    words = re.findall(r"[A-Z]+", normalize(text).upper())
    instruments = {index for index, word in enumerate(words) if word in {"GOLD", "XAU", "XAUUSD"}}
    nearby = {
        word
        for index, word in enumerate(words)
        if word in {"BUY", "SELL"} and any(abs(index - instrument) <= 3 for instrument in instruments)
    }
    return next(iter(nearby)) if len(nearby) == 1 else None


def is_gold_activation(text: str) -> bool:
    return _gold_direction(text) is not None


def extract_direction(text: str):
    return _gold_direction(text)


def is_close_all_command(text: str) -> bool:
    normalized = normalize(text).upper()
    if "CLOSE ALL" in normalized:
        return True
    return any(
        re.search(rf"\b{re.escape(keyword)}\b", normalized)
        for keyword in ("COLLECT", "EXIT", "CLOSE", "CLAIM")
    )


def is_collect_command(text: str) -> bool:
    # Backward-compatible alias used by older tests/code.
    return is_close_all_command(text)


# ============================================================
# ENTRY EXTRACTION
# ============================================================

def extract_entry(text: str):

    text = normalize(text).upper()

    # --------------------------------------------------------
    # ENTRY ZONE FIRST
    # --------------------------------------------------------

    # ENTRY:4397-4398
    match = re.search(
        r"\bENTRY\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)\s*[-–]\s*"
        r"(\d+(?:\.\d+)?)",
        text
    )

    if match:

        first = float(match.group(1))
        second = float(match.group(2))

        return {
            "entry_type": "ZONE",
            "entry_low": min(first, second),
            "entry_high": max(first, second),
        }

    # 4397-4398 ENTRY
    match = re.search(
        r"\b"
        r"(\d+(?:\.\d+)?)\s*[-–]\s*"
        r"(\d+(?:\.\d+)?)"
        r"\s+ENTRY\b",
        text
    )

    if match:

        first = float(match.group(1))
        second = float(match.group(2))

        return {
            "entry_type": "ZONE",
            "entry_low": min(first, second),
            "entry_high": max(first, second),
        }

    # ZONE:4391-4388
    match = re.search(
        r"\bZONE\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)\s*[-–]\s*"
        r"(\d+(?:\.\d+)?)",
        text
    )

    if match:

        first = float(match.group(1))
        second = float(match.group(2))

        return {
            "entry_type": "ZONE",
            "entry_low": min(first, second),
            "entry_high": max(first, second),
        }

    # --------------------------------------------------------
    # EXACT PRICE
    # --------------------------------------------------------

    # ENTRY:4397
    # ENTRY 4397
    match = re.search(
        r"\bENTRY\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)\b",
        text
    )

    if match:

        price = float(match.group(1))

        return {
            "entry_type": "PRICE",
            "entry_low": price,
            "entry_high": price,
        }

    # 4397 ENTRY
    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s+ENTRY\b",
        text
    )

    if match:

        price = float(match.group(1))

        return {
            "entry_type": "PRICE",
            "entry_low": price,
            "entry_high": price,
        }

    # ZONE 4424
    match = re.search(
        r"\bZONE\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)\b",
        text
    )

    if match:

        price = float(match.group(1))

        return {
            "entry_type": "ZONE",
            "entry_low": price,
            "entry_high": price,
        }

    return None


# ============================================================
# STOP LOSS
# ============================================================

def extract_stop_loss(text: str):

    text = normalize(text).upper()

    match = re.search(
        r"\b(?:CUT\s*LOSS|CUTLOSS|SL)"
        r"\s*[:\-]?\s*"
        r"(\d+(?:\.\d+)?)",
        text
    )

    if match:
        return float(match.group(1))

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def extract_take_profits(text: str):
    """Extract only executable TP tokens; ignore provider chatter after the target."""
    text = normalize(text).upper()
    target_pattern = r"OPEN|1\s*:\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*PIPS?|\d+(?:\.\d+)?"

    numbered = re.findall(
        rf"\bTP\s*\d+\s*[:\-]?\s*({target_pattern})",
        text,
    )
    if numbered:
        return [re.sub(r"\s+", " ", value.strip()) for value in numbered]

    match = re.search(r"\bTP\s*[:\-]?\s*(.+)", text)
    if not match:
        return None

    raw = match.group(1).strip()
    raw = re.split(
        r"\b(?:CUT\s*LOSS|CUTLOSS|STOP\s*LOSS|SL)\b\s*[:\-]?",
        raw,
        maxsplit=1,
    )[0].strip()

    targets = []
    for fragment in re.split(r"[/,|]+", raw):
        token = re.search(rf"\b({target_pattern})\b", fragment.strip())
        if token:
            targets.append(re.sub(r"\s+", " ", token.group(1).strip()))

    return targets or None


def take_profit_value(value):
    """Return a broker price or pip distance from a provider TP fragment."""
    if value is None:
        return None, None
    text = str(value).strip().upper()
    numeric = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    if numeric:
        return float(numeric.group(1)), None
    pips = re.fullmatch(r"(\d+(?:\.\d+)?)\s*PIPS?", text)
    if pips:
        return None, float(pips.group(1))
    return None, None


# ============================================================
# MESSAGE RECORD
# ============================================================

def build_message_record(event):

    message = event.message

    chat = getattr(
        event,
        "chat",
        None
    )

    chat_name = None
    chat_id = None

    if chat:

        chat_name = (
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or getattr(chat, "first_name", None)
            or "Unknown"
        )

        chat_id = getattr(
            chat,
            "id",
            None
        )

    return {
        "chat_name": chat_name,
        "chat_id": chat_id,
        "message_id": message.id,
        "timestamp": (
            message.date.isoformat()
            if message.date
            else None
        ),
        "text": message.text or "",
    }


# ============================================================
# AI ANALYSIS
# ============================================================

async def analyze_context():

    messages = list(
        recent_messages
    )

    if not messages:
        return None

    try:

        result = await asyncio.to_thread(
            analyze_messages,
            messages
        )

        return result

    except Exception as exc:

        print(
            f"⚠️ AI analysis error: {exc}"
        )

        return None


# ============================================================
# APPLY AI RESULT
# ============================================================

def apply_analysis(result):

    if not result:
        return

    if not state.has_active_trade():
        return

    event_type = result.event_type

    # --------------------------------------------------------
    # NEVER LET AI CREATE A NEW TRADE
    # --------------------------------------------------------

    if event_type == "NEW_SIGNAL":

        print(
            "🤖 AI EVENT: NEW_SIGNAL "
            "→ ignored because trade already exists."
        )

        return

    print(
        f"🤖 AI EVENT: {event_type}"
    )

    trade = state.get_trade()

    # --------------------------------------------------------
    # ENTRY FALLBACK
    # --------------------------------------------------------

    if (
        result.entry_low is not None
        and not trade.entry_locked
    ):

        entry_type = (
            result.entry_type
            or "PRICE"
        )

        state.update_entry(
            entry_type=entry_type,
            entry_low=result.entry_low,
            entry_high=result.entry_high,
        )

        print(
            f"📍 AI ENTRY: "
            f"{result.entry_low}"
            f" - "
            f"{result.entry_high}"
        )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if result.stop_loss is not None:

        state.update_stop_loss(
            result.stop_loss
        )
        if get_local_stop_loss_pips() is None:
            submit_stop_loss(state.get_trade(), result.stop_loss, "AI stop-loss update.")
            if state.get_trade().take_profit_values:
                provider_tp, _ = take_profit_value(state.get_trade().tp1)
                submit_take_profit(state.get_trade(), provider_tp, "Recalculate take profit after AI stop-loss update.")

        print(
            f"🛡️ AI SL: "
            f"{result.stop_loss}"
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if result.take_profit_values:

        state.update_take_profits(
            result.take_profit_values
        )
        if get_local_take_profit_pips() is None:
            provider_tp, _ = take_profit_value(state.get_trade().tp1)
            submit_take_profit(state.get_trade(), provider_tp, "AI take-profit update.")

        print(
            "🎯 AI TP: "
            + " / ".join(
                result.take_profit_values
            )
        )

        print(
            "🎯 DEFAULT CLAIM TARGET: "
            f"TP1 = {result.take_profit_values[0]}"
        )

    # --------------------------------------------------------
    # MANAGEMENT
    # --------------------------------------------------------

    if event_type == "HOLD":

        state.hold()

        print("✋ HOLD")

    elif event_type == "PROFIT_UPDATE":

        state.update_profit(
            result.explanation
        )

        print(
            f"📈 PROFIT UPDATE: "
            f"{result.explanation}"
        )

    elif event_type == "CLOSE_PARTIAL":

        state.close_partial()

        print(
            "🟡 CLOSE PARTIAL"
        )

    elif event_type == "CLOSE_ALL":

        state.close_all()
        submit_close_all(state.get_trade(), "Provider requested CLOSE ALL.")

        print(
            "🔴 CLOSE ALL"
        )

    elif event_type == "BREAKEVEN":

        state.breakeven()
        submit_breakeven(state.get_trade(), "Provider requested breakeven.")

        print(
            "🟢 BREAKEVEN"
        )

    elif event_type == "CANCEL":

        state.cancel()

        print(
            "❌ CANCEL"
        )


# ============================================================
# DETERMINISTIC MESSAGE PROCESSING
# ============================================================

def process_deterministic(
    text: str,
    message_id: int,
):

    if not state.has_active_trade():
        return

    trade = state.get_trade()

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    if not trade.entry_locked:

        entry = extract_entry(text)

        if entry:

            state.update_entry(
                entry_type=entry["entry_type"],
                entry_low=entry["entry_low"],
                entry_high=entry["entry_high"],
                message_id=message_id,
            )

            print(
                f"📍 ENTRY RECEIVED: "
                f"{entry['entry_low']}"
                f" - "
                f"{entry['entry_high']}"
            )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = extract_stop_loss(text)

    if sl is not None:

        state.update_stop_loss(
            sl,
            message_id=message_id,
        )
        if get_local_stop_loss_pips() is None:
            submit_stop_loss(state.get_trade(), sl, "Provider stop-loss update.")
            if state.get_trade().take_profit_values:
                provider_tp, _ = take_profit_value(state.get_trade().tp1)
                submit_take_profit(state.get_trade(), provider_tp, "Recalculate take profit after provider stop-loss update.")

        print(
            f"🛡️ CUTLOSS RECEIVED: {sl}"
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    tps = extract_take_profits(text)

    if tps:

        state.update_take_profits(
            tps,
            message_id=message_id,
        )
        if get_local_take_profit_pips() is None:
            provider_tp, _ = take_profit_value(state.get_trade().tp1)
            submit_take_profit(state.get_trade(), provider_tp, "Provider take-profit update.")

        print(
            "🎯 TP RECEIVED: "
            + " / ".join(tps)
        )

        print(
            f"🎯 DEFAULT CLAIM TARGET: "
            f"TP1 = {tps[0]}"
        )

    # --------------------------------------------------------
    # MANAGEMENT
    # --------------------------------------------------------

    upper = text.upper()

    if is_close_all_command(text):

        state.close_all()
        submit_close_all(state.get_trade(), "Provider requested account close-all keyword.")

        print(
            "🔴 CLOSE ALL"
        )

    elif (
        "CLOSE MOST" in upper
        or "CLOSE PARTIAL" in upper
        or "CLOSE HALF" in upper
    ):

        state.close_partial()

        print(
            "🟡 CLOSE PARTIAL"
        )

    elif (
        "BREAKEVEN" in upper
        or "BREAK EVEN" in upper
    ):

        state.breakeven()
        submit_breakeven(state.get_trade(), "Provider requested breakeven.")

        print(
            "🟢 BREAKEVEN"
        )

    elif "STILL HOLDING" in upper:

        state.hold()

        print(
            "✋ HOLD"
        )

    elif (
        "RUNNING" in upper
        or "PIPS" in upper
        or "PROFIT" in upper
    ):

        state.update_profit(
            text
        )

        print(
            f"📈 PROFIT UPDATE: {text}"
        )


# ============================================================
# CHECK WHETHER AI IS NEEDED
# ============================================================

def message_needs_ai(text: str) -> bool:

    upper = text.upper()

    # Obvious messages are already handled
    # deterministically.

    if extract_entry(text) is not None:
        return False

    if extract_stop_loss(text) is not None:
        return False

    if extract_take_profits(text) is not None:
        return False

    obvious_management = [
        "STILL HOLDING",
        "CLOSE ALL",
        "COLLECT",
        "EXIT",
        "CLOSE",
        "CLAIM",
        "CLOSE MOST",
        "CLOSE PARTIAL",
        "CLOSE HALF",
        "BREAKEVEN",
        "BREAK EVEN",
        "RUNNING",
    ]

    for phrase in obvious_management:

        if phrase in upper:
            return False

    # Messages containing explicit PIPS/PROFIT
    # are also deterministic profit updates.

    if "PIPS" in upper:
        return False

    if "PROFIT" in upper:
        return False

    return True


# ============================================================
# APPROVAL / EXECUTION FLOW
# ============================================================

def trade_details_dict(trade):
    return {
        "symbol": trade.symbol,
        "direction": trade.direction,
        "entry_type": trade.entry_type,
        "entry_low": trade.entry_low,
        "entry_high": trade.entry_high,
        "actual_entry_price": trade.actual_entry_price,
        "stop_loss": trade.stop_loss,
        "tp1": trade.tp1,
        "tp2": trade.tp2,
        "tp3": trade.tp3,
        "source_chat_name": trade.source_chat_name,
        "source_chat_id": trade.source_chat_id,
        "activation_message_id": trade.activation_message_id,
        "latest_message_id": trade.latest_message_id,
        "lot_size": get_lot_size(),
    }


def emit_trade_event(event_type, trade, reason=None):
    calculated = trade.calculated_take_profit_levels
    return create_event(
        trade_id=trade.trade_id,
        event_type=event_type,
        symbol=trade.symbol,
        direction=trade.direction,
        entry_type=trade.entry_type,
        entry_low=trade.entry_low,
        entry_high=trade.entry_high,
        actual_entry_price=trade.actual_entry_price,
        stop_loss=trade.stop_loss,
        tp1=calculated[0] if len(calculated) > 0 else trade.tp1,
        tp2=calculated[1] if len(calculated) > 1 else trade.tp2,
        tp3=calculated[2] if len(calculated) > 2 else trade.tp3,
        execution_type=trade.execution_type,
        execution_status=trade.execution_status,
        status=trade.status,
        source_chat_name=trade.source_chat_name,
        source_chat_id=trade.source_chat_id,
        activation_message_id=trade.activation_message_id,
        latest_message_id=trade.latest_message_id,
        management_action=trade.management_action,
        lot_size=get_lot_size(),
        reason=reason,
    )


def submit_entry_decision(decision):
    """Send a core entry decision to the platform-neutral executor queue."""
    if not decision or not state.has_active_trade():
        return None
    trade = state.get_trade()
    action_map = {
        "EXECUTE_PROVIDER_PRICE": ExecutionAction.OPEN_EXACT,
        "EXECUTE_MARKET": ExecutionAction.OPEN_MARKET,
        "WAIT_FOR_ZONE": ExecutionAction.WATCH_ZONE,
    }
    action = action_map.get(decision.action)
    if not action:
        return None
    local_sl_pips = get_local_stop_loss_pips()
    local_tp_pips = get_local_take_profit_pips()
    provider_take_profit, provider_take_profit_pips = take_profit_value(trade.tp1)
    command = new_command(
        trade_id=trade.trade_id,
        action=action,
        idempotency_key=f"trade:{trade.trade_id}:{action.value}",
        symbol=trade.symbol,
        direction=trade.direction,
        volume=get_lot_size(),
        provider_price=decision.execution_price,
        zone_low=decision.signal_low,
        zone_high=decision.signal_high,
        # A configured local pip value deliberately overrides the channel SL/TP
        # for broker execution while retaining the source values for journaling.
        stop_loss=None if local_sl_pips is not None else trade.stop_loss,
        take_profit=None if local_tp_pips is not None else provider_take_profit,
        metadata={
            "entry_type": decision.entry_type,
            "source": "telegram_listener",
            "local_stop_loss_pips": local_sl_pips,
            "local_take_profit_pips": local_tp_pips,
            "provider_take_profit_pips": provider_take_profit_pips,
            "provider_take_profit_targets": list(trade.take_profit_values),
            "default_take_profit_rr": get_default_rr(),
        },
    )
    queued = enqueue(command)
    trade.execution_command_id = queued.command_id
    override_note = []
    if local_sl_pips is not None:
        override_note.append(f"local SL {local_sl_pips:g} pips")
    if local_tp_pips is not None:
        override_note.append(f"local TP {local_tp_pips:g} pips")
    reason = decision.reason + ("; using " + ", ".join(override_note) if override_note else "")
    emit_trade_event("EXECUTION_SUBMITTED", trade, reason)
    return queued


def publish_dashboard_state():
    """Make a signal lifecycle change visible to the dashboard immediately."""
    try:
        write_state(state)
    except Exception as exc:
        print(f"⚠️ Dashboard state error: {type(exc).__name__}")


def start_automatic_entry(trade):
    """Start the entry path immediately after automatic approval.

    Entry Wait controls only the wait for a missing provider entry. When it is
    OFF, a signal without one must submit its market entry immediately.
    """
    global entry_timer_task
    settings = get_settings()
    if trade.entry_low is not None:
        decision = entry_engine.process_provider_entry(state)
    elif settings["wait_for_entry"]:
        entry_timer_task = asyncio.create_task(entry_window())
        print(f"⏱️ Entry window started: {settings['entry_wait_seconds']} seconds")
        publish_dashboard_state()
        return None
    else:
        decision = entry_engine.process_entry_timeout(state)

    if not decision:
        return None
    queued = submit_entry_decision(decision)
    if queued:
        emit_trade_event(
            "AUTO_EXECUTION_SUBMITTED",
            state.get_trade(),
            "No Auth is ON. Entry was submitted to the local MT5 executor.",
        )
    publish_dashboard_state()
    print(f"\n⚡ AUTO ENTRY: {decision.action} — {decision.reason}")
    return queued


def execute_provider_entry_if_ready():
    """Execute a provider entry received in a follow-up Telegram message."""
    global entry_timer_task
    if not state.has_active_trade():
        return None
    trade = state.get_trade()
    if trade.status != "WAITING_FOR_ENTRY" or trade.entry_low is None:
        return None
    if entry_timer_task and not entry_timer_task.done():
        entry_timer_task.cancel()
        entry_timer_task = None
    decision = entry_engine.process_provider_entry(state)
    queued = submit_entry_decision(decision) if decision else None
    if queued:
        emit_trade_event(
            "AUTO_EXECUTION_SUBMITTED",
            state.get_trade(),
            "Provider entry received and submitted to the local MT5 executor.",
        )
    publish_dashboard_state()
    return queued


def submit_close_all(trade, reason):
    """Close every currently open MT5 position in the connected account."""
    trade_id = trade.trade_id if trade is not None else int(time.time() * 1_000_000)
    symbol = trade.symbol if trade is not None else get_execution_settings()["symbol"]
    direction = trade.direction if trade is not None else None
    command = new_command(
        trade_id=trade_id,
        action=ExecutionAction.CLOSE_ALL,
        idempotency_key=f"account:close-all:{trade_id}",
        symbol=symbol,
        direction=direction,
        metadata={"reason": reason, "scope": "ACCOUNT"},
    )
    queued = enqueue(command)
    if trade is not None:
        trade.execution_command_id = queued.command_id
        emit_trade_event("CLOSE_ALL_SUBMITTED", trade, reason)
    return queued


def submit_stop_loss(trade, stop_loss, reason):
    if not trade.entry_locked:
        return None
    command = new_command(
        trade_id=trade.trade_id,
        action=ExecutionAction.MODIFY_STOP_LOSS,
        idempotency_key=f"trade:{trade.trade_id}:sl:{stop_loss}",
        symbol=trade.symbol,
        stop_loss=stop_loss,
        position_ticket=trade.position_ticket,
        metadata={"reason": reason},
    )
    queued = enqueue(command)
    emit_trade_event("STOP_LOSS_SUBMITTED", trade, reason)
    return queued


def submit_take_profit(trade, take_profit, reason):
    targets = list(trade.take_profit_values)
    if not trade.entry_locked or (take_profit is None and not targets):
        return None
    target_key = ",".join(targets) if targets else str(take_profit)
    command = new_command(
        trade_id=trade.trade_id,
        action=ExecutionAction.MODIFY_TAKE_PROFIT,
        idempotency_key=f"trade:{trade.trade_id}:tp:{target_key}:sl:{trade.stop_loss}",
        symbol=trade.symbol,
        direction=trade.direction,
        stop_loss=trade.stop_loss,
        take_profit=take_profit,
        position_ticket=trade.position_ticket,
        metadata={"reason": reason, "provider_take_profit_targets": targets, "default_take_profit_rr": get_default_rr()},
    )
    queued = enqueue(command)
    emit_trade_event("TAKE_PROFIT_SUBMITTED", trade, reason)
    return queued


def submit_breakeven(trade, reason):
    if not trade.entry_locked or trade.actual_entry_price is None:
        return None
    command = new_command(
        trade_id=trade.trade_id,
        action=ExecutionAction.MOVE_TO_BREAKEVEN,
        idempotency_key=f"trade:{trade.trade_id}:breakeven",
        symbol=trade.symbol,
        stop_loss=trade.actual_entry_price,
        position_ticket=trade.position_ticket,
        metadata={"reason": reason},
    )
    queued = enqueue(command)
    emit_trade_event("BREAKEVEN_SUBMITTED", trade, reason)
    return queued


async def approval_watch(trade_id):
    global entry_timer_task

    while state.has_active_trade():
        trade = state.get_trade()
        if trade.trade_id != trade_id:
            return

        request = get_approval_request(trade_id)
        if not request:
            await asyncio.sleep(0.5)
            continue

        decision = request.get("decision")

        if decision == "IGNORE":
            state.cancel()
            emit_trade_event("TRADE_IGNORED", trade, "User selected IGNORE TRADE.")
            print("\n🔴 TRADE IGNORED BY USER")
            state.display()
            return

        if decision == "EXECUTE":
            trade.status = "WAITING_FOR_ENTRY"
            trade.last_event = "TRADE_APPROVED"
            emit_trade_event("TRADE_APPROVED", trade, "User approved the trade.")
            print("\n🟢 TRADE APPROVED BY USER")

            settings = get_settings()
            if not settings["trading_enabled"] or settings["emergency_stop"]:
                state.cancel()
                emit_trade_event(
                    "TRADE_BLOCKED",
                    trade,
                    "Trading is disabled or Emergency Stop is active.",
                )
                print("🛑 TRADE BLOCKED — trading disabled or Emergency Stop active.")
                state.display()
                return

            if entry_timer_task and not entry_timer_task.done():
                entry_timer_task.cancel()

            start_automatic_entry(trade)

            state.display()
            return

        await asyncio.sleep(0.5)


def refresh_approval_details():
    if not state.has_active_trade():
        return
    trade = state.get_trade()
    if trade.status != "AWAITING_APPROVAL":
        return
    update_approval_request(trade.trade_id, **trade_details_dict(trade))


# ============================================================
# ENTRY WINDOW
# ============================================================

async def entry_window():

    settings = get_settings()
    if not settings["wait_for_entry"]:
        return

    await asyncio.sleep(settings["entry_wait_seconds"])

    settings = get_settings()
    if settings["emergency_stop"] or not settings["trading_enabled"]:
        print("🛑 Entry window ended while trading was blocked.")
        return

    if not state.has_active_trade():
        return

    trade = state.get_trade()

    if trade.entry_locked:
        return

    # WAIT FOR ENTRY ON: no provider entry by timeout means NO TRADE.
    # Never fall back to market execution in this mode.
    if trade.entry_low is None:
        state.cancel()
        trade = state.get_trade()
        emit_trade_event(
            "NO_TRADE",
            trade,
            "Entry window expired without a provider ENTRY or ZONE.",
        )
        print("\n⛔ NO TRADE — entry window expired without provider entry.")
        state.display()
        return

    decision = entry_engine.process_provider_entry(state)

    if not decision:
        return

    submit_entry_decision(decision)

    print(
        "\n" + "=" * 70
    )

    print(
        "⚙️ ENTRY DECISION"
    )

    print(
        "=" * 70
    )

    print(
        f"Action     : {decision.action}"
    )

    print(
        f"Entry Type : {decision.entry_type}"
    )

    print(
        f"Signal Low : {decision.signal_low}"
    )

    print(
        f"Signal High: {decision.signal_high}"
    )

    print(
        f"Exec Price : {decision.execution_price}"
    )

    print(
        f"Reason     : {decision.reason}"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # EXACT PROVIDER ENTRY
    # --------------------------------------------------------

    if decision.action == "EXECUTE_PROVIDER_PRICE":

        print(
            f"🟢 PROVIDER PRICE READY: "
            f"{decision.execution_price}"
        )

        print(
            "⚠️ DEMO MODE — MT5 execution "
            "not connected."
        )

    # --------------------------------------------------------
    # PROVIDER ZONE
    # --------------------------------------------------------

    elif decision.action == "WAIT_FOR_ZONE":

        print(
            f"🟡 WAITING FOR PRICE TO ENTER ZONE "
            f"{decision.signal_low} - "
            f"{decision.signal_high}"
        )

        print(
            "⚠️ DEMO MODE — MT5 price monitoring "
            "not connected yet."
        )

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    elif decision.action == "EXECUTE_MARKET":

        print(
            "🟢 MARKET ENTRY READY"
        )

        print(
            "⚠️ DEMO MODE — actual market price "
            "will come from MT5 later."
        )

    state.display()


# ============================================================
# TELEGRAM MESSAGE HANDLER
# ============================================================

@client.on(events.NewMessage)
async def message_handler(event):

    global entry_timer_task, approval_watch_task

    text = event.message.text or ""

    if not text.strip():
        return

    settings = get_settings()
    if settings["emergency_stop"]:
        print("🛑 EMERGENCY STOP ACTIVE — message ignored for trading.")
        return

    record = build_message_record(
        event
    )

    # --------------------------------------------------------
    # ROLLING 3-MESSAGE CONTEXT
    # --------------------------------------------------------

    recent_messages.append(
        record
    )

    print(
        "\n" + "=" * 70
    )

    print(
        "📨 NEW TELEGRAM MESSAGE"
    )

    print(
        "=" * 70
    )

    print(
        f"Source      : "
        f"{record['chat_name']}"
    )

    print(
        f"Chat ID     : "
        f"{record['chat_id']}"
    )

    print(
        f"Message ID  : "
        f"{record['message_id']}"
    )

    print(
        f"Text        : "
        f"{text}"
    )

    print(
        "=" * 70
    )

    # Account-wide exit keywords must work even when Signal Sniper has no
    # active in-memory trade (for example after a restart).
    if is_close_all_command(text):
        trade = state.get_trade() if state.has_active_trade() else None
        if trade is not None:
            state.close_all()
        submit_close_all(trade, "Provider requested account close-all keyword.")
        publish_dashboard_state()
        print("🔴 ACCOUNT CLOSE ALL SUBMITTED")
        return

    # ========================================================
    # GOLD ACTIVATION
    # ========================================================

    if is_gold_activation(text):

        direction = extract_direction(text)

        reconcile_active_trade_with_broker()
        if state.has_active_trade():
            existing = state.get_trade()

            if existing.status in ("CLOSED", "CANCELLED", "NO_TRADE", "ENTRY_FAILED"):
                state.clear_trade()
            else:
                print("⚠️ Existing active trade found.")
                print("Ignoring new GOLD activation.")
                return

        state.create_trade(
            direction=direction,
            message_id=record["message_id"],
            chat_name=record["chat_name"],
            chat_id=record["chat_id"],
            symbol=get_execution_settings()["symbol"],
        )

        trade = state.get_trade()
        settings = get_settings()

        # Collect details already present in the GOLD message.
        process_deterministic(text, record["message_id"])
        trade = state.get_trade()
        publish_dashboard_state()

        emit_trade_event("TRADE_DETECTED", trade, "GOLD activation detected.")

        print(f"\n🚨 GOLD {direction} ACTIVATED")
        print(f"Source      : {trade.source_chat_name}")
        print(f"Chat ID     : {trade.source_chat_id}")
        print(f"Message ID  : {trade.activation_message_id}")

        if not settings["trading_enabled"] or settings["emergency_stop"]:
            print("🛑 NEW TRADE BLOCKED — trading is disabled or Emergency Stop is active.")
            state.cancel()
            emit_trade_event(
                "TRADE_BLOCKED",
                trade,
                "Trading is disabled or Emergency Stop is active.",
            )
            state.display()
            return

        if not settings["no_auth_trading"]:
            # NO AUTH OFF = manual approval required.
            trade.status = "AWAITING_APPROVAL"
            trade.last_event = "APPROVAL_REQUESTED"

            create_approval_request(
                trade_id=trade.trade_id,
                **trade_details_dict(trade),
            )

            emit_trade_event(
                "APPROVAL_REQUESTED",
                trade,
                "No Auth Trading is OFF. Waiting for user approval.",
            )

            print("🔐 NO AUTH OFF — waiting for Telegram approval.")
            print("🟢 EXECUTE TRADE / 🔴 IGNORE TRADE")
            state.display()

            if entry_timer_task and not entry_timer_task.done():
                entry_timer_task.cancel()

            if approval_watch_task and not approval_watch_task.done():
                approval_watch_task.cancel()

            approval_watch_task = asyncio.create_task(approval_watch(trade.trade_id))
            return

        # NO AUTH ON = automatically approve.
        trade.status = "WAITING_FOR_ENTRY"
        trade.last_event = "TRADE_AUTO_APPROVED"
        emit_trade_event("TRADE_AUTO_APPROVED", trade, "No Auth Trading is ON.")

        if entry_timer_task and not entry_timer_task.done():
            entry_timer_task.cancel()

        start_automatic_entry(trade)

        state.display()
        return

    # ========================================================
    # ACTIVE TRADE
    # ========================================================

    if state.has_active_trade():

        # ----------------------------------------------------
        # DETERMINISTIC FIRST
        # ----------------------------------------------------

        process_deterministic(
            text,
            record["message_id"]
        )

        # A provider entry can arrive after the activation. Previously this
        # was parsed but left waiting until the timer expired.
        execute_provider_entry_if_ready()

        # ----------------------------------------------------
        # AI ONLY WHEN NEEDED
        # ----------------------------------------------------

        if message_needs_ai(text) and state.get_trade().status != "AWAITING_APPROVAL":

            result = await analyze_context()

            apply_analysis(
                result
            )

        refresh_approval_details()
        publish_dashboard_state()
        state.display()


# ============================================================
# MT5 COMMAND WATCHER
# ============================================================

async def mt5_command_watch():
    global entry_timer_task
    seen = set()
    while True:
        try:
            for command in get_pending_commands():
                command_id = command.get("command_id")
                if not command_id or command_id in seen:
                    continue
                seen.add(command_id)

                if command.get("command") == "EMERGENCY_CLOSE_ALL":
                    if entry_timer_task and not entry_timer_task.done():
                        entry_timer_task.cancel()
                        entry_timer_task = None

                    if state.has_active_trade():
                        trade = state.get_trade()
                        enqueue(new_command(
                            trade_id=trade.trade_id,
                            action=ExecutionAction.CLOSE_ALL,
                            idempotency_key=f"trade:{trade.trade_id}:emergency-close-all",
                            symbol=trade.symbol,
                            direction=trade.direction,
                            metadata={"reason": "Telegram Emergency Stop"},
                        ))
                        state.cancel()
                        create_event(
                            trade_id=trade.trade_id,
                            event_type="EMERGENCY_STOP",
                            symbol=trade.symbol,
                            direction=trade.direction,
                            execution_status=trade.execution_status,
                            status=trade.status,
                            reason="Emergency Stop activated from Telegram.",
                        )
                    print("🛑 EMERGENCY STOP: new AI/trade processing blocked and pending entry cancelled.")
                    mark_listener_ack(command_id)
        except Exception as exc:
            print(f"⚠️ MT5 command watcher error: {exc}")
        await asyncio.sleep(0.5)


async def execution_result_loop():
    """Reflect broker-confirmed executor results in the live signal state."""
    while True:
        try:
            if state.has_active_trade():
                trade = state.get_trade()
                for result in results_for_trade(trade.trade_id):
                    command_id = result.get("command_id")
                    if not command_id or command_id in processed_execution_results:
                        continue
                    # A zone watch stays pending until the executor emits its
                    # eventual entry result; it is not a broker fill itself.
                    if result.get("status") == "PENDING":
                        continue
                    processed_execution_results.add(command_id)
                    # Emergency Stop has already emitted the authoritative
                    # TRADE_BLOCKED event and cancelled the local lifecycle.
                    # Do not overwrite it with a second expected executor
                    # rejection for a command that was already in flight.
                    if (
                        trade.status == "CANCELLED"
                        and result.get("action") in {"OPEN_EXACT", "OPEN_MARKET"}
                        and result.get("status") in {"REJECTED", "CANCELLED"}
                    ):
                        continue
                    state.apply_execution_result(result)
                    action = result.get("action")
                    status = result.get("status")

                    if action in {"OPEN_EXACT", "OPEN_MARKET"}:
                        event_type = "EXECUTION_FILLED" if status == "FILLED" else "EXECUTION_FAILED"
                    elif action == "CLOSE_ALL":
                        event_type = "CLOSE_ALL_FILLED" if status == "FILLED" else "CLOSE_ALL_FAILED"
                    elif action in {"MODIFY_STOP_LOSS", "MODIFY_TAKE_PROFIT", "MOVE_TO_BREAKEVEN"}:
                        # Protection updates are intentionally silent on success to avoid
                        # multiple Telegram messages for one entry. Only surface failures.
                        event_type = None if status == "FILLED" else "PROTECTION_FAILED"
                    else:
                        event_type = None

                    if event_type and state.has_active_trade():
                        emit_trade_event(event_type, state.get_trade(), result.get("message"))
                    # If details arrived while the entry was in flight, apply
                    # the latest per-trade protections after MT5 confirms the
                    # position. Local pip overrides remain authoritative.
                    if (
                        result.get("status") == "FILLED"
                        and result.get("action") in {"OPEN_EXACT", "OPEN_MARKET"}
                    ):
                        opened = state.get_trade()
                        if get_local_stop_loss_pips() is None and opened.stop_loss is not None:
                            submit_stop_loss(opened, opened.stop_loss, "Apply trade stop-loss after entry fill.")
                        if get_local_take_profit_pips() is None and not result.get("final_take_profit"):
                            provider_tp, _ = take_profit_value(opened.tp1)
                            submit_take_profit(opened, provider_tp, "Apply trade take-profit after entry fill.")
                    publish_dashboard_state()
                    if action == "CLOSE_ALL" and status == "FILLED":
                        state.clear_trade()
                        publish_dashboard_state()
        except Exception as exc:
            print(f"⚠️ Execution result watcher error: {type(exc).__name__}")
        await asyncio.sleep(0.5)


# ============================================================
# TELEGRAM CONNECTION LOOP
# ============================================================

_missing_position_checks = {}

def reconcile_active_trade_with_broker():
    """Clear stale in-memory trades after their MT5 position no longer exists."""
    if not state.has_active_trade():
        return
    trade = state.get_trade()
    if not trade.entry_locked or not trade.position_ticket:
        return
    connection = get_connection()
    if connection.get("status") != "CONNECTED":
        return
    tickets = connection.get("position_tickets")
    if tickets is None:
        return
    live_tickets = {int(ticket) for ticket in tickets}
    key = trade.trade_id
    if int(trade.position_ticket) in live_tickets:
        _missing_position_checks.pop(key, None)
        return
    checks = _missing_position_checks.get(key, 0) + 1
    _missing_position_checks[key] = checks
    if checks >= 3:
        print(f"✅ MT5 position {trade.position_ticket} is closed; clearing stale trade state.")
        state.clear_trade()
        _missing_position_checks.pop(key, None)

async def dashboard_state_loop():
    while True:
        try:
            reconcile_active_trade_with_broker()
            write_state(state)
        except Exception as exc:
            print(f"⚠️ Dashboard state error: {exc}")
        await asyncio.sleep(1)


async def run_listener():

    print(
        "\n" + "=" * 70
    )

    print(
        "🎯 SIGNAL SNIPER"
    )

    print(
        "Telegram Signal Recognition Engine"
    )

    print(
        "=" * 70
    )

    asyncio.create_task(dashboard_state_loop())
    asyncio.create_task(mt5_command_watch())
    asyncio.create_task(execution_result_loop())

    while True:

        try:

            print(
                "\n🔌 Connecting to Telegram..."
            )

            await client.connect()

            if not await client.is_user_authorized():

                print(
                    "❌ Telegram session is not authorized."
                )

                print(
                    "Starting Telegram login..."
                )

                await client.start()

            me = await client.get_me()

            print(
                f"\nLogged in as : "
                f"{me.first_name or ''}"
            )

            if me.username:

                print(
                    f"Username     : "
                    f"@{me.username}"
                )

            else:

                print(
                    "Username     : None"
                )

            print(
                "\n🟢 Signal Sniper is listening..."
            )

            print(
                "GOLD = ACTIVATOR"
            )

            print(
                f"Entry window = "
                f"{get_settings()['entry_wait_seconds']} seconds"
            )

            print(
                f"Context window = "
                f"last {CONTEXT_SIZE} messages"
            )

            print(
                "Execution mode = Native executor ("
                + ("ENABLED" if (
                    get_execution_settings()["execution_enabled"]
                    and get_settings()["trading_enabled"]
                    and not get_settings()["emergency_stop"]
                ) else "DISABLED")
                + ")"
            )

            print(
                "\nWaiting for Telegram messages."
            )

            print(
                "Press CTRL+C to stop."
            )

            await client.run_until_disconnected()

        except KeyboardInterrupt:

            print(
                "\n🛑 Signal Sniper stopped."
            )

            break

        except Exception as exc:

            print(
                "\n⚠️ Telegram connection lost:"
            )

            print(
                f"{type(exc).__name__}: {exc}"
            )

            print(
                "🔄 Reconnecting in 5 seconds..."
            )

            try:

                await client.disconnect()

            except Exception:
                pass

            await asyncio.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            run_listener()
        )

    except KeyboardInterrupt:

        print(
            "\n🛑 Signal Sniper stopped."
        )
