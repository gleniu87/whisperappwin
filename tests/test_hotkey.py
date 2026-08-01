"""Hotkey gesture state machine.

Drives the listener's key handlers directly instead of starting the real hook,
so these run headless and deterministically. Thresholds are shrunk to keep the
suite fast; the sleeps are the smallest that stay reliable on a loaded machine.
"""

import time
import unittest

from pynput.keyboard import Key, KeyCode

from whisperdictate.hotkey import HotkeyListener

THRESHOLD_MS = 40
SETTLE_S = 0.12  # comfortably past the threshold


class Recorder:
    """Records callback order so tests can assert on the whole gesture."""

    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")

    def cancel(self):
        self.events.append("cancel")


def make_listener(**overrides) -> tuple[HotkeyListener, Recorder]:
    calls = Recorder()
    kwargs = dict(
        key="alt_r",
        mode="hold",
        hold_threshold_ms=THRESHOLD_MS,
        cancel_on_other_key=True,
        on_start=calls.start,
        on_stop=calls.stop,
        on_cancel=calls.cancel,
    )
    kwargs.update(overrides)
    return HotkeyListener(**kwargs), calls


class HoldModeTest(unittest.TestCase):
    def test_hold_past_threshold_then_release_records(self):
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_quick_tap_does_not_record(self):
        """This is what makes AltGr+a still type 'a'."""
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        listener._on_release(Key.alt_r)
        time.sleep(SETTLE_S)
        self.assertEqual(calls.events, [])

    def test_altgr_reported_as_alt_gr_also_triggers(self):
        """Polish (Programmers) layout makes pynput report Key.alt_gr, not alt_r."""
        listener, calls = make_listener()
        listener._on_press(Key.alt_gr)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_gr)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_key_autorepeat_starts_only_once(self):
        listener, calls = make_listener()
        for _ in range(5):
            listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_character_before_threshold_suppresses_recording(self):
        """AltGr+a: the combination must never start a recording."""
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        listener._on_press(KeyCode.from_char("a"))
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, [])

    def test_character_during_recording_cancels(self):
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_press(KeyCode.from_char("x"))
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "cancel"])

    def test_synthetic_left_ctrl_does_not_cancel(self):
        """Windows emits LCtrl alongside every AltGr - it must be ignored."""
        listener, calls = make_listener()
        listener._on_press(Key.ctrl_l)
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_shift_during_recording_does_not_cancel(self):
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_press(Key.shift)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_cancel_on_other_key_disabled(self):
        listener, calls = make_listener(cancel_on_other_key=False)
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_press(KeyCode.from_char("x"))
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_unrelated_key_alone_does_nothing(self):
        listener, calls = make_listener()
        listener._on_press(KeyCode.from_char("q"))
        listener._on_release(KeyCode.from_char("q"))
        time.sleep(SETTLE_S)
        self.assertEqual(calls.events, [])


class ToggleModeTest(unittest.TestCase):
    def test_press_starts_second_press_stops(self):
        listener, calls = make_listener(key="f9", mode="toggle")
        listener._on_press(Key.f9)
        listener._on_release(Key.f9)
        listener._on_press(Key.f9)
        listener._on_release(Key.f9)
        self.assertEqual(calls.events, ["start", "stop"])

    def test_autorepeat_does_not_toggle(self):
        listener, calls = make_listener(key="f9", mode="toggle")
        listener._on_press(Key.f9)
        listener._on_press(Key.f9)
        listener._on_press(Key.f9)
        self.assertEqual(calls.events, ["start"])


class PauseTest(unittest.TestCase):
    def test_disabling_mid_recording_cancels(self):
        listener, calls = make_listener()
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener.set_enabled(False)
        self.assertEqual(calls.events, ["start", "cancel"])

    def test_disabled_listener_ignores_the_trigger(self):
        listener, calls = make_listener()
        listener.set_enabled(False)
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)
        self.assertEqual(calls.events, [])


class ConfigurationTest(unittest.TestCase):
    def test_unknown_key_falls_back_to_alt_r(self):
        listener, _ = make_listener(key="nonexistent")
        self.assertIn(Key.alt_r, listener.trigger_keys)

    def test_unknown_mode_falls_back_to_hold(self):
        listener, _ = make_listener(mode="sideways")
        self.assertEqual(listener.mode, "hold")

    def test_callback_exception_does_not_escape(self):
        """A raising callback must not kill the global keyboard hook."""

        def boom():
            raise RuntimeError("nope")

        listener, _ = make_listener(on_start=boom)
        listener._on_press(Key.alt_r)
        time.sleep(SETTLE_S)
        listener._on_release(Key.alt_r)  # must not raise


if __name__ == "__main__":
    unittest.main()
