"""Stream format and host-API negotiation.

Regression cover for a failure seen in the wild on a WASAPI conference mic:

    PortAudioError: Error starting stream:
      AUDCLNT_E_UNSUPPORTED_FORMAT [Windows WASAPI error -2004287480]

Three facts measured on real hardware shape the expected behaviour:

* WASAPI accepts its mix format (48 kHz) and rejects 16 kHz and 44.1 kHz
  outright, at open.
* WASAPI intermittently rejects even 48 kHz, at start, while another process
  reconfigures the device. The same request succeeds a minute later.
* DirectSound and MME expose the same physical microphone and accept every
  rate and channel count tried.

So recovery means crossing to another host API, not retrying one forever.
"""

import unittest
from unittest import mock

import numpy as np
import sounddevice as sd

from whisperdictate import audio
from whisperdictate.audio import AudioError, DeviceInfo, Recorder, _formats_for, _same_physical_device

WASAPI_ANKER = DeviceInfo(25, "Mikrofon (Anker PowerConf C200)", 2, 48000.0, "Windows WASAPI", False)
DSOUND_ANKER = DeviceInfo(12, "Mikrofon (Anker PowerConf C200)", 4, 44100.0, "Windows DirectSound", False)
MME_ANKER = DeviceInfo(2, "Mikrofon (Anker PowerConf C200)", 4, 44100.0, "MME", False)
WDMKS_ANKER = DeviceInfo(41, "Mikrofon 1 (Anker PowerConf C200)", 2, 48000.0, "Windows WDM-KS", False)
WASAPI_INTEL = DeviceInfo(26, "Zestaw mikrofonow (Technologia Intel Smart Sound)", 2, 48000.0, "Windows WASAPI", False)
MME_INTEL = DeviceInfo(3, "Zestaw mikrofonow (Technologia ", 4, 44100.0, "MME", False)

ALL_DEVICES = [MME_ANKER, MME_INTEL, DSOUND_ANKER, WASAPI_ANKER, WASAPI_INTEL, WDMKS_ANKER]


class SiblingMatchingTest(unittest.TestCase):
    def test_truncated_mme_name_matches_full_wasapi_name(self):
        """MME cuts names at 31 characters; it is still the same microphone."""
        self.assertTrue(
            _same_physical_device(
                "Zestaw mikrofonow (Technologia ",
                "Zestaw mikrofonow (Technologia Intel Smart Sound)",
            )
        )

    def test_different_devices_do_not_match(self):
        self.assertFalse(_same_physical_device("Mikrofon (Anker PowerConf C200)", "Miks stereo (Realtek)"))

    def test_short_names_do_not_match_promiscuously(self):
        self.assertFalse(_same_physical_device("Mic", "Microphone Array"))

    def test_wdmks_per_channel_entry_is_not_a_sibling(self):
        """'Mikrofon 1 (Anker...)' is a channel pair, not the device."""
        self.assertFalse(
            _same_physical_device("Mikrofon (Anker PowerConf C200)", "Mikrofon 1 (Anker PowerConf C200)")
        )


class SiblingOrderingTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(audio, "list_input_devices", lambda **_: ALL_DEVICES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_orders_wasapi_then_directsound_then_mme(self):
        order = [(d.index, d.host_api) for d in audio.sibling_devices(WASAPI_ANKER)]
        self.assertEqual(order, [(25, "Windows WASAPI"), (12, "Windows DirectSound"), (2, "MME")])

    def test_excludes_wdmks(self):
        self.assertNotIn(41, [d.index for d in audio.sibling_devices(WASAPI_ANKER)])

    def test_resolved_device_leads_even_from_a_lower_ranked_api(self):
        order = [d.index for d in audio.sibling_devices(MME_ANKER)]
        self.assertEqual(order[0], 2)
        self.assertEqual(sorted(order), [2, 12, 25])


class FormatLadderTest(unittest.TestCase):
    def test_native_rate_leads_then_stereo_then_16k(self):
        self.assertEqual(
            _formats_for(25, 48000, 2),
            [(25, 48000, 1), (25, 48000, 2), (25, 16000, 1)],
        )

    def test_mono_device_gets_no_stereo_candidate(self):
        self.assertEqual(_formats_for(27, 48000, 1), [(27, 48000, 1), (27, 16000, 1)])

    def test_native_16k_device_is_not_offered_16k_twice(self):
        self.assertEqual(_formats_for(30, 16000, 1), [(30, 16000, 1)])


class FakeStream:
    def __init__(self, *, fail_on_start=False, **kwargs):
        self.samplerate = kwargs["samplerate"]
        self.channels = kwargs["channels"]
        self.device = kwargs["device"]
        self._fail_on_start = fail_on_start
        self.started = False
        self.closed = False

    def start(self):
        if self._fail_on_start:
            raise sd.PortAudioError("Error starting stream: AUDCLNT_E_UNSUPPORTED_FORMAT")
        self.started = True

    def close(self):
        self.closed = True


class OpenStreamTest(unittest.TestCase):
    """Mirrors the measured hardware: WASAPI is 48 kHz-only, the rest accept anything."""

    def setUp(self):
        for target in (audio, ):
            patcher = mock.patch.object(target, "list_input_devices", lambda **_: ALL_DEVICES)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(audio, "find_device", lambda _: WASAPI_ANKER)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep the retry pass instant.
        patcher = mock.patch.object(audio.time, "sleep", lambda _: None)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.attempts = []
        self.opened = []

    def install(self, verdict):
        """verdict(device, rate, channels) -> "ok" | "open" | "start"."""

        def factory(**kwargs):
            key = (kwargs["device"], kwargs["samplerate"], kwargs["channels"])
            self.attempts.append(key)
            result = verdict(*key)
            if result == "open":
                raise sd.PortAudioError("Error opening InputStream: Invalid sample rate")
            stream = FakeStream(fail_on_start=(result == "start"), **kwargs)
            self.opened.append(stream)
            return stream

        patcher = mock.patch.object(audio.sd, "InputStream", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def recorder(self):
        return Recorder(device="Anker")

    def test_happy_path_takes_wasapi_native_mono(self):
        self.install(lambda d, r, c: "ok")
        _, rate, channels = self.recorder()._open_stream()
        self.assertEqual((rate, channels), (48000, 1))
        self.assertEqual(self.attempts, [(25, 48000, 1)])

    def test_wasapi_rejecting_16k_is_never_asked_for_it_first(self):
        """Leading with 16 kHz would waste the attempt most likely to succeed."""
        self.install(lambda d, r, c: "open" if (d == 25 and r != 48000) else "ok")
        _, rate, _ = self.recorder()._open_stream()
        self.assertEqual(rate, 48000)
        self.assertEqual(self.attempts[0], (25, 48000, 1))

    def test_falls_across_to_directsound_when_wasapi_fails_at_start(self):
        """The real-world failure: WASAPI opens then refuses to start."""
        self.install(lambda d, r, c: "start" if d == 25 else "ok")
        _, rate, channels = self.recorder()._open_stream()
        self.assertEqual([a[0] for a in self.attempts][-1], 12)
        self.assertEqual((rate, channels), (44100, 1))

    def test_falls_through_to_mme_when_wasapi_and_directsound_both_fail(self):
        self.install(lambda d, r, c: "ok" if d == 2 else "start")
        _, rate, _ = self.recorder()._open_stream()
        self.assertEqual(self.attempts[-1][0], 2)
        self.assertEqual(rate, 44100)

    def test_streams_that_failed_to_start_are_closed(self):
        self.install(lambda d, r, c: "start" if d == 25 else "ok")
        self.recorder()._open_stream()
        abandoned = [s for s in self.opened if not s.started]
        self.assertTrue(abandoned)
        self.assertTrue(all(s.closed for s in abandoned))

    def test_transient_failure_recovers_on_the_second_pass(self):
        """Everything fails once, then the device settles - as observed in the log."""
        state = {"pass_one": True}

        def verdict(device, rate, channels):
            if state["pass_one"]:
                return "start"
            return "ok"

        self.install(verdict)
        recorder = self.recorder()
        # Flip after the first full ladder is exhausted.
        original = recorder._try_open
        seen = []

        def counting(index, samplerate, channels, failures):
            seen.append(index)
            if len(seen) == len(recorder._candidates(WASAPI_ANKER)):
                state["pass_one"] = False
            return original(index, samplerate, channels, failures)

        recorder._try_open = counting
        _, rate, _ = recorder._open_stream()
        self.assertTrue(rate)

    def test_total_failure_raises_audio_error_naming_every_device_tried(self):
        self.install(lambda d, r, c: "start")
        with self.assertRaises(AudioError) as ctx:
            self.recorder()._open_stream()
        message = str(ctx.exception)
        for index in (25, 12, 2):
            self.assertIn(f"[{index}]", message)

    def test_raw_portaudio_error_never_escapes(self):
        """The controller only catches AudioError; anything else hits the key hook."""
        self.install(lambda d, r, c: "start")
        with self.assertRaises(AudioError):
            self.recorder()._open_stream()

    def test_working_combination_is_cached_and_tried_first(self):
        self.install(lambda d, r, c: "ok" if d == 2 else "start")
        recorder = self.recorder()
        recorder._open_stream()
        self.assertGreater(len(self.attempts), 1)

        self.attempts.clear()
        recorder._open_stream()
        self.assertEqual(self.attempts, [(2, 44100, 1)])


class DownmixTest(unittest.TestCase):
    def test_stereo_block_is_averaged_not_left_channel_only(self):
        """A quiet capsule on one channel must not halve the recorded level."""
        recorder = Recorder()
        recorder._capture_rate = 48000
        recorder._callback(np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32), 2, None, None)
        np.testing.assert_allclose(recorder._chunks[0], [0.5, 0.5])

    def test_mono_block_passes_through(self):
        recorder = Recorder()
        recorder._capture_rate = 16000
        recorder._callback(np.array([[0.25], [0.5]], dtype=np.float32), 2, None, None)
        np.testing.assert_allclose(recorder._chunks[0], [0.25, 0.5])


if __name__ == "__main__":
    unittest.main()
