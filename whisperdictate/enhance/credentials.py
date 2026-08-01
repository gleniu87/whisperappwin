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

from ..i18n import t
from .registry import spec

log = logging.getLogger(__name__)

_TARGET_PREFIX = "WhisperDictateWin:"

#: Where a key was found. Machine values, because callers branch on them - the
#: dialog offers to delete a stored key only when there is one - and branching on
#: a translated sentence breaks the moment the sentence is translated.
SOURCE_ENV = "env"
SOURCE_STORE = "store"
SOURCE_NONE = "none"

# The blob API is asymmetric, verified against pywin32 rather than assumed:
#   CredWrite wants a str and encodes it as UTF-16-LE itself. Handing it bytes
#     raises TypeError("Objects of type 'bytes' can not be converted to Unicode").
#   CredRead hands back raw bytes, which we decode with the same encoding.
# Getting the read side wrong yields mojibake rather than an error, so the
# encoding is named here once and used only on the way in.
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
    """Which store the key would come from — never the key itself.

    One of SOURCE_ENV / SOURCE_STORE / SOURCE_NONE. Use describe_source() for
    something to show a human.
    """
    env_var = spec(provider).env_var
    if env_var and os.environ.get(env_var, "").strip():
        return SOURCE_ENV
    if _read_credential(target_for(provider)):
        return SOURCE_STORE
    return SOURCE_NONE


def describe_source(provider: str) -> str:
    """The same answer in the interface language, for --check and the key dialog."""
    found = source(provider)
    if found == SOURCE_ENV:
        return t("credentials.env", env_var=spec(provider).env_var)
    if found == SOURCE_STORE:
        return t("credentials.store")
    return t("credentials.none")


def set_api_key(provider: str, key: str) -> None:
    """Store a key for the current user. Raises OSError if the store rejects it."""
    key = key.strip()
    if not key:
        raise ValueError("API key is empty")

    import win32cred

    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target_for(provider),
            "UserName": provider,
            # str, not bytes — see the encoding note at the top of this module.
            "CredentialBlob": key,
            "Comment": f"WhisperDictate for Windows - API key ({provider})",
            # LOCAL_MACHINE, not ENTERPRISE: keys must not roam to other machines
            # with a domain profile.
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )
    log.info("API key (%s) stored in Credential Manager", provider)


def delete_api_key(provider: str) -> bool:
    """Remove a stored key. Returns False if there was nothing to remove."""
    import win32cred

    try:
        win32cred.CredDelete(target_for(provider), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error when absent
        log.debug("Cannot delete credential (%s): %s", provider, exc)
        return False
    log.info("API key (%s) removed from Credential Manager", provider)
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
        log.warning("Credential %s has an unexpected encoding - ignoring it", target)
        return None
