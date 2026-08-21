#!/usr/bin/env python3
"""PCS GPIO allocation and guarded device commissioning utility.

The default demo backend is a simulator. Real GPIO writes require both
``--hardware`` and ``--apply`` so importing or inspecting this module cannot
key the APRS radio or unexpectedly animate attached devices.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence


@dataclass(frozen=True)
class PinAssignment:
    function: str
    gpio: int
    physical: int
    owner: str
    status: str


PIN_ASSIGNMENTS: tuple[PinAssignment, ...] = (
    PinAssignment("RTC SDA", 2, 3, "Linux I2C", "existing; kernel-managed"),
    PinAssignment("RTC SCL", 3, 5, "Linux I2C", "existing; kernel-managed"),
    PinAssignment("LCD RS", 4, 7, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("APRS PTT", 6, 31, "Dire Wolf", "selected; activation-gated"),
    PinAssignment("MAX7219 CS", 8, 24, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("MAX7219 DIN", 10, 19, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("MAX7219 CLK", 11, 23, "Linux SPI0", "installed and bench-tested"),
    PinAssignment("SA818 UART TX", 14, 8, "Linux UART", "reserved; logic level unverified"),
    PinAssignment("SA818 UART RX", 15, 10, "Linux UART", "reserved; logic level unverified"),
    PinAssignment("LCD E", 17, 11, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("Fan PWM", 18, 12, "pcs_gpio", "hardware PWM control; RPM unmeasured"),
    PinAssignment("WS2812 data", 21, 40, "pcs_gpio", "installed and live-tested"),
    PinAssignment("LCD D4", 27, 13, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D5", 22, 15, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D6", 23, 16, "pcs_gpio", "installed and bench-tested"),
    PinAssignment("LCD D7", 24, 18, "pcs_gpio", "installed and bench-tested"),
)

LCD_PINS = {"rs": 4, "enable": 17, "d4": 27, "d5": 22, "d6": 23, "d7": 24}
LCD_COLUMNS = 16
LCD_ROWS = 2
HD44780_CHARACTER_CODES = {"°": 0xDF}
WS2812_PIN = 21
WS2812_COUNT = 6
WS2812_FREQUENCY_HZ = 800_000
WS2812_DMA_CHANNEL = 10
WS2812_BRIGHTNESS = 32
WS2812_POLL_SECONDS = 3.0
WS2812_PYTHON_PATH = Path("/opt/pcs-gpio-leds/bin/python")
# Full RGB values are deliberately scaled by the strip-wide 32/255 brightness.
LED_OFF = (0, 0, 0)
LED_HEALTHY = (0, 255, 0)
LED_WARNING = (220, 48, 0)
LED_CRITICAL = (176, 0, 0)
LED_UNKNOWN = (32, 32, 96)
FAN_PWM_PIN = 18
FAN_PWM_CHANNEL = 0
FAN_PWM_FREQUENCY_HZ = 100
FAN_PWM_PERIOD_NS = 1_000_000_000 // FAN_PWM_FREQUENCY_HZ
FAN_PWM_CHIP_PATH = Path("/sys/class/pwm/pwmchip0")
FAN_STATUS_PATH = Path("/run/pcs-gpio-fan/status.json")
FAN_FAILSAFE_DUTY = 100
FAN_HYSTERESIS_C = 3
FAN_POLL_SECONDS = 5.0
FAN_CURVE: tuple[tuple[int, int], ...] = (
    (0, 40),
    (45, 55),
    (55, 70),
    (65, 85),
    (75, 100),
)
MAX7219_SPI_BUS = 0
MAX7219_SPI_DEVICE = 0
MAX7219_SPI_HZ = 500_000
MAX7219_INTENSITY = 3
INSTALL_CONFIG_PATH = Path(
    os.environ.get(
        "PCS_INSTALL_CONFIG",
        "/home/pi/Projects/PCS-Portable-Comm-Server/config/pcs-install.conf",
    )
)
PISTAR_HOST = os.environ.get("PCS_PISTAR_HOST", "10.42.0.3")
OPENWRT_HOST = os.environ.get("PCS_OPENWRT_HOST", "10.42.0.2")
PISTAR_PAIR_DIR = Path(
    os.environ.get("PCS_PISTAR_PAIR_DIR", "/etc/pcs/pistar-shutdown")
)


class Backend(Protocol):
    def lcd(self, lines: Sequence[str]) -> None: ...
    def matrix(self, rows: Sequence[int]) -> None: ...
    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None: ...
    def fan(self, duty_percent: float) -> None: ...
    def close(self) -> None: ...


class FanController(Protocol):
    def duty(self, duty_percent: float) -> None: ...
    def close(self) -> None: ...


class LedDisplay(Protocol):
    def colors(self, colors: Sequence[tuple[int, int, int]]) -> None: ...
    def close(self) -> None: ...


class MockBackend:
    """Records intended writes without accessing GPIO hardware."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def lcd(self, lines: Sequence[str]) -> None:
        self.events.append({"device": "lcd", "lines": list(lines)})

    def matrix(self, rows: Sequence[int]) -> None:
        self.events.append({"device": "matrix", "rows": list(rows)})

    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None:
        self.events.append({"device": "leds", "colors": [list(color) for color in colors]})

    def fan(self, duty_percent: float) -> None:
        self.events.append({"device": "fan", "duty_percent": duty_percent})

    def close(self) -> None:
        self.events.append({"device": "controller", "action": "close"})


class HD44780:
    """Minimal write-only HD44780 4-bit driver using GPIO Zero outputs."""

    def __init__(self) -> None:
        from gpiozero import DigitalOutputDevice

        self.rs = DigitalOutputDevice(LCD_PINS["rs"], initial_value=False)
        self.enable = DigitalOutputDevice(LCD_PINS["enable"], initial_value=False)
        self.data = [
            DigitalOutputDevice(LCD_PINS[name], initial_value=False)
            for name in ("d4", "d5", "d6", "d7")
        ]
        time.sleep(0.05)
        for nibble, pause in ((0x3, 0.005), (0x3, 0.001), (0x3, 0.001), (0x2, 0.001)):
            self._write_nibble(nibble)
            time.sleep(pause)
        for command in (0x28, 0x08, 0x01, 0x06, 0x0C):
            self.command(command)

    def _pulse(self) -> None:
        self.enable.on()
        time.sleep(0.000001)
        self.enable.off()
        time.sleep(0.00005)

    def _write_nibble(self, value: int) -> None:
        for bit, output in enumerate(self.data):
            output.value = bool(value & (1 << bit))
        self._pulse()

    def _send(self, value: int, *, data: bool) -> None:
        self.rs.value = data
        self._write_nibble((value >> 4) & 0x0F)
        self._write_nibble(value & 0x0F)
        if not data and value in (0x01, 0x02):
            time.sleep(0.002)

    def command(self, value: int) -> None:
        self._send(value, data=False)

    def text(self, lines: Sequence[str]) -> None:
        padded = normalize_lcd_lines(lines)
        for address, line in zip((0x00, 0x40), padded):
            self.command(0x80 | address)
            for character in line:
                code = HD44780_CHARACTER_CODES.get(character, ord(character))
                self._send(code if 0 <= code <= 0xFF else ord("?"), data=True)

    def close(self, *, clear: bool = True) -> None:
        if clear:
            self.command(0x01)
        for output in (*self.data, self.enable, self.rs):
            output.off()
        for output in (*self.data, self.enable, self.rs):
            output.close()


class Max7219:
    def __init__(self) -> None:
        import spidev

        self.spi = spidev.SpiDev()
        self.spi.open(MAX7219_SPI_BUS, MAX7219_SPI_DEVICE)
        self.spi.max_speed_hz = MAX7219_SPI_HZ
        self.spi.mode = 0
        for register, value in (
            (0x0F, 0),
            (0x09, 0),
            (0x0B, 7),
            (0x0A, MAX7219_INTENSITY),
            (0x0C, 1),
        ):
            self._write(register, value)
        self.rows([0] * 8)

    def _write(self, register: int, value: int) -> None:
        self.spi.xfer2([register & 0x0F, value & 0xFF])

    def rows(self, rows: Sequence[int]) -> None:
        if len(rows) != 8 or any(not 0 <= row <= 0xFF for row in rows):
            raise ValueError("MAX7219 rows must contain exactly eight byte values")
        for register, value in enumerate(rows, start=1):
            self._write(register, value)

    def intensity(self, value: int) -> None:
        if not 0 <= value <= 15:
            raise ValueError("MAX7219 intensity must be from 0 to 15")
        self._write(0x0A, value)

    def close(self) -> None:
        self.rows([0] * 8)
        self._write(0x0C, 0)
        self.spi.close()


class Ws2812:
    def __init__(self) -> None:
        from rpi_ws281x import Color, PixelStrip, ws

        self._color = Color
        # GPIO21 selects the PCM output path, leaving GPIO18 available for the
        # cooler's independent PWM signal. Brightness is intentionally low.
        self.strip = PixelStrip(
            WS2812_COUNT,
            WS2812_PIN,
            WS2812_FREQUENCY_HZ,
            WS2812_DMA_CHANNEL,
            False,
            WS2812_BRIGHTNESS,
            0,
            ws.WS2811_STRIP_GRB,
        )
        self.strip.begin()

    def colors(self, colors: Sequence[tuple[int, int, int]]) -> None:
        if len(colors) != WS2812_COUNT:
            raise ValueError(f"expected {WS2812_COUNT} WS2812 colors")
        for index, (red, green, blue) in enumerate(colors):
            if any(not 0 <= component <= 255 for component in (red, green, blue)):
                raise ValueError("WS2812 color components must be from 0 to 255")
            self.strip.setPixelColor(index, self._color(red, green, blue))
        self.strip.show()

    def close(self) -> None:
        self.colors([LED_OFF] * WS2812_COUNT)


class HardwarePwmFan:
    """GPIO18 PWM0 controller using the kernel hardware-PWM sysfs interface."""

    def __init__(
        self,
        chip_path: Path = FAN_PWM_CHIP_PATH,
        channel: int = FAN_PWM_CHANNEL,
        period_ns: int = FAN_PWM_PERIOD_NS,
    ) -> None:
        self.chip_path = chip_path
        self.channel = channel
        self.period_ns = period_ns
        self.channel_path = chip_path / f"pwm{channel}"
        if not chip_path.is_dir():
            raise RuntimeError(
                f"hardware PWM is unavailable at {chip_path}; enable the GPIO18 pwm overlay and reboot"
            )
        if not self.channel_path.is_dir():
            try:
                (chip_path / "export").write_text(f"{channel}\n", encoding="ascii")
            except OSError as error:
                if not self.channel_path.is_dir():
                    raise RuntimeError(f"cannot export PWM channel {channel}: {error}") from error
            deadline = time.monotonic() + 2.0
            while not self.channel_path.is_dir() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not self.channel_path.is_dir():
                raise RuntimeError(f"PWM channel {channel} did not appear after export")

        enabled_path = self.channel_path / "enable"
        if enabled_path.read_text(encoding="ascii").strip() == "1":
            enabled_path.write_text("0\n", encoding="ascii")
        (self.channel_path / "period").write_text(f"{period_ns}\n", encoding="ascii")
        (self.channel_path / "duty_cycle").write_text(f"{period_ns}\n", encoding="ascii")
        enabled_path.write_text("1\n", encoding="ascii")
        self.current_duty = FAN_FAILSAFE_DUTY

    def duty(self, duty_percent: float) -> None:
        if not 0 <= duty_percent <= 100:
            raise ValueError("fan duty must be between 0 and 100 percent")
        duty_ns = round(self.period_ns * float(duty_percent) / 100.0)
        (self.channel_path / "duty_cycle").write_text(f"{duty_ns}\n", encoding="ascii")
        self.current_duty = float(duty_percent)

    def close(self) -> None:
        # Leave PWM enabled at full duty so stopping or restarting the daemon
        # fails toward maximum cooling instead of silently stopping the fan.
        self.duty(FAN_FAILSAFE_DUTY)


class RaspberryPiBackend:
    """Creates only the device drivers selected by a guarded demo."""

    def __init__(self) -> None:
        self._devices: list[object] = []

    def lcd(self, lines: Sequence[str]) -> None:
        device = HD44780()
        self._devices.append(device)
        device.text(lines)

    def matrix(self, rows: Sequence[int]) -> None:
        device = Max7219()
        self._devices.append(device)
        device.rows(rows)

    def leds(self, colors: Sequence[tuple[int, int, int]]) -> None:
        device = Ws2812()
        self._devices.append(device)
        device.colors(colors)

    def fan(self, duty_percent: float) -> None:
        device = HardwarePwmFan()
        self._devices.append(device)
        device.duty(duty_percent)

    def close(self) -> None:
        for device in reversed(self._devices):
            try:
                device.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._devices.clear()


CHECKERBOARD = (0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55)
LED_TEST_COLORS = ((32, 0, 0), (0, 32, 0), (0, 0, 32), (32, 16, 0), (0, 16, 32), (16, 0, 32))
SAFE_ALL_TARGETS = ("lcd", "matrix", "leds")

DIGITS: dict[str, tuple[int, ...]] = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b010, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
}

DEGREE_C_ICON = (0xE7, 0xA8, 0xE8, 0x08, 0x08, 0x08, 0x07, 0x00)
SIGNAL_ICON = (0x00, 0x00, 0x02, 0x02, 0x0A, 0x0A, 0x2A, 0xAA)
SATELLITE_DISH_ICON = (0x01, 0x05, 0x12, 0x0A, 0x3C, 0x18, 0x18, 0x3C)
X_ICON = (0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81)
NO_FIX_ICON = X_ICON
UNKNOWN_ICON = (0x3C, 0x42, 0x02, 0x0C, 0x10, 0x00, 0x10, 0x00)
CHECK_ICON = (0x00, 0x01, 0x03, 0x06, 0xCC, 0x78, 0x30, 0x00)
EXCLAMATION_ICON = (0x18, 0x18, 0x18, 0x18, 0x18, 0x00, 0x18, 0x18)
STORAGE_ICON = (0x7E, 0x42, 0x5A, 0x42, 0x42, 0x5A, 0x42, 0x7E)
SERVICE_ICON = (0x24, 0x7E, 0xDB, 0xBD, 0xBD, 0xDB, 0x7E, 0x24)
PISTAR_ICON = (0x66, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x18)
ROUTER_ICON = (0x7E, 0x81, 0x81, 0x3C, 0x42, 0x42, 0x18, 0x18)
TEMPERATURE_WARNING_C = 75
TEMPERATURE_CRITICAL_C = 85
DISK_WARNING_PERCENT = 85
DISK_CRITICAL_PERCENT = 95


@dataclass(frozen=True)
class StatsSnapshot:
    temperature_c: int | None
    cellular_quality: int | None
    gps_satellites: int | None
    gps_locked: bool | None
    cellular_online: bool | None = None
    gps_satellites_used: int | None = None
    network_uplink: str | None = None
    ap_clients: int | None = None
    grid_square: str | None = None

    def as_dict(self) -> dict[str, int | bool | str | None]:
        return asdict(self)


class MatrixDisplay(Protocol):
    def rows(self, rows: Sequence[int]) -> None: ...
    def intensity(self, value: int) -> None: ...
    def close(self) -> None: ...


class LcdDisplay(Protocol):
    def text(self, lines: Sequence[str]) -> None: ...

    def close(self, *, clear: bool = True) -> None: ...


@dataclass(frozen=True)
class StatsFrame:
    metric: str
    kind: str
    rows: tuple[int, ...]
    intensity: int | None = None

    def as_dict(self) -> dict[str, str | int | list[int] | None]:
        return {
            "metric": self.metric,
            "kind": self.kind,
            "rows": list(self.rows),
            "intensity": self.intensity,
        }


@dataclass(frozen=True)
class MatrixHealthSnapshot:
    stats: StatsSnapshot
    root_used_percent: int | None
    primary_usb_mounted: bool | None
    failed_services: int | None
    pistar_online: bool | None = None
    router_online: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stats": self.stats.as_dict(),
            "root_used_percent": self.root_used_percent,
            "primary_usb_mounted": self.primary_usb_mounted,
            "failed_services": self.failed_services,
            "pistar_online": self.pistar_online,
            "router_online": self.router_online,
        }


@dataclass(frozen=True)
class MatrixAlert:
    name: str
    severity: str
    icon: tuple[int, ...]

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "severity": self.severity}


@dataclass(frozen=True)
class LedIndicator:
    pixel: int
    name: str
    state: str
    color: tuple[int, int, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "pixel": self.pixel,
            "name": self.name,
            "state": self.state,
            "color": list(self.color),
        }


def selected_targets(target: str) -> tuple[str, ...]:
    return SAFE_ALL_TARGETS if target == "all" else (target,)


def normalize_lcd_lines(lines: Sequence[str]) -> tuple[str, str]:
    """Return exactly two centered, printable 16-character HD44780 rows."""
    normalized = [str(line).replace("\n", " ").replace("\r", " ") for line in lines[:LCD_ROWS]]
    normalized.extend("" for _ in range(LCD_ROWS - len(normalized)))
    return tuple(line[:LCD_COLUMNS].center(LCD_COLUMNS) for line in normalized)  # type: ignore[return-value]


def run_demo(
    backend: Backend,
    target: str,
    *,
    duration: float = 3.0,
    fan_duty: float | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    targets = selected_targets(target)
    if "fan" in targets and fan_duty is None:
        raise ValueError("the fan demo requires an explicit --fan-duty value")
    if "fan" in targets and not 0 <= float(fan_duty) <= 100:
        raise ValueError("fan duty must be between 0 and 100 percent")
    try:
        if "lcd" in targets:
            backend.lcd(("PCS GPIO TEST", "LCD: OK"))
        if "matrix" in targets:
            backend.matrix(CHECKERBOARD)
        if "leds" in targets:
            backend.leds(LED_TEST_COLORS)
        if "fan" in targets:
            backend.fan(float(fan_duty))
        pause(max(0.0, duration))
    finally:
        backend.close()


def render_two_digits(value: int | None) -> tuple[int, ...]:
    """Render a zero-padded 0-99 value in two centered 3x5 glyphs."""
    if value is None:
        rows = [0] * 8
        rows[3] = 0b11101110
        return tuple(rows)
    text = f"{max(0, min(99, int(value))):02d}"
    rows = [0] * 8
    for row_index in range(5):
        rows[row_index + 1] = (DIGITS[text[0]][row_index] << 5) | (DIGITS[text[1]][row_index] << 1)
    return tuple(rows)


def read_temperature(path: Path = Path("/sys/class/thermal/thermal_zone0/temp")) -> int | None:
    try:
        millidegrees = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return max(0, min(99, round(millidegrees / 1000)))


def fan_duty_for_temperature(
    temperature_c: int | None,
    current_duty: int | None = None,
) -> int:
    if temperature_c is None:
        return FAN_FAILSAFE_DUTY

    target_index = 0
    for index, (threshold_c, _duty_percent) in enumerate(FAN_CURVE):
        if temperature_c >= threshold_c:
            target_index = index

    if current_duty is None:
        return FAN_CURVE[target_index][1]

    current_index = min(
        range(len(FAN_CURVE)),
        key=lambda index: abs(FAN_CURVE[index][1] - current_duty),
    )
    if target_index >= current_index:
        return FAN_CURVE[target_index][1]

    while current_index > target_index:
        threshold_c = FAN_CURVE[current_index][0]
        if temperature_c > threshold_c - FAN_HYSTERESIS_C:
            break
        current_index -= 1
    return FAN_CURVE[current_index][1]


def write_fan_status(
    status: dict[str, object],
    path: Path = FAN_STATUS_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_fan_control(
    fan: FanController,
    *,
    once: bool = False,
    poll_seconds: float = FAN_POLL_SECONDS,
    temperature_reader: Callable[[], int | None] = read_temperature,
    status_writer: Callable[[dict[str, object]], None] = write_fan_status,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    fan.duty(FAN_FAILSAFE_DUTY)
    current_duty: int | None = None
    while not should_stop():
        temperature_c = temperature_reader()
        target_duty = fan_duty_for_temperature(temperature_c, current_duty)
        changed = target_duty != current_duty
        if changed:
            fan.duty(target_duty)
        status = {
            "temperature_c": temperature_c,
            "duty_percent": target_duty,
            "frequency_hz": FAN_PWM_FREQUENCY_HZ,
            "gpio": FAN_PWM_PIN,
            "mode": "failsafe" if temperature_c is None else "automatic",
            "timestamp": int(time.time()),
        }
        status_writer(status)
        if changed or temperature_c is None:
            print(json.dumps(status, separators=(",", ":")), flush=True)
        current_duty = target_duty
        if once:
            break
        sleeper(poll_seconds)


def parse_cellular_quality(output: str) -> int | None:
    match = re.search(r"^modem\.generic\.signal-quality\.value\s*:\s*(\d+)", output, re.MULTILINE)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def parse_cellular_state(output: str) -> bool | None:
    match = re.search(r"^modem\.generic\.state\s*:\s*(\S+)", output, re.MULTILINE)
    if not match:
        return None
    state = match.group(1).lower()
    if state in {"failed", "disabled", "disabling", "locked"}:
        return False
    if state in {
        "initializing",
        "enabling",
        "enabled",
        "searching",
        "registered",
        "disconnecting",
        "connecting",
        "connected",
    }:
        return True
    return None


def parse_cellular_data_state(output: str) -> bool | None:
    """Return NetworkManager's cellular data-session state."""
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or parts[1] != "gsm":
            continue
        return parts[2] == "connected"
    return None


def read_cellular_data_state() -> bool | None:
    try:
        result = subprocess.run(
            ["nmcli", "-t", "--escape", "no", "-f", "DEVICE,TYPE,STATE", "device", "status"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_cellular_data_state(result.stdout) if result.returncode == 0 else None


def read_cellular_status() -> tuple[bool | None, int | None]:
    try:
        result = subprocess.run(
            ["mmcli", "-m", "any", "-K"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    quality = parse_cellular_quality(result.stdout)
    return read_cellular_data_state(), quality


def read_cellular_quality() -> int | None:
    return read_cellular_status()[1]


def parse_network_uplink(output: str) -> str:
    match = re.search(r"\bdev\s+(\S+)", output)
    if not match:
        return "Offline"
    interface = match.group(1)
    if interface == "wlan0":
        return "WiFi"
    if interface in {"wwan0", "ppp0"} or interface.startswith(("wwan", "ppp")):
        return "Cellular"
    return "Offline"


def read_network_uplink() -> str:
    try:
        result = subprocess.run(
            ["ip", "route", "get", "8.8.8.8"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "Offline"
    return parse_network_uplink(result.stdout if result.returncode == 0 else "")


def parse_ap_client_count(output: str) -> int:
    """Count active PCS client neighbors while excluding fixed infrastructure."""
    clients: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("10.42.0."):
            continue
        if any(state in parts for state in ("FAILED", "INCOMPLETE")):
            continue
        try:
            host = int(parts[0].rsplit(".", 1)[1])
        except (ValueError, IndexError):
            continue
        if host not in {1, 2, 3}:
            clients.add(parts[0])
    return len(clients)


def read_ap_client_count() -> int | None:
    try:
        result = subprocess.run(
            ["ip", "neigh", "show", "dev", "eth0"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ap_client_count(result.stdout) if result.returncode == 0 else None


def maidenhead_grid(latitude: float, longitude: float) -> str | None:
    """Return the same six-character Maidenhead locator used by PCS web status."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    lon = longitude + 180
    lat = latitude + 90
    letters = "ABCDEFGHIJKLMNOPQRSTUVWX"
    field_lon = int(lon // 20)
    field_lat = int(lat // 10)
    lon -= field_lon * 20
    lat -= field_lat * 10
    square_lon = int(lon // 2)
    square_lat = int(lat // 1)
    lon -= square_lon * 2
    lat -= square_lat
    subsquare_lon = int(lon / (2 / 24))
    subsquare_lat = int(lat / (1 / 24))
    return (
        letters[field_lon]
        + letters[field_lat]
        + str(square_lon)
        + str(square_lat)
        + letters[subsquare_lon].lower()
        + letters[subsquare_lat].lower()
    )


def satellite_count_from_sky(record: dict[str, object]) -> int | None:
    """Return gpsd's satellites-in-view count without retaining satellite details."""
    count = record.get("nSat")
    if isinstance(count, int) and not isinstance(count, bool):
        return max(0, min(99, count))
    satellites = record.get("satellites")
    if isinstance(satellites, list):
        return min(99, len(satellites))
    return None


def satellite_counts_from_sky(record: dict[str, object]) -> tuple[int | None, int | None]:
    """Return gpsd satellites in view and used from one SKY report."""
    viewed = satellite_count_from_sky(record)
    used = record.get("uSat")
    if isinstance(used, int) and not isinstance(used, bool):
        return viewed, max(0, min(99, used))
    satellites = record.get("satellites")
    if isinstance(satellites, list):
        used_count = sum(
            1 for satellite in satellites
            if isinstance(satellite, dict) and satellite.get("used") is True
        )
        return viewed, min(99, used_count)
    return viewed, None


def merge_gps_details(
    satellites_view: int | None,
    satellites_used: int | None,
    locked: bool | None,
    record: dict[str, object],
) -> tuple[int | None, int | None, bool | None]:
    """Keep paired counts from the fullest SKY report and the best TPV fix."""
    if record.get("class") == "SKY":
        candidate_view, candidate_used = satellite_counts_from_sky(record)
        if candidate_view is not None and (
            satellites_view is None
            or candidate_view > satellites_view
            or (candidate_view == satellites_view and (candidate_used or 0) > (satellites_used or 0))
        ):
            satellites_view = candidate_view
            satellites_used = candidate_used
    elif record.get("class") == "TPV":
        mode = record.get("mode")
        if isinstance(mode, int) and not isinstance(mode, bool):
            candidate_lock = mode >= 2
            locked = candidate_lock if locked is None else locked or candidate_lock
    return satellites_view, satellites_used, locked


def merge_gps_status(
    satellites: int | None,
    locked: bool | None,
    record: dict[str, object],
) -> tuple[int | None, bool | None]:
    """Keep the fullest SKY report and best TPV fix without retaining coordinates."""
    satellites, _used, locked = merge_gps_details(satellites, None, locked, record)
    return satellites, locked


def read_gps_details(
    host: str = "127.0.0.1",
    port: int = 2947,
) -> tuple[int | None, int | None, bool | None, str | None]:
    """Read gpsd counts, fix and grid; coordinates are neither retained nor logged."""
    deadline = time.monotonic() + 2.0
    satellites_view: int | None = None
    satellites_used: int | None = None
    locked: bool | None = None
    grid_square: str | None = None
    try:
        with socket.create_connection((host, port), timeout=1.0) as connection:
            connection.settimeout(0.5)
            connection.sendall(b'?WATCH={"enable":true,"json":true};\n')
            buffer = b""
            while time.monotonic() < deadline:
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    try:
                        record = json.loads(raw_line.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeError):
                        continue
                    satellites_view, satellites_used, locked = merge_gps_details(
                        satellites_view,
                        satellites_used,
                        locked,
                        record,
                    )
                    if record.get("class") == "TPV":
                        mode = record.get("mode")
                        latitude = record.get("lat")
                        longitude = record.get("lon")
                        if (
                            isinstance(mode, int)
                            and not isinstance(mode, bool)
                            and mode >= 2
                            and isinstance(latitude, (int, float))
                            and not isinstance(latitude, bool)
                            and isinstance(longitude, (int, float))
                            and not isinstance(longitude, bool)
                        ):
                            grid_square = maidenhead_grid(float(latitude), float(longitude))
    except OSError:
        pass
    return satellites_view, satellites_used, locked, grid_square


def read_gps_status(host: str = "127.0.0.1", port: int = 2947) -> tuple[int | None, bool | None]:
    satellites_view, _satellites_used, locked, _grid_square = read_gps_details(host, port)
    return satellites_view, locked


def collect_stats() -> StatsSnapshot:
    cellular_online, cellular_quality = read_cellular_status()
    gps_satellites, gps_satellites_used, gps_locked, grid_square = read_gps_details()
    return StatsSnapshot(
        temperature_c=read_temperature(),
        cellular_quality=cellular_quality,
        gps_satellites=gps_satellites,
        gps_locked=gps_locked,
        cellular_online=cellular_online,
        gps_satellites_used=gps_satellites_used,
        network_uplink=read_network_uplink(),
        ap_clients=read_ap_client_count(),
        grid_square=grid_square,
    )


def read_uptime_seconds(path: Path = Path("/proc/uptime")) -> int | None:
    try:
        value = float(path.read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return max(0, int(value))


def format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "Up: --d --h --m"
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    return f"Up: {min(days, 999)}d {hours:02}h {minutes:02}m"


def lcd_status_pages(
    snapshot: StatsSnapshot,
    uptime_seconds: int | None,
) -> tuple[tuple[str, str], ...]:
    if snapshot.temperature_c is None:
        temperature_line = "--°C / --°F"
    else:
        temperature_f = round(snapshot.temperature_c * 9 / 5 + 32)
        temperature_line = f"{snapshot.temperature_c:02d}°C / {temperature_f:02d}°F"

    cellular_state = "On" if snapshot.cellular_online is True else "Off"
    cellular_quality = snapshot.cellular_quality if snapshot.cellular_quality is not None else 0

    if snapshot.gps_locked is True:
        gps_heading = "GPS Status: Lock"
    elif snapshot.gps_locked is False or snapshot.gps_satellites == 0:
        gps_heading = "GPS Status: NoFx"
    else:
        gps_heading = "GPS Status: Err"
    gps_view = "--" if snapshot.gps_satellites is None else f"{snapshot.gps_satellites:02d}"
    gps_used = "--" if snapshot.gps_satellites_used is None else f"{snapshot.gps_satellites_used:02d}"
    network_uplink = snapshot.network_uplink or "Offline"
    ap_clients = "--" if snapshot.ap_clients is None else str(snapshot.ap_clients)
    grid_square = snapshot.grid_square or "------"
    return (
        ("PCS Online", format_uptime(uptime_seconds)),
        ("Pi CPU Temp", temperature_line),
        ("Network Uplink", network_uplink),
        (f"Cell Data: {cellular_state}", f"Signal: {cellular_quality:03d}%"),
        (gps_heading, f"View {gps_view} Used {gps_used}"),
        (f"AP Clients: {ap_clients}", f"GridSq: {grid_square}"),
    )


def lcd_alert_page(
    snapshot: MatrixHealthSnapshot,
    alert: MatrixAlert,
) -> tuple[str, str]:
    heading = "HARD FAULT" if alert.severity == "critical" else "WARNING"
    if alert.name == "cpu_temperature":
        value = snapshot.stats.temperature_c
        condition = "CPU TEMP ERROR" if value is None else f"CPU TEMP: {value}°C"
    elif alert.name == "root_disk":
        value = snapshot.root_used_percent
        condition = "ROOT DISK ERROR" if value is None else f"ROOT DISK: {value}%"
    elif alert.name == "primary_usb":
        condition = "USB NOT MOUNTED"
    elif alert.name == "failed_services":
        condition = "SERVICE FAILURE"
    elif alert.name == "pistar":
        condition = "PI-STAR OFFLINE"
    elif alert.name == "router":
        condition = "ROUTER OFFLINE"
    elif alert.name == "network_uplink":
        condition = "UPLINK OFFLINE"
    elif alert.name == "gps_fix":
        condition = "NO GPS FIX"
    else:
        condition = alert.name.replace("_", " ").upper()
    return heading, condition[:LCD_COLUMNS]


def lcd_health_pages(
    snapshot: MatrixHealthSnapshot,
    uptime_seconds: int | None,
) -> tuple[tuple[str, str], ...]:
    alerts = matrix_alerts(snapshot)
    critical = tuple(alert for alert in alerts if alert.severity == "critical")
    if critical:
        return tuple(lcd_alert_page(snapshot, alert) for alert in critical)
    warning_pages = tuple(lcd_alert_page(snapshot, alert) for alert in alerts)
    return lcd_status_pages(snapshot.stats, uptime_seconds) + warning_pages


def stats_frames(snapshot: StatsSnapshot) -> tuple[StatsFrame, ...]:
    if snapshot.gps_satellites == 0 or snapshot.gps_locked is False:
        gps_value = NO_FIX_ICON
    elif snapshot.gps_satellites is None or snapshot.gps_locked is None:
        gps_value = UNKNOWN_ICON
    else:
        gps_value = render_two_digits(snapshot.gps_satellites)
    return (
        StatsFrame("temperature_c", "icon", DEGREE_C_ICON),
        StatsFrame("temperature_c", "value", render_two_digits(snapshot.temperature_c)),
        StatsFrame("cellular_quality", "icon", SIGNAL_ICON),
        StatsFrame("cellular_quality", "value", render_two_digits(snapshot.cellular_quality)),
        StatsFrame("gps_satellites", "icon", SATELLITE_DISH_ICON),
        StatsFrame("gps_satellites", "value", gps_value),
    )


def run_stats(
    matrix: MatrixDisplay,
    *,
    once: bool = False,
    icon_seconds: float = 0.8,
    value_seconds: float = 1.5,
    cycle_pause: float = 0.3,
    collector: Callable[[], StatsSnapshot] = collect_stats,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    while not should_stop():
        snapshot = collector()
        print(json.dumps(snapshot.as_dict(), separators=(",", ":")), flush=True)
        for frame in stats_frames(snapshot):
            if should_stop():
                break
            matrix.rows(frame.rows)
            sleeper(icon_seconds if frame.kind == "icon" else value_seconds)
        if once:
            break
        sleeper(cycle_pause)


def read_disk_used_percent(path: Path = Path("/")) -> int | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return max(0, min(100, round(usage.used * 100 / usage.total)))


def read_primary_usb_mounted(path: Path = Path("/mnt/pcs-usb")) -> bool | None:
    try:
        return path.is_mount()
    except OSError:
        return None


def parse_failed_service_count(output: str) -> int:
    return sum(1 for line in output.splitlines() if line.strip())


def read_failed_service_count() -> int | None:
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_failed_service_count(result.stdout) if result.returncode in (0, 1) else None


def read_install_setting(
    key: str,
    path: Path = INSTALL_CONFIG_PATH,
) -> str | None:
    """Read one simple shell assignment without executing the config file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        candidate, value = stripped.split("=", 1)
        if candidate.strip() != key:
            continue
        try:
            parsed = shlex.split(value, comments=True, posix=True)
        except ValueError:
            return value.strip().strip("\"'")
        return parsed[0] if parsed else ""
    return None


def read_host_online(host: str, ports: Sequence[int] = (80, 22)) -> bool:
    """Return reachability using ICMP first and bounded TCP fallbacks."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", host],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def read_router_online(host: str = OPENWRT_HOST) -> bool:
    """Return reachability for the required OpenWrt AP/switch."""
    return read_host_online(host)


def read_pistar_online(
    config_path: Path = INSTALL_CONFIG_PATH,
    host: str = PISTAR_HOST,
    pair_dir: Path = PISTAR_PAIR_DIR,
) -> bool | None:
    """Return Pi-Star reachability only when its monitoring is configured."""
    configured = read_install_setting("PCS_SETUP_PISTAR", config_path)
    if configured is None and pair_dir.is_dir():
        # ProtectHome=yes deliberately hides the private install config from
        # the LCD and matrix daemons. The dedicated pairing directory is a
        # non-secret /etc marker created only for configured Pi-Star builds.
        configured = "yes"
    if configured is None or configured.lower() != "yes":
        return None
    return read_host_online(host)


def collect_matrix_health() -> MatrixHealthSnapshot:
    return MatrixHealthSnapshot(
        stats=collect_stats(),
        root_used_percent=read_disk_used_percent(),
        primary_usb_mounted=read_primary_usb_mounted(),
        failed_services=read_failed_service_count(),
        pistar_online=read_pistar_online(),
        router_online=read_router_online(),
    )


def matrix_alerts(snapshot: MatrixHealthSnapshot) -> tuple[MatrixAlert, ...]:
    alerts: list[MatrixAlert] = []
    temperature = snapshot.stats.temperature_c
    if temperature is not None and temperature >= TEMPERATURE_CRITICAL_C:
        alerts.append(MatrixAlert("cpu_temperature", "critical", DEGREE_C_ICON))
    elif temperature is not None and temperature >= TEMPERATURE_WARNING_C:
        alerts.append(MatrixAlert("cpu_temperature", "warning", DEGREE_C_ICON))

    root_used = snapshot.root_used_percent
    if root_used is not None and root_used >= DISK_CRITICAL_PERCENT:
        alerts.append(MatrixAlert("root_disk", "critical", STORAGE_ICON))
    elif root_used is not None and root_used >= DISK_WARNING_PERCENT:
        alerts.append(MatrixAlert("root_disk", "warning", STORAGE_ICON))

    if snapshot.primary_usb_mounted is False:
        alerts.append(MatrixAlert("primary_usb", "warning", STORAGE_ICON))
    if snapshot.failed_services is not None and snapshot.failed_services > 0:
        alerts.append(MatrixAlert("failed_services", "critical", SERVICE_ICON))
    if snapshot.router_online is False:
        alerts.append(MatrixAlert("router", "critical", ROUTER_ICON))
    if snapshot.pistar_online is False:
        alerts.append(MatrixAlert("pistar", "warning", PISTAR_ICON))
    if snapshot.stats.network_uplink == "Offline":
        alerts.append(MatrixAlert("network_uplink", "warning", SIGNAL_ICON))
    if snapshot.stats.gps_locked is not True:
        alerts.append(MatrixAlert("gps_fix", "warning", SATELLITE_DISH_ICON))
    return tuple(sorted(alerts, key=lambda alert: 0 if alert.severity == "critical" else 1))


def led_status_indicators(snapshot: MatrixHealthSnapshot) -> tuple[LedIndicator, ...]:
    """Map the six installed status pixels to stable, documented PCS conditions."""
    temperature = snapshot.stats.temperature_c
    if temperature is None:
        cpu = ("unknown", LED_UNKNOWN)
    elif temperature >= TEMPERATURE_CRITICAL_C:
        cpu = ("critical", LED_CRITICAL)
    elif temperature >= TEMPERATURE_WARNING_C:
        cpu = ("warning", LED_WARNING)
    else:
        cpu = ("healthy", LED_HEALTHY)

    root_used = snapshot.root_used_percent
    if root_used is None:
        root_disk = ("unknown", LED_UNKNOWN)
    elif root_used >= DISK_CRITICAL_PERCENT:
        root_disk = ("critical", LED_CRITICAL)
    elif root_used >= DISK_WARNING_PERCENT:
        root_disk = ("warning", LED_WARNING)
    else:
        root_disk = ("healthy", LED_HEALTHY)

    if snapshot.primary_usb_mounted is None:
        primary_usb = ("unknown", LED_UNKNOWN)
    elif snapshot.primary_usb_mounted:
        primary_usb = ("mounted", LED_HEALTHY)
    else:
        primary_usb = ("missing", LED_WARNING)

    if snapshot.failed_services is None:
        services = ("unknown", LED_UNKNOWN)
    elif snapshot.failed_services > 0:
        services = ("failed", LED_CRITICAL)
    elif snapshot.pistar_online is False:
        services = ("dependency_warning", LED_WARNING)
    else:
        services = ("healthy", LED_HEALTHY)

    uplink = snapshot.stats.network_uplink
    if snapshot.router_online is False:
        network = ("router_offline", LED_CRITICAL)
    elif uplink == "Cellular":
        network = ("cellular", LED_HEALTHY)
    elif uplink == "WiFi":
        network = ("wifi", LED_HEALTHY)
    elif uplink == "Offline":
        network = ("offline", LED_WARNING)
    else:
        network = ("unknown", LED_UNKNOWN)

    if snapshot.stats.gps_locked is True:
        gps = ("locked", LED_HEALTHY)
    elif snapshot.stats.gps_locked is False:
        gps = ("no_fix", LED_WARNING)
    else:
        gps = ("unknown", LED_UNKNOWN)

    assignments = (
        ("cpu_temperature", cpu),
        ("root_disk", root_disk),
        ("primary_usb", primary_usb),
        ("failed_services", services),
        ("network_uplink", network),
        ("gps_fix", gps),
    )
    return tuple(
        LedIndicator(pixel, name, state, color)
        for pixel, (name, (state, color)) in enumerate(assignments)
    )


def run_led_status(
    leds: LedDisplay,
    *,
    once: bool = False,
    poll_seconds: float = WS2812_POLL_SECONDS,
    collector: Callable[[], MatrixHealthSnapshot] = collect_matrix_health,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    previous_summary = ""
    previous_colors: tuple[tuple[int, int, int], ...] | None = None
    while not should_stop():
        snapshot = collector()
        indicators = led_status_indicators(snapshot)
        colors = tuple(indicator.color for indicator in indicators)
        if colors != previous_colors:
            leds.colors(colors)
            previous_colors = colors
        summary = json.dumps(
            {
                "health": snapshot.as_dict(),
                "indicators": [indicator.as_dict() for indicator in indicators],
            },
            separators=(",", ":"),
        )
        if summary != previous_summary:
            print(summary, flush=True)
            previous_summary = summary
        if once:
            break
        sleeper(poll_seconds)


def matrix_alert_frames(alerts: Sequence[MatrixAlert]) -> tuple[StatsFrame, ...]:
    if not alerts:
        return (StatsFrame("system_health", "healthy", CHECK_ICON, 1),)
    frames: list[StatsFrame] = []
    for alert in alerts:
        attention = X_ICON if alert.severity == "critical" else EXCLAMATION_ICON
        intensity = 10
        frames.append(StatsFrame(alert.name, alert.severity, attention, intensity))
        frames.append(StatsFrame(alert.name, "subsystem", alert.icon, intensity))
    return tuple(frames)


def run_matrix_alerts(
    matrix: MatrixDisplay,
    *,
    once: bool = False,
    frame_seconds: float = 0.7,
    cycle_pause: float = 2.5,
    collector: Callable[[], MatrixHealthSnapshot] = collect_matrix_health,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    previous_summary = ""
    while not should_stop():
        snapshot = collector()
        alerts = matrix_alerts(snapshot)
        frames = matrix_alert_frames(alerts)
        summary = json.dumps(
            {
                "health": snapshot.as_dict(),
                "alerts": [alert.as_dict() for alert in alerts],
            },
            separators=(",", ":"),
        )
        if summary != previous_summary:
            print(summary, flush=True)
            previous_summary = summary
        for frame in frames:
            if should_stop():
                break
            if frame.intensity is not None:
                matrix.intensity(frame.intensity)
            matrix.rows(frame.rows)
            sleeper(frame_seconds)
        if once:
            break
        sleeper(cycle_pause)


def run_lcd_status(
    lcd: LcdDisplay,
    *,
    once: bool = False,
    page_seconds: float = 3.0,
    collector: Callable[[], MatrixHealthSnapshot] = collect_matrix_health,
    uptime_reader: Callable[[], int | None] = read_uptime_seconds,
    sleeper: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    while not should_stop():
        snapshot = collector()
        alerts = matrix_alerts(snapshot)
        pages = lcd_health_pages(snapshot, uptime_reader())
        print(
            json.dumps(
                {
                    "health": snapshot.as_dict(),
                    "alerts": [alert.as_dict() for alert in alerts],
                    "pages": [list(page) for page in pages],
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        for page in pages:
            if should_stop():
                break
            lcd.text(page)
            sleeper(page_seconds)
        if once:
            break


def python_module_available(name: str, isolated_python: Path | None = None) -> bool:
    if importlib.util.find_spec(name) is not None:
        return True
    if isolated_python is None or not isolated_python.is_file():
        return False
    try:
        result = subprocess.run(
            (str(isolated_python), "-c", f"import {name}"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def dependency_report() -> dict[str, object]:
    model_path = Path("/proc/device-tree/model")
    model = ""
    try:
        model = model_path.read_text(encoding="utf-8").rstrip("\x00")
    except OSError:
        pass
    modules = {
        "gpiozero": python_module_available("gpiozero"),
        "spidev": python_module_available("spidev"),
        "rpi_ws281x": python_module_available("rpi_ws281x", WS2812_PYTHON_PATH),
    }
    return {
        "platform": platform.system(),
        "raspberry_pi_model": model,
        "gpio_header_available": bool(model),
        "spi0_device": Path("/dev/spidev0.0").exists(),
        "fan_pwm_chip": FAN_PWM_CHIP_PATH.exists(),
        "python_modules": modules,
        "effective_uid": os.geteuid() if hasattr(os, "geteuid") else None,
        "writes_performed": False,
    }


def pin_map_json() -> list[dict[str, object]]:
    return [asdict(pin) for pin in PIN_ASSIGNMENTS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and commission PCS GPIO devices safely")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pins = subparsers.add_parser("pins", help="print the authoritative BCM/physical pin map")
    pins.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="read-only Raspberry Pi dependency and interface check")
    check.add_argument("--json", action="store_true")

    demo = subparsers.add_parser("demo", help="simulate a device pattern or explicitly apply it to hardware")
    demo.add_argument("target", choices=("all", "lcd", "matrix", "leds", "fan"))
    demo.add_argument("--hardware", action="store_true", help="select real Raspberry Pi hardware")
    demo.add_argument("--apply", action="store_true", help="confirm that GPIO writes are intended")
    demo.add_argument("--duration", type=float, default=3.0)
    demo.add_argument("--fan-duty", type=float)

    led_status = subparsers.add_parser("led-status", help="show six persistent PCS health indicators")
    led_status.add_argument("--hardware", action="store_true", help="select the real GPIO21 WS2812 chain")
    led_status.add_argument("--apply", action="store_true", help="confirm that LED writes are intended")
    led_status.add_argument("--once", action="store_true", help="apply one health snapshot, then exit")
    led_status.add_argument("--poll-seconds", type=float, default=WS2812_POLL_SECONDS)
    led_status.add_argument("--hold-seconds", type=float, default=0.0, help="hold a one-shot hardware frame before clearing")

    fan_control = subparsers.add_parser("fan-control", help="run the GPIO18 hardware-PWM thermal fan curve")
    fan_control.add_argument("--hardware", action="store_true", help="select the real PWM0 hardware")
    fan_control.add_argument("--apply", action="store_true", help="confirm that fan PWM writes are intended")
    fan_control.add_argument("--once", action="store_true", help="apply one temperature sample, then leave full duty")
    fan_control.add_argument("--poll-seconds", type=float, default=FAN_POLL_SECONDS)

    fan_failsafe = subparsers.add_parser("fan-failsafe", help="leave the GPIO18 fan PWM enabled at full duty")
    fan_failsafe.add_argument("--hardware", action="store_true", help="select the real PWM0 hardware")
    fan_failsafe.add_argument("--apply", action="store_true", help="confirm that full-duty fan PWM is intended")

    lcd = subparsers.add_parser("lcd", help="write two lines to the HD44780-compatible 16x2 LCD")
    lcd.add_argument("--line1", default="PCS ONLINE")
    lcd.add_argument("--line2", default="LCD DRIVER READY")
    lcd.add_argument("--hardware", action="store_true", help="select the real Raspberry Pi LCD")
    lcd.add_argument("--apply", action="store_true", help="confirm that LCD GPIO writes are intended")
    lcd.add_argument("--duration", type=float, default=0.0, help="seconds to hold GPIO ownership after writing")
    lcd.add_argument("--clear", action="store_true", help="clear the LCD before releasing GPIO")

    stats = subparsers.add_parser("stats", help="rotate useful live PCS statistics on the MAX7219")
    stats.add_argument("--hardware", action="store_true", help="select the real SPI0 MAX7219")
    stats.add_argument("--apply", action="store_true", help="confirm that matrix writes are intended")
    stats.add_argument("--once", action="store_true", help="show one complete rotation, then exit")
    stats.add_argument("--icon-seconds", type=float, default=0.8)
    stats.add_argument("--value-seconds", type=float, default=1.5)
    stats.add_argument("--cycle-pause", type=float, default=0.3)

    alerts = subparsers.add_parser("alerts", help="show prioritized PCS health alerts on the MAX7219")
    alerts.add_argument("--hardware", action="store_true", help="select the real SPI0 MAX7219")
    alerts.add_argument("--apply", action="store_true", help="confirm that matrix writes are intended")
    alerts.add_argument("--once", action="store_true", help="show one complete health sequence, then exit")
    alerts.add_argument("--frame-seconds", type=float, default=0.7)
    alerts.add_argument("--cycle-pause", type=float, default=2.5)

    lcd_status = subparsers.add_parser("lcd-status", help="rotate live PCS status on the 16x2 LCD")
    lcd_status.add_argument("--hardware", action="store_true", help="select the real Raspberry Pi LCD")
    lcd_status.add_argument("--apply", action="store_true", help="confirm that LCD GPIO writes are intended")
    lcd_status.add_argument("--once", action="store_true", help="show one complete rotation, then exit")
    lcd_status.add_argument("--page-seconds", type=float, default=3.0)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "pins":
        if args.json:
            print(json.dumps(pin_map_json(), indent=2))
        else:
            for pin in PIN_ASSIGNMENTS:
                print(f"GPIO{pin.gpio:<2} pin {pin.physical:<2}  {pin.function:<18} {pin.owner}; {pin.status}")
        return 0

    if args.command == "check":
        report = dependency_report()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Platform:       {report['platform']}")
            print(f"Raspberry Pi:   {report['raspberry_pi_model'] or 'not detected'}")
            print(f"SPI0 / CE0:     {'available' if report['spi0_device'] else 'missing'}")
            print(f"Fan PWM0:       {'available' if report['fan_pwm_chip'] else 'missing; overlay/reboot required'}")
            for name, available in report["python_modules"].items():  # type: ignore[union-attr]
                print(f"Python {name:<10} {'available' if available else 'missing'}")
            print("GPIO writes:    none")
        return 0

    if args.hardware and not args.apply:
        raise SystemExit("ERROR: real GPIO operation requires both --hardware and --apply")
    if args.apply and not args.hardware:
        raise SystemExit("ERROR: --apply is valid only together with --hardware")

    if args.command == "led-status":
        if args.poll_seconds <= 0:
            raise SystemExit("ERROR: --poll-seconds must be positive")
        if args.hold_seconds < 0:
            raise SystemExit("ERROR: --hold-seconds cannot be negative")
        if not args.hardware:
            snapshot = collect_matrix_health()
            indicators = led_status_indicators(snapshot)
            print(json.dumps({
                "backend": "simulation",
                "gpio": WS2812_PIN,
                "pixel_count": WS2812_COUNT,
                "brightness": WS2812_BRIGHTNESS,
                "health": snapshot.as_dict(),
                "indicators": [indicator.as_dict() for indicator in indicators],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_led_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_led_stop)
        signal.signal(signal.SIGINT, request_led_stop)
        leds: Ws2812 | None = None
        try:
            leds = Ws2812()
            run_led_status(
                leds,
                once=args.once,
                poll_seconds=args.poll_seconds,
                should_stop=lambda: stopped,
            )
            if args.once and args.hold_seconds:
                time.sleep(args.hold_seconds)
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if leds is not None:
                leds.close()
        return 0

    if args.command == "fan-control":
        if args.poll_seconds <= 0:
            raise SystemExit("ERROR: --poll-seconds must be positive")
        if not args.hardware:
            print(json.dumps({
                "backend": "simulation",
                "gpio": FAN_PWM_PIN,
                "channel": FAN_PWM_CHANNEL,
                "frequency_hz": FAN_PWM_FREQUENCY_HZ,
                "curve": [
                    {"temperature_c": temperature_c, "duty_percent": duty_percent}
                    for temperature_c, duty_percent in FAN_CURVE
                ],
                "hysteresis_c": FAN_HYSTERESIS_C,
                "failsafe_duty_percent": FAN_FAILSAFE_DUTY,
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_fan_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_fan_stop)
        signal.signal(signal.SIGINT, request_fan_stop)
        fan: HardwarePwmFan | None = None
        try:
            fan = HardwarePwmFan()
            run_fan_control(
                fan,
                once=args.once,
                poll_seconds=args.poll_seconds,
                should_stop=lambda: stopped,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if fan is not None:
                fan.close()
        return 0

    if args.command == "fan-failsafe":
        if not args.hardware:
            print(json.dumps({
                "backend": "simulation",
                "gpio": FAN_PWM_PIN,
                "duty_percent": FAN_FAILSAFE_DUTY,
                "writes_performed": False,
            }, indent=2))
            return 0
        fan: HardwarePwmFan | None = None
        try:
            fan = HardwarePwmFan()
            fan.duty(FAN_FAILSAFE_DUTY)
        except (OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if fan is not None:
                fan.close()
        return 0

    if args.command == "stats":
        durations = (args.icon_seconds, args.value_seconds, args.cycle_pause)
        if any(duration < 0 for duration in durations):
            raise SystemExit("ERROR: stats durations cannot be negative")
        if not args.hardware:
            snapshot = collect_stats()
            print(json.dumps({
                "backend": "simulation",
                "snapshot": snapshot.as_dict(),
                "frames": [frame.as_dict() for frame in stats_frames(snapshot)],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        matrix: Max7219 | None = None
        try:
            matrix = Max7219()
            run_stats(
                matrix,
                once=args.once,
                icon_seconds=args.icon_seconds,
                value_seconds=args.value_seconds,
                cycle_pause=args.cycle_pause,
                should_stop=lambda: stopped,
            )
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if matrix is not None:
                matrix.close()
        return 0

    if args.command == "alerts":
        if args.frame_seconds < 0 or args.cycle_pause < 0:
            raise SystemExit("ERROR: alert durations cannot be negative")
        if not args.hardware:
            snapshot = collect_matrix_health()
            alerts = matrix_alerts(snapshot)
            print(json.dumps({
                "backend": "simulation",
                "health": snapshot.as_dict(),
                "alerts": [alert.as_dict() for alert in alerts],
                "frames": [frame.as_dict() for frame in matrix_alert_frames(alerts)],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_alert_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_alert_stop)
        signal.signal(signal.SIGINT, request_alert_stop)
        matrix: Max7219 | None = None
        try:
            matrix = Max7219()
            run_matrix_alerts(
                matrix,
                once=args.once,
                frame_seconds=args.frame_seconds,
                cycle_pause=args.cycle_pause,
                should_stop=lambda: stopped,
            )
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if matrix is not None:
                matrix.close()
        return 0

    if args.command == "lcd-status":
        if args.page_seconds < 0:
            raise SystemExit("ERROR: --page-seconds cannot be negative")
        if not args.hardware:
            snapshot = collect_matrix_health()
            alerts = matrix_alerts(snapshot)
            print(json.dumps({
                "backend": "simulation",
                "health": snapshot.as_dict(),
                "alerts": [alert.as_dict() for alert in alerts],
                "pages": [list(page) for page in lcd_health_pages(snapshot, read_uptime_seconds())],
                "writes_performed": False,
            }, indent=2))
            return 0

        stopped = False

        def request_lcd_stop(_signum: int, _frame: object) -> None:
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_lcd_stop)
        signal.signal(signal.SIGINT, request_lcd_stop)
        device: HD44780 | None = None
        try:
            device = HD44780()
            run_lcd_status(
                device,
                once=args.once,
                page_seconds=args.page_seconds,
                should_stop=lambda: stopped,
            )
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if device is not None:
                device.close(clear=False)
        return 0

    if args.command == "lcd":
        if args.duration < 0:
            raise SystemExit("ERROR: --duration cannot be negative")
        lines = normalize_lcd_lines((args.line1, args.line2))
        if not args.hardware:
            print(json.dumps({
                "backend": "simulation",
                "device": "lcd",
                "pins": LCD_PINS,
                "lines": list(lines),
                "writes_performed": False,
            }, indent=2))
            return 0

        device: HD44780 | None = None
        try:
            device = HD44780()
            device.text(lines)
            time.sleep(args.duration)
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
            raise SystemExit(f"ERROR: {error}") from error
        finally:
            if device is not None:
                device.close(clear=args.clear)
        return 0

    if args.duration < 0:
        raise SystemExit("ERROR: --duration cannot be negative")

    backend: Backend = RaspberryPiBackend() if args.hardware else MockBackend()
    try:
        run_demo(backend, args.target, duration=args.duration, fan_duty=args.fan_duty)
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error
    if isinstance(backend, MockBackend):
        print(json.dumps({"backend": "simulation", "events": backend.events}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
