"""Small Tk dialogs. Must run on the Tk thread — see `dispatch.MainThreadDispatcher`."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, simpledialog

from ..enhance import credentials, spec

log = logging.getLogger(__name__)


def review_vocabulary_suggestions(root: tk.Tk, controller) -> None:  # noqa: ANN001
    """Walk the pending suggestions, one at a time.

    The proposed term is editable because the clean-up model returns whatever
    form the sentence needed ("DeepSeeka"), while the vocabulary wants the base
    form ("DeepSeek").
    """
    pending = controller.pending_vocabulary
    if not pending:
        root.attributes("-topmost", True)
        try:
            messagebox.showinfo(
                "WhisperDictate", "Brak nowych propozycji.", parent=root
            )
        finally:
            root.attributes("-topmost", False)
        return

    root.attributes("-topmost", True)
    try:
        for found in pending:
            answer = simpledialog.askstring(
                "WhisperDictate - nowa nazwa wlasna?",
                f"Model czyszczacy poprawil:\n\n"
                f"     {found.heard}  ->  {found.corrected}\n\n"
                "Wpisz forme podstawowa, zeby dopisac do slownika.\n"
                "Whisper bedzie odtad nastawiony na te pisownie.\n\n"
                "Puste pole = nie pytaj o to slowo ponownie.\n"
                "Anuluj = zostaw na pozniej.",
                initialvalue=found.corrected,
                parent=root,
            )
            if answer is None:
                return  # leave the rest pending
            if answer.strip():
                controller.accept_vocabulary(answer)
            else:
                controller.reject_vocabulary(found.corrected)
    finally:
        root.attributes("-topmost", False)


def edit_vocabulary(root: tk.Tk, controller) -> None:  # noqa: ANN001 - avoids a circular import
    """Edit the comma-separated proper-noun list."""
    from ..vocabulary import terms

    current = str(controller.config.get("transcription.vocabulary", "") or "")

    root.attributes("-topmost", True)
    try:
        answer = simpledialog.askstring(
            "WhisperDictate - nazwy wlasne",
            "Nazwy, ktore Whisper przekreca, po przecinku.\n"
            "Np.: DeepSeek, Claude Code, ICE InsureTech\n\n"
            "Trafiaja do Whispera (zeby uslyszal je poprawnie)\n"
            "i do modelu czyszczacego (zeby naprawil te przekrecone).",
            initialvalue=current,
            parent=root,
        )
        if answer is None:
            return  # cancelled

        controller.set_vocabulary(answer)
        count = len(terms(answer))
        messagebox.showinfo(
            "WhisperDictate",
            f"Zapisano {count} nazw(y)." if count else "Lista wyczyszczona.",
            parent=root,
        )
    finally:
        root.attributes("-topmost", False)


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
