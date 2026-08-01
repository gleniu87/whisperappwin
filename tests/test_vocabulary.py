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
