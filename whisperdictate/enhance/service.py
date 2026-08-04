"""Orchestration and output filtering for the enhancement layer."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from ..i18n import t
from . import prompts, providers, registry
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


def says_empty(reply: str) -> bool:
    """True when the model actually asked for the EMPTY sentinel.

    Kept separate from "output_filter returned nothing", because those are not
    the same event and conflating them destroys dictations. A reply consisting
    only of a reasoning block filters down to "" as well - and llama.cpp and
    LM Studio, both of which enhancement.base_url exists to support, put that
    block *inside* `content`, where the provider's empty-content check cannot see
    it. Measured: content="<think>the user just greeted me</think>" against the
    transcript "dzień dobry" produced text="" and the dictation was dropped.
    """
    stripped = reply.strip()
    if stripped == _EMPTY_SENTINEL:
        return True
    # A model that adds a trailing period or wraps the sentinel still means EMPTY.
    return stripped.strip("\"'.").strip() == _EMPTY_SENTINEL


_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("„", "”"), ("“", "”"))

# Guardrail against a provider that ignores the prompt and answers the
# transcript instead of cleaning it. Cleanup output tracks input length; a reply
# several times longer is a different kind of text.
_RUNAWAY_RATIO = 3.0
_RUNAWAY_FLOOR = 400

# Exactly the fillers prompts._CLEANUP_RULES tells the model to remove, split
# into words: "no wiec", "w sensie", "po prostu", "you know" and "sort of"
# contribute both halves. Written without diacritics because that is how the
# prompt writes them - the comparison folds both sides, so "no więc" matches.
#
# Kept in lockstep with the prompt on purpose, and a test enforces it. Adding a
# plausible-looking extra ("aaa", "hmm") would mean the guard forgives a word the
# model was never asked to strip; worse, an earlier draft of this list included
# "tak", which the prompt explicitly protects - it is what Polish "no" usually
# means, so it is a word with content, not noise.
_FILLER_WORDS = frozenset({
    "yyy", "eee", "mmm", "no", "wiec", "wiesz", "jakby", "w", "sensie",
    "po", "prostu", "tego", "um", "uh", "like", "you", "know", "sort", "of",
    "basically",
})

# Below this, a transcript is too short for us to second-guess a model that
# called it noise. It must stay under 27 characters so that "Zaproponuj
# nastepne zadania" - a valid command that qwen3.5:4b reduced to nothing - is
# protected. It does not need to cover "yyy eee no wiec yyy" (19), because the
# all-fillers rule already does.
_EMPTY_MAX_CHARS = 16

_WORD_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _plausibly_empty(transcript: str) -> bool:
    """True when EMPTY is a believable verdict on this transcript.

    The EMPTY sentinel is load-bearing and mostly right, but honouring it is
    irreversible: controller.py pastes nothing *and* returns before writing
    history, so a wrong EMPTY leaves no trace anywhere - not in the clipboard,
    not in the log of past dictations, not in "Copy last transcription".

    Measured: DeepSeek returned EMPTY 0 times across 68 real history entries, so
    this changes nothing for it. The local models are the reason it exists -
    qwen3.5:9b lost 1 transcript of 76, qwen3.5:4b lost 3, one of them the
    perfectly good command "Zaproponuj nastepne zadania".

    The worst this guard can do is paste a raw "yyy eee" instead of nothing,
    which is plainly better than losing a sentence.
    """
    from ..postprocess import looks_like_hallucination
    from ..vocabulary import fold

    stripped = transcript.strip()
    if not stripped:
        return True

    # A Whisper silence artefact is a legitimate EMPTY however long it is. The
    # 44-character "Napisy stworzone przez spolecznosc Amara.org" is why this
    # cannot be a length rule alone - it is also one of the fixed case set's
    # expect_empty entries, so getting it wrong would fail every provider there.
    if looks_like_hallucination(stripped):
        return True

    # A digit is content, and the filler rule cannot see one: _WORD_TOKEN matches
    # letters only, so every *word* of "no wiec 601 234 567" is a filler and the
    # number would be thrown away with them. Dictated phone numbers, times,
    # amounts and dates arrive in exactly that shape, wrapped in hesitation, and
    # they are the least reconstructible thing a user can lose.
    if any(character.isdigit() for character in stripped):
        return False

    words = _WORD_TOKEN.findall(stripped)
    if words and all(fold(word) in _FILLER_WORDS for word in words):
        return True

    return len(stripped) <= _EMPTY_MAX_CHARS


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

    # One implementation of "is this the sentinel", shared with the caller's
    # guard, so the two cannot come to disagree about what EMPTY looks like.
    if says_empty(text):
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
        return name if name in registry.PROVIDERS else "anthropic"

    @property
    def model(self) -> str:
        return self.config.get("enhancement.model", "claude-haiku-4-5")

    def describe(self) -> str:
        if not self.enabled:
            return t("enhancement.disabled")
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

        # Snapshot once. Every property here re-reads the live config, which the
        # tray writes from another thread, so reading `self.model` again later
        # would let a menu click land between the request and the result: the
        # history would then name a model that never saw this text, and the pair
        # could come out as "ollama/claude-haiku-4-5". That history is the dataset
        # the model choice was measured from, so it has to stay truthful.
        model = self.model
        provider = self._provider(model)
        from ..vocabulary import combined

        system = prompts.build(
            self.config.get("enhancement.prompt", "default"),
            language,
            combined(self.config.get("transcription.vocabulary", "")),
        )
        user = prompts.wrap_transcript(text)
        timeout = float(self.config.get("enhancement.timeout_seconds", 30))
        started = time.perf_counter()

        try:
            raw_reply = provider.complete(system, user, model, timeout)
        except ProviderError as exc:
            log.warning("Text clean-up failed (%s) - pasting the raw text", exc)
            return None
        except Exception:  # noqa: BLE001 - a provider bug must not eat the dictation
            log.exception("Unexpected clean-up error - pasting the raw text")
            return None

        cleaned = output_filter(raw_reply)
        elapsed = time.perf_counter() - started

        if not cleaned and not (says_empty(raw_reply) and _plausibly_empty(text)):
            # Nothing to paste, and no good reason for it. Two cases land here and
            # both must fall back to the raw text rather than to silence:
            #   * the model asked for EMPTY on a transcript that plainly was not
            #     (measured: qwen3.5:4b did this to "Zaproponuj następne zadania"),
            #   * the reply filtered down to nothing without asking for it at all,
            #     which is what a reasoning-only answer does.
            # Returning "" instead would discard the dictation outright, with no
            # copy in the clipboard, the history or "Copy last transcription".
            log.warning(
                "The clean-up layer produced nothing for a %d-character transcript "
                "(sentinel=%s) - pasting the raw text instead: %r",
                len(text), says_empty(raw_reply), text,
            )
            return None

        if _looks_like_an_answer(cleaned, text):
            log.warning(
                "LLM reply (%d characters) out of proportion to the transcript (%d) "
                "- looks like an answer, not a clean-up; pasting the raw text",
                len(cleaned), len(text),
            )
            return None

        log.info(
            "Clean-up: %d -> %d characters in %.2f s (%s / %s)",
            len(text), len(cleaned), elapsed, provider.name, model,
        )
        return EnhancementResult(
            text=cleaned,
            raw_text=text,
            provider=provider.name,
            model=model,
            elapsed_seconds=elapsed,
        )

    def _provider(self, model: str = "") -> providers.Provider:
        """`model` is only read by check(); complete() is passed one explicitly.

        Defaults to the live config value so check() - which has no snapshot -
        still asks about the model the user currently has selected.
        """
        return providers.build(
            self.provider_name,
            cli_path=self.config.get("enhancement.cli_path", "") or "",
            base_url=self.config.get("enhancement.base_url", "") or "",
            model=model or self.model,
        )


def _looks_like_an_answer(cleaned: str, original: str) -> bool:
    """True when the reply is too long to be a cleaned version of the input.

    An empty result is not this guard's business - by the time it runs,
    `_plausibly_empty` has already decided whether that emptiness was the EMPTY
    sentinel firing or a model losing a dictation.
    """
    if not cleaned:
        return False
    return len(cleaned) > _RUNAWAY_FLOOR and len(cleaned) > len(original) * _RUNAWAY_RATIO
