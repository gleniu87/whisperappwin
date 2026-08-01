"""The translation catalogue and the first-run language guess.

Two kinds of test here. The catalogue ones are structural: a key present in one
language and missing in the other is exactly the bug that ships as an English
word in the middle of a Polish menu, and it is invisible until someone switches.
The detection ones pin down the decision itself, which is otherwise only
observable on a machine set to the language you are trying to test.
"""

import unittest

from whisperdictate import i18n


class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.use, i18n.language())

    def test_every_entry_covers_every_language(self):
        for key, entry in i18n.MESSAGES.items():
            for code in i18n.UI_LANGUAGES:
                self.assertIn(code, entry, f"{key} has no {code}")
                self.assertTrue(entry[code].strip(), f"{key}/{code} is blank")

    def test_no_entry_carries_a_language_we_do_not_translate_into(self):
        """A stray "de" would sit there looking translated and never be shown."""
        for key, entry in i18n.MESSAGES.items():
            self.assertEqual(set(entry), set(i18n.UI_LANGUAGES), key)

    def test_placeholders_match_across_languages(self):
        """A translation that drops {count} silently loses the number it names,
        and one that invents a placeholder raises KeyError at the call site."""
        import re

        for key, entry in i18n.MESSAGES.items():
            fields = {
                code: {m.split(":")[0] for m in re.findall(r"\{([^}]*)\}", text)}
                for code, text in entry.items()
            }
            self.assertEqual(
                len(set(map(frozenset, fields.values()))), 1,
                f"{key}: placeholders differ between languages ({fields})",
            )

    def test_polish_ui_text_actually_uses_polish_letters(self):
        """The bug this whole file exists for: 'laduje model' instead of
        'ładuję model'. Not every entry has a diacritic, but the ones checked
        here cannot be spelled correctly without one."""
        must_have_diacritics = (
            "state.loading", "state.transcribing", "state.enhancing", "state.error",
            "menu.dictation_language", "menu.open_config", "menu.quit",
            "menu.enhancement.enable", "menu.vocabulary.edit", "device.default",
        )
        for key in must_have_diacritics:
            text = i18n.MESSAGES[key]["pl"]
            self.assertTrue(
                any(ch in text for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"),
                f"{key}: {text!r} looks like ASCII-folded Polish",
            )


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.use, i18n.language())

    def test_use_returns_the_language_it_activated(self):
        self.assertEqual(i18n.use("pl"), "pl")
        self.assertEqual(i18n.language(), "pl")

    def test_an_untranslated_language_falls_back_instead_of_raising(self):
        """An unreadable menu is a bad reason to refuse to start."""
        self.assertEqual(i18n.use("de"), i18n.FALLBACK)
        self.assertEqual(i18n.use(None), i18n.FALLBACK)

    def test_lookup_follows_the_active_language(self):
        i18n.use("pl")
        self.assertEqual(i18n.t("state.idle"), "Gotowy")
        i18n.use("en")
        self.assertEqual(i18n.t("state.idle"), "Ready")

    def test_placeholders_are_filled(self):
        i18n.use("pl")
        self.assertEqual(i18n.t("menu.api_key", provider="deepseek"), "Klucz API: deepseek...")

    def test_a_field_named_key_does_not_collide_with_the_parameter(self):
        """`key` is positional-only for exactly this call - see t()."""
        i18n.use("en")
        self.assertEqual(i18n.t("menu.hotkey", key="Right Ctrl"), "Hotkey: Right Ctrl")

    def test_a_missing_key_shows_itself_rather_than_vanishing(self):
        with self.assertLogs("whisperdictate.i18n", level="WARNING"):
            self.assertEqual(i18n.t("nope.not.here"), "nope.not.here")


class SystemLanguageTest(unittest.TestCase):
    """resolve_language() is pure, so the interesting cases are all reachable."""

    def test_polish_windows_gives_polish(self):
        self.assertEqual(i18n.resolve_language(0x0415, []), "pl")  # pl-PL

    def test_any_english_sublanguage_gives_english(self):
        self.assertEqual(i18n.resolve_language(0x0409, []), "en")  # en-US
        self.assertEqual(i18n.resolve_language(0x0809, []), "en")  # en-GB

    def test_a_language_we_do_not_speak_falls_back_to_english(self):
        """The user's rule: neither Polish nor English means English."""
        self.assertEqual(i18n.resolve_language(0x0407, []), "en")  # de-DE

    def test_the_display_language_outranks_the_locale(self):
        """English Windows with Polish formats is a deliberate setup, and the
        display language is the one that answers 'what do I read software in'."""
        self.assertEqual(i18n.resolve_language(0x0409, ["pl_PL"]), "en")

    def test_the_locale_is_used_when_there_is_no_display_language(self):
        """Non-Windows, or a runtime without ctypes: the only signal left."""
        self.assertEqual(i18n.resolve_language(None, ["pl_PL"]), "pl")
        self.assertEqual(i18n.resolve_language(None, ["en_GB.UTF-8"]), "en")

    def test_the_windows_shaped_locale_name_is_understood_too(self):
        """setlocale reports "Polish_Poland" where the C library says "pl_PL"."""
        self.assertEqual(i18n.resolve_language(None, ["Polish_Poland"]), "pl")

    def test_the_first_locale_hint_that_we_recognise_wins(self):
        self.assertEqual(i18n.resolve_language(None, ["de_DE", "pl_PL"]), "pl")

    def test_nothing_at_all_falls_back_to_english(self):
        self.assertEqual(i18n.resolve_language(None, []), "en")
        self.assertEqual(i18n.resolve_language(None, ["C", "", "klingon"]), "en")

    def test_detection_on_this_machine_returns_something_usable(self):
        """Whatever this box is set to, the answer has to be a language we have."""
        self.assertIn(i18n.detect_system_language(), i18n.UI_LANGUAGES)


if __name__ == "__main__":
    unittest.main()
