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

log = logging.getLogger(__name__)

LANGUAGES = ("pl", "en", "auto")
MODES = ("hold", "toggle")
DEVICES = ("auto", "cuda", "cpu")
ENHANCEMENT_PROVIDERS = ("anthropic", "claude_cli")
ENHANCEMENT_PROMPTS = ("default", "chat", "verbatim")

# Offered in the tray. Haiku 4.5 leads because dictation is interactive: it is
# the fastest current model, and cleaning a transcript is not a reasoning task.
ENHANCEMENT_MODELS = (
    "claude-haiku-4-5",
    "claude-sonnet-5",
)

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
        "key": "alt_r",
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
    },
    "ui": {
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

    # -- construction ---------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                with path.open("rb") as fh:
                    data = _deep_merge(data, tomllib.load(fh))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.error("Nie udalo sie wczytac %s (%s) - uzywam domyslnych ustawien", path, exc)
        cfg = cls(path, data)
        cfg._validate()
        if not path.exists():
            cfg.save()
            log.info("Utworzono domyslny config: %s", path)
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
            log.error("Nie udalo sie zapisac configu %s: %s", self.path, exc)

    # -- validation -----------------------------------------------------

    def _validate(self) -> None:
        """Clamp or reset anything that would blow up later, loudly."""
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
            log.warning("[replacements] nie jest tabela - ignoruje")
            self.set("replacements", {}, save=False)

    def _one_of(self, dotted: str, allowed: tuple[str, ...]) -> None:
        value = self.get(dotted)
        if value not in allowed:
            fallback = _default_for(dotted)
            log.warning("%s = %r nie jest jednym z %s - uzywam %r", dotted, value, allowed, fallback)
            self.set(dotted, fallback, save=False)

    def _clamp(self, dotted: str, low: float, high: float) -> None:
        value = self.get(dotted)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            fallback = _default_for(dotted)
            log.warning("%s = %r nie jest liczba - uzywam %r", dotted, value, fallback)
            self.set(dotted, fallback, save=False)
            return
        clamped = max(low, min(high, value))
        if clamped != value:
            log.warning("%s = %r poza zakresem [%s, %s] - przycinam do %r", dotted, value, low, high, clamped)
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
