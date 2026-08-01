"""Handing the last transcript back after a paste that went nowhere.

Ctrl+V into a window with no text field does nothing, `restore_clipboard` then
takes the transcript off the clipboard, and the overlay has already said "N
characters" - so the dictation exists only in the history. This is the way back.

The clipboard cases are integration tests against the real clipboard, for the
same reason tests/test_output.py is: whether Windows accepted what we handed it
is the whole question, and a mock accepts anything.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from whisperdictate import i18n
from whisperdictate.config import Config
from whisperdictate.controller import DictationController
from whisperdictate.history import History

try:
    import win32clipboard  # noqa: F401

    HAVE_PYWIN32 = True
except ImportError:  # pragma: no cover
    HAVE_PYWIN32 = False


class RecordingUi:
    """Captures what the user would have been told."""

    def __init__(self):
        self.messages = []

    def set_state(self, state, detail=""):
        pass

    def notify(self, message, *, error=False):
        self.messages.append((message, error))


class CopyLastTranscriptionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(i18n.use, i18n.language())
        i18n.use("pl")

        root = Path(self._tmp.name)
        self.config = Config.load(root / "config.toml")
        self.history = History(root / "history.jsonl", enabled=True)
        self.ui = RecordingUi()
        self.controller = DictationController(
            config=self.config,
            recorder=mock.Mock(is_recording=False, level=0.0),
            transcriber=mock.Mock(model_name="large-v3-turbo"),
            history=self.history,
            sounds=mock.Mock(),
            ui=self.ui,
        )
        self.written = []
        patcher = mock.patch(
            "whisperdictate.output.set_clipboard_text",
            lambda text, *, allow_history=True: self.written.append((text, allow_history)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- the happy path --------------------------------------------------

    def test_copies_the_newest_transcript(self):
        self.history.append(text="first dictation")
        self.history.append(text="second, with ĄĆĘŁŃÓŚŹŻ")
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [("second, with ĄĆĘŁŃÓŚŹŻ", True)])

    def test_copies_the_cleaned_text_not_the_raw_one(self):
        """`text` is what was pasted; `raw_text` is the transcript before clean-up.
        Handing back the raw version would undo the clean-up silently."""
        self.history.append(text="Check the parser.", raw_text="so um check the parser")
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [("Check the parser.", True)])

    def test_the_user_is_told_how_much_was_copied(self):
        self.history.append(text="twelve chars")  # exactly 12, matched below
        self.controller.copy_last_transcription()
        message, error = self.ui.messages[-1]
        self.assertFalse(error)
        self.assertIn("12", message)

    def test_a_deliberate_copy_is_allowed_into_the_clipboard_history(self):
        """The opposite of a dictation being pasted. He asked for this one, so it
        belongs in Win+V - see tests/test_output.py for the other direction."""
        self.history.append(text="anything")
        self.controller.copy_last_transcription()
        self.assertTrue(self.written[-1][1], "allow_history must be True")

    # -- nothing to give back --------------------------------------------

    def test_an_empty_history_touches_nothing_and_says_so(self):
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [])
        self.assertTrue(self.ui.messages[-1][1], "should be flagged as an error")

    def test_a_switched_off_history_refuses_rather_than_serving_a_stale_entry(self):
        """append() no-ops while disabled, so the newest line is from whenever
        recording was last on. Offering that as "the last transcription" would be
        a wrong answer dressed as a right one."""
        self.history.append(text="from back when history was on")
        self.history.enabled = False
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [])
        self.assertTrue(self.ui.messages[-1][1])

    def test_an_entry_without_text_is_not_copied(self):
        self.history.append(model="large-v3-turbo")  # no text field at all
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [])

    def test_a_corrupted_history_line_does_not_break_the_rescue(self):
        """json.loads can legitimately return a number for a corrupted line, and
        .get() on that raises - the same trap as in refresh_vocabulary_suggestions."""
        self.history.path.write_text("12345\n", encoding="utf-8")
        self.controller.copy_last_transcription()
        self.assertEqual(self.written, [])
        self.assertTrue(self.ui.messages[-1][1])

    def test_a_busy_clipboard_is_reported_not_raised(self):
        from whisperdictate.output import ClipboardError

        self.history.append(text="anything")
        with mock.patch(
            "whisperdictate.output.set_clipboard_text",
            side_effect=ClipboardError("clipboard busy"),
        ):
            self.controller.copy_last_transcription()  # must not raise
        self.assertTrue(self.ui.messages[-1][1])


@unittest.skipUnless(HAVE_PYWIN32, "pywin32 unavailable")
class CopyReachesTheRealClipboardTest(unittest.TestCase):
    """One end-to-end pass, against the real clipboard, without mocks."""

    def setUp(self):
        from whisperdictate.output import get_clipboard_text, set_clipboard_text

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        previous = get_clipboard_text()
        self.addCleanup(lambda: previous is not None and set_clipboard_text(previous))

        root = Path(self._tmp.name)
        self.history = History(root / "history.jsonl", enabled=True)
        self.controller = DictationController(
            config=Config.load(root / "config.toml"),
            recorder=mock.Mock(is_recording=False, level=0.0),
            transcriber=mock.Mock(model_name="large-v3-turbo"),
            history=self.history,
            sounds=mock.Mock(),
            ui=RecordingUi(),
        )

    def test_the_text_really_lands_where_ctrl_v_will_find_it(self):
        from whisperdictate.output import get_clipboard_text

        self.history.append(text="Unicode round-trip — ĄĆĘŁŃÓŚŹŻ ąćęłńóśźż")
        self.controller.copy_last_transcription()
        self.assertEqual(get_clipboard_text(), "Unicode round-trip — ĄĆĘŁŃÓŚŹŻ ąćęłńóśźż")

    def test_and_is_visible_to_the_clipboard_history(self):
        import win32clipboard

        self.history.append(text="deliberate copy")
        self.controller.copy_last_transcription()
        excluded = win32clipboard.RegisterClipboardFormat("CanIncludeInClipboardHistory")
        win32clipboard.OpenClipboard()
        try:
            self.assertFalse(win32clipboard.IsClipboardFormatAvailable(excluded))
        finally:
            win32clipboard.CloseClipboard()


if __name__ == "__main__":
    unittest.main()
