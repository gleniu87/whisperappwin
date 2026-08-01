# Handoff — state as of 2026-08-01 (session 4)

A between-sessions handover note. `README.md` describes how to use the app;
`docs/ARCHITECTURE.md` — why the code looks the way it does. This file says
**where we are and what comes next**.

---

## What this is

The Windows counterpart of the macOS application
[`jacek-gajewski-ice/whisper-app`](https://github.com/jacek-gajewski-ice/whisper-app)
(WhisperDictate) — hold-to-talk dictation, everything local apart from the optional
text clean-up.

The original is Swift + SwiftUI + AVFoundation and **has no chance of running on
Windows**; that was the first thing we established. We reproduce its *behaviour*,
not its code. The layer mapping is in `docs/ARCHITECTURE.md`.

The reference repo is cloned in an earlier session's scratchpad — if it is needed
again:
`git clone --depth 1 https://github.com/jacek-gajewski-ice/whisper-app`.
It has its own `CLAUDE.md` with Jacek's engineering notes — worth a look before
reproducing another feature.

## Environment

| | |
|---|---|
| Repo | `C:\claude_projects\whisperappwin` (git, branch `main`, 9 commits) |
| Venv | `.\.venv` (Python 3.12.7 from Anaconda as the base) |
| Hardware | i7-12700H, 64 GB RAM, RTX 3070 Ti Laptop (8 GB VRAM) |
| GPU | works: `large-v3-turbo @ cuda/float16`, ~29× realtime |
| Microphone | `audio.device = "Mikrofon (Anker PowerConf C200)"` |
| Tests | **378, all passing** — `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |

Running it: `.\run.ps1` (with a console) or `.\run.ps1 -Hidden` (tray only).
Diagnostics: `.\run.ps1 -Check`.

---

## NEXT STEP

**The repository is now English-only** (session 4). Code, comments, docstrings, log
messages, CLI output, README, `docs/`, PowerShell scripts, test names and
descriptions. Six categories of Polish stayed on purpose, because each is *data*
rather than the source language — see "Polish that stays" below before you
"finish the job" on any of them.

**The interface is bilingual and has Polish characters.** All UI text lives in
`whisperdictate/i18n.py`; the tray menu, the recording overlay and the dialogs speak
Polish or English, the switch is in the tray → *App language*, and the first run
takes the language from Windows (fallback: English). Details below.

Open items, in order of value:

0. **Click the new menu live.** Verified so far: rendering of the overlay and the
   proper-nouns window (screenshots — Polish characters come out correctly), the
   full menu tree in both languages (dumped programmatically) and 378 tests. I have
   **not** clicked the real tray menu, switched the language, or touched the master
   switch in a running app — that is the first thing to check.
   Incidentally: the user's `transcription.vocabulary` still holds `Anthropica` (an
   old entry, harmless — see below). I left it so he has something to try the new
   *Remove selected* on; his config is his data.

1. **Verify the rescue live.** *Copy last transcription* is implemented and covered
   by tests (including an integration test against the real clipboard), but has not
   been clicked in a running app. Scenario: dictate anything into a window with no
   text field (the desktop, say), then tray menu → *Copy last transcription* →
   `Ctrl+V` in an editor. While you are there, check `Win+V`: that entry **should**
   be visible there, a plain dictation **should not**.
2. **The Anthropic API is still untested** — still no key. The only provider without
   a single real call. When a key appears: `.\run.ps1 -SetApiKey anthropic`, then the
   same benchmark.
3. **Decide whether to enable clean-up permanently** and with which provider. The
   data for the decision is already gathered (table below) — only the user's choice
   is missing. Do not change the "disabled" default for him.
4. **`-Hidden` mode** (pythonw) and **`toggle` mode** — still untouched live.

`getpass` works correctly in a real PowerShell window. It does **not** work under Git
Bash (`!` in a Claude Code session goes through bash) — there it can print the key to
the screen, which is why `--set-api-key` reads from stdin when stdin is not a tty.

### What the benchmark settled

The test text (fillers, the self-correction `handler znaczy parser`, and the
identifier `user_id` the model should not touch):

```powershell
.\run.ps1 -Benchmark "no wiec yyy wez sprawdz czy ten handler znaczy ten parser user_id sie nie wywala na pustym stringu bo jakby mi sie wydaje ze tam jest blad"
```

- **DeepSeek latency from Poland: ~1.5 s** (flash 1.62 s, pro 1.48 s). That was the
  main question and the answer is favourable — 10× cheaper *without* paying in time.
- **Polish: good.** Both models cut the fillers, applied the self-correction and
  **left `user_id` untouched**. Pro keeps "Weź" (closer to the speaker's tone); flash
  removes it more often.
- **Claude CLI: take Sonnet.** Measured 5 times: haiku-4-5 19.8–60+ s (timing out
  twice), sonnet-5 steady at 4.2–5.7 s. The CLI's overhead dominates the model, so
  "smaller model = faster" does not hold here. The default CLI model was changed to
  Sonnet (`registry.py`).

---

## Done and verified live

- **End-to-end dictation** — works, confirmed by the user. Hotkey → recording →
  Whisper on the GPU → paste.
- **Hold-to-talk on right Alt** coexists with AltGr: the hook intercepts nothing,
  300 ms threshold, a letter mid-gesture cancels it. Modifiers are excluded from
  cancelling, because Windows sends a synthetic left Ctrl with AltGr.
- **Microphone selection in the tray**, saved by name (PortAudio indices shift),
  with a fallback when the device disappears.
- **Host-API fallback** — WASAPI → DirectSound → MME. Verified with WASAPI
  sabotaged: it drops to DirectSound and captures real audio.
- **LLM text clean-up** — verified live through the Claude Code CLI on a real
  dictation of the user's. It cut the fillers and preserved the meaning.
- **Tray menu** — every entry invoked programmatically the way pystray does it.

- **DeepSeek live** — both models, repeatedly, through the benchmark and through
  probes straight at the API. Works, ~1.5 s, correct Polish.
- **Clean-up switched on permanently by the user** (`enabled = true`,
  `claude_cli` / `claude-sonnet-5`) and tested on his real 938-character dictation.
  Sonnet repaired proper nouns Whisper had garbled (`Dipsick` → `Deepseek`,
  `sonet` → `Sonnet`) and split the utterance into paragraphs.
- **`--quality`** — a quality comparison over 12 difficult transcripts, run against
  all four ready provider/model combinations.
- **The tray menu was rebuilt** — Provider / Model / Style as separate submenus
  labelled with the current choice, plus a *Hotkey* submenu and a *Proper nouns...*
  entry. DeepSeek's model choice (flash/pro) existed before, but was lost in a flat
  list of nine entries — the user never found it. That was a UI defect, not a
  missing feature.
- **Polish characters in the recording overlay — verified at the pixel level.**
  Screenshots of the real Tk window show "Ładuję model...", "Czyszczę tekst..." and
  the detail `zażółć gęślą jaźń`. The script: create an `Overlay` on a withdrawn
  root, `set_state()`, a few `root.update()`, then `ImageGrab.grab()` at
  `winfo_rootx/rooty`. Tests do not catch this — it is the same gap through which,
  in session 2, a dialog stayed 1×1 in the corner of the screen.
- **Language detection on this machine**: `GetUserDefaultUILanguage()` → `0x415`
  (pl-PL) → `pl`. Matches expectation.

## NOT verified live (new in session 3)

- **The real tray menu after the changes** — labels checked programmatically and by
  tests, but never clicked. This applies especially to *App language*: switching it
  rebuilds the whole menu (`Tray._rebuild_menu`), and `update_menu()` on a live icon
  is the one path a test does not fully reproduce.
- **The notification balloon with Polish characters** — it goes through
  `Shell_NotifyIconW`, so it should be fine, but I have not seen it with my own eyes.

- **Whisper priming from the vocabulary** — the prompt half is measured (below), but
  the effect on **recognition** is not. It needs a recording. That is the first thing
  to check: enter the names, dictate a sentence containing "DeepSeek", compare
  `raw_text` in `history.jsonl` before and after.
- **The suggestion balloon after a dictation** — the code is there, but I have not
  seen it. It needs a real dictation in which the model repairs something.
- **Changing the hotkey at runtime** (`HotkeyListener.set_key`) — covered by unit
  tests, never clicked in a real tray.
- **The *Proper nouns...* dialog** — since session 3 **tested by screenshot**, not
  merely built: the script creates the window on a withdrawn root, types a name,
  sends `<Return>`, selects the entry, clicks *Remove selected* and grabs
  `ImageGrab.grab()` after each step. It can be done via `root.after()` before
  `wait_window()` — "untestable headless" was too strong. The logic (add/remove) has
  ordinary tests; this route is for inspecting the layout.

## NOT verified

- **The Anthropic API as a clean-up provider** — no key, never run live. The code
  path is the same as DeepSeek's (`MessagesApiProvider`), so the benchmark will cover
  it if a key appears. Note: `_THINKS_BY_DEFAULT` assumes Haiku 4.5 does not think
  unless asked — **that assumption is from the same family as the disproved one
  about DeepSeek**. Check it when there is a key.
- **`-Hidden` mode** (pythonw, no console) — untested.
- **The hotkey's `toggle` mode** — covered by tests, never used live.

---

## Decisions that are easy to undo by accident

Each one cost a diagnosis. Do not change them without a reason.

**Text clean-up is DISABLED by default.** As in the original (`Helpers.swift`:
`enhanceTranscription = false`). This is not an oversight.

**The user deliberately separates use cases:** DeepSeek for private projects only;
for work material either clean-up off or through the Claude Code CLI (it goes
through his subscription, no third party). That is why every provider declares its
jurisdiction (`registry.hosting()`, since session 3 from the translation catalogue),
visible in `--check`, in the key dialog and in the README. **Do not hide it.**

**`None` ≠ empty string in `EnhancementService.enhance()`.** `None` = "paste the raw
transcript" (a failure). Empty = the `EMPTY` sentinel = "that was pure noise, paste
nothing". Merging the two makes a dead API look like silence.

**A provider never returns an empty string — it raises `ProviderError`.** This is
the other half of the rule above, and it was only added after it happened live:
`deepseek-v4-pro` answered with nothing but a `thinking` block and
`stop_reason=end_turn`, i.e. formally a success. The code collapsed "no text blocks"
into `""`, which downstream means `EMPTY` — **the dictation would have vanished with
no trace in the log**. It applies to both providers: an API reply with no `text`
block, and empty `stdout` from the CLI with exit code 0. `EmptyReplyTest` and
`CliEmptyOutputTest` guard this.

**DeepSeek models DO think unless you forbid it.** An earlier comment in
`_THINKS_BY_DEFAULT` claimed otherwise — that was an assumption, not a measurement,
and it was false: `thinking` appeared in 4/4 probes. On `v4-flash` the reasoning ate
the entire 1024-token budget (`stop_reason=max_tokens`, zero text) in **100%** of
calls, so flash did not work at all. With `thinking: {"type": "disabled"}` — 6/6
clean replies in 35–44 tokens. Do not remove those models from the set.

**The default `claude_cli` model is Sonnet, not Haiku.** It looks like a mistake and
is not — see the measurements above. Reversing the order in `registry.py` would make
the default CLI choice the one that regularly exceeds the 60 s limit. The user
confirmed this choice explicitly.

**The default hotkey is right Ctrl, not right Alt.** The original had right Alt and
there were three defensive mechanisms for it (no interception, a 300 ms threshold,
cancel on a letter) — described in the README and still working. **They were not
enough in practice:** the user reported that typing "ą" starts a recording anyway.
The threshold catches quick presses, but not hesitation. For a key pressed dozens of
times per paragraph, "nearly always" is not enough. `alt_r` stayed as an option — on
the *Polish (214)* layout the problem does not arise. This change came from usage,
not from a measurement; do not revert it without talking to the user.

**The proper-noun vocabulary goes to TWO places, not one.**
`transcription.vocabulary` → Whisper's `initial_prompt` **and** a section in the
clean-up prompt (`vocabulary.py`). It is tempting to simplify this to one; do not.
Priming alone sometimes fails, and repairing after the fact alone leaves the garbled
text in `history.jsonl` and does nothing with clean-up switched off. The prompt
section carries an explicit ban on inserting names from the list — without it the
model puts them where they never were.

**A Tk window parented to a withdrawn root MUST call `transient()` conditionally.**
The app's `root` is withdrawn (there is no main window). A `Toplevel` with
`transient(root)` on such a root **never maps** — it stays 1×1 in the corner and the
user sees nothing. The order: `transient` only when `root.winfo_viewable()`, then
`deiconify()`, `wait_visibility()`, `grab_set()`. That is what `tkinter.simpledialog`
does, which is why those dialogs worked and mine did not. It only surfaced with a
screenshot — tests do not catch it.

**`add()` collapses inflected forms onto the base one.** Without it the vocabulary
ends up holding `Anthropic` and `Anthropica` side by side — which really happened. A
difference containing a space (`Claude` vs `Claude Code`) is another word, not an
ending, and must be added.

**Vocabulary suggestions NEVER add themselves.** The threshold is a single
occurrence — and it is safe only because the user is the filter. If entries landed in
the vocabulary without confirmation, a wrong entry would reach `initial_prompt` and
spoil **every** future dictation, and do it at the source. Do not "improve" this into
automatic adding.

**The prompt's vocabulary section says to inflect "po polsku" and it stays that
way.** It looks like a leftover from when the app was Polish-only — the user also
dictates in English, so the instruction makes no sense there. **I rewrote it
language-neutral and measured it: it is worse.** Polish dropped from 14/16 to 9/16 on
restoring `Sonnet` (deepseek-v4-flash, same text); English was 8/8 in both versions.
English never needed the fix, because `language_directive` is appended AFTER this
section and overrides it. Reverted. `test_section_keeps_the_polish_declension_cue`
guards this. Do not "fix" it without repeating the measurement.

**The vocabulary has two sources and they are MERGED, not substituted.**
`vocabulary.txt` in the repo (version-controlled technical names) +
`transcription.vocabulary` in `%APPDATA%` (private: clients, projects, people —
outside git). Every read of the vocabulary goes through `vocabulary.combined()`. If
you ever see a bare `config.get("transcription.vocabulary")` on a path that feeds
Whisper or the prompt, that is a bug — it loses half the list.

**One vocabulary for both languages, names in base form.** The model inflects them
itself (`DeepSeek` → "na DeepSeeka" in Polish, "to DeepSeek" in English). Two lists
cannot sensibly be done: `transcription.language` accepts `auto`, so under
auto-detection there is no way to know which one to pick before transcription.

**`_fold()` maps `ł` → `l` by hand.** This is not over-engineering: `ł` (U+0142) is
an atomic codepoint **with no NFD decomposition**, unlike ą, ć, ę, ń, ó, ś, ź, ż.
Without that map "ustawilem" and "ustawiłem" compare as different words, which knocks
the sequence alignment out of step and loses the real repair in the same sentence. It
cost three failing tests before I saw it.

**The default DeepSeek model is Flash — at the user's explicit request.** The reason
he gave (Pro being slower) **was not borne out by the measurements**: Pro came out at
1.54 s against Flash's 1.60 s, i.e. faster. I told him so; the Flash choice is
reasonable anyway on token grounds and it is his decision. Do not change it, but do
not repeat the "Pro is slower" rationale either — it is untrue.

**On quality Pro > Flash, even though both score 0 errors in a single run.** The
difference only shows on ×5 repetitions: Flash returned the same proper noun as
`DeepSick` / `Deepsika` / `DeepSeeka` and once replaced a meaningful "jakby" with
"jakieś" (4/5); Pro gave the same result five times. **A single `--quality` run does
not settle a model** — for that kind of comparison, repeat the hardest cases.

**`CredWrite` wants `str`, `CredRead` returns `bytes`.** The API is asymmetric.
Verified empirically, covered by `tests/test_credentials.py` (an integration test — a
mock would not have caught it).

**pystray menu actions are closures, never `lambda x=value:`.** pystray picks how to
invoke by `__code__.co_argcount`, and a default argument counts — such a lambda gets
an `Icon` object instead of the value. `tests/test_tray_menu.py` guards this.

**Opening an audio stream must include `start()`.** WASAPI validates lazily:
`InputStream(...)` succeeds and `.start()` rejects. That was a real production bug
(`118fe29`).

**The tray menu has no status line and is not to get one back.** It was a single
inactive entry carrying the state, the model and the provider in one sentence.
Windows stretches a popup to its longest entry, so with clean-up enabled it turned
the menu into a band across half the screen — reported by the user. Everything it
said is elsewhere: the state in the icon colour and the tooltip, the choices in the
submenu labels (`Model: {name}`, `Hotkey: {key}`, `Provider: {name}`).
`test_the_menu_carries_no_status_line` and
`test_no_top_level_entry_is_absurdly_wide` guard this.

**The master switch is the inverse of `paused` and that is deliberate.** The menu
says "Enable dictation", checked while it is running; the controller says `set_paused`
/ `State.PAUSED`. Do not unify one side to the other: the machine state is called
"paused" and the menu offers to switch something *on* — two halves of the same fact,
each readable on its own side. The switch does **not** unload the model or remove the
hook; that is what the user asked for ("so I don't have to close the app"). The old
"Pause dictation" entry is gone — two toggles for one state, in opposite phases, was
a trap.

**Proper-noun vocabulary: add one at a time, not one comma-separated line.** The
previous window was an `askstring` prefilled with the whole list as a single string.
With three names it worked; with ten it stopped: nothing is visible in a field
holding 400 characters, removing an entry means editing around commas, and Enter
saves whatever happens to be on the line. The user raised it himself ("it'll balloon
like that"). Now: type a name → Enter → it lands in the list and the field clears;
select → *Remove selected*. The window shows **only the private half**, because
`vocabulary.txt` is version-controlled and a dialog has no business making a commit
for the user — below the list is a count of the shared names and the file name, so it
is clear where the rest come from.

**`vocabulary.remove()` must match the way `add()` matches.** `add()` collapses
`Anthropica` onto `Anthropic`; if `remove("Anthropic")` left the inflected copy
behind, the name would come back into priming after the user removed it.
`test_removing_the_stem_takes_its_inflections_too` guards this.

**Where `Anthropic` in the window and `Anthropica` in the config came from
(session 3).** A real report, worth knowing both sources. `Anthropic` is in
`vocabulary.txt` (the shared file, seeded by me in session 2) — it goes to priming,
but does **not** show in the window, because the window shows only the private half.
`Anthropica` sat in `transcription.vocabulary` in `%APPDATA%`: written by the UI (the
log line, then `Slownik nazw wlasnych: 1 pozycji`, now
`Proper-noun vocabulary: 1 entries`), with `vocabulary_rejected` empty, i.e. **not**
via "never ask about this". Most likely Enter in the suggestion dialog — `<Return>`
is bound there to "Add", and the button is `default="active"`. I left it that way:
the dialog is opened from the menu deliberately and "add" is the right default
action, and since session 3 a wrong entry can be removed with one click. The entry
itself was harmless — `combined()` collapsed it onto `Anthropic` from the shared file
(verified with a probe), so it never reached Whisper.

**`refresh_vocabulary_suggestions()` catches exceptions from the whole scan, not
just from reading the file.** `history.recent()` returns whatever `json.loads` made
of each line — a damaged history can parse into a number or a list, on which
`pending()` raises `AttributeError`. This runs from `_offer_vocabulary` at the very
end of a successful dictation, so the text would paste and a moment later the overlay
would show "internal error". Found while writing a test, not live.

**Dictations are excluded from the Windows clipboard history, and that is not the
same as `restore_clipboard`.** The user reported the clipboard filling up even though
`restore_clipboard = true` has restored the previous contents from the start.
Restoring **does not remove the Win+V entry** — Windows records every change at the
moment it happens. That is why `output.clipboard_history = false` (the default) adds
two registered formats to the clipboard, `CanIncludeInClipboardHistory` and
`CanUploadToCloudClipboard`, both as DWORD 0 — the same mechanism password managers
use. They must be set **in the same clipboard session** as the text (between
`EmptyClipboard` and `CloseClipboard`), or they apply to nothing. The restored
contents are marked the same way, because otherwise every dictation would add a
duplicate of the user's entry to Win+V. Verified against the real clipboard
(`tests/test_output.py`, an integration test — a mock would have accepted a DWORD
written backwards and nobody would have noticed).

**Do not swap pasting for typing character by character.** Considered and rejected
together with the user when he asked about switching the clipboard off: with
`SendInput` every newline in the transcript becomes an Enter, which sends a
half-finished message in Slack and Teams; 900 characters take noticeably longer to
type than to paste; and applications' autocomplete gets in the way. Excluding it from
the history gives what he was after (a clean Win+V) without any of those risks.

**The recording overlay is positioned by Win32, not Tk.** `winfo_screenwidth()` is
the **primary** monitor, and the origin of Tk's coordinate system is always 0,0 — so
on two monitors the overlay always came out on the primary one, regardless of where
the user was typing. Now: `GetForegroundWindow` →
`MonitorFromWindow(MONITOR_DEFAULTTONEAREST)` → `rcWork`. Measured on this machine:
the active window on monitor `(-1920, 0, 0, 1040)`, i.e. **to the left of the
primary** and with a taskbar; the old code gave `x=810` (the primary monitor), the
new one `x=-1110`. Tk accepts a negative offset written as `+-1920+100` and puts the
window there literally — checked via `winfo_rootx()`, not assumed. The arithmetic
sits in `position_in()`, apart from the Win32 calls, so it can be tested; `rcWork`
instead of `rcMonitor`, to clear the taskbar on that particular screen. The position
is recomputed every time the overlay is **shown**, not while it is up — otherwise the
window would chase the active window across screens mid-recording.

**The history does NOT grow without bound, and do not bolt time-based deletion onto
it.** The user asked, reasonably, whether `history.jsonl` would grow to gigabytes. It
will not: `history.py` trims the file to `history.max_entries` (5000 by default)
every 100 appends, and has since the first version. Measured on his real data: median
entry 667 B, mean 871 B, max 3165 B → a **ceiling of ~4.2 MB** at the default limit
(2000 → ~1.7 MB, 1000 → ~0.83 MB), and parsing the full 5000 entries takes 23 ms.
Before you add date-based deletion or a settings window, repeat that measurement — at
these numbers it would be code without a reason. **Do not take the limit below
~1000:** after every dictation the last 300 entries are scanned
(`controller._SUGGESTION_WINDOW`) and `--suggest-vocabulary` reads 1000, so a smaller
limit cuts the vocabulary suggestions, not just the file size.

**"Copy last transcription" is deliberately NOT excluded from Win+V.** The opposite
of a plain dictation (`clipboard_history = false`): here the user is copying
deliberately, so an entry in the clipboard history is what he wants. Two tests
looking in opposite directions guard this —
`test_rescue.py::test_and_is_visible_to_the_clipboard_history` and
`test_output.py::test_deliver_excludes_by_default`.

**A rescue instead of a pre-paste guard — a decision, not an omission.** The user
asked about a warning: "you have no text field focused, the text will vanish". That
cannot be established reliably in Electron (Teams, VS Code, the browser): the whole
window is one HWND and `hwndCaret` is usually empty. A warning that throws false
alarms in the most frequently used applications stops being read, so instead of
fortune-telling beforehand there is recovery afterwards from `history.jsonl`. If you
ever reach for UI Automation (`GetFocusedElement`, `IsTextPatternAvailable`),
**measure** it on Teams and VS Code first, not on Notepad.

**`_last_transcription()` refuses when the history is disabled, instead of reading
the file.** `History.append` does nothing when `enabled = False`, so the newest line
is from whenever recording was last on. Offering that as "the last transcription" is a
wrong answer dressed as a right one.

**Window appearance: `tk.Entry` and `tk.Listbox` have NO `padx`/`pady` options.**
Checked: `'padx' in widget.keys()` → `False` for both. That is why the field is a
`tk.Frame` with `highlightthickness=1` (white, with a 1-pixel border in the chosen
colour) and the borderless widget sits inside it with padding. Do not "simplify" this
back to a bare `Entry` — the user reported exactly that symptom: the cursor and text
glued to the edge. On top of that `insertwidth=1` (Tk defaults to 2, Windows draws 1)
and `ttk` instead of `tk` for buttons and labels, so the `vista` theme gives a native
look and the system font instead of a hard-coded "Segoe UI 10". Measured after the
change: **cursor 1 px, gap from the field's edge 7 px** (was 2 px and 0 px). **DPI was
not the cause** — `GetDpiForMonitor` after `SetProcessDpiAwareness(2)` in a separate
process returns 96 for both monitors.

**The status row in the proper-nouns window always has `text=" "`.** It looks like an
oversight and is not: the duplicate message appears while typing, and an empty
`text=""` would collapse the row and shift the window under the user's hand.

**Monitor geometry lives in `ui/screens.py`, not `overlay.py`.** Two things need it
(the recording overlay and the dialogs), and `dialogs` has no reason to import the
whole overlay. `position_in()` for the overlay, `centre_in()` for windows — both
pure, next to the impure `focused_work_area()`.

**The prompt contains Polish filler vocabulary.** A prompt in English cuts "um" and
leaves "no więc yyy" untouched. That is the core, not a translation.

**The missing Polish characters in the tray were NOT a font or encoding problem.**
That is how the user reported it and how it looked, but the cause was bare strings in
the source: `"Laduje model"`, `"Nazwy wlasne..."`. pystray on Windows uses
`InsertMenuItemW`, `Shell_NotifyIconW` and `MENUITEMINFO.dwTypeData` as `LPCWSTR` —
all wide-character (checked in the package's source). Tk draws diacritics with no
configuration at all (checked by screenshot). **If you ever see "?" instead of "ą"
again, look in the string, not in the font.**

**`ui.language` and `transcription.language` are two separate settings.** It is
tempting to merge them; do not. Dictating in English with a Polish menu is normal,
and `transcription.language` additionally accepts `auto`, which is meaningless for
the interface. Both switches sit **next to each other in the menu on purpose** — a
single "Language" entry is exactly what used to confuse one with the other.

**The log and the CLI are in ENGLISH — written directly, not through `i18n`.**
Reversed by the user in session 4. An earlier version of this note said they stayed
Polish and called it "a boundary, not an oversight"; that decision no longer holds, so
do not restore Polish here citing it. The reasoning that survives is only about
*where* the strings live, not what language they are in: log and CLI strings are
written in English at their call sites and must **not** be moved into the translation
catalogue, which would double its size with no user ever reading it.
`whisperdictate/i18n.py` is for the GUI — the tray, the overlay, the dialogs — and
that is its documented scope. The exception remains exception messages that **reach a
balloon** (`AudioError`, `TranscriptionError`, `ClipboardError`, `fallback_note`):
those are translated, because the user sees them. `ProviderError` is English and not
translated, because it goes only to the log.

**The one-shot CLI commands force `i18n.use("en")`, and that line is load-bearing.**
`main()` calls `i18n.use(i18n.FALLBACK)` when `_is_one_shot(args)`, and honours
`ui.language` only for `run_app()`. Reason: a handful of values the CLI prints come
from helpers shared with the GUI — `credentials.describe_source()` and
`registry.hosting()` — which legitimately go through the catalogue. Caught live: with
`ui.language = "pl"`, `--check` printed `key deepseek  Menedżer poświadczeń Windows ->
DeepSeek (Chiny) - nie używać do treści służbowych` in the middle of otherwise English
output. Do not "simplify" `main()` back to a single unconditional
`i18n.use(config.get("ui.language"))`.

**`t()` takes `key` as a positional-only parameter (`def t(key, /, **fields)`).**
Not decoration: the `menu.hotkey` entry has a `{key}` placeholder, so without the `/`
the call `t("menu.hotkey", key=...)` fails with "got multiple values for argument
'key'". Tests caught it on the first run. Do not "simplify" the signature.

**The *App language* menu shows endonyms ("Polski", "English") in both
catalogues.** It looks like an unfinished translation and is not: someone who
switched to a language they cannot read has to be able to get back.

**`credentials.source()` returns `env` / `store` / `none`, not a sentence.** The key
dialog branches on that value, and you cannot branch on a translated sentence. For
showing a human there is `describe_source()`.

**`ProviderSpec` no longer holds `hosting` or the hint in `label`.** What is left is
the bare product name ("DeepSeek API"), because that is identical in every language.
The hint and the jurisdiction come from `registry.label_with_hint()` and
`registry.hosting()`. The jurisdiction declaration **is still visible** in `--check`,
in the key dialog and in the README —
`test_hosting_note_flags_the_jurisdiction_in_both_languages` guards it, checking
"Chiny" in Polish and "China" in English.

**The first run asks Windows for the *display language*, not the region.**
`GetUserDefaultUILanguage()`, not `locale.getlocale()`. That answers the question
"what language does this person read software in"; the region is a formats setting and
on an English Windows with a Polish region it would give Polish, even though the
person deliberately set both. The locale is consulted **only** when the display
language cannot be read (non-Windows, no ctypes).
`test_the_display_language_outranks_the_locale` settles this.

**`i18n.py` imports nothing from the package and is to stay that way.** `config`,
`audio`, `transcriber`, `output` and `enhance/` all read from it — one import in the
other direction makes a cycle in the place where it is hardest to notice.

**The detected language is written to the config, not guessed at every start.** A
fresh config gets it immediately; a config from a previous version (with no
`ui.language`) gets a one-off write on the next start. That way the file shows what
the app is doing. `test_a_config_that_already_has_the_setting_is_not_rewritten` keeps
this from turning into a write on every load.

**Tray tests MUST pin `ui.language`.** A fresh config takes the language from
Windows, so on an English machine every assertion on a Polish label would fail.
`setUp` sets `pl` and restores the previous language via `addCleanup` — the language
is module state, and one of the tests deliberately switches it.

**Do not create `pystray.Icon` objects in tests that nobody holds.** pystray names
the Win32 window class `"<name><id(icon)>SystemTrayIcon"` and unregisters it only for
an icon that actually *ran* — never in tests. A collected icon frees its address, a
new one can land on it, get the same class name and fail with
`ERROR_CLASS_ALREADY_EXISTS` — in a random test, not the one that caused it. It cost
one false diagnosis. That is why the second tray in
`test_configured_but_absent_device_...` hangs off `self._extra_tray`.

---

## Polish that stays, and why (session 4)

The repository is English. These six are **data**, not the source language;
translating any of them undoes measured behaviour. A detector run over the repo will
flag them — that is expected, and the list below is the adjudication.

1. **`whisperdictate/i18n.py` — the `"pl"` half of every `MESSAGES` entry.** This is
   a product feature (a Polish interface), added at the user's explicit request; he
   runs with `ui.language = "pl"`. Comments in that file are English; the `"pl"`
   values stay. Removing Polish from the UI means dropping `"pl"` from
   `UI_LANGUAGES` and half the catalogue with it — **ask, do not assume**.
2. **`whisperdictate/enhance/prompts.py` — the body of the clean-up prompt.** It
   carries Polish filler vocabulary ("no więc", "yyy", "jakby"). A prompt written in
   English cuts "um" and leaves "no więc yyy" untouched. The core, not a translation.
3. **`vocabulary.py::prompt_section()` — the "odmień naturalnie po polsku" cue.**
   Rewriting it language-neutral was measured and came out worse: Polish dropped from
   14/16 to 9/16 on restoring `Sonnet`. `test_section_keeps_the_polish_declension_cue`
   guards it.
4. **`vocabulary.py` — the Polish grammatical data**: `_ENDINGS` (owi, iem, em, a, u)
   and `_ATOMIC` (ł→l). Comments around them are English; the values stay.
5. **`enhance/quality.py` — the case corpus** (`Case.text`, `must_keep`,
   `must_drop`, `must_not_contain`). This is the Polish dictation corpus that
   exercises the Polish prompt. The `why` fields are descriptions and are English;
   the violation strings reach CLI output and are English, with `test_quality.py`
   updated to match.
6. **`vocabulary.txt` — the proper nouns themselves** are language-neutral and stay;
   the comment header at the top of the file is English.

Two further categories the detector flags, adjudicated the same way and left alone:

- **Real Windows device names** in comments and test fixtures
  (`"Mikrofon (Anker PowerConf C200)"`, `"Zestaw mikrofonow (Technologia "`). These
  are strings the OS produces, and the MME truncation test depends on that exact
  31-character prefix. Translating them would test nothing real.
- **Polish hallucination patterns** in `postprocess.py` and the Polish payloads in
  `test_postprocess.py` / `test_quality.py`. These are the literal strings Whisper
  emits on silence for a Polish model; translated, they would stop matching.

The detector used for the sweep was a throwaway script (regex over Polish diacritics
plus a word list, across `*.py`, `*.md`, `*.ps1`, `*.toml`, `*.txt`, skipping `.venv`
and `__pycache__`). It over-matches on English words like "model", "to" and "test" —
treat its output as a worklist, not a verdict.

---

## Measured numbers (do not guess again)

| | |
|---|---|
| Whisper `large-v3-turbo` on CUDA | ~29× realtime, loading from cache 2.9 s |
| Claude Code CLI, trivial prompt | 6.1 s |
| **DeepSeek V4 Pro**, full prompt, from Poland | **1.48 s** |
| **DeepSeek V4 Flash**, full prompt, from Poland | **1.62 s** |
| **Claude CLI + Sonnet 5**, full prompt | **4.2–5.7 s** (5 runs, steady) |
| **Claude CLI + Haiku 4.5**, full prompt | **19.8–60+ s** (5 runs, 2× timeout) |
| 938-character dictation: Sonnet / Flash | 10.2 s / 3.7 s — time grows with length |
| `--quality`, 12 cases, errors | pro 0, flash 0, haiku 0, sonnet 1 |
| `--quality`, average time per case | pro 1.54 s, flash 1.60 s, sonnet 4.25 s, haiku 20.9 s |
| Vocabulary in the prompt, flash, `Sonnet` correct | **0/3 without → 2/3 with the vocabulary** |
| Vocabulary in the prompt, flash, `DeepSeek` correct | 2/3 → 3/3 (small sample, direction clear) |
| Vocabulary candidates from 22 dictations | 1, correct (after filtering out the `alta`→`Altu` inflection) |
| Haiku 4.5 | $1 / $5 per 1M, ~$0.0026 per dictation |
| DeepSeek V4 Flash | $0.14 / $0.28 per 1M, ~$0.00025 per dictation |
| System prompt `default` + the `pl` lock | 3425 characters, ~1150 input tokens |
| Min. cacheable prefix for Haiku 4.5 | 4096 tokens — our prompt is ~1100, **the cache does not engage** |
| Anker PowerConf, WASAPI | accepts **only** 48 kHz; MME/DirectSound take anything |

## Environment traps

- The user's default Windows audio input is **"Virtual Desktop Audio"** — a virtual
  device that records silence. Hence the need to pick a microphone explicitly.
- Windows exposes 3 physical microphones as **26 PortAudio entries** through 4 host
  APIs. The menu shows WASAPI only.
- `!` in a Claude Code session goes through **bash**, not PowerShell — backslashes
  are lost.
- The session's working directory resets between tool calls; use absolute paths or
  `Push-Location`.
- Do not generate code containing `\x00` escape sequences through a heredoc — real
  NUL bytes have ended up in a file instead of the escape text. Build such values in
  code (`(0).to_bytes(4, "little")`).
- git warns `LF will be replaced by CRLF` in this repo; that is normal, ignore it.

## Possible directions (nothing is promised)

- A dashboard window (the original has 8 tabs). The core imports nothing from `ui/`
  — this is a third implementation of the `UiSink` protocol.
- More clean-up providers (OpenAI, Ollama). `Provider` has one method.
- Statistics from the history — `history.jsonl` already carries `audio_seconds`,
  `elapsed_seconds`, `model`, `language`, `raw_text`.

## How to work with this user

He verifies and catches loose ends — rightly. Three things that have worked:

- **Measure, do not guess.** Several times my assumption ("the CLI is 6 s", "that's
  my bug") was wrong, and only the log or a probe settled it. The application log
  (`%APPDATA%\WhisperDictateWin\whisperdictate.log`) settled two diagnoses. In
  session 2 the same thing recurred in a worse form: **a confident comment in the
  code** ("DeepSeek models do not think unless asked") was an invention that blocked
  the whole provider. A comment describing someone else's API behaviour is a
  hypothesis — a 20-line probe printing the raw response settled in a minute what the
  benchmark could not show. The console showed only "FAILED"; the log and the probe
  showed it was two different bugs, one of which quietly lost text.
- **Do not narrow his choices.** I proposed only Anthropic providers and he rightly
  questioned it — DeepSeek is 10× cheaper. When asked about options, include ones off
  the obvious path.
- **Say plainly when something is unverified.** He values "I have not verified this"
  more than a smooth summary.
