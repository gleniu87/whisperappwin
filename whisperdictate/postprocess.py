"""Text clean-up applied between transcription and paste.

Deliberately dumber than the macOS original, which sends the transcript to an
LLM (Anthropic/Ollama) for rewriting. That is a separate feature with its own
latency and privacy trade-offs; this build stays fully local and instant.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Whisper emits these when handed silence or noise - it is reproducing subtitle
# credits from its training data. VAD filtering catches most cases; this is the
# backstop for the rest.
#
# The Polish patterns are data, not leftovers: they are the literal strings
# Whisper produces on silence for a Polish-language model. Translating them would
# stop matching what it actually emits.
_HALLUCINATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^napisy (?:stworzone przez|robione przez|created by).{0,40}$",
        r"^(?:subtitles|transcription) by .{0,40}$",
        r"^(?:dziekuje|dziekujemy) za (?:uwage|obejrzenie).{0,20}$",
        r"^thanks? for watching.{0,20}$",
        r"^\.{2,}$",
    )
)

_WHITESPACE = re.compile(r"[ \t]{2,}")


def clean(text: str) -> str:
    """Trim, collapse runs of spaces, and drop known hallucination boilerplate."""
    text = _WHITESPACE.sub(" ", text.strip())
    if not text:
        return ""
    if any(pattern.match(text) for pattern in _HALLUCINATION_PATTERNS):
        log.info("Discarded a likely hallucination: %r", text)
        return ""
    return text


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    """Case-insensitive literal substitutions from the [replacements] config table.

    Keys that look like words are matched on word boundaries, so a "ci" -> "CI"
    rule does not mangle "ciasto". Keys containing punctuation or spaces are
    matched literally.
    """
    if not text or not replacements:
        return text

    for needle, replacement in replacements.items():
        if not needle:
            continue
        escaped = re.escape(needle)
        if needle[0].isalnum() and needle[-1].isalnum():
            escaped = rf"\b{escaped}\b"
        try:
            text = re.sub(escaped, lambda _m, r=replacement: r, text, flags=re.IGNORECASE)
        except re.error as exc:  # pragma: no cover - re.escape makes this unreachable
            log.warning("Skipped the replacement %r: %s", needle, exc)
    return text


def process(text: str, replacements: dict[str, str] | None = None) -> str:
    return apply_replacements(clean(text), replacements or {})
