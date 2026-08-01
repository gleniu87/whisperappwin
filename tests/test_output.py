"""Keeping dictations out of the Windows clipboard history.

Integration, against the real clipboard, for the same reason
tests/test_credentials.py talks to the real Credential Manager: the thing that
can go wrong is whether Windows accepts what we hand it. A mocked
SetClipboardData accepts anything, including a DWORD written the wrong way round
or set outside the clipboard session where it applies to nothing.

Nothing here sends a keystroke. Every case uses auto_paste=False, because a test
that fires Ctrl+V pastes a transcript into whatever window happens to be focused.

The clipboard is a single global resource, so each test puts back what it found.
"""

import unittest

try:
    import win32clipboard
    import win32con

    HAVE_PYWIN32 = True
except ImportError:  # pragma: no cover
    HAVE_PYWIN32 = False

if HAVE_PYWIN32:
    from whisperdictate.output import deliver, get_clipboard_text, set_clipboard_text


@unittest.skipUnless(HAVE_PYWIN32, "pywin32 niedostepny")
class ClipboardHistoryExclusionTest(unittest.TestCase):
    def setUp(self):
        self.previous = get_clipboard_text()
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is not None:
            try:
                set_clipboard_text(self.previous)
            except Exception:  # noqa: BLE001 - a busy clipboard is not this test's fault
                pass

    @staticmethod
    def excluded() -> tuple[bool, bool]:
        """Whether the current clipboard entry carries both opt-out formats."""
        history = win32clipboard.RegisterClipboardFormat("CanIncludeInClipboardHistory")
        cloud = win32clipboard.RegisterClipboardFormat("CanUploadToCloudClipboard")
        win32clipboard.OpenClipboard()
        try:
            return (
                bool(win32clipboard.IsClipboardFormatAvailable(history)),
                bool(win32clipboard.IsClipboardFormatAvailable(cloud)),
            )
        finally:
            win32clipboard.CloseClipboard()

    def test_an_ordinary_write_is_left_alone(self):
        """The flags must be opt-in: set_clipboard_text is used to restore the
        user's own content too, and marking that would be lying about its origin."""
        set_clipboard_text("zwykly zapis")
        self.assertEqual(self.excluded(), (False, False))

    def test_an_excluded_write_carries_both_opt_outs(self):
        set_clipboard_text("transkrypcja", allow_history=False)
        self.assertEqual(self.excluded(), (True, True))

    def test_an_excluded_write_is_still_readable(self):
        """The whole point of choosing this over typing the text out: Ctrl+V
        still has something to paste."""
        set_clipboard_text("zażółć gęślą jaźń", allow_history=False)
        self.assertEqual(get_clipboard_text(), "zażółć gęślą jaźń")

    def test_the_opt_out_value_is_a_four_byte_zero(self):
        """Windows reads a DWORD. A one-byte 0, or a 4-byte 1, would silently
        mean nothing or the opposite."""
        set_clipboard_text("transkrypcja", allow_history=False)
        history = win32clipboard.RegisterClipboardFormat("CanIncludeInClipboardHistory")
        win32clipboard.OpenClipboard()
        try:
            self.assertEqual(win32clipboard.GetClipboardData(history), b"\x00" * 4)
        finally:
            win32clipboard.CloseClipboard()

    def test_deliver_excludes_by_default(self):
        deliver("transkrypcja z dyktowania", auto_paste=False)
        self.assertEqual(self.excluded(), (True, True))

    def test_deliver_can_be_asked_to_keep_the_history_entry(self):
        deliver("transkrypcja z dyktowania", auto_paste=False, clipboard_history=True)
        self.assertEqual(self.excluded(), (False, False))

    def test_deliver_still_puts_the_text_where_ctrl_v_will_find_it(self):
        deliver("tekst do wklejenia", auto_paste=False)
        self.assertEqual(get_clipboard_text(), "tekst do wklejenia")

    def test_empty_text_touches_nothing(self):
        set_clipboard_text("wartosc uzytkownika")
        deliver("", auto_paste=False)
        self.assertEqual(get_clipboard_text(), "wartosc uzytkownika")


@unittest.skipUnless(HAVE_PYWIN32, "pywin32 niedostepny")
class ClipboardTextTest(unittest.TestCase):
    def setUp(self):
        self.previous = get_clipboard_text()
        self.addCleanup(
            lambda: self.previous is not None and set_clipboard_text(self.previous)
        )

    def test_round_trips_polish_text(self):
        set_clipboard_text("Zażółć gęślą jaźń — ĄĆĘŁŃÓŚŹŻ")
        self.assertEqual(get_clipboard_text(), "Zażółć gęślą jaźń — ĄĆĘŁŃÓŚŹŻ")

    def test_non_text_clipboard_reads_as_none(self):
        """A clipboard holding no CF_UNICODETEXT must not raise; the caller uses
        None to mean "nothing worth restoring"."""
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()
        self.assertIsNone(get_clipboard_text())


if __name__ == "__main__":
    unittest.main()
