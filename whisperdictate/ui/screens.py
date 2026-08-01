"""Which monitor a window belongs on, and where exactly.

Tk knows one screen: `winfo_screenwidth()` reports the *primary* monitor and its
origin is always 0,0. On a multi-monitor desktop that put the recording overlay
on the primary screen no matter which one the user was typing into. So the lookup
goes through Win32 and the arithmetic lives here, apart from it, where it can be
tested against monitor rectangles that do not exist on this machine.

Shared by the overlay and the dialogs, which want the same answer to a slightly
different question: "just above the bottom" versus "in the middle".
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

#: MonitorFromWindow: fall back to the nearest monitor rather than to none.
_MONITOR_DEFAULTTONEAREST = 2


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def focused_work_area() -> tuple[int, int, int, int] | None:
    """Work area of the monitor holding the foreground window, or None.

    The work area, not the full monitor rect, so a window clears a taskbar docked
    on that particular screen.
    """
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        handle = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if not handle:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom
    except Exception:  # noqa: BLE001 - not Windows, or a stripped-down runtime
        log.debug("Nie moge ustalic monitora aktywnego okna", exc_info=True)
        return None


def work_area_for(window) -> tuple[int, int, int, int]:  # noqa: ANN001 - tk.Misc
    """`focused_work_area()`, falling back to Tk's single-screen view of the world."""
    return focused_work_area() or (
        0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
    )


def position_in(
    area: tuple[int, int, int, int], width: int, height: int, margin: int
) -> tuple[int, int]:
    """Centred horizontally in `area`, `margin` up from its bottom edge."""
    left, top, right, bottom = area
    x = left + (right - left - width) // 2
    y = bottom - height - margin
    # A short screen (or a tall margin) must not push the window off the top.
    return x, max(top, y)


def centre_in(
    area: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int]:
    """Dead centre of `area`. Clamped to its top-left, so an oversized window
    loses its bottom-right corner rather than its title bar and buttons."""
    left, top, right, bottom = area
    x = left + (right - left - width) // 2
    y = top + (bottom - top - height) // 2
    return max(left, x), max(top, y)


def place(window, x: int, y: int, width: int, height: int) -> None:  # noqa: ANN001
    """Move a Tk window to absolute desktop coordinates.

    Tk accepts a negative offset written as "+-1920+100" and places the window
    there literally, which is what a monitor left of the primary needs. Verified
    against `winfo_rootx()` rather than assumed.
    """
    window.geometry(f"{width}x{height}+{x}+{y}")


def centre_on_active_monitor(window) -> None:  # noqa: ANN001 - tk.Toplevel
    """Centre an already-built window on the screen the user is working on.

    Call after the widgets are packed: the size comes from `winfo_reqwidth()`,
    which is only meaningful once there is something to measure.
    """
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x, y = centre_in(work_area_for(window), width, height)
    place(window, x, y, width, height)
