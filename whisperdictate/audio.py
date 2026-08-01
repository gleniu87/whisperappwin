"""Microphone capture via PortAudio (sounddevice).

Produces exactly what faster-whisper wants: mono float32 at 16 kHz in memory.
No WAV round-trip - the macOS original writes a temp file only because
whisper-cli is a separate process.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

TARGET_RATE = 16_000


class AudioError(RuntimeError):
    """Recording could not start or produced nothing usable."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool

    def __str__(self) -> str:
        marker = " (domyslne)" if self.is_default else ""
        return f"[{self.index}] {self.name} - {self.channels} kanal(y) @ {self.default_samplerate:.0f} Hz{marker}"


def list_input_devices() -> list[DeviceInfo]:
    try:
        default_index = sd.default.device[0]
    except (TypeError, IndexError):
        default_index = None

    devices = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        devices.append(
            DeviceInfo(
                index=index,
                name=dev["name"],
                channels=dev["max_input_channels"],
                default_samplerate=dev["default_samplerate"],
                is_default=index == default_index,
            )
        )
    return devices


def resolve_device(spec: int | str | None) -> int | None:
    """Turn a config value into a PortAudio device index.

    Accepts None (system default), an index, or a case-insensitive substring of
    the device name. Falls back to the default device with a warning rather
    than refusing to start - a renamed USB mic should not brick dictation.
    """
    if spec is None:
        return None
    devices = list_input_devices()
    if isinstance(spec, int) and not isinstance(spec, bool):
        if any(d.index == spec for d in devices):
            return spec
        log.warning("Urzadzenie audio o indeksie %s nie istnieje - uzywam domyslnego", spec)
        return None

    needle = str(spec).casefold()
    for dev in devices:
        if needle in dev.name.casefold():
            log.info("Urzadzenie audio %r -> %s", spec, dev)
            return dev.index
    log.warning("Nie znaleziono urzadzenia audio pasujacego do %r - uzywam domyslnego", spec)
    return None


class Recorder:
    """One-shot recorder. Not reusable concurrently; the controller serialises calls."""

    def __init__(self, device: int | str | None = None, max_seconds: float = 300.0):
        self._device_spec = device
        self._max_seconds = max_seconds

        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._level = 0.0
        self._capture_rate = TARGET_RATE
        self._overflowed = False
        self._truncated = False

    # -- lifecycle ------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def level(self) -> float:
        """Most recent RMS amplitude, roughly 0.0-1.0. Drives the overlay meter."""
        return self._level

    def start(self) -> None:
        if self._stream is not None:
            log.debug("start() na juz nagrywajacym rekorderze - ignoruje")
            return

        with self._lock:
            self._chunks = []
        self._level = 0.0
        self._overflowed = False
        self._truncated = False

        device = resolve_device(self._device_spec)
        self._stream, self._capture_rate = self._open_stream(device)
        self._stream.start()
        log.debug("Nagrywanie wystartowalo @ %d Hz", self._capture_rate)

    def stop(self) -> np.ndarray | None:
        """Stop and return mono float32 audio at 16 kHz, or None if nothing was captured."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError as exc:  # pragma: no cover - device yanked mid-recording
                log.warning("Blad przy zamykaniu strumienia audio: %s", exc)

        with self._lock:
            chunks, self._chunks = self._chunks, []

        if not chunks:
            return None
        if self._overflowed:
            log.warning("Przepelnienie bufora audio - czesc probek moze byc zgubiona")
        if self._truncated:
            log.warning("Nagranie przycięte do limitu %.0f s", self._max_seconds)

        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        return _resample(audio, self._capture_rate, TARGET_RATE)

    def cancel(self) -> None:
        """Stop and throw the audio away."""
        self.stop()

    # -- internals ------------------------------------------------------

    def _open_stream(self, device: int | None) -> tuple[sd.InputStream, int]:
        """Prefer capturing natively at 16 kHz; fall back to the device rate.

        WASAPI shared mode usually resamples for us, but exclusive-mode devices
        and some USB interfaces reject a rate they do not support - in that case
        we take whatever they offer and resample ourselves.
        """
        try:
            return self._make_stream(device, TARGET_RATE), TARGET_RATE
        except sd.PortAudioError as exc:
            log.info("Urzadzenie nie przyjmuje 16 kHz (%s) - probuje czestotliwosc natywna", exc)

        try:
            info = sd.query_devices(device if device is not None else sd.default.device[0], "input")
            native = int(info["default_samplerate"])
        except (sd.PortAudioError, TypeError, KeyError, IndexError) as exc:
            raise AudioError(f"Nie moge odczytac parametrow urzadzenia audio: {exc}") from exc

        try:
            return self._make_stream(device, native), native
        except sd.PortAudioError as exc:
            raise AudioError(f"Nie moge otworzyc mikrofonu: {exc}") from exc

    def _make_stream(self, device: int | None, samplerate: int) -> sd.InputStream:
        return sd.InputStream(
            device=device,
            channels=1,
            samplerate=samplerate,
            dtype="float32",
            blocksize=0,  # let PortAudio choose; lowest-latency it can manage
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001 - sounddevice API
        if status:
            if status.input_overflow:
                self._overflowed = True
            else:
                log.debug("Status strumienia audio: %s", status)

        block = indata[:, 0].copy()
        self._level = float(np.sqrt(np.mean(np.square(block)))) if frames else 0.0

        max_frames = int(self._max_seconds * self._capture_rate)
        with self._lock:
            captured = sum(len(c) for c in self._chunks)
            if captured >= max_frames:
                self._truncated = True
                return
            room = max_frames - captured
            self._chunks.append(block[:room] if len(block) > room else block)


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resampling.

    Good enough here: Whisper's own front-end is a mel spectrogram at 16 kHz, and
    speech energy sits well below the Nyquist limit, so the aliasing a proper
    polyphase filter would remove is inaudible to the model. Avoids a scipy dependency.
    """
    if source_rate == target_rate or audio.size == 0:
        return audio

    duration = audio.size / source_rate
    target_len = int(round(duration * target_rate))
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)

    source_x = np.arange(audio.size, dtype=np.float64)
    target_x = np.linspace(0, audio.size - 1, target_len, dtype=np.float64)
    return np.interp(target_x, source_x, audio).astype(np.float32)
