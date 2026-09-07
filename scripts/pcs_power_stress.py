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
import time
from typing import Sequence
from urllib.parse import urlparse


REPO_DIR = Path(__file__).resolve().parent.parent
INSTALL_CONFIG = REPO_DIR / "config" / "pcs-install.conf"
POWER_STATUS = Path("/run/pcs-power-monitor/status.json")
UPLOAD_URL = "https://speed.cloudflare.com/__up"
APPLY_CONFIRMATION = "PCS-POWER-STRESS"
RF_CONFIRMATION = "KEY-SA818S-W8IJC-10"
DISPLAY_SERVICES = ("pcs-gpio-leds.service", "pcs-gpio-stats.service")
FAN_SERVICE = "pcs-gpio-fan.service"
FALLBACK_SERVICE = "pcs-cellular-fallback.service"
PTT_GUARD_SERVICE = "pcs-aprs-ptt-safe.service"
RADIO_SERVICES = ("direwolf.service", "graywolf.service")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, check=check)


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


def wait_for_cellular(timeout: float = 35.0) -> tuple[str, str]:
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


class DisplayLoad:
    def __init__(self) -> None:
        self.leds = None
        self.matrix = None

    def start(self) -> None:
        sys.path.insert(0, str(REPO_DIR / "scripts"))
        import pcs_gpio  # pylint: disable=import-outside-toplevel

        pcs_gpio.WS2812_BRIGHTNESS = 255
        self.leds = pcs_gpio.Ws2812()
        self.matrix = pcs_gpio.Max7219()
        self.leds.colors([(255, 255, 255)] * pcs_gpio.WS2812_COUNT)
        self.matrix.intensity(15)
        self.matrix.rows([0xFF] * 8)

    def close(self) -> None:
        try:
            if self.matrix is not None:
                self.matrix.close(clear=True)
        finally:
            self.matrix = None
            if self.leds is not None:
                try:
                    self.leds.close(clear=True)
                finally:
                    self.leds = None


class StressRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.original_active: dict[str, bool] = {}
        self.cellular_started = False
        self.buzzer_was_muted = Path("/run/pcs-buzzer/muted").exists()
        self.cpu_workers: list[subprocess.Popen[bytes]] = []
        self.dd_process: subprocess.Popen[bytes] | None = None
        self.curl_process: subprocess.Popen[bytes] | None = None
        self.ptt_process: subprocess.Popen[bytes] | None = None
        self.radio_handoff = False
        self.display = DisplayLoad()
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
            run(("/usr/local/sbin/pcs-web-action", "cellular-connect"))
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
            for service in (*DISPLAY_SERVICES, FAN_SERVICE):
                if self.original_active.get(service):
                    safely(service, lambda service=service: run(("systemctl", "start", service), check=False))
            for service in RADIO_SERVICES:
                if self.original_active.get(service):
                    safely(service, lambda service=service: run(("systemctl", "start", service), check=False))
            if self.cellular_started:
                safely(
                    "cellular disconnect",
                    lambda: run(("/usr/local/sbin/pcs-web-action", "cellular-disconnect"), check=False),
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
        self.display.start()
        iface = self.start_cellular()
        self.start_upload(iface)
        self.start_cpu()
        self.key_radio()
        started = time.monotonic()
        rf_deadline = started + self.args.rf_seconds
        deadline = started + self.args.duration
        while time.monotonic() < deadline:
            if self.ptt_process is not None and time.monotonic() >= rf_deadline:
                self.release_radio()
                print("SA818S PTT released and guard verified", flush=True)
            if self.curl_process is not None and self.curl_process.poll() is not None:
                raise RuntimeError(f"cellular upload exited early with status {self.curl_process.returncode}")
            try:
                power = json.loads(POWER_STATUS.read_text(encoding="utf-8"))
                low = power.get("low_voltage", {})
                monitor = power.get("monitors", {}).get("input", {})
                print(
                    f"Input {monitor.get('voltage', '--')}V {monitor.get('current', '--')}A "
                    f"{monitor.get('power', '--')}W; {max(0, int(deadline - time.monotonic()))}s left",
                    flush=True,
                )
                if low.get("active"):
                    raise RuntimeError("low input voltage detected; ending stress immediately")
            except FileNotFoundError:
                raise RuntimeError("PCS power snapshot disappeared during stress") from None
            time.sleep(2)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
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
