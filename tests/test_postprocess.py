"""Transcript clean-up: whitespace, hallucination filter, replacement dictionary."""

import unittest

from whisperdictate.postprocess import apply_replacements, clean, process


class CleanTest(unittest.TestCase):
    def test_trims_and_collapses_spaces(self):
        self.assertEqual(clean("  ala   ma    kota  "), "ala ma kota")

    def test_empty_stays_empty(self):
        self.assertEqual(clean("   "), "")

    def test_drops_amara_hallucination(self):
        self.assertEqual(clean("Napisy stworzone przez społeczność Amara.org"), "")

    def test_drops_english_subtitle_credit(self):
        self.assertEqual(clean("Subtitles by the Amara.org community"), "")

    def test_keeps_legitimate_text_mentioning_napisy(self):
        text = "Napisy do tego filmu trzeba jeszcze poprawić przed publikacją"
        self.assertEqual(clean(text), text)


class ReplacementsTest(unittest.TestCase):
    def test_case_insensitive_substitution(self):
        self.assertEqual(apply_replacements("uzywam kubernetes", {"kubernetes": "Kubernetes"}), "uzywam Kubernetes")

    def test_word_boundaries_protect_substrings(self):
        """A 'ci' -> 'CI' rule must not turn 'ciasto' into 'CIasto'."""
        self.assertEqual(apply_replacements("ciasto w ci", {"ci": "CI"}), "ciasto w CI")

    def test_multi_word_key(self):
        self.assertEqual(
            apply_replacements("pracuje w ajs insure tech", {"ajs insure tech": "ICE InsureTech"}),
            "pracuje w ICE InsureTech",
        )

    def test_replacement_value_is_literal_not_a_regex_template(self):
        """A '\\1' in the replacement must not be interpreted as a backreference."""
        self.assertEqual(apply_replacements("x", {"x": r"\1 raw $0"}), r"\1 raw $0")

    def test_empty_dict_is_a_noop(self):
        self.assertEqual(apply_replacements("bez zmian", {}), "bez zmian")

    def test_empty_key_is_skipped(self):
        self.assertEqual(apply_replacements("bez zmian", {"": "X"}), "bez zmian")


class ProcessTest(unittest.TestCase):
    def test_cleans_then_replaces(self):
        self.assertEqual(process("  uzywam   kubernetes ", {"kubernetes": "Kubernetes"}), "uzywam Kubernetes")

    def test_hallucination_short_circuits_replacements(self):
        self.assertEqual(process("Thanks for watching!", {"x": "y"}), "")


if __name__ == "__main__":
    unittest.main()
