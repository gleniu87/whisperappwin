"""TOML-backed configuration with dotted-path access.

The file is user-owned but app-writable: changing the language from the tray
menu persists immediately. Because we rewrite the file, hand-typed comments in
it are lost - config.example.toml in the repo is the documented reference.
"""

from __future__ import annotations

import copy
import logging
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

# Both import-free by design, so neither can cycle back into config.
from .enhance.registry import PROVIDER_KEYS as ENHANCEMENT_PROVIDERS
from .i18n import UI_LANGUAGES, detect_system_language

log = logging.getLogger(__name__)

#: Dictation languages. Not the same list as i18n.UI_LANGUAGES: "auto" makes
#: sense for speech and not for a menu, and dictating in English through a Polish
#: interface is normal.
LANGUAGES = ("pl", "en", "auto")
MODES = ("hold", "toggle")
DEVICES = ("auto", "cuda", "cpu")
ENHANCEMENT_PROMPTS = ("default", "chat", "verbatim")

# Offered in the tray menu. Anything faster-whisper accepts still works if you
# type it into the config by hand.
MODEL_CHOICES = (
    "large-v3-turbo",
    "large-v3",
    "medium",
    "small",
    "base",
)

DEFAULTS: dict[str, Any] = {
    "hotkey": {
        # Right Ctrl, not right Alt: on a Polish layout right Alt is AltGr, so
        # the dictation key would be pressed on every ą, ę, ó. See hotkey.py.
        "key": "ctrl_r",
        "mode": "hold",
        "hold_threshold_ms": 300,
        "cancel_on_other_key": True,
    },
    "transcription": {
        "model": "large-v3-turbo",
        "language": "pl",
        "device": "auto",
        "compute_type": "auto",
        "beam_size": 5,
        "vad_filter": True,
        "initial_prompt": "",
        # Comma-separated proper nouns. Primes Whisper and is handed to the
        # clean-up model, so a name that still comes back garbled can be
        # repaired. See whisperdictate/vocabulary.py.
        "vocabulary": "",
        # Watch for names the clean-up model repaired on its own and offer to
        # add them here. Only a tray balloon and a menu counter - nothing steals
        # focus, and nothing is added without confirmation.
        "suggest_vocabulary": True,
        # Suggestions dismissed with "never ask again", so they stop coming back.
        "vocabulary_rejected": "",
    },
    "audio": {
        "device": None,
        "min_seconds": 0.4,
        "max_seconds": 300.0,
    },
    "output": {
        "auto_paste": True,
        "restore_clipboard": True,
        "paste_delay_ms": 120,
        # Whether a dictation may be kept in the Windows clipboard history
        # (Win+V) and synced to the cloud clipboard. Off, because restoring the
        # previous clipboard content does not remove the history entry, so every
        # dictation was accumulating there. The clipboard itself is still used -
        # that is how Ctrl+V works.
        "clipboard_history": False,
    },
    "ui": {
        # None means "not chosen yet". _resolve_ui_language() replaces it with the
        # system's language on first run, so what lands in config.toml is always
        # a concrete "pl" or "en" the user can read and edit.
        "language": None,
        "overlay": True,
        "sounds": True,
    },
    "history": {
        "enabled": True,
        "max_entries": 5000,
    },
    "enhancement": {
        # Off by default, matching the macOS original (Helpers.swift defaults
        # enhanceTranscription to false). Cleaning costs seconds and money, and
        # raw output is predictable - opting in should be deliberate.
        "enabled": False,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt": "default",
        # Ceiling, not a wait: the API path answers in ~1 s. Sized for the CLI
        # path, measured at ~23 s with the full system prompt - a 30 s ceiling
        # made it fail intermittently.
        "timeout_seconds": 60.0,
        "cli_path": "",
    },
    "replacements": {},
}

_HEADER = (
    "# WhisperDictate for Windows - live configuration.\n"
    "#\n"
    "# This file is rewritten by the app when you change a setting from the tray\n"
    "# menu, so comments you add here will not survive. See config.example.toml in\n"
    "# the repository for the documented reference.\n"
    "\n"
)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively overlay `override` onto a copy of `base`."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Dict-backed settings with `get("section.key")` access and atomic save."""

    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self._data = data
        #: Set by _resolve_ui_language() when it had to guess, so load() can
        #: write the guess down instead of repeating it on every start.
        self._guessed_ui_language = False

    # -- construction ---------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                with path.open("rb") as fh:
                    data = _deep_merge(data, tomllib.load(fh))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.error("Could not read %s (%s) - falling back to the defaults", path, exc)
        cfg = cls(path, data)
        cfg._validate()
        if not path.exists():
            cfg.save()
            log.info("Created a default config: %s", path)
        elif cfg._guessed_ui_language:
            # A config written before ui.language existed. Write the guess down
            # once, so the file says what the app is doing and the setting is
            # findable by someone reading it - rather than re-guessing at every
            # start and looking, in the file, like it was never set.
            cfg.save()
            log.info("Wrote ui.language = %r into the config", cfg.get("ui.language"))
        return cfg

    # -- access ---------------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, *, save: bool = True) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if save:
            self.save()

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    # -- persistence ----------------------------------------------------

    def save(self) -> None:
        """Write via a temp file + replace so a crash cannot truncate the config."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".toml.tmp")
            payload = {k: v for k, v in self._data.items() if v is not None}
            with tmp.open("wb") as fh:
                fh.write(_HEADER.encode("utf-8"))
                tomli_w.dump(_strip_nones(payload), fh)
            tmp.replace(self.path)
        except OSError as exc:
            log.error("Could not save the config %s: %s", self.path, exc)

    # -- validation -----------------------------------------------------

    def _validate(self) -> None:
        """Clamp or reset anything that would blow up later, loudly."""
        self._resolve_ui_language()
        self._one_of("transcription.language", LANGUAGES)
        self._one_of("hotkey.mode", MODES)
        self._one_of("transcription.device", DEVICES)
        self._one_of("enhancement.provider", ENHANCEMENT_PROVIDERS)
        self._one_of("enhancement.prompt", ENHANCEMENT_PROMPTS)
        self._clamp("enhancement.timeout_seconds", 5.0, 300.0)
        self._clamp("hotkey.hold_threshold_ms", 0, 3000)
        self._clamp("transcription.beam_size", 1, 10)
        self._clamp("audio.min_seconds", 0.0, 10.0)
        self._clamp("audio.max_seconds", 5.0, 3600.0)
        self._clamp("output.paste_delay_ms", 0, 2000)
        self._clamp("history.max_entries", 0, 1_000_000)

        replacements = self.get("replacements")
        if not isinstance(replacements, dict):
            log.warning("[replacements] is not a table - ignoring it")
            self.set("replacements", {}, save=False)

    def _resolve_ui_language(self) -> None:
        """Interface language: whatever the user picked, else Windows' own.

        Not handled by `_one_of`, because there is no fixed default to fall back
        to - the default *is* the system's language, and it has to be resolved
        rather than stored. First run therefore writes a real value into
        config.toml instead of leaving a sentinel the next version has to
        interpret. A garbled value gets the same treatment plus a warning.
        """
        current = self.get("ui.language")
        if current in UI_LANGUAGES:
            return
        self._guessed_ui_language = True
        detected = detect_system_language()
        if current is None:
            log.info("Interface language taken from the system: %s", detected)
        else:
            log.warning(
                "ui.language = %r is not one of %s - using %r",
                current, UI_LANGUAGES, detected,
            )
        self.set("ui.language", detected, save=False)

    def _one_of(self, dotted: str, allowed: tuple[str, ...]) -> None:
        value = self.get(dotted)
        if value not in allowed:
            fallback = _default_for(dotted)
            log.warning("%s = %r is not one of %s - using %r", dotted, value, allowed, fallback)
            self.set(dotted, fallback, save=False)

    def _clamp(self, dotted: str, low: float, high: float) -> None:
        value = self.get(dotted)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            fallback = _default_for(dotted)
            log.warning("%s = %r is not a number - using %r", dotted, value, fallback)
            self.set(dotted, fallback, save=False)
            return
        clamped = max(low, min(high, value))
        if clamped != value:
            log.warning("%s = %r outside [%s, %s] - clamping to %r", dotted, value, low, high, clamped)
            self.set(dotted, clamped, save=False)


def _default_for(dotted: str) -> Any:
    node: Any = DEFAULTS
    for part in dotted.split("."):
        node = node[part]
    return node


def _strip_nones(value: Any) -> Any:
    """tomli_w cannot serialise None; drop those keys (they mean 'use default')."""
    if isinstance(value, dict):
        return {k: _strip_nones(v) for k, v in value.items() if v is not None}
    return value
