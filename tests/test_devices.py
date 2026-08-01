"""Microphone resolution.

The real device list is hardware- and machine-dependent, so these substitute a
fixture that mirrors the shape Windows actually produces: the same physical
microphone repeated across MME (name truncated at 31 chars), DirectSound,
WASAPI, and WDM-KS (split per channel pair).
"""

import unittest
from unittest import mock

from whisperdictate import audio
from whisperdictate.audio import DeviceInfo, find_device


def device(index, name, host_api, channels=2, rate=48000.0, default=False):
    return DeviceInfo(
        index=index, name=name, channels=channels,
        default_samplerate=rate, host_api=host_api, is_default=default,
    )


ALL_DEVICES = [
    device(0, "Microsoft Sound Mapper - Input", "MME", rate=44100.0),
    device(1, "Mikrofon (Virtual Desktop Audio", "MME", channels=1, rate=44100.0, default=True),
    device(2, "Mikrofon (Anker PowerConf C200)", "MME", channels=4, rate=44100.0),
    device(12, "Mikrofon (Anker PowerConf C200)", "Windows DirectSound", channels=4, rate=44100.0),
    device(25, "Mikrofon (Anker PowerConf C200)", "Windows WASAPI"),
    device(26, "Zestaw mikrofonow (Intel Smart Sound)", "Windows WASAPI"),
    device(27, "Mikrofon (Virtual Desktop Audio)", "Windows WASAPI", channels=1),
    device(41, "Mikrofon 1 (Anker PowerConf C200)", "Windows WDM-KS"),
]

WASAPI_DEVICES = [d for d in ALL_DEVICES if d.host_api == "Windows WASAPI"]


def patched_list(*, all_host_apis=False):
    return ALL_DEVICES if all_host_apis else WASAPI_DEVICES


class FindDeviceTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(audio, "list_input_devices", patched_list)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_none_means_system_default(self):
        self.assertIsNone(find_device(None))

    def test_exact_name_prefers_wasapi_over_mme(self):
        """MME lists the same mic at index 2; WASAPI (25) is the better path."""
        found = find_device("Mikrofon (Anker PowerConf C200)")
        self.assertEqual(found.index, 25)
        self.assertEqual(found.host_api, "Windows WASAPI")

    def test_substring_match(self):
        self.assertEqual(find_device("anker").index, 25)

    def test_match_is_case_insensitive(self):
        self.assertEqual(find_device("ANKER POWERCONF").index, 25)

    def test_exact_match_beats_substring(self):
        """'Mikrofon 1 (Anker...)' contains the shorter name, but is not it."""
        self.assertEqual(find_device("Mikrofon (Anker PowerConf C200)").index, 25)

    def test_falls_back_to_other_host_apis_when_wasapi_lacks_it(self):
        found = find_device("Sound Mapper")
        self.assertEqual(found.index, 0)
        self.assertEqual(found.host_api, "MME")

    def test_unknown_name_returns_none(self):
        self.assertIsNone(find_device("Blue Yeti"))

    def test_index_lookup_searches_every_host_api(self):
        self.assertEqual(find_device(41).name, "Mikrofon 1 (Anker PowerConf C200)")

    def test_unknown_index_returns_none(self):
        self.assertIsNone(find_device(999))

    def test_bool_is_not_treated_as_an_index(self):
        """True == 1 in Python; it must not silently select device 1."""
        self.assertIsNone(find_device(True))


class RecorderDeviceSpecTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(audio, "list_input_devices", patched_list)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_device_falls_back_with_a_note(self):
        recorder = audio.Recorder(device="Blue Yeti")
        with mock.patch.object(audio, "refresh_devices") as refresh:
            self.assertIsNone(recorder._resolve_device_index())
            refresh.assert_called_once()  # re-scan before giving up
        self.assertIn("Blue Yeti", recorder.fallback_note)

    def test_present_device_resolves_without_a_note(self):
        recorder = audio.Recorder(device="Anker")
        self.assertEqual(recorder._resolve_device_index(), 25)
        self.assertIsNone(recorder.fallback_note)

    def test_none_spec_never_scans(self):
        recorder = audio.Recorder(device=None)
        with mock.patch.object(audio, "refresh_devices") as refresh:
            self.assertIsNone(recorder._resolve_device_index())
            refresh.assert_not_called()

    def test_device_spec_is_settable(self):
        recorder = audio.Recorder(device=None)
        recorder.device_spec = "Anker"
        self.assertEqual(recorder._resolve_device_index(), 25)


if __name__ == "__main__":
    unittest.main()
