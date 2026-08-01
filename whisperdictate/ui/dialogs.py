"""Small Tk dialogs. Must run on the Tk thread — see `dispatch.MainThreadDispatcher`."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, simpledialog

from ..enhance import credentials, spec

log = logging.getLogger(__name__)


def _ask_about_name(root: tk.Tk, found, index: int, total: int) -> tuple[str, str]:  # noqa: ANN001
    """One suggestion. Returns (action, term): add | skip | never | stop.

    A purpose-built window rather than `askstring`, because the field must be
    pre-filled with the *base* form while the inflected original stays visible.
    Pre-filling the inflected form is how "Anthropica" got into a real
    vocabulary: the dialog offered it and OK was the obvious button.
    """
    from ..vocabulary import base_form

    suggested = base_form(found.corrected)
    outcome = {"action": "stop", "term": ""}

    window = tk.Toplevel(root)
    window.title("WhisperDictate - nowa nazwa wlasna?")
    window.resizable(False, False)
    # Only when the parent is actually on screen. The app's root is withdrawn,
    # and a transient of an unmapped parent never gets mapped itself - the
    # window stayed 1x1 at 0,0 until this was made conditional. Same guard
    # tkinter.simpledialog uses, for the same reason.
    if root.winfo_viewable():
        window.transient(root)

    frame = tk.Frame(window, padx=16, pady=14)
    frame.pack(fill="both", expand=True)

    counter = f"  ({index}/{total})" if total > 1 else ""
    tk.Label(frame, text=f"Model czyszczacy poprawil:{counter}", anchor="w").pack(fill="x")
    tk.Label(
        frame,
        text=f"{found.heard}   ->   {found.corrected}",
        font=("Segoe UI", 11, "bold"),
        pady=8,
    ).pack()

    tk.Label(
        frame,
        text="Do slownika trafi forma podstawowa (mianownik) -\n"
             "odmiane model zrobi sam. Popraw, jesli zgadlem zle:",
        justify="left",
        anchor="w",
    ).pack(fill="x")

    entry = tk.Entry(frame, width=38, font=("Segoe UI", 10))
    entry.insert(0, suggested)
    entry.select_range(0, "end")
    entry.pack(pady=(6, 4), fill="x")

    if suggested != found.corrected:
        tk.Label(
            frame,
            text=f"(model podal odmienione: {found.corrected})",
            fg="#666666",
            anchor="w",
        ).pack(fill="x")

    tk.Label(
        frame,
        text="Whisper bedzie odtad nastawiony na te pisownie.",
        fg="#666666",
        anchor="w",
        pady=6,
    ).pack(fill="x")

    def finish(action: str) -> None:
        outcome["action"] = action
        outcome["term"] = entry.get()
        window.destroy()

    buttons = tk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 0))
    tk.Button(buttons, text="Dopisz", width=12, default="active",
              command=lambda: finish("add")).pack(side="left")
    tk.Button(buttons, text="Nie tym razem", width=14,
              command=lambda: finish("skip")).pack(side="left", padx=6)
    tk.Button(buttons, text="Nigdy o to nie pytaj", width=20,
              command=lambda: finish("never")).pack(side="left")

    entry.focus_set()
    window.bind("<Return>", lambda _e: finish("add"))
    window.bind("<Escape>", lambda _e: finish("stop"))
    # Closing with the X means "decide later", like Escape.
    window.protocol("WM_DELETE_WINDOW", lambda: finish("stop"))

    # Order matters: map the window, wait until it really is on screen, only
    # then take the grab. Grabbing an unmapped window silently does nothing.
    window.deiconify()
    window.update_idletasks()
    window.wait_visibility()
    window.grab_set()
    window.lift()
    root.wait_window(window)
    return outcome["action"], outcome["term"]


def review_vocabulary_suggestions(root: tk.Tk, controller) -> None:  # noqa: ANN001
    """Walk the pending suggestions, one at a time."""
    pending = controller.pending_vocabulary
    if not pending:
        root.attributes("-topmost", True)
        try:
            messagebox.showinfo("WhisperDictate", "Brak nowych propozycji.", parent=root)
        finally:
            root.attributes("-topmost", False)
        return

    root.attributes("-topmost", True)
    try:
        for position, found in enumerate(pending, start=1):
            action, term = _ask_about_name(root, found, position, len(pending))
            if action == "stop":
                return  # leave the rest pending
            if action == "add" and term.strip():
                controller.accept_vocabulary(term)
            elif action == "never":
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
            "Np.: DeepSeek, Claude Code, Kubernetes\n\n"
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
