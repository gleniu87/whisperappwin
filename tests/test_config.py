"""Config loading, validation and persistence."""

import tempfile
import unittest
from pathlib import Path

from unittest import mock

from whisperdictate import config as config_module
from whisperdictate.config import Config
from whisperdictate.i18n import UI_LANGUAGES


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_file_with_defaults_on_first_load(self):
        cfg = Config.load(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(cfg.get("transcription.language"), "pl")
        self.assertEqual(cfg.get("transcription.model"), "large-v3-turbo")
        self.assertEqual(cfg.get("hotkey.key"), "ctrl_r")

    def test_dotted_get_returns_default_for_missing_path(self):
        cfg = Config.load(self.path)
        self.assertIsNone(cfg.get("nope.nothing"))
        self.assertEqual(cfg.get("nope.nothing", "fallback"), "fallback")

    def test_set_persists_across_reload(self):
        Config.load(self.path).set("transcription.language", "en")
        self.assertEqual(Config.load(self.path).get("transcription.language"), "en")

    def test_partial_file_is_merged_onto_defaults(self):
        self.path.write_text('[transcription]\nlanguage = "en"\n', encoding="utf-8")
        cfg = Config.load(self.path)
        self.assertEqual(cfg.get("transcription.language"), "en")
        # Untouched keys in the same section must survive the merge.
        self.assertEqual(cfg.get("transcription.model"), "large-v3-turbo")
        self.assertEqual(cfg.get("hotkey.mode"), "hold")

    def test_invalid_enum_falls_back_to_default(self):
        self.path.write_text('[transcription]\nlanguage = "klingon"\n', encoding="utf-8")
        self.assertEqual(Config.load(self.path).get("transcription.language"), "pl")

    def test_out_of_range_number_is_clamped(self):
        self.path.write_text("[hotkey]\nhold_threshold_ms = 99999\n", encoding="utf-8")
        self.assertEqual(Config.load(self.path).get("hotkey.hold_threshold_ms"), 3000)

    def test_malformed_toml_falls_back_to_defaults(self):
        self.path.write_text("this is not = = toml [[[", encoding="utf-8")
        self.assertEqual(Config.load(self.path).get("transcription.language"), "pl")

    # -- interface language ---------------------------------------------

    def test_first_run_takes_the_interface_language_from_the_system(self):
        with mock.patch.object(config_module, "detect_system_language", return_value="pl"):
            cfg = Config.load(self.path)
        self.assertEqual(cfg.get("ui.language"), "pl")

    def test_a_system_we_do_not_translate_into_lands_on_english(self):
        """detect_system_language() already applies the rule; this pins that the
        config asks it rather than inventing a default of its own."""
        with mock.patch.object(config_module, "detect_system_language", return_value="en"):
            self.assertEqual(Config.load(self.path).get("ui.language"), "en")

    def test_the_detected_language_is_written_to_the_file_not_left_as_a_sentinel(self):
        """So the value in config.toml is always one the user can read and edit,
        and the next version has no sentinel to interpret."""
        with mock.patch.object(config_module, "detect_system_language", return_value="pl"):
            Config.load(self.path)
        self.assertIn('language = "pl"', self.path.read_text(encoding="utf-8"))

    def test_a_chosen_language_is_never_overridden_by_the_system(self):
        self.path.write_text('[ui]\nlanguage = "en"\n', encoding="utf-8")
        with mock.patch.object(config_module, "detect_system_language", return_value="pl"):
            self.assertEqual(Config.load(self.path).get("ui.language"), "en")

    def test_an_older_config_gains_the_setting_on_the_next_start(self):
        """Written down once rather than re-guessed every start, so the file says
        what the app is doing."""
        self.path.write_text('[ui]\noverlay = true\n', encoding="utf-8")
        with mock.patch.object(config_module, "detect_system_language", return_value="pl"):
            Config.load(self.path)
        self.assertIn('language = "pl"', self.path.read_text(encoding="utf-8"))

    def test_a_config_that_already_has_the_setting_is_not_rewritten(self):
        """Nothing to fix means nothing to write - a load must not churn the file."""
        self.path.write_text('[ui]\nlanguage = "en"\n', encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        Config.load(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_nonsense_language_is_replaced_by_the_detected_one(self):
        self.path.write_text('[ui]\nlanguage = "klingon"\n', encoding="utf-8")
        with mock.patch.object(config_module, "detect_system_language", return_value="pl"):
            self.assertEqual(Config.load(self.path).get("ui.language"), "pl")

    def test_the_real_detection_yields_a_language_we_have(self):
        self.assertIn(Config.load(self.path).get("ui.language"), UI_LANGUAGES)

    def test_replacements_roundtrip(self):
        cfg = Config.load(self.path)
        cfg.set("replacements", {"kubernetes": "Kubernetes"})
        self.assertEqual(Config.load(self.path).get("replacements"), {"kubernetes": "Kubernetes"})

    def test_none_values_are_not_written(self):
        """tomli_w cannot serialise None, and audio.device defaults to it."""
        Config.load(self.path)  # must not raise
        written = self.path.read_text(encoding="utf-8")
        audio_section = written.split("[audio]")[1].split("[output]")[0]
        self.assertNotIn("device", audio_section)
        # ...while a sibling key named device in another section is untouched.
        self.assertIn('device = "auto"', written)


if __name__ == "__main__":
    unittest.main()
