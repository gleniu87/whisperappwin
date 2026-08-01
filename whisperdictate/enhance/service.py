"""Orchestration and output filtering for the enhancement layer."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from . import prompts, providers
from .providers import ProviderError

log = logging.getLogger(__name__)

# Reasoning models occasionally emit these wrappers into the visible response.
# Verbatim port of the original's OutputFilter patterns.
_REASONING_BLOCKS = tuple(
    re.compile(pattern, re.DOTALL | re.IGNORECASE)
    for pattern in (r"<thinking>.*?</thinking>", r"<think>.*?</think>", r"<reasoning>.*?</reasoning>")
)

# The prompt's sentinel for "this was silence or pure filler - paste nothing".
_EMPTY_SENTINEL = "EMPTY"

_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("„", "”"), ("“", "”"))

# Guardrail against a provider that ignores the prompt and answers the
# transcript instead of cleaning it. Cleanup output tracks input length; a reply
# several times longer is a different kind of text.
_RUNAWAY_RATIO = 3.0
_RUNAWAY_FLOOR = 400


@dataclass(frozen=True)
class EnhancementResult:
    text: str
    raw_text: str
    provider: str
    model: str
    elapsed_seconds: float

    @property
    def changed(self) -> bool:
        return self.text != self.raw_text


def output_filter(text: str) -> str:
    """Strip reasoning artefacts, resolve the EMPTY sentinel, unwrap stray quotes."""
    for pattern in _REASONING_BLOCKS:
        text = pattern.sub("", text)
    text = text.strip()

    if text == _EMPTY_SENTINEL:
        return ""
    # A model that adds a trailing period or wraps the sentinel still means EMPTY.
    if text.strip("\"'.").strip() == _EMPTY_SENTINEL:
        return ""

    return _strip_outer_quotes(text)


def _strip_outer_quotes(text: str) -> str:
    """The <TRANSCRIPT> envelope tempts models into echoing a quoted reply."""
    if len(text) < 2:
        return text
    for opening, closing in _QUOTE_PAIRS:
        if text.startswith(opening) and text.endswith(closing):
            inner = text[1:-1]
            # Only unwrap a quote that actually wraps the whole string, not a
            # sentence that happens to open and close with quoted speech.
            if opening not in inner and closing not in inner:
                return inner.strip()
    return text


class EnhancementService:
    """Reads its settings from config on every call, so tray changes apply at once."""

    def __init__(self, config):  # noqa: ANN001 - avoids a circular import
        self.config = config

    # -- state ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enhancement.enabled", False))

    @property
    def provider_name(self) -> str:
        name = self.config.get("enhancement.provider", "anthropic")
        return name if name in providers.PROVIDERS else "anthropic"

    @property
    def model(self) -> str:
        return self.config.get("enhancement.model", "claude-haiku-4-5")

    def describe(self) -> str:
        if not self.enabled:
            return "wylaczone"
        return f"{self.provider_name} / {self.model}"

    def check(self) -> str | None:
        """Why the current provider would fail, or None if it looks usable."""
        return self._provider().check()

    # -- work -----------------------------------------------------------

    def enhance(self, text: str, language: str) -> EnhancementResult | None:
        """Clean `text`, or return None to mean 'paste the raw transcript'.

        Never raises: an enhancement failure must degrade to the raw transcript,
        never to a lost dictation.
        """
        if not self.enabled or not text.strip():
            return None

        provider = self._provider()
        system = prompts.build(
            self.config.get("enhancement.prompt", "default"),
            language,
            self.config.get("transcription.vocabulary", ""),
        )
        user = prompts.wrap_transcript(text)
        timeout = float(self.config.get("enhancement.timeout_seconds", 30))
        started = time.perf_counter()

        try:
            raw_reply = provider.complete(system, user, self.model, timeout)
        except ProviderError as exc:
            log.warning("Czyszczenie tekstu nieudane (%s) - wklejam surowy tekst", exc)
            return None
        except Exception:  # noqa: BLE001 - a provider bug must not eat the dictation
            log.exception("Nieoczekiwany blad czyszczenia tekstu - wklejam surowy tekst")
            return None

        cleaned = output_filter(raw_reply)
        elapsed = time.perf_counter() - started

        if _looks_like_an_answer(cleaned, text):
            log.warning(
                "Odpowiedz LLM (%d znakow) nieproporcjonalna do transkrypcji (%d) "
                "- wyglada na odpowiedz, nie czyszczenie; wklejam surowy tekst",
                len(cleaned), len(text),
            )
            return None

        log.info(
            "Czyszczenie: %d -> %d znakow w %.2f s (%s / %s)",
            len(text), len(cleaned), elapsed, provider.name, self.model,
        )
        return EnhancementResult(
            text=cleaned,
            raw_text=text,
            provider=provider.name,
            model=self.model,
            elapsed_seconds=elapsed,
        )

    def _provider(self) -> providers.Provider:
        return providers.build(
            self.provider_name,
            cli_path=self.config.get("enhancement.cli_path", "") or "",
        )


def _looks_like_an_answer(cleaned: str, original: str) -> bool:
    """True when the reply is too long to be a cleaned version of the input.

    An empty result is legitimate — that is the EMPTY sentinel firing.
    """
    if not cleaned:
        return False
    return len(cleaned) > _RUNAWAY_FLOOR and len(cleaned) > len(original) * _RUNAWAY_RATIO
