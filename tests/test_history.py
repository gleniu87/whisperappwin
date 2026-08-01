"""JSONL history: appending, resilience to damage, trimming."""

import json
import tempfile
import unittest
from pathlib import Path

from whisperdictate.history import History


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "history.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_writes_one_line_with_a_timestamp(self):
        History(self.path).append(text="czesc", language="pl")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["text"], "czesc")
        self.assertIn("timestamp", entry)

    def test_disabled_history_writes_nothing(self):
        History(self.path, enabled=False).append(text="czesc")
        self.assertFalse(self.path.exists())

    def test_non_ascii_is_stored_readable_not_escaped(self):
        History(self.path).append(text="non-ascii ĄĆĘŁŃÓŚŹŻ ąćęłńóśźż")
        self.assertIn("ĄĆĘŁŃÓŚŹŻ", self.path.read_text(encoding="utf-8"))

    def test_recent_returns_last_n_in_order(self):
        history = History(self.path)
        for i in range(5):
            history.append(text=str(i))
        self.assertEqual([e["text"] for e in history.recent(2)], ["3", "4"])

    def test_torn_line_is_skipped_not_fatal(self):
        history = History(self.path)
        history.append(text="ok")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"text": "urwane\n')  # simulates a crash mid-write
        history.append(text="tez ok")
        self.assertEqual([e["text"] for e in history.recent(10)], ["ok", "tez ok"])

    def test_trim_keeps_the_newest_entries(self):
        history = History(self.path, max_entries=10)
        for i in range(120):  # many times the trim interval
            history.append(text=str(i))
        entries = history.recent(1000)
        self.assertEqual(len(entries), 10)
        self.assertEqual(entries[-1]["text"], "119")
        self.assertEqual(entries[0]["text"], "110")

    def test_trim_interval_scales_down_for_small_limits(self):
        """max_entries=10 must not mean 'grow to 110 lines, then trim'."""
        history = History(self.path, max_entries=10)
        for i in range(25):
            history.append(text=str(i))
        line_count = len(self.path.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(line_count, 20)

    def test_unwritable_path_does_not_raise(self):
        """Losing a history entry must never cost the user their transcript."""
        history = History(Path(self._tmp.name) / "nested" / "\0invalid" / "history.jsonl")
        history.append(text="czesc")  # must not raise


if __name__ == "__main__":
    unittest.main()
