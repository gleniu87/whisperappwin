"""Config loading, validation and persistence."""

import tempfile
import unittest
from pathlib import Path

from whisperdictate.config import Config


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
        self.assertEqual(cfg.get("hotkey.key"), "alt_r")

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
