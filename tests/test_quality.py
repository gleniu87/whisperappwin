"""The quality checker itself — a false verdict here is worse than no checker.

`check()` decides whether a provider mangled a transcript, so its own failure
modes matter: a substring match would flag "no" inside "nowy" and quietly fail a
provider that did nothing wrong.
"""

import unittest

from whisperdictate.enhance import quality
from whisperdictate.enhance.quality import Case, check


class CheckTest(unittest.TestCase):
    def test_clean_result_has_no_violations(self):
        case = Case(name="x", text="raw", must_keep=("user_id",), must_drop=("yyy",))
        self.assertEqual(check(case, "Sprawdź `user_id` na pustym stringu."), [])

    def test_missing_identifier_is_flagged(self):
        case = Case(name="x", text="raw", must_keep=("user_id",))
        self.assertIn("zgubiono 'user_id'", check(case, "Sprawdź parser user id."))

    def test_identifier_match_is_case_sensitive(self):
        """getUserProfile rewritten as GetUserProfile is still a mangled name."""
        case = Case(name="x", text="raw", must_keep=("getUserProfile",))
        self.assertTrue(check(case, "Popraw GetUserProfile."))

    def test_leftover_filler_is_flagged(self):
        case = Case(name="x", text="raw", must_drop=("jakby",))
        self.assertIn("zostawiono 'jakby'", check(case, "To jakby działa."))

    def test_filler_is_matched_on_word_boundaries(self):
        """'no' must not match inside 'nowy' — that would fail a clean result."""
        case = Case(name="x", text="raw", must_drop=("no",))
        self.assertEqual(check(case, "Zrobiłem nowy parser."), [])

    def test_filler_check_ignores_case(self):
        case = Case(name="x", text="raw", must_drop=("yyy",))
        self.assertTrue(check(case, "YYY, sprawdź to."))

    def test_expected_empty_accepts_empty_string(self):
        case = Case(name="x", text="raw", expect_empty=True)
        self.assertEqual(check(case, ""), [])

    def test_expected_empty_rejects_text(self):
        case = Case(name="x", text="raw", expect_empty=True)
        self.assertTrue(check(case, "Napisy stworzone przez społeczność."))

    def test_unexpected_empty_is_flagged_as_a_lost_dictation(self):
        case = Case(name="x", text="raw")
        self.assertIn("pusty wynik - tekst zniknalby przy wklejaniu", check(case, ""))

    def test_provider_failure_is_reported_not_swallowed(self):
        case = Case(name="x", text="raw")
        self.assertEqual(check(case, None), ["provider nie odpowiedzial"])

    def test_answering_instead_of_cleaning_is_flagged(self):
        case = Case(name="x", text="raw", must_not_contain=("def ",))
        self.assertTrue(check(case, "def sortuj(xs): return sorted(xs)"))

    def test_violations_accumulate(self):
        case = Case(name="x", text="raw", must_keep=("user_id",), must_drop=("yyy",))
        self.assertEqual(len(check(case, "yyy sprawdź parser")), 2)


class CaseResultTest(unittest.TestCase):
    def test_failed_is_a_bool_not_the_violation_list(self):
        """The summary does sum(r.failed ...) — a list here is a TypeError."""
        case = Case(name="x", text="raw", must_keep=("user_id",))
        result = quality.CaseResult(case, "brak", 1.0, check(case, "brak"))
        self.assertIsInstance(result.failed, bool)
        self.assertTrue(result.failed)
        self.assertEqual(sum(r.failed for r in [result]), 1)

    def test_clean_result_is_not_failed(self):
        result = quality.CaseResult(Case(name="x", text="raw"), "Gotowe.", 1.0, [])
        self.assertFalse(result.failed)
        self.assertEqual(result.status, "OK")

    def test_provider_failure_reads_as_awaria(self):
        result = quality.CaseResult(Case(name="x", text="raw"), None, 1.0, ["x"])
        self.assertEqual(result.status, "AWARIA")


class ContrastCaseTest(unittest.TestCase):
    """Regression: this case used to fail correct output.

    A checker that flags a good answer is worse than no checker — it would push
    the next person to "fix" a prompt that was already right.
    """

    def _case(self):
        return next(c for c in quality.CASES if c.name == "nie-jako-przeczenie")

    def test_keeping_the_contrast_passes(self):
        good = "Spotkanie jest we wtorek, nie w środę, i to nie jest problem dla mnie."
        self.assertEqual(check(self._case(), good), [])

    def test_dropping_the_first_day_still_fails(self):
        """The real failure mode: treating "nie" as a self-correction."""
        bad = "Spotkanie jest w środę i to nie jest problem dla mnie."
        self.assertTrue(check(self._case(), bad))


class CaseSetTest(unittest.TestCase):
    def test_case_names_are_unique(self):
        names = [c.name for c in quality.CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_case_explains_itself(self):
        """The `why` is what makes a failure actionable rather than mysterious."""
        for case in quality.CASES:
            self.assertTrue(case.why.strip(), case.name)

    def test_every_case_asserts_something(self):
        for case in quality.CASES:
            asserts = case.must_keep or case.must_drop or case.must_not_contain
            self.assertTrue(asserts or case.expect_empty, case.name)

    def test_must_keep_strings_are_present_in_the_input(self):
        """A must_keep the transcript never contained would fail every provider."""
        for case in quality.CASES:
            for needle in case.must_keep:
                if needle.startswith("--"):
                    continue  # spoken as words: "flaga minus minus check"
                self.assertIn(needle.lower(), case.text.lower(), f"{case.name}: {needle}")

    def test_must_drop_words_are_present_in_the_input(self):
        for case in quality.CASES:
            for word in case.must_drop:
                self.assertIn(word.lower(), case.text.lower(), f"{case.name}: {word}")

    def test_empty_cases_assert_nothing_else(self):
        """expect_empty short-circuits the other checks; mixing them hides bugs."""
        for case in quality.CASES:
            if case.expect_empty:
                self.assertFalse(case.must_keep or case.must_drop, case.name)


if __name__ == "__main__":
    unittest.main()
