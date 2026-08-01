"""Credential Manager round-trip.

An integration test on purpose. The bug this exists to prevent — passing bytes
to CredWrite, which only accepts str — cannot be caught by mocking pywin32,
because a mock accepts whatever you hand it. The asymmetry is the trap:
CredWrite takes str and encodes internally; CredRead returns bytes.

Uses a probe-only target name and always cleans up, so it never touches the
entries the app actually uses.
"""

import unittest

from whisperdictate.enhance import credentials

PROBE_PROVIDER = "__whisperdictate_test__"

try:
    import win32cred  # noqa: F401

    HAVE_PYWIN32 = True
except ImportError:  # pragma: no cover
    HAVE_PYWIN32 = False


@unittest.skipUnless(HAVE_PYWIN32, "pywin32 niedostepny")
class CredentialRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self._cleanup)
        self._cleanup()

    @staticmethod
    def _cleanup():
        try:
            credentials.delete_api_key(PROBE_PROVIDER)
        except Exception:  # noqa: BLE001 - nothing to remove is fine
            pass

    def read_back(self):
        """Bypass get_api_key so a real ANTHROPIC_API_KEY cannot mask the result."""
        return credentials._read_credential(credentials.target_for(PROBE_PROVIDER))

    def test_ascii_key_round_trips(self):
        credentials.set_api_key(PROBE_PROVIDER, "sk-ant-abc123")
        self.assertEqual(self.read_back(), "sk-ant-abc123")

    def test_non_ascii_survives_the_utf16_round_trip(self):
        """A wrong encoding on the read side returns mojibake, not an error."""
        credentials.set_api_key(PROBE_PROVIDER, "klucz-ĄŻÓŁ-śćń")
        self.assertEqual(self.read_back(), "klucz-ĄŻÓŁ-śćń")

    def test_long_key_round_trips(self):
        key = "sk-" + "x" * 400
        credentials.set_api_key(PROBE_PROVIDER, key)
        self.assertEqual(self.read_back(), key)

    def test_surrounding_whitespace_is_stripped(self):
        """Keys get pasted; a trailing newline must not become part of the secret."""
        credentials.set_api_key(PROBE_PROVIDER, "  sk-ant-trimmed\n")
        self.assertEqual(self.read_back(), "sk-ant-trimmed")

    def test_overwrite_replaces_the_previous_value(self):
        credentials.set_api_key(PROBE_PROVIDER, "first")
        credentials.set_api_key(PROBE_PROVIDER, "second")
        self.assertEqual(self.read_back(), "second")

    def test_delete_removes_it(self):
        credentials.set_api_key(PROBE_PROVIDER, "sk-ant-temp")
        self.assertTrue(credentials.delete_api_key(PROBE_PROVIDER))
        self.assertIsNone(self.read_back())

    def test_delete_of_a_missing_entry_reports_false(self):
        self.assertFalse(credentials.delete_api_key(PROBE_PROVIDER))

    def test_missing_entry_reads_as_none(self):
        self.assertIsNone(self.read_back())

    def test_empty_key_is_rejected_before_touching_the_store(self):
        with self.assertRaises(ValueError):
            credentials.set_api_key(PROBE_PROVIDER, "   ")
        self.assertIsNone(self.read_back())


if __name__ == "__main__":
    unittest.main()
