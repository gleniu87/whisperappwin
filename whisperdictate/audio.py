"""Microphone capture via PortAudio (sounddevice).

Produces exactly what faster-whisper wants: mono float32 at 16 kHz in memory.
No WAV round-trip - the macOS original writes a temp file only because
whisper-cli is a separate process.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from .i18n import t

log = logging.getLogger(__name__)

TARGET_RATE = 16_000

# Windows exposes the same physical microphone through four host APIs. WASAPI is
# the one worth showing: it is the modern path, reports the true sample rate, and
# gives one entry per device with an untruncated name. MME truncates names at 31
# characters ("Mikrofon (Virtual Desktop Audio" - note the missing bracket) and
# WDM-KS splits a multi-channel device into one entry per channel pair.
_PREFERRED_HOST_API = "Windows WASAPI"

# Fallback order when the preferred path refuses to open. WASAPI is strict: on a
# typical USB conference microphone it accepts its mix format (48 kHz) and
# nothing else, and it transiently returns AUDCLNT_E_UNSUPPORTED_FORMAT while
# another process reconfigures the device. DirectSound and MME wrap the same
# physical hardware, resample internally, and accept essentially any format -
# worse latency, but they work when WASAPI will not.
#
# WDM-KS is excluded on purpose: it is exclusive-mode, mostly answers "Invalid
# device", and splits a multi-capsule microphone into per-channel-pair entries.
_FALLBACK_HOST_APIS = ("Windows WASAPI", "Windows DirectSound", "MME")

# Shortest name prefix that may be treated as the same physical device. MME
# truncates device names at 31 characters, so sibling entries are matched by
# prefix rather than equality.
_SIBLING_MIN_PREFIX = 8

_RETRY_PASSES = 2
_RETRY_DELAY_S = 0.25


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
        marker = " (default)" if self.is_default else ""
        return (
            f"[{self.index}] {self.name} - {self.channels} channel(s) "
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
        log.debug("Audio device list refreshed")
    except Exception as exc:  # noqa: BLE001 - PortAudio internals
        log.warning("Cannot refresh the audio device list: %s", exc)


def list_input_devices(*, all_host_apis: bool = False) -> list[DeviceInfo]:
    """Input devices, WASAPI-only by default.

    Pass all_host_apis=True for diagnostics, where seeing every duplicate is
    the point.
    """
    try:
        host_apis = sd.query_hostapis()
        default_index = sd.default.device[0]
    except Exception as exc:  # noqa: BLE001 - PortAudio not initialised
        log.warning("Cannot read the audio device list: %s", exc)
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


def _same_physical_device(a: str, b: str) -> bool:
    """Whether two PortAudio entries plausibly wrap the same hardware.

    Prefix comparison, not equality: MME truncates names at 31 characters, so
    "Zestaw mikrofonow (Technologia " and the full WASAPI name are the same mic.
    """
    x, y = " ".join(a.split()).casefold(), " ".join(b.split()).casefold()
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    return len(shorter) >= _SIBLING_MIN_PREFIX and longer.startswith(shorter)


def sibling_devices(device: DeviceInfo) -> list[DeviceInfo]:
    """The same microphone as seen through each usable host API, best first.

    This is what lets dictation survive WASAPI having a bad moment: the very
    same hardware is still reachable through DirectSound and MME.
    """
    siblings = [
        d
        for d in list_input_devices(all_host_apis=True)
        if d.host_api in _FALLBACK_HOST_APIS and _same_physical_device(d.name, device.name)
    ]
    siblings.sort(key=lambda d: (_FALLBACK_HOST_APIS.index(d.host_api), d.index))

    # The explicitly resolved device leads, whatever its host API.
    ordered = [d for d in siblings if d.index == device.index]
    ordered += [d for d in siblings if d.index != device.index]
    return ordered or [device]


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
        self._capture_channels = 1
        self._overflowed = False
        self._truncated = False
        # device index -> (samplerate, channels) known to work, so the format
        # fallback ladder is walked once instead of on every dictation.
        self._format_cache: dict[int | None, tuple[int, int]] = {}

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
            log.debug("start() on an already-recording recorder - ignoring it")
            return

        with self._lock:
            self._chunks = []
        self._level = 0.0
        self._overflowed = False
        self._truncated = False
        self.fallback_note = None

        self._stream, self._capture_rate, self._capture_channels = self._open_stream()
        log.debug(
            "Recording started @ %d Hz, %d channel(s)", self._capture_rate, self._capture_channels
        )

    def _resolve_device(self) -> DeviceInfo | None:
        """The configured microphone, or None to let Windows choose."""
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
            self.fallback_note = t("error.device_missing", device=repr(spec))
            log.warning("%s", self.fallback_note)
            return None

        log.debug("Microphone %r -> %s", spec, device)
        return device

    def _resolve_device_index(self) -> int | None:
        device = self._resolve_device()
        return device.index if device is not None else None

    def stop(self) -> np.ndarray | None:
        """Stop and return mono float32 audio at 16 kHz, or None if nothing was captured."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError as exc:  # pragma: no cover - device yanked mid-recording
                log.warning("Error while closing the audio stream: %s", exc)

        with self._lock:
            chunks, self._chunks = self._chunks, []

        if not chunks:
            return None
        if self._overflowed:
            log.warning("Audio buffer overflow - some samples may have been lost")
        if self._truncated:
            log.warning("Recording truncated at the %.0f s limit", self._max_seconds)

        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        return _resample(audio, self._capture_rate, TARGET_RATE)

    def cancel(self) -> None:
        """Stop and throw the audio away."""
        self.stop()

    # -- internals ------------------------------------------------------

    def _open_stream(self) -> tuple[sd.InputStream, int, int]:
        """Open and start a capture stream, trying host APIs and formats until one works.

        Three Windows realities drive the shape of this:

        * A format can be accepted at open and rejected at start. WASAPI
          validates lazily, so `InputStream(...)` succeeds and `.start()` then
          fails with AUDCLNT_E_UNSUPPORTED_FORMAT. Starting must be part of the
          attempt, or the failure escapes as a raw PortAudioError.
        * WASAPI accepts its mix format and nothing else, and refuses even that
          while another process is reconfiguring the device. DirectSound and MME
          expose the same hardware and accept almost anything, so falling across
          host APIs recovers where retrying one of them forever cannot.
        * The failure is often transient, so a second pass after a short pause
          is worth more than a longer ladder.

        The winning combination is cached, so the ladder is walked once rather
        than on every dictation.
        """
        primary = self._resolve_device()
        candidates = self._candidates(primary)

        cache_key = primary.index if primary is not None else None
        cached = self._format_cache.get(cache_key)
        if cached is not None and cached in candidates:
            candidates = [cached] + [c for c in candidates if c != cached]

        failures: list[str] = []
        for attempt in range(_RETRY_PASSES):
            if attempt:
                time.sleep(_RETRY_DELAY_S)
                log.info("No format worked - retrying (attempt %d)", attempt + 1)

            for index, samplerate, channels in candidates:
                stream = self._try_open(index, samplerate, channels, failures)
                if stream is None:
                    continue

                if cached != (index, samplerate, channels):
                    log.info(
                        "Audio format: device %s, %d Hz, %d channel(s)", index, samplerate, channels
                    )
                    self._format_cache[cache_key] = (index, samplerate, channels)
                return stream, samplerate, channels

        raise AudioError(t("error.microphone_open", failures="; ".join(failures)))

    def _try_open(
        self, index: int | None, samplerate: int, channels: int, failures: list[str]
    ) -> sd.InputStream | None:
        """One attempt. Returns a started stream, or None after recording why not."""
        stream = None
        try:
            stream = sd.InputStream(
                device=index,
                channels=channels,
                samplerate=samplerate,
                dtype="float32",
                blocksize=0,  # let PortAudio pick the lowest latency it can
                callback=self._callback,
            )
            # Set before start(): the callback can fire the moment the stream runs.
            self._capture_rate = samplerate
            self._capture_channels = channels
            stream.start()
        except sd.PortAudioError as exc:
            failures.append(f"[{index}] {samplerate} Hz / {channels} ch: {exc}")
            if stream is not None:
                try:
                    stream.close()
                except sd.PortAudioError:
                    pass
            return None
        return stream

    def _candidates(self, primary: DeviceInfo | None) -> list[tuple[int | None, int, int]]:
        """Ordered (device index, rate, channels) attempts across host APIs."""
        if primary is None:
            native, max_channels = self._device_format(None)
            return _formats_for(None, native, max_channels)

        candidates: list[tuple[int | None, int, int]] = []
        for device in sibling_devices(primary):
            candidates += _formats_for(device.index, int(device.default_samplerate), device.channels)

        # dict.fromkeys preserves order while removing duplicates.
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _device_format(device: int | None) -> tuple[int, int]:
        try:
            info = sd.query_devices(device if device is not None else sd.default.device[0], "input")
            return int(info["default_samplerate"]), int(info["max_input_channels"])
        except Exception as exc:  # noqa: BLE001 - PortAudio raises several types here
            log.warning("Cannot read the parameters of device %s (%s) - assuming 48 kHz stereo", device, exc)
            return 48_000, 2

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001 - sounddevice API
        if status:
            if status.input_overflow:
                self._overflowed = True
            else:
                log.debug("Status strumienia audio: %s", status)

        # Downmix rather than taking channel 0: on a multi-capsule device like a
        # conference speakerphone, one capsule can be far quieter than the others.
        block = indata[:, 0].copy() if indata.shape[1] == 1 else indata.mean(axis=1)
        self._level = float(np.sqrt(np.mean(np.square(block)))) if frames else 0.0

        max_frames = int(self._max_seconds * self._capture_rate)
        with self._lock:
            captured = sum(len(c) for c in self._chunks)
            if captured >= max_frames:
                self._truncated = True
                return
            room = max_frames - captured
            self._chunks.append(block[:room] if len(block) > room else block)


def _formats_for(index: int | None, native: int, max_channels: int) -> list[tuple[int | None, int, int]]:
    """Formats to try on one device, best first.

    Native rate leads. Capturing at 16 kHz directly would skip our resampling
    step, but WASAPI rejects anything but its mix format, so leading with 16 kHz
    just burns an attempt on the host API most likely to succeed.
    """
    formats = [(index, native, 1)]
    if max_channels > 1:
        formats.append((index, native, min(2, max_channels)))
    if native != TARGET_RATE:
        formats.append((index, TARGET_RATE, 1))
    return formats


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
