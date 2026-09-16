import asyncio
import html
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot_settings import *
from dashboard_state import read_state
from trade_settings import (
    get_lot_size, set_lot_size,
    get_local_take_profit_pips, set_local_take_profit_pips,
    get_local_stop_loss_pips, set_local_stop_loss_pips,
    get_default_rr,
)
from mt5_connection import get_connection, set_connected, set_disconnected
from mt5_login import create_login_request
from execution_contract import ExecutionAction
from execution_store import cancel_pending_entries, cancel_zone_watches, enqueue, new_command
from trade_notifications import (
    get_pending_approval_requests,
    set_approval_message,
    set_approval_rendered,
    set_approval_decision,
    get_unnotified_events,
    mark_notified,
)


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("TG_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("TG_BOT_TOKEN is missing from .env")


OWNER_FILE = Path(__file__).with_name("control_owner.json")
DASHBOARD_FILE = Path(__file__).with_name("dashboard_message.json")
MT5_PORTAL_URL = os.getenv("MT5_PORTAL_URL", "").rstrip("/")
PORTAL_RUNTIME_FILE = Path(__file__).with_name("portal_runtime.json")




# ============================================================
# UI STATE
# ============================================================

# Possible values:
#
# dashboard
# wait_time
# emergency
#
# The 1-second refresh only runs while this is "dashboard".
ui_state = "dashboard"
# Telegram rejects or rate-limits repeated edits of identical messages. Keep a
# local render fingerprint and only edit when state actually changed.
last_dashboard_fingerprint = None
dashboard_dirty = True
dashboard_retry_after = 0.0


# ============================================================
# OWNER / AUTHORIZATION
# ============================================================

def get_owner_chat_id():
    env_owner = os.getenv("TG_CONTROL_CHAT_ID")

    if env_owner:
        return int(env_owner)

    if OWNER_FILE.exists():
        try:
            data = json.loads(
                OWNER_FILE.read_text()
            )
            return int(data["chat_id"])
        except Exception:
            return None

    return None


def authorized(update: Update) -> bool:
    chat = update.effective_chat

    if not chat:
        return False

    chat_id = chat.id
    owner_id = get_owner_chat_id()

    # First user automatically becomes owner.
    if owner_id is None:
        OWNER_FILE.write_text(
            json.dumps(
                {"chat_id": chat_id},
                indent=2,
            )
        )
        return True

    return chat_id == owner_id




# ============================================================
# TRADE APPROVAL / NOTIFICATIONS
# ============================================================


def _fmt_price(value):
    if value is None:
        return "Waiting"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def approval_text(request):
    entry_type = request.get("entry_type")
    low = request.get("entry_low")
    high = request.get("entry_high")

    if entry_type == "PRICE" and low is not None:
        entry = _fmt_price(low)
    elif low is not None and high is not None:
        entry = f"{_fmt_price(low)} - {_fmt_price(high)}"
    else:
        entry = "Waiting for provider entry"

    tp1 = request.get("tp1") or "—"
    source = request.get("source_chat_name") or "Unknown"

    return (
        "🚨 NEW TRADE DETECTED\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Symbol      : {request.get('symbol') or 'XAUUSD'}\n"
        f"Direction   : {request.get('direction') or '—'}\n"
        f"Entry       : {entry}\n"
        f"Stop Loss   : {_fmt_price(request.get('stop_loss'))}\n"
        f"TP1         : {tp1}\n"
        f"Lot Size    : {request.get('lot_size') or get_lot_size():g}\n\n"
        f"Source      : {source}\n"
        f"Message ID  : {request.get('activation_message_id') or '—'}\n\n"
        "Do you want Signal Sniper to execute this trade?"
    )


def approval_keyboard(trade_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🟢 EXECUTE TRADE",
                callback_data=f"approve_trade:{trade_id}",
            ),
            InlineKeyboardButton(
                "🔴 IGNORE TRADE",
                callback_data=f"ignore_trade:{trade_id}",
            ),
        ]
    ])


def notification_text(event):
    event_type = event.get("event_type") or "TRADE_UPDATE"
    symbol = event.get("symbol") or "XAUUSD"
    direction = event.get("direction") or "—"
    entry = event.get("actual_entry_price")
    if entry is None:
        entry = event.get("entry_low")

    titles = {
        "AUTO_EXECUTION_SUBMITTED": "⏳ <b>Auto execution submitted</b>",
        "EXECUTION_SUBMITTED": "⏳ <b>Execution submitted</b>",
        "EXECUTION_FILLED": "🟢 <b>Trade executed</b>",
        "EXECUTION_FAILED": "🔴 <b>Execution failed</b>",
        "TRADE_BLOCKED": "🔴 <b>Trade blocked</b>",
        "TRADE_IGNORED": "🔴 <b>Trade ignored</b>",
        "NO_TRADE": "🔴 <b>No trade</b>",
        "CLOSE_ALL_SUBMITTED": "⏳ <b>Closing trade</b>",
        "CLOSE_ALL_FILLED": "✅ <b>All positions closed</b>",
        "CLOSE_ALL_FAILED": "🔴 <b>Close all failed</b>",
        "PROTECTION_FAILED": "⚠️ <b>Protection update failed</b>",
        "EMERGENCY_STOP": "🔴 <b>Emergency stop</b>",
    }
    title = titles.get(event_type, f"📣 <b>{html.escape(event_type.replace('_', ' ').title())}</b>")

    direction_text = html.escape(str(direction).title())
    if str(direction).upper() == "BUY":
        direction_text = f"🟢 {direction_text}"
    elif str(direction).upper() == "SELL":
        direction_text = f"🔴 {direction_text}"

    def value(item):
        return html.escape(str(item if item is not None else "—"))

    lines = [
        title,
        "",
        f"<b>Symbol</b> · {value(symbol)}",
        f"<b>Direction</b> · {direction_text}",
        f"<b>Entry</b> · {value(_fmt_price(entry))}",
        f"🔴 <b>Stop loss</b> · {value(_fmt_price(event.get('stop_loss')))}",
        f"🟢 <b>Take profit</b> · {value(event.get('tp1'))}",
    ]

    if event.get("tp2") is not None:
        lines.append(f"🟢 <b>TP2</b> · {value(event.get('tp2'))}")
    if event.get("tp3") is not None:
        lines.append(f"🟢 <b>TP3</b> · {value(event.get('tp3'))}")
    if event.get("lot_size") is not None:
        lines.append(f"<b>Lot size</b> · {value(event.get('lot_size'))}")

    if event.get("volume") is not None:
        lines.append(f"<b>Volume</b> · {value(event.get('volume'))}")
    if event.get("ticket") is not None:
        lines.append(f"<b>Ticket</b> · {value(event.get('ticket'))}")
    if event.get("profit") is not None:
        try:
            marker = "🟢" if float(event.get("profit")) >= 0 else "🔴"
        except (TypeError, ValueError):
            marker = "💰"
        lines.append(f"{marker} <b>Profit / loss</b> · {value(event.get('profit'))}")
    if event.get("reason"):
        lines.extend(["", f"<b>Reason</b>\n{value(event.get('reason'))}"])

    return "\n".join(lines)


async def approval_notification_loop(context: ContextTypes.DEFAULT_TYPE):
    owner_id = get_owner_chat_id()
    if owner_id is None:
        return

    # Send/update the one approval message for each pending trade.
    for request in get_pending_approval_requests():
        trade_id = request.get("trade_id")
        if trade_id is None:
            continue

        text = approval_text(request)
        previous = request.get("last_rendered")

        if not request.get("sent"):
            try:
                message = await context.bot.send_message(
                    chat_id=owner_id,
                    text=text,
                    reply_markup=approval_keyboard(trade_id),
                )
                set_approval_message(trade_id, owner_id, message.message_id)
                set_approval_rendered(trade_id, text)
                print(f"📨 Approval request sent for trade {trade_id}.")
            except Exception as exc:
                print(f"⚠️ Approval message error: {exc}")
            continue

        if previous == text:
            continue

        message_id = request.get("message_id")
        chat_id = request.get("chat_id") or owner_id
        if not message_id:
            continue

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=approval_keyboard(trade_id),
            )
            set_approval_rendered(trade_id, text)
        except Exception:
            pass


async def notification_loop(context: ContextTypes.DEFAULT_TYPE):
    owner_id = get_owner_chat_id()
    if owner_id is None:
        return

    # These events are represented by the approval UI and should not
    # create a second DM for the same detection.
    skip = {
        "TRADE_DETECTED",
        "APPROVAL_REQUESTED",
        "TRADE_APPROVED",
        "TRADE_AUTO_APPROVED",
        "EXECUTION_SUBMITTED",
        "AUTO_EXECUTION_SUBMITTED",
        "STOP_LOSS_SUBMITTED",
        "TAKE_PROFIT_SUBMITTED",
        "BREAKEVEN_SUBMITTED",
        "CLOSE_ALL_SUBMITTED",
    }

    for event in get_unnotified_events():
        event_id = event.get("event_id")
        if not event_id:
            continue

        try:
            if event.get("event_type") not in skip:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=notification_text(event),
                    parse_mode=ParseMode.HTML,
                )
            mark_notified(event_id)
        except Exception as exc:
            print(f"⚠️ Notification error: {exc}")


# ============================================================
# MT5 CONNECTION PORTAL
# ============================================================

def mt5_is_connected():
    return get_connection().get("status") == "CONNECTED"


def mt5_portal_url():
    """Prefer a current locally-published development tunnel URL.

    Production uses the stable URL configured in .env. The local runtime file
    is written only by the Mac development portal launcher and is ignored by
    Git; it contains no credential material.
    """
    try:
        value = json.loads(PORTAL_RUNTIME_FILE.read_text()).get("url", "")
        if isinstance(value, str) and value.startswith("https://"):
            return value.rstrip("/")
    except Exception:
        pass
    # A trycloudflare hostname is intentionally ephemeral. Never keep serving
    # a stale one from .env after its process has ended.
    if "trycloudflare.com" in MT5_PORTAL_URL.lower():
        return ""
    return MT5_PORTAL_URL


def mt5_connection_message():
    if not mt5_portal_url():
        return (
            "⚠️ MT5 CONNECTION REQUIRED\n\n"
            "The MT5 login portal is not available yet.\n"
            "Start the local portal launcher or configure the production Cloudflare tunnel."
        )
    return (
        "🔐 MT5 CONNECTION REQUIRED\n\n"
        "Signal Sniper is not connected to an MT5 trading account.\n\n"
        "Tap the button below to open the secure Cloudflare-protected MT5 portal.\n\n"
        "Enter your MT5 account number, trading password, and server there.\n"
        "Never send your MT5 password in Telegram."
    )


def mt5_login_keyboard(chat_id):
    portal_url = mt5_portal_url()
    if not portal_url:
        return None
    token = create_login_request(chat_id)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🔗 Connect MT5",
            url=f"{portal_url}/login/{token}",
        )
    ]])


# ============================================================
# DASHBOARD TEXT
# ============================================================

def dashboard_text():
    settings = get_settings()
    dashboard = read_state()
    mt5 = get_connection()

    trade = dashboard.get("trade")

    trading_icon = (
        "🟢"
        if settings["trading_enabled"]
        else "🔴"
    )

    no_auth_icon = (
        "🟢"
        if settings["no_auth_trading"]
        else "🔴"
    )

    wait_icon = (
        "🟢"
        if settings["wait_for_entry"]
        else "🔴"
    )

    emergency_icon = (
        "🔴"
        if settings["emergency_stop"]
        else "🟢"
    )

    trading = (
        "ON"
        if settings["trading_enabled"]
        else "OFF"
    )

    no_auth = (
        "ON"
        if settings["no_auth_trading"]
        else "OFF"
    )

    wait_entry = (
        "ON"
        if settings["wait_for_entry"]
        else "OFF"
    )

    emergency = (
        "ACTIVE"
        if settings["emergency_stop"]
        else "OFF"
    )

    lines = [
        "🎯 SIGNAL SNIPER",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🟢 System     {trading_icon} Trading",
        f"🔐 No Auth    {no_auth_icon} {no_auth}",
        f"⏳ Entry Wait {wait_icon} {wait_entry}",
        f"⏱ Wait Time   {settings['entry_wait_seconds']}s",
        f"📦 Lot Size    {get_lot_size():g}",
        f"🎯 Local TP    {_pips_display(get_local_take_profit_pips())}",
        f"⚖️ Default RR  {get_default_rr()}",
        f"🛡️ Local SL    {_pips_display(get_local_stop_loss_pips())}",
        f"🛑 Emergency   {emergency_icon} {emergency}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💰 MT5 ACCOUNT",
        "",
        f"Status        {"🟢 Connected" if mt5.get("status") == "CONNECTED" else "🔴 Not connected"}",
        f"Account       {mt5.get("login") or "—"}",
        f"Server        {mt5.get("server") or "—"}",
        f"Balance       {mt5.get("balance") if mt5.get("balance") is not None else "—"}",
        f"Equity        {mt5.get("equity") if mt5.get("equity") is not None else "—"}",
        f"Floating P/L  {mt5.get("profit") if mt5.get("profit") is not None else "—"}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "📈 OPEN POSITION",
        "",
    ]

    if trade:

        symbol = (
            trade.get("symbol")
            or "XAUUSD"
        )

        direction = (
            trade.get("direction")
            or "—"
        )

        if direction == "BUY":
            direction_display = "🟢 BUY"
        elif direction == "SELL":
            direction_display = "🔴 SELL"
        else:
            direction_display = direction

        actual_entry = trade.get(
            "actual_entry_price"
        )

        stop_loss = trade.get(
            "stop_loss"
        )

        calculated_levels = trade.get("calculated_take_profit_levels") or []
        tp1 = trade.get("final_take_profit")
        if tp1 is None and calculated_levels:
            tp1 = calculated_levels[0]
        if tp1 is None:
            tp1 = trade.get("tp1")

        status = (
            trade.get("status")
            or "—"
        )

        entry_display = (
            str(actual_entry)
            if actual_entry is not None
            else "Pending MT5"
        )

        sl_display = (
            str(stop_loss)
            if stop_loss is not None
            else "—"
        )

        tp1_display = (
            str(tp1)
            if tp1 is not None
            else "—"
        )

        lines.extend(
            [
                f"{symbol}  {direction_display}",
                "",
                f"Entry       {entry_display}",
                f"Stop Loss   {sl_display}",
                f"TP1         {tp1_display}",
                f"Status      {status}",
            ]
        )

    else:
        lines.append(
            "No active trade."
        )

    return "\n".join(lines)


# ============================================================
# DASHBOARD BUTTONS
# ============================================================

def dashboard_keyboard():

    settings = get_settings()

    no_auth = (
        "🔐 No Auth: ON"
        if settings["no_auth_trading"]
        else "🔐 No Auth: OFF"
    )

    wait = (
        "⏳ Wait: ON"
        if settings["wait_for_entry"]
        else "⏳ Wait: OFF"
    )

    wait_time = (
        f"⏱ Wait: "
        f"{settings['entry_wait_seconds']}s"
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    no_auth,
                    callback_data="na",
                ),
                InlineKeyboardButton(
                    wait,
                    callback_data="wait",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚙️ TRADE CONFIG",
                    callback_data="trade_config",
                ),
                InlineKeyboardButton(
                    wait_time,
                    callback_data="time",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔌 TEST MT5",
                    callback_data="mt5_test",
                ),
                InlineKeyboardButton(
                    "▶️ TURN ON TRADING" if settings["emergency_stop"] else "🛑 EMERGENCY STOP",
                    callback_data="resume" if settings["emergency_stop"] else "emergency",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📖 Trade Journal",
                    callback_data="journal",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="refresh",
                ),
            ],
        ]
    )


# ============================================================
# WAIT TIME MENU
# ============================================================

def wait_time_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "5 seconds",
                    callback_data="t5",
                ),
                InlineKeyboardButton(
                    "10 seconds",
                    callback_data="t10",
                ),
            ],
            [
                InlineKeyboardButton(
                    "15 seconds",
                    callback_data="t15",
                ),
                InlineKeyboardButton(
                    "20 seconds",
                    callback_data="t20",
                ),
            ],
            [
                InlineKeyboardButton(
                    "30 seconds",
                    callback_data="t30",
                ),
            ],
            [
                InlineKeyboardButton(
                    "◀️ Back",
                    callback_data="refresh",
                ),
            ],
        ]
    )


# ============================================================
# LOT SIZE MENU
# ============================================================

def lot_keyboard():
    current = get_lot_size()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("0.01", callback_data="lot_0.01"), InlineKeyboardButton("0.02", callback_data="lot_0.02"), InlineKeyboardButton("0.05", callback_data="lot_0.05")],
        [InlineKeyboardButton("0.10", callback_data="lot_0.10"), InlineKeyboardButton("0.20", callback_data="lot_0.20"), InlineKeyboardButton("0.50", callback_data="lot_0.50")],
        [InlineKeyboardButton("1.00", callback_data="lot_1.00"), InlineKeyboardButton("✏️ Custom", callback_data="lot_custom")],
        [InlineKeyboardButton("◀️ Back", callback_data="refresh")],
    ])


def _pips_display(value):
    return f"{value:g} pips" if value is not None else "Channel"


def trade_config_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📦 SET LOT ({get_lot_size():g})", callback_data="lot")],
        [InlineKeyboardButton(f"🎯 SET TP ({_pips_display(get_local_take_profit_pips())})", callback_data="set_tp")],
        [InlineKeyboardButton(f"🛡️ SET SL ({_pips_display(get_local_stop_loss_pips())})", callback_data="set_sl")],
        [InlineKeyboardButton("◀️ Back", callback_data="refresh")],
    ])


# ============================================================
# EMERGENCY STOP
# ============================================================


# ============================================================
# DASHBOARD EDIT
# ============================================================

async def edit_dashboard(
    context: ContextTypes.DEFAULT_TYPE
):

    global ui_state, last_dashboard_fingerprint, dashboard_dirty, dashboard_retry_after

    # IMPORTANT:
    #
    # Do NOT overwrite menus while the user
    # is making a selection.
    if ui_state != "dashboard":
        dashboard_dirty = True
        return

    if not DASHBOARD_FILE.exists():
        return
    if time.monotonic() < dashboard_retry_after:
        return

    try:

        data = json.loads(
            DASHBOARD_FILE.read_text()
        )
        if data.get("screen", "dashboard") != "dashboard":
            return

        text = dashboard_text()
        keyboard = dashboard_keyboard()
        fingerprint = json.dumps(
            {
                "chat_id": data["chat_id"],
                "message_id": data["message_id"],
                "text": text,
                "keyboard": keyboard.to_dict(),
            },
            sort_keys=True,
        )
        if not dashboard_dirty and fingerprint == last_dashboard_fingerprint:
            return

        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=text,
            reply_markup=keyboard,
        )
        last_dashboard_fingerprint = fingerprint
        dashboard_dirty = False
        dashboard_retry_after = 0.0

    except BadRequest as exc:
        # Telegram returns this when our first post-restart edit is identical;
        # that is already the desired dashboard state, not a real failure.
        if "message is not modified" in str(exc).lower():
            last_dashboard_fingerprint = fingerprint
            dashboard_dirty = False
            dashboard_retry_after = 0.0
            return
        dashboard_retry_after = time.monotonic() + 5
        print(f"⚠️ Dashboard update error: {type(exc).__name__}")
    except RetryAfter as exc:
        # Respect Telegram's own flood-control window instead of attempting an
        # edit every second and extending the cooldown.
        dashboard_retry_after = time.monotonic() + float(exc.retry_after) + 1
        print("⚠️ Dashboard update paused until Telegram's retry window ends.")
    except Exception as exc:
        # Leave the render dirty so a transient Telegram API failure retries
        # on the next state change without flooding the API every second.
        dashboard_retry_after = time.monotonic() + 5
        print(f"⚠️ Dashboard update error: {type(exc).__name__}")


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global ui_state

    if not authorized(update):
        return

    ui_state = "dashboard"
    chat = update.effective_chat

    # Keep the Telegram chat clean. The /start command is only a trigger.
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass

    if not mt5_is_connected():
        try:
            old_dashboard = json.loads(DASHBOARD_FILE.read_text()) if DASHBOARD_FILE.exists() else None
            if old_dashboard and old_dashboard.get("chat_id") == chat.id:
                try:
                    await context.bot.delete_message(
                        chat_id=chat.id,
                        message_id=old_dashboard["message_id"],
                    )
                except Exception:
                    pass
        except Exception:
            pass

        login_message = await chat.send_message(
            mt5_connection_message(),
            reply_markup=mt5_login_keyboard(chat.id),
        )
        DASHBOARD_FILE.write_text(
            json.dumps({
                "chat_id": chat.id,
                "message_id": login_message.message_id,
                "screen": "mt5_login",
            }, indent=2)
        )
        return

    message = await chat.send_message(
        dashboard_text(),
        reply_markup=dashboard_keyboard(),
    )

    DASHBOARD_FILE.write_text(
        json.dumps(
            {
                "chat_id": chat.id,
                "message_id": message.message_id,
                "screen": "dashboard",
            },
            indent=2,
        )
    )


# ============================================================
# MT5 LOGIN / ACCOUNT SWITCH
# ============================================================

async def login_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Create a fresh one-time MT5 login link without clearing the current session."""

    if not authorized(update):
        return

    chat = update.effective_chat
    if chat is None:
        return

    # Remove the /login command itself to keep the control chat clean.
    try:
        if update.message:
            await update.message.delete()
    except Exception:
        pass

    portal_url = mt5_portal_url()

    if not portal_url:
        await chat.send_message(
            "⚠️ MT5 LOGIN PORTAL OFFLINE\n\n"
            "The MT5 login portal is currently unavailable."
        )
        return

    mt5 = get_connection()
    connected = mt5.get("status") == "CONNECTED"

    if connected:
        login = str(mt5.get("login") or "Unknown")
        server = str(mt5.get("server") or "Unknown")

        # Mask most of the account number in Telegram.
        masked_login = (
            ("*" * max(0, len(login) - 4)) + login[-4:]
            if login != "Unknown"
            else login
        )

        message = (
            "🔐 MT5 ACCOUNT\n\n"
            "🟢 Currently connected\n"
            f"Account: {masked_login}\n"
            f"Server: {server}\n\n"
            "Use the button below to login or change the MT5 account.\n\n"
            "Your current session remains active while the new login is being entered."
        )
    else:
        message = (
            "🔐 MT5 ACCOUNT\n\n"
            "🔴 No MT5 account is currently connected.\n\n"
            "Use the button below to connect an MT5 account."
        )

    token = create_login_request(chat.id)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🔄 Login / Change MT5 Account",
            url=f"{portal_url}/login/{token}",
        )
    ]])

    await chat.send_message(
        message,
        reply_markup=keyboard,
    )


# ============================================================
# CUSTOM LOT TEXT INPUT
# ============================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ui_state
    if not authorized(update) or ui_state not in {"lot_input", "tp_input", "sl_input"}:
        return
    text = (update.message.text or "").strip()
    try:
        if ui_state == "lot_input":
            value = float(text)
            if value <= 0 or value > 100:
                raise ValueError
            set_lot_size(value)
            message = f"📦 Lot Size set to {value:g}"
        elif ui_state == "tp_input":
            value = None if text.upper() in {"OFF", "CHANNEL"} else float(text)
            set_local_take_profit_pips(value)
            message = f"🎯 Local TP: {_pips_display(value)}"
        else:
            value = None if text.upper() in {"OFF", "CHANNEL"} else float(text)
            set_local_stop_loss_pips(value)
            message = f"🛡️ Local SL: {_pips_display(value)}"
        await update.message.reply_text(message)
        ui_state = "dashboard"
        await edit_dashboard(context)
    except ValueError:
        await update.message.reply_text("❌ Invalid value. Send a positive number, or OFF to use the channel value.")


def _test_local_mt5():
    """A physical adapter health check; it never submits an order."""
    from native_mt5_executor import build_adapter
    adapter = build_adapter()
    try:
        return adapter.health()
    finally:
        adapter.shutdown()


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global ui_state

    query = update.callback_query

    if not authorized(update):

        await query.answer(
            "Not authorized",
            show_alert=True,
        )

        return

    await query.answer()

    action = query.data
    settings = get_settings()

    confirmation = None

    # ========================================================
    # TRADE APPROVAL
    # ========================================================

    if action.startswith("approve_trade:") or action.startswith("ignore_trade:"):
        try:
            trade_id = int(action.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Invalid trade ID", show_alert=True)
            return

        decision = "EXECUTE" if action.startswith("approve_trade:") else "IGNORE"
        request = set_approval_decision(trade_id, decision)

        if request is None:
            await query.answer("Trade request no longer exists", show_alert=True)
            return

        if decision == "EXECUTE":
            text = (
                "🟢 TRADE APPROVED\n\n"
                "Signal Sniper will continue to the entry/execution stage."
            )
        else:
            text = (
                "🔴 TRADE IGNORED\n\n"
                "This signal will not be executed."
            )

        try:
            await query.message.edit_text(text)
        except Exception:
            pass

        return

    # ========================================================
    # NO AUTH
    # ========================================================

    if action == "na":

        set_no_auth_trading(
            not settings["no_auth_trading"]
        )

        new_state = (
            "ON"
            if not settings["no_auth_trading"]
            else "OFF"
        )

        confirmation = (
            f"🔐 No Auth Trading: {new_state}"
        )

        ui_state = "dashboard"

    # ========================================================
    # ENTRY WAIT
    # ========================================================

    elif action == "wait":

        set_wait_for_entry(
            not settings["wait_for_entry"]
        )

        new_state = (
            "ON"
            if not settings["wait_for_entry"]
            else "OFF"
        )

        confirmation = (
            f"⏳ Entry Wait: {new_state}"
        )

        ui_state = "dashboard"

    # ========================================================
    # TRADE CONFIG
    # ========================================================

    elif action == "trade_config":
        ui_state = "trade_config"
        await query.message.edit_text(
            "⚙️ TRADE CONFIG\n\n"
            "Local TP/SL values are in broker pips and override the Telegram channel for each new trade.\n"
            "Send OFF in either setting to use the channel value again.",
            reply_markup=trade_config_keyboard(),
        )
        return

    # ========================================================
    # WAIT TIME MENU
    # ========================================================

    elif action == "time":

        # PAUSE dashboard refresh.
        ui_state = "wait_time"

        await query.message.edit_text(
            "⏱ ENTRY WAIT TIME\n\n"
            "Choose how long Signal Sniper "
            "should wait for provider entry.",
            reply_markup=wait_time_keyboard(),
        )

        return

    # ========================================================
    # WAIT TIME SELECTION
    # ========================================================

    elif action.startswith("t"):

        seconds = int(action[1:])

        set_entry_wait_seconds(
            seconds
        )

        confirmation = (
            f"⏱ Entry Wait: {seconds}s"
        )

        # Selection completed.
        # Return to dashboard.
        ui_state = "dashboard"

    # ========================================================
    # LOT SIZE MENU
    # ========================================================

    elif action == "lot":
        ui_state = "lot"
        await query.message.edit_text(
            f"📦 LOT SIZE\n\nCurrent lot: {get_lot_size():g}\n\nChoose the fixed lot size Signal Sniper will use for each new MT5 trade.",
            reply_markup=lot_keyboard(),
        )
        return

    elif action == "set_tp":
        ui_state = "tp_input"
        await query.message.edit_text(
            f"🎯 SET LOCAL TAKE PROFIT\n\nCurrent: {_pips_display(get_local_take_profit_pips())}\n\nSend a positive pip value, e.g. 100. Send OFF to use Telegram TP.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="trade_config")]]),
        )
        return

    elif action == "set_sl":
        ui_state = "sl_input"
        await query.message.edit_text(
            f"🛡️ SET LOCAL STOP LOSS\n\nCurrent: {_pips_display(get_local_stop_loss_pips())}\n\nSend a positive pip value, e.g. 50. Send OFF to use Telegram SL.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="trade_config")]]),
        )
        return

    elif action.startswith("lot_") and action != "lot_custom":
        try:
            value = float(action.split("_", 1)[1])
            set_lot_size(value)
            confirmation = f"📦 Lot Size: {value:g}"
        except ValueError:
            confirmation = "❌ Invalid lot size."
        ui_state = "dashboard"

    elif action == "lot_custom":
        ui_state = "lot_input"
        await query.message.edit_text(
            "✏️ CUSTOM LOT SIZE\n\nSend the lot size as a number.\nExample: 0.07",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="refresh")]]),
        )
        return

    # ========================================================
    # ONE-TAP EMERGENCY STOP
    # ========================================================

    elif action == "emergency":
        set_emergency_stop(True)
        cancel_pending_entries()
        current_trade = read_state().get("trade") or {}
        trade_id = int(current_trade.get("trade_id") or 0)
        if trade_id:
            cancel_zone_watches(trade_id)
        enqueue(new_command(
            trade_id=trade_id,
            action=ExecutionAction.CLOSE_ALL,
            idempotency_key=f"emergency-close-all:{int(time.time() * 1000)}",
            symbol=current_trade.get("symbol") or "XAUUSD",
            metadata={"reason": "Telegram Emergency Stop"},
        ))
        confirmation = (
            "🛑 EMERGENCY STOP ACTIVE\n\n"
            "New trading blocked.\n"
            "Close-all Signal Sniper positions command sent to local MT5 execution."
        )
        ui_state = "dashboard"

    # ========================================================
    # RESUME AFTER EMERGENCY STOP
    # ========================================================

    elif action == "resume":
        set_emergency_stop(False)
        confirmation = "▶️ Trading is ON. New trades may proceed."
        ui_state = "dashboard"

    # ========================================================
    # PHYSICAL MT5 CONNECTION TEST (NO ORDER)
    # ========================================================

    elif action == "mt5_test":
        try:
            health = await asyncio.to_thread(_test_local_mt5)
            if health.get("status") == "CONNECTED":
                set_connected(health["login"], health["server"], health)
                confirmation = (
                    "🔌 MT5 CONNECTION TEST PASSED\n\n"
                    f"Terminal connected. External Python trading: {'ON' if health.get('trade_allowed') else 'OFF'}.\n"
                    "No order was sent."
                )
            else:
                set_disconnected("MT5 connection test failed.")
                confirmation = "🔴 MT5 CONNECTION TEST FAILED\n\nCheck the local MT5 terminal and adapter setup."
        except Exception:
            set_disconnected("MT5 connection test failed.")
            confirmation = "🔴 MT5 CONNECTION TEST FAILED\n\nCheck the local MT5 terminal and adapter setup."
        ui_state = "dashboard"

    # ========================================================
    # JOURNAL
    # ========================================================

    elif action == "journal":

        confirmation = (
            "📖 MT5 trade journal will appear "
            "here after confirmed closed trades."
        )

        ui_state = "dashboard"

    # ========================================================
    # REFRESH / BACK
    # ========================================================

    elif action == "refresh":

        # This is also the Back button.
        ui_state = "dashboard"

        await edit_dashboard(
            context
        )

        return

    # ========================================================
    # TEMPORARY CONFIRMATION
    # ========================================================

    if confirmation:

        try:

            confirmation_message = (
                await query.message.chat.send_message(
                    confirmation
                )
            )

            await asyncio.sleep(1.5)

            try:
                await confirmation_message.delete()
            except Exception:
                pass

        except Exception:
            pass

    # ========================================================
    # REDRAW DASHBOARD
    # ========================================================

    await edit_dashboard(
        context
    )


# ============================================================
# MT5 CONNECTION POLLING
# ============================================================

async def mt5_connection_loop(context: ContextTypes.DEFAULT_TYPE):
    global ui_state
    if not mt5_is_connected() or ui_state != "dashboard":
        return
    # If the dashboard file still points to the login screen, replace it.
    if not DASHBOARD_FILE.exists():
        return
    try:
        data = json.loads(DASHBOARD_FILE.read_text())
        if data.get("screen") != "mt5_login":
            return
        chat_id = int(data["chat_id"])
        old_message_id = int(data["message_id"])
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception:
            pass
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=dashboard_text(),
            reply_markup=dashboard_keyboard(),
        )
        DASHBOARD_FILE.write_text(json.dumps({
            "chat_id": chat_id,
            "message_id": message.message_id,
            "screen": "dashboard",
        }, indent=2))
    except Exception:
        pass


# ============================================================
# MAIN
# ============================================================

async def main():

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )
    application.add_handler(
        CommandHandler(
            "login",
            login_command,
        )
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler),
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
        )
    )

    # Refresh dashboard once every second.
    #
    # edit_dashboard() itself checks ui_state,
    # so menus are NEVER overwritten while open.
    application.job_queue.run_repeating(
        edit_dashboard,
        interval=1,
        first=1,
    )

    application.job_queue.run_repeating(
        mt5_connection_loop,
        interval=1,
        first=1,
    )

    # Poll shared files for approval requests and trade notifications.
    application.job_queue.run_repeating(
        approval_notification_loop,
        interval=1,
        first=1,
    )

    application.job_queue.run_repeating(
        notification_loop,
        interval=1,
        first=1,
    )

    print(
        "🎛️ Telegram Control Bot running."
    )

    print(
        "Open your bot and tap START."
    )

    await application.initialize()

    await application.start()

    await application.updater.start_polling()

    try:

        while True:
            await asyncio.sleep(3600)

    finally:

        await application.updater.stop()

        await application.stop()

        await application.shutdown()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
