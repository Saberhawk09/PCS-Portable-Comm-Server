#!/usr/bin/env python3

"""Recover a stuck Dire Wolf APRS-IS session after a real uplink change."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import time
from pathlib import Path


DEFAULT_CONFIG = "/etc/direwolf.conf"
DEFAULT_STATE = "/run/pcs-direwolf-uplink"
DEFAULT_RESTART_MARKER = "/run/pcs-direwolf-uplink-recovery.last"
DEFAULT_GRACE_SECONDS = 45
DEFAULT_VERIFY_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_APRS_IS_PORT = 14580


class CommandRunner:
    def run(self, arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def default_interface(runner: CommandRunner) -> str:
    result = runner.run(["ip", "-4", "route", "get", "1.1.1.1"], timeout=5)
    if result.returncode != 0:
        return ""
    match = re.search(r"(?:^|\s)dev\s+(\S+)", result.stdout)
    return match.group(1) if match else ""


def aprs_is_server(path: str | os.PathLike[str]) -> tuple[str, int] | None:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            continue
        if not parts or parts[0].upper() != "IGSERVER" or len(parts) < 2:
            continue
        port = DEFAULT_APRS_IS_PORT
        if len(parts) >= 3:
            try:
                port = int(parts[2])
            except ValueError:
                return None
        if not 1 <= port <= 65535:
            return None
        return parts[1], port
    return None


def server_resolves(runner: CommandRunner, hostname: str) -> bool:
    return runner.run(["getent", "ahostsv4", hostname], timeout=10).returncode == 0


def direwolf_active(runner: CommandRunner) -> bool:
    return runner.run(["systemctl", "is-active", "--quiet", "direwolf.service"], timeout=10).returncode == 0


def direwolf_connected(runner: CommandRunner, port: int) -> bool:
    result = runner.run(["ss", "-H", "-t", "-n", "-p", "state", "established"], timeout=10)
    if result.returncode != 0:
        return False
    port_suffix = f":{port}"
    for line in result.stdout.splitlines():
        fields = line.split()
        if '"direwolf"' not in line or len(fields) < 4:
            continue
        if any(field.rstrip(",").endswith(port_suffix) for field in fields[:5]):
            return True
    return False


def _marker_age(path: Path, now: float) -> float | None:
    try:
        value = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, now - value)


class UplinkRecovery:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        config_path: str | os.PathLike[str] = DEFAULT_CONFIG,
        state_path: str | os.PathLike[str] = DEFAULT_STATE,
        restart_marker: str | os.PathLike[str] = DEFAULT_RESTART_MARKER,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
        verify_seconds: int = DEFAULT_VERIFY_SECONDS,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        sleep=time.sleep,
        monotonic=time.monotonic,
        wall_time=time.time,
    ) -> None:
        self.runner = runner
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.restart_marker = Path(restart_marker)
        self.grace_seconds = grace_seconds
        self.verify_seconds = verify_seconds
        self.cooldown_seconds = cooldown_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self.wall_time = wall_time

    def record_current(self) -> str:
        interface = default_interface(self.runner)
        if not interface:
            return "No default IPv4 uplink is available; baseline unchanged."
        _atomic_write(self.state_path, interface)
        return f"Recorded Dire Wolf uplink baseline: {interface}."

    def _wait_for_connection(self, port: int, seconds: int) -> bool:
        deadline = self.monotonic() + seconds
        while True:
            if direwolf_connected(self.runner, port):
                return True
            if self.monotonic() >= deadline:
                return False
            self.sleep(min(5, max(0, deadline - self.monotonic())))

    def recover(self) -> tuple[bool, str]:
        current = default_interface(self.runner)
        if not current:
            return True, "No default IPv4 uplink is available; Dire Wolf was not restarted."
        try:
            previous = self.state_path.read_text(encoding="utf-8").strip()
        except OSError:
            _atomic_write(self.state_path, current)
            return True, f"Initialized Dire Wolf uplink baseline at {current}."
        if previous == current:
            return True, f"Default uplink remains {current}; no Dire Wolf action required."

        _atomic_write(self.state_path, current)
        server = aprs_is_server(self.config_path)
        if server is None:
            return True, f"Uplink changed from {previous} to {current}; APRS-IS is not configured."
        if not direwolf_active(self.runner):
            return True, f"Uplink changed from {previous} to {current}; Dire Wolf is inactive."

        hostname, port = server
        if self._wait_for_connection(port, self.grace_seconds):
            return True, f"Dire Wolf reconnected to APRS-IS after uplink changed from {previous} to {current}."
        if not server_resolves(self.runner, hostname):
            return True, f"APRS-IS DNS is unavailable after uplink changed to {current}; Dire Wolf remains running and will retry."

        now = self.wall_time()
        age = _marker_age(self.restart_marker, now)
        if age is not None and age < self.cooldown_seconds:
            return True, f"Dire Wolf recovery restart suppressed by the {self.cooldown_seconds}-second cooldown."

        result = self.runner.run(["systemctl", "restart", "direwolf.service"], timeout=90)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "systemctl returned an error"
            return False, f"Dire Wolf recovery restart failed: {detail}"
        _atomic_write(self.restart_marker, str(now))
        if not self._wait_for_connection(port, self.verify_seconds):
            return False, f"Dire Wolf restarted after the uplink change, but APRS-IS did not reconnect within {self.verify_seconds} seconds."
        return True, f"Restarted Dire Wolf after uplink changed from {previous} to {current}; APRS-IS is connected."


def bounded_seconds(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 600:
        raise argparse.ArgumentTypeError("seconds must be between 0 and 600")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record-current", action="store_true")
    mode.add_argument("--recover", action="store_true")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--restart-marker", default=DEFAULT_RESTART_MARKER)
    parser.add_argument("--grace-seconds", type=bounded_seconds, default=DEFAULT_GRACE_SECONDS)
    parser.add_argument("--verify-seconds", type=bounded_seconds, default=DEFAULT_VERIFY_SECONDS)
    parser.add_argument("--cooldown-seconds", type=bounded_seconds, default=DEFAULT_COOLDOWN_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    recovery = UplinkRecovery(
        CommandRunner(),
        config_path=args.config,
        state_path=args.state,
        restart_marker=args.restart_marker,
        grace_seconds=args.grace_seconds,
        verify_seconds=args.verify_seconds,
        cooldown_seconds=args.cooldown_seconds,
    )
    if args.record_current:
        print(recovery.record_current(), flush=True)
        return 0
    ok, message = recovery.recover()
    print(message, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
