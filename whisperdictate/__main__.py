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
import tkinter as tk

from . import APP_NAME, __version__, paths
from .audio import AudioError, Recorder, list_input_devices
from .config import Config
from .controller import DictationController
from .enhance import EnhancementService, credentials
from .history import History
from .hotkey import TRIGGERS, HotkeyListener
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
        initial_prompt=config.get("transcription.initial_prompt", "") or "",
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

    key = config.get("hotkey.key", "alt_r")
    known = "OK" if key in TRIGGERS else f"NIEZNANY (dostepne: {', '.join(sorted(TRIGGERS))})"
    print(f"  Hotkey           {key} / {config.get('hotkey.mode')} - {known}")

    enhancement = EnhancementService(config)
    problem = enhancement.check()
    status = "OK" if problem is None else f"NIEGOTOWE ({problem})"
    print(f"  Czyszczenie      {enhancement.describe()} - {status}")
    print(f"  Klucz API        {credentials.source()}")

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


def cmd_set_api_key() -> int:
    """Read a key from the console (never echoed) into Credential Manager."""
    import getpass

    print("Klucz API Anthropic zostanie zapisany w Menedzerze polswiadczen Windows.")
    print("Nie trafi do pliku konfiguracyjnego ani do logow.\n")
    try:
        key = getpass.getpass("Klucz (sk-ant-...): ")
    except (EOFError, KeyboardInterrupt):
        print("\nPrzerwano.")
        return 1

    try:
        credentials.set_api_key(key)
    except (ValueError, OSError) as exc:
        print(f"BLAD: {exc}")
        return 1
    print("Zapisano. Wlacz czyszczenie w menu tray albo ustaw enhancement.enabled = true.")
    return 0


def cmd_enhance(config: Config, text: str) -> int:
    """Run the clean-up layer over a literal string. Exercises it without a mic."""
    service = EnhancementService(config)
    problem = service.check()
    if problem:
        print(f"BLAD: {problem}")
        return 1

    # --enhance is an explicit request, so honour it even when the feature is
    # off in the config; that is what makes it useful for trying before enabling.
    config.set("enhancement.enabled", True, save=False)
    language = config.get("transcription.language", "pl")

    print(f"Provider: {service.provider_name} / {service.model}")
    print(f"Styl:     {config.get('enhancement.prompt')}\n")
    print(f"Przed ({len(text)} znakow):\n  {text}\n")

    result = service.enhance(text, language)
    if result is None:
        print("Czyszczenie nie powiodlo sie - w aplikacji wkleiłby sie surowy tekst.")
        print("Szczegoly w logu: " + str(paths.log_path()))
        return 1
    if not result.text:
        print("Wynik: EMPTY - model uznal to za sam szum, nic nie zostaloby wklejone.")
        return 0

    print(f"Po ({len(result.text)} znakow, {result.elapsed_seconds:.2f} s):\n  {result.text}")
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
        key=config.get("hotkey.key", "alt_r"),
        mode=config.get("hotkey.mode", "hold"),
        hold_threshold_ms=int(config.get("hotkey.hold_threshold_ms", 300)),
        cancel_on_other_key=bool(config.get("hotkey.cancel_on_other_key", True)),
        on_start=controller.on_start,
        on_stop=controller.on_stop,
        on_cancel=controller.on_cancel,
    )

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
        "--set-api-key", action="store_true",
        help="zapisz klucz API Anthropic w Menedzerze polswiadczen Windows",
    )
    parser.add_argument(
        "--enhance", metavar="TEKST",
        help="przepusc tekst przez warstwe czyszczaca i wypisz wynik",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    config = Config.load(paths.config_path())

    if args.set_api_key:
        return cmd_set_api_key()
    if args.enhance is not None:
        return cmd_enhance(config, args.enhance)
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
