"""Transcript clean-up layer: output filtering, fail-soft behaviour, prompts.

No network. The provider is substituted throughout — these cover the wiring and
the guardrails, which is where a clean-up layer actually goes wrong: swallowing
a dictation on an API error, pasting an LLM's answer instead of the transcript,
or leaking reasoning tags into the paste.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whisperdictate import i18n
from whisperdictate.config import Config
from whisperdictate.enhance import prompts, service
from whisperdictate.enhance.providers import ProviderError
from whisperdictate.enhance.service import EnhancementService, output_filter


class OutputFilterTest(unittest.TestCase):
    def test_passes_clean_text_through(self):
        self.assertEqual(output_filter("Send it to Martin."), "Send it to Martin.")

    def test_strips_thinking_block(self):
        raw = "<thinking>user wants a summary</thinking>\nDone."
        self.assertEqual(output_filter(raw), "Done.")

    def test_strips_think_and_reasoning_variants(self):
        self.assertEqual(output_filter("<think>x</think>A"), "A")
        self.assertEqual(output_filter("<reasoning>y</reasoning>B"), "B")

    def test_strips_multiline_thinking(self):
        raw = "<thinking>\nline one\nline two\n</thinking>\n\nResult."
        self.assertEqual(output_filter(raw), "Result.")

    def test_empty_sentinel_becomes_empty_string(self):
        self.assertEqual(output_filter("EMPTY"), "")

    def test_empty_sentinel_tolerates_punctuation_and_quotes(self):
        for variant in ('"EMPTY"', "EMPTY.", " 'EMPTY' "):
            self.assertEqual(output_filter(variant), "", variant)

    def test_text_merely_containing_empty_is_kept(self):
        self.assertEqual(output_filter("The EMPTY field is blank"), "The EMPTY field is blank")

    def test_unwraps_quotes_around_whole_output(self):
        self.assertEqual(output_filter('"Send it to Martin."'), "Send it to Martin.")

    def test_keeps_internal_quotes(self):
        text = 'He said "no" and left'
        self.assertEqual(output_filter(text), text)

    def test_does_not_unwrap_when_quotes_are_not_a_pair(self):
        """A sentence that opens with quoted speech and ends with it is not wrapped."""
        text = '"first" and then "second"'
        self.assertEqual(output_filter(text), text)


class PromptTest(unittest.TestCase):
    def test_default_prompt_lists_polish_fillers(self):
        """An English-only prompt cleans 'um' and leaves 'no więc yyy' untouched."""
        for filler in ("yyy", "no wiec", "jakby", "w sensie"):
            self.assertIn(filler, prompts.DEFAULT)

    def test_default_prompt_defines_the_empty_sentinel(self):
        self.assertIn("EMPTY", prompts.DEFAULT)

    def test_polish_language_lock_is_appended_last(self):
        built = prompts.build("default", "pl")
        self.assertTrue(built.startswith(prompts.DEFAULT))
        self.assertIn("JEZYK", built)

    def test_auto_language_adds_no_lock(self):
        self.assertEqual(prompts.build("default", "auto"), prompts.DEFAULT)

    def test_verbatim_prompt_preserves_fillers(self):
        self.assertIn("NIE usuwaj przerywnikow", prompts.VERBATIM)

    def test_unknown_style_falls_back_to_default(self):
        self.assertEqual(prompts.build("nonexistent", None), prompts.DEFAULT)

    def test_transcript_is_wrapped_in_a_tag(self):
        """The tag is what stops the model answering a transcript that is a question."""
        wrapped = prompts.wrap_transcript("can you check this")
        self.assertTrue(wrapped.startswith("<TRANSCRIPT>"))
        self.assertTrue(wrapped.endswith("</TRANSCRIPT>"))


class FakeProvider:
    name = "fake"

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system, user, model, timeout):
        self.calls.append({"system": system, "user": user, "model": model, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.reply

    def check(self):
        return None


class OneDictationOneCallTest(unittest.TestCase):
    """A dictation goes to exactly one model: the configured one.

    The comparison commands (`--benchmark`, `--quality`) do fan out across every
    ready provider, and that is the point of them — but they are one-shot CLI
    entry points that exit before the app starts. Nothing on the dictation path
    may ever poll several models and pick a winner: that would burn every
    provider's quota per dictation and make the chosen model meaningless.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        self.config.set("enhancement.enabled", True)

    def test_one_enhance_makes_exactly_one_provider_call(self):
        provider = FakeProvider(reply="Done.")
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            EnhancementService(self.config).enhance("so um done", "pl")
        self.assertEqual(len(provider.calls), 1)

    def test_only_the_configured_provider_is_built(self):
        built = []

        def spy(name, **kwargs):
            built.append(name)
            return FakeProvider(reply="Done.")

        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-flash")
        with mock.patch.object(service.providers, "build", spy):
            EnhancementService(self.config).enhance("text", "pl")
        self.assertEqual(built, ["deepseek"])

    def test_the_configured_model_is_the_one_sent(self):
        provider = FakeProvider(reply="Done.")
        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-pro")
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            EnhancementService(self.config).enhance("text", "pl")
        self.assertEqual(provider.calls[0]["model"], "deepseek-v4-pro")

    def test_a_failure_does_not_retry_on_another_model(self):
        """Fail-soft means the raw transcript, not a second opinion."""
        provider = FakeProvider(error=ProviderError("it broke"))
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            result = EnhancementService(self.config).enhance("text", "pl")
        self.assertIsNone(result)
        self.assertEqual(len(provider.calls), 1)

    def test_the_dictation_path_does_not_reference_the_case_set(self):
        """The case set is a CLI tool; the runtime must not reach for it.

        Checked against the source, not `hasattr` on the package — importing
        `quality` anywhere in the test run sets that attribute, so the runtime
        check would pass or fail depending on test order.
        """
        from pathlib import Path as _Path

        import whisperdictate.controller
        import whisperdictate.enhance.service

        for module in (whisperdictate.enhance.service, whisperdictate.controller):
            source = _Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("quality", source, module.__name__)

    def test_quality_is_not_exported_from_the_package(self):
        from whisperdictate import enhance as enhance_pkg

        self.assertNotIn("quality", enhance_pkg.__all__)


class EnhancementServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        self.config.set("enhancement.enabled", True)

    def install(self, provider):
        patcher = mock.patch.object(service.providers, "build", lambda *_a, **_k: provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider

    # -- the off switch --------------------------------------------------

    def test_disabled_by_default(self):
        fresh = Config.load(Path(self._tmp.name) / "fresh.toml")
        self.assertFalse(EnhancementService(fresh).enabled)

    def test_disabled_service_never_calls_the_provider(self):
        provider = self.install(FakeProvider(reply="cleaned"))
        self.config.set("enhancement.enabled", False)
        self.assertIsNone(EnhancementService(self.config).enhance("text", "pl"))
        self.assertEqual(provider.calls, [])

    # -- happy path ------------------------------------------------------

    def test_returns_cleaned_text(self):
        self.install(FakeProvider(reply="Send it to Martin."))
        result = EnhancementService(self.config).enhance("no wiec yyy wyslij to do Marcina", "pl")
        self.assertEqual(result.text, "Send it to Martin.")
        self.assertTrue(result.changed)

    def test_passes_language_lock_and_wrapped_transcript(self):
        provider = self.install(FakeProvider(reply="ok"))
        EnhancementService(self.config).enhance("text", "pl")
        call = provider.calls[0]
        self.assertIn("JEZYK", call["system"])
        self.assertIn("<TRANSCRIPT>", call["user"])
        self.assertEqual(call["model"], "claude-haiku-4-5")

    def test_empty_sentinel_yields_empty_text_not_none(self):
        """Empty means 'paste nothing'; None means 'paste the raw transcript'."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance("yyy eee", "pl")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    # -- fail-soft -------------------------------------------------------

    def test_provider_error_returns_none(self):
        self.install(FakeProvider(error=ProviderError("no API key")))
        self.assertIsNone(EnhancementService(self.config).enhance("text", "pl"))

    def test_unexpected_provider_exception_returns_none(self):
        """A provider bug must not cost the user their dictation."""
        self.install(FakeProvider(error=ValueError("boom")))
        self.assertIsNone(EnhancementService(self.config).enhance("text", "pl"))

    def test_blank_input_is_not_sent(self):
        provider = self.install(FakeProvider(reply="x"))
        self.assertIsNone(EnhancementService(self.config).enhance("   ", "pl"))
        self.assertEqual(provider.calls, [])

    # -- runaway guard ---------------------------------------------------

    def test_answer_instead_of_cleanup_is_rejected(self):
        """If the model answers the transcript, paste the transcript, not the answer."""
        self.install(FakeProvider(reply="Oto szczegolowa odpowiedz. " * 60))
        self.assertIsNone(
            EnhancementService(self.config).enhance("jak dziala fotosynteza", "pl")
        )

    def test_long_dictation_cleaned_to_similar_length_is_kept(self):
        original = "no wiec " * 200
        self.install(FakeProvider(reply="w porzadku " * 150))
        self.assertIsNotNone(EnhancementService(self.config).enhance(original, "pl"))

    def test_short_expansion_is_not_treated_as_runaway(self):
        """Adding punctuation and diacritics can lengthen a short transcript."""
        self.install(FakeProvider(reply="Dobrze, zrobię to jutro rano."))
        self.assertIsNotNone(EnhancementService(self.config).enhance("dobrze", "pl"))

    # -- EMPTY guard -----------------------------------------------------
    #
    # The mirror image of the runaway guard: that one catches a reply too long
    # to be a clean-up, this one a reply too short. Honouring EMPTY is the only
    # irreversible outcome in the pipeline - controller.py pastes nothing *and*
    # returns before history.append, so nothing survives anywhere.
    #
    # Measured on 76 real transcripts: DeepSeek said EMPTY 0 times in 68 history
    # entries, so this changes nothing for it. qwen3.5:9b lost 1 of 76 and
    # qwen3.5:4b lost 3, one of them the command asserted below.

    def test_empty_on_a_substantive_transcript_pastes_the_raw_text(self):
        """Measured casualty: qwen3.5:4b answered EMPTY to this exact command.

        None means "paste the raw transcript", which is the only outcome here
        that does not destroy the dictation.
        """
        self.install(FakeProvider(reply="EMPTY"))
        self.assertIsNone(
            EnhancementService(self.config).enhance("Zaproponuj następne zadania", "pl")
        )

    def test_empty_on_pure_filler_is_still_honoured(self):
        """The sentinel keeps working where it is right - nothing regresses for
        the providers that only ever fire it on noise."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance("yyy eee no wiec yyy", "pl")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    def test_empty_on_a_whisper_hallucination_is_still_honoured(self):
        """44 characters and not one filler among them, so a length rule alone
        would reject it - and it is an expect_empty entry in the fixed case set,
        where a wrong verdict here would fail every provider at once."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance(
            "Napisy stworzone przez społeczność Amara.org", "pl"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    def test_empty_on_polish_fillers_with_diacritics_is_honoured(self):
        """The filler list is written the way the prompt writes it, without
        diacritics; Whisper produces them. Both sides are folded, so "no więc"
        has to match "no wiec"."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance("no więc yyy", "pl")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    def test_empty_on_a_very_short_transcript_is_honoured(self):
        """Under the backstop nothing distinguishes a word from a cough. The
        accepted residual loss: qwen3.5:9b discarded "Voilà." once in 76."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance("Voilà.", "pl")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    def test_a_whitespace_only_reply_is_guarded_the_same_way(self):
        """A provider that answers with blanks must not be able to delete a
        dictation either - output_filter cannot tell that from the sentinel."""
        self.install(FakeProvider(reply="   \n  "))
        self.assertIsNone(
            EnhancementService(self.config).enhance("Zaproponuj następne zadania", "pl")
        )

    def test_the_guard_names_the_transcript_it_rescued(self):
        """The log line is the recovery path, so it has to carry the text."""
        self.install(FakeProvider(reply="EMPTY"))
        with self.assertLogs("whisperdictate.enhance.service", level="WARNING") as caught:
            EnhancementService(self.config).enhance("Zaproponuj następne zadania", "pl")
        self.assertIn("Zaproponuj następne zadania", "\n".join(caught.output))

    def test_every_filler_the_guard_trusts_appears_in_the_prompt(self):
        """Anti-drift: the guard's list came from the prompt's own filler rules.

        If a word is only in one of the two places, either the model is told to
        strip something the guard will not forgive, or the guard forgives
        something the model was never asked to strip.
        """
        import re

        from whisperdictate.vocabulary import fold

        # Word boundaries, not substrings. `assertIn` passed for "po" because it
        # occurs inside "poprawnie", so the guard could have kept forgiving a word
        # the prompt no longer mentions while this test stayed green - the exact
        # drift it exists to catch. Same for "no", "w", "of" and "like".
        prompt_text = fold(prompts.DEFAULT)
        for filler in service._FILLER_WORDS:
            self.assertRegex(prompt_text, rf"\b{re.escape(filler)}\b", filler)

    def test_every_expect_empty_case_still_satisfies_the_guard(self):
        """If the guard stopped calling these plausibly empty, `enhance()` would
        return None for them, `cmd_quality` would read that as "the provider did
        not answer", and the case set would report CRASH for every provider at
        once - blaming the models for a change made here."""
        from whisperdictate.enhance import quality

        for case in quality.CASES:
            if case.expect_empty:
                self.assertTrue(service._plausibly_empty(case.text), case.name)

    def test_empty_is_not_honoured_when_the_transcript_carries_digits(self):
        """A number is content, and the filler rule cannot see it.

        `_WORD_TOKEN` matches letters only, so every *word* of
        "no wiec 2137 yyy 1410 eee" is a filler and the guard would call the whole
        thing plausibly empty - throwing away the only part that meant anything.
        Dictated times, amounts, phone numbers and codes arrive in exactly this
        shape, wrapped in hesitation, and they are the least reconstructible
        thing a user can lose.
        """
        self.install(FakeProvider(reply="EMPTY"))
        for transcript in ("no wiec 2137 yyy 1410 eee", "um like 555 0199 you know"):
            self.assertIsNone(
                EnhancementService(self.config).enhance(transcript, "pl"), transcript
            )

    def test_a_reasoning_only_reply_never_counts_as_the_sentinel(self):
        """The failure that made the guard insufficient on its own.

        llama.cpp and LM Studio - the servers `enhancement.base_url` exists to
        support - put the reasoning block inside `content`, so the provider's
        empty-content check cannot see it. `output_filter` then strips it to "",
        which used to be indistinguishable from EMPTY: every short dictation on
        such a server was dropped. Measured with "dzień dobry" (11 characters,
        inside the backstop, so the guard alone would have honoured it).
        """
        self.install(FakeProvider(reply="<think>just a greeting, nothing to clean</think>"))
        self.assertIsNone(EnhancementService(self.config).enhance("dzień dobry", "pl"))

    def test_the_short_transcript_backstop_sits_where_the_comment_says(self):
        """Pinned so the constant cannot drift up into real commands: "Zamknij
        okno" is 12 characters and "Odpal testy" 11, both already inside the
        window, and 27 is where the protected "Zaproponuj następne zadania" sits.
        """
        self.assertTrue(service._plausibly_empty("a" * service._EMPTY_MAX_CHARS))
        self.assertFalse(service._plausibly_empty("a" * (service._EMPTY_MAX_CHARS + 1)))
        self.assertLess(service._EMPTY_MAX_CHARS, len("Zaproponuj następne zadania"))

    def test_a_polish_hallucination_with_diacritics_is_still_honoured(self):
        """The patterns are written without diacritics and Whisper emits them, so
        matching had to fold first. Unfolded, "Dziękuję za uwagę" missed every
        pattern, the guard refused EMPTY, and the artefact was pasted into
        whatever had focus - a regression the guard itself introduced."""
        self.install(FakeProvider(reply="EMPTY"))
        result = EnhancementService(self.config).enhance("Dziękuję za uwagę.", "pl")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "")

    def test_the_recorded_model_is_the_one_that_saw_the_text(self):
        """`self.model` re-reads the live config, which the tray writes from
        another thread. Reading it again when building the result let a menu click
        during a 3-6 s local clean-up write a model into history that never saw
        the transcript - and history is the dataset the model choice was measured
        from, so it has to stay truthful."""
        service_under_test = EnhancementService(self.config)

        class SwitchesModelMidCall(FakeProvider):
            def complete(self, system, user, model, timeout):  # noqa: ANN001
                service_under_test.config.set("enhancement.model", "qwen3.5:9b", save=False)
                return super().complete(system, user, model, timeout)

        provider = self.install(SwitchesModelMidCall(reply="Cleaned."))
        result = service_under_test.enhance("no wiec yyy zrob to", "pl")
        self.assertEqual(result.model, provider.calls[0]["model"])

    # -- settings --------------------------------------------------------

    def test_unknown_provider_falls_back_to_anthropic(self):
        self.config.set("enhancement.provider", "nonsense", save=False)
        self.assertEqual(EnhancementService(self.config).provider_name, "anthropic")

    def test_describe_reports_off_when_disabled(self):
        i18n.use("pl")
        self.config.set("enhancement.enabled", False)
        self.assertEqual(EnhancementService(self.config).describe(), "wyłączone")

    def test_settings_are_read_per_call_not_cached(self):
        """A tray change must apply to the next dictation without a restart."""
        provider = self.install(FakeProvider(reply="ok"))
        svc = EnhancementService(self.config)
        svc.enhance("a", "pl")
        self.config.set("enhancement.model", "claude-sonnet-5")
        svc.enhance("b", "pl")
        self.assertEqual([c["model"] for c in provider.calls],
                         ["claude-haiku-4-5", "claude-sonnet-5"])


if __name__ == "__main__":
    unittest.main()
