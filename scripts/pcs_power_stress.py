#!/usr/bin/env python3
"""Bounded, fail-safe whole-appliance power stress test for commissioned PCS."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Sequence
from urllib.parse import urlparse


REPO_DIR = Path(__file__).resolve().parent.parent
INSTALL_CONFIG = REPO_DIR / "config" / "pcs-install.conf"
POWER_STATUS = Path("/run/pcs-power-monitor/status.json")
POWER_LOG_DIR = Path(os.environ.get("PCS_POWER_STRESS_LOG_DIR", "/var/log/pcs/power-stress"))
UPLOAD_URL = "https://speed.cloudflare.com/__up"
APPLY_CONFIRMATION = "PCS-POWER-STRESS"
RF_CONFIRMATION = "KEY-SA818S-W8IJC-10"
STRESS_MIN_INPUT_VOLTAGE = 11.8
STRESS_MIN_5V_VOLTAGE = 4.75
MAX_INPUT_SAG_FRACTION = 0.15
POWER_SAMPLE_SECONDS = 0.05
MAX_CONSECUTIVE_SAMPLE_ERRORS = 3
STAGE_SETTLE_SECONDS = 2.0
DISPLAY_SERVICES = ("pcs-gpio-leds.service", "pcs-gpio-stats.service")
FAN_SERVICE = "pcs-gpio-fan.service"
FALLBACK_SERVICE = "pcs-cellular-fallback.service"
PTT_GUARD_SERVICE = "pcs-aprs-ptt-safe.service"
RADIO_SERVICES = ("direwolf.service", "graywolf.service")
WS2812_PYTHON = Path("/opt/pcs-gpio-leds/bin/python")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(
    command: Sequence[str], *, check: bool = True, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, check=check, timeout=timeout)


def service_active(name: str) -> bool:
    return subprocess.run(
        ("systemctl", "is-active", "--quiet", name), check=False
    ).returncode == 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stress all Pi CPUs, upload through the cellular interface, run the "
            "fan at full duty, and illuminate the MAX7219 and WS2812 displays fully."
        )
    )
    parser.add_argument("--duration", type=int, default=60, help="test seconds (10-300; default 60)")
    parser.add_argument(
        "--rf-seconds", type=int, default=0,
        help="also key the commissioned SA818S for 1-60 seconds (default disabled)",
    )
    parser.add_argument("--upload-url", default=UPLOAD_URL, help="HTTPS upload sink")
    parser.add_argument("--apply", action="store_true", help="perform the real stress test")
    parser.add_argument("--confirm", default="", help=f"required with --apply: {APPLY_CONFIRMATION}")
    parser.add_argument(
        "--confirm-rf", default="",
        help=f"required when --rf-seconds is nonzero: {RF_CONFIRMATION}",
    )
    parser.add_argument("--_ws2812-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 10 <= args.duration <= 300:
        parser.error("--duration must be from 10 to 300 seconds")
    if not 0 <= args.rf_seconds <= min(60, args.duration):
        parser.error("--rf-seconds must be zero or from 1 to 60 and no longer than --duration")
    parsed_url = urlparse(args.upload_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username:
        parser.error("--upload-url must be an HTTPS URL without embedded credentials")
    return args


def plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "duration_seconds": args.duration,
        "cpu_workers": os.cpu_count() or 1,
        "cellular_upload": args.upload_url,
        "fan": "full duty",
        "max7219": "all pixels, intensity 15/15",
        "ws2812": "six white pixels, brightness 255/255",
        "sa818s_ptt_seconds": args.rf_seconds,
        "abort_below_input_voltage": STRESS_MIN_INPUT_VOLTAGE,
        "abort_below_5v_voltage": STRESS_MIN_5V_VOLTAGE,
        "maximum_input_sag_percent": round(MAX_INPUT_SAG_FRACTION * 100),
        "power_sample_interval_ms": round(POWER_SAMPLE_SECONDS * 1000),
        "persistent_jsonl_log_directory": str(POWER_LOG_DIR),
        "load_sequence": ["baseline", "displays_and_fan", "cellular_upload", "full_cpu"],
        "writes_performed": False,
    }


def read_install_value(name: str) -> str | None:
    try:
        text = INSTALL_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(rf'^\s*{re.escape(name)}=(?:"([^"]*)"|([^\s#]+))', text, re.MULTILINE)
    return (match.group(1) or match.group(2)) if match else None


def gsm_state() -> tuple[str, str] | None:
    result = subprocess.run(
        ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"),
        text=True, capture_output=True, check=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[1] == "gsm":
            return fields[0], fields[2]
    return None


def gsm_profile() -> str:
    result = subprocess.run(
        ("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"),
        text=True, capture_output=True, check=True, timeout=5,
    )
    profiles = []
    for line in result.stdout.splitlines():
        name, separator, connection_type = line.rpartition(":")
        if separator and connection_type == "gsm" and name:
            profiles.append(name.replace(r"\:", ":"))
    if len(profiles) != 1:
        raise RuntimeError(f"expected one configured GSM profile, found {len(profiles)}")
    return profiles[0]


def wait_for_cellular(timeout: float = 10.0) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = gsm_state()
        if state and state[1] == "connected":
            device = state[0]
            lines = subprocess.run(
                ("nmcli", "-g", "GENERAL.IP-IFACE", "device", "show", device),
                text=True, capture_output=True, check=True,
            ).stdout.strip().splitlines()
            iface = lines[0] if lines else ""
            if iface and re.fullmatch(r"[A-Za-z0-9_.:-]+", iface):
                return device, iface
        time.sleep(1)
    raise RuntimeError("cellular interface did not become connected with an IP interface")


class HighRatePowerLogger:
    """Own both INA226s during stress and durably record fast rail samples."""

    FAST_INA226_CONFIG = 0x4007  # 1 average, 140 us bus/shunt, continuous mode.

    def __init__(self, *, interval: float = POWER_SAMPLE_SECONDS) -> None:
        self.interval = interval
        self.stage = "initializing"
        self.abort_reason: str | None = None
        self.path: Path | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._handle = None
        self._bus = None
        self._monitors = {}
        self._started_monotonic = 0.0
        self._last_throttled_check = 0.0
        self._throttled = None
        self.minimum_voltages: dict[str, float] = {}
        self.maximum_currents: dict[str, float] = {}
        self.latest_readings: dict[str, dict[str, object]] = {}
        self.input_abort_floor = STRESS_MIN_INPUT_VOLTAGE
        self.consecutive_sample_errors = 0

    def start(self) -> None:
        sys.path.insert(0, str(REPO_DIR / "scripts"))
        import pcs_power_monitor  # pylint: disable=import-outside-toplevel

        config = pcs_power_monitor.load_config()
        monitor_configs = config["monitors"]
        if not {"input", "rail_5v"}.issubset(monitor_configs):
            raise RuntimeError("stress logging requires input and rail_5v INA226 monitors")
        POWER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.path = POWER_LOG_DIR / f"power-stress-{stamp}-{os.getpid()}.jsonl"
        self._handle = self.path.open("x", encoding="utf-8", buffering=1)
        os.chmod(self.path, 0o644)
        self._bus = pcs_power_monitor.open_bus()
        try:
            for name in ("input", "rail_5v"):
                monitor = pcs_power_monitor.Ina226(self._bus, monitor_configs[name])
                monitor._write(0x00, self.FAST_INA226_CONFIG)  # pylint: disable=protected-access
                self._monitors[name] = monitor
            time.sleep(0.01)
            self._started_monotonic = time.monotonic()
            self._record({"type": "start", "sample_interval_ms": round(self.interval * 1000)})
            self._thread = threading.Thread(target=self._run, name="pcs-power-sampler", daemon=True)
            self._thread.start()
        except Exception:
            self.close()
            raise

    def _record(self, fields: dict[str, object]) -> None:
        if self._handle is None:
            return
        document = {
            "time_epoch": round(time.time(), 6),
            "elapsed_seconds": round(max(0.0, time.monotonic() - self._started_monotonic), 6),
            "stage": self.stage,
            **fields,
        }
        with self._lock:
            self._handle.write(json.dumps(document, separators=(",", ":")) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self._record({"type": "stage"})

    @staticmethod
    def _read_throttled() -> str | None:
        try:
            result = subprocess.run(
                ("vcgencmd", "get_throttled"), text=True, capture_output=True,
                check=False, timeout=0.5,
            )
            value = result.stdout.strip()
            return value.partition("=")[2] if value.startswith("throttled=") else None
        except (OSError, subprocess.SubprocessError):
            return None

    def _sample(self) -> None:
        readings: dict[str, dict[str, object]] = {}
        for name, monitor in self._monitors.items():
            # The regular monitor intentionally restores its averaged mode.
            # Reassert fast conversion before each diagnostic sample so both
            # processes can coexist and normal LCD/web/buzzer protection stays live.
            monitor._write(0x00, self.FAST_INA226_CONFIG)  # pylint: disable=protected-access
            time.sleep(0.001)
            reading = monitor.reading()
            readings[name] = {
                "voltage": reading.voltage,
                "current": reading.current,
                "power": reading.power,
            }
            assert reading.voltage is not None and reading.current is not None
            self.minimum_voltages[name] = min(self.minimum_voltages.get(name, reading.voltage), reading.voltage)
            self.maximum_currents[name] = max(self.maximum_currents.get(name, reading.current), reading.current)
        self.latest_readings = readings
        now = time.monotonic()
        if now - self._last_throttled_check >= 1.0:
            self._throttled = self._read_throttled()
            self._last_throttled_check = now
        self._record({"type": "sample", "rails": readings, "pi_throttled": self._throttled})
        self.consecutive_sample_errors = 0
        input_voltage = readings["input"]["voltage"]
        rail_5v_voltage = readings["rail_5v"]["voltage"]
        if isinstance(input_voltage, (int, float)) and input_voltage < self.input_abort_floor:
            self.abort_reason = f"input fell below {self.input_abort_floor:.2f}V ({input_voltage:.3f}V)"
        elif isinstance(rail_5v_voltage, (int, float)) and rail_5v_voltage < STRESS_MIN_5V_VOLTAGE:
            self.abort_reason = f"5V rail fell below {STRESS_MIN_5V_VOLTAGE:.2f}V ({rail_5v_voltage:.3f}V)"
        if self.abort_reason:
            self._record({"type": "abort", "reason": self.abort_reason})
            self._stop.set()

    def _sample_failed(self, error: Exception) -> None:
        self.consecutive_sample_errors += 1
        detail = f"{type(error).__name__}: {error}"
        self._record({
            "type": "sample_error",
            "error": detail,
            "consecutive_errors": self.consecutive_sample_errors,
        })
        if self.consecutive_sample_errors >= MAX_CONSECUTIVE_SAMPLE_ERRORS:
            self.abort_reason = (
                "high-rate INA226 sampling failed "
                f"{self.consecutive_sample_errors} consecutive times: {detail}"
            )
            self._record({"type": "abort", "reason": self.abort_reason})
            self._stop.set()

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            try:
                self._sample()
            except (OSError, RuntimeError, ValueError, AssertionError) as error:
                self._sample_failed(error)
                if self.abort_reason:
                    break
            deadline += self.interval
            self._stop.wait(max(0.0, deadline - time.monotonic()))

    def raise_if_abort(self) -> None:
        if self.abort_reason:
            raise RuntimeError(self.abort_reason)

    def arm_baseline_sag_limit(self) -> float:
        baseline = self.minimum_voltages.get("input")
        if baseline is None:
            raise RuntimeError("no input-voltage baseline was captured")
        self.input_abort_floor = max(
            STRESS_MIN_INPUT_VOLTAGE,
            baseline * (1.0 - MAX_INPUT_SAG_FRACTION),
        )
        self._record({
            "type": "thresholds",
            "input_abort_floor": round(self.input_abort_floor, 3),
            "rail_5v_abort_floor": STRESS_MIN_5V_VOLTAGE,
            "baseline_input_voltage": baseline,
        })
        return self.input_abort_floor

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.raise_if_abort()
            self._stop.wait(min(0.05, max(0.0, deadline - time.monotonic())))
        self.raise_if_abort()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._handle is not None:
            self._record({
                "type": "stop",
                "abort_reason": self.abort_reason,
                "minimum_voltages": self.minimum_voltages,
                "maximum_currents": self.maximum_currents,
            })
        if self._bus is not None:
            self._bus.close()
            self._bus = None
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class DisplayLoad:
    def __init__(self) -> None:
        self.led_process: subprocess.Popen[bytes] | None = None
        self.matrix = None

    def start(self) -> None:
        sys.path.insert(0, str(REPO_DIR / "scripts"))
        import pcs_gpio  # pylint: disable=import-outside-toplevel

        self.led_process = subprocess.Popen((str(WS2812_PYTHON), str(Path(__file__).resolve()), "--_ws2812-worker"))
        time.sleep(0.5)
        if self.led_process.poll() is not None:
            raise RuntimeError("full-brightness WS2812 worker failed to start")
        self.matrix = pcs_gpio.Max7219()
        self.matrix.intensity(15)
        self.matrix.rows([0xFF] * 8)

    def close(self) -> None:
        try:
            if self.matrix is not None:
                self.matrix.close(clear=True)
        finally:
            self.matrix = None
            if self.led_process is not None:
                try:
                    StressRun.terminate(self.led_process)
                finally:
                    self.led_process = None


def ws2812_worker() -> int:
    """Own full-white LEDs under the installed isolated WS2812 interpreter."""

    sys.path.insert(0, str(REPO_DIR / "scripts"))
    import pcs_gpio  # pylint: disable=import-outside-toplevel

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    pcs_gpio.WS2812_BRIGHTNESS = 255
    leds = pcs_gpio.Ws2812()
    try:
        leds.colors([(255, 255, 255)] * pcs_gpio.WS2812_COUNT)
        print("WS2812 full-white load active", flush=True)
        while not stopping:
            time.sleep(0.2)
    finally:
        leds.close(clear=True)
    return 0


class StressRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.original_active: dict[str, bool] = {}
        self.cellular_started = False
        self.cellular_profile: str | None = None
        self.buzzer_was_muted = Path("/run/pcs-buzzer/muted").exists()
        self.cpu_workers: list[subprocess.Popen[bytes]] = []
        self.dd_process: subprocess.Popen[bytes] | None = None
        self.curl_process: subprocess.Popen[bytes] | None = None
        self.ptt_process: subprocess.Popen[bytes] | None = None
        self.radio_handoff = False
        self.display = DisplayLoad()
        self.power_logger = HighRatePowerLogger()
        self.stopping = False
        self.cleanup_errors: list[str] = []

    def snapshot_services(self) -> None:
        names = (*DISPLAY_SERVICES, FAN_SERVICE, FALLBACK_SERVICE, *RADIO_SERVICES, PTT_GUARD_SERVICE)
        self.original_active = {name: service_active(name) for name in names}

    def start_cellular(self) -> str:
        initial = gsm_state()
        if initial is None:
            raise RuntimeError("no NetworkManager GSM device is present")
        if self.original_active.get(FALLBACK_SERVICE):
            run(("systemctl", "stop", FALLBACK_SERVICE))
        if initial[1] != "connected":
            self.cellular_profile = gsm_profile()
            run(
                ("nmcli", "--wait", "20", "connection", "up", self.cellular_profile),
                timeout=25,
            )
            self.cellular_started = True
        _device, iface = wait_for_cellular()
        print(f"Cellular upload interface: {iface}", flush=True)
        return iface

    def start_upload(self, iface: str) -> None:
        self.dd_process = subprocess.Popen(
            ("dd", "if=/dev/zero", "bs=1M", "status=none"), stdout=subprocess.PIPE
        )
        assert self.dd_process.stdout is not None
        self.curl_process = subprocess.Popen(
            (
                "curl", "--interface", iface, "-4", "--fail", "--show-error", "--silent",
                "--connect-timeout", "15", "--max-time", str(self.args.duration + 10),
                "--request", "POST", "--header", "Content-Type: application/octet-stream",
                "--data-binary", "@-", self.args.upload_url,
            ),
            stdin=self.dd_process.stdout, stdout=subprocess.DEVNULL,
        )
        self.dd_process.stdout.close()

    def start_cpu(self) -> None:
        for _ in range(os.cpu_count() or 1):
            self.cpu_workers.append(
                subprocess.Popen(("yes",), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            )

    def key_radio(self) -> None:
        if not self.args.rf_seconds:
            return
        expected = {
            "PCS_APRS_CALLSIGN": "W8IJC-10",
            "PCS_APRS_PTT_GPIO_LINE": "6",
            "PCS_APRS_PTT_ACTIVE_LEVEL": "high",
        }
        for name, value in expected.items():
            if read_install_value(name) != value:
                raise RuntimeError(f"commissioned RF safety value {name} does not match {value}")
        self.radio_handoff = True
        for service in RADIO_SERVICES:
            if self.original_active.get(service):
                run(("systemctl", "stop", service))
        time.sleep(1)
        run(("systemctl", "stop", PTT_GUARD_SERVICE), check=False)
        self.ptt_process = subprocess.Popen(
            ("gpioset", "--chip", "gpiochip0", "--consumer", "pcs-power-stress", "6=1")
        )
        time.sleep(0.2)
        if self.ptt_process.poll() is not None:
            raise RuntimeError("GPIO6 PTT could not be acquired")
        print(f"SA818S PTT asserted for at most {self.args.rf_seconds}s", flush=True)

    def release_radio(self) -> None:
        try:
            self.terminate(self.ptt_process)
        finally:
            self.ptt_process = None
            run(("systemctl", "start", PTT_GUARD_SERVICE), check=False)
            subprocess.run(("/usr/local/sbin/pcs-aprs-ptt-safe", "--check"), check=False)

    @staticmethod
    def terminate(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def cleanup(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        def safely(label: str, action) -> None:
            try:
                action()
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
                self.cleanup_errors.append(f"{label}: {error}")

        if self.radio_handoff:
            safely("PTT safety handoff", self.release_radio)
        safely("cellular upload", lambda: self.terminate(self.curl_process))
        safely("upload generator", lambda: self.terminate(self.dd_process))
        for index, worker in enumerate(self.cpu_workers):
            safely(f"CPU worker {index}", lambda worker=worker: self.terminate(worker))
        try:
            safely("display clear", self.display.close)
        finally:
            safely("power logger", self.power_logger.close)
            for service in (*DISPLAY_SERVICES, FAN_SERVICE):
                if self.original_active.get(service):
                    safely(service, lambda service=service: run(("systemctl", "start", service), check=False))
            for service in RADIO_SERVICES:
                if self.original_active.get(service):
                    safely(service, lambda service=service: run(("systemctl", "start", service), check=False))
            if self.cellular_started:
                safely(
                    "cellular disconnect",
                    lambda: run(
                        ("nmcli", "--wait", "10", "connection", "down", self.cellular_profile or ""),
                        check=False,
                        timeout=15,
                    ),
                )
            if self.original_active.get(FALLBACK_SERVICE):
                safely(
                    FALLBACK_SERVICE,
                    lambda: run(("systemctl", "start", FALLBACK_SERVICE), check=False),
                )
            if not self.buzzer_was_muted:
                safely(
                    "buzzer unmute",
                    lambda: run(("/usr/local/sbin/pcs-buzzer", "unmute"), check=False),
                )
        for error in self.cleanup_errors:
            print(f"WARNING: cleanup {error}", file=sys.stderr)

    def execute(self) -> None:
        self.snapshot_services()
        for service in DISPLAY_SERVICES:
            if self.original_active.get(service):
                run(("systemctl", "stop", service))
        if self.original_active.get(FAN_SERVICE):
            run(("systemctl", "stop", FAN_SERVICE))
        if not self.buzzer_was_muted:
            run(("/usr/local/sbin/pcs-buzzer", "mute"), check=False)
        self.power_logger.start()
        print(f"Persistent 20 Hz power log: {self.power_logger.path}", flush=True)
        self.power_logger.set_stage("baseline")
        self.power_logger.wait(STAGE_SETTLE_SECONDS)
        input_floor = self.power_logger.arm_baseline_sag_limit()
        print(f"Dynamic input abort floor: {input_floor:.2f}V", flush=True)
        self.display.start()
        self.power_logger.set_stage("displays_and_fan")
        self.power_logger.wait(STAGE_SETTLE_SECONDS)
        self.power_logger.set_stage("cellular_connect")
        iface = self.start_cellular()
        self.start_upload(iface)
        self.power_logger.set_stage("cellular_upload")
        self.power_logger.wait(STAGE_SETTLE_SECONDS)
        self.start_cpu()
        self.power_logger.set_stage("full_cpu")
        self.key_radio()
        started = time.monotonic()
        rf_deadline = started + self.args.rf_seconds
        deadline = started + self.args.duration
        next_report = started
        while time.monotonic() < deadline:
            self.power_logger.raise_if_abort()
            if self.ptt_process is not None and time.monotonic() >= rf_deadline:
                self.release_radio()
                print("SA818S PTT released and guard verified", flush=True)
            if self.curl_process is not None and self.curl_process.poll() is not None:
                raise RuntimeError(f"cellular upload exited early with status {self.curl_process.returncode}")
            if time.monotonic() >= next_report:
                input_rail = self.power_logger.latest_readings.get("input", {})
                rail_5v = self.power_logger.latest_readings.get("rail_5v", {})
                print(
                    f"Input {input_rail.get('voltage', '--')}V {input_rail.get('current', '--')}A; "
                    f"5V {rail_5v.get('voltage', '--')}V {rail_5v.get('current', '--')}A; "
                    f"{max(0, int(deadline - time.monotonic()))}s left",
                    flush=True,
                )
                next_report += 1.0
            self.power_logger.wait(min(0.25, max(0.0, deadline - time.monotonic())))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args._ws2812_worker:
        return ws2812_worker()
    if not args.apply:
        print(json.dumps(plan(args), indent=2))
        print(f"Apply with --apply --confirm {APPLY_CONFIRMATION}")
        if args.rf_seconds:
            print(f"RF also requires --confirm-rf {RF_CONFIRMATION}")
        return 0
    if args.confirm != APPLY_CONFIRMATION:
        raise SystemExit(f"ERROR: --apply requires --confirm {APPLY_CONFIRMATION}")
    if args.rf_seconds and args.confirm_rf != RF_CONFIRMATION:
        raise SystemExit(f"ERROR: RF key-down requires --confirm-rf {RF_CONFIRMATION}")
    if os.geteuid() != 0:
        raise SystemExit("ERROR: run the applied test with sudo")
    if not WS2812_PYTHON.is_file():
        raise SystemExit(f"ERROR: installed WS2812 Python runtime is unavailable: {WS2812_PYTHON}")
    for command in ("curl", "dd", "gpioset", "nmcli", "systemctl", "yes"):
        if not command_exists(command):
            raise SystemExit(f"ERROR: required command is unavailable: {command}")

    stress = StressRun(args)
    interrupted = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        stress.execute()
    except KeyboardInterrupt:
        print("Stress test interrupted; restoring PCS", file=sys.stderr)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        stress.cleanup()
    if stress.cleanup_errors:
        return 1
    if interrupted:
        return 130
    print("PCS power stress completed; normal services restored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
