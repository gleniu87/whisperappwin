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
from .enhance import EnhancementService
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
    ENHANCING = "enhancing"
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
        enhancement: EnhancementService | None = None,
        ui: UiSink | None = None,
    ):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.history = history
        self.sounds = sounds
        self.enhancement = enhancement if enhancement is not None else EnhancementService(config)
        self._uis: list[UiSink] = [ui] if ui is not None else []

        self._state = State.IDLE
        self._paused = False
        self._lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        # Populated on demand; the tray reads it every time the menu opens, so
        # it must never be a history scan.
        self._pending_vocabulary: list = []

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
        if self.recorder.fallback_note:
            # Recording from the wrong microphone usually means recording silence.
            # Say so now rather than letting the user wonder why nothing appears.
            self._notify(self.recorder.fallback_note, error=True)

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

    def set_audio_device(self, spec: int | str | None) -> None:
        """Choose the input device. Applies from the next recording onwards."""
        self.config.set("audio.device", spec)
        self.recorder.device_spec = spec
        log.info("Mikrofon: %s", spec if spec is not None else "domyslny systemowy")
        self._set_state(self._state)

    def attach_hotkey(self, listener) -> None:  # noqa: ANN001 - HotkeyListener; avoids a cycle
        """Injected after construction: the listener needs this object's callbacks."""
        self.hotkey = listener

    def set_hotkey_key(self, key: str) -> None:
        """Change the push-to-talk key, live."""
        listener = getattr(self, "hotkey", None)
        if listener is not None and not listener.set_key(key):
            self._notify(f"Nieznany klawisz: {key}", error=True)
            return
        self.config.set("hotkey.key", key)
        log.info("Hotkey: %s", key)
        self._set_state(self._state)

    def set_vocabulary(self, raw: str) -> None:
        """Update the proper-noun list and re-prime the loaded model.

        Pushed onto the live transcriber rather than waiting for a restart: the
        list is edited precisely when a name has just come back mangled, and
        being told to restart at that moment is the wrong answer.
        """
        from . import vocabulary

        self.config.set("transcription.vocabulary", raw)
        self.transcriber.initial_prompt = vocabulary.whisper_priming(
            vocabulary.combined(raw), self.config.get("transcription.initial_prompt", "")
        ) or None
        log.info("Slownik nazw wlasnych: %d pozycji", len(vocabulary.terms(raw)))
        self._set_state(self._state)

    def set_enhancement_enabled(self, enabled: bool) -> None:
        """Turn transcript clean-up on or off, warning if the provider is unusable."""
        self.config.set("enhancement.enabled", enabled)
        log.info("Czyszczenie tekstu %s", "wlaczone" if enabled else "wylaczone")
        if enabled:
            problem = self.enhancement.check()
            if problem:
                self._notify(f"Czyszczenie wlaczone, ale {problem}.", error=True)
        self._set_state(self._state)

    def set_enhancement_provider(self, provider: str) -> None:
        from .enhance import registry

        self.config.set("enhancement.provider", provider, save=False)
        # Model names do not carry across providers. Leaving claude-haiku-4-5
        # selected after switching to DeepSeek would silently resolve to
        # whatever that API maps unknown names to.
        if not registry.supports_model(provider, self.config.get("enhancement.model", "")):
            self.config.set("enhancement.model", registry.default_model(provider), save=False)
        self.config.save()

        log.info("Provider czyszczenia: %s / %s", provider, self.config.get("enhancement.model"))
        problem = self.enhancement.check()
        if problem and self.enhancement.enabled:
            self._notify(f"Provider {provider}: {problem}.", error=True)
        self._set_state(self._state)

    def set_enhancement_model(self, model: str) -> None:
        self.config.set("enhancement.model", model)
        log.info("Model czyszczenia: %s", model)
        self._set_state(self._state)

    def set_enhancement_prompt(self, prompt: str) -> None:
        self.config.set("enhancement.prompt", prompt)
        log.info("Styl czyszczenia: %s", prompt)
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

        raw_text = process(result.text, self.config.get("replacements", {}))
        if not raw_text:
            log.info("Pusta transkrypcja - nic do wklejenia")
            self.sounds.play("cancel")
            self._set_state(State.IDLE, "cisza")
            return

        text, enhancement = raw_text, None
        if self.enhancement.enabled:
            self._set_state(State.ENHANCING)
            # Returns None on any failure - the raw transcript is pasted instead.
            enhancement = self.enhancement.enhance(raw_text, result.language)
            if enhancement is not None:
                text = enhancement.text

        if not text:
            # The prompt's EMPTY sentinel: the model judged this pure filler.
            log.info("Warstwa czyszczaca uznala transkrypcje za pusta - nie wklejam")
            self.sounds.play("cancel")
            self._set_state(State.IDLE, "odrzucone jako szum")
            return

        # Deliberately ordered: history first. If the paste fails, the transcript
        # is still recoverable from the log instead of being lost. raw_text is
        # kept alongside so a bad clean-up never destroys the original.
        self.history.append(
            text=text,
            raw_text=raw_text if enhancement is not None else None,
            enhanced_by=f"{enhancement.provider}/{enhancement.model}" if enhancement else None,
            language=result.language,
            language_probability=round(result.language_probability, 3),
            model=self.transcriber.model_name,
            audio_seconds=round(result.audio_seconds, 2),
            elapsed_seconds=round(result.elapsed_seconds, 2),
        )

        # Imported lazily (pulls in pywin32 + pynput, neither needed for --check)
        # but outside the try: an ImportError here must not be caught by an
        # `except ClipboardError` whose name would not even be bound yet.
        from .output import ClipboardError, deliver

        try:
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
        detail = f"{len(text)} znakow, {result.speedup:.0f}x realtime"
        if enhancement is not None:
            detail += f", oczyszczone +{enhancement.elapsed_seconds:.1f} s"
        self._set_state(State.IDLE, detail)

        # Last, and never in the way: the paste has already happened, so a
        # failure here costs a suggestion, not the dictation.
        if enhancement is not None:
            self._offer_vocabulary(raw_text, text)

    # -- vocabulary suggestions -------------------------------------------

    #: Scanning the whole history on every dictation would grow unbounded.
    _SUGGESTION_WINDOW = 300

    @property
    def pending_vocabulary(self) -> list:
        """Names the clean-up model repaired that are neither known nor refused."""
        return list(self._pending_vocabulary)

    def refresh_vocabulary_suggestions(self) -> None:
        from . import vocabulary

        try:
            entries = self.history.recent(self._SUGGESTION_WINDOW)
        except Exception:  # noqa: BLE001 - a suggestion is never worth an exception
            log.debug("Nie moge odczytac historii dla propozycji slownika")
            return
        self._pending_vocabulary = vocabulary.pending(
            entries,
            # Combined: a name already in the shared file must not be offered.
            vocabulary.combined(self.config.get("transcription.vocabulary", "")),
            self.config.get("transcription.vocabulary_rejected", ""),
        )

    def _offer_vocabulary(self, raw_text: str, cleaned: str) -> None:
        """Notify only when this dictation produced something new."""
        if not self.config.get("transcription.suggest_vocabulary", True):
            return
        from . import vocabulary

        before = {s.corrected for s in self._pending_vocabulary}
        self.refresh_vocabulary_suggestions()
        fresh = [s for s in self._pending_vocabulary if s.corrected not in before]
        if not fresh:
            return

        found = fresh[0]
        log.info("Kandydat do slownika: %r -> %r", found.heard, found.corrected)
        self._notify(
            f"Nowa nazwa wlasna? {found.heard} -> {found.corrected}. "
            "Menu tray > Czyszczenie tekstu > Propozycje slownika."
        )

    def accept_vocabulary(self, term: str) -> None:
        from . import vocabulary

        self.set_vocabulary(vocabulary.add(self.config.get("transcription.vocabulary", ""), term))
        self.refresh_vocabulary_suggestions()

    def reject_vocabulary(self, term: str) -> None:
        """'Never ask again' for one name, so a refusal does not come back."""
        from . import vocabulary

        self.config.set(
            "transcription.vocabulary_rejected",
            vocabulary.add(self.config.get("transcription.vocabulary_rejected", ""), term),
        )
        self.refresh_vocabulary_suggestions()

    def set_suggest_vocabulary(self, enabled: bool) -> None:
        self.config.set("transcription.suggest_vocabulary", enabled)
        log.info("Propozycje slownika %s", "wlaczone" if enabled else "wylaczone")
        self._set_state(self._state)

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
