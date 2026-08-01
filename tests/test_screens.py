"""Which monitor a window lands on, and where.

Only the arithmetic is testable here; the Win32 monitor lookup needs a real
desktop. The arithmetic is where the bug was, though: the old overlay centred on
`winfo_screenwidth()` and placed itself at an origin of 0,0, which *is* the
primary monitor - so on two screens it always appeared on the wrong one whenever
the user was typing on the other.
"""

import unittest

from whisperdictate.ui.screens import centre_in, focused_work_area, position_in


class PositionInTest(unittest.TestCase):
    """The overlay: centred horizontally, sitting above the bottom edge."""

    def test_centres_horizontally_on_a_single_screen(self):
        x, _ = position_in((0, 0, 1920, 1080), 300, 56, 140)
        self.assertEqual(x, (1920 - 300) // 2)

    def test_sits_the_margin_above_the_bottom(self):
        _, y = position_in((0, 0, 1920, 1080), 300, 56, 140)
        self.assertEqual(y, 1080 - 56 - 140)

    def test_follows_a_monitor_to_the_right_of_the_primary(self):
        """The whole point: an origin that is not 0,0."""
        x, y = position_in((1920, 0, 3840, 1080), 300, 56, 140)
        self.assertEqual(x, 1920 + (1920 - 300) // 2)
        self.assertEqual(y, 1080 - 56 - 140)

    def test_follows_a_monitor_to_the_left_of_the_primary(self):
        """Windows gives those a negative origin - the real case on this machine,
        where the foreground window sat at (-1920, 0, 0, 1040)."""
        x, _ = position_in((-1920, 0, 0, 1040), 300, 56, 140)
        self.assertEqual(x, -1920 + (1920 - 300) // 2)
        self.assertLess(x, 0)

    def test_respects_a_taskbar_at_the_bottom_of_that_screen(self):
        """rcWork, not rcMonitor: a 1080-tall screen with a 40 px taskbar."""
        _, y = position_in((0, 0, 1920, 1040), 300, 56, 140)
        self.assertEqual(y, 1040 - 56 - 140)

    def test_respects_a_taskbar_at_the_top_of_that_screen(self):
        x, y = position_in((0, 40, 1920, 1080), 300, 56, 140)
        self.assertEqual(x, (1920 - 300) // 2)
        self.assertEqual(y, 1080 - 56 - 140)

    def test_a_monitor_shorter_than_the_margin_keeps_the_window_on_screen(self):
        _, y = position_in((0, 0, 1024, 120), 300, 56, 140)
        self.assertEqual(y, 0)


class CentreInTest(unittest.TestCase):
    """The dialogs: dead centre of the screen being worked on."""

    def test_centres_on_a_single_screen(self):
        self.assertEqual(centre_in((0, 0, 1920, 1080), 400, 500), (760, 290))

    def test_centres_on_a_monitor_left_of_the_primary(self):
        x, y = centre_in((-1920, 0, 0, 1040), 400, 500)
        self.assertEqual(x, -1920 + (1920 - 400) // 2)
        self.assertEqual(y, (1040 - 500) // 2)

    def test_accounts_for_a_taskbar_offset_top(self):
        _, y = centre_in((0, 40, 1920, 1080), 400, 500)
        self.assertEqual(y, 40 + (1040 - 500) // 2)

    def test_a_window_too_big_for_the_screen_keeps_its_top_left_visible(self):
        """Losing the bottom-right corner is recoverable; losing the title bar and
        the buttons is not."""
        self.assertEqual(centre_in((0, 0, 800, 600), 1200, 900), (0, 0))

    def test_a_window_exactly_the_size_of_the_screen_sits_at_the_origin(self):
        self.assertEqual(centre_in((100, 50, 900, 650), 800, 600), (100, 50))


class WorkAreaTest(unittest.TestCase):
    def test_the_lookup_returns_a_usable_rect_on_this_machine(self):
        """Integration, on whatever desktop this runs on. A mock would only prove
        that ctypes can be mocked."""
        area = focused_work_area()
        if area is None:
            self.skipTest("brak okna pierwszoplanowego")
        left, top, right, bottom = area
        self.assertLess(left, right)
        self.assertLess(top, bottom)


if __name__ == "__main__":
    unittest.main()
