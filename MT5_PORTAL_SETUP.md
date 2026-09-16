# Signal Sniper — MT5 Cloudflare Connection Portal

> The portal is only the Cloudflare-protected credential-provisioning surface.
> It is not in the trade execution path. See `EXECUTION_ARCHITECTURE.md` for
> the local native executor design and platform setup commands.

## 1. Add these variables to `.env`

```env
MT5_PORTAL_URL=https://mt5.example.com
MT5_PORTAL_HOST=127.0.0.1
MT5_PORTAL_PORT=8080
REQUIRE_CF_ACCESS=1
CF_ALLOWED_EMAIL=your-cloudflare-login-email@example.com
MT5_LOGIN_TOKEN_TTL=600
```

Do not put the MT5 account password in `.env`.

## 2. Start the portal

```bash
cd ~/Desktop/signal-sniper
source venv/bin/activate
python mt5_portal.py
```

Local health check:

```bash
curl http://127.0.0.1:8080/health
```

Expected response:

```json
{"ok":true,"service":"signal-sniper-mt5-portal"}
```

## 3. Install Cloudflare Tunnel

Install `cloudflared` for macOS from Cloudflare's official downloads.

Then authenticate:

```bash
cloudflared tunnel login
```

Create the tunnel:

```bash
cloudflared tunnel create signal-sniper-mt5
```

Route your chosen hostname:

```bash
cloudflared tunnel route dns signal-sniper-mt5 mt5.example.com
```

Create `~/.cloudflared/config.yml` using the included example and replace the tunnel UUID and hostname.

Run it:

```bash
cloudflared tunnel run signal-sniper-mt5
```

## 4. Configure Cloudflare Access

In Cloudflare Zero Trust, create a **Self-hosted** application for:

```text
mt5.example.com
```

Create an Allow policy for your identity/email. Keep the application deny-by-default except for your Allow policy.

The portal also checks:

```text
CF-Access-Authenticated-User-Email
```

when `REQUIRE_CF_ACCESS=1`.

## 5. Connect Telegram to the portal

The control bot reads `MT5_PORTAL_URL`. When `/start` is received and MT5 is not connected, it:

1. Deletes the `/start` message.
2. Creates a short-lived, one-time login request.
3. Sends a single **Connect MT5** button.
4. The button opens the Cloudflare-protected portal.
5. The user enters MT5 account number, trading password, and server in the browser.
6. After successful connector validation, the portal writes MT5 connection state.
7. The Telegram bot detects the connection and deletes the connection message.
8. The dashboard is sent automatically.

## 6. Native executor requirement

Run the portal and the native executor on the same host. The portal stores the
password only in OS-protected storage—macOS Keychain or Windows Credential
Manager/DPAPI—and validates the selected local adapter. It does not write the
password to `.env`, JSON state, logs, or source code.

On macOS the adapter runs the official MetaTrader5 Python package in the Wine
environment bundled with MT5.app. On Windows the production adapter imports the
official MetaTrader5 package directly. Existing execution continues locally if
Cloudflare is unavailable after provisioning.
