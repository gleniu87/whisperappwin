"""Orchestration: hotkey gesture -> recording -> transcription -> paste.

Everything the hotkey layer calls must return within microseconds, because those
callbacks run on the global keyboard hook thread. So `stop()` hands the audio to
a worker thread and returns immediately.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Protocol

from .audio import AudioError, Recorder
from .config import Config
from .history import History
from .postprocess import process
from .sounds import Sounds
from .transcriber import Transcriber, TranscriptionError

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"
    PAUSED = "paused"


class UiSink(Protocol):
    """What the controller needs from a UI. Implemented by tray and overlay."""

    def set_state(self, state: State, detail: str = "") -> None: ...
    def notify(self, message: str, *, error: bool = False) -> None: ...


class NullUi:
    def set_state(self, state: State, detail: str = "") -> None:
        pass

    def notify(self, message: str, *, error: bool = False) -> None:
        pass


class DictationController:
    def __init__(
        self,
        *,
        config: Config,
        recorder: Recorder,
        transcriber: Transcriber,
        history: History,
        sounds: Sounds,
        ui: UiSink | None = None,
    ):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.history = history
        self.sounds = sounds
        self._uis: list[UiSink] = [ui] if ui is not None else []

        self._state = State.IDLE
        self._paused = False
        self._lock = threading.Lock()
        self._workers: set[threading.Thread] = set()

    # -- wiring ---------------------------------------------------------

    def add_ui(self, ui: UiSink) -> None:
        self._uis.append(ui)
        ui.set_state(self._state)

    @property
    def state(self) -> State:
        return self._state

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def audio_level(self) -> float:
        return self.recorder.level

    # -- model preloading -----------------------------------------------

    def preload(self) -> None:
        """Load the model in the background so the first dictation is not slow."""

        def work() -> None:
            self._set_state(State.LOADING, "laduje model")
            try:
                self.transcriber.ensure_loaded()
            except TranscriptionError as exc:
                log.error("%s", exc)
                self._set_state(State.ERROR, str(exc))
                self._notify(str(exc), error=True)
                return
            self._set_state(State.IDLE, self.transcriber.description)

        self._spawn(work, name="preload")

    # -- hotkey callbacks -----------------------------------------------

    def on_start(self) -> None:
        if self._paused:
            return
        with self._lock:
            if self.recorder.is_recording:
                return
            try:
                self.recorder.start()
            except AudioError as exc:
                log.error("%s", exc)
                self._set_state(State.ERROR, str(exc))
                self._notify(str(exc), error=True)
                self.sounds.play("error")
                return
        self.sounds.play("start")
        self._set_state(State.RECORDING)

    def on_stop(self) -> None:
        with self._lock:
            if not self.recorder.is_recording:
                return
            audio = self.recorder.stop()

        self.sounds.play("stop")

        min_seconds = float(self.config.get("audio.min_seconds", 0.4))
        duration = audio.size / 16_000 if audio is not None else 0.0
        if audio is None or duration < min_seconds:
            log.info("Nagranie %.2f s ponizej progu %.2f s - pomijam", duration, min_seconds)
            self._set_state(State.IDLE, "za krotkie")
            return

        self._set_state(State.TRANSCRIBING)
        self._spawn(lambda: self._transcribe_and_deliver(audio, duration), name="transcribe")

    def on_cancel(self) -> None:
        with self._lock:
            if not self.recorder.is_recording:
                return
            self.recorder.cancel()
        self.sounds.play("cancel")
        self._set_state(State.IDLE, "anulowano")

    # -- pause ----------------------------------------------------------

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self.on_cancel()
        self._set_state(State.PAUSED if paused else State.IDLE)
        log.info("Dyktowanie %s", "wstrzymane" if paused else "wznowione")

    # -- settings changes -------------------------------------------------

    def set_language(self, language: str) -> None:
        self.config.set("transcription.language", language)
        log.info("Jezyk: %s", language)
        self._set_state(self._state)

    def set_model(self, model: str) -> None:
        if model == self.transcriber.model_name:
            return
        self.config.set("transcription.model", model)

        def work() -> None:
            self._set_state(State.LOADING, f"laduje {model}")
            try:
                self.transcriber.reload(model=model)
            except TranscriptionError as exc:
                log.error("%s", exc)
                self._set_state(State.ERROR, str(exc))
                self._notify(str(exc), error=True)
                return
            self._set_state(State.IDLE, self.transcriber.description)
            self._notify(f"Model: {self.transcriber.description}")

        self._spawn(work, name="reload-model")

    # -- worker ---------------------------------------------------------

    def _transcribe_and_deliver(self, audio, duration: float) -> None:  # noqa: ANN001
        language = self.config.get("transcription.language", "pl")
        try:
            result = self.transcriber.transcribe(audio, language)
        except TranscriptionError as exc:
            log.error("%s", exc)
            self.sounds.play("error")
            self._set_state(State.ERROR, str(exc))
            self._notify(str(exc), error=True)
            return

        text = process(result.text, self.config.get("replacements", {}))
        if not text:
            log.info("Pusta transkrypcja - nic do wklejenia")
            self.sounds.play("cancel")
            self._set_state(State.IDLE, "cisza")
            return

        # Deliberately ordered: history first. If the paste fails, the transcript
        # is still recoverable from the log instead of being lost.
        self.history.append(
            text=text,
            language=result.language,
            language_probability=round(result.language_probability, 3),
            model=self.transcriber.model_name,
            audio_seconds=round(result.audio_seconds, 2),
            elapsed_seconds=round(result.elapsed_seconds, 2),
        )

        try:
            # Imported lazily: pulls in pywin32 + pynput, neither needed for --check.
            from .output import ClipboardError, deliver

            deliver(
                text,
                auto_paste=bool(self.config.get("output.auto_paste", True)),
                restore_clipboard=bool(self.config.get("output.restore_clipboard", True)),
                paste_delay_ms=int(self.config.get("output.paste_delay_ms", 120)),
            )
        except ClipboardError as exc:
            log.error("%s", exc)
            self.sounds.play("error")
            self._set_state(State.ERROR, str(exc))
            self._notify(f"{exc} Tekst jest w historii.", error=True)
            return

        self.sounds.play("success")
        self._set_state(State.IDLE, f"{len(text)} znakow, {result.speedup:.0f}x realtime")

    # -- helpers ----------------------------------------------------------

    def _spawn(self, target, *, name: str) -> None:  # noqa: ANN001
        thread = threading.Thread(target=self._tracked(target), name=name, daemon=True)
        self._workers.add(thread)
        thread.start()

    def _tracked(self, target):  # noqa: ANN001
        def wrapper() -> None:
            try:
                target()
            except Exception:  # noqa: BLE001 - a crashing worker must not be silent
                log.exception("Nieobsluzony blad w watku roboczym")
                self._set_state(State.ERROR, "blad wewnetrzny")
            finally:
                self._workers.discard(threading.current_thread())

        return wrapper

    def _set_state(self, state: State, detail: str = "") -> None:
        if self._paused and state in (State.IDLE, State.RECORDING):
            state = State.PAUSED
        self._state = state
        for ui in self._uis:
            try:
                ui.set_state(state, detail)
            except Exception:  # noqa: BLE001
                log.exception("Blad UI przy zmianie stanu")

    def _notify(self, message: str, *, error: bool = False) -> None:
        for ui in self._uis:
            try:
                ui.notify(message, error=error)
            except Exception:  # noqa: BLE001
                log.exception("Blad UI przy powiadomieniu")
