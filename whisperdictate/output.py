"""Delivering the transcript: clipboard, then a synthetic Ctrl+V.

Same approach as the macOS original (NSPasteboard + Cmd+V). The Windows-specific
hazards handled here:

* The clipboard is a single global resource any process can hold open. Opening it
  fails transiently, so every access retries.
* Only CF_UNICODETEXT is preserved when restoring. If the previous clipboard held
  an image or a file list, that content is lost - documented, not silently ignored.
* Restoring the previous content does not undo the *history* entry. Windows
  Clipboard History (Win+V) records every write the moment it happens, so a
  dictation showed up there even though the old content came back. That is what
  the exclusion formats below are for.
* The user has just been holding AltGr. If Windows still believes Ctrl or Alt is
  down when we send Ctrl+V, the target app sees Ctrl+Alt+V instead. We force the
  modifiers up first.
"""

from __future__ import annotations

import logging
import time

import win32api
import win32clipboard
import win32con
from pynput.keyboard import Controller, Key

from .i18n import t

log = logging.getLogger(__name__)

_OPEN_ATTEMPTS = 12
_OPEN_BACKOFF_S = 0.03

#: Undocumented-looking but official: two registered clipboard formats that ask
#: Windows not to keep the entry in Clipboard History (Win+V) and not to sync it
#: to the cloud clipboard. Set to a DWORD 0 alongside the text. Password managers
#: use exactly this pair. Unknown to Windows versions without the feature, where
#: they are simply extra formats nothing reads.
_NO_HISTORY_FORMATS = ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")
#: A DWORD 0. Built rather than written as a bytes literal - four escaped NULs
#: in source are one careless edit away from becoming four real ones.
_FALSE_DWORD = (0).to_bytes(4, "little")

_STUCK_MODIFIER_VKS = (
    win32con.VK_LMENU, win32con.VK_RMENU, win32con.VK_MENU,
    win32con.VK_LCONTROL, win32con.VK_RCONTROL, win32con.VK_CONTROL,
    win32con.VK_LSHIFT, win32con.VK_RSHIFT, win32con.VK_SHIFT,
)

_keyboard = Controller()


class ClipboardError(RuntimeError):
    """The clipboard stayed locked by another process."""


def _open_clipboard() -> None:
    last: Exception | None = None
    for _ in range(_OPEN_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:  # noqa: BLE001 - pywintypes.error
            last = exc
            time.sleep(_OPEN_BACKOFF_S)
    raise ClipboardError(t("error.clipboard_busy", last=last))


def get_clipboard_text() -> str | None:
    """Current clipboard text, or None if it holds something else (or nothing)."""
    try:
        _open_clipboard()
    except ClipboardError as exc:
        log.warning("%s", exc)
        return None
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception as exc:  # noqa: BLE001
        log.debug("Cannot read the clipboard: %s", exc)
        return None
    finally:
        _close_quietly()


def set_clipboard_text(text: str, *, allow_history: bool = True) -> None:
    """Replace the clipboard contents.

    With `allow_history=False` the entry is marked as excluded from Clipboard
    History and from the cloud clipboard, in the same clipboard session as the
    text itself - the flags travel with the data, so they have to be set between
    EmptyClipboard and CloseClipboard or they apply to nothing.
    """
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        if not allow_history:
            _mark_excluded_from_history()
    finally:
        _close_quietly()


def _mark_excluded_from_history() -> None:
    """Best effort: a failure here costs a history entry, never the paste."""
    for name in _NO_HISTORY_FORMATS:
        try:
            win32clipboard.SetClipboardData(
                win32clipboard.RegisterClipboardFormat(name), _FALSE_DWORD
            )
        except Exception as exc:  # noqa: BLE001 - pywintypes.error on odd builds
            log.debug("Cannot mark the clipboard as %s: %s", name, exc)


def _close_quietly() -> None:
    try:
        win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass


def release_stuck_modifiers() -> None:
    """Force Ctrl/Alt/Shift up so our Ctrl+V is not read as Ctrl+Alt+V.

    Only sends key-up for modifiers Windows currently reports as down, so we do
    not fight a user who is legitimately holding Shift.
    """
    for vk in _STUCK_MODIFIER_VKS:
        if win32api.GetAsyncKeyState(vk) & 0x8000:
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def send_paste() -> None:
    """Synthetic Ctrl+V to whatever window has focus."""
    release_stuck_modifiers()
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("v")
        _keyboard.release("v")


def deliver(
    text: str,
    *,
    auto_paste: bool = True,
    restore_clipboard: bool = True,
    paste_delay_ms: int = 120,
    clipboard_history: bool = False,
) -> None:
    """Put `text` on the clipboard and optionally paste it into the focused window.

    `clipboard_history=False` (the default) keeps dictations out of Win+V. The
    clipboard is still used - it is how Ctrl+V works, and typing the text out
    character by character instead would turn every newline into an Enter, which
    sends a half-finished message in every chat app there is.

    Raises ClipboardError if the clipboard could not be written - the caller
    surfaces that to the user rather than silently dropping a transcript.
    """
    if not text:
        return

    previous = get_clipboard_text() if (restore_clipboard and auto_paste) else None

    set_clipboard_text(text, allow_history=clipboard_history)
    if not auto_paste:
        log.info("Text on the clipboard (%d characters), auto-paste disabled", len(text))
        return

    # Give the target app time to notice the clipboard update. Electron-based
    # apps in particular read it asynchronously and will paste stale content
    # if we race them.
    time.sleep(paste_delay_ms / 1000.0)
    send_paste()
    log.info("Pasted %d characters", len(text))

    if previous is not None:
        # The paste itself is asynchronous; restoring too early makes the target
        # app read the *old* content back.
        time.sleep(max(0.15, paste_delay_ms / 1000.0))
        try:
            # The restore is excluded too: putting the user's own text back is not
            # a copy he made, and without this every dictation would leave a
            # duplicate of his previous entry in Win+V.
            set_clipboard_text(previous, allow_history=clipboard_history)
        except ClipboardError as exc:
            log.warning("Cannot restore the previous clipboard contents: %s", exc)
