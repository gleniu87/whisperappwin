"""The speaker's proper nouns, fed to both halves of the pipeline.

Whisper mangles names it has no reason to expect - "DeepSeek" comes back as
"Dipsick", "dipsyka", "Deepsika". The clean-up model then cannot repair what it
cannot recognise: with nothing to anchor on, "Dipsick" is just a word, and a
model that guessed at it would be hallucinating rather than cleaning.

So one list serves two purposes:

* Whisper's `initial_prompt` primes the decoder towards these spellings, which
  stops most of the damage at the source.
* The clean-up prompt gets the same list, so a name that slipped through
  garbled has something to be matched against.

Neither alone is enough. Priming still misses; and repairing after the fact
leaves the raw transcript in the history mangled, and does nothing at all when
clean-up is switched off.
"""

from __future__ import annotations

#: Splitting on commas only. Proper nouns contain spaces ("Claude Code",
#: "ICE InsureTech"), so whitespace is not a separator.
_SEPARATOR = ","


def terms(raw: str | None) -> tuple[str, ...]:
    """Parse the configured list. Order is the user's; duplicates collapse."""
    if not raw:
        return ()
    seen: dict[str, None] = {}
    for chunk in str(raw).split(_SEPARATOR):
        term = " ".join(chunk.split())
        if term:
            seen.setdefault(term, None)
    return tuple(seen)


def whisper_priming(raw: str | None, initial_prompt: str | None = "") -> str:
    """Text for Whisper's `initial_prompt`.

    `transcription.initial_prompt` stays honoured and comes first: it is the
    escape hatch for anyone who wants full control of the priming text, and the
    vocabulary is appended to it rather than replacing it.
    """
    parts = []
    primer = " ".join(str(initial_prompt or "").split())
    if primer:
        parts.append(primer if primer.endswith((".", "!", "?")) else primer + ".")
    names = terms(raw)
    if names:
        parts.append(", ".join(names) + ".")
    return " ".join(parts)


def prompt_section(raw: str | None) -> str:
    """Section appended to the clean-up system prompt, or "" when unset.

    The last sentence is the important one. Handed a list of names, a model will
    otherwise start inserting them into transcripts that never mentioned them -
    which is exactly the hallucination the rest of the prompt works to prevent.
    """
    names = terms(raw)
    if not names:
        return ""
    return (
        "\n\nNAZWY WLASNE uzywane przez mowiacego: "
        + ", ".join(names)
        + ".\nJesli transkrypcja zawiera slowo brzmiace podobnie do ktorejs z nich "
        "(Whisper czesto je przekreca, np. 'Dipsick' zamiast 'DeepSeek'), przywroc "
        "poprawna pisownie z tej listy i odmien ja naturalnie po polsku. "
        "NIE dopisuj nazw z tej listy, jesli w transkrypcji nic ich nie przypomina."
    )
