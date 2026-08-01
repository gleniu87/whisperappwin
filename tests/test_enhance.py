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

from whisperdictate.config import Config
from whisperdictate.enhance import prompts, service
from whisperdictate.enhance.providers import ProviderError
from whisperdictate.enhance.service import EnhancementService, output_filter


class OutputFilterTest(unittest.TestCase):
    def test_passes_clean_text_through(self):
        self.assertEqual(output_filter("Wyślij to do Marcina."), "Wyślij to do Marcina.")

    def test_strips_thinking_block(self):
        raw = "<thinking>user wants a summary</thinking>\nGotowe."
        self.assertEqual(output_filter(raw), "Gotowe.")

    def test_strips_think_and_reasoning_variants(self):
        self.assertEqual(output_filter("<think>x</think>A"), "A")
        self.assertEqual(output_filter("<reasoning>y</reasoning>B"), "B")

    def test_strips_multiline_thinking(self):
        raw = "<thinking>\nline one\nline two\n</thinking>\n\nWynik."
        self.assertEqual(output_filter(raw), "Wynik.")

    def test_empty_sentinel_becomes_empty_string(self):
        self.assertEqual(output_filter("EMPTY"), "")

    def test_empty_sentinel_tolerates_punctuation_and_quotes(self):
        for variant in ('"EMPTY"', "EMPTY.", " 'EMPTY' "):
            self.assertEqual(output_filter(variant), "", variant)

    def test_text_merely_containing_empty_is_kept(self):
        self.assertEqual(output_filter("Pole EMPTY jest puste"), "Pole EMPTY jest puste")

    def test_unwraps_quotes_around_whole_output(self):
        self.assertEqual(output_filter('"Wyślij to do Marcina."'), "Wyślij to do Marcina.")

    def test_keeps_internal_quotes(self):
        text = 'Powiedział "nie" i wyszedł'
        self.assertEqual(output_filter(text), text)

    def test_does_not_unwrap_when_quotes_are_not_a_pair(self):
        """A sentence that opens with quoted speech and ends with it is not wrapped."""
        text = '"pierwsze" a potem "drugie"'
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
        wrapped = prompts.wrap_transcript("czy mozesz to sprawdzic")
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
        provider = FakeProvider(reply="Gotowe.")
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            EnhancementService(self.config).enhance("no wiec yyy gotowe", "pl")
        self.assertEqual(len(provider.calls), 1)

    def test_only_the_configured_provider_is_built(self):
        built = []

        def spy(name, **kwargs):
            built.append(name)
            return FakeProvider(reply="Gotowe.")

        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-flash")
        with mock.patch.object(service.providers, "build", spy):
            EnhancementService(self.config).enhance("tekst", "pl")
        self.assertEqual(built, ["deepseek"])

    def test_the_configured_model_is_the_one_sent(self):
        provider = FakeProvider(reply="Gotowe.")
        self.config.set("enhancement.provider", "deepseek")
        self.config.set("enhancement.model", "deepseek-v4-pro")
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            EnhancementService(self.config).enhance("tekst", "pl")
        self.assertEqual(provider.calls[0]["model"], "deepseek-v4-pro")

    def test_a_failure_does_not_retry_on_another_model(self):
        """Fail-soft means the raw transcript, not a second opinion."""
        provider = FakeProvider(error=ProviderError("padlo"))
        with mock.patch.object(service.providers, "build", lambda *_a, **_k: provider):
            result = EnhancementService(self.config).enhance("tekst", "pl")
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
        self.assertIsNone(EnhancementService(self.config).enhance("tekst", "pl"))
        self.assertEqual(provider.calls, [])

    # -- happy path ------------------------------------------------------

    def test_returns_cleaned_text(self):
        self.install(FakeProvider(reply="Wyślij to do Marcina."))
        result = EnhancementService(self.config).enhance("no wiec yyy wyslij to do Marcina", "pl")
        self.assertEqual(result.text, "Wyślij to do Marcina.")
        self.assertTrue(result.changed)

    def test_passes_language_lock_and_wrapped_transcript(self):
        provider = self.install(FakeProvider(reply="ok"))
        EnhancementService(self.config).enhance("tekst", "pl")
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
        self.install(FakeProvider(error=ProviderError("brak klucza")))
        self.assertIsNone(EnhancementService(self.config).enhance("tekst", "pl"))

    def test_unexpected_provider_exception_returns_none(self):
        """A provider bug must not cost the user their dictation."""
        self.install(FakeProvider(error=ValueError("boom")))
        self.assertIsNone(EnhancementService(self.config).enhance("tekst", "pl"))

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

    # -- settings --------------------------------------------------------

    def test_unknown_provider_falls_back_to_anthropic(self):
        self.config.set("enhancement.provider", "nonsense", save=False)
        self.assertEqual(EnhancementService(self.config).provider_name, "anthropic")

    def test_describe_reports_off_when_disabled(self):
        self.config.set("enhancement.enabled", False)
        self.assertEqual(EnhancementService(self.config).describe(), "wylaczone")

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
