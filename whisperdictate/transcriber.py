"""faster-whisper wrapper: lazy model loading, CUDA with graceful CPU fallback."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import paths, runtime_cuda
from .i18n import t

log = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """The model could not be loaded or decoding failed."""


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float
    audio_seconds: float
    elapsed_seconds: float

    @property
    def speedup(self) -> float:
        """How many times faster than real time. Useful in logs and history."""
        return self.audio_seconds / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0


class Transcriber:
    """Owns the WhisperModel. Loading is deferred and reference-counted by a lock.

    The model is several GB of weights; constructing it takes seconds on first
    run (download) and ~1-2 s afterwards. The app preloads it in the background
    at startup so the first dictation is not the one that pays for it.
    """

    def __init__(
        self,
        model: str,
        *,
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 5,
        vad_filter: bool = True,
        initial_prompt: str = "",
    ):
        self.model_name = model
        self.device_pref = device
        self.compute_type_pref = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.initial_prompt = initial_prompt or None

        self._model: Any = None
        self._lock = threading.RLock()
        self._active_device = "?"
        self._active_compute = "?"

    # -- introspection --------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def description(self) -> str:
        if not self.is_loaded:
            return f"{self.model_name} (not loaded)"
        return f"{self.model_name} @ {self._active_device}/{self._active_compute}"

    # -- loading --------------------------------------------------------

    def ensure_loaded(self) -> None:
        """Idempotent, thread-safe. Raises TranscriptionError if the model cannot load."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return

            # Must precede the faster_whisper import: it pulls in ctranslate2,
            # which resolves its CUDA DLLs once, at import time.
            runtime_cuda.enable_cuda_dlls()

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(t("error.no_faster_whisper")) from exc

            device, compute = self._resolve_backend()
            started = time.perf_counter()

            try:
                self._model = self._construct(WhisperModel, device, compute)
            except Exception as exc:  # noqa: BLE001 - ctranslate2 raises bare RuntimeError
                if device != "cuda":
                    raise TranscriptionError(
                        t("error.model_load", model=self.model_name, error=exc)
                    ) from exc
                log.warning("Model failed to start on CUDA (%s) - falling back to CPU", exc)
                device, compute = "cpu", self._compute_for("cpu")
                try:
                    self._model = self._construct(WhisperModel, device, compute)
                except Exception as cpu_exc:  # noqa: BLE001
                    raise TranscriptionError(
                        t("error.model_load_anywhere", model=self.model_name, error=cpu_exc)
                    ) from cpu_exc

            self._active_device, self._active_compute = device, compute
            log.info(
                "Model %s loaded on %s/%s in %.1f s",
                self.model_name, device, compute, time.perf_counter() - started,
            )

    def _construct(self, whisper_model_cls, device: str, compute: str):  # noqa: ANN001
        return whisper_model_cls(
            self.model_name,
            device=device,
            compute_type=compute,
            download_root=str(paths.model_cache_dir()),
        )

    def _resolve_backend(self) -> tuple[str, str]:
        device = self.device_pref
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
            log.info("Detected backend: %s", device)
        elif device == "cuda" and not _cuda_available():
            log.warning("device=cuda was forced, but CTranslate2 sees no GPU - trying anyway")
        return device, self._compute_for(device)

    def _compute_for(self, device: str) -> str:
        if self.compute_type_pref != "auto":
            return self.compute_type_pref
        # float16 is the sweet spot on any RTX card; int8 keeps CPU usable.
        return "float16" if device == "cuda" else "int8"

    def reload(self, *, model: str | None = None) -> None:
        """Swap the model, freeing the old one first so both never sit in VRAM."""
        with self._lock:
            if model is not None:
                self.model_name = model
            self._model = None
            self._active_device = self._active_compute = "?"
        self.ensure_loaded()

    # -- inference ------------------------------------------------------

    def transcribe(self, audio: np.ndarray, language: str) -> TranscriptionResult:
        self.ensure_loaded()
        audio_seconds = audio.size / 16_000
        started = time.perf_counter()

        try:
            with self._lock:
                segments, info = self._model.transcribe(
                    audio,
                    # "auto" means let Whisper detect. Never leave this implicit:
                    # the CLI's own default is "en", which translates rather than
                    # transcribes anything that is not English.
                    language=None if language == "auto" else language,
                    beam_size=self.beam_size,
                    vad_filter=self.vad_filter,
                    initial_prompt=self.initial_prompt,
                    # Each dictation is independent. Carrying context across
                    # utterances is the main driver of repetition loops.
                    condition_on_previous_text=False,
                )
                text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001 - ctranslate2 raises bare RuntimeError
            raise TranscriptionError(t("error.transcription_failed", error=exc)) from exc

        result = TranscriptionResult(
            text=text,
            language=getattr(info, "language", language) or language,
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            audio_seconds=audio_seconds,
            elapsed_seconds=time.perf_counter() - started,
        )
        log.info(
            "Transcription: %.1f s of audio in %.2f s (%.1fx realtime), %d characters",
            result.audio_seconds, result.elapsed_seconds, result.speedup, len(result.text),
        )
        return result


def _cuda_available() -> bool:
    runtime_cuda.enable_cuda_dlls()
    try:
        import ctranslate2
    except ImportError:
        return False
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception as exc:  # noqa: BLE001 - missing DLLs surface as RuntimeError/OSError
        log.info("CUDA probe failed (%s) - assuming CPU", exc)
        return False
