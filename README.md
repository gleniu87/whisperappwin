# WhisperDictate for Windows

> **A Windows port of [WhisperDictate](https://github.com/jacek-gajewski-ice/whisper-app)
> by Jacek Gajewski.** The original runs on macOS only. This is a reconstruction of
> its behaviour on Windows — the idea, the design and the product decisions come
> from there.

Local *hold-to-talk* dictation: hold a key, speak, let go — the text is pasted
wherever your cursor is. Everything runs on your own machine; nothing goes to the
cloud.

## Relationship to the original

**This is not a fork and not a code translation — it is an independent
implementation of the same behaviour.** There was no other option: the original is
Swift + SwiftUI + AVFoundation + Core Audio, frameworks that simply do not exist on
Windows. Not a single line of code was carried over, because there was nothing to
carry.

What comes from the original:

- **The whole product idea** — hold-to-talk, local processing, a tray icon instead
  of a window, pasting into the active application.
- **Product decisions**, including the non-obvious ones: LLM text clean-up
  **disabled by default** (`Helpers.swift`: `enhanceTranscription = false`), the
  `EMPTY` sentinel for silence, fail-soft to the raw transcript.
- **The construction of the clean-up prompt** — `Enhancement/CustomPrompt.swift`,
  which in turn credits [FreeFlow](https://github.com/zachlatta/freeflow)
  and [VoiceInk](https://github.com/Beingpax/VoiceInk).

What is local to this port, because it had to be:

| | original (macOS) | here (Windows) |
|---|---|---|
| Engine | whisper.cpp | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2 |
| Audio | AVFoundation / Core Audio | PortAudio via `sounddevice`, falling back WASAPI → DirectSound → MME |
| UI | SwiftUI | pystray + Tk |
| Hotkey | Carbon / NSEvent | pynput, handling the AltGr clash on the Polish layout |
| API keys | Keychain | Windows Credential Manager |

A few things were added that the original does not have, because Windows or
working in Polish demanded them: microphone selection by name (PortAudio indices
shift), Polish filler vocabulary in the prompt, a proper-noun list feeding both
Whisper and the clean-up model, and the DeepSeek and Claude Code CLI providers.

On an NVIDIA GPU faster-whisper is faster than whisper.cpp; on CPU it is
comparable.

## Requirements

| Requirement | Specification |
|---|---|
| OS | Windows 10 1809+ / Windows 11 |
| Python | 3.10 or newer |
| GPU (optional) | NVIDIA with ≥6 GB VRAM for `large-v3-turbo` |
| Disk | ~1 GB for dependencies + ~1.6 GB for the model |

It works without a GPU too — on a modern CPU `large-v3-turbo` transcribes at
roughly the pace of speech, and the smaller models (`small`, `medium`) are much
faster.

## Installation

```powershell
git clone <repo-address> C:\claude_projects\whisperappwin
cd C:\claude_projects\whisperappwin
.\setup.ps1
```

`setup.ps1` creates a local `.venv`, installs the dependencies, detects an NVIDIA
card and pulls the matching cuBLAS/cuDNN libraries, then runs the diagnostics.
Force CPU mode with `.\setup.ps1 -Cpu`.

The first run downloads the model (~1.6 GB) from Hugging Face into
`%LOCALAPPDATA%\WhisperDictateWin\models`. Later runs start from disk.

## Usage

```powershell
.\run.ps1              # with a console (you see the logs)
.\run.ps1 -Hidden      # in the background, tray icon only
```

**Hold right Ctrl, speak, let go.** The text appears in the active window.

The tray icon shows the state by colour — grey (ready), red (recording), blue
(transcribing), yellow (loading the model), dark grey (disabled). From its menu you
can switch the dictation language, the app language, the model and the
**microphone**, and open the config, the history or the log.

At the top of the menu is **Enable dictation** — the master switch. Unchecked: the
hotkey stops working and any recording in progress is dropped, but the model stays
in memory and the icon stays in the tray. For the duration of a meeting that is one
click, not a restart.

The menu has no status line. It had one and it was convenient, right up until
several features were switched on — Windows stretches a menu to its longest entry,
so a single sentence carrying the state, the model and the provider turned the whole
menu into a band across half the screen. The state lives in the icon colour and the
tooltip (hover), and the choices live in the submenu labels: *Model:
large-v3-turbo*, *Hotkey: Right Ctrl*, *Provider: DeepSeek API*.

### Interface language

The tray menu, the recording overlay and the dialogs speak **Polish or English**;
the switch is in the tray menu → *App language*. The change takes effect
immediately, without a restart, and is saved to `ui.language`.

**The first run takes the language from Windows** — from the system *display*
language, not the region, because that is the one answering "what language does
this person read software in". If it is neither Polish nor English, the app picks
English. The result is written down as a concrete `"pl"` or `"en"`, so you can see
what it chose.

This is a **separate setting from the dictation language**
(`transcription.language`) and it is meant to be: dictating in English through a
Polish menu is a perfectly normal combination. That is why both switches sit next
to each other in the menu — *Dictation language* and *App language* explain each
other, whereas either one alone would read as "the language setting".

The log and the command-line output (`-Check`, `-Benchmark`, `-Quality`) are always
in English, whatever this setting says. They are read by whoever is diagnosing a
problem, not by whoever is dictating, and they end up pasted into bug reports and
diffs.

### Choosing a microphone

The **Microphone** menu shows WASAPI devices — one per physical piece of hardware.
Windows exposes the same microphone through four APIs (MME, DirectSound, WASAPI,
WDM-KS), so the full PortAudio list can hold 25 entries for 3 microphones; WASAPI
is the one with complete names and the true sample rate.

The choice is saved **by name, not by index** — PortAudio indices shift whenever
hardware is plugged in, so a number saved today points at a different device
tomorrow.

Unplugging the selected microphone (a USB webcam, say) does not break the app: on
the next dictation it re-scans the hardware, and if the device is still missing it
records from the system default and tells you so with a notification. The device
stays marked in the menu as *(disconnected)*, so you can see what the app is
waiting for. **Refresh list** forces hardware re-detection (PortAudio caches the
list at startup, so a freshly plugged-in microphone will not appear otherwise).

### The clipboard and the recording overlay

Pasting goes through the clipboard and `Ctrl+V` — as in the original
(`NSPasteboard` + `⌘V`). There are two things you can do about it:

- **`output.restore_clipboard`** (default `true`) — after the paste, whatever you
  had on the clipboard comes back. Set it to `false` if you want the transcript to
  stay on the clipboard for another paste.
- **`output.clipboard_history`** (default `false`) — whether a dictation may reach
  the **Windows clipboard history (Win+V)** and the cloud clipboard.

The second setting exists because the first was not enough: restoring the previous
contents **does not remove the history entry**. Windows records every clipboard
change at the moment it happens, so Win+V was collecting every dictation despite
`restore_clipboard = true`. By default the data is therefore marked with the
`CanIncludeInClipboardHistory` and `CanUploadToCloudClipboard` formats — exactly
what password managers do. The restored contents are marked the same way, so as not
to add a duplicate of your own entry to Win+V on every dictation.

Typing the text out character by character (`SendInput`) instead of pasting is a
deliberate **no**: a newline in the text then becomes Enter, which is a
half-finished message sent in every chat app, and 900 characters take noticeably
longer to type than to paste.

#### When a dictation landed nowhere

`Ctrl+V` into a window with no text field pastes nothing, and `restore_clipboard`
then takes the transcript off the clipboard — the overlay has time to report "N
characters", as if it had worked. The text is not lost: it is in the history. Tray
menu → **Copy last transcription** puts it back on the clipboard (this time **with**
Win+V history, because that is a deliberate copy, not an automatic one).

The entry is hidden when `history.enabled = false` — with nothing recorded there is
nothing to hand back. There is deliberately no warning *before* the paste: checking
"is a text field focused" is unreliable in exactly Electron (Teams, VS Code, the
browser), where the whole window is one HWND and usually has no caret, so such a
guard would throw false alarms in the most frequently used applications.

**The recording overlay appears on the monitor holding the active window**, not on
the primary one. Tk knows only one screen (`winfo_screenwidth()` is the primary
monitor, and its origin is always 0,0), so the position is computed from Win32:
`GetForegroundWindow` → `MonitorFromWindow` → that monitor's `rcWork`. `rcWork`, not
`rcMonitor`, so it clears the taskbar on that particular screen. The position is
recomputed every time the overlay is shown, not while it is up — the window does not
chase the cursor across screens mid-recording.

### The dictation key and Polish characters

**Right Ctrl** by default. Change it in the tray menu → *Hotkey*: right/left Ctrl,
right/left Alt, Scroll Lock, Pause. In the config (`hotkey.key`) `f1`–`f20` work
too. The change takes effect immediately, without a restart.

**Right Alt is best avoided on a Polish layout**, and that is the only reason it is
not the default key: on the *Polish (programmers)* layout right Alt **is** AltGr —
the key you use to type `ą ę ó ś ł ż ź ć ń`. Three mechanisms soften the clash:

1. **Nothing is intercepted.** The hook only observes the keyboard; AltGr reaches
   the application untouched, whether or not WhisperDictate is running.
2. **The hold threshold (300 ms).** `AltGr+a` is a press under 100 ms and will never
   cross the threshold. Only a deliberate hold starts recording.
3. **Cancel on another key.** Pressing a letter while AltGr is held aborts the
   gesture — that was a character combination, not dictation.

Modifier keys are excluded from point 3 on purpose: Windows sends a synthetic left
Ctrl alongside every AltGr, so if modifiers cancelled the gesture the hotkey would
never fire at all.

That is enough for *most* presses, but not all — a longer hesitation over `ą` can
cross the threshold and start a recording. For a key you press dozens of times per
paragraph, "nearly always right" is not enough. Hence right Ctrl as the default;
the `alt_r` option stayed, because on the *Polish (214)* layout and on keyboards
without AltGr the problem does not arise.

**If you are upgrading an older installation**, your `config.toml` still holds
`key = "alt_r"` — the new default only applies to fresh configs. Switch it from the
tray menu.

### Proper nouns Whisper garbles

Whisper does not know names it has no reason to expect: "DeepSeek" comes back as
`Dipsick`, `dipsyka`, `Deepsika`. The clean-up model usually cannot repair that —
there is nothing to anchor on, and guessing would be hallucination.

The vocabulary has **two halves that are merged**:

| | where | what to keep there |
|---|---|---|
| shared | [`vocabulary.txt`](vocabulary.txt) in the repo, **version-controlled** | technical and public names — tools, libraries, models |
| private | `transcription.vocabulary` in `%APPDATA%`, **outside git** | client, project and personal names |

The split is deliberate: a shared technical base is worth version-controlling and
having on every machine, whereas names from work are not something you want to push
to GitHub with one `git push`. `config.toml` has been in `.gitignore` since the
first commit.

You edit the private half from the tray menu → *Text clean-up* → **Proper
nouns...**: type **one** name, press Enter, and it lands in the list below. The
field clears itself and keeps the cursor, so you can add the next one straight away.
To remove something, select it in the list and choose *Remove selected*. Nothing has
to be comma-separated by hand.

The window shows **only the private half**, because only that half is editable from
the tray; below the list is a count of the names from the shared file. If you see a
name in Whisper's priming that is not in the window's list, it comes from
[`vocabulary.txt`](vocabulary.txt) and is changed there, in an ordinary editor (the
file is in the repo, so a change is a commit).

Format of the shared file: comma-separated or one per line, `#` starts a comment,
multi-word names allowed.

```
DeepSeek, Claude Code, Anthropic, Kubernetes, Terraform
```

Write names in their **base form** (`Anthropic`, not `Anthropica`) — the model
handles inflection. The app enforces this: adding `Anthropica` to a list that
already holds `Anthropic` does not create a second entry, and removing `Anthropic`
takes its inflected variants with it. Both halves of the vocabulary are merged by
the same rule, so an inflected entry in the private half loses to the base form
from the shared file and reaches neither Whisper nor the prompt.

One list goes to **two** places:

- to Whisper's `initial_prompt` — so it hears them correctly and the problem never
  arises,
- to the clean-up model's prompt — so it repairs whatever got garbled anyway.

Neither half is enough alone. Priming sometimes fails, and repairing after the fact
leaves the garbled text in the history and does not help with clean-up switched off.
The prompt carries an explicit ban on inserting names from the list into transcripts
that contain nothing resembling them — without it the model starts putting them
where they never were.

Measured effect of the second half (`deepseek-v4-flash`, same text, 3 runs each):

| | `DeepSeek` correct | `Sonnet` correct |
|---|---|---|
| without the vocabulary | 2 / 3 | **0 / 3** |
| with the vocabulary | 3 / 3 | **2 / 3** |

The effect on Whisper's priming was not measured — that is documented
`initial_prompt` behaviour, not a measurement on a particular voice.

#### One vocabulary for both languages

You dictate in Polish and in English from **the same list**. Names are written in
their **base form** (`DeepSeek`, not `DeepSeeka`) — the model inflects them itself,
and does so to match the language of the sentence:

| dictation | in the vocabulary | in the result |
|---|---|---|
| "przełączmy na dipsicka" | `DeepSeek` | "przełączmy na **DeepSeeka**" |
| "switch to dipsick" | `DeepSeek` | "switch to **DeepSeek**" |

Two vocabularies are not needed and would be awkward: `transcription.language`
accepts `auto`, so under auto-detection there would be no way to pick the right list
before transcription. Whisper gets one `initial_prompt` either way, and proper nouns
usually sound the same in both languages.

The vocabulary section of the prompt is written in Polish and **deliberately**
instructs the model to inflect in Polish, even though that reads as nonsense for
English. Rewriting it language-neutral was measured and came out **worse**: Polish
dropped from 14/16 to 9/16 on restoring `Sonnet`, while English was 8/8 either way —
because the language lock appended at the end of the prompt overrides that
instruction anyway.

#### A vocabulary that fills itself in

Sometimes the clean-up model recognises a garbled name from context **on its own** —
that is how Sonnet turned `Dipsick` into `Deepseek` with no vocabulary at all. But
such a repair only fixes that one piece of text: Whisper does not learn, and next
time it will garble the name the same way.

The app catches those moments by comparing `raw_text` with the cleaned text in the
history, and offers to add the name to the vocabulary. From then on it works
preventively — **the stronger model teaches the weaker one once, and after that the
problem does not arise**. You pay for the recognition once, not on every dictation.

```powershell
.\run.ps1 -SuggestVocabulary              # what it found in the history
.\run.ps1 -AddVocabulary "DeepSeek, Sonnet"
```

In automatic mode (*Text clean-up* → *Suggest proper nouns*, on by default) a
balloon appears after a dictation, and the menu grows a *Vocabulary suggestions
(N)...* counter — visible only when there is something to review. Nothing steals
focus and **nothing reaches the vocabulary without your confirmation**: a wrong
entry would spoil every future dictation, and do it at the source. An empty field in
the suggestion window means "never ask about this word again".

The filter is deliberately sharp, because the clean-up model changes a great many
words. Out go differences only in diacritics or capitalisation (`wez`→`weź`),
inflections of the same stem (`alta`→`Altu`), substitutions with no phonetic
similarity, and rewrites spanning several words. What stays are one-for-one
substitutions that sound alike and whose result looks like a proper noun. Over 22
dictations that produced 1 candidate — a correct one.

A limitation worth knowing: this does not help with a name the clean-up model will
**never** guess. The first occurrence of something completely unusual has to be
typed in by hand. It complements the manual vocabulary; it does not replace it.

## LLM text clean-up

A raw transcript contains everything you said — including `yyy`, `no więc`,
`jakby`, `w sensie` and mid-sentence corrections. The clean-up layer runs it through
a model before it reaches the clipboard:

```
BEFORE: ...bo myślałem, że ta oryginalna aplikacja od Jacka, to ona jakby te,
        ucina takie, wiesz, że przerabia te moje słowa...
AFTER:  ...bo myślałem, że oryginalna aplikacja od Jacka przerabia te moje słowa...
```

It does four things: cuts fillers, applies spoken self-corrections (`wyślij do
Marka znaczy do Marcina` → `wyślij do Marcina`), fixes punctuation and Polish
diacritics, and on pure silence returns the `EMPTY` sentinel and pastes nothing. It
does not touch identifiers, paths, CLI flags or `camelCase`.

**Disabled by default**, as in the original. Switch it on from the tray menu →
*Text clean-up* → *Enable clean-up*.

The rest of the settings live in the same submenu, in three groups — each labelled
with the current choice, so the state is visible without expanding anything:

```
Text clean-up >
    [ ] Enable clean-up
    ---
    Provider: DeepSeek API >     Anthropic API / DeepSeek API / Claude Code CLI
    Model: deepseek-v4-flash >   the selected provider's models
    Style: Default >             Default / Chat / Verbatim
    ---
    API key: anthropic...
    API key: deepseek...
```

The model list shows **only the active provider's models** — after switching to
DeepSeek you get `deepseek-v4-flash` and `deepseek-v4-pro`, after switching to
Anthropic `claude-haiku-4-5` and `claude-sonnet-5`.

### Which provider

| | Anthropic API | DeepSeek API | Claude Code CLI |
|---|---|---|---|
| Default model | `claude-haiku-4-5` | `deepseek-v4-flash` | `claude-sonnet-5` |
| Input / 1M | $1.00 | **$0.14** | — |
| Output / 1M | $5.00 | **$0.28** | — |
| Cost / dictation | ~$0.0026 | ~$0.00025 | subscription |
| Time | ~1 s (not measured) | **~1.5 s** (measured from PL) | **~5 s** (measured) |
| API key | required | required | **not needed** |
| Traffic goes to | Anthropic (USA) | **DeepSeek (China)** | Anthropic (your subscription) |

DeepSeek is ~10× cheaper and speaks the Anthropic Messages protocol at a different
`base_url`, so the same client serves it. Prompt caching also works better: the
minimum cacheable prefix at Anthropic for Haiku 4.5 is 4096 tokens and our system
prompt is ~1100 — meaning at Anthropic **the cache will not engage at all**, while
DeepSeek caches automatically with no threshold.

DeepSeek's latency from Poland came out at ~1.5 s for both models — so the price is
not a trade-off against waiting here. Polish on a test with fillers, a
self-correction and the identifier `user_id` came out correct: `user_id` untouched.

**Through the Claude Code CLI pick Sonnet, not Haiku.** Counter-intuitively: over
five runs of the same text haiku-4-5 took 19.8–60+ s (twice exceeding the timeout),
while sonnet-5 stayed at 4.2–5.7 s. The CLI's own overhead dominates the model's
speed. That is why the default CLI model is Sonnet.

**Before you switch DeepSeek on: the traffic goes to servers in China.** For work
material use the Claude Code CLI (it goes through your subscription) or switch
clean-up off entirely.

Do not guess which one is best — measure it on your own text:

```powershell
.\run.ps1 -Benchmark "no wiec yyy wyslij to do Marka znaczy do Marcina"
```

It runs the same text through every ready provider and prints the times and the
results side by side.

### Quality comparison

Speed is half the question. The other half is "can I trust it with my text" — and
this answers it:

```powershell
.\run.ps1 -Quality                      # every ready provider
.\run.ps1 -Quality -Provider deepseek   # just one
```

It runs a fixed set of difficult transcripts (identifiers, `camelCase`, paths,
self-corrections, "nie" as a contrast, Whisper hallucinations on silence, a
transcript that is an instruction) and checks for **mechanical errors**: a lost
identifier, a filler left in, the model answering instead of cleaning. You judge the
style yourself — that cannot be measured.

The set doubles as a prompt regression suite: change `prompts.py` or the model, run
it again, and see what broke. The cases live in
`whisperdictate/enhance/quality.py`, each with a rationale for why it exists.

Measured (12 cases, one run):

| | Errors | Avg time |
|---|---|---|
| `deepseek-v4-pro` | 0 / 12 | 1.54 s |
| `deepseek-v4-flash` | 0 / 12 | 1.60 s |
| `claude_cli` + `claude-haiku-4-5` | 0 / 12 | 20.9 s |
| `claude_cli` + `claude-sonnet-5` | 1 / 12 | 4.25 s |

One run is not enough — these models are not deterministic. Repeating the hardest
cases ×5, **Pro turned out to be clearly more consistent than Flash** at the same
speed: Flash returned the proper noun in three different forms (`DeepSick`,
`Deepsika`, `DeepSeeka`) and once replaced a meaningful "jakby" with "jakieś"; Pro
gave the same result five times over. If repeatability matters to you, Pro costs no
time here — only tokens.

### API keys

They do not go into the config file. They land in the **Windows Credential
Manager**, encrypted per user, one entry per provider:

```powershell
.\run.ps1 -SetApiKey anthropic
.\run.ps1 -SetApiKey deepseek
```

Or from the tray menu → *Text clean-up* → *API key: …*. The `ANTHROPIC_API_KEY` and
`DEEPSEEK_API_KEY` variables take precedence; each provider reads only its own, so
one never shadows the other.

### The fail-soft rule

A clean-up failure **never costs you a dictation**. A missing key, an API limit, no
network, a timeout, a provider error — the raw transcript is pasted and the reason
goes to the log. The history records both versions (`text` and `raw_text`), so a bad
clean-up does not destroy the original either.

A separate safeguard: when the model's reply is disproportionately long relative to
the transcript, it is rejected. That is the case where the model **answered** your
dictation instead of cleaning it — and then the raw text is the better one.

Test it without a microphone:

```powershell
.\run.ps1 -Enhance "no wiec yyy wyslij to do Marka znaczy do Marcina"
```

## Configuration

File: `%APPDATA%\WhisperDictateWin\config.toml` (created on first start). Full
description of the options with comments:
[`config.example.toml`](config.example.toml).

The ones changed most often:

| Key | Default | Meaning |
|---|---|---|
| `transcription.language` | `"pl"` | dictation: `pl`, `en` or `auto` |
| `ui.language` | from the system | interface: `pl` or `en` (fallback: `en`) |
| `transcription.model` | `"large-v3-turbo"` | smaller = faster, less accurate |
| `hotkey.key` | `"ctrl_r"` | `alt_l`, `alt_r`, `ctrl_l`, `f1`–`f20`, `scroll_lock`, `pause` |
| `hotkey.mode` | `"hold"` | `hold` or `toggle` |
| `audio.device` | `null` | microphone name; set from the tray menu |
| `output.clipboard_history` | `false` | `true` = dictations also reach Win+V |
| `output.auto_paste` | `true` | `false` = clipboard only, no Ctrl+V |
| `[replacements]` | empty | substitution table, e.g. `"kubernetes" = "Kubernetes"` |

The app **overwrites** this file when you change a setting from the menu —
comments in it will not survive. Keep notes in `config.example.toml`.

## Diagnostics

```powershell
.\run.ps1 -Check                      # CUDA, microphones, hotkey, model loading
.\run.ps1 -ListDevices                # microphones (WASAPI)
.\run.ps1 -ListDevices -All           # + duplicates from MME/DirectSound/WDM-KS
.\run.ps1 -Record 5                   # record 5 s and print the transcript (no hotkey)
.\run.ps1 -Record 5 -Device "Anker"   # ...from a specific microphone, without changing the config
.\run.ps1 -SetApiKey                  # store an API key in the Credential Manager
.\run.ps1 -Enhance "no wiec yyy test" # test the text clean-up
.\run.ps1 -Trace                      # DEBUG logging
```

Log: `%APPDATA%\WhisperDictateWin\whisperdictate.log`.
Transcription history: `%APPDATA%\WhisperDictateWin\history.jsonl`.

#### The history does not grow without bound

The file is **trimmed** to `history.max_entries` (5000 by default), checked every
100 appends. Measured on real data: median entry 667 B, mean 871 B, longest seen
3165 B (with `raw_text`, i.e. with clean-up enabled).

| `max_entries` | size ceiling |
|---|---|
| 5000 (default) | **~4.2 MB** |
| 2000 | ~1.7 MB |
| 1000 | ~0.83 MB |

Parsing the full 5000 entries takes 23 ms, and it happens after the paste, on a
worker thread. There is no problem to solve here — which is why there is no "older
than N days" deletion and no separate settings window.

**If you lower the limit, mind two windows:** after every dictation the last **300**
entries are scanned for proper nouns, and `-SuggestVocabulary` reads **1000**. Below
those values the vocabulary suggestions have less to work with.
`history.enabled = false` disables recording entirely — but then *Copy last
transcription* has nothing to read and disappears from the menu.

### Common problems

**"CTranslate2 UNAVAILABLE", or running on CPU despite an NVIDIA card**
The CUDA libraries are missing. `.\.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt`.
You need an NVIDIA driver supporting CUDA 12 (R525 or newer).

**The hotkey does not respond**
If the focused window runs as administrator and WhisperDictate does not, Windows
blocks it from observing the keyboard (User Interface Privilege Isolation). Run the
app with the same privileges as the target window.

**The text pastes in the wrong place, or not at all**
Raise `output.paste_delay_ms` (to 250, say). Some Electron-based applications read
the clipboard asynchronously. The alternative: `output.auto_paste = false` and
Ctrl+V by hand.

**Transcripts out of nowhere, e.g. "Napisy stworzone przez społeczność Amara.org"**
The classic Whisper hallucination on silence. Make sure
`transcription.vad_filter = true`, and check with `-ListDevices` that you are
recording from the right microphone.

## What is not here

The original has a few things this version deliberately does not reproduce:

- **The OpenAI, OpenRouter and Ollama providers** for text clean-up. Three are
  implemented: Anthropic API, DeepSeek API and Claude Code CLI (the last two are an
  addition; the original does not have them). The `Provider` interface has a single
  method, so adding another is one class.
- **A dashboard window** (8 tabs, statistics, a history browser). The history is
  written to JSONL, but you read it in an editor.
- **Model downloads with a progress bar.** Hugging Face Hub handles that.

The architecture is separated (the core knows nothing about the UI), so adding a
window would not require rewriting the logic. Details:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Acknowledgements

- **[Jacek Gajewski](https://github.com/jacek-gajewski-ice)** — author of the
  original [WhisperDictate](https://github.com/jacek-gajewski-ice/whisper-app) for
  macOS. This project exists because that one existed first: the idea, the
  interaction design and the product decisions are his. His `CLAUDE.md` with
  engineering notes was the best documentation anyone could have had while
  reconstructing the behaviour.
- **[FreeFlow](https://github.com/zachlatta/freeflow)**
  and **[VoiceInk](https://github.com/Beingpax/VoiceInk)** — the clean-up prompt
  pattern, by way of the original, which credits them.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** (SYSTRAN)
  and **[CTranslate2](https://github.com/OpenNMT/CTranslate2)** — the transcription
  engine.
- **[Whisper](https://github.com/openai/whisper)** (OpenAI) — the model.

## Licence

The code in this repository: MIT. The Whisper models: MIT (OpenAI).
faster-whisper: MIT.

The code is not a derivative of the original (not a line was carried over — it is a
different language and different frameworks), so that project's licence does not
apply here. The attribution above is a matter of honesty, not a legal requirement.
