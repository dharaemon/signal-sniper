import os
import json
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic import BaseModel, Field, field_validator
import requests


load_dotenv()


# ============================================================
# SIGNAL ANALYSIS SCHEMA
# ============================================================

class SignalAnalysis(BaseModel):
    event_type: str
    symbol: Optional[str] = None
    direction: Optional[str] = None
    entry_type: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_values: list[str] = Field(default_factory=list)
    management_action: Optional[str] = None
    trade_status: Optional[str] = None
    confidence: float
    explanation: str

    @field_validator("take_profit_values", mode="before")
    @classmethod
    def normalize_take_profits(cls, value):
        if value is None:
            return []

        if isinstance(value, str):
            return [value]

        return value


# ============================================================
# OPENROUTER CONFIG
# ============================================================

api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    raise RuntimeError(
        "OPENROUTER_API_KEY is missing from .env"
    )


# Gemma is our preferred model.
# OpenRouter can fall back if it is temporarily unavailable.
MODELS = [
    "google/gemma-4-31b-it:free",
    "openrouter/free",
]


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are the signal-analysis engine for an autonomous XAUUSD
trading system called Signal Sniper.

Your job is to interpret Telegram trading messages.

You MUST be conservative.

Never invent prices.
Never invent an entry.
Never invent a stop loss.
Never invent take-profit prices.

Messages may be fragments of ONE trading signal spread across
multiple Telegram messages.

You must understand the relationship between messages.

------------------------------------------------------------
IMPORTANT EVENT TYPES
------------------------------------------------------------

Use one of:

NEW_SIGNAL
ENTRY_UPDATE
SL_UPDATE
TP_UPDATE
HOLD
CLOSE_PARTIAL
CLOSE_ALL
BREAKEVEN
PROFIT_UPDATE
CANCEL
NOISE
UNKNOWN

------------------------------------------------------------
GOLD
------------------------------------------------------------

GOLD means XAUUSD.

------------------------------------------------------------
EXAMPLES
------------------------------------------------------------

"GOLD BUY NOW"

means:

event_type = NEW_SIGNAL
symbol = XAUUSD
direction = BUY

but do NOT invent an entry price.

------------------------------------------------------------

"ZONE: 4391-4388"

means:

entry_low = 4388
entry_high = 4391

------------------------------------------------------------

"4401-4402 ENTRY"

means:

entry_low = 4401
entry_high = 4402

------------------------------------------------------------

"CUTLOSS: 4386"

means:

stop_loss = 4386

------------------------------------------------------------

"CUTLOSS:4428.90(41PIPS)"

means:

stop_loss = 4428.90

Do NOT convert the 41 PIPS into another price.

------------------------------------------------------------

"TP:OPEN/1:1/1:3"

means the take-profit information is:

["OPEN", "1:1", "1:3"]

Do NOT invent numeric TP prices.

------------------------------------------------------------

"TP:100pips,130PIPS,210PIPS"

means:

["100pips", "130PIPS", "210PIPS"]

Preserve the information.

------------------------------------------------------------

"STILL HOLDING"

means:

HOLD

------------------------------------------------------------

"RUNNING 40PIPS"

means:

PROFIT_UPDATE

------------------------------------------------------------

"CLOSE MOST NOW"

means:

CLOSE_PARTIAL

------------------------------------------------------------

"BOOM"

Usually NOISE unless the surrounding context clearly gives
it trading meaning.

------------------------------------------------------------

"READY", "READYYY", "PAM", "hi.."

Usually NOISE.

------------------------------------------------------------

CRITICAL RULE:

A management message must NOT become a new trade.

For example:

GOLD BUY NOW
ZONE: 4391-4388
CUTLOSS: 4386
STILL HOLDING
RUNNING 40PIPS
CLOSE MOST NOW

represents one trade lifecycle.

"STILL HOLDING" is not a new BUY.

"RUNNING 40PIPS" is not a new BUY.

"CLOSE MOST NOW" is not a new SELL.

------------------------------------------------------------

CONFIDENCE

Return confidence between 0 and 1.

Use high confidence only when the meaning is clear.

If the message is ambiguous, use UNKNOWN or NOISE.

------------------------------------------------------------

OUTPUT

Return ONLY valid JSON.

The JSON must contain:

event_type
symbol
direction
entry_type
entry_low
entry_high
stop_loss
take_profit_values
management_action
trade_status
confidence
explanation
"""


# ============================================================
# MESSAGE FORMATTER
# ============================================================

def format_messages(messages: list[dict]) -> str:
    """
    Convert Telegram messages into a compact context block.
    """

    lines = []

    for msg in messages:
        message_id = msg.get("message_id", "?")
        date = msg.get("date", "")
        text = msg.get("text", "")

        lines.append(
            f"[Message ID: {message_id} | {date}]\n{text}"
        )

    return "\n\n".join(lines)


# ============================================================
# AI ANALYZER
# ============================================================

def analyze_messages(messages: list[dict]) -> SignalAnalysis:

    context = format_messages(messages)

    user_prompt = f"""
Analyze these Telegram messages as one piece of trading context.

IMPORTANT:
They may contain multiple fragments belonging to the same signal.

Do not invent missing information.

Return ONLY JSON.

TELEGRAM CONTEXT:

{context}
"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://signal-sniper.local",
        "X-OpenRouter-Title": "Signal Sniper",
    }

    payload = {
        "models": MODELS,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_object"
        },
    }

    response = requests.post(
        OPENROUTER_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    # --------------------------------------------------------
    # HTTP ERROR
    # --------------------------------------------------------

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter API error {response.status_code}:\n"
            f"{response.text}"
        )

    # --------------------------------------------------------
    # PARSE RESPONSE
    # --------------------------------------------------------

    try:
        response_data = response.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OpenRouter returned invalid JSON:\n"
            f"{response.text}"
        ) from e

    if "choices" not in response_data:
        raise RuntimeError(
            "OpenRouter returned an unexpected response:\n"
            f"{json.dumps(response_data, indent=2)}"
        )

    raw = response_data["choices"][0]["message"]["content"]

    if not raw:
        raise RuntimeError(
            "OpenRouter returned an empty response."
        )

    # --------------------------------------------------------
    # PARSE AI JSON
    # --------------------------------------------------------

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Gemma returned invalid JSON:\n{raw}"
        ) from e

    # --------------------------------------------------------
    # VALIDATE WITH PYDANTIC
    # --------------------------------------------------------

    try:
        result = SignalAnalysis.model_validate(data)
    except Exception as e:
        raise RuntimeError(
            "Gemma returned JSON that does not match "
            "SignalAnalysis:\n"
            f"{json.dumps(data, indent=2)}"
        ) from e

    return result