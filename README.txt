SIGNAL SNIPER TELEGRAM CONTROL BUILD

Native execution is now structured behind a shared platform adapter contract.
Run `./setup_mac.sh` on macOS or `powershell -ExecutionPolicy Bypass -File
.\setup_windows.ps1` on Windows. Execution begins disabled and requires explicit
adapter preflight; see EXECUTION_ARCHITECTURE.md.

To launch all local services in one terminal after setup:

    ./launch_mac.sh

On Windows:

    powershell -ExecutionPolicy Bypass -File .\launch_windows.ps1

1. Copy these files into your signal-sniper folder, replacing the matching files:
   telegram_listener.py
   entry_engine.py
   signal_state.py

2. Add these new files:
   bot_settings.py
   dashboard_state.py
   telegram_control_bot.py
   trade_journal.py

3. Keep your existing .env. It must contain:
   TG_BOT_TOKEN=your_existing_botfather_token

4. Install the control bot dependency:
   pip install "python-telegram-bot[job-queue]"

5. Terminal 1:
   python telegram_control_bot.py

6. Open your Telegram bot and tap START. The first chat to start it becomes the owner unless TG_CONTROL_CHAT_ID is set in .env.

7. Terminal 2:
   python telegram_listener.py

Current controls:
- No Auth Trading ON/OFF
- Entry Wait ON/OFF
- Wait time 5/10/15/20/30 seconds
- Trading ON/OFF
- Emergency Stop confirmation
- Open Positions
- Trade Journal
- Dashboard refresh every second

WAIT FOR ENTRY ON:
  waits configured seconds; provider ENTRY executes; provider ZONE waits for price; no provider entry = NO TRADE.

WAIT FOR ENTRY OFF:
  GOLD BUY/SELL is immediately eligible for market execution.

MT5 execution is local-only through the native executor. Confirmed fills update
the dashboard/events; trade-journal deal reconciliation remains the next build
milestone.
