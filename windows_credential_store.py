"""Windows Credential Manager backend (protected by Windows/DPAPI).

This module is imported only on Windows by :mod:`credential_store`.
"""
from __future__ import annotations

import base64


def _target(service: str, account_key: str) -> str:
    return f"{service}:{account_key}"


def save_secret(service: str, account_key: str, secret: str) -> None:
    try:
        import win32cred
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for Windows protected credential storage.") from exc
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": _target(service, account_key),
        "UserName": account_key,
        "CredentialBlob": secret.encode("utf-16-le"),
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
    }, 0)


def load_secret(service: str, account_key: str) -> str | None:
    try:
        import win32cred
        credential = win32cred.CredRead(_target(service, account_key), win32cred.CRED_TYPE_GENERIC)
    except Exception:
        return None
    return credential["CredentialBlob"].decode("utf-16-le")


def delete_secret(service: str, account_key: str) -> None:
    try:
        import win32cred
        win32cred.CredDelete(_target(service, account_key), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass
