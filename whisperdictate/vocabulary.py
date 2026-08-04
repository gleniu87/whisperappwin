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

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

#: Version-controlled half of the vocabulary, in the repository root.
SHARED_FILE = "vocabulary.txt"

#: Splitting on commas only. Proper nouns contain spaces ("Claude Code",
#: "Visual Studio"), so whitespace is not a separator.
_SEPARATOR = ","

#: Below this, two words are not the same word misheard - they are two words.
_SIMILARITY_FLOOR = 0.55

#: Shorter tokens produce noise: at three characters almost anything is 0.55
#: similar to anything else.
_MIN_LENGTH = 4

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def terms(raw: str | None) -> tuple[str, ...]:
    """Parse the configured list. Order is the user's; duplicates collapse.

    Newlines count as separators too, so the same parser reads both the config
    string and the repository file.
    """
    if not raw:
        return ()
    seen: dict[str, None] = {}
    text = str(raw)
    for line in text.splitlines():
        line = line.split("#", 1)[0]  # comments, for the file form
        for chunk in line.split(_SEPARATOR):
            term = " ".join(chunk.split())
            if term:
                seen.setdefault(term, None)
    return tuple(seen)


def shared_path() -> Path:
    """`vocabulary.txt` next to the package, i.e. in the repository."""
    return Path(__file__).resolve().parent.parent / SHARED_FILE


def read_shared(path: Path | None = None) -> str:
    """The version-controlled list, or "" when absent or unreadable.

    Never raises: a missing or broken shared file must degrade to "no shared
    terms", not break dictation.
    """
    target = path if path is not None else shared_path()
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def combined(config_raw: str | None, shared_raw: str | None = None) -> str:
    """Shared list plus the private one, as a single comma-separated string.

    Two sources on purpose. Technical names are worth sharing across machines and
    belong in the repository; client and project names are not, and stay in
    `%APPDATA%` where version control never sees them.
    """
    shared = read_shared() if shared_raw is None else shared_raw
    merged = ""
    for source in (shared, config_raw):
        for term in terms(source):
            merged = add(merged, term)
    return merged


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


@dataclass(frozen=True)
class Suggestion:
    """A name the clean-up model appears to have repaired on its own."""

    heard: str      # what Whisper produced ("dipsyka")
    corrected: str  # what the clean-up model made of it ("DeepSeeka")


#: NFD decomposes ą ć ę ń ó ś ź ż into a base letter plus a combining mark, but
#: not ł - U+0142 is an atomic codepoint with no decomposition. Without this,
#: "ustawilem" and "ustawiłem" compare as different words, which knocks the
#: whole word alignment out of step on most Polish sentences.
_ATOMIC = str.maketrans({"ł": "l", "Ł": "L"})


def fold(word: str) -> str:
    """Lowercase, diacritics stripped - the form used only for comparison.

    Public because the clean-up layer's EMPTY guard compares against filler
    lists taken from the prompt, which are written without diacritics
    ("no wiec") while Whisper produces them ("no więc"). The `ł` subtlety above
    is exactly why that comparison must not grow its own second implementation.
    """
    decomposed = unicodedata.normalize("NFD", word.lower().translate(_ATOMIC))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _looks_like_a_name(word: str) -> bool:
    """An initial capital, or an inner one (camelCase, DeepSeek, ICE)."""
    return word[:1].isupper() or any(c.isupper() for c in word[1:])


def _differs_only_in_ending(left: str, right: str) -> bool:
    """Same stem, different inflection - "alta" vs "Altu", not a misheard name.

    Found on real history: the clean-up model fixing Polish grammar around a
    capitalised word looks exactly like a name repair unless this is excluded.
    """
    shared = 0
    for a, b in zip(left, right):
        if a != b:
            break
        shared += 1
    return shared >= min(len(left), len(right)) - 1


def _is_repair(heard: str, corrected: str) -> bool:
    """True when this substitution looks like a mangled name being restored.

    The filters exist because a clean-up model rewrites a great deal that has
    nothing to do with proper nouns. Without them the user would be asked about
    every second word, and a suggestion list nobody reads is worse than none.
    """
    if len(heard) < _MIN_LENGTH or len(corrected) < _MIN_LENGTH:
        return False
    if not _looks_like_a_name(corrected):
        return False

    left, right = fold(heard), fold(corrected)
    if left == right:
        # Differs only in diacritics or capitalisation: "wez" -> "weź". That is
        # ordinary spelling repair, not a name the decoder failed to recognise.
        return False
    if _differs_only_in_ending(left, right):
        return False
    similarity = SequenceMatcher(None, left, right).ratio()
    return similarity >= _SIMILARITY_FLOOR


def detect(heard_text: str | None, cleaned_text: str | None) -> list[Suggestion]:
    """Word substitutions between a raw transcript and its cleaned version.

    Aligns the two word sequences and keeps one-for-one replacements that pass
    `_is_repair`. This is the only free signal available: the clean-up model
    occasionally recognises a mangled name from context, and that guess is worth
    keeping - moved into the vocabulary it stops the mishearing at the source,
    where even a weaker model never has to guess again.
    """
    heard_words = _WORD.findall(heard_text or "")
    clean_words = _WORD.findall(cleaned_text or "")
    if not heard_words or not clean_words:
        return []

    found: list[Suggestion] = []
    matcher = SequenceMatcher(
        None, [fold(w) for w in heard_words], [fold(w) for w in clean_words]
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        # Only 1:1 swaps. A run of replaced words is a rephrasing, not a name.
        if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
            continue
        heard, corrected = heard_words[i1], clean_words[j1]
        if _is_repair(heard, corrected):
            found.append(Suggestion(heard=heard, corrected=corrected))
    return found


def pending(entries, known: str | None, rejected: str | None) -> list[Suggestion]:
    """Suggestions from history entries, minus what is already settled.

    Regenerated from history rather than stored: history is the source of truth,
    so there is no separate list to keep in sync, and accepting a name makes its
    suggestion disappear on its own.
    """
    settled = {fold(t) for t in terms(known)} | {fold(t) for t in terms(rejected)}
    seen: dict[str, Suggestion] = {}
    for entry in entries:
        for found in detect(entry.get("raw_text"), entry.get("text")):
            key = fold(found.corrected)
            if key in settled or key in seen:
                continue
            # A name is settled once accepted in any inflected form; matching on
            # the stem keeps "DeepSeeka" from being offered after "DeepSeek".
            if any(key.startswith(s) or s.startswith(key) for s in settled):
                continue
            seen[key] = found
    return list(seen.values())


#: Polish case endings on borrowed masculine nouns: DeepSeeka, DeepSeekowi,
#: DeepSeekiem, DeepSeeku, Anthropica. Longest first so "owi" wins over "i".
_ENDINGS = ("owi", "iem", "em", "a", "u")

#: Below this a stem is too short to trust; "Java" -> "Jav" is the failure mode.
_MIN_STEM = 4


def _is_inflection_of(longer: str, shorter: str) -> bool:
    """True when `longer` is `shorter` plus a case ending.

    The space check is what keeps "Claude" from swallowing "Claude Code": that
    difference is another word, not an ending.
    """
    if not longer.startswith(shorter) or longer == shorter:
        return False
    suffix = longer[len(shorter):]
    return " " not in suffix and len(suffix) <= 3


def base_form(word: str) -> str:
    """Best guess at the nominative. A guess - the caller must let it be edited.

    The clean-up model returns whatever form the sentence needed ("Anthropica"),
    but the vocabulary wants the stem: it primes Whisper and anchors the prompt,
    and an inflected entry does both jobs worse.

    Deliberately no dictionary and no cleverness. It is wrong on names that
    genuinely end in -a ("Java" -> "Jav"), which is why the dialog shows the
    original alongside and the field stays editable.
    """
    cleaned = " ".join(str(word or "").split())
    if " " in cleaned:
        return cleaned  # multi-word names are not declined as one token here
    for ending in _ENDINGS:
        if cleaned.lower().endswith(ending) and len(cleaned) - len(ending) >= _MIN_STEM:
            return cleaned[: -len(ending)]
    return cleaned


def add(known: str | None, term: str) -> str:
    """Append a term, collapsing inflected forms onto the base one.

    Without this, accepting a suggestion before the stem was known leaves both
    "Anthropic" and "Anthropica" in the list - which is how the real vocabulary
    ended up with one of each.
    """
    existing = list(terms(known))
    cleaned = " ".join(str(term or "").split())
    if not cleaned:
        return ", ".join(existing)

    new_fold = fold(cleaned)
    result: list[str] = []
    replaced = False
    for current in existing:
        current_fold = fold(current)
        if current_fold == new_fold:
            return ", ".join(existing)  # already there, verbatim
        if _is_inflection_of(new_fold, current_fold):
            return ", ".join(existing)  # the new one is an inflection: keep the stem
        if _is_inflection_of(current_fold, new_fold):
            # The new one is the stem; it supersedes the inflected entry in place.
            if not replaced:
                result.append(cleaned)
                replaced = True
            continue
        result.append(current)

    if not replaced:
        result.append(cleaned)
    return ", ".join(result)


def remove(known: str | None, term: str) -> str:
    """Drop a term. Matches the way `add` matches, so what got in can get out.

    Folded comparison, and inflections of the target go too: a list that took
    "Anthropica" in as an inflection of "Anthropic" must not keep a copy of it
    after "Anthropic" is removed. Anything not there is simply not there - no
    error, because the caller is a dialog acting on what it just displayed.
    """
    target = fold(" ".join(str(term or "").split()))
    if not target:
        return ", ".join(terms(known))
    kept = [
        current for current in terms(known)
        if fold(current) != target and not _is_inflection_of(fold(current), target)
    ]
    return ", ".join(kept)


def prompt_section(raw: str | None) -> str:
    """Section appended to the clean-up system prompt, or "" when unset.

    The last sentence is the important one. Handed a list of names, a model will
    otherwise start inserting them into transcripts that never mentioned them -
    which is exactly the hallucination the rest of the prompt works to prevent.
    """
    names = terms(raw)
    if not names:
        return ""
    # "odmien ja naturalnie po polsku" stays, and it is not an oversight left
    # over from a Polish-only version. It reads wrong for English dictation, so
    # it was rewritten language-neutral - and that was measured, twice, as worse:
    #
    #   deepseek-v4-flash, same text, "Sonnet" restored correctly
    #     Polish   "po polsku" 14/16   neutral 9/16
    #     English  "po polsku"   8/8   neutral   8/8
    #
    # English never needed the fix: the language lock appended after this section
    # already overrides the Polish instruction. Polish, meanwhile, loses a third
    # of its hit rate without the concrete grammatical cue. One list, one
    # section, and the base-form terms get declined correctly in both languages.
    return (
        "\n\nNAZWY WLASNE uzywane przez mowiacego: "
        + ", ".join(names)
        + ".\nJesli transkrypcja zawiera slowo brzmiace podobnie do ktorejs z nich "
        "(Whisper czesto je przekreca, np. 'Dipsick' zamiast 'DeepSeek'), przywroc "
        "poprawna pisownie z tej listy i odmien ja naturalnie po polsku. "
        "NIE dopisuj nazw z tej listy, jesli w transkrypcji nic ich nie przypomina."
    )
