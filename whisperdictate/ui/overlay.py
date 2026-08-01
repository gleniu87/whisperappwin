"""Floating always-on-top recording indicator.

Tk must be driven from the thread that created it, but state changes arrive from
the keyboard hook and from worker threads. Everything crossing that boundary goes
through a queue drained by a periodic `after()` callback on the Tk thread.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from collections.abc import Callable

from ..controller import State
from ..i18n import t
from .screens import place, position_in, work_area_for

log = logging.getLogger(__name__)

WIDTH = 300
HEIGHT = 56
BOTTOM_MARGIN = 140  # clears the taskbar and most app status bars

BG = "#16181d"
BORDER = "#2c3038"
TEXT = "#e6e8ec"
MUTED = "#8b919c"
METER_BG = "#242830"

STATE_COLOUR: dict[State, str] = {
    State.LOADING: "#f0b429",
    State.RECORDING: "#e5484d",
    State.TRANSCRIBING: "#3b82f6",
    State.ENHANCING: "#8b5cf6",
    State.ERROR: "#e5484d",
}

# States that keep the overlay on screen. Everything else hides it.
VISIBLE_STATES = frozenset(STATE_COLOUR)

# Work in progress gets the trailing dots; a state that is simply true does not.
ONGOING_STATES = frozenset({State.LOADING, State.TRANSCRIBING, State.ENHANCING})

POLL_MS = 40
TOAST_MS = 2600
# Whisper input is normalised speech; RMS rarely exceeds this, so scale to it
# rather than to full scale or the meter never moves.
METER_FULL_SCALE = 0.25


def _label(state: State) -> str:
    """The state's name in the interface language, looked up at draw time.

    Not captured in a table at import: the words change when the user switches
    language in the tray, and the overlay redraws 25 times a second anyway.
    """
    text = t(f"state.{state.value}")
    return f"{text}..." if state in ONGOING_STATES else text


class Overlay:
    """Thread-safe façade over a Tk toplevel.

    `set_state` / `notify` may be called from any thread; nothing else may.
    """

    def __init__(
        self,
        root: tk.Tk,
        *,
        level_provider: Callable[[], float] = lambda: 0.0,
        enabled: bool = True,
    ):
        self.root = root
        self.enabled = enabled
        self._level_provider = level_provider
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._state = State.IDLE
        self._detail = ""
        self._toast: str | None = None
        self._toast_error = False
        self._toast_job: str | None = None

        self._window: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        if self.enabled:
            self._build()
        self.root.after(POLL_MS, self._pump)

    # -- public, thread-safe --------------------------------------------

    def set_state(self, state: State, detail: str = "") -> None:
        self._events.put(("state", (state, detail)))

    def notify(self, message: str, *, error: bool = False) -> None:
        self._events.put(("toast", (message, error)))

    def destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None

    # -- Tk thread only ---------------------------------------------------

    def _build(self) -> None:
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.94)
        window.configure(bg=BG)
        try:
            # Click-through-ish: the overlay must never take focus away from the
            # window the user is dictating into.
            window.attributes("-disabled", True)
        except tk.TclError:  # pragma: no cover - older Tk
            log.debug("Tk nie wspiera -disabled; overlay moze przejmowac focus")

        canvas = tk.Canvas(window, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0)
        canvas.pack()

        self._window = window
        self._canvas = canvas
        self._position(window)
        window.withdraw()

    @staticmethod
    def _position(window: tk.Toplevel) -> None:
        """Put the overlay on the screen the user is working on.

        Recomputed every time the overlay is shown, not once at construction: the
        answer is "wherever the foreground window is now", and that changes
        between dictations. Not recomputed while it is already visible - the
        window would chase the mouse across screens mid-recording.
        """
        x, y = position_in(work_area_for(window), WIDTH, HEIGHT, BOTTOM_MARGIN)
        place(window, x, y, WIDTH, HEIGHT)

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "state":
                    self._state, self._detail = payload  # type: ignore[assignment]
                elif kind == "toast":
                    self._toast, self._toast_error = payload  # type: ignore[assignment]
                    self._schedule_toast_clear()
        except queue.Empty:
            pass

        if self.enabled:
            self._render()
        self.root.after(POLL_MS, self._pump)

    def _schedule_toast_clear(self) -> None:
        if self._toast_job is not None:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(TOAST_MS, self._clear_toast)

    def _clear_toast(self) -> None:
        self._toast = None
        self._toast_job = None

    def _render(self) -> None:
        window, canvas = self._window, self._canvas
        if window is None or canvas is None:
            return

        should_show = self._state in VISIBLE_STATES or self._toast is not None
        if not should_show:
            if window.winfo_viewable():
                window.withdraw()
            return

        if not window.winfo_viewable():
            self._position(window)
            window.deiconify()
            window.attributes("-topmost", True)

        canvas.delete("all")
        canvas.create_rectangle(0, 0, WIDTH - 1, HEIGHT - 1, outline=BORDER, width=1)

        if self._toast is not None:
            colour = "#e5484d" if self._toast_error else "#3b82f6"
            self._draw_dot(canvas, colour)
            self._draw_text(canvas, self._toast, "", TEXT)
            return

        colour = STATE_COLOUR.get(self._state, MUTED)
        self._draw_dot(canvas, colour)
        self._draw_text(canvas, _label(self._state), self._detail, TEXT)

        if self._state is State.RECORDING:
            self._draw_meter(canvas, colour)

    @staticmethod
    def _draw_dot(canvas: tk.Canvas, colour: str) -> None:
        canvas.create_oval(18, HEIGHT // 2 - 6, 30, HEIGHT // 2 + 6, fill=colour, outline="")

    @staticmethod
    def _draw_text(canvas: tk.Canvas, label: str, detail: str, colour: str) -> None:
        y = HEIGHT // 2 - (8 if detail else 0)
        canvas.create_text(44, y, text=label, anchor="w", fill=colour, font=("Segoe UI", 11, "bold"))
        if detail:
            canvas.create_text(
                44, y + 17, text=_ellipsise(detail, 42), anchor="w",
                fill=MUTED, font=("Segoe UI", 8),
            )

    def _draw_meter(self, canvas: tk.Canvas, colour: str) -> None:
        x0, x1 = 44, WIDTH - 20
        y = HEIGHT - 14
        canvas.create_rectangle(x0, y, x1, y + 4, fill=METER_BG, outline="")

        level = min(1.0, max(0.0, self._level_provider() / METER_FULL_SCALE))
        if level > 0.01:
            canvas.create_rectangle(x0, y, x0 + (x1 - x0) * level, y + 4, fill=colour, outline="")


def _ellipsise(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
