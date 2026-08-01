"""The proper-noun list, and how it reaches Whisper and the clean-up prompt.

The whole point is that one list feeds two very different consumers, so the
parsing has to survive both: Whisper wants priming prose, the LLM wants an
instruction with a guard against inventing names that were never said.
"""

import unittest

from whisperdictate import vocabulary
from whisperdictate.enhance import prompts


class TermsTest(unittest.TestCase):
    def test_splits_on_commas(self):
        self.assertEqual(vocabulary.terms("DeepSeek, Sonnet"), ("DeepSeek", "Sonnet"))

    def test_keeps_multi_word_names_intact(self):
        """Whitespace is not a separator — "Claude Code" is one name, not two."""
        self.assertEqual(
            vocabulary.terms("Claude Code, ICE InsureTech"),
            ("Claude Code", "ICE InsureTech"),
        )

    def test_collapses_inner_whitespace(self):
        self.assertEqual(vocabulary.terms("Claude   Code"), ("Claude Code",))

    def test_trims_and_drops_blanks(self):
        self.assertEqual(vocabulary.terms("  A ,, B ,  "), ("A", "B"))

    def test_duplicates_collapse_but_order_is_kept(self):
        self.assertEqual(vocabulary.terms("B, A, B"), ("B", "A"))

    def test_empty_and_none_give_nothing(self):
        self.assertEqual(vocabulary.terms(""), ())
        self.assertEqual(vocabulary.terms(None), ())
        self.assertEqual(vocabulary.terms("   "), ())

    def test_case_is_preserved(self):
        """Casing is the point: getUserProfile is not GetUserProfile."""
        self.assertEqual(vocabulary.terms("DeepSeek"), ("DeepSeek",))


class WhisperPrimingTest(unittest.TestCase):
    def test_names_become_a_sentence(self):
        self.assertEqual(vocabulary.whisper_priming("A, B"), "A, B.")

    def test_empty_vocabulary_gives_empty_priming(self):
        self.assertEqual(vocabulary.whisper_priming(""), "")

    def test_existing_initial_prompt_is_kept_and_comes_first(self):
        """initial_prompt is the escape hatch; the list appends to it."""
        result = vocabulary.whisper_priming("DeepSeek", "Mowie o programowaniu.")
        self.assertEqual(result, "Mowie o programowaniu. DeepSeek.")

    def test_initial_prompt_without_punctuation_gets_a_full_stop(self):
        self.assertEqual(vocabulary.whisper_priming("A", "Kontekst"), "Kontekst. A.")

    def test_initial_prompt_alone_still_works(self):
        self.assertEqual(vocabulary.whisper_priming("", "Kontekst."), "Kontekst.")

    def test_both_empty_gives_empty(self):
        self.assertEqual(vocabulary.whisper_priming("", ""), "")


class PromptSectionTest(unittest.TestCase):
    def test_empty_vocabulary_adds_nothing(self):
        self.assertEqual(vocabulary.prompt_section(""), "")

    def test_names_appear_in_the_section(self):
        section = vocabulary.prompt_section("DeepSeek, Claude Code")
        self.assertIn("DeepSeek", section)
        self.assertIn("Claude Code", section)

    def test_section_forbids_inserting_unmentioned_names(self):
        """Without this guard a model starts sprinkling the list into transcripts."""
        self.assertIn("NIE dopisuj", vocabulary.prompt_section("DeepSeek"))


class DetectTest(unittest.TestCase):
    """Finding names the clean-up model repaired on its own.

    Precision matters more than recall here: every hit becomes a question put to
    the user, and a list full of junk is a list nobody reads.
    """

    def found(self, heard, cleaned):
        return [(s.heard, s.corrected) for s in vocabulary.detect(heard, cleaned)]

    def test_finds_a_mangled_name(self):
        self.assertEqual(
            self.found("ustawilem dipsyka wczoraj", "Ustawiłem DeepSeeka wczoraj"),
            [("dipsyka", "DeepSeeka")],
        )

    def test_ignores_diacritic_only_repair(self):
        """"wez" -> "weź" is spelling, not a misheard name."""
        self.assertEqual(self.found("wez to sprawdz", "Weź to sprawdź"), [])

    def test_ignores_capitalisation_only_change(self):
        self.assertEqual(self.found("dobra zrobmy tak", "Dobra zróbmy tak"), [])

    def test_ignores_inflection_of_the_same_stem(self):
        """Real false positive from history: "prawego alta" -> "prawego Altu"."""
        self.assertEqual(self.found("nacisnij prawego alta", "Naciśnij prawego Altu"), [])

    def test_ignores_lowercase_replacements(self):
        """A repair that is not capitalised is not a proper noun."""
        self.assertEqual(self.found("mamy problem tutaj", "mamy kłopot tutaj"), [])

    def test_ignores_short_words(self):
        self.assertEqual(self.found("to jest kot", "to jest Kos"), [])

    def test_ignores_unrelated_words(self):
        """Too dissimilar to be the same word misheard."""
        self.assertEqual(self.found("wyslij wiadomosc", "wyślij Marcinowi"), [])

    def test_ignores_multi_word_rephrasing(self):
        self.assertEqual(self.found("no wiec yyy sprawdz to", "Sprawdź to"), [])

    def test_polish_l_does_not_break_the_alignment(self):
        """U+0142 has no NFD decomposition; unhandled, it desynchronises the
        word alignment and the real repair in the same sentence is missed."""
        self.assertEqual(
            self.found("wlaczylem dipsyka wczoraj", "Włączyłem DeepSeeka wczoraj"),
            [("dipsyka", "DeepSeeka")],
        )

    def test_words_split_by_whisper_are_out_of_scope(self):
        """"get user profile" -> "getUserProfile" is 3:1, not a 1:1 swap.

        Documented rather than fixed: allowing many-to-one would also admit
        every rephrasing, and precision is what keeps the list worth reading.
        """
        self.assertEqual(
            self.found("ten get user profile zwraca null", "ten getUserProfile zwraca null"),
            [],
        )

    def test_handles_missing_text(self):
        self.assertEqual(vocabulary.detect(None, "cos"), [])
        self.assertEqual(vocabulary.detect("cos", None), [])
        self.assertEqual(vocabulary.detect("", ""), [])


class PendingTest(unittest.TestCase):
    ENTRIES = [
        {"raw_text": "ustawilem dipsyka", "text": "Ustawiłem DeepSeeka"},
        {"raw_text": "znowu dipsyka wlaczam", "text": "Znowu DeepSeeka włączam"},
    ]

    def test_duplicates_are_offered_once(self):
        self.assertEqual(len(vocabulary.pending(self.ENTRIES, "", "")), 1)

    def test_known_names_are_not_offered(self):
        self.assertEqual(vocabulary.pending(self.ENTRIES, "DeepSeek", ""), [])

    def test_rejected_names_are_not_offered(self):
        self.assertEqual(vocabulary.pending(self.ENTRIES, "", "DeepSeeka"), [])

    def test_accepting_a_base_form_settles_the_inflected_suggestion(self):
        """Accepting "DeepSeek" must silence "DeepSeeka", not leave it pending."""
        self.assertEqual(vocabulary.pending(self.ENTRIES, "DeepSeek", ""), [])

    def test_entries_without_cleanup_are_skipped(self):
        self.assertEqual(vocabulary.pending([{"text": "Cokolwiek"}], "", ""), [])

    def test_empty_history_gives_nothing(self):
        self.assertEqual(vocabulary.pending([], "", ""), [])


class AddTest(unittest.TestCase):
    def test_appends_to_an_empty_list(self):
        self.assertEqual(vocabulary.add("", "DeepSeek"), "DeepSeek")

    def test_appends_to_an_existing_list(self):
        self.assertEqual(vocabulary.add("A, B", "C"), "A, B, C")

    def test_does_not_duplicate(self):
        self.assertEqual(vocabulary.add("DeepSeek", "DeepSeek"), "DeepSeek")

    def test_duplicate_check_ignores_case_and_diacritics(self):
        self.assertEqual(vocabulary.add("DeepSeek", "deepseek"), "DeepSeek")

    def test_blank_term_changes_nothing(self):
        self.assertEqual(vocabulary.add("A", "   "), "A")


class PromptIntegrationTest(unittest.TestCase):
    def test_vocabulary_reaches_the_built_prompt(self):
        system = prompts.build("default", "pl", "DeepSeek")
        self.assertIn("DeepSeek", system)

    def test_language_lock_stays_last(self):
        """It is appended last on purpose, to outrank everything above it."""
        system = prompts.build("default", "pl", "DeepSeek")
        self.assertLess(system.index("DeepSeek"), system.index("JEZYK:"))

    def test_prompt_is_unchanged_when_no_vocabulary_is_set(self):
        self.assertEqual(prompts.build("default", "pl", ""), prompts.build("default", "pl"))

    def test_vocabulary_applies_to_every_style(self):
        for style in ("default", "chat", "verbatim"):
            self.assertIn("DeepSeek", prompts.build(style, "pl", "DeepSeek"), style)


if __name__ == "__main__":
    unittest.main()
