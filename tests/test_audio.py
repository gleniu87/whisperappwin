"""Resampling. Device I/O is not covered here - it needs real hardware."""

import unittest

import numpy as np

from whisperdictate.audio import TARGET_RATE, _resample


class ResampleTest(unittest.TestCase):
    def test_same_rate_returns_input_unchanged(self):
        audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        self.assertIs(_resample(audio, TARGET_RATE, TARGET_RATE), audio)

    def test_downsample_produces_expected_length(self):
        audio = np.zeros(48_000, dtype=np.float32)  # 1 s @ 48 kHz
        self.assertEqual(_resample(audio, 48_000, 16_000).size, 16_000)

    def test_upsample_produces_expected_length(self):
        audio = np.zeros(8_000, dtype=np.float32)  # 1 s @ 8 kHz
        self.assertEqual(_resample(audio, 8_000, 16_000).size, 16_000)

    def test_output_is_float32(self):
        audio = np.zeros(48_000, dtype=np.float32)
        self.assertEqual(_resample(audio, 48_000, 16_000).dtype, np.float32)

    def test_empty_input_is_handled(self):
        self.assertEqual(_resample(np.zeros(0, dtype=np.float32), 48_000, 16_000).size, 0)

    def test_endpoints_are_preserved(self):
        audio = np.linspace(-1.0, 1.0, 48_000, dtype=np.float32)
        out = _resample(audio, 48_000, 16_000)
        self.assertAlmostEqual(float(out[0]), -1.0, places=4)
        self.assertAlmostEqual(float(out[-1]), 1.0, places=4)

    def test_sine_wave_keeps_its_shape(self):
        """A 440 Hz tone must still be a 440 Hz tone after resampling."""
        source_rate, seconds, freq = 48_000, 0.5, 440.0
        t = np.arange(int(source_rate * seconds)) / source_rate
        audio = np.sin(2 * np.pi * freq * t).astype(np.float32)

        out = _resample(audio, source_rate, TARGET_RATE)

        spectrum = np.abs(np.fft.rfft(out))
        peak_hz = np.fft.rfftfreq(out.size, 1 / TARGET_RATE)[int(np.argmax(spectrum))]
        self.assertAlmostEqual(peak_hz, freq, delta=5.0)


if __name__ == "__main__":
    unittest.main()
