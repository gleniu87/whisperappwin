"""Run a callable on the Tk main thread from anywhere.

The tray runs its own Win32 message loop, so a menu action that needs a Tk
dialog cannot open one directly — Tk objects may only be touched from the thread
that created them. Menu handlers hand the work here instead.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from collections.abc import Callable

log = logging.getLogger(__name__)

POLL_MS = 60


class MainThreadDispatcher:
    """Queue of callables drained on the Tk thread by a periodic `after()`."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.root.after(POLL_MS, self._drain)

    def call(self, func: Callable[[], None]) -> None:
        """Schedule `func`. Safe from any thread; returns immediately."""
        self._queue.put(func)

    def _drain(self) -> None:
        try:
            while True:
                func = self._queue.get_nowait()
                try:
                    func()
                except Exception:  # noqa: BLE001 - a bad dialog must not kill the loop
                    log.exception("Error in a task on the main thread")
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._drain)
