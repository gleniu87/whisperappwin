"""Per-provider API key storage in Windows Credential Manager.

Not the config file: config.toml is plain text in a roaming profile, and the app
rewrites it on every tray click. Credential Manager encrypts per-user at rest and
keeps keys out of anything that gets shared, synced, or pasted into a bug report.

Each provider gets its own entry and its own environment variable, so an
Anthropic key and a DeepSeek key can coexist and neither shadows the other.
"""

from __future__ import annotations

import logging
import os

from .registry import spec

log = logging.getLogger(__name__)

_TARGET_PREFIX = "WhisperDictateWin:"

# pywin32 hands back the blob as raw bytes; the Credential Manager convention
# for text blobs is UTF-16-LE, and mismatching this yields mojibake, not an error.
_ENCODING = "utf-16-le"


def target_for(provider: str) -> str:
    return f"{_TARGET_PREFIX}{provider}-api-key"


def get_api_key(provider: str) -> str | None:
    """The provider's key from its environment variable, else Credential Manager."""
    env_var = spec(provider).env_var
    if env_var:
        env = os.environ.get(env_var)
        if env and env.strip():
            return env.strip()
    return _read_credential(target_for(provider))


def source(provider: str) -> str:
    """Where the key would come from — for diagnostics, never the key itself."""
    env_var = spec(provider).env_var
    if env_var and os.environ.get(env_var, "").strip():
        return f"zmienna srodowiskowa {env_var}"
    if _read_credential(target_for(provider)):
        return "Menedzer polswiadczen Windows"
    return "brak"


def set_api_key(provider: str, key: str) -> None:
    """Store a key for the current user. Raises OSError if the store rejects it."""
    key = key.strip()
    if not key:
        raise ValueError("Klucz API jest pusty")

    import win32cred

    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target_for(provider),
            "UserName": provider,
            "CredentialBlob": key.encode(_ENCODING),
            "Comment": f"WhisperDictate for Windows - klucz API ({provider})",
            # LOCAL_MACHINE, not ENTERPRISE: keys must not roam to other machines
            # with a domain profile.
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )
    log.info("Klucz API (%s) zapisany w Menedzerze polswiadczen", provider)


def delete_api_key(provider: str) -> bool:
    """Remove a stored key. Returns False if there was nothing to remove."""
    import win32cred

    try:
        win32cred.CredDelete(target_for(provider), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error when absent
        log.debug("Nie moge usunac polswiadczenia (%s): %s", provider, exc)
        return False
    log.info("Klucz API (%s) usuniety z Menedzera polswiadczen", provider)
    return True


def _read_credential(target: str) -> str | None:
    try:
        import win32cred
    except ImportError:  # pragma: no cover - pywin32 missing
        return None

    try:
        entry = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:  # noqa: BLE001 - pywintypes.error when not found
        return None

    blob = entry.get("CredentialBlob")
    if not blob:
        return None
    try:
        return blob.decode(_ENCODING).strip() or None
    except UnicodeDecodeError:
        log.warning("Polswiadczenie %s ma nieoczekiwane kodowanie - ignoruje", target)
        return None
