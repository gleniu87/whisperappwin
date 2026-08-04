"""Entry point: `python -m whisperdictate`.

Threading layout, because it is the one thing that is easy to get wrong here:

  main thread      Tk event loop - owns the overlay, polls for the quit signal
  pystray thread   Win32 message loop for the tray icon (run_detached)
  pynput thread    low-level keyboard hook; callbacks must return immediately
  worker threads   model loading and transcription, spawned per job
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import threading
import time
import tkinter as tk

from . import APP_NAME, __version__, i18n, paths, vocabulary
from .audio import AudioError, Recorder, list_input_devices
from .config import Config
from .controller import DictationController
from .enhance import EnhancementService, credentials
from .history import History
from .hotkey import DEFAULT_KEY, TRIGGERS, HotkeyListener
from .sounds import Sounds
from .transcriber import Transcriber, TranscriptionError

log = logging.getLogger("whisperdictate")

MUTEX_NAME = "Global\\WhisperDictateWin_SingleInstance"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 2
QUIT_POLL_MS = 200


# ---------------------------------------------------------------- logging


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        paths.log_path(), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Third-party noise. httpx in particular logs one INFO line per HTTP request,
    # which turns a single model download into dozens of lines of nothing.
    for noisy in ("httpx", "httpcore", "huggingface_hub", "filelock", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.INFO)


# ------------------------------------------------------- single instance


def acquire_single_instance() -> object | None:
    """Return a mutex handle, or None if another instance already holds it.

    Two instances would both hook the keyboard and both paste, which looks like
    the app has gone haywire. Cheaper to refuse than to explain.
    """
    try:
        import win32api
        import win32event
        import winerror

        handle = win32event.CreateMutex(None, True, MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return None
        return handle
    except ImportError:  # pragma: no cover - pywin32 missing
        log.warning("pywin32 unavailable - skipping the single-instance check")
        return object()


# ------------------------------------------------------------- factories


def build_transcriber(config: Config) -> Transcriber:
    return Transcriber(
        config.get("transcription.model", "large-v3-turbo"),
        device=config.get("transcription.device", "auto"),
        compute_type=config.get("transcription.compute_type", "auto"),
        beam_size=int(config.get("transcription.beam_size", 5)),
        vad_filter=bool(config.get("transcription.vad_filter", True)),
        initial_prompt=vocabulary.whisper_priming(
            vocabulary.combined(config.get("transcription.vocabulary", "")),
            config.get("transcription.initial_prompt", ""),
        ),
    )


def build_recorder(config: Config) -> Recorder:
    return Recorder(
        device=config.get("audio.device"),
        max_seconds=float(config.get("audio.max_seconds", 300.0)),
    )


# ------------------------------------------------------------ subcommands


def cmd_list_devices(*, all_host_apis: bool = False) -> int:
    devices = list_input_devices(all_host_apis=all_host_apis)
    if not devices:
        print("No audio input device found.")
        return 1

    print("Available microphones:")
    for device in devices:
        print(f"  {device}")

    if not all_host_apis:
        total = len(list_input_devices(all_host_apis=True))
        hidden = total - len(devices)
        if hidden > 0:
            print(f"\nHid {hidden} duplicate(s) from MME/DirectSound/WDM-KS. Full list: --list-devices --all")

    print("\nPick a microphone from the tray menu, or put its name in audio.device in config.toml.")
    return 0


def cmd_check(config: Config) -> int:
    from . import runtime_cuda

    print(f"{APP_NAME} for Windows {__version__}")
    print(f"  Python           {sys.version.split()[0]}")
    print(f"  Config           {paths.config_path()}")
    print(f"  Log              {paths.log_path()}")
    print(f"  Model cache      {paths.model_cache_dir()}")

    dll_dirs = runtime_cuda.enable_cuda_dlls()
    print(f"  CUDA libraries   {len(dll_dirs)} directory(ies) added to the DLL search path")

    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        print(f"  CTranslate2      {ctranslate2.__version__}, GPUs visible: {count}")
    except Exception as exc:  # noqa: BLE001
        print(f"  CTranslate2      UNAVAILABLE ({exc})")
        return 1

    devices = list_input_devices()
    print(f"  Microphones      {len(devices)}")
    for device in devices[:5]:
        print(f"                   {device}")

    key = config.get("hotkey.key", DEFAULT_KEY)
    known = "OK" if key in TRIGGERS else f"UNKNOWN (available: {', '.join(sorted(TRIGGERS))})"
    print(f"  Hotkey           {key} / {config.get('hotkey.mode')} - {known}")

    from .enhance import PROVIDERS as PROVIDER_SPECS
    from .enhance import registry

    enhancement = EnhancementService(config)
    problem = enhancement.check()
    status = "OK" if problem is None else f"NOT READY ({problem})"
    print(f"  Clean-up         {enhancement.describe()} - {status}")
    # Where the transcript goes, for the provider actually selected. Printed even
    # for the keyless ones, because a keyless provider still has a destination -
    # and for a redirected local server that destination is the whole question.
    base_url = str(config.get("enhancement.base_url", "") or "")
    print(f"    traffic        {registry.hosting(enhancement.provider_name, base_url)}")
    for key, provider_spec in PROVIDER_SPECS.items():
        if provider_spec.env_var is None:
            continue
        print(f"    key {key:<12} {credentials.describe_source(key)}"
              f"  ->  {registry.hosting(key)}")

    print("\nLoading the model (the first run downloads ~1.6 GB)...")
    transcriber = build_transcriber(config)
    try:
        transcriber.ensure_loaded()
    except TranscriptionError as exc:
        print(f"  ERROR: {exc}")
        return 1
    print(f"  Model            {transcriber.description}")
    print("\nAll set.")
    return 0


def cmd_record(config: Config, seconds: float) -> int:
    """Record from the mic and print the transcript. Exercises everything but the hotkey."""
    import time

    transcriber = build_transcriber(config)
    print("Loading the model...")
    try:
        transcriber.ensure_loaded()
    except TranscriptionError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Model: {transcriber.description}")

    recorder = build_recorder(config)
    try:
        recorder.start()
    except AudioError as exc:
        print(f"ERROR: {exc}")
        return 1
    if recorder.fallback_note:
        print(f"NOTE: {recorder.fallback_note}")

    device = config.get("audio.device") or "system default"
    print(f"Microphone: {device}")
    print(f"Speak now - recording {seconds:.0f} s...")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.1)
        bars = int(min(1.0, recorder.level / 0.25) * 30)
        print(f"\r  [{'#' * bars}{'.' * (30 - bars)}]", end="", flush=True)
    audio = recorder.stop()
    print()

    if audio is None or audio.size == 0:
        print("No audio was captured.")
        return 1

    language = config.get("transcription.language", "pl")
    result = transcriber.transcribe(audio, language)
    print(f"\nLanguage: {result.language} ({result.language_probability:.2f})")
    print(f"Time:     {result.elapsed_seconds:.2f} s for {result.audio_seconds:.1f} s of audio "
          f"({result.speedup:.0f}x realtime)")
    print(f"\nText: {result.text!r}")
    return 0


def cmd_set_api_key(provider: str) -> int:
    """Read a key from the console (never echoed) into Credential Manager."""
    import getpass

    from .enhance import PROVIDERS as PROVIDER_SPECS
    from .enhance import registry

    if provider not in PROVIDER_SPECS or PROVIDER_SPECS[provider].env_var is None:
        keyed = [k for k, s in PROVIDER_SPECS.items() if s.env_var]
        print(f"Unknown provider {provider!r}. Available: {', '.join(keyed)}")
        return 1

    print(f"Provider:     {registry.label_with_hint(provider)}")
    print(f"Traffic goes to: {registry.hosting(provider)}")
    print("The key will be stored in the Windows Credential Manager.")
    print("It will not reach the config file or the log.\n")

    # Piped input wins over the interactive prompt. getpass needs a real Windows
    # console; under Git Bash or an MSYS pty it falls back to an echoing read,
    # which would print the key to the screen and into the scrollback.
    if not sys.stdin.isatty():
        key = sys.stdin.readline()
        if not key.strip():
            print("ERROR: nothing arrived on stdin.")
            return 1
    else:
        try:
            key = getpass.getpass("Key: ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        except Exception as exc:  # noqa: BLE001 - getpass raises GetPassWarning-adjacent errors
            print(f"ERROR: cannot read the key safely ({exc}).")
            print("Pipe it in instead, for example:")
            print(f'  Read-Host "Key" -AsSecureString | ... | python -m whisperdictate '
                  f"--set-api-key {provider}")
            return 1

    try:
        credentials.set_api_key(provider, key)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"\nStored ({provider}). Switch clean-up on from the tray menu.")
    return 0


def _prepare_for_enhance(config: Config, provider: str | None) -> str:
    """Force the feature on for a one-shot run, optionally overriding the provider."""
    from .enhance import registry

    config.set("enhancement.enabled", True, save=False)
    if provider:
        config.set("enhancement.provider", provider, save=False)
        if not registry.supports_model(provider, config.get("enhancement.model", "")):
            config.set("enhancement.model", registry.default_model(provider), save=False)
    return config.get("transcription.language", "pl")


def cmd_enhance(config: Config, text: str, provider: str | None) -> int:
    """Run the clean-up layer over a literal string. Exercises it without a mic."""
    language = _prepare_for_enhance(config, provider)
    service = EnhancementService(config)

    problem = service.check()
    if problem:
        print(f"ERROR: {problem}")
        return 1

    print(f"Provider: {service.provider_name} / {service.model}")
    print(f"Style:    {config.get('enhancement.prompt')}\n")
    print(f"Before ({len(text)} characters):\n  {text}\n")

    result = service.enhance(text, language)
    if result is None:
        print("Clean-up failed - in the app the raw text would have been pasted.")
        print("Details in the log: " + str(paths.log_path()))
        return 1
    if not result.text:
        print("Result: EMPTY - the model judged this pure noise, nothing would be pasted.")
        return 0

    print(f"After ({len(result.text)} characters, {result.elapsed_seconds:.2f} s):\n  {result.text}")
    return 0


def cmd_benchmark(config: Config, text: str) -> int:
    """Run the same transcript through every ready provider and compare.

    Latency is the deciding factor for dictation, and it cannot be reasoned
    about from pricing pages — it has to be measured from where you sit.
    """
    from .enhance import PROVIDERS as PROVIDER_SPECS
    from .enhance import registry

    language = _prepare_for_enhance(config, None)
    print(f"Input text ({len(text)} characters):\n  {text}\n")

    rows = []
    for key, provider_spec in PROVIDER_SPECS.items():
        for model in provider_spec.models:
            config.set("enhancement.provider", key, save=False)
            config.set("enhancement.model", model, save=False)
            service = EnhancementService(config)

            problem = service.check()
            if problem:
                print(f"--- {key} / {model}: SKIPPED ({problem})")
                continue

            print(f"--- {key} / {model}  [{registry.hosting(key)}]")
            result = service.enhance(text, language)
            if result is None:
                print("    FAILED (details in the log)\n")
                rows.append((key, model, None, None))
                continue
            print(f"    {result.elapsed_seconds:6.2f} s  ->  {result.text}\n")
            rows.append((key, model, result.elapsed_seconds, len(result.text)))

    ok = [r for r in rows if r[2] is not None]
    if not ok:
        print("No provider answered.")
        return 1

    print("Summary (sorted by time):")
    print(f"  {'provider/model':<34} {'time':>8} {'chars':>8}")
    for key, model, elapsed, length in sorted(ok, key=lambda r: r[2]):
        print(f"  {key + '/' + model:<34} {elapsed:>7.2f}s {length:>8}")
    return 0


def cmd_suggest_vocabulary(config: Config) -> int:
    """List names the clean-up model repaired that are not in the vocabulary yet.

    The manual half of the same mechanism the tray uses: no notifications, no
    state, just what the history already knows.
    """
    from .history import History

    history = History(paths.history_path(), enabled=True)
    entries = history.recent(1000)
    private = config.get("transcription.vocabulary", "")
    shared = vocabulary.read_shared()
    known = vocabulary.combined(private, shared)
    found = vocabulary.pending(entries, known, config.get("transcription.vocabulary_rejected", ""))

    paired = sum(1 for e in entries if e.get("raw_text"))
    print(f"Reviewed {len(entries)} history entries ({paired} with clean-up).")
    if shared.strip():
        print(f"Shared ({vocabulary.SHARED_FILE}): {', '.join(vocabulary.terms(shared))}")
    if private:
        print(f"Private (config): {', '.join(vocabulary.terms(private))}")

    if not found:
        print("\nNo new candidates.")
        print("A candidate appears when the clean-up model repairs a mangled name on its own.")
        return 0

    print(f"\nCandidates ({len(found)}):")
    for item in found:
        print(f"  {item.corrected:<24} <- Whisper heard {item.heard!r}")

    proposed = ", ".join(i.corrected for i in found)
    print("\nAdd the ones that are proper nouns (in their base form):")
    print(f'  .\\run.ps1 -AddVocabulary "{proposed}"')
    return 0


def cmd_add_vocabulary(config: Config, raw: str) -> int:
    """Append terms to the vocabulary from the command line."""
    before = config.get("transcription.vocabulary", "")
    updated = before
    for term in vocabulary.terms(raw):
        updated = vocabulary.add(updated, term)
    config.set("transcription.vocabulary", updated)

    added = [t for t in vocabulary.terms(updated) if t not in vocabulary.terms(before)]
    print(f"Added {len(added)}: {', '.join(added)}" if added else "Nothing new to add.")
    print(f"Vocabulary ({len(vocabulary.terms(updated))}): {updated}")
    print("\nTakes effect on the next app start (or immediately, if you change it from the tray).")
    return 0


def _combos_for(provider_filter: str | None):
    """(provider, model) pairs to compare — every model of every provider."""
    from .enhance import PROVIDERS as PROVIDER_SPECS

    for key, provider_spec in PROVIDER_SPECS.items():
        if provider_filter and key != provider_filter:
            continue
        for model in provider_spec.models:
            yield key, model, provider_spec


def cmd_quality(config: Config, provider_filter: str | None) -> int:
    """Run the fixed case set through each provider and report what broke.

    The latency benchmark says which provider is fast enough; this says which
    one can be trusted with the text. A provider that mangles an identifier is
    not a cheaper option, it is a wrong one.
    """
    from .enhance import quality

    language = _prepare_for_enhance(config, None)
    config.set("enhancement.timeout_seconds", 120.0, save=False)

    combos = []
    for key, model, provider_spec in _combos_for(provider_filter):
        config.set("enhancement.provider", key, save=False)
        config.set("enhancement.model", model, save=False)
        problem = EnhancementService(config).check()
        if problem:
            print(f"SKIPPED {key} / {model}: {problem}")
            continue
        combos.append((key, model, provider_spec))

    if not combos:
        print("No provider is ready.")
        return 1

    print(f"\n{len(quality.CASES)} cases x {len(combos)} models. "
          f"Only mechanical errors are checked - judge the style yourself.\n")

    results: dict[tuple[str, str], list] = {c[:2]: [] for c in combos}

    for case in quality.CASES:
        print("=" * 78)
        print(f"[{case.name}] {case.why}")
        print(f"  INPUT: {case.text}")
        if case.expect_empty:
            print("  EXPECTED: EMPTY (nothing to paste)")

        for key, model, _ in combos:
            config.set("enhancement.provider", key, save=False)
            config.set("enhancement.model", model, save=False)
            started = time.perf_counter()
            result = EnhancementService(config).enhance(case.text, language)
            elapsed = time.perf_counter() - started

            cleaned = None if result is None else result.text
            violations = quality.check(case, cleaned)
            outcome = quality.CaseResult(case, cleaned, elapsed, violations)
            results[(key, model)].append(outcome)

            shown = "(EMPTY)" if cleaned == "" else cleaned
            print(f"\n  {outcome.status:6} {key}/{model} ({elapsed:.2f} s)")
            print(f"         {shown}")
            for problem in violations:
                print(f"         !! {problem}")
        print()

    print("=" * 78)
    print("Summary - fewer errors is better:\n")
    print(f"  {'provider/model':<34} {'OK':>4} {'FAIL':>6} {'avg time':>10}")
    ranked = sorted(
        results.items(),
        key=lambda item: (sum(r.failed for r in item[1]), sum(r.elapsed for r in item[1])),
    )
    for (key, model), outcomes in ranked:
        bad = sum(r.failed for r in outcomes)
        avg = sum(r.elapsed for r in outcomes) / len(outcomes)
        print(f"  {key + '/' + model:<34} {len(outcomes) - bad:>4} {bad:>6} {avg:>9.2f}s")

    print("\nError details:")
    clean_sweep = True
    for (key, model), outcomes in ranked:
        for outcome in outcomes:
            if outcome.failed:
                clean_sweep = False
                print(f"  {key}/{model} [{outcome.case.name}]: "
                      + "; ".join(outcome.violations))
    if clean_sweep:
        print("  none - every model passed every case")
    return 0


# ------------------------------------------------------------------- app


def run_app(config: Config) -> int:
    mutex = acquire_single_instance()
    if mutex is None:
        log.error("%s is already running (check the system tray)", APP_NAME)
        return 1

    recorder = build_recorder(config)
    transcriber = build_transcriber(config)
    history = History(
        paths.history_path(),
        enabled=bool(config.get("history.enabled", True)),
        max_entries=int(config.get("history.max_entries", 5000)),
    )
    sounds = Sounds(enabled=bool(config.get("ui.sounds", True)))

    root = tk.Tk()
    root.withdraw()

    from .ui.dispatch import MainThreadDispatcher
    from .ui.overlay import Overlay
    from .ui.tray import Tray

    overlay = Overlay(
        root,
        level_provider=lambda: recorder.level,
        enabled=bool(config.get("ui.overlay", True)),
    )
    dispatcher = MainThreadDispatcher(root)

    controller = DictationController(
        config=config,
        recorder=recorder,
        transcriber=transcriber,
        history=history,
        sounds=sounds,
        enhancement=EnhancementService(config),
        ui=overlay,
    )

    quit_event = threading.Event()
    tray = Tray(
        controller=controller,
        config=config,
        on_quit=quit_event.set,
        dispatcher=dispatcher,
    )
    controller.add_ui(tray)

    listener = HotkeyListener(
        key=config.get("hotkey.key", DEFAULT_KEY),
        mode=config.get("hotkey.mode", "hold"),
        hold_threshold_ms=int(config.get("hotkey.hold_threshold_ms", 300)),
        cancel_on_other_key=bool(config.get("hotkey.cancel_on_other_key", True)),
        on_start=controller.on_start,
        on_stop=controller.on_stop,
        on_cancel=controller.on_cancel,
    )
    controller.attach_hotkey(listener)
    controller.refresh_vocabulary_suggestions()

    signal.signal(signal.SIGINT, lambda *_: quit_event.set())

    tray.start()
    listener.start()
    controller.preload()

    log.info(
        "%s %s started. Hold %s to dictate (language: %s).",
        APP_NAME, __version__, config.get("hotkey.key"), config.get("transcription.language"),
    )

    def poll_quit() -> None:
        if quit_event.is_set():
            root.quit()
        else:
            root.after(QUIT_POLL_MS, poll_quit)

    root.after(QUIT_POLL_MS, poll_quit)
    try:
        root.mainloop()
    finally:
        listener.stop()
        tray.stop()
        overlay.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass
        log.info("Shut down.")
    return 0


# ------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisperdictate",
        description="Local hold-to-talk dictation for Windows (faster-whisper).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    parser.add_argument("--list-devices", action="store_true", help="list microphones and exit")
    parser.add_argument(
        "--all", action="store_true",
        help="with --list-devices: also show MME/DirectSound/WDM-KS duplicates",
    )
    parser.add_argument("--check", action="store_true", help="environment diagnostics (CUDA, audio, model)")
    parser.add_argument(
        "--record", type=float, metavar="SECONDS",
        help="record N seconds, print the transcript and exit (a test without the hotkey)",
    )
    parser.add_argument(
        "--device", metavar="NAME",
        help="with --record: use this microphone instead of the configured one",
    )
    parser.add_argument(
        "--set-api-key", metavar="PROVIDER", nargs="?", const="anthropic",
        help="store an API key in the Credential Manager (anthropic | deepseek)",
    )
    parser.add_argument(
        "--enhance", metavar="TEXT",
        help="run text through the clean-up layer and print the result",
    )
    parser.add_argument(
        "--provider", metavar="NAME",
        help="with --enhance: use this provider instead of the configured one",
    )
    parser.add_argument(
        "--benchmark", metavar="TEXT",
        help="compare every ready provider on the same text",
    )
    parser.add_argument(
        "--quality", action="store_true",
        help="quality comparison over a fixed set of difficult transcripts",
    )
    parser.add_argument(
        "--suggest-vocabulary", action="store_true",
        help="list proper nouns the clean-up model repaired on its own",
    )
    parser.add_argument(
        "--add-vocabulary", metavar="NAMES",
        help="add names (comma-separated) to the vocabulary",
    )
    return parser.parse_args(argv)


def _is_one_shot(args: argparse.Namespace) -> bool:
    """Whether this invocation prints to a terminal and exits, rather than starting the app."""
    return bool(
        args.set_api_key
        or args.enhance is not None
        or args.benchmark is not None
        or args.quality
        or args.suggest_vocabulary
        or args.add_vocabulary is not None
        or args.list_devices
        or args.check
        or args.record is not None
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    config = Config.load(paths.config_path())
    # Before anything can produce a user-facing string. Config.load has already
    # resolved "which language" - including detecting it from Windows on a first
    # run - so this only puts the answer into force.
    #
    # The one-shot commands force English instead. Their output is CLI output, and
    # CLI output is English by decision (docs/HANDOFF.md) - but a few of the values
    # they print come from helpers shared with the GUI (`credentials.describe_source`,
    # `registry.hosting`), which do go through the catalogue. Without this, --check
    # printed Polish sentences into otherwise English output.
    i18n.use(i18n.FALLBACK if _is_one_shot(args) else config.get("ui.language"))

    if args.set_api_key:
        return cmd_set_api_key(args.set_api_key)
    if args.enhance is not None:
        return cmd_enhance(config, args.enhance, args.provider)
    if args.benchmark is not None:
        return cmd_benchmark(config, args.benchmark)
    if args.quality:
        return cmd_quality(config, args.provider)
    if args.suggest_vocabulary:
        return cmd_suggest_vocabulary(config)
    if args.add_vocabulary is not None:
        return cmd_add_vocabulary(config, args.add_vocabulary)
    if args.list_devices:
        return cmd_list_devices(all_host_apis=args.all)
    if args.check:
        return cmd_check(config)
    if args.record is not None:
        if args.device:
            config.set("audio.device", args.device, save=False)
        return cmd_record(config, args.record)
    return run_app(config)


if __name__ == "__main__":
    sys.exit(main())
