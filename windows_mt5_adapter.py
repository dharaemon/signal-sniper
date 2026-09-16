"""Primary production adapter: official MetaTrader5 Python API on Windows."""
from __future__ import annotations

from execution_contract import ExecutionResult
from execution_settings import get_execution_settings
from mt5_adapter_common import MT5AdapterCommon
from credential_store import CredentialStore


class WindowsMT5Adapter(MT5AdapterCommon):
    def __init__(self):
        settings = get_execution_settings()
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError("Official MetaTrader5 package is not installed for Windows.") from exc
        path = settings.get("terminal_path") or None
        login = settings.get("login")
        server = settings.get("server") or None
        password = CredentialStore().load(f"{login}@{server}") if login and server else None
        kwargs = {"login": int(login), "server": server, "password": password} if login and server and password else {}
        if path:
            initialized = mt5.initialize(path=path, **kwargs)
        else:
            initialized = mt5.initialize(**kwargs)
        if not initialized:
            raise RuntimeError("Unable to initialize the configured MT5 terminal.")
        super().__init__(mt5, settings)

    def shutdown(self) -> None:
        self.mt5.shutdown()
