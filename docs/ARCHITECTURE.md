# Architecture

## Where this project comes from

The original (`jacek-gajewski-ice/whisper-app`) is a native macOS application:
Swift 6, SwiftUI `MenuBarExtra`, AVAudioRecorder, Carbon event taps for the hotkey,
`NSPasteboard`, TCC permissions, `codesign` signing. None of those layers exists on
Windows, so the port reproduces the **behaviour**, not the code.

Layer mapping:

| Original (macOS) | This version (Windows) |
|---|---|
| whisper.cpp (`whisper-cli`, child process) | faster-whisper / CTranslate2 (in-process) |
| AVAudioRecorder → WAV file | sounddevice → NumPy array in memory |
| Carbon event tap on left ⌥ | pynput `Listener` on right Alt |
| SwiftUI `MenuBarExtra` | pystray (tray icon) |
| SwiftUI panel | Tk `Toplevel` (recording overlay) |
| `NSPasteboard` + ⌘V | `win32clipboard` + Ctrl+V via pynput |
| `UserDefaults` | TOML in `%APPDATA%` |
| TCC (permissions) | no equivalent — Windows does not ask for per-application microphone access for desktop apps |

Swapping whisper.cpp for faster-whisper is not cosmetic: the child process goes
away, the WAV write to disk goes away, and on an NVIDIA GPU CTranslate2 with
`float16` is faster than whisper.cpp with CUDA. The cost is a heavier install (cuDNN
libraries).

## Threading model

This is the easiest thing to get wrong in this application, so it is spelled out.

```
main            Tk event loop — overlay, polling for the quit signal
pystray         its own Win32 message loop (run_detached)
pynput          global keyboard hook (low-level)
worker-*        model loading and transcription, one thread per job
```

Rules the code enforces:

- **Hotkey callbacks must return immediately.** They run on the global keyboard
  hook's thread; blocking it stops key handling system-wide. That is why
  `DictationController.on_stop()` hands the audio to a worker thread and returns.
- **Tk may only be touched from the main thread.** `Overlay.set_state()` pushes an
  event onto a `queue.Queue`, drained by a recurring `after()`.
- **Shutdown goes through a `threading.Event`.** The tray menu runs on its own
  thread and cannot call `root.quit()`; it sets an event the main thread polls.
- **An exception on a worker thread must not vanish silently.** `_tracked()` wraps
  every thread, logs the traceback and switches the state to `ERROR`.

## The flow of one dictation

```
right Alt pressed
  └─ 300 ms timer ──(another key?)──> cancelled, that was an AltGr combination
       └─ threshold passed
            └─ Recorder.start()          PortAudio stream, mono f32 @ 16 kHz
                 ├─ overlay: red dot + level meter
                 └─ right Alt released
                      └─ Recorder.stop() → np.ndarray
                           ├─ < min_seconds? discard
                           └─ worker thread:
                                ├─ Transcriber.transcribe()   VAD → CT2 → text
                                ├─ postprocess.process()      clean-up + replacements
                                ├─ History.append()           JSONL (before the paste!)
                                └─ output.deliver()           clipboard + Ctrl+V
```

The "history before the paste" ordering is deliberate: if the clipboard is locked by
another application, the transcript is already recorded and can be recovered.

## Modules

| File | Responsibility |
|---|---|
| `config.py` | TOML with `get("section.key")` access, validation, atomic save |
| `i18n.py` | catalogue of UI text (pl/en), language selection, detection from Windows |
| `paths.py` | locations under `%APPDATA%` / `%LOCALAPPDATA%` |
| `runtime_cuda.py` | registering DLLs from the `nvidia-*-cu12` packages before ctranslate2 is imported |
| `audio.py` | microphone detection and selection, capture, resampling, level meter |
| `transcriber.py` | lazy model loading, CUDA with a CPU fallback |
| `hotkey.py` | right-Alt gesture detection, hold threshold, AltGr protection |
| `output.py` | clipboard, releasing stuck modifiers, Ctrl+V |
| `postprocess.py` | text clean-up, hallucination filter, replacement table |
| `history.py` | appending to JSONL, trimming |
| `sounds.py` | non-blocking audio cues |
| `enhance/` | LLM transcript clean-up: prompts, providers, output filter, API key |
| `controller.py` | state machine, orchestration, the `UiSink` protocol |
| `ui/tray.py` | tray icon, menu, notifications |
| `ui/overlay.py` | floating recording indicator |
| `__main__.py` | CLI, logging, single instance, dependency assembly |

## The core / UI boundary

`DictationController` imports nothing from `ui/`. It knows only the protocol:

```python
class UiSink(Protocol):
    def set_state(self, state: State, detail: str = "") -> None: ...
    def notify(self, message: str, *, error: bool = False) -> None: ...
```

The tray and the overlay are two independent implementations, registered via
`add_ui()`. Adding a dashboard window (absent from this version, present in the
original) comes down to a third implementation of that protocol — with no changes to
the logic.

## The language layer

`i18n.py` holds **all the text the user sees** — the tray menu, the recording
overlay, the dialogs, plus those error messages that reach a balloon (`AudioError`,
`TranscriptionError`, `ClipboardError`). It imports nothing from the package, so
`config`, `audio` and `enhance/` can read from it without a cycle.

**The log and the CLI are written in English directly, not through the catalogue,
and that is the documented scope of `i18n`: the GUI only.** They are developer-facing
— read by whoever is diagnosing a problem, and pasted into bug reports and diffs —
so a second language for them would double the catalogue's size with no user ever
reading it. Log and CLI strings must not be moved into `i18n`.

A few values the CLI prints do come from the catalogue, because the GUI needs them
too (`credentials.describe_source()`, `registry.hosting()`). So `main()` forces
`i18n.use("en")` for the one-shot commands and honours `ui.language` only for
`run_app()` — otherwise `--check` mixes translated sentences into English output.

The active language is module state (`i18n.use()`), not an object passed down. The
alternative would be threading a translator through the controller into the tray, the
overlay and every dialog, and there is exactly one user per process.

The two languages are **two independent settings**: `ui.language` (the interface) and
`transcription.language` (what Whisper hears). Dictating in English through a Polish
menu is a normal combination, and `transcription.language` additionally accepts
`auto`, which makes no sense for a menu.

Machine-readable text has been pushed out of the data and into the catalogue:
`ProviderSpec` now holds only the product name ("DeepSeek API"), while the hint
("~10x cheaper") and the jurisdiction (`registry.hosting()`) come from `i18n`.
Likewise `credentials.source()` now returns `env` / `store` / `none` rather than a
sentence — you cannot branch code on a translated sentence.

## Decisions that look odd and are deliberate

**Linear resampling instead of scipy's polyphase.**
Whisper's front-end is a mel spectrogram at 16 kHz, and speech energy sits well
below the Nyquist frequency. The aliasing a proper filter would remove is inaudible
to the model. The fallback path is rare anyway — WASAPI in shared mode usually
reports 16 kHz itself.

**`condition_on_previous_text=False`.**
Every dictation is independent. Carrying context across utterances is the main cause
of repetition loops in Whisper.

**Modifiers do not cancel the hotkey gesture.**
Windows sends a synthetic left Ctrl with every AltGr. If modifiers counted as
"another key", the right-Alt hotkey would never fire.

**Only text is restored to the clipboard.**
`CF_UNICODETEXT` and nothing else. If the clipboard held an image or a file list
before the dictation, it is lost. Preserving the full contents would mean capturing
every format including delayed rendering — disproportionately much code for the
gain. The alternative for the demanding: `output.restore_clipboard = false`.

**The microphone menu shows WASAPI only.**
Windows exposes the same microphone through four PortAudio host APIs. On the test
machine that came to 26 entries for 3 physical devices. MME truncates names at 31
characters (`Mikrofon (Virtual Desktop Audio` — note the missing closing bracket),
WDM-KS splits a multi-channel device into per-channel-pair entries, and WASAPI gives
one clean entry with the true sample rate. Name resolution also searches WASAPI
first, so a saved name does not quietly land on the inferior MME entry for the same
microphone. `--list-devices --all` shows the full list.

**The device is saved by name, not by index.**
PortAudio indices shift whenever the hardware changes. An index saved today points at
a different microphone tomorrow — a silent failure that looks like a broken
application.

**`refresh_devices()` restarts PortAudio.**
The device list is a snapshot from initialisation time, so a microphone plugged in
later is invisible. The restart is only safe with no stream open, which is why it is
called from the menu (blocked while recording) and once on a failed name resolution
— that is, in exactly the "I unplugged the webcam" scenario.

**pystray menu actions are closures, never `lambda x=value:`.**
pystray decides how to invoke an action from `__code__.co_argcount`: 0 = call with no
arguments, 1 = pass the `Icon`. A default argument **counts** towards that number, so
the late-binding idiom `lambda n=device.name: ...` receives an `Icon` object instead
of the name. `checked` predicates are immune (pystray calls them with one argument, so
the second takes its default), but actions are not. `tests/test_tray_menu.py` guards
this.

**Text clean-up is fail-soft and never raises.**
`EnhancementService.enhance()` returns `None` on any failure — a missing key, an API
limit, a timeout, a provider error, even an unexpected exception. `None` means "paste
the raw transcript". An empty string means something else: the `EMPTY` sentinel from
the prompt, i.e. "that was pure noise, paste nothing". The distinction is deliberate —
a failure and silence look identical if both return emptiness.

**`EMPTY` is only honoured on a transcript that could plausibly be empty.**
Honouring it is the one irreversible outcome in the pipeline: `controller.py` pastes
nothing *and* returns before `history.append`, so nothing survives — not the
clipboard, not the history, not *Copy last transcription*. So `_plausibly_empty()`
gates it: blank text, a known Whisper silence artefact, an all-filler transcript, or
one under 16 characters. Anything more substantial makes `EMPTY` a provider failure
instead, and the raw transcript gets pasted. This changed the meaning of `""` for
every provider, not just the local one — measured, DeepSeek returned `EMPTY` 0 times
in 68 real dictations, while `qwen3.5:9b` lost 1 transcript in 76 and `qwen3.5:4b`
lost 3, one of them a valid command. The residual gap is a dictation short enough to
be indistinguishable from noise; the discarded text is logged so it is at least
recoverable.

**The transcript is wrapped in `<TRANSCRIPT>`.**
Without it a dictation that happens to be a question ("czy możesz to sprawdzić")
reads as an instruction and the model answers it. The tag turns it into data.

**A safeguard against answering instead of cleaning.**
When the result is more than three times longer than the input (and longer than 400
characters), it is rejected. Cleaned text is close in length to the original; a
result several times longer is a different kind of text — the model answered instead
of cleaning.

**The prompt contains Polish filler vocabulary.**
A prompt written in English cuts "um" and leaves "no więc yyy" untouched. The list of
Polish fillers and self-correction phrases is the functional core, not a translation.

**The provider registry is a separate, import-free module.**
`enhance/registry.py` imports nothing from the package, so both `config.py` and
`enhance/providers.py` can read it without a cycle. It holds what distinguishes the
providers: `base_url`, the key's environment variable, the model list and the
jurisdiction.

`base_url` in the spec is a constant for the hosted providers and only a *default* for
the local one, which `enhancement.base_url` overrides — the server is the user's, so
its address has to be theirs too. `providers.build()` forwards that override only to
the `OPENAI_API` kind. Letting it reach a key-carrying provider would post the API key
to an arbitrary host while `hosting()` still claimed the traffic went to Anthropic or
DeepSeek, which is precisely the fact the user relies on that function for.

**DeepSeek has no client of its own.**
It exposes an endpoint compatible with the Anthropic Messages protocol, so
`MessagesApiProvider` serves both — only `base_url` and the key differ. It ignores
`anthropic-beta`, `anthropic-version`, `top_k` and `cache_control`; we send none of
them. Its prompt cache is automatic server-side, so the ignored `cache_control` costs
us no cache hits.

**Changing the provider resets the model.**
Model names do not carry across providers. Leaving `claude-haiku-4-5` after switching
to DeepSeek would hit their API's silent fallback to `deepseek-v4-flash` — it works,
but the configuration then lies about what is actually running.

**The model menu uses a `visible` predicate, not a rebuild.**
A pystray menu is immutable once built, but `visible` is evaluated on every display.
Every provider's models are declared up front and hidden when their provider is not
selected.

**API keys in the Credential Manager, not the config — one entry per provider.**
`config.toml` is plain text in a roaming profile, rewritten on every tray click. The
Credential Manager encrypts per user and keeps the key out of anything that can be
shared by accident. `Persist` is set to `LOCAL_MACHINE` so the key does not roam with
a domain profile.

**Tk dialogs are launched through `MainThreadDispatcher`.**
The tray has its own Win32 message loop, and Tk objects may only be touched from the
thread that created them. A menu action pushes the call onto a queue drained by
`after()` on the Tk thread.

**The model is preloaded in the background.**
Constructing `WhisperModel` takes seconds. Loading it on the first dictation would
make the first use after startup noticeably slower.

## What is missing relative to the original

- The OpenAI and OpenRouter clean-up providers. Four are implemented here:
  Anthropic API, DeepSeek API, Claude Code CLI and a local OpenAI-compatible server —
  the middle two are additions the original does not have, and the local one covers
  what the original used Ollama for.
- The dashboard window with eight tabs.
- A model manager with a download progress bar.
- Usage statistics (the original counts words, time, and typing time saved).

The history is recorded in a format sufficient to build statistics later — every
entry carries `audio_seconds`, `elapsed_seconds`, `model`, `language`.
