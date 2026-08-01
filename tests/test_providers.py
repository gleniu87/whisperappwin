"""Provider registry, model pairing, and credential isolation.

DeepSeek speaks the Anthropic Messages protocol, so the same client class serves
both — which makes it easy to accidentally send one provider's model name, or
one provider's key, to the other. These cover that seam.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whisperdictate import i18n
from whisperdictate.config import Config
from whisperdictate.controller import DictationController
from whisperdictate.enhance import credentials, providers, registry
from whisperdictate.enhance.providers import ClaudeCliProvider, MessagesApiProvider


class RegistryTest(unittest.TestCase):
    def test_deepseek_uses_the_anthropic_compatible_endpoint(self):
        self.assertEqual(
            registry.spec("deepseek").base_url, "https://api.deepseek.com/anthropic"
        )

    def test_anthropic_uses_the_sdk_default_base_url(self):
        self.assertIsNone(registry.spec("anthropic").base_url)

    def test_providers_have_distinct_env_vars(self):
        env_vars = [s.env_var for s in registry.PROVIDERS.values() if s.env_var]
        self.assertEqual(len(env_vars), len(set(env_vars)))

    def test_cli_provider_needs_no_key(self):
        self.assertIsNone(registry.spec("claude_cli").env_var)

    def test_every_provider_declares_where_traffic_goes(self):
        """Hosting is a decision input, not a footnote — it must never be blank."""
        for key in registry.PROVIDERS:
            self.assertTrue(registry.hosting(key).strip(), key)

    def test_hosting_note_flags_the_jurisdiction_in_both_languages(self):
        """The one fact that must survive translation, because acting on the
        wrong reading of it means sending work material to another country."""
        i18n.use("pl")
        self.assertIn("Chiny", registry.hosting("deepseek"))
        i18n.use("en")
        self.assertIn("China", registry.hosting("deepseek"))

    def test_menu_label_carries_the_product_name_and_the_hint(self):
        i18n.use("pl")
        self.assertEqual(registry.label_with_hint("deepseek"), "DeepSeek API (~10x tańszy)")

    def test_hosting_of_an_unknown_provider_describes_the_fallback(self):
        self.assertEqual(registry.hosting("nonsense"), registry.hosting(registry.DEFAULT_PROVIDER))

    def test_unknown_provider_falls_back_to_the_default(self):
        self.assertEqual(registry.spec("nonsense").key, registry.DEFAULT_PROVIDER)

    def test_model_membership_is_per_provider(self):
        self.assertTrue(registry.supports_model("deepseek", "deepseek-v4-flash"))
        self.assertFalse(registry.supports_model("deepseek", "claude-haiku-4-5"))
        self.assertFalse(registry.supports_model("anthropic", "deepseek-v4-flash"))

    def test_default_model_is_the_first_listed(self):
        self.assertEqual(registry.default_model("deepseek"), "deepseek-v4-flash")
        self.assertEqual(registry.default_model("anthropic"), "claude-haiku-4-5")

    def test_cli_defaults_to_sonnet_because_haiku_times_out_there(self):
        """Measured, not assumed: haiku through the CLI ran 19.8-60+ s over 5
        runs and timed out twice; sonnet stayed at 4.2-5.7 s. Flipping this back
        makes the default CLI model the one that misses the 60 s ceiling."""
        self.assertEqual(registry.default_model("claude_cli"), "claude-sonnet-5")


class ProviderBuildTest(unittest.TestCase):
    def test_builds_a_messages_client_for_deepseek(self):
        provider = providers.build("deepseek")
        self.assertIsInstance(provider, MessagesApiProvider)
        self.assertEqual(provider.name, "deepseek")
        self.assertEqual(provider.spec.base_url, "https://api.deepseek.com/anthropic")

    def test_builds_the_cli_provider(self):
        self.assertIsInstance(providers.build("claude_cli"), ClaudeCliProvider)

    def test_cli_path_override_is_honoured(self):
        self.assertEqual(providers.build("claude_cli", cli_path="C:/x/claude.exe").executable,
                         "C:/x/claude.exe")


class CredentialIsolationTest(unittest.TestCase):
    """Each provider reads its own env var; neither shadows the other."""

    def test_env_vars_are_read_per_provider(self):
        with mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x", "DEEPSEEK_API_KEY": "sk-ds-y"}
        ):
            self.assertEqual(credentials.get_api_key("anthropic"), "sk-ant-x")
            self.assertEqual(credentials.get_api_key("deepseek"), "sk-ds-y")

    def test_anthropic_key_does_not_satisfy_deepseek(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}, clear=False):
            with mock.patch.object(credentials, "_read_credential", lambda _t: None):
                self.assertIsNone(credentials.get_api_key("deepseek"))

    def test_credential_targets_are_distinct(self):
        self.assertNotEqual(
            credentials.target_for("anthropic"), credentials.target_for("deepseek")
        )

    def test_blank_env_var_falls_through_to_the_store(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "   "}):
            with mock.patch.object(credentials, "_read_credential", lambda _t: "stored"):
                self.assertEqual(credentials.get_api_key("deepseek"), "stored")


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _Message:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class EmptyReplyTest(unittest.TestCase):
    """A reply with no text must raise, never return "".

    `output_filter` maps "" to the EMPTY sentinel — "that was only noise, paste
    nothing". A provider that returns "" on failure would therefore delete the
    user's dictation instead of falling back to the raw transcript. Observed
    live: deepseek-v4-pro answered with a lone `thinking` block and
    stop_reason=end_turn, which is a success as far as the API is concerned.
    """

    def _complete(self, message):
        provider = MessagesApiProvider(registry.spec("deepseek"))
        client = mock.Mock()
        client.messages.create.return_value = message
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-ds-x"}):
            with mock.patch("anthropic.Anthropic", return_value=client):
                return provider.complete("sys", "user", "deepseek-v4-pro", 30.0)

    def test_thinking_only_reply_raises(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._complete(_Message([_Block("thinking")]))
        self.assertIn("contains no text", str(caught.exception))

    def test_error_names_the_blocks_that_did_arrive(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._complete(_Message([_Block("thinking")]))
        self.assertIn("thinking", str(caught.exception))

    def test_whitespace_only_text_raises(self):
        with self.assertRaises(providers.ProviderError):
            self._complete(_Message([_Block("text", "   \n  ")]))

    def test_reply_with_no_blocks_at_all_raises(self):
        with self.assertRaises(providers.ProviderError):
            self._complete(_Message([]))

    def test_real_text_alongside_thinking_is_returned(self):
        message = _Message([_Block("thinking", "considering"), _Block("text", "Done.")])
        self.assertEqual(self._complete(message), "Done.")

    def test_literal_empty_sentinel_still_gets_through(self):
        """EMPTY is a real answer; only an absent one is an error."""
        self.assertEqual(self._complete(_Message([_Block("text", "EMPTY")])), "EMPTY")


class ThinkingDisabledTest(unittest.TestCase):
    """DeepSeek thinks unless told not to — measured, not assumed.

    Left on, v4-flash spent its entire token budget reasoning and returned no
    text at all on every call.
    """

    def _request_for(self, model, provider_key="deepseek"):
        provider = MessagesApiProvider(registry.spec(provider_key))
        client = mock.Mock()
        client.messages.create.return_value = _Message([_Block("text", "Done.")])
        env = {"DEEPSEEK_API_KEY": "sk-ds-x", "ANTHROPIC_API_KEY": "sk-ant-x"}
        with mock.patch.dict("os.environ", env):
            with mock.patch("anthropic.Anthropic", return_value=client):
                provider.complete("sys", "user", model, 30.0)
        return client.messages.create.call_args.kwargs

    def test_deepseek_models_ask_for_thinking_off(self):
        for model in registry.spec("deepseek").models:
            self.assertEqual(
                self._request_for(model).get("thinking"), {"type": "disabled"}, model
            )

    def test_haiku_is_left_alone(self):
        """Haiku 4.5 does not think unless asked and rejects the parameter."""
        self.assertNotIn("thinking", self._request_for("claude-haiku-4-5", "anthropic"))


class CliEmptyOutputTest(unittest.TestCase):
    def _run(self, stdout, stderr="", returncode=0):
        completed = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch("subprocess.run", return_value=completed):
            return ClaudeCliProvider("claude").complete("sys", "user", "m", 30.0)

    def test_empty_stdout_raises_instead_of_reading_as_empty_sentinel(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._run("   \n ")
        self.assertIn("empty reply", str(caught.exception))

    def test_stderr_is_quoted_when_there_is_one(self):
        with self.assertRaises(providers.ProviderError) as caught:
            self._run("", stderr="usage limit reached")
        self.assertIn("usage limit reached", str(caught.exception))

    def test_normal_output_is_returned_stripped(self):
        self.assertEqual(self._run("  Done.\n"), "Done.")


class ProviderSwitchTest(unittest.TestCase):
    """Switching provider must carry the model with it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Config.load(Path(self._tmp.name) / "config.toml")
        self.controller = DictationController(
            config=self.config,
            recorder=mock.Mock(is_recording=False, level=0.0),
            transcriber=mock.Mock(model_name="large-v3-turbo"),
            history=mock.Mock(),
            sounds=mock.Mock(),
        )

    def test_switching_to_deepseek_swaps_the_model(self):
        self.assertEqual(self.config.get("enhancement.model"), "claude-haiku-4-5")
        self.controller.set_enhancement_provider("deepseek")
        self.assertEqual(self.config.get("enhancement.model"), "deepseek-v4-flash")

    def test_switching_back_restores_an_anthropic_model(self):
        self.controller.set_enhancement_provider("deepseek")
        self.controller.set_enhancement_provider("anthropic")
        self.assertEqual(self.config.get("enhancement.model"), "claude-haiku-4-5")

    def test_a_valid_model_for_the_new_provider_is_kept(self):
        """claude_cli and anthropic share model names — don't reset needlessly."""
        self.config.set("enhancement.model", "claude-sonnet-5")
        self.controller.set_enhancement_provider("claude_cli")
        self.assertEqual(self.config.get("enhancement.model"), "claude-sonnet-5")

    def test_switch_survives_a_reload(self):
        self.controller.set_enhancement_provider("deepseek")
        reloaded = Config.load(self.config.path)
        self.assertEqual(reloaded.get("enhancement.provider"), "deepseek")
        self.assertEqual(reloaded.get("enhancement.model"), "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
