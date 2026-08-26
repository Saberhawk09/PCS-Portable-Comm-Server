#!/usr/bin/env python3
"""Guarded raw-touch test for an uncommissioned XPT2046 controller.

The default preflight is read-only. The sample command clocks SPI0 CE1 and
therefore requires explicit hardware confirmations. It does not initialize or
test the LCD controller paired with the XPT2046.
"""

from __future__ import annotations

import argparse
import array
import importlib.util
import json
import mmap
import os
import platform
import re
import select
import shutil
import statistics
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


DEFAULT_SPI_DEVICE = "/dev/spidev0.1"
DEFAULT_SPI_HZ = 500_000
DEFAULT_FRAMEBUFFER = "/dev/fb0"
ADC_MAX = 4095

# Vendor's 90-degree MPI3501 calibration profile. These remain raw bounds;
# they are used only to place diagnostic dots and do not configure the desktop.
MPI3501_X_MIN = 227
MPI3501_X_MAX = 3936
MPI3501_Y_MIN = 268
MPI3501_Y_MAX = 3880

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0
ABS_X = 0
ABS_Y = 1
ABS_PRESSURE = 24
BTN_TOUCH = 330

# XPT2046 control bytes: start bit, 12-bit differential conversion, power down.
READ_X = 0xD0
READ_Y = 0x90
READ_Z1 = 0xB0
READ_Z2 = 0xC0

CONFLICTING_SERVICES = (
    "pcs-gpio-lcd.service",
    "pcs-gpio-stats.service",
    "pcs-gpio-fan.service",
    "pcs-gpio-shutdown.service",
)

# Representative direct-plug 3.5-inch XPT2046 HAT wiring. Exact boards vary.
COMMON_HAT_LINES = (
    ("TP_CS", 7, 26, "normally SPI0 CE1; currently unallocated by PCS"),
    ("LCD_CS", 8, 24, "conflicts with PCS MAX7219 CS/CE0"),
    ("MISO", 9, 21, "currently unallocated by PCS"),
    ("MOSI", 10, 19, "shared SPI line used by PCS MAX7219"),
    ("SCLK", 11, 23, "shared SPI line used by PCS MAX7219"),
    ("TP_IRQ", 17, 11, "commonly conflicts with PCS HD44780 enable"),
    ("LCD_BL", 18, 12, "used by some variants; conflicts with PCS fan PWM"),
    ("LCD_DC", 22, 15, "used by some variants; conflicts with PCS LCD D5"),
    ("LCD_DC/RS", 24, 18, "used by some variants; conflicts with PCS LCD D7"),
    ("LCD_RST", 27, 13, "used by some variants; conflicts with PCS LCD D4"),
)


class SpiDevice(Protocol):
    max_speed_hz: int
    mode: int
    bits_per_word: int

    def open(self, bus: int, chip_select: int) -> None: ...
    def xfer2(self, values: list[int]) -> list[int]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class TouchSample:
    x: int
    y: int
    z1: int
    z2: int

    @property
    def pressure(self) -> int:
        """Return a useful raw contact score, not a calibrated force value."""

        return max(0, self.z1 + ADC_MAX - self.z2)


def is_touch(sample: TouchSample, min_pressure: int) -> bool:
    """Reject an all-zero bus/idle frame before applying the pressure gate."""

    has_coordinate_activity = sample.x != 0 or sample.y != 0
    return has_coordinate_activity and sample.pressure >= min_pressure


def parse_spi_device(device: str) -> tuple[int, int]:
    match = re.fullmatch(r"/dev/spidev(\d+)\.(\d+)", device)
    if not match:
        raise ValueError("SPI device must look like /dev/spidev0.1")
    return int(match.group(1)), int(match.group(2))


def decode_12bit(reply: Sequence[int]) -> int:
    if len(reply) != 3:
        raise ValueError("XPT2046 transfer must return exactly three bytes")
    return (((int(reply[1]) << 8) | int(reply[2])) >> 3) & ADC_MAX


class Xpt2046:
    def __init__(self, spi: SpiDevice):
        self.spi = spi

    def read_channel(self, command: int) -> int:
        return decode_12bit(self.spi.xfer2([command, 0x00, 0x00]))

    def read(self, samples: int = 5) -> TouchSample:
        if samples < 1:
            raise ValueError("samples must be at least 1")
        readings = [
            (
                self.read_channel(READ_X),
                self.read_channel(READ_Y),
                self.read_channel(READ_Z1),
                self.read_channel(READ_Z2),
            )
            for _ in range(samples)
        ]
        columns = zip(*readings)
        values = [int(statistics.median(column)) for column in columns]
        return TouchSample(*values)


def raspberry_pi_model() -> str:
    model_path = Path("/proc/device-tree/model")
    try:
        return model_path.read_text(encoding="ascii").rstrip("\x00\n")
    except (OSError, UnicodeError):
        return platform.platform()


def service_state(service: str) -> str:
    if not shutil.which("systemctl"):
        return "unavailable"
    result = subprocess.run(
        ["systemctl", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    return state or "unknown"


def preflight(device: str = DEFAULT_SPI_DEVICE) -> dict[str, object]:
    try:
        bus, chip_select = parse_spi_device(device)
        device_error = None
    except ValueError as error:
        bus, chip_select = -1, -1
        device_error = str(error)

    device_path = Path(device)
    states = {name: service_state(name) for name in CONFLICTING_SERVICES}
    blockers: list[str] = []
    if device_error:
        blockers.append(device_error)
    if importlib.util.find_spec("spidev") is None:
        blockers.append("Python spidev module is unavailable")
    if not device_path.exists():
        blockers.append(f"{device} is unavailable")
    elif not os.access(device_path, os.R_OK | os.W_OK):
        blockers.append(f"current user cannot open {device} read/write")
    active = [name for name, state in states.items() if state == "active"]
    if active:
        blockers.append("conflicting PCS services are active: " + ", ".join(active))

    return {
        "model": raspberry_pi_model(),
        "spi_device": device,
        "spi_bus": bus,
        "spi_chip_select": chip_select,
        "spidev_module": importlib.util.find_spec("spidev") is not None,
        "device_exists": device_path.exists(),
        "device_read_write": device_path.exists()
        and os.access(device_path, os.R_OK | os.W_OK),
        "service_states": states,
        "blockers": blockers,
        "ready_for_guarded_sampling": not blockers,
        "hardware_accessed": False,
    }


def print_preflight(report: dict[str, object]) -> None:
    print("=== XPT2046 raw-touch preflight ===")
    print(f"Host: {report['model']}")
    print(
        f"SPI target: {report['spi_device']} "
        f"(exists={str(report['device_exists']).lower()}, "
        f"read/write={str(report['device_read_write']).lower()})"
    )
    print(f"Python spidev: {'available' if report['spidev_module'] else 'missing'}")
    print("PCS service state:")
    for name, state in report["service_states"].items():
        print(f"  {name}: {state}")
    print("Representative HAT lines (verify the exact board label/pinout):")
    for function, gpio, physical, note in COMMON_HAT_LINES:
        print(f"  {function:9} BCM {gpio:2} / pin {physical:2}: {note}")
    if report["blockers"]:
        print("Not ready for raw sampling:")
        for blocker in report["blockers"]:
            print(f"  - {blocker}")
    else:
        print("Ready for guarded raw sampling.")
    print("Hardware accessed: no")


def require_hardware_confirmation(args: argparse.Namespace) -> None:
    missing: list[str] = []
    if not args.hardware:
        missing.append("--hardware")
    if not args.apply:
        missing.append("--apply")
    if not args.confirm_pcs_displays_disconnected:
        missing.append("--confirm-pcs-displays-disconnected")
    if missing:
        raise SystemExit(
            "ERROR: raw sampling clocks the SPI bus; add " + " ".join(missing)
        )


def open_spi(device: str, speed_hz: int) -> SpiDevice:
    bus, chip_select = parse_spi_device(device)
    try:
        import spidev  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("Python spidev is unavailable") from error
    spi = spidev.SpiDev()
    spi.open(bus, chip_select)
    spi.mode = 0
    spi.bits_per_word = 8
    spi.max_speed_hz = speed_hz
    return spi


def sample_touch(args: argparse.Namespace) -> int:
    require_hardware_confirmation(args)
    if args.seconds <= 0:
        raise SystemExit("ERROR: --seconds must be greater than zero")
    if args.interval <= 0:
        raise SystemExit("ERROR: --interval must be greater than zero")
    if args.samples < 1:
        raise SystemExit("ERROR: --samples must be at least one")
    if not 10_000 <= args.speed_hz <= 2_000_000:
        raise SystemExit("ERROR: --speed-hz must be between 10000 and 2000000")
    if not 0 <= args.min_pressure <= ADC_MAX * 2:
        raise SystemExit(f"ERROR: --min-pressure must be between 0 and {ADC_MAX * 2}")

    report = preflight(args.device)
    active = [
        name
        for name, state in report["service_states"].items()
        if state == "active"
    ]
    if active:
        raise SystemExit(
            "ERROR: conflicting PCS services are active: " + ", ".join(active)
        )
    non_service_blockers = [
        blocker
        for blocker in report["blockers"]
        if not blocker.startswith("conflicting PCS services")
    ]
    if non_service_blockers:
        raise SystemExit("ERROR: " + "; ".join(non_service_blockers))

    print(
        f"Sampling {args.device} at {args.speed_hz} Hz for {args.seconds:g} seconds."
    )
    print("Touch the center and all four corners. Ctrl-C ends early.")
    print("elapsed_s raw_x raw_y raw_z1 raw_z2 pressure state")

    spi: SpiDevice | None = None
    touched_samples: list[TouchSample] = []
    started = time.monotonic()
    last_idle_report = -1.0
    try:
        spi = open_spi(args.device, args.speed_hz)
        controller = Xpt2046(spi)
        while (elapsed := time.monotonic() - started) < args.seconds:
            sample = controller.read(args.samples)
            if is_touch(sample, args.min_pressure):
                touched_samples.append(sample)
                print(
                    f"{elapsed:9.3f} {sample.x:5d} {sample.y:5d} "
                    f"{sample.z1:6d} {sample.z2:6d} {sample.pressure:8d} TOUCH"
                )
            elif elapsed - last_idle_report >= 1.0:
                print(
                    f"{elapsed:9.3f} {sample.x:5d} {sample.y:5d} "
                    f"{sample.z1:6d} {sample.z2:6d} {sample.pressure:8d} idle"
                )
                last_idle_report = elapsed
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Sampling stopped by operator.")
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"ERROR: XPT2046 sampling failed: {error}") from error
    finally:
        if spi is not None:
            spi.close()

    print(f"Touch samples: {len(touched_samples)}")
    if touched_samples:
        print(
            "Observed raw bounds: "
            f"X={min(item.x for item in touched_samples)}.."
            f"{max(item.x for item in touched_samples)}, "
            f"Y={min(item.y for item in touched_samples)}.."
            f"{max(item.y for item in touched_samples)}"
        )
        print("Result: raw XPT2046 touch activity observed; calibration is not implied.")
        return 0
    print("Result: no touch activity crossed the configured pressure threshold.")
    return 1


def rgb565(red: int, green: int, blue: int) -> int:
    """Pack 8-bit RGB channels into a 16-bit RGB565 pixel."""

    for name, value in (("red", red), ("green", green), ("blue", blue)):
        if not 0 <= value <= 255:
            raise ValueError(f"{name} must be between 0 and 255")
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def test_pattern(width: int, height: int) -> array.array[int]:
    """Return an RGB565 color-bar/grid pattern for a framebuffer test."""

    if width < 8 or height < 8:
        raise ValueError("framebuffer must be at least 8x8 pixels")
    colors = (
        rgb565(255, 255, 255),
        rgb565(255, 255, 0),
        rgb565(0, 255, 255),
        rgb565(0, 255, 0),
        rgb565(255, 0, 255),
        rgb565(255, 0, 0),
        rgb565(0, 0, 255),
        rgb565(0, 0, 0),
    )
    pixels = array.array("H")
    border = max(2, min(width, height) // 64)
    grid = max(16, min(width, height) // 5)
    for y in range(height):
        for x in range(width):
            bar = min(len(colors) - 1, x * len(colors) // width)
            pixel = colors[bar]
            if x < border or y < border or x >= width - border or y >= height - border:
                pixel = rgb565(255, 255, 255)
            elif x % grid < border or y % grid < border:
                pixel = rgb565(96, 96, 96)
            if abs(x - width // 2) < border or abs(y - height // 2) < border:
                pixel = rgb565(255, 255, 255)
            pixels.append(pixel)
    if sys.byteorder != "little":
        pixels.byteswap()
    return pixels


def mpi3501_screen_point(
    raw_x: int, raw_y: int, width: int, height: int
) -> tuple[int, int]:
    """Map raw MPI3501 coordinates to the vendor's 90-degree screen profile."""

    if width < 1 or height < 1:
        raise ValueError("display dimensions must be positive")
    x = round((raw_y - MPI3501_Y_MIN) * (width - 1) / (MPI3501_Y_MAX - MPI3501_Y_MIN))
    y = round((MPI3501_X_MAX - raw_x) * (height - 1) / (MPI3501_X_MAX - MPI3501_X_MIN))
    return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def touch_target_pattern(width: int, height: int) -> array.array[int]:
    """Return a dark RGB565 target pattern for five-point touch testing."""

    if width < 64 or height < 64:
        raise ValueError("touch-test framebuffer must be at least 64x64 pixels")
    black = rgb565(0, 0, 0)
    white = rgb565(255, 255, 255)
    yellow = rgb565(255, 255, 0)
    pixels = array.array("H", [black]) * (width * height)
    border = max(2, min(width, height) // 80)
    for y in range(height):
        for x in range(width):
            if x < border or y < border or x >= width - border or y >= height - border:
                pixels[y * width + x] = white
    margin_x = width // 7
    margin_y = height // 10
    targets = (
        (margin_x, margin_y),
        (width - 1 - margin_x, margin_y),
        (width // 2, height // 2),
        (margin_x, height - 1 - margin_y),
        (width - 1 - margin_x, height - 1 - margin_y),
    )
    radius = max(10, min(width, height) // 16)
    for center_x, center_y in targets:
        for offset in range(-radius, radius + 1):
            x = center_x + offset
            y = center_y + offset
            if 0 <= x < width:
                pixels[center_y * width + x] = yellow
            if 0 <= y < height:
                pixels[y * width + center_x] = yellow
        for delta in range(-border, border + 1):
            for x, y in (
                (center_x - radius, center_y + delta),
                (center_x + radius, center_y + delta),
                (center_x + delta, center_y - radius),
                (center_x + delta, center_y + radius),
            ):
                if 0 <= x < width and 0 <= y < height:
                    pixels[y * width + x] = white
    if sys.byteorder != "little":
        pixels.byteswap()
    return pixels


def framebuffer_geometry(framebuffer: str) -> tuple[int, int, int, int]:
    device = Path(framebuffer)
    if not re.fullmatch(r"fb\d+", device.name):
        raise ValueError("framebuffer must look like /dev/fb0")
    sysfs = Path("/sys/class/graphics") / device.name
    width_text, height_text = (sysfs / "virtual_size").read_text().strip().split(",")
    width, height = int(width_text), int(height_text)
    bits_per_pixel = int((sysfs / "bits_per_pixel").read_text().strip())
    stride = int((sysfs / "stride").read_text().strip())
    return width, height, bits_per_pixel, stride


def write_framebuffer_pattern(args: argparse.Namespace) -> int:
    require_hardware_confirmation(args)
    try:
        width, height, bits_per_pixel, stride = framebuffer_geometry(args.framebuffer)
    except (OSError, ValueError) as error:
        raise SystemExit(f"ERROR: cannot inspect {args.framebuffer}: {error}") from error
    if bits_per_pixel != 16:
        raise SystemExit(
            f"ERROR: {args.framebuffer} uses {bits_per_pixel} bpp; RGB565 requires 16"
        )
    if stride != width * 2:
        raise SystemExit(
            f"ERROR: unsupported framebuffer stride {stride}; expected {width * 2}"
        )
    pixels = test_pattern(width, height)
    try:
        with Path(args.framebuffer).open("r+b", buffering=0) as output:
            output.write(pixels.tobytes())
    except OSError as error:
        raise SystemExit(f"ERROR: cannot write {args.framebuffer}: {error}") from error
    print(f"Wrote RGB565 color bars and grid to {args.framebuffer} ({width}x{height}).")
    return 0


def draw_touch_dot(
    framebuffer: mmap.mmap,
    width: int,
    height: int,
    stride: int,
    x: int,
    y: int,
    color: int,
    radius: int = 6,
) -> None:
    for pixel_y in range(max(0, y - radius), min(height, y + radius + 1)):
        for pixel_x in range(max(0, x - radius), min(width, x + radius + 1)):
            if (pixel_x - x) ** 2 + (pixel_y - y) ** 2 <= radius**2:
                struct.pack_into("<H", framebuffer, pixel_y * stride + pixel_x * 2, color)


def visualize_touch(args: argparse.Namespace) -> int:
    require_hardware_confirmation(args)
    if args.seconds <= 0 or args.seconds > 1800:
        raise SystemExit("ERROR: --seconds must be greater than zero and at most 1800")
    input_path = Path(args.input_device)
    if not re.fullmatch(r"event\d+", input_path.name):
        raise SystemExit("ERROR: input device must look like /dev/input/event0")
    try:
        width, height, bits_per_pixel, stride = framebuffer_geometry(args.framebuffer)
    except (OSError, ValueError) as error:
        raise SystemExit(f"ERROR: cannot inspect {args.framebuffer}: {error}") from error
    if bits_per_pixel != 16 or stride != width * 2:
        raise SystemExit("ERROR: touch visualization requires a packed 16-bit framebuffer")

    baseline = touch_target_pattern(width, height)
    event_format = struct.Struct("@llHHi")
    raw_x = raw_y = pressure = 0
    button_down = False
    reports: list[tuple[int, int, int]] = []
    last_drawn: tuple[int, int] | None = None
    deadline = time.monotonic() + args.seconds
    print(f"Five-point touch test on {input_path} for {args.seconds:g} seconds.")
    print("Press the four yellow corner targets and the center target. Ctrl-C ends early.")
    print("raw_x raw_y pressure screen_x screen_y")
    try:
        with Path(args.framebuffer).open("r+b", buffering=0) as fb_output:
            fb_output.write(baseline.tobytes())
            fb_output.flush()
            with mmap.mmap(fb_output.fileno(), stride * height) as mapped:
                with input_path.open("rb", buffering=0) as input_stream:
                    while (remaining := deadline - time.monotonic()) > 0:
                        readable, _, _ = select.select([input_stream], [], [], min(0.25, remaining))
                        if not readable:
                            continue
                        data = os.read(input_stream.fileno(), event_format.size * 64)
                        for offset in range(0, len(data) - event_format.size + 1, event_format.size):
                            _, _, event_type, code, value = event_format.unpack_from(data, offset)
                            if event_type == EV_ABS:
                                if code == ABS_X:
                                    raw_x = value
                                elif code == ABS_Y:
                                    raw_y = value
                                elif code == ABS_PRESSURE:
                                    pressure = value
                            elif event_type == EV_KEY and code == BTN_TOUCH:
                                button_down = bool(value)
                            elif event_type == EV_SYN and code == SYN_REPORT:
                                if button_down or pressure > 0:
                                    point = mpi3501_screen_point(raw_x, raw_y, width, height)
                                    if point != last_drawn:
                                        draw_touch_dot(
                                            mapped,
                                            width,
                                            height,
                                            stride,
                                            point[0],
                                            point[1],
                                            rgb565(255, 0, 0),
                                        )
                                        mapped.flush()
                                        reports.append((raw_x, raw_y, pressure))
                                        last_drawn = point
                                        print(
                                            f"{raw_x:5d} {raw_y:5d} {pressure:8d} "
                                            f"{point[0]:8d} {point[1]:8d}"
                                        )
    except KeyboardInterrupt:
        print("Touch test stopped by operator.")
    except OSError as error:
        raise SystemExit(f"ERROR: touch visualization failed: {error}") from error

    print(f"Touch reports: {len(reports)}")
    if reports:
        print(
            "Observed raw bounds: "
            f"X={min(item[0] for item in reports)}..{max(item[0] for item in reports)}, "
            f"Y={min(item[1] for item in reports)}..{max(item[1] for item in reports)}"
        )
        print("Result: ADS7846/XPT2046 touch contacts observed; red dots mark reported positions.")
        return 0
    print("Result: no ADS7846/XPT2046 touch contacts were reported.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or deliberately sample an XPT2046 on SPI0 CE1."
    )
    subparsers = parser.add_subparsers(dest="command")

    preflight_parser = subparsers.add_parser(
        "preflight", help="inspect readiness without opening SPI"
    )
    preflight_parser.add_argument("--device", default=DEFAULT_SPI_DEVICE)
    preflight_parser.add_argument("--json", action="store_true")

    sample_parser = subparsers.add_parser(
        "sample", help="read raw X/Y/Z touch channels from the controller"
    )
    sample_parser.add_argument("--device", default=DEFAULT_SPI_DEVICE)
    sample_parser.add_argument("--seconds", type=float, default=20.0)
    sample_parser.add_argument("--interval", type=float, default=0.05)
    sample_parser.add_argument("--samples", type=int, default=5)
    sample_parser.add_argument("--speed-hz", type=int, default=DEFAULT_SPI_HZ)
    sample_parser.add_argument("--min-pressure", type=int, default=200)
    sample_parser.add_argument("--hardware", action="store_true")
    sample_parser.add_argument("--apply", action="store_true")
    sample_parser.add_argument(
        "--confirm-pcs-displays-disconnected",
        action="store_true",
        help="confirm the installed PCS LCD/matrix are physically disconnected",
    )

    pattern_parser = subparsers.add_parser(
        "pattern", help="write an RGB565 color-bar/grid pattern to a framebuffer"
    )
    pattern_parser.add_argument("--framebuffer", default=DEFAULT_FRAMEBUFFER)
    pattern_parser.add_argument("--hardware", action="store_true")
    pattern_parser.add_argument("--apply", action="store_true")
    pattern_parser.add_argument(
        "--confirm-pcs-displays-disconnected",
        action="store_true",
        help="confirm the installed PCS LCD/matrix are physically disconnected",
    )

    touch_map_parser = subparsers.add_parser(
        "touch-map", help="draw model-specific touch reports on a framebuffer"
    )
    touch_map_parser.add_argument("--framebuffer", required=True)
    touch_map_parser.add_argument("--input-device", required=True)
    touch_map_parser.add_argument("--seconds", type=float, default=60.0)
    touch_map_parser.add_argument("--hardware", action="store_true")
    touch_map_parser.add_argument("--apply", action="store_true")
    touch_map_parser.add_argument(
        "--confirm-pcs-displays-disconnected",
        action="store_true",
        help="confirm the installed PCS LCD/matrix are physically disconnected",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "preflight"):
        report = preflight(getattr(args, "device", DEFAULT_SPI_DEVICE))
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2))
        else:
            print_preflight(report)
        return 0 if report["ready_for_guarded_sampling"] else 1
    if args.command == "sample":
        return sample_touch(args)
    if args.command == "pattern":
        return write_framebuffer_pattern(args)
    if args.command == "touch-map":
        return visualize_touch(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
