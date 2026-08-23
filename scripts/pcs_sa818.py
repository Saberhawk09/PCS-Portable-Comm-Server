#!/usr/bin/env python3
"""Program and verify the PCS SA818S radio over its dedicated UART.

The utility deliberately owns only the SA818S control UART. Dire Wolf remains
the sole owner of GPIO6 PTT and the USB audio path.
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import select
import sys
import time
from dataclasses import dataclass
from pathlib import Path


GROUP_RE = re.compile(
    r"\+DMOREADGROUP:(?P<bandwidth>[01]),"
    r"(?P<tx>[0-9]{3}\.[0-9]{4}),"
    r"(?P<rx>[0-9]{3}\.[0-9]{4}),"
    r"(?P<tx_tone>[0-9]{4}|[0-9]{3}[A-Z]),"
    r"(?P<squelch>[0-8]),"
    r"(?P<rx_tone>[0-9]{4}|[0-9]{3}[A-Z])"
)

# The commissioned SA818S V1.2 returns its acknowledgement quickly but is not
# ready for another command immediately. The validated UART tooling waited
# 400 ms between transactions; without that settling time the next otherwise
# valid command can return +DMOERROR.
COMMAND_SETTLE_SECONDS = 0.4


@dataclass(frozen=True)
class RadioProfile:
    device: str = "/dev/serial0"
    baud: int = 9600
    bandwidth_khz: int = 25
    tx_frequency_mhz: str = "144.5500"
    rx_frequency_mhz: str = "144.5500"
    tx_tone: str = "0000"
    squelch: int = 1
    rx_tone: str = "0000"
    volume: int = 8
    pre_de_emphasis: bool = False
    high_pass: bool = False
    low_pass: bool = False
    tx_tail: bool = False

    @property
    def bandwidth_code(self) -> int:
        return 1 if self.bandwidth_khz == 25 else 0

    @staticmethod
    def _filter_code(enabled: bool) -> int:
        # SA818S uses 0 for enabled and 1 for disabled.
        return 0 if enabled else 1

    def expected_group(self) -> str:
        return (
            f"{self.bandwidth_code},{self.tx_frequency_mhz},"
            f"{self.rx_frequency_mhz},{self.tx_tone},{self.squelch},"
            f"{self.rx_tone}"
        )

    def programming_commands(self) -> tuple[tuple[str, str], ...]:
        return (
            ("AT+DMOCONNECT", r"\+DMOCONNECT:0"),
            (f"AT+DMOSETGROUP={self.expected_group()}", r"\+DMOSETGROUP:0"),
            (f"AT+DMOSETVOLUME={self.volume}", r"\+DMOSETVOLUME:0"),
            (
                "AT+SETFILTER="
                f"{self._filter_code(self.pre_de_emphasis)},"
                f"{self._filter_code(self.high_pass)},"
                f"{self._filter_code(self.low_pass)}",
                r"\+DMOSETFILTER:0",
            ),
            (f"AT+SETTAIL={1 if self.tx_tail else 0}", r"\+DMOSETTAIL:0"),
        )


def parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"yes", "true", "on", "1"}:
        return True
    if normalized in {"no", "false", "off", "0"}:
        return False
    raise ValueError(f"{name} must be yes/no, true/false, on/off, or 1/0")


def load_profile(path: Path) -> RadioProfile:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except OSError as exc:
        raise ValueError(f"cannot read radio profile {path}: {exc}") from exc
    if "radio" not in parser:
        raise ValueError(f"radio profile {path} is missing [radio]")

    radio = parser["radio"]
    profile = RadioProfile(
        device=radio.get("device", RadioProfile.device),
        baud=radio.getint("baud", RadioProfile.baud),
        bandwidth_khz=radio.getint("bandwidth_khz", RadioProfile.bandwidth_khz),
        tx_frequency_mhz=radio.get("tx_frequency_mhz", RadioProfile.tx_frequency_mhz),
        rx_frequency_mhz=radio.get("rx_frequency_mhz", RadioProfile.rx_frequency_mhz),
        tx_tone=radio.get("tx_tone", RadioProfile.tx_tone).upper(),
        squelch=radio.getint("squelch", RadioProfile.squelch),
        rx_tone=radio.get("rx_tone", RadioProfile.rx_tone).upper(),
        volume=radio.getint("volume", RadioProfile.volume),
        pre_de_emphasis=parse_bool(
            radio.get("pre_de_emphasis", "off"), "pre_de_emphasis"
        ),
        high_pass=parse_bool(radio.get("high_pass", "off"), "high_pass"),
        low_pass=parse_bool(radio.get("low_pass", "off"), "low_pass"),
        tx_tail=parse_bool(radio.get("tx_tail", "off"), "tx_tail"),
    )
    validate_profile(profile)
    return profile


def validate_profile(profile: RadioProfile) -> None:
    if not profile.device.startswith("/dev/") or not re.fullmatch(
        r"/dev/[A-Za-z0-9._/-]+", profile.device
    ):
        raise ValueError("device must be an absolute path below /dev")
    if profile.baud != 9600:
        raise ValueError("the commissioned SA818S UART must use 9600 baud")
    if profile.bandwidth_khz not in {12, 25}:
        raise ValueError("bandwidth_khz must be 12 or 25")
    for name, value in (
        ("tx_frequency_mhz", profile.tx_frequency_mhz),
        ("rx_frequency_mhz", profile.rx_frequency_mhz),
    ):
        if not re.fullmatch(r"[0-9]{3}\.[0-9]{4}", value):
            raise ValueError(f"{name} must use exactly four decimal places")
        frequency = float(value)
        if not (134.0 <= frequency <= 174.0 or 400.0 <= frequency <= 470.0):
            raise ValueError(f"{name} is outside the SA818S VHF/UHF ranges")
    for name, value in (("tx_tone", profile.tx_tone), ("rx_tone", profile.rx_tone)):
        if not re.fullmatch(r"[0-9]{4}|[0-9]{3}[A-Z]", value):
            raise ValueError(f"{name} has an unsupported SA818S tone code")
    if not 0 <= profile.squelch <= 8:
        raise ValueError("squelch must be between 0 and 8")
    if not 1 <= profile.volume <= 8:
        raise ValueError("volume must be between 1 and 8")


class SerialSession:
    def __init__(self, profile: RadioProfile, timeout: float = 1.5) -> None:
        self.profile = profile
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> "SerialSession":
        if os.name != "posix":
            raise RuntimeError("SA818S UART access is supported only on Linux")
        import fcntl
        import termios

        self.fd = os.open(self.profile.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[3] = 0
        attrs[4] = termios.B9600
        attrs[5] = termios.B9600
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def transact(self, command: str, expected: str) -> str:
        if self.fd is None:
            raise RuntimeError("serial session is not open")
        import termios

        termios.tcflush(self.fd, termios.TCIFLUSH)
        os.write(self.fd, f"{command}\r\n".encode("ascii"))
        deadline = time.monotonic() + self.timeout
        response = bytearray()
        pattern = re.compile(expected)
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(self.fd, 256)
            if chunk:
                response.extend(chunk)
                decoded = response.decode("ascii", errors="replace")
                if pattern.search(decoded):
                    return decoded.strip()
        decoded = response.decode("ascii", errors="replace").strip()
        raise RuntimeError(
            f"{command} did not return the expected response {expected!r}; got {decoded!r}"
        )


def verify_group(response: str, profile: RadioProfile) -> None:
    match = GROUP_RE.search(response)
    if match is None:
        raise RuntimeError(f"SA818S returned an invalid group readback: {response!r}")
    actual = ",".join(match.group(name) for name in (
        "bandwidth", "tx", "rx", "tx_tone", "squelch", "rx_tone"
    ))
    expected = profile.expected_group()
    if actual != expected:
        raise RuntimeError(f"SA818S group mismatch: expected {expected}, received {actual}")


def apply_profile(profile: RadioProfile, retries: int, timeout: float) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with SerialSession(profile, timeout=timeout) as session:
                for command, expected in profile.programming_commands():
                    session.transact(command, expected)
                    time.sleep(COMMAND_SETTLE_SECONDS)
                readback = session.transact("AT+DMOREADGROUP", GROUP_RE.pattern)
                verify_group(readback, profile)
            return
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5)
    raise RuntimeError(f"SA818S initialization failed after {retries} attempts: {last_error}")


def check_profile(profile: RadioProfile, timeout: float) -> None:
    with SerialSession(profile, timeout=timeout) as session:
        session.transact("AT+DMOCONNECT", r"\+DMOCONNECT:0")
        time.sleep(COMMAND_SETTLE_SECONDS)
        readback = session.transact("AT+DMOREADGROUP", GROUP_RE.pattern)
        verify_group(readback, profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Program and verify the PCS SA818S")
    parser.add_argument("--config", default="/etc/pcs/aprs/sa818.ini")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true", help="program and verify the profile")
    action.add_argument("--check", action="store_true", help="read and verify the group profile")
    action.add_argument("--dry-run", action="store_true", help="print commands without opening UART")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args(argv)

    try:
        profile = load_profile(Path(args.config))
        if not 1 <= args.retries <= 10:
            raise ValueError("retries must be between 1 and 10")
        if not 0.1 <= args.timeout <= 10:
            raise ValueError("timeout must be between 0.1 and 10 seconds")
        if args.dry_run:
            for command, _ in profile.programming_commands():
                print(command)
            print("AT+DMOREADGROUP")
        elif args.check:
            check_profile(profile, args.timeout)
            print(f"SA818S group verified: {profile.expected_group()}")
        else:
            apply_profile(profile, args.retries, args.timeout)
            print(f"SA818S programmed and verified: {profile.expected_group()}")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
