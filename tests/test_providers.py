"""Provider registry, model pairing, and credential isolation.

DeepSeek speaks the Anthropic Messages protocol, so the same client class serves
both — which makes it easy to accidentally send one provider's model name, or
one provider's key, to the other. These cover that seam.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        for key, provider_spec in registry.PROVIDERS.items():
            self.assertTrue(provider_spec.hosting.strip(), key)

    def test_deepseek_hosting_note_flags_the_jurisdiction(self):
        self.assertIn("Chiny", registry.spec("deepseek").hosting)

    def test_unknown_provider_falls_back_to_the_default(self):
        self.assertEqual(registry.spec("nonsense").key, registry.DEFAULT_PROVIDER)

    def test_model_membership_is_per_provider(self):
        self.assertTrue(registry.supports_model("deepseek", "deepseek-v4-flash"))
        self.assertFalse(registry.supports_model("deepseek", "claude-haiku-4-5"))
        self.assertFalse(registry.supports_model("anthropic", "deepseek-v4-flash"))

    def test_default_model_is_the_first_listed(self):
        self.assertEqual(registry.default_model("deepseek"), "deepseek-v4-flash")
        self.assertEqual(registry.default_model("anthropic"), "claude-haiku-4-5")


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
