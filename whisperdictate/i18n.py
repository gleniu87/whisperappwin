"""Interface text in Polish and English, and how the language gets chosen.

Three surfaces speak to whoever is dictating - the tray menu, the recording
overlay and the dialogs - and everything they say lives in `MESSAGES` below. The
log and the CLI stay Polish: they are read by whoever is debugging, and keeping
them out of here keeps the catalogue to the size of a screen of real UI text.

The active language is module state on purpose. The alternative is threading a
translator object through the controller into the tray, the overlay and every
dialog, and there is exactly one user per process. `use()` is called once from
`main()` with what the config says, and again whenever the tray switches it.

Imports nothing from the package, so config, audio and the enhancement layer can
all read it without a cycle.
"""

from __future__ import annotations

import locale
import logging
import os
from collections.abc import Sequence

log = logging.getLogger(__name__)

#: Languages the interface is translated into. The dictation language is a
#: separate setting with a separate list (config.LANGUAGES) - dictating in
#: English with a Polish menu is a perfectly normal thing to want.
UI_LANGUAGES: tuple[str, ...] = ("pl", "en")

#: Used when the system says something we do not speak, and as the last resort
#: for a key missing from a translation.
FALLBACK = "en"

# Windows primary language IDs (the low 10 bits of an LCID). Only the two we
# translate into are listed: anything else is a fallback case anyway, so a fuller
# table would be lookup code that can never change the answer.
_PRIMARY_LANGUAGE_IDS = {0x15: "pl", 0x09: "en"}

# Locale names arrive in two shapes on the same machine: "pl_PL" from the C
# library, "Polish_Poland" from the Windows setlocale layer.
_LOCALE_PREFIXES = {"pl": "pl", "polish": "pl", "en": "en", "english": "en"}


MESSAGES: dict[str, dict[str, str]] = {
    # -- states, shared by the tray tooltip and the overlay -------------
    "state.idle": {"pl": "Gotowy", "en": "Ready"},
    "state.loading": {"pl": "Ładuję model", "en": "Loading model"},
    "state.recording": {"pl": "Nagrywanie", "en": "Recording"},
    "state.transcribing": {"pl": "Transkrybuję", "en": "Transcribing"},
    "state.enhancing": {"pl": "Czyszczę tekst", "en": "Cleaning up text"},
    "state.error": {"pl": "Błąd", "en": "Error"},
    "state.paused": {"pl": "Wstrzymane", "en": "Paused"},

    # -- dictation language, named in the language of the menu ----------
    "language.pl": {"pl": "Polski", "en": "Polish"},
    "language.en": {"pl": "Angielski", "en": "English"},
    "language.auto": {"pl": "Auto-detekcja", "en": "Auto-detect"},

    # -- interface language, named in itself ----------------------------
    # Endonyms, identical in both catalogues and deliberately so: someone who
    # switched to a language he cannot read has to be able to find his way back,
    # and "Polski" is recognisable from an English menu in a way that a
    # translated "Polish" would not be from a Polish one.
    "ui_language.pl": {"pl": "Polski", "en": "Polski"},
    "ui_language.en": {"pl": "English", "en": "English"},

    # -- tray menu ------------------------------------------------------
    "menu.dictation_language": {"pl": "Język dyktowania", "en": "Dictation language"},
    "menu.app_language": {"pl": "Język aplikacji", "en": "App language"},
    "menu.model": {"pl": "Model: {name}", "en": "Model: {name}"},
    "menu.microphone": {"pl": "Mikrofon", "en": "Microphone"},
    "menu.hotkey": {"pl": "Hotkey: {key}", "en": "Hotkey: {key}"},
    "menu.enhancement": {"pl": "Czyszczenie tekstu", "en": "Text clean-up"},
    # Master switch. Phrased as "switch it on", checked while it is on - the same
    # shape as "Włącz czyszczenie", and the opposite half of the controller's
    # "paused". Named after dictation, not the app: the app is plainly still
    # running, since you are reading its menu.
    "menu.enabled": {"pl": "Włącz dyktowanie", "en": "Enable dictation"},
    "menu.copy_last": {
        "pl": "Skopiuj ostatnią transkrypcję",
        "en": "Copy last transcription",
    },
    "menu.open_config": {"pl": "Otwórz konfigurację", "en": "Open config file"},
    "menu.open_history": {"pl": "Otwórz historię", "en": "Open history"},
    "menu.open_log": {"pl": "Otwórz log", "en": "Open log"},
    "menu.quit": {"pl": "Zakończ", "en": "Quit"},
    "menu.refresh_devices": {"pl": "Odśwież listę", "en": "Refresh list"},
    "menu.enhancement.enable": {"pl": "Włącz czyszczenie", "en": "Enable clean-up"},
    "menu.enhancement.provider": {"pl": "Provider: {name}", "en": "Provider: {name}"},
    "menu.enhancement.model": {"pl": "Model: {name}", "en": "Model: {name}"},
    "menu.enhancement.style": {"pl": "Styl: {name}", "en": "Style: {name}"},
    "menu.vocabulary.suggest": {"pl": "Proponuj nazwy własne", "en": "Suggest proper nouns"},
    "menu.vocabulary.pending": {
        "pl": "Propozycje słownika ({count})...",
        "en": "Vocabulary suggestions ({count})...",
    },
    "menu.vocabulary.edit": {"pl": "Nazwy własne...", "en": "Proper nouns..."},
    "menu.api_key": {"pl": "Klucz API: {provider}...", "en": "API key: {provider}..."},

    # -- tray tooltip and balloons --------------------------------------
    "tray.tooltip.microphone": {"pl": "Mikrofon: {device}", "en": "Microphone: {device}"},
    "tray.error_title": {"pl": "{app} - błąd", "en": "{app} - error"},
    "tray.devices_found": {
        "pl": "Znaleziono {count} mikrofon(ów).",
        "en": "Found {count} microphone(s).",
    },
    "tray.no_refresh_while_recording": {
        "pl": "Nie mogę odświeżyć listy w trakcie nagrywania.",
        "en": "Cannot refresh the list while recording.",
    },

    # -- microphones ----------------------------------------------------
    "device.default": {"pl": "Domyślne systemowe", "en": "System default"},
    "device.disconnected": {"pl": "{name} (niepodłączony)", "en": "{name} (disconnected)"},

    # -- push-to-talk keys ----------------------------------------------
    # The long form carries the trade-off, the short one goes in the menu label
    # that shows the current choice. Two entries rather than splitting the long
    # form on a separator, because the separator would differ per language.
    "hotkey.ctrl_r": {"pl": "Prawy Ctrl (zalecany)", "en": "Right Ctrl (recommended)"},
    "hotkey.ctrl_r.short": {"pl": "Prawy Ctrl", "en": "Right Ctrl"},
    "hotkey.ctrl_l": {"pl": "Lewy Ctrl", "en": "Left Ctrl"},
    "hotkey.ctrl_l.short": {"pl": "Lewy Ctrl", "en": "Left Ctrl"},
    # The ą, ę, ó stay in the English text: the clash is a property of the Polish
    # keyboard layout, not of the menu's language.
    "hotkey.alt_r": {
        "pl": "Prawy Alt / AltGr - koliduje z ą, ę, ó",
        "en": "Right Alt / AltGr - clashes with ą, ę, ó",
    },
    "hotkey.alt_r.short": {"pl": "Prawy Alt / AltGr", "en": "Right Alt / AltGr"},
    "hotkey.alt_l": {"pl": "Lewy Alt", "en": "Left Alt"},
    "hotkey.alt_l.short": {"pl": "Lewy Alt", "en": "Left Alt"},
    "hotkey.scroll_lock": {"pl": "Scroll Lock", "en": "Scroll Lock"},
    "hotkey.scroll_lock.short": {"pl": "Scroll Lock", "en": "Scroll Lock"},
    "hotkey.pause": {"pl": "Pause", "en": "Pause"},
    "hotkey.pause.short": {"pl": "Pause", "en": "Pause"},

    # -- clean-up styles ------------------------------------------------
    "style.default": {"pl": "Domyślny (pełne czyszczenie)", "en": "Default (full clean-up)"},
    "style.default.short": {"pl": "Domyślny", "en": "Default"},
    "style.chat": {"pl": "Czat / Slack", "en": "Chat / Slack"},
    "style.chat.short": {"pl": "Czat / Slack", "en": "Chat / Slack"},
    "style.verbatim": {
        "pl": "Dosłowny (tylko interpunkcja)",
        "en": "Verbatim (punctuation only)",
    },
    "style.verbatim.short": {"pl": "Dosłowny", "en": "Verbatim"},

    # -- clean-up providers ---------------------------------------------
    # The product name itself is data and lives in enhance/registry.py; only the
    # trade-off hint and the jurisdiction are translated.
    "provider.anthropic.hint": {"pl": "~1 s", "en": "~1 s"},
    "provider.deepseek.hint": {"pl": "~10x tańszy", "en": "~10x cheaper"},
    "provider.claude_cli.hint": {"pl": "~5 s, bez klucza", "en": "~5 s, no key needed"},
    "provider.anthropic.hosting": {"pl": "Anthropic (USA)", "en": "Anthropic (USA)"},
    "provider.deepseek.hosting": {
        "pl": "DeepSeek (Chiny) - nie używać do treści służbowych",
        "en": "DeepSeek (China) - do not use for work material",
    },
    "provider.claude_cli.hosting": {
        "pl": "Anthropic, przez Twoją subskrypcję Claude Code",
        "en": "Anthropic, through your Claude Code subscription",
    },
    "provider.problem.no_package": {
        "pl": "brak pakietu anthropic",
        "en": "the anthropic package is missing",
    },
    "provider.problem.no_key": {
        "pl": "brak klucza API ({provider})",
        "en": "no API key for {provider}",
    },
    "provider.problem.not_in_path": {
        "pl": "nie znaleziono {executable} w PATH",
        "en": "{executable} not found in PATH",
    },
    "enhancement.disabled": {"pl": "wyłączone", "en": "disabled"},

    # -- where an API key comes from ------------------------------------
    "credentials.env": {
        "pl": "zmienna środowiskowa {env_var}",
        "en": "environment variable {env_var}",
    },
    "credentials.store": {
        "pl": "Menedżer poświadczeń Windows",
        "en": "Windows Credential Manager",
    },
    "credentials.none": {"pl": "brak", "en": "none"},

    # -- what the overlay and the balloons report after a dictation -----
    "detail.loading_model": {"pl": "ładuję model", "en": "loading model"},
    "detail.loading_named_model": {"pl": "ładuję {model}", "en": "loading {model}"},
    "detail.too_short": {"pl": "za krótkie", "en": "too short"},
    "detail.cancelled": {"pl": "anulowano", "en": "cancelled"},
    "detail.silence": {"pl": "cisza", "en": "silence"},
    "detail.noise_rejected": {"pl": "odrzucone jako szum", "en": "rejected as noise"},
    "detail.internal_error": {"pl": "błąd wewnętrzny", "en": "internal error"},
    "detail.delivered": {
        "pl": "{chars} znaków, {speedup:.0f}x realtime",
        "en": "{chars} chars, {speedup:.0f}x realtime",
    },
    "detail.enhanced_suffix": {
        "pl": ", oczyszczone +{seconds:.1f} s",
        "en": ", cleaned +{seconds:.1f} s",
    },
    "notify.model_ready": {"pl": "Model: {description}", "en": "Model: {description}"},
    "notify.unknown_key": {"pl": "Nieznany klawisz: {key}", "en": "Unknown key: {key}"},
    "notify.enhancement_problem": {
        "pl": "Czyszczenie włączone, ale {problem}.",
        "en": "Clean-up is on, but {problem}.",
    },
    "notify.provider_problem": {
        "pl": "Provider {provider}: {problem}.",
        "en": "Provider {provider}: {problem}.",
    },
    "notify.copied_last": {
        "pl": "Ostatnia transkrypcja w schowku ({chars} znaków).",
        "en": "Last transcription copied ({chars} chars).",
    },
    "notify.nothing_to_copy": {
        "pl": "Nie ma czego skopiować - historia jest pusta albo wyłączona.",
        "en": "Nothing to copy - the history is empty or switched off.",
    },
    "notify.paste_failed": {
        "pl": "{error} Tekst jest w historii.",
        "en": "{error} The text is in the history.",
    },
    # The menu path has to match the translated menu entries above.
    "notify.new_proper_noun": {
        "pl": "Nowa nazwa własna? {heard} -> {corrected}. "
              "Menu tray > Czyszczenie tekstu > Propozycje słownika.",
        "en": "New proper noun? {heard} -> {corrected}. "
              "Tray menu > Text clean-up > Vocabulary suggestions.",
    },

    # -- failures the user sees, not just the log ------------------------
    "error.microphone_open": {
        "pl": "Nie mogę otworzyć mikrofonu. Próbowane formaty: {failures}",
        "en": "Cannot open the microphone. Formats tried: {failures}",
    },
    "error.device_missing": {
        "pl": "Nie znaleziono mikrofonu {device} - nagrywam z domyślnego systemowego",
        "en": "Microphone {device} not found - recording from the system default",
    },
    "error.no_faster_whisper": {
        "pl": "Brak pakietu faster-whisper. Uruchom: pip install -r requirements.txt",
        "en": "The faster-whisper package is missing. Run: pip install -r requirements.txt",
    },
    "error.model_load": {
        "pl": "Nie mogę załadować modelu {model}: {error}",
        "en": "Cannot load the {model} model: {error}",
    },
    "error.model_load_anywhere": {
        "pl": "Nie mogę załadować modelu {model} ani na GPU, ani na CPU: {error}",
        "en": "Cannot load the {model} model on the GPU or the CPU: {error}",
    },
    "error.transcription_failed": {
        "pl": "Transkrypcja nie powiodła się: {error}",
        "en": "Transcription failed: {error}",
    },
    "error.clipboard_busy": {
        "pl": "Schowek zajęty przez inną aplikację: {last}",
        "en": "The clipboard is held by another application: {last}",
    },

    # -- dialogs: a repaired name offered for the vocabulary -------------
    "dialog.suggestion.title": {
        "pl": "{app} - nowa nazwa własna?",
        "en": "{app} - a new proper noun?",
    },
    "dialog.suggestion.corrected": {
        "pl": "Model czyszczący poprawił:{counter}",
        "en": "The clean-up model corrected:{counter}",
    },
    "dialog.suggestion.explain": {
        "pl": "Do słownika trafi forma podstawowa (mianownik) -\n"
              "odmianę model zrobi sam. Popraw, jeśli zgadłem źle:",
        "en": "The vocabulary stores the base form -\n"
              "the model inflects it on its own. Fix it if I guessed wrong:",
    },
    "dialog.suggestion.inflected": {
        "pl": "(model podał odmienione: {corrected})",
        "en": "(the model returned an inflected form: {corrected})",
    },
    "dialog.suggestion.priming": {
        "pl": "Whisper będzie odtąd nastawiony na tę pisownię.",
        "en": "Whisper will be primed for this spelling from now on.",
    },
    "dialog.suggestion.none": {"pl": "Brak nowych propozycji.", "en": "No new suggestions."},
    "dialog.button.add": {"pl": "Dopisz", "en": "Add"},
    "dialog.button.skip": {"pl": "Nie tym razem", "en": "Not this time"},
    "dialog.button.never": {"pl": "Nigdy o to nie pytaj", "en": "Never ask again"},

    # -- dialogs: the vocabulary itself ---------------------------------
    "dialog.vocabulary.title": {"pl": "{app} - nazwy własne", "en": "{app} - proper nouns"},
    "dialog.vocabulary.intro": {
        "pl": "Nazwy, które Whisper przekręca. Trafiają do Whispera (żeby usłyszał\n"
              "je poprawnie) i do modelu czyszczącego (żeby naprawił przekręcone).",
        "en": "Names Whisper garbles. They go to Whisper (so it hears them right)\n"
              "and to the clean-up model (so it repairs the garbled ones).",
    },
    "dialog.vocabulary.add_label": {
        "pl": "Dodaj nazwę (Enter):",
        "en": "Add a name (Enter):",
    },
    "dialog.vocabulary.add_hint": {
        "pl": "Po jednej. W formie podstawowej - odmianę model zrobi sam.",
        "en": "One at a time, in its base form - the model inflects it itself.",
    },
    "dialog.vocabulary.your_names": {"pl": "Twoje nazwy ({count}):", "en": "Your names ({count}):"},
    "dialog.vocabulary.empty": {
        "pl": "Jeszcze nic tu nie ma.",
        "en": "Nothing here yet.",
    },
    "dialog.vocabulary.shared": {
        "pl": "Do tego {count} nazw technicznych z {file} (wspólne, w repozytorium).",
        "en": "Plus {count} technical names from {file} (shared, in the repository).",
    },
    "dialog.vocabulary.button_add": {"pl": "Dodaj", "en": "Add"},
    "dialog.vocabulary.button_remove": {"pl": "Usuń zaznaczoną", "en": "Remove selected"},
    "dialog.vocabulary.button_close": {"pl": "Zamknij", "en": "Close"},
    "dialog.vocabulary.duplicate": {
        "pl": "{name} jest już na liście (albo jej odmiana).",
        "en": "{name} is already on the list (or an inflection of it).",
    },

    # -- dialogs: API keys ----------------------------------------------
    "dialog.key.title": {"pl": "{app} - klucz {provider}", "en": "{app} - {provider} key"},
    "dialog.key.exists": {
        "pl": "Klucz jest już ustawiony ({source}).\n\n"
              "Tak - wpisz nowy\n"
              "Nie - usuń zapisany klucz\n"
              "Anuluj - zostaw bez zmian",
        "en": "A key is already set ({source}).\n\n"
              "Yes - enter a new one\n"
              "No - delete the stored key\n"
              "Cancel - leave it alone",
    },
    "dialog.key.removed": {"pl": "Klucz usunięty.", "en": "Key deleted."},
    "dialog.key.nothing_to_remove": {
        "pl": "Nie było zapisanego klucza.",
        "en": "There was no stored key.",
    },
    "dialog.key.prompt": {
        "pl": "Wklej klucz API dla: {label}\n"
              "Ruch trafia do: {hosting}\n\n"
              "Klucz zostanie zapisany w Menedżerze poświadczeń Windows,\n"
              "nie w pliku konfiguracyjnym.",
        "en": "Paste the API key for: {label}\n"
              "Traffic goes to: {hosting}\n\n"
              "The key is stored in Windows Credential Manager,\n"
              "not in the config file.",
    },
    "dialog.key.save_failed": {
        "pl": "Nie mogę zapisać klucza:\n{error}",
        "en": "Cannot save the key:\n{error}",
    },
    "dialog.key.saved": {"pl": "Klucz ({provider}) zapisany.", "en": "Key ({provider}) saved."},
}


_active = FALLBACK


# -- the active language ------------------------------------------------


def use(code: str | None) -> str:
    """Activate a language. Returns the one actually in force.

    Anything untranslated silently becomes the fallback rather than raising: an
    unreadable menu is a bad reason to refuse to start.
    """
    global _active
    _active = code if code in UI_LANGUAGES else FALLBACK
    return _active


def language() -> str:
    return _active


def t(key: str, /, **fields: object) -> str:
    """One catalogue entry in the active language, with `{placeholders}` filled.

    `key` is positional-only, and that is not decoration: "menu.hotkey" has a
    `{key}` placeholder, and without the `/` the call would raise "t() got
    multiple values for argument 'key'". Any placeholder named after this
    function's own parameter would hit the same wall.
    """
    entry = MESSAGES.get(key)
    if entry is None:
        # Returning the key makes the gap visible in the UI instead of blank.
        log.warning("Brak wpisu w katalogu tlumaczen: %r", key)
        return key
    text = entry.get(_active) or entry[FALLBACK]
    return text.format(**fields) if fields else text


# -- what the system says -----------------------------------------------


def detect_system_language() -> str:
    """The interface language to start with on a machine we know nothing about."""
    return resolve_language(_windows_ui_language_id(), _locale_names())


def resolve_language(ui_language_id: int | None, locale_names: Sequence[str]) -> str:
    """Pure decision: which language to use, given what the OS reported.

    Windows' own display language wins. It is the question actually being asked -
    "which language does this person read software in" - whereas the locale is
    the formats setting, and a Polish person running English Windows deliberately
    set both. The locale is consulted only when the display language is
    unavailable (no ctypes, not Windows), where it is the sole remaining signal.
    """
    if ui_language_id is not None:
        primary = _PRIMARY_LANGUAGE_IDS.get(ui_language_id & 0x3FF)
        return primary if primary in UI_LANGUAGES else FALLBACK

    for name in locale_names:
        head = name.strip().lower().replace("-", "_").split("_")[0]
        if head in _LOCALE_PREFIXES:
            return _LOCALE_PREFIXES[head]
    return FALLBACK


def _windows_ui_language_id() -> int | None:
    """GetUserDefaultUILanguage, or None anywhere it cannot be reached."""
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not Windows, or a stripped-down runtime
        log.debug("Nie moge odczytac jezyka interfejsu Windows")
        return None


def _locale_names() -> list[str]:
    """Locale hints, most specific first. Shapes differ per platform and layer."""
    names: list[str] = []
    try:
        current = locale.getlocale()[0]
    except (TypeError, ValueError):  # pragma: no cover - malformed locale setting
        current = None
    if current:
        names.append(current)
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(env_var, "")
        if value:
            names.append(value)
    return names
