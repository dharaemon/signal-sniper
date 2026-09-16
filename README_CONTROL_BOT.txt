SIGNAL SNIPER — TELEGRAM CONTROL BOT

1) Put this folder's Python files into the Signal Sniper project folder.
2) Keep your existing .env. It must contain:

   TG_BOT_TOKEN=YOUR_EXISTING_BOTFATHER_TOKEN

   Do NOT paste the token into chat.

3) Install the bot library:

   pip install python-telegram-bot[job-queue]

4) Start the control bot in Terminal 1:

   python telegram_control_bot.py

5) Open your BotFather bot in Telegram and tap START.
   The first Telegram chat to start it becomes the control owner and is saved locally in control_owner.json.
   For stronger explicit authorization, set TG_CONTROL_CHAT_ID in .env and restart the control bot.

6) Start Signal Sniper in Terminal 2:

   python telegram_listener.py

BUTTONS
- No Auth Trading: ON/OFF
- Entry Wait: ON/OFF
- Trading: ON/OFF
- Set Wait Time: 5/10/15/20/30 sec
- Emergency Stop: confirmation required
- Open Positions
- Trade Journal
- Refresh

IMPORTANT
- Native MT5 execution runs as a separate local executor and starts disabled.
- The dashboard is driven by local executor health and broker-confirmed results.
- The trade journal formatter is included; full deal/P&L journal reconciliation is pending.
- WAIT FOR ENTRY ON means: wait the configured number of seconds; provider ENTRY executes, provider ZONE waits for price; no provider entry becomes NO TRADE.
- WAIT FOR ENTRY OFF means: GOLD BUY/SELL is immediately eligible for market execution, subject to Trading and Emergency Stop.
