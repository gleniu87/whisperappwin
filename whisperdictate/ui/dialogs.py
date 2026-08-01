"""Small Tk dialogs. Must run on the Tk thread — see `dispatch.MainThreadDispatcher`.

These windows are hand-built rather than `simpledialog` because they hold a list
and a field at once. That means the chrome is ours to get right, and the first
version got it wrong in a way that was reported straight away: the caret sat
against the border of the field with no margin at all.

The reasons, measured rather than guessed:

* `tk.Entry` and `tk.Listbox` have **no** padx/pady options - `'padx' in
  widget.keys()` is False for both. There is no margin to set, so the field is a
  bordered frame with the borderless widget packed inside it at a padding.
* Tk's default caret is 2 px (`insertwidth`); Windows draws 1.
* Plain `tk.Button`/`tk.Label` are the old flat Motif-ish widgets. `ttk` under the
  `vista` theme draws the native ones, and picks up the system font instead of a
  hardcoded "Segoe UI 10" that was a point too large.

DPI was ruled out as a cause: both monitors here report 96 DPI through
`GetDpiForMonitor`, so nothing is being bitmap-stretched.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk

from .. import APP_NAME
from ..enhance import credentials, spec
from ..enhance.registry import hosting
from ..i18n import t
from .screens import centre_on_active_monitor

log = logging.getLogger(__name__)

#: One spacing scale, so nothing is padded by feel. Window edge, between blocks,
#: inside a field.
PAD_WINDOW = 18
PAD_BLOCK = 8
PAD_FIELD = 6

FIELD_BG = "#ffffff"
FIELD_BORDER = "#7a7a7a"       # Windows 10 edit control, unfocused
FIELD_BORDER_FOCUS = "#0078d7"  # ...and focused
HINT_FG = "#666666"
ERROR_FG = "#a12d2d"


def _field(parent: tk.Misc, **pack: object) -> tk.Frame:
    """A white box with a 1 px border, to hold a borderless entry or listbox.

    `highlightthickness` rather than `relief`, because it draws exactly one pixel
    in a colour we choose - the 3D reliefs look a decade out of date next to the
    vista theme.
    """
    frame = tk.Frame(
        parent, bg=FIELD_BG, highlightthickness=1, highlightbackground=FIELD_BORDER,
    )
    frame.pack(**pack)  # type: ignore[arg-type]
    return frame


def _follow_focus(field: tk.Frame, widget: tk.Widget) -> None:
    """Border turns blue while the widget inside has focus, as native fields do."""
    widget.bind(
        "<FocusIn>", lambda _e: field.configure(highlightbackground=FIELD_BORDER_FOCUS), add=True
    )
    widget.bind(
        "<FocusOut>", lambda _e: field.configure(highlightbackground=FIELD_BORDER), add=True
    )


def _shell(root: tk.Tk, title: str) -> tuple[tk.Toplevel, ttk.Frame]:
    """A dialog window and its padded content frame, positioned and themed."""
    window = tk.Toplevel(root)
    window.title(title)
    window.resizable(False, False)
    # Only when the parent is actually on screen. The app's root is withdrawn, and
    # a transient of an unmapped parent never gets mapped itself - the window
    # stayed 1x1 at 0,0 until this was made conditional. Same guard
    # tkinter.simpledialog uses, for the same reason.
    if root.winfo_viewable():
        window.transient(root)

    frame = ttk.Frame(window, padding=PAD_WINDOW)
    frame.pack(fill="both", expand=True)
    return window, frame


def _show(root: tk.Tk, window: tk.Toplevel) -> None:
    """Map the window, wait until it is really on screen, then take the grab.

    Order matters: grabbing an unmapped window silently does nothing. Centring
    happens first, so the window does not appear at one place and jump.
    """
    centre_on_active_monitor(window)
    window.deiconify()
    window.update_idletasks()
    window.wait_visibility()
    window.grab_set()
    window.lift()
    root.wait_window(window)


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

    window, frame = _shell(root, t("dialog.suggestion.title", app=APP_NAME))

    counter = f"  ({index}/{total})" if total > 1 else ""
    ttk.Label(
        frame, text=t("dialog.suggestion.corrected", counter=counter), anchor="w",
    ).pack(fill="x")

    # The system UI font, emphasised - not a hardcoded family, which is how the
    # old dialogs ended up a point larger than every other window on the desktop.
    bold = tkfont.nametofont("TkDefaultFont").copy()
    bold.configure(weight="bold")
    ttk.Label(frame, text=f"{found.heard}   ->   {found.corrected}", font=bold).pack(
        pady=PAD_BLOCK
    )

    ttk.Label(frame, text=t("dialog.suggestion.explain"), justify="left", anchor="w").pack(
        fill="x"
    )

    field = _field(frame, fill="x", pady=(PAD_BLOCK, 0))
    entry = tk.Entry(field, bd=0, highlightthickness=0, bg=FIELD_BG, insertwidth=1, width=34)
    entry.pack(fill="x", padx=PAD_FIELD, pady=PAD_FIELD // 2)
    entry.insert(0, suggested)
    entry.select_range(0, "end")
    _follow_focus(field, entry)

    if suggested != found.corrected:
        ttk.Label(
            frame,
            text=t("dialog.suggestion.inflected", corrected=found.corrected),
            foreground=HINT_FG,
            anchor="w",
        ).pack(fill="x", pady=(PAD_FIELD, 0))

    ttk.Label(
        frame, text=t("dialog.suggestion.priming"), foreground=HINT_FG, anchor="w",
    ).pack(fill="x", pady=(PAD_FIELD, 0))

    def finish(action: str) -> None:
        outcome["action"] = action
        outcome["term"] = entry.get()
        window.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(PAD_BLOCK * 2, 0))
    ttk.Button(buttons, text=t("dialog.button.add"), width=12,
               command=lambda: finish("add")).pack(side="left")
    ttk.Button(buttons, text=t("dialog.button.skip"), width=16,
               command=lambda: finish("skip")).pack(side="left", padx=PAD_FIELD)
    ttk.Button(buttons, text=t("dialog.button.never"), width=22,
               command=lambda: finish("never")).pack(side="left")

    entry.focus_set()
    window.bind("<Return>", lambda _e: finish("add"))
    window.bind("<Escape>", lambda _e: finish("stop"))
    # Closing with the X means "decide later", like Escape.
    window.protocol("WM_DELETE_WINDOW", lambda: finish("stop"))

    _show(root, window)
    return outcome["action"], outcome["term"]


def review_vocabulary_suggestions(root: tk.Tk, controller) -> None:  # noqa: ANN001
    """Walk the pending suggestions, one at a time."""
    pending = controller.pending_vocabulary
    if not pending:
        root.attributes("-topmost", True)
        try:
            messagebox.showinfo(APP_NAME, t("dialog.suggestion.none"), parent=root)
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
    """Add and remove proper nouns, one at a time.

    This used to be a single `askstring` pre-filled with the whole list as one
    comma-separated line. It worked at three names and stopped working at ten:
    nothing is readable in a 400-character text field, removing an entry means
    editing around commas, and the obvious keystroke - Enter - saves whatever
    state the line happens to be in. The list is data; it wants a list widget.

    So: type a name, press Enter, it moves to the list below and the field
    clears. Select one, press Remove, it is gone. Nothing to parse by hand.
    """
    from .. import vocabulary

    def private() -> tuple[str, ...]:
        return vocabulary.terms(controller.config.get("transcription.vocabulary", ""))

    window, frame = _shell(root, t("dialog.vocabulary.title", app=APP_NAME))

    ttk.Label(frame, text=t("dialog.vocabulary.intro"), justify="left", anchor="w").pack(
        fill="x"
    )

    # -- add ------------------------------------------------------------
    ttk.Label(frame, text=t("dialog.vocabulary.add_label"), anchor="w").pack(
        fill="x", pady=(PAD_BLOCK * 2, PAD_FIELD // 2)
    )

    add_row = ttk.Frame(frame)
    add_row.pack(fill="x")
    entry_field = tk.Frame(
        add_row, bg=FIELD_BG, highlightthickness=1, highlightbackground=FIELD_BORDER,
    )
    entry_field.pack(side="left", fill="x", expand=True)
    entry = tk.Entry(
        entry_field, bd=0, highlightthickness=0, bg=FIELD_BG, insertwidth=1, width=30,
    )
    entry.pack(fill="x", padx=PAD_FIELD, pady=PAD_FIELD // 2)
    _follow_focus(entry_field, entry)
    ttk.Button(
        add_row, text=t("dialog.vocabulary.button_add"), width=10, command=lambda: submit(),
    ).pack(side="left", padx=(PAD_FIELD, 0))

    ttk.Label(
        frame, text=t("dialog.vocabulary.add_hint"), foreground=HINT_FG, anchor="w",
    ).pack(fill="x", pady=(PAD_FIELD // 2, 0))

    # -- the list -------------------------------------------------------
    count_label = ttk.Label(frame, anchor="w")
    count_label.pack(fill="x", pady=(PAD_BLOCK * 2, PAD_FIELD // 2))

    list_field = _field(frame, fill="both", expand=True)
    listbox = tk.Listbox(
        list_field, height=8, width=32, bd=0, highlightthickness=0, bg=FIELD_BG,
        activestyle="none", exportselection=False,
    )
    listbox.pack(side="left", fill="both", expand=True, padx=(PAD_FIELD, 0), pady=PAD_FIELD // 2)
    scrollbar = ttk.Scrollbar(list_field, command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="left", fill="y", padx=(PAD_FIELD // 2, 0))

    remove_button = ttk.Button(
        frame, text=t("dialog.vocabulary.button_remove"), command=lambda: drop(),
    )
    remove_button.pack(anchor="w", pady=(PAD_BLOCK, 0))

    shared_label = ttk.Label(frame, foreground=HINT_FG, anchor="w", justify="left")
    shared_label.pack(fill="x", pady=(PAD_BLOCK, 0))

    # Always present, so a message about a duplicate does not resize the window
    # under the user's hands while he is typing the next name.
    status = ttk.Label(frame, foreground=ERROR_FG, anchor="w", text=" ")
    status.pack(fill="x", pady=(PAD_FIELD // 2, 0))

    ttk.Button(
        frame, text=t("dialog.vocabulary.button_close"), width=12, command=window.destroy,
    ).pack(anchor="e", pady=(PAD_BLOCK, 0))

    # -- behaviour ------------------------------------------------------

    def refresh() -> None:
        names = private()
        listbox.delete(0, "end")
        for name in names:
            listbox.insert("end", name)
        count_label.configure(text=t("dialog.vocabulary.your_names", count=len(names)))
        if not names:
            listbox.insert("end", t("dialog.vocabulary.empty"))
            listbox.itemconfigure(0, foreground="#999999")
        remove_button.configure(state="normal" if names else "disabled")

    def submit() -> None:
        term = entry.get().strip()
        if not term:
            return
        before = private()
        controller.accept_vocabulary(term)
        after = private()
        # add() collapses inflections, so "nothing changed" means "already known"
        # in some form. Saying so beats a silent no-op that reads as a broken button.
        status.configure(
            text=" " if after != before else t("dialog.vocabulary.duplicate", name=term)
        )
        entry.delete(0, "end")
        entry.focus_set()
        refresh()

    def drop() -> None:
        selected = listbox.curselection()
        if not selected or not private():
            return
        controller.remove_vocabulary(listbox.get(selected[0]))
        status.configure(text=" ")
        refresh()

    shared_count = len(vocabulary.terms(vocabulary.read_shared()))
    shared_label.configure(
        text=t("dialog.vocabulary.shared", count=shared_count, file=vocabulary.SHARED_FILE)
    )
    refresh()

    entry.focus_set()
    window.bind("<Return>", lambda _e: submit())
    window.bind("<Escape>", lambda _e: window.destroy())

    _show(root, window)


def manage_api_key(root: tk.Tk, provider: str) -> None:
    """Set, replace, or clear the API key for one provider.

    Native `simpledialog`/`messagebox` throughout: this is a sequence of questions
    with no list to show, and the stock dialogs already look like Windows.
    """
    provider_spec = spec(provider)
    title = t("dialog.key.title", app=APP_NAME, provider=provider)

    # The app has no visible window, so a dialog parented to the withdrawn root
    # opens behind whatever the user is looking at. Raise it deliberately.
    root.attributes("-topmost", True)
    try:
        if credentials.source(provider) != credentials.SOURCE_NONE:
            keep = messagebox.askyesnocancel(
                title,
                t("dialog.key.exists", source=credentials.describe_source(provider)),
                parent=root,
            )
            if keep is None:
                return
            if keep is False:
                removed = credentials.delete_api_key(provider)
                messagebox.showinfo(
                    APP_NAME,
                    t("dialog.key.removed") if removed
                    else t("dialog.key.nothing_to_remove"),
                    parent=root,
                )
                return

        key = simpledialog.askstring(
            title,
            t("dialog.key.prompt", label=provider_spec.label, hosting=hosting(provider)),
            show="*",
            parent=root,
        )
        if key is None:
            return  # cancelled

        try:
            credentials.set_api_key(provider, key)
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_NAME, t("dialog.key.save_failed", error=exc), parent=root)
            return

        messagebox.showinfo(APP_NAME, t("dialog.key.saved", provider=provider), parent=root)
    finally:
        root.attributes("-topmost", False)
