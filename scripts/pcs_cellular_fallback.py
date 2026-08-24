#!/usr/bin/env python3

"""PCS NetworkManager Wi-Fi-to-cellular fallback controller."""

from __future__ import annotations

import argparse
import configparser
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = "/etc/pcs/cellular-fallback.conf"
DEFAULT_MARKER = "/run/pcs-cellular-fallback-owned"


@dataclass(frozen=True)
class FallbackConfig:
    wifi_interface: str = "wlan0"
    cellular_profile: str = "pcs-cellular-profile"
    poll_seconds: int = 10
    wifi_loss_seconds: int = 30
    wifi_recovery_seconds: int = 30


def _bounded_integer(parser: configparser.ConfigParser, key: str, default: int) -> int:
    value = parser.getint("fallback", key, fallback=default)
    if not 0 <= value <= 3600:
        raise ValueError(f"{key} must be between 0 and 3600 seconds")
    return value


def load_config(path: str | os.PathLike[str]) -> FallbackConfig:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path, encoding="utf-8"):
        raise ValueError(f"fallback configuration was not readable: {path}")
    if not parser.has_section("fallback"):
        raise ValueError("fallback configuration is missing [fallback]")

    wifi_interface = parser.get("fallback", "wifi_interface", fallback="wlan0").strip()
    cellular_profile = parser.get(
        "fallback",
        "cellular_profile",
        fallback="pcs-cellular-profile",
    ).strip()
    if not wifi_interface or any(char.isspace() for char in wifi_interface):
        raise ValueError("wifi_interface must be one non-empty interface name")
    if not cellular_profile or "\n" in cellular_profile or "\r" in cellular_profile:
        raise ValueError("cellular_profile must be one non-empty profile name")

    poll_seconds = _bounded_integer(parser, "poll_seconds", 10)
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be at least 1")

    return FallbackConfig(
        wifi_interface=wifi_interface,
        cellular_profile=cellular_profile,
        poll_seconds=poll_seconds,
        wifi_loss_seconds=_bounded_integer(parser, "wifi_loss_seconds", 30),
        wifi_recovery_seconds=_bounded_integer(parser, "wifi_recovery_seconds", 30),
    )


class NetworkManager:
    def _run(self, arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["nmcli", *arguments],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def wifi_active(self, interface: str) -> bool:
        result = self._run(["--get-values", "GENERAL.STATE", "device", "show", interface])
        if result.returncode != 0:
            return False
        value = result.stdout.strip().split(maxsplit=1)[0] if result.stdout.strip() else ""
        return value == "100"

    def cellular_active(self, profile: str) -> bool:
        result = self._run(["--get-values", "NAME", "connection", "show", "--active"])
        if result.returncode != 0:
            return False
        return profile in {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def connect_cellular(self, profile: str) -> tuple[bool, str]:
        result = self._run(["--wait", "60", "connection", "up", "id", profile], timeout=75)
        message = (result.stdout or result.stderr).strip()
        return result.returncode == 0, message

    def disconnect_cellular(self, profile: str) -> tuple[bool, str]:
        result = self._run(["--wait", "30", "connection", "down", "id", profile], timeout=45)
        message = (result.stdout or result.stderr).strip()
        return result.returncode == 0, message


class FallbackController:
    def __init__(
        self,
        config: FallbackConfig,
        network_manager: NetworkManager,
        marker_path: str | os.PathLike[str] = DEFAULT_MARKER,
    ) -> None:
        self.config = config
        self.network_manager = network_manager
        self.marker_path = Path(marker_path)
        self.wifi_lost_at: float | None = None
        self.wifi_restored_at: float | None = None

    def owns_cellular(self) -> bool:
        try:
            return self.marker_path.read_text(encoding="utf-8").strip() == self.config.cellular_profile
        except OSError:
            return False

    def _record_ownership(self) -> None:
        temporary = self.marker_path.with_name(f"{self.marker_path.name}.tmp.{os.getpid()}")
        temporary.write_text(f"{self.config.cellular_profile}\n", encoding="utf-8")
        # The profile name is not secret. World-readable/root-writable mode lets
        # the normal-user status and self-test tools verify ownership safely.
        os.chmod(temporary, 0o644)
        os.replace(temporary, self.marker_path)

    def _clear_ownership(self) -> None:
        try:
            self.marker_path.unlink()
        except FileNotFoundError:
            pass

    def release_owned(self) -> str:
        if not self.owns_cellular():
            return "No fallback-owned cellular session is recorded."
        if self.network_manager.cellular_active(self.config.cellular_profile):
            ok, detail = self.network_manager.disconnect_cellular(self.config.cellular_profile)
            if not ok:
                raise RuntimeError(f"failed to release fallback-owned cellular session: {detail}")
        self._clear_ownership()
        return "Fallback-owned cellular session released."

    def step(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        wifi_active = self.network_manager.wifi_active(self.config.wifi_interface)
        cellular_active = self.network_manager.cellular_active(self.config.cellular_profile)
        owned = self.owns_cellular()

        if wifi_active:
            self.wifi_lost_at = None
            if not owned:
                self.wifi_restored_at = None
                return "Wi-Fi active; cellular remains under manual control."
            if not cellular_active:
                self._clear_ownership()
                self.wifi_restored_at = None
                return "Wi-Fi active; cleared stale fallback ownership."
            if self.wifi_restored_at is None:
                self.wifi_restored_at = now
                return "Wi-Fi restored; waiting for recovery stability."
            if now - self.wifi_restored_at < self.config.wifi_recovery_seconds:
                return "Wi-Fi recovery stability window is still running."
            ok, detail = self.network_manager.disconnect_cellular(self.config.cellular_profile)
            if not ok:
                return f"Wi-Fi active, but fallback-owned cellular disconnect failed: {detail}"
            self._clear_ownership()
            self.wifi_restored_at = None
            return "Wi-Fi stable; disconnected fallback-owned cellular session."

        self.wifi_restored_at = None
        if cellular_active:
            self.wifi_lost_at = None
            return "Wi-Fi unavailable; cellular is already connected."
        if self.wifi_lost_at is None:
            self.wifi_lost_at = now
            return "Wi-Fi unavailable; waiting for loss stability."
        if now - self.wifi_lost_at < self.config.wifi_loss_seconds:
            return "Wi-Fi loss stability window is still running."

        ok, detail = self.network_manager.connect_cellular(self.config.cellular_profile)
        if not ok:
            return f"Wi-Fi unavailable, but cellular activation failed: {detail}"
        self._record_ownership()
        self.wifi_lost_at = None
        return "Wi-Fi unavailable; connected cellular as an automatic fallback."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--once", action="store_true", help="Evaluate one controller step and exit.")
    parser.add_argument(
        "--release-owned",
        action="store_true",
        help="Disconnect only a cellular session previously started by this controller.",
    )
    parser.add_argument("--check", action="store_true", help="Print configuration and live state without changes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError, configparser.Error) as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2

    network_manager = NetworkManager()
    controller = FallbackController(config, network_manager, args.marker)

    if args.check:
        print(f"Wi-Fi interface: {config.wifi_interface}")
        print(f"Cellular profile: {config.cellular_profile}")
        print(f"Poll interval: {config.poll_seconds} seconds")
        print(f"Wi-Fi loss stability: {config.wifi_loss_seconds} seconds")
        print(f"Wi-Fi recovery stability: {config.wifi_recovery_seconds} seconds")
        print(f"Wi-Fi active: {'yes' if network_manager.wifi_active(config.wifi_interface) else 'no'}")
        print(f"Cellular active: {'yes' if network_manager.cellular_active(config.cellular_profile) else 'no'}")
        print(f"Fallback owns cellular: {'yes' if controller.owns_cellular() else 'no'}")
        return 0

    if args.release_owned:
        try:
            print(controller.release_owned(), flush=True)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", flush=True)
            return 1
        return 0
    if args.once:
        print(controller.step(), flush=True)
        return 0

    previous_message = ""
    while True:
        try:
            message = controller.step()
            if message != previous_message:
                print(message, flush=True)
                previous_message = message
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Fallback evaluation failed: {exc}", flush=True)
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
