"""Small Tk dialogs. Must run on the Tk thread — see `dispatch.MainThreadDispatcher`."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, simpledialog

from ..enhance import credentials

log = logging.getLogger(__name__)


def ask_api_key(root: tk.Tk) -> None:
    """Prompt for an Anthropic API key and store it in Credential Manager."""
    # The app has no visible window, so a dialog parented to the withdrawn root
    # opens behind whatever the user is looking at. Raise it deliberately.
    root.attributes("-topmost", True)
    try:
        key = simpledialog.askstring(
            "WhisperDictate - klucz API",
            "Wklej klucz API Anthropic (sk-ant-...).\n"
            "Zostanie zapisany w Menedzerze polswiadczen Windows,\n"
            "nie w pliku konfiguracyjnym.",
            show="*",
            parent=root,
        )
        if key is None:
            return  # cancelled

        try:
            credentials.set_api_key(key)
        except (ValueError, OSError) as exc:
            messagebox.showerror("WhisperDictate", f"Nie moge zapisac klucza:\n{exc}", parent=root)
            return

        messagebox.showinfo(
            "WhisperDictate",
            "Klucz zapisany. Czyszczenie tekstu jest gotowe do wlaczenia.",
            parent=root,
        )
    finally:
        root.attributes("-topmost", False)


def confirm_delete_api_key(root: tk.Tk) -> None:
    root.attributes("-topmost", True)
    try:
        if not messagebox.askyesno(
            "WhisperDictate", "Usunac zapisany klucz API z Menedzera polswiadczen?", parent=root
        ):
            return
        removed = credentials.delete_api_key()
        messagebox.showinfo(
            "WhisperDictate",
            "Klucz usuniety." if removed else "Nie bylo zapisanego klucza.",
            parent=root,
        )
    finally:
        root.attributes("-topmost", False)
