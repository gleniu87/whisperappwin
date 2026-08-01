"""The proper-noun list, and how it reaches Whisper and the clean-up prompt.

The whole point is that one list feeds two very different consumers, so the
parsing has to survive both: Whisper wants priming prose, the LLM wants an
instruction with a guard against inventing names that were never said.
"""

import tempfile
import unittest
from pathlib import Path

from whisperdictate import vocabulary
from whisperdictate.enhance import prompts


class TermsTest(unittest.TestCase):
    def test_splits_on_commas(self):
        self.assertEqual(vocabulary.terms("DeepSeek, Sonnet"), ("DeepSeek", "Sonnet"))

    def test_keeps_multi_word_names_intact(self):
        """Whitespace is not a separator — "Claude Code" is one name, not two."""
        self.assertEqual(
            vocabulary.terms("Claude Code, Visual Studio"),
            ("Claude Code", "Visual Studio"),
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


class SharedFileTest(unittest.TestCase):
    """The repository half of the vocabulary.

    Split in two on purpose: technical names are worth version-controlling and
    sharing across machines, client and project names are not.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vocabulary.txt"

    def test_file_form_accepts_newlines_as_separators(self):
        self.assertEqual(vocabulary.terms("A\nB, C\nD"), ("A", "B", "C", "D"))

    def test_comments_are_stripped(self):
        raw = "# header\nDeepSeek, Sonnet  # end of line\n# a whole line\nKubernetes"
        self.assertEqual(vocabulary.terms(raw), ("DeepSeek", "Sonnet", "Kubernetes"))

    def test_a_comment_only_file_yields_nothing(self):
        self.assertEqual(vocabulary.terms("# comment only\n\n"), ())

    def test_missing_file_is_not_an_error(self):
        """A vanished shared file must degrade to "no shared terms"."""
        self.assertEqual(vocabulary.read_shared(self.path), "")

    def test_reads_an_existing_file(self):
        self.path.write_text("DeepSeek, Sonnet", encoding="utf-8")
        self.assertEqual(vocabulary.read_shared(self.path), "DeepSeek, Sonnet")

    def test_combined_merges_both_sources(self):
        self.assertEqual(
            vocabulary.combined("Client Smith", "DeepSeek, Sonnet"),
            "DeepSeek, Sonnet, Client Smith",
        )

    def test_combined_deduplicates_across_sources(self):
        self.assertEqual(vocabulary.combined("DeepSeek", "DeepSeek, Sonnet"),
                         "DeepSeek, Sonnet")

    def test_combined_works_with_either_side_empty(self):
        self.assertEqual(vocabulary.combined("", "DeepSeek"), "DeepSeek")
        self.assertEqual(vocabulary.combined("Private", ""), "Private")
        self.assertEqual(vocabulary.combined("", ""), "")

    def test_the_repository_file_exists_and_parses(self):
        """The shipped vocabulary.txt must be readable, not just present."""
        shipped = vocabulary.read_shared()
        self.assertTrue(shipped.strip(), "vocabulary.txt is empty")
        self.assertIn("DeepSeek", vocabulary.terms(shipped))

    def test_the_repository_file_carries_no_private_names(self):
        """Guard against a client name reaching version control by habit."""
        shipped = vocabulary.read_shared().lower()
        for private in ("insuretech", "tomasz", "glen"):
            self.assertNotIn(private, shipped)


class WhisperPrimingTest(unittest.TestCase):
    def test_names_become_a_sentence(self):
        self.assertEqual(vocabulary.whisper_priming("A, B"), "A, B.")

    def test_empty_vocabulary_gives_empty_priming(self):
        self.assertEqual(vocabulary.whisper_priming(""), "")

    def test_existing_initial_prompt_is_kept_and_comes_first(self):
        """initial_prompt is the escape hatch; the list appends to it."""
        result = vocabulary.whisper_priming("DeepSeek", "I am talking about programming.")
        self.assertEqual(result, "I am talking about programming. DeepSeek.")

    def test_initial_prompt_without_punctuation_gets_a_full_stop(self):
        self.assertEqual(vocabulary.whisper_priming("A", "Context"), "Context. A.")

    def test_initial_prompt_alone_still_works(self):
        self.assertEqual(vocabulary.whisper_priming("", "Context."), "Context.")

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

    def test_section_keeps_the_polish_declension_cue(self):
        """Looks like a bug for English dictation. Measured, removing it is worse.

        Rewriting this language-neutral dropped Polish from 14/16 to 9/16 on
        restoring "Sonnet" (deepseek-v4-flash), while English stayed 8/8 either
        way - the language lock appended later already handles English. Do not
        "fix" this without re-running that comparison.
        """
        self.assertIn("po polsku", vocabulary.prompt_section("DeepSeek"))


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
        self.assertEqual(vocabulary.pending([{"text": "Anything"}], "", ""), [])

    def test_empty_history_gives_nothing(self):
        self.assertEqual(vocabulary.pending([], "", ""), [])


class BaseFormTest(unittest.TestCase):
    """Guessing the nominative from whatever form the sentence needed.

    A guess, not a rule - the dialog keeps the field editable and shows the
    original. These pin the common Polish case endings on borrowed nouns.
    """

    def test_strips_common_case_endings(self):
        for inflected, expected in (
            ("Anthropica", "Anthropic"),
            ("DeepSeeka", "DeepSeek"),
            ("DeepSeekowi", "DeepSeek"),
            ("DeepSeekiem", "DeepSeek"),
            ("DeepSeeku", "DeepSeek"),
            ("Sonneta", "Sonnet"),
        ):
            self.assertEqual(vocabulary.base_form(inflected), expected, inflected)

    def test_leaves_uninflected_names_alone(self):
        for name in ("Kubernetes", "Docker", "Redis", "GitHub", "PostgreSQL"):
            self.assertEqual(vocabulary.base_form(name), name)

    def test_refuses_to_shorten_below_a_usable_stem(self):
        """"Java" -> "Jav" is the failure this guard prevents."""
        self.assertEqual(vocabulary.base_form("Java"), "Java")

    def test_multi_word_names_are_left_alone(self):
        self.assertEqual(vocabulary.base_form("Claude Code"), "Claude Code")

    def test_handles_blank_input(self):
        self.assertEqual(vocabulary.base_form(""), "")


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

    def test_inflected_form_is_not_added_next_to_the_stem(self):
        """The real bug: accepting a suggestion left Anthropic AND Anthropica."""
        self.assertEqual(vocabulary.add("Anthropic", "Anthropica"), "Anthropic")

    def test_stem_supersedes_an_inflected_entry_in_place(self):
        self.assertEqual(
            vocabulary.add("Sonnet, Anthropica, Docker", "Anthropic"),
            "Sonnet, Anthropic, Docker",
        )

    def test_a_longer_name_sharing_a_prefix_is_still_added(self):
        """"Claude Code" is not an inflection of "Claude" - the space says so."""
        self.assertEqual(vocabulary.add("Claude", "Claude Code"), "Claude, Claude Code")

    def test_an_unrelated_name_sharing_a_prefix_is_still_added(self):
        """Four characters of suffix is a different word, not an ending."""
        self.assertEqual(vocabulary.add("Post", "PostgreSQL"), "Post, PostgreSQL")

    def test_combining_the_two_sources_collapses_the_inflected_entry(self):
        """Shared file has the stem, config has the inflected form: one survives."""
        self.assertEqual(vocabulary.combined("Anthropica", "Anthropic"), "Anthropic")


class RemoveTest(unittest.TestCase):
    """Whatever `add` let in, `remove` has to be able to take out - the dialog
    offers a Remove button next to a name it just displayed, and a button that
    does nothing on some names is worse than no button."""

    def test_removes_a_name(self):
        self.assertEqual(vocabulary.remove("A, DeepSeek, B", "DeepSeek"), "A, B")

    def test_removes_the_only_name(self):
        self.assertEqual(vocabulary.remove("DeepSeek", "DeepSeek"), "")

    def test_matching_ignores_case_and_diacritics(self):
        self.assertEqual(vocabulary.remove("Zażółć, B", "zazolc"), "B")

    def test_an_absent_name_changes_nothing(self):
        """The caller is a dialog, not a transaction. Nothing to raise about."""
        self.assertEqual(vocabulary.remove("A, B", "C"), "A, B")

    def test_blank_term_changes_nothing(self):
        self.assertEqual(vocabulary.remove("A, B", "  "), "A, B")

    def test_removing_the_stem_takes_its_inflections_too(self):
        """add() folds "Anthropica" onto "Anthropic", so removing the stem must
        not leave a copy of the inflected form behind to be re-primed."""
        self.assertEqual(vocabulary.remove("Anthropic, Anthropica, Docker", "Anthropic"), "Docker")

    def test_a_longer_name_sharing_a_prefix_survives(self):
        """The mirror of add(): "Claude Code" is a different name, not an ending."""
        self.assertEqual(vocabulary.remove("Claude, Claude Code", "Claude"), "Claude Code")

    def test_an_unrelated_name_sharing_a_prefix_survives(self):
        self.assertEqual(vocabulary.remove("Post, PostgreSQL", "Post"), "PostgreSQL")

    def test_removing_from_the_private_list_cannot_touch_the_shared_one(self):
        """Only the config half is editable from the tray; vocabulary.txt is
        version-controlled and a dialog must not produce a git diff."""
        self.assertEqual(vocabulary.remove("Private", "Anthropic"), "Private")
        self.assertIn("Anthropic", vocabulary.terms(vocabulary.read_shared()))


class ControllerEditingTest(unittest.TestCase):
    """Adding and removing through the controller, which is what the dialog does.

    The link worth testing is the re-priming: an entry removed from the config but
    still sitting in the loaded transcriber's initial_prompt would keep steering
    Whisper towards a name the user just deleted, until the next restart.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from whisperdictate.config import Config
        from whisperdictate.controller import DictationController

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        self.transcriber = mock.Mock(model_name="large-v3-turbo")
        self.controller = DictationController(
            config=self.config,
            recorder=mock.Mock(is_recording=False, level=0.0),
            transcriber=self.transcriber,
            # A real list, not a bare Mock: editing the vocabulary rescans history
            # for suggestions, and a Mock that swallowed the scan would make these
            # tests pass without exercising it.
            history=mock.Mock(recent=lambda _count: []),
            sounds=mock.Mock(),
        )

    def test_adding_one_name_at_a_time(self):
        self.controller.accept_vocabulary("Iceberg")
        self.controller.accept_vocabulary("ICE Insurance")
        self.assertEqual(
            vocabulary.terms(self.config.get("transcription.vocabulary")),
            ("Iceberg", "ICE Insurance"),
        )

    def test_removing_a_name(self):
        self.controller.accept_vocabulary("Iceberg")
        self.controller.accept_vocabulary("ICE Insurance")
        self.controller.remove_vocabulary("Iceberg")
        self.assertEqual(
            vocabulary.terms(self.config.get("transcription.vocabulary")), ("ICE Insurance",)
        )

    def test_removal_re_primes_the_loaded_model(self):
        self.controller.accept_vocabulary("Iceberg")
        self.assertIn("Iceberg", self.transcriber.initial_prompt)
        self.controller.remove_vocabulary("Iceberg")
        self.assertNotIn("Iceberg", self.transcriber.initial_prompt or "")

    def test_removal_leaves_the_shared_names_primed(self):
        """Only the private half is being edited; the shared file still applies."""
        self.controller.accept_vocabulary("Iceberg")
        self.controller.remove_vocabulary("Iceberg")
        self.assertIn("DeepSeek", self.transcriber.initial_prompt)


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
