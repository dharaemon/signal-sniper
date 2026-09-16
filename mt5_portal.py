import os
import json
import time
from pathlib import Path

from flask import Flask, abort, render_template_string, request
from dotenv import load_dotenv

from mt5_connection import set_connected
from mt5_login import get_login_request, consume_login_request
from credential_store import CredentialStore
from execution_settings import write_execution_settings

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
REQUEST_FILE = BASE_DIR / "mt5_login_requests.json"

PORT = int(os.getenv("MT5_PORTAL_PORT", "8080"))
HOST = os.getenv("MT5_PORTAL_HOST", "127.0.0.1")
REQUIRE_CF_ACCESS = os.getenv("REQUIRE_CF_ACCESS", "0") == "1"
CF_ALLOWED_EMAIL = os.getenv("CF_ALLOWED_EMAIL", "").strip().lower()


def positive_int_env(name: str, default: int) -> int:
    """Keep a malformed optional setting from preventing portal startup."""
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


TOKEN_TTL = positive_int_env("MT5_LOGIN_TOKEN_TTL", 600)

app = Flask(__name__)


@app.after_request
def security_headers(response):
    # Login pages handle credentials. Never allow browser/proxy caching or
    # cross-origin embedding of a one-time login URL.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def cloudflare_identity():
    email = request.headers.get("CF-Access-Authenticated-User-Email", "").strip().lower()
    if REQUIRE_CF_ACCESS and not email:
        abort(403, description="Cloudflare Access authentication required.")
    if CF_ALLOWED_EMAIL and email and email != CF_ALLOWED_EMAIL:
        abort(403, description="This Cloudflare identity is not authorized.")
    return email or None


def loopback_only():
    """Guard the Mac-development login route from all network clients."""
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(404)


def mt5_connect(login, password, server):
    """Provision credentials locally and validate through the selected adapter.

    Cloudflare protects access to this portal but does not participate in trading.
    The password goes straight into OS-protected storage and is never persisted in
    this project or returned in an error message.
    """
    account_key = f"{int(login)}@{server}"
    store = CredentialStore()
    try:
        store.save(account_key, password)
        write_execution_settings({"login": int(login), "server": server})
        from native_mt5_executor import build_adapter
        adapter = build_adapter()
        try:
            account = adapter.health()
        finally:
            adapter.shutdown()
        if account.get("status") != "CONNECTED":
            raise RuntimeError("adapter health check failed")
        return True, "MT5 connected successfully.", account
    except Exception:
        # Do not reveal provider/adapter internals to a browser or log password
        # material. A subsequent valid portal login safely overwrites this key.
        store.delete(account_key)
        return False, "MT5 connection validation failed. Check the terminal and account details.", None


LOGIN_HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal Sniper — Connect MT5</title>
<style>
body{margin:0;background:#070707;color:#f3f3f3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:460px;margin:60px auto;padding:24px}
.card{background:#111;border:1px solid #292929;border-radius:20px;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
h1{font-size:25px;margin:0 0 8px}.muted{color:#999;font-size:14px;line-height:1.5}
label{display:block;margin:20px 0 7px;font-size:13px;color:#bbb}
input{box-sizing:border-box;width:100%;padding:14px;border-radius:11px;border:1px solid #333;background:#090909;color:#fff;font-size:16px;outline:none}
input:focus{border-color:#777}
button{width:100%;margin-top:24px;padding:14px;border:0;border-radius:11px;background:#fff;color:#080808;font-size:16px;font-weight:700;cursor:pointer}
.error{margin-top:18px;padding:12px;border-radius:10px;background:#2a1111;color:#ff9b9b;font-size:14px}
.badge{display:inline-block;margin-bottom:18px;padding:6px 10px;border-radius:99px;background:#172b1c;color:#8be09b;font-size:12px}
</style>
</head>
<body><div class="wrap"><div class="card">
<div class="badge">{{ mode_label }}</div>
<h1>Connect MetaTrader 5</h1>
<p class="muted">Your Telegram account is requesting an MT5 connection. Your MT5 password is sent only to the local MT5 connector and is not written to the Signal Sniper settings file.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post">
<label>MT5 Account Number</label>
<input name="login" inputmode="numeric" type="number" required autocomplete="username">
<label>MT5 Trading Password</label>
<input name="password" type="password" required autocomplete="current-password">
<label>MT5 Server</label>
<input name="server" placeholder="XMGlobal-MT5 ..." required autocomplete="organization">
<button type="submit">Connect MT5</button>
</form>
</div></div></body></html>
"""

SUCCESS_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>MT5 Connected</title>
<style>body{margin:0;background:#070707;color:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.card{max-width:460px;margin:80px auto;padding:30px;background:#111;border:1px solid #292929;border-radius:20px}.ok{font-size:38px}h1{margin:12px 0}.muted{color:#999;line-height:1.5}</style></head>
<body><div class="card"><div class="ok">🟢</div><h1>MT5 Connected</h1><p class="muted">Signal Sniper has received the connection. Return to Telegram — the dashboard will appear automatically.</p></div></body></html>
"""


@app.get("/health")
def health():
    return {"ok": True, "service": "signal-sniper-mt5-portal"}


@app.route("/login/<token>", methods=["GET", "POST"])
def login_page(token):
    cloudflare_identity()
    item = get_login_request(token)
    if not item or item.get("used") or item.get("expires_at", 0) < int(time.time()):
        return render_template_string(LOGIN_HTML, error="This login link is expired. Send /start in Telegram to create a new one.", mode_label="🔐 Cloudflare-protected portal"), 400

    if request.method == "GET":
        return render_template_string(LOGIN_HTML, error=None, mode_label="🔐 Cloudflare-protected portal")

    try:
        login = int(request.form.get("login", ""))
        password = request.form.get("password", "")
        server = request.form.get("server", "").strip()
        if not password or not server:
            raise ValueError
    except (ValueError, TypeError):
        return render_template_string(LOGIN_HTML, error="Enter a valid account number, password, and server.", mode_label="🔐 Cloudflare-protected portal"), 400

    ok, message, account = mt5_connect(login, password, server)
    if not ok:
        return render_template_string(LOGIN_HTML, error=message, mode_label="🔐 Cloudflare-protected portal"), 400

    # Consume the one-time request only after successful MT5 validation.
    consume_login_request(token)

    set_connected(account["login"], account["server"], account)
    return render_template_string(SUCCESS_HTML)


@app.route("/local-login", methods=["GET", "POST"])
def local_login_page():
    """Mac-only credential handoff while the Cloudflare hostname is pending.

    This route binds to loopback and rejects every non-local request. It is a
    development fallback only; the production `/login/<token>` route retains
    its Cloudflare Access and one-time-token flow unchanged.
    """
    loopback_only()
    if request.method == "GET":
        return render_template_string(LOGIN_HTML, error=None, mode_label="🔒 Local Mac-only portal")

    try:
        login = int(request.form.get("login", ""))
        password = request.form.get("password", "")
        server = request.form.get("server", "").strip()
        if not password or not server:
            raise ValueError
    except (ValueError, TypeError):
        return render_template_string(LOGIN_HTML, error="Enter a valid account number, password, and server.", mode_label="🔒 Local Mac-only portal"), 400

    ok, message, account = mt5_connect(login, password, server)
    if not ok:
        return render_template_string(LOGIN_HTML, error=message, mode_label="🔒 Local Mac-only portal"), 400

    set_connected(account["login"], account["server"], account)
    return render_template_string(SUCCESS_HTML)


if __name__ == "__main__":
    print(f"🔐 MT5 Portal listening on http://{HOST}:{PORT}")
    print(f"Cloudflare Access required: {REQUIRE_CF_ACCESS}")
    app.run(host=HOST, port=PORT, debug=False)
