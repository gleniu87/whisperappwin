"""Global hotkey detection, tuned for right Alt on a Polish keyboard layout.

The hard constraint: on Polish (Programmers) layout right Alt *is* AltGr, the key
you press to type a/e/o/s/l/z with diacritics. A naive hold-to-talk hook there
would either swallow those characters or fire on every one of them.

Three properties keep it usable:

1. The listener never suppresses the key. AltGr combinations reach the focused
   application untouched, so typing is unaffected whether or not we are running.
2. Recording only starts after the key has been held past a threshold
   (~300 ms). Typing "a" is a sub-100 ms tap and never crosses it.
3. Pressing a character key while the trigger is held cancels the gesture - it
   was an AltGr combination, not a dictation.

Modifier keys are exempt from rule 3 on purpose: Windows synthesises a left-Ctrl
press alongside every AltGr, so treating modifiers as cancelling input would make
the trigger fire never.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

Key = keyboard.Key

# Never count as "some other key was pressed".
_MODIFIER_KEYS = frozenset(
    {
        Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr,
        Key.ctrl, Key.ctrl_l, Key.ctrl_r,
        Key.shift, Key.shift_l, Key.shift_r,
        Key.cmd, Key.cmd_l, Key.cmd_r,
        Key.caps_lock, Key.num_lock,
    }
)


def _trigger_table() -> dict[str, frozenset]:
    table: dict[str, frozenset] = {
        # alt_gr included: which of the two pynput reports depends on whether the
        # active layout defines an AltGr level. Polish (Programmers) does, US does not.
        "alt_r": frozenset({Key.alt_r, Key.alt_gr}),
        "alt_l": frozenset({Key.alt_l}),
        "ctrl_r": frozenset({Key.ctrl_r}),
        "ctrl_l": frozenset({Key.ctrl_l}),
        "scroll_lock": frozenset({Key.scroll_lock}),
        "pause": frozenset({Key.pause}),
    }
    for n in range(1, 21):
        key = getattr(Key, f"f{n}", None)
        if key is not None:
            table[f"f{n}"] = frozenset({key})
    return table


TRIGGERS = _trigger_table()


class HotkeyListener:
    """Watches the keyboard and calls back on start / stop / cancel.

    Callbacks run on the listener or timer thread. They must return promptly -
    blocking here stalls every subsequent key event system-wide.
    """

    def __init__(
        self,
        *,
        key: str = "alt_r",
        mode: str = "hold",
        hold_threshold_ms: int = 300,
        cancel_on_other_key: bool = True,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None] = lambda: None,
    ):
        self.trigger_keys = TRIGGERS.get(key.lower(), TRIGGERS["alt_r"])
        if key.lower() not in TRIGGERS:
            log.warning("Nieznany klawisz %r - uzywam alt_r. Dostepne: %s", key, ", ".join(sorted(TRIGGERS)))
        self.key_name = key.lower()
        self.mode = mode if mode in ("hold", "toggle") else "hold"
        self.hold_threshold = max(0.0, hold_threshold_ms / 1000.0)
        self.cancel_on_other_key = cancel_on_other_key

        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel

        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._trigger_down = False
        self._gesture_cancelled = False
        self._recording = False
        self._enabled = True

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info(
            "Hotkey aktywny: %s, tryb %s%s",
            self.key_name,
            self.mode,
            f", prog {self.hold_threshold * 1000:.0f} ms" if self.mode == "hold" else "",
        )

    def stop(self) -> None:
        self._cancel_timer()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def set_enabled(self, enabled: bool) -> None:
        """Pause without tearing down the hook. An in-flight recording is cancelled."""
        self._enabled = enabled
        if not enabled:
            with self._lock:
                was_recording = self._recording
                self._recording = False
                self._trigger_down = False
            self._cancel_timer()
            if was_recording:
                self._safe(self._on_cancel)

    # -- key events -----------------------------------------------------

    def _on_press(self, key) -> None:  # noqa: ANN001 - pynput API
        if not self._enabled:
            return
        if key in self.trigger_keys:
            self._trigger_pressed()
        elif self.cancel_on_other_key and key not in _MODIFIER_KEYS:
            self._other_key_pressed()

    def _on_release(self, key) -> None:  # noqa: ANN001 - pynput API
        if key not in self.trigger_keys:
            return
        if self.mode == "toggle":
            with self._lock:
                self._trigger_down = False
            return
        self._trigger_released()

    # -- hold mode ------------------------------------------------------

    def _trigger_pressed(self) -> None:
        callback: Callable[[], None] | None = None
        with self._lock:
            if self._trigger_down:
                return  # key auto-repeat, not a new press
            self._trigger_down = True
            self._gesture_cancelled = False

            if self.mode == "toggle":
                was_recording = self._recording
                self._recording = not was_recording
                callback = self._on_stop if was_recording else self._on_start
            else:
                self._timer = threading.Timer(self.hold_threshold, self._threshold_reached)
                self._timer.daemon = True
                self._timer.start()

        # Outside the lock: callbacks may re-enter (e.g. set_enabled from the tray).
        if callback is not None:
            self._safe(callback)

    def _threshold_reached(self) -> None:
        with self._lock:
            if not self._trigger_down or self._gesture_cancelled or self._recording:
                return
            self._recording = True
        self._safe(self._on_start)

    def _trigger_released(self) -> None:
        self._cancel_timer()
        with self._lock:
            self._trigger_down = False
            was_recording = self._recording
            self._recording = False
        if was_recording:
            self._safe(self._on_stop)

    def _other_key_pressed(self) -> None:
        """A real key while the trigger is held: this was an AltGr combination."""
        with self._lock:
            if not self._trigger_down:
                return
            self._gesture_cancelled = True
            was_recording = self._recording
            self._recording = False
        self._cancel_timer()
        if was_recording:
            log.debug("Nagrywanie anulowane - wcisnieto inny klawisz")
            self._safe(self._on_cancel)

    # -- helpers --------------------------------------------------------

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    @staticmethod
    def _safe(callback: Callable[[], None]) -> None:
        """A raising callback must not kill the keyboard hook."""
        try:
            callback()
        except Exception:  # noqa: BLE001
            log.exception("Blad w callbacku hotkey")
