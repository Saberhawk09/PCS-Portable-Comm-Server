#!/usr/bin/env python3

"""Validate and apply the non-secret PCS automatic-backup policy."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CONFIG_FILE = Path(os.environ.get("PCS_BACKUP_CONFIG_FILE", "/etc/pcs-backup/config.json"))
SYSTEMCTL = os.environ.get("PCS_SYSTEMCTL", "/usr/bin/systemctl")
TIMER_UNIT = "pcs-backup.timer"
SERVICE_UNIT = "pcs-backup.service"
SYSTEMD_RUN = "/usr/bin/systemd-run"
HOST_MARKER = "PCS_BACKUP_HOST_ACTION"
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 43_200
DEFAULT_CONFIG = {
    "version": 2,
    "enabled": True,
    "interval_minutes": 10,
    "keep_history": False,
}
MAX_INPUT_BYTES = 4096


class ConfigError(ValueError):
    pass


def validate_config(value: object, *, require_version: bool = True) -> dict:
    if not isinstance(value, dict):
        raise ConfigError("backup configuration must be a JSON object")
    expected = (
        {"version", "enabled", "interval_minutes", "keep_history"}
        if require_version
        else {"enabled", "interval_minutes", "keep_history"}
    )
    if set(value) != expected:
        raise ConfigError("backup configuration contains missing or unsupported fields")
    enabled = value.get("enabled")
    interval = value.get("interval_minutes")
    keep_history = value.get("keep_history")
    if not isinstance(enabled, bool):
        raise ConfigError("enabled must be true or false")
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise ConfigError("interval_minutes must be an integer")
    if not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
        raise ConfigError(
            f"interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}"
        )
    if not isinstance(keep_history, bool):
        raise ConfigError("keep_history must be true or false")
    if require_version and value.get("version") != 2:
        raise ConfigError("unsupported backup configuration version")
    return {
        "version": 2,
        "enabled": enabled,
        "interval_minutes": interval,
        "keep_history": keep_history,
    }


def migrate_config(value: object) -> dict:
    if isinstance(value, dict) and set(value) == {"version", "enabled", "interval_hours"}:
        enabled = value.get("enabled")
        interval_hours = value.get("interval_hours")
        if (
            value.get("version") == 1
            and isinstance(enabled, bool)
            and isinstance(interval_hours, int)
            and not isinstance(interval_hours, bool)
            and 1 <= interval_hours <= 720
        ):
            return {
                "version": 2,
                "enabled": enabled,
                "interval_minutes": interval_hours * 60,
                "keep_history": False,
            }
    return validate_config(value)


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("backup configuration is unreadable") from exc
    return migrate_config(value)


def write_config(value: dict, path: Path | None = None) -> None:
    path = path or CONFIG_FILE
    validated = validate_config(value)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(validated, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def systemctl(*arguments: str) -> None:
    result = subprocess.run(
        [SYSTEMCTL, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "systemctl failed"
        raise RuntimeError(message)


def apply_runtime(value: dict, *, start_due_check: bool) -> None:
    if value["enabled"]:
        systemctl("enable", "--now", TIMER_UNIT)
        if start_due_check:
            systemctl("--no-block", "start", SERVICE_UNIT)
    else:
        systemctl("disable", "--now", TIMER_UNIT)


def update_config(request: dict) -> dict:
    replacement = validate_config(request, require_version=False)
    previous = load_config()
    write_config(replacement)
    try:
        apply_runtime(replacement, start_due_check=True)
    except Exception:
        write_config(previous)
        try:
            apply_runtime(previous, start_due_check=False)
        except Exception:
            pass
        raise
    return replacement


def update_on_host(request: dict) -> dict:
    validated = validate_config(request, require_version=False)
    helper = os.path.realpath(sys.argv[0])
    enabled = "true" if validated["enabled"] else "false"
    try:
        result = subprocess.run(
            [
                SYSTEMD_RUN, "--wait", "--pipe", "--collect", "--quiet",
                "--unit=pcs-backup-config-apply", "--service-type=exec",
                "/usr/bin/env", f"{HOST_MARKER}=1",
                helper, "apply-host", enabled, str(validated["interval_minutes"]),
                "true" if validated["keep_history"] else "false",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("host backup configuration service was unavailable") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "host backup configuration service failed")
    try:
        return validated_backup_response(json.loads(result.stdout))
    except (json.JSONDecodeError, ConfigError) as exc:
        raise RuntimeError("host backup configuration service returned an invalid response") from exc


def validated_backup_response(value: object) -> dict:
    return validate_config(value)


def read_stdin_json() -> dict:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ConfigError("backup configuration request is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("backup configuration request is invalid JSON") from exc
    return value


def require_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("this helper must run as root")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"show", "initialize", "set-from-stdin", "apply-host"}:
        print(f"Usage: {sys.argv[0]} show|initialize|set-from-stdin", file=sys.stderr)
        return 2
    try:
        command = sys.argv[1]
        if command == "show":
            if len(sys.argv) != 2:
                raise ConfigError("show does not accept arguments")
            value = load_config()
        elif command == "initialize":
            if len(sys.argv) != 2:
                raise ConfigError("initialize does not accept arguments")
            require_root()
            value = load_config()
            write_config(value)
            apply_runtime(value, start_due_check=True)
        elif command == "set-from-stdin":
            if len(sys.argv) != 2:
                raise ConfigError("set-from-stdin does not accept arguments")
            require_root()
            value = update_on_host(read_stdin_json())
        else:
            if len(sys.argv) != 5 or os.environ.get(HOST_MARKER) != "1":
                raise PermissionError("apply-host is restricted to the transient host service")
            require_root()
            if sys.argv[2] not in {"true", "false"}:
                raise ConfigError("invalid enabled value")
            try:
                interval = int(sys.argv[3])
            except ValueError as exc:
                raise ConfigError("invalid interval value") from exc
            if sys.argv[4] not in {"true", "false"}:
                raise ConfigError("invalid keep_history value")
            value = update_config({
                "enabled": sys.argv[2] == "true",
                "interval_minutes": interval,
                "keep_history": sys.argv[4] == "true",
            })
        print(json.dumps(value, separators=(",", ":"), sort_keys=True))
        return 0
    except (ConfigError, OSError, PermissionError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
