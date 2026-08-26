import argparse
import array
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import xpt2046_touch_test as touch_test  # noqa: E402


class FakeSpi:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.commands = []

    def xfer2(self, values):
        self.commands.append(values)
        return next(self.replies)


def encoded(value):
    shifted = value << 3
    return [0, (shifted >> 8) & 0xFF, shifted & 0xFF]


class Xpt2046TouchTests(unittest.TestCase):
    def test_spi_path_parser_accepts_ce1(self):
        self.assertEqual(touch_test.parse_spi_device("/dev/spidev0.1"), (0, 1))
        with self.assertRaisesRegex(ValueError, "must look like"):
            touch_test.parse_spi_device("SPI0 CE1")

    def test_12_bit_reply_decoding(self):
        for value in (0, 1, 2048, 4095):
            with self.subTest(value=value):
                self.assertEqual(touch_test.decode_12bit(encoded(value)), value)

    def test_controller_reads_median_xy_and_pressure_channels(self):
        values = (
            (100, 900, 500, 3900),
            (300, 700, 700, 3700),
            (200, 800, 600, 3800),
        )
        replies = [encoded(value) for row in values for value in row]
        spi = FakeSpi(replies)
        sample = touch_test.Xpt2046(spi).read(samples=3)

        self.assertEqual(sample, touch_test.TouchSample(200, 800, 600, 3800))
        self.assertEqual(sample.pressure, 895)
        self.assertTrue(touch_test.is_touch(sample, min_pressure=200))
        self.assertEqual(
            [command[0] for command in spi.commands[:4]],
            [
                touch_test.READ_X,
                touch_test.READ_Y,
                touch_test.READ_Z1,
                touch_test.READ_Z2,
            ],
        )

    def test_all_zero_idle_frame_is_not_reported_as_touch(self):
        idle = touch_test.TouchSample(0, 0, 0, 0)

        self.assertEqual(idle.pressure, touch_test.ADC_MAX)
        self.assertFalse(touch_test.is_touch(idle, min_pressure=200))

    def test_hardware_sampling_needs_all_three_confirmations(self):
        base = {
            "hardware": False,
            "apply": False,
            "confirm_pcs_displays_disconnected": False,
        }
        with self.assertRaisesRegex(SystemExit, "--hardware.*--apply"):
            touch_test.require_hardware_confirmation(argparse.Namespace(**base))

        confirmed = argparse.Namespace(
            hardware=True,
            apply=True,
            confirm_pcs_displays_disconnected=True,
        )
        touch_test.require_hardware_confirmation(confirmed)

    def test_common_hat_warning_covers_live_pcs_conflicts(self):
        notes = "\n".join(line[3] for line in touch_test.COMMON_HAT_LINES)
        self.assertIn("MAX7219", notes)
        self.assertIn("HD44780", notes)
        self.assertIn("fan PWM", notes)
        self.assertIn("pcs-gpio-shutdown.service", touch_test.CONFLICTING_SERVICES)

    def test_rgb565_primary_colors(self):
        self.assertEqual(touch_test.rgb565(255, 0, 0), 0xF800)
        self.assertEqual(touch_test.rgb565(0, 255, 0), 0x07E0)
        self.assertEqual(touch_test.rgb565(0, 0, 255), 0x001F)
        with self.assertRaisesRegex(ValueError, "red"):
            touch_test.rgb565(256, 0, 0)

    def test_pattern_has_expected_size_and_visible_variation(self):
        pixels = touch_test.test_pattern(80, 40)
        self.assertIsInstance(pixels, array.array)
        self.assertEqual(len(pixels), 80 * 40)
        self.assertGreater(len(set(pixels)), 4)

    def test_mpi3501_vendor_calibration_maps_corners_and_center(self):
        self.assertEqual(
            touch_test.mpi3501_screen_point(
                touch_test.MPI3501_X_MAX,
                touch_test.MPI3501_Y_MIN,
                320,
                480,
            ),
            (0, 0),
        )
        self.assertEqual(
            touch_test.mpi3501_screen_point(
                touch_test.MPI3501_X_MIN,
                touch_test.MPI3501_Y_MAX,
                320,
                480,
            ),
            (319, 479),
        )
        center = touch_test.mpi3501_screen_point(2082, 2074, 320, 480)
        self.assertAlmostEqual(center[0], 160, delta=1)
        self.assertAlmostEqual(center[1], 240, delta=1)

    def test_touch_target_pattern_has_five_visible_targets(self):
        pixels = touch_test.touch_target_pattern(320, 480)
        self.assertEqual(len(pixels), 320 * 480)
        self.assertGreater(len(set(pixels)), 2)


if __name__ == "__main__":
    unittest.main()
