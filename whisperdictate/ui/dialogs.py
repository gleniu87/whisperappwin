"""Small Tk dialogs. Must run on the Tk thread — see `dispatch.MainThreadDispatcher`."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, simpledialog

from ..enhance import credentials, spec

log = logging.getLogger(__name__)


def manage_api_key(root: tk.Tk, provider: str) -> None:
    """Set, replace, or clear the API key for one provider."""
    provider_spec = spec(provider)
    current = credentials.source(provider)

    # The app has no visible window, so a dialog parented to the withdrawn root
    # opens behind whatever the user is looking at. Raise it deliberately.
    root.attributes("-topmost", True)
    try:
        if current != "brak":
            keep = messagebox.askyesnocancel(
                f"WhisperDictate - klucz {provider}",
                f"Klucz jest juz ustawiony ({current}).\n\n"
                "Tak - wpisz nowy\n"
                "Nie - usun zapisany klucz\n"
                "Anuluj - zostaw bez zmian",
                parent=root,
            )
            if keep is None:
                return
            if keep is False:
                removed = credentials.delete_api_key(provider)
                messagebox.showinfo(
                    "WhisperDictate",
                    "Klucz usuniety." if removed else "Nie bylo zapisanego klucza.",
                    parent=root,
                )
                return

        key = simpledialog.askstring(
            f"WhisperDictate - klucz {provider}",
            f"Wklej klucz API dla: {provider_spec.label}\n"
            f"Ruch trafia do: {provider_spec.hosting}\n\n"
            "Klucz zostanie zapisany w Menedzerze polswiadczen Windows,\n"
            "nie w pliku konfiguracyjnym.",
            show="*",
            parent=root,
        )
        if key is None:
            return  # cancelled

        try:
            credentials.set_api_key(provider, key)
        except (ValueError, OSError) as exc:
            messagebox.showerror("WhisperDictate", f"Nie moge zapisac klucza:\n{exc}", parent=root)
            return

        messagebox.showinfo("WhisperDictate", f"Klucz ({provider}) zapisany.", parent=root)
    finally:
        root.attributes("-topmost", False)
