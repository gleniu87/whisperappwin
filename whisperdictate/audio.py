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

# Windows exposes the same physical microphone through four host APIs. WASAPI is
# the one worth showing: it is the modern path, reports the true sample rate, and
# gives one entry per device with an untruncated name. MME truncates names at 31
# characters ("Mikrofon (Virtual Desktop Audio" - note the missing bracket) and
# WDM-KS splits a multi-channel device into one entry per channel pair.
_PREFERRED_HOST_API = "Windows WASAPI"


class AudioError(RuntimeError):
    """Recording could not start or produced nothing usable."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    channels: int
    default_samplerate: float
    host_api: str
    is_default: bool

    def __str__(self) -> str:
        marker = " (domyslne)" if self.is_default else ""
        return (
            f"[{self.index}] {self.name} - {self.channels} kanal(y) "
            f"@ {self.default_samplerate:.0f} Hz [{self.host_api}]{marker}"
        )


def refresh_devices() -> None:
    """Re-enumerate audio hardware.

    PortAudio snapshots the device list when it initialises, so a microphone
    plugged in (or unplugged) afterwards is invisible until it is restarted.
    Only safe with no stream open - the caller is responsible for that.
    """
    try:
        sd._terminate()
        sd._initialize()
        log.debug("Lista urzadzen audio odswiezona")
    except Exception as exc:  # noqa: BLE001 - PortAudio internals
        log.warning("Nie moge odswiezyc listy urzadzen audio: %s", exc)


def list_input_devices(*, all_host_apis: bool = False) -> list[DeviceInfo]:
    """Input devices, WASAPI-only by default.

    Pass all_host_apis=True for diagnostics, where seeing every duplicate is
    the point.
    """
    try:
        host_apis = sd.query_hostapis()
        default_index = sd.default.device[0]
    except Exception as exc:  # noqa: BLE001 - PortAudio not initialised
        log.warning("Nie moge odczytac listy urzadzen audio: %s", exc)
        return []

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
                host_api=host_apis[dev["hostapi"]]["name"],
                is_default=index == default_index,
            )
        )

    if all_host_apis:
        return devices
    preferred = [d for d in devices if d.host_api == _PREFERRED_HOST_API]
    return preferred or devices


def find_device(spec: int | str | None) -> DeviceInfo | None:
    """Resolve a config value to a device, or None for the system default.

    Accepts an index or a case-insensitive name (exact match preferred over
    substring). Names are searched in the WASAPI list first so a stored name
    does not silently resolve to the same microphone's inferior MME entry.
    """
    if spec is None:
        return None

    if isinstance(spec, int) and not isinstance(spec, bool):
        for device in list_input_devices(all_host_apis=True):
            if device.index == spec:
                return device
        return None

    needle = str(spec).casefold()
    for candidates in (list_input_devices(), list_input_devices(all_host_apis=True)):
        for device in candidates:
            if device.name.casefold() == needle:
                return device
        for device in candidates:
            if needle in device.name.casefold():
                return device
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

        #: Set by start() when the configured device could not be honoured, so
        #: the caller can tell the user instead of silently recording silence
        #: from whatever Windows considers the default.
        self.fallback_note: str | None = None

    # -- lifecycle ------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def device_spec(self) -> int | str | None:
        return self._device_spec

    @device_spec.setter
    def device_spec(self, spec: int | str | None) -> None:
        """Takes effect on the next start(); an in-flight recording is untouched."""
        self._device_spec = spec

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
        self.fallback_note = None

        index = self._resolve_device_index()
        self._stream, self._capture_rate = self._open_stream(index)
        self._stream.start()
        log.debug("Nagrywanie wystartowalo @ %d Hz", self._capture_rate)

    def _resolve_device_index(self) -> int | None:
        """Index of the configured device, or None to let Windows choose."""
        spec = self._device_spec
        if spec is None:
            return None

        device = find_device(spec)
        if device is None:
            # The usual cause is a USB microphone unplugged since startup, and
            # PortAudio caches its device list - so re-enumerate before giving up.
            refresh_devices()
            device = find_device(spec)

        if device is None:
            self.fallback_note = f"Nie znaleziono mikrofonu {spec!r} - nagrywam z domyslnego systemowego"
            log.warning("%s", self.fallback_note)
            return None

        log.debug("Mikrofon %r -> %s", spec, device)
        return device.index

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
