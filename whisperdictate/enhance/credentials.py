"""API key storage in Windows Credential Manager.

Not the config file: config.toml is plain text in a roaming profile, and the app
rewrites it on every tray click. Credential Manager encrypts per-user at rest and
keeps the key out of anything that gets shared, synced, or pasted into a bug
report. The ANTHROPIC_API_KEY environment variable still wins if set, so CI and
one-off shells work without touching the store.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

TARGET = "WhisperDictateWin:anthropic-api-key"
_ENV_VAR = "ANTHROPIC_API_KEY"

# pywin32 hands back the blob as raw bytes; Credential Manager convention for
# text blobs is UTF-16-LE, and mismatching this yields mojibake, not an error.
_ENCODING = "utf-16-le"


def get_api_key() -> str | None:
    """The key from the environment, else Credential Manager, else None."""
    env = os.environ.get(_ENV_VAR)
    if env:
        return env.strip() or None
    return _read_credential()


def source() -> str:
    """Where the key would come from — for diagnostics, never the key itself."""
    if os.environ.get(_ENV_VAR):
        return f"zmienna srodowiskowa {_ENV_VAR}"
    if _read_credential():
        return "Menedzer polswiadczen Windows"
    return "brak"


def set_api_key(key: str) -> None:
    """Store the key for the current user. Raises OSError if the store rejects it."""
    key = key.strip()
    if not key:
        raise ValueError("Klucz API jest pusty")

    import win32cred

    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": TARGET,
            "UserName": "anthropic",
            "CredentialBlob": key.encode(_ENCODING),
            "Comment": "WhisperDictate for Windows - klucz API Anthropic",
            # LOCAL_MACHINE, not ENTERPRISE: the key must not roam to other
            # machines with the user's domain profile.
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )
    log.info("Klucz API zapisany w Menedzerze polswiadczen (%s)", TARGET)


def delete_api_key() -> bool:
    """Remove the stored key. Returns False if there was nothing to remove."""
    import win32cred

    try:
        win32cred.CredDelete(TARGET, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error when absent
        log.debug("Nie moge usunac polswiadczenia: %s", exc)
        return False
    log.info("Klucz API usuniety z Menedzera polswiadczen")
    return True


def _read_credential() -> str | None:
    try:
        import win32cred
    except ImportError:  # pragma: no cover - pywin32 missing
        return None

    try:
        entry = win32cred.CredRead(TARGET, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:  # noqa: BLE001 - pywintypes.error when not found
        return None

    blob = entry.get("CredentialBlob")
    if not blob:
        return None
    try:
        return blob.decode(_ENCODING).strip() or None
    except UnicodeDecodeError:
        log.warning("Polswiadczenie %s ma nieoczekiwane kodowanie - ignoruje", TARGET)
        return None
