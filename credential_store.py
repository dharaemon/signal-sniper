"""OS-backed credential storage. Passwords are never written to project files."""
from __future__ import annotations

import platform
import subprocess

SERVICE_NAME = "SignalSniperMT5"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def save(self, account_key: str, password: str) -> None:
        if not password:
            raise CredentialStoreError("Refusing to store an empty password.")
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE_NAME,
                            "-a", account_key, "-w", password], check=True, capture_output=True, text=True)
            return
        if system == "Windows":
            self._save_windows(account_key, password)
            return
        raise CredentialStoreError(f"Unsupported credential-store platform: {system}")

    def load(self, account_key: str) -> str | None:
        system = platform.system()
        if system == "Darwin":
            result = subprocess.run(["security", "find-generic-password", "-s", SERVICE_NAME,
                                     "-a", account_key, "-w"], capture_output=True, text=True)
            return result.stdout.rstrip("\n") if result.returncode == 0 else None
        if system == "Windows":
            return self._load_windows(account_key)
        raise CredentialStoreError(f"Unsupported credential-store platform: {system}")

    def delete(self, account_key: str) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["security", "delete-generic-password", "-s", SERVICE_NAME,
                            "-a", account_key], capture_output=True, text=True)
            return
        if system == "Windows":
            from windows_credential_store import delete_secret
            delete_secret(SERVICE_NAME, account_key)
            return
        raise CredentialStoreError(f"Unsupported credential-store platform: {system}")

    @staticmethod
    def _save_windows(account_key: str, password: str) -> None:
        # Implemented in the Windows deployment module; importing win32 APIs on
        # macOS is deliberately avoided.
        from windows_credential_store import save_secret
        save_secret(SERVICE_NAME, account_key, password)

    @staticmethod
    def _load_windows(account_key: str) -> str | None:
        from windows_credential_store import load_secret
        return load_secret(SERVICE_NAME, account_key)
