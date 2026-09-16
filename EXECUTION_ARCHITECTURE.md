# Signal Sniper execution architecture

Signal Sniper core never imports MetaTrader5. It creates durable execution
commands, while `native_mt5_executor.py` is the sole local process that owns a
platform adapter and MT5 session.

## Adapters

- **WindowsMT5Adapter** is the production adapter. It imports the official
  `MetaTrader5` Python package directly and connects to the Windows terminal.
- **MacMT5Adapter** is a development adapter. It launches the repository-owned
  `mac_mt5_worker.py` in the Wine runtime bundled with `MetaTrader 5.app`. That
  worker imports the official MetaTrader5 package inside Wine and communicates
  locally with the Mac terminal. No EA, HTTP bridge, or third-party trading
  service is used.

Both implement the execution contract in `execution_contract.py`.

## Safety boundaries

- `execution_settings.json` contains no password and starts with execution
  disabled.
- macOS passwords are saved only in Keychain. Windows passwords are saved only
  in Windows Credential Manager/DPAPI-backed protected storage.
- The Cloudflare-protected portal only provisions credentials into that OS
  storage and validates the selected local adapter. Cloudflare is not on the
  runtime order path.
- Every command has an idempotency key and every result is retained in the local
  SQLite command/event store.
- Signal Sniper state changes to `OPEN` only after a normalized `FILLED` result.
- Emergency close scopes to positions opened with Signal Sniper's configured
  MT5 magic number, protecting manual positions by default.

## Running safely

1. Run `./setup_mac.sh` on macOS or `powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1` on Windows.
2. Configure only non-secret terminal/account metadata in `execution_settings.json`.
3. Provision credentials through the Cloudflare-protected portal on the machine
   hosting the executor.
4. Run the adapter preflight. Confirm account, symbol, terminal permissions, and
   a demo environment.
5. Start `native_mt5_executor.py`, then the listener/control bot.
6. Change `execution_enabled` to `true` only after explicit demo validation.

`CLOSE_PARTIAL` remains state-only until a partial-close percentage policy is
configured; “close most” is not safe to invent.
