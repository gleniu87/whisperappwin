"""Stream format negotiation.

Regression cover for a failure seen in the wild on a WASAPI conference mic:

    PortAudioError: Error starting stream:
      AUDCLNT_E_UNSUPPORTED_FORMAT [Windows WASAPI error -2004287480]

WASAPI validates a format lazily, so InputStream(...) succeeds and .start()
is what fails. If starting is not part of the attempt, the error escapes as a
raw PortAudioError instead of a handled AudioError - and the fallback ladder
never runs.
"""

import unittest
from unittest import mock

import sounddevice as sd

from whisperdictate import audio
from whisperdictate.audio import AudioError, Recorder


class FakeStream:
    """Stands in for sd.InputStream, failing at construction or at start()."""

    def __init__(self, *, fail_on_start=False, **kwargs):
        self.samplerate = kwargs["samplerate"]
        self.channels = kwargs["channels"]
        self._fail_on_start = fail_on_start
        self.started = False
        self.closed = False

    def start(self):
        if self._fail_on_start:
            raise sd.PortAudioError("Error starting stream: AUDCLNT_E_UNSUPPORTED_FORMAT")
        self.started = True

    def close(self):
        self.closed = True


class FormatNegotiationTest(unittest.TestCase):
    def setUp(self):
        # A stereo 48 kHz device, like the Anker PowerConf over WASAPI.
        patcher = mock.patch.object(
            audio.sd, "query_devices",
            lambda *_a, **_k: {"default_samplerate": 48000.0, "max_input_channels": 2},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.attempts = []

    def install(self, accept):
        """Route InputStream construction through `accept(rate, channels) -> str`.

        Returns "ok", "open" (fail at construction) or "start" (fail at start).
        """

        def factory(**kwargs):
            rate, channels = kwargs["samplerate"], kwargs["channels"]
            self.attempts.append((rate, channels))
            verdict = accept(rate, channels)
            if verdict == "open":
                raise sd.PortAudioError("Error opening InputStream: Invalid sample rate")
            return FakeStream(fail_on_start=(verdict == "start"), **kwargs)

        patcher = mock.patch.object(audio.sd, "InputStream", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefers_16k_mono_when_the_device_accepts_it(self):
        self.install(lambda r, c: "ok")
        stream, rate, channels = Recorder()._open_stream(25)
        self.assertEqual((rate, channels), (16000, 1))
        self.assertTrue(stream.started)
        self.assertEqual(self.attempts, [(16000, 1)])

    def test_falls_back_to_native_rate_when_16k_is_rejected(self):
        self.install(lambda r, c: "open" if r == 16000 else "ok")
        _, rate, channels = Recorder()._open_stream(25)
        self.assertEqual((rate, channels), (48000, 1))

    def test_failure_at_start_falls_through_instead_of_escaping(self):
        """The exact bug: mono opens fine, then start() rejects it."""
        self.install(lambda r, c: "start" if c == 1 else "ok")
        _, rate, channels = Recorder()._open_stream(25)
        self.assertEqual(channels, 2)
        self.assertEqual(rate, 48000)

    def test_stream_that_failed_to_start_is_closed(self):
        opened = []

        def factory(**kwargs):
            stream = FakeStream(fail_on_start=(kwargs["channels"] == 1), **kwargs)
            opened.append(stream)
            return stream

        with mock.patch.object(audio.sd, "InputStream", factory):
            Recorder()._open_stream(25)

        self.assertTrue(opened[0].closed, "porzucony strumien musi byc zamkniety")

    def test_all_formats_rejected_raises_audio_error_listing_attempts(self):
        self.install(lambda r, c: "start")
        with self.assertRaises(AudioError) as ctx:
            Recorder()._open_stream(25)
        message = str(ctx.exception)
        self.assertIn("16000 Hz / 1 ch", message)
        self.assertIn("48000 Hz / 2 ch", message)

    def test_raw_portaudio_error_never_escapes(self):
        """The controller only catches AudioError; anything else reaches the key hook."""
        self.install(lambda r, c: "start")
        with self.assertRaises(AudioError):
            Recorder()._open_stream(25)

    def test_working_format_is_cached_and_tried_first(self):
        self.install(lambda r, c: "start" if c == 1 else "ok")
        recorder = Recorder()
        recorder._open_stream(25)
        first_pass = len(self.attempts)
        self.assertGreater(first_pass, 1, "pierwsze przejscie ma isc po drabince")

        self.attempts.clear()
        recorder._open_stream(25)
        self.assertEqual(self.attempts, [(48000, 2)], "drugie przejscie ma trafic od razu")

    def test_mono_device_never_offers_a_stereo_candidate(self):
        with mock.patch.object(
            audio.sd, "query_devices",
            lambda *_a, **_k: {"default_samplerate": 44100.0, "max_input_channels": 1},
        ):
            candidates = Recorder()._candidate_formats(27)
        self.assertEqual(candidates, [(16000, 1), (44100, 1)])

    def test_unreadable_device_falls_back_to_a_sane_assumption(self):
        with mock.patch.object(audio.sd, "query_devices", side_effect=sd.PortAudioError("gone")):
            self.assertEqual(Recorder()._device_format(99), (48000, 2))


class DownmixTest(unittest.TestCase):
    def test_stereo_block_is_averaged_not_left_channel_only(self):
        """A quiet capsule on one channel must not halve the recorded level."""
        import numpy as np

        recorder = Recorder()
        recorder._capture_rate = 48000
        stereo = np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32)
        recorder._callback(stereo, 2, None, None)

        self.assertEqual(len(recorder._chunks), 1)
        np.testing.assert_allclose(recorder._chunks[0], [0.5, 0.5])

    def test_mono_block_passes_through(self):
        import numpy as np

        recorder = Recorder()
        recorder._capture_rate = 16000
        mono = np.array([[0.25], [0.5]], dtype=np.float32)
        recorder._callback(mono, 2, None, None)

        np.testing.assert_allclose(recorder._chunks[0], [0.25, 0.5])


if __name__ == "__main__":
    unittest.main()
