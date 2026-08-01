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

from . import APP_NAME, __version__, paths, vocabulary
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
        log.warning("pywin32 niedostepny - pomijam kontrole pojedynczej instancji")
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
            config.get("transcription.vocabulary", ""),
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
        print("Nie znaleziono zadnego urzadzenia wejsciowego audio.")
        return 1

    print("Dostepne mikrofony:")
    for device in devices:
        print(f"  {device}")

    if not all_host_apis:
        total = len(list_input_devices(all_host_apis=True))
        hidden = total - len(devices)
        if hidden > 0:
            print(f"\nUkryto {hidden} duplikatow z MME/DirectSound/WDM-KS. Pelna lista: --list-devices --all")

    print("\nWybierz mikrofon z menu tray, albo wpisz nazwe jako audio.device w config.toml.")
    return 0


def cmd_check(config: Config) -> int:
    from . import runtime_cuda

    print(f"{APP_NAME} for Windows {__version__}")
    print(f"  Python           {sys.version.split()[0]}")
    print(f"  Config           {paths.config_path()}")
    print(f"  Log              {paths.log_path()}")
    print(f"  Cache modeli     {paths.model_cache_dir()}")

    dll_dirs = runtime_cuda.enable_cuda_dlls()
    print(f"  Biblioteki CUDA  {len(dll_dirs)} katalog(ow) dodanych do sciezki DLL")

    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        print(f"  CTranslate2      {ctranslate2.__version__}, widoczne GPU: {count}")
    except Exception as exc:  # noqa: BLE001
        print(f"  CTranslate2      NIEDOSTEPNY ({exc})")
        return 1

    devices = list_input_devices()
    print(f"  Mikrofony        {len(devices)}")
    for device in devices[:5]:
        print(f"                   {device}")

    key = config.get("hotkey.key", DEFAULT_KEY)
    known = "OK" if key in TRIGGERS else f"NIEZNANY (dostepne: {', '.join(sorted(TRIGGERS))})"
    print(f"  Hotkey           {key} / {config.get('hotkey.mode')} - {known}")

    from .enhance import PROVIDERS as PROVIDER_SPECS

    enhancement = EnhancementService(config)
    problem = enhancement.check()
    status = "OK" if problem is None else f"NIEGOTOWE ({problem})"
    print(f"  Czyszczenie      {enhancement.describe()} - {status}")
    for key, provider_spec in PROVIDER_SPECS.items():
        if provider_spec.env_var is None:
            continue
        print(f"    klucz {key:<10} {credentials.source(key)}  ->  {provider_spec.hosting}")

    print("\nLaduje model (przy pierwszym uruchomieniu pobiera ~1.6 GB)...")
    transcriber = build_transcriber(config)
    try:
        transcriber.ensure_loaded()
    except TranscriptionError as exc:
        print(f"  BLAD: {exc}")
        return 1
    print(f"  Model            {transcriber.description}")
    print("\nWszystko gotowe.")
    return 0


def cmd_record(config: Config, seconds: float) -> int:
    """Record from the mic and print the transcript. Exercises everything but the hotkey."""
    import time

    transcriber = build_transcriber(config)
    print("Laduje model...")
    try:
        transcriber.ensure_loaded()
    except TranscriptionError as exc:
        print(f"BLAD: {exc}")
        return 1
    print(f"Model: {transcriber.description}")

    recorder = build_recorder(config)
    try:
        recorder.start()
    except AudioError as exc:
        print(f"BLAD: {exc}")
        return 1
    if recorder.fallback_note:
        print(f"UWAGA: {recorder.fallback_note}")

    device = config.get("audio.device") or "domyslny systemowy"
    print(f"Mikrofon: {device}")
    print(f"Mow teraz - nagrywam {seconds:.0f} s...")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.1)
        bars = int(min(1.0, recorder.level / 0.25) * 30)
        print(f"\r  [{'#' * bars}{'.' * (30 - bars)}]", end="", flush=True)
    audio = recorder.stop()
    print()

    if audio is None or audio.size == 0:
        print("Nie zarejestrowano dzwieku.")
        return 1

    language = config.get("transcription.language", "pl")
    result = transcriber.transcribe(audio, language)
    print(f"\nJezyk: {result.language} ({result.language_probability:.2f})")
    print(f"Czas:  {result.elapsed_seconds:.2f} s dla {result.audio_seconds:.1f} s audio "
          f"({result.speedup:.0f}x realtime)")
    print(f"\nTekst: {result.text!r}")
    return 0


def cmd_set_api_key(provider: str) -> int:
    """Read a key from the console (never echoed) into Credential Manager."""
    import getpass

    from .enhance import PROVIDERS as PROVIDER_SPECS

    if provider not in PROVIDER_SPECS or PROVIDER_SPECS[provider].env_var is None:
        keyed = [k for k, s in PROVIDER_SPECS.items() if s.env_var]
        print(f"Nieznany provider {provider!r}. Dostepne: {', '.join(keyed)}")
        return 1

    provider_spec = PROVIDER_SPECS[provider]
    print(f"Provider:  {provider_spec.label}")
    print(f"Ruch idzie do: {provider_spec.hosting}")
    print("Klucz zostanie zapisany w Menedzerze polswiadczen Windows.")
    print("Nie trafi do pliku konfiguracyjnego ani do logow.\n")

    # Piped input wins over the interactive prompt. getpass needs a real Windows
    # console; under Git Bash or an MSYS pty it falls back to an echoing read,
    # which would print the key to the screen and into the scrollback.
    if not sys.stdin.isatty():
        key = sys.stdin.readline()
        if not key.strip():
            print("BLAD: nic nie przyszlo na stdin.")
            return 1
    else:
        try:
            key = getpass.getpass("Klucz: ")
        except (EOFError, KeyboardInterrupt):
            print("\nPrzerwano.")
            return 1
        except Exception as exc:  # noqa: BLE001 - getpass raises GetPassWarning-adjacent errors
            print(f"BLAD: nie moge bezpiecznie odczytac klucza ({exc}).")
            print("Podaj go przez potok, np.:")
            print(f'  Read-Host "Klucz" -AsSecureString | ... | python -m whisperdictate '
                  f"--set-api-key {provider}")
            return 1

    try:
        credentials.set_api_key(provider, key)
    except (ValueError, OSError) as exc:
        print(f"BLAD: {exc}")
        return 1
    print(f"\nZapisano ({provider}). Wlacz czyszczenie w menu tray.")
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
        print(f"BLAD: {problem}")
        return 1

    print(f"Provider: {service.provider_name} / {service.model}")
    print(f"Styl:     {config.get('enhancement.prompt')}\n")
    print(f"Przed ({len(text)} znakow):\n  {text}\n")

    result = service.enhance(text, language)
    if result is None:
        print("Czyszczenie nie powiodlo sie - w aplikacji wkleilby sie surowy tekst.")
        print("Szczegoly w logu: " + str(paths.log_path()))
        return 1
    if not result.text:
        print("Wynik: EMPTY - model uznal to za sam szum, nic nie zostaloby wklejone.")
        return 0

    print(f"Po ({len(result.text)} znakow, {result.elapsed_seconds:.2f} s):\n  {result.text}")
    return 0


def cmd_benchmark(config: Config, text: str) -> int:
    """Run the same transcript through every ready provider and compare.

    Latency is the deciding factor for dictation, and it cannot be reasoned
    about from pricing pages — it has to be measured from where you sit.
    """
    from .enhance import PROVIDERS as PROVIDER_SPECS

    language = _prepare_for_enhance(config, None)
    print(f"Tekst wejsciowy ({len(text)} znakow):\n  {text}\n")

    rows = []
    for key, provider_spec in PROVIDER_SPECS.items():
        for model in provider_spec.models:
            config.set("enhancement.provider", key, save=False)
            config.set("enhancement.model", model, save=False)
            service = EnhancementService(config)

            problem = service.check()
            if problem:
                print(f"--- {key} / {model}: POMINIETO ({problem})")
                continue

            print(f"--- {key} / {model}  [{provider_spec.hosting}]")
            result = service.enhance(text, language)
            if result is None:
                print("    NIEUDANE (szczegoly w logu)\n")
                rows.append((key, model, None, None))
                continue
            print(f"    {result.elapsed_seconds:6.2f} s  ->  {result.text}\n")
            rows.append((key, model, result.elapsed_seconds, len(result.text)))

    ok = [r for r in rows if r[2] is not None]
    if not ok:
        print("Zaden provider nie odpowiedzial.")
        return 1

    print("Podsumowanie (posortowane po czasie):")
    print(f"  {'provider/model':<34} {'czas':>8} {'znakow':>8}")
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
    known = config.get("transcription.vocabulary", "")
    found = vocabulary.pending(entries, known, config.get("transcription.vocabulary_rejected", ""))

    paired = sum(1 for e in entries if e.get("raw_text"))
    print(f"Przejrzano {len(entries)} wpisow historii ({paired} z czyszczeniem).")
    if known:
        print(f"W slowniku juz: {', '.join(vocabulary.terms(known))}")

    if not found:
        print("\nBrak nowych kandydatow.")
        print("Kandydat powstaje, gdy model czyszczacy sam poprawi przekrecona nazwe.")
        return 0

    print(f"\nKandydaci ({len(found)}):")
    for item in found:
        print(f"  {item.corrected:<24} <- Whisper uslyszal {item.heard!r}")

    proposed = ", ".join(i.corrected for i in found)
    print("\nDopisz te, ktore sa nazwami wlasnymi (w formie podstawowej):")
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
    print(f"Dopisano {len(added)}: {', '.join(added)}" if added else "Nic nowego do dopisania.")
    print(f"Slownik ({len(vocabulary.terms(updated))}): {updated}")
    print("\nDziala od nastepnego uruchomienia aplikacji (albo od razu, jesli zmienisz w tray).")
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
            print(f"POMINIETO {key} / {model}: {problem}")
            continue
        combos.append((key, model, provider_spec))

    if not combos:
        print("Zaden provider nie jest gotowy.")
        return 1

    print(f"\n{len(quality.CASES)} przypadkow x {len(combos)} modeli. "
          f"Sprawdzam tylko bledy mechaniczne - styl ocen sam.\n")

    results: dict[tuple[str, str], list] = {c[:2]: [] for c in combos}

    for case in quality.CASES:
        print("=" * 78)
        print(f"[{case.name}] {case.why}")
        print(f"  WEJSCIE: {case.text}")
        if case.expect_empty:
            print("  OCZEKIWANE: EMPTY (nic do wklejenia)")

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
    print("Podsumowanie - im mniej bledow, tym lepiej:\n")
    print(f"  {'provider/model':<34} {'OK':>4} {'BLAD':>6} {'sr. czas':>10}")
    ranked = sorted(
        results.items(),
        key=lambda item: (sum(r.failed for r in item[1]), sum(r.elapsed for r in item[1])),
    )
    for (key, model), outcomes in ranked:
        bad = sum(r.failed for r in outcomes)
        avg = sum(r.elapsed for r in outcomes) / len(outcomes)
        print(f"  {key + '/' + model:<34} {len(outcomes) - bad:>4} {bad:>6} {avg:>9.2f}s")

    print("\nSzczegoly bledow:")
    clean_sweep = True
    for (key, model), outcomes in ranked:
        for outcome in outcomes:
            if outcome.failed:
                clean_sweep = False
                print(f"  {key}/{model} [{outcome.case.name}]: "
                      + "; ".join(outcome.violations))
    if clean_sweep:
        print("  brak - wszystkie modele przeszly wszystkie przypadki")
    return 0


# ------------------------------------------------------------------- app


def run_app(config: Config) -> int:
    mutex = acquire_single_instance()
    if mutex is None:
        log.error("%s juz dziala (sprawdz zasobnik systemowy)", APP_NAME)
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
        "%s %s uruchomiony. Przytrzymaj %s aby dyktowac (jezyk: %s).",
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
        log.info("Zakonczono.")
    return 0


# ------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisperdictate",
        description="Lokalne dyktowanie hold-to-talk dla Windows (faster-whisper).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="logowanie DEBUG")
    parser.add_argument("--list-devices", action="store_true", help="wypisz mikrofony i zakoncz")
    parser.add_argument(
        "--all", action="store_true",
        help="z --list-devices: pokaz tez duplikaty z MME/DirectSound/WDM-KS",
    )
    parser.add_argument("--check", action="store_true", help="diagnostyka srodowiska (CUDA, audio, model)")
    parser.add_argument(
        "--record", type=float, metavar="SEKUNDY",
        help="nagraj N sekund, wypisz transkrypcje i zakoncz (test bez hotkeya)",
    )
    parser.add_argument(
        "--device", metavar="NAZWA",
        help="z --record: uzyj tego mikrofonu zamiast tego z konfiguracji",
    )
    parser.add_argument(
        "--set-api-key", metavar="PROVIDER", nargs="?", const="anthropic",
        help="zapisz klucz API w Menedzerze polswiadczen (anthropic | deepseek)",
    )
    parser.add_argument(
        "--enhance", metavar="TEKST",
        help="przepusc tekst przez warstwe czyszczaca i wypisz wynik",
    )
    parser.add_argument(
        "--provider", metavar="NAZWA",
        help="z --enhance: uzyj tego providera zamiast tego z konfiguracji",
    )
    parser.add_argument(
        "--benchmark", metavar="TEKST",
        help="porownaj wszystkich gotowych providerow na tym samym tekscie",
    )
    parser.add_argument(
        "--quality", action="store_true",
        help="porownanie jakosciowe na stalym zestawie trudnych transkrypcji",
    )
    parser.add_argument(
        "--suggest-vocabulary", action="store_true",
        help="wypisz nazwy wlasne, ktore model czyszczacy poprawil sam",
    )
    parser.add_argument(
        "--add-vocabulary", metavar="NAZWY",
        help="dopisz nazwy (po przecinku) do slownika",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    config = Config.load(paths.config_path())

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
