#!/usr/bin/env python3

"""Run the existing PCS mirror action only when the configured interval is due."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


CONFIG_FILE = Path(os.environ.get("PCS_BACKUP_CONFIG_FILE", "/etc/pcs-backup/config.json"))
LAST_SYNC_FILE = Path(os.environ.get("PCS_BACKUP_LAST_SYNC_FILE", "/srv/pcs-share-backup/LAST_SYNC.txt"))
DISPATCHER = os.environ.get("PCS_WEB_ACTION", "/usr/local/sbin/pcs-web-action")


def load_config() -> dict:
    value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "enabled", "interval_minutes", "keep_history"}
        or value.get("version") != 2
        or not isinstance(value.get("enabled"), bool)
        or isinstance(value.get("interval_minutes"), bool)
        or not isinstance(value.get("interval_minutes"), int)
        or not 1 <= value["interval_minutes"] <= 43_200
        or not isinstance(value.get("keep_history"), bool)
    ):
        raise ValueError("invalid PCS backup configuration")
    return value


def backup_due(interval_minutes: int) -> bool:
    try:
        age = max(0.0, time.time() - LAST_SYNC_FILE.stat().st_mtime)
    except FileNotFoundError:
        return True
    return age >= interval_minutes * 60


def main() -> int:
    try:
        config = load_config()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not config["enabled"]:
        print("Automatic PCS backups are disabled.")
        return 0
    if not backup_due(config["interval_minutes"]):
        print(f"PCS backup is not due (interval {config['interval_minutes']} minutes).")
        return 0

    try:
        result = subprocess.run([DISPATCHER, "sync-backup"], timeout=1800)
    except subprocess.TimeoutExpired:
        print("ERROR: PCS automatic backup exceeded its 30-minute limit.", file=sys.stderr)
        return 124
    if result.returncode == 75:
        print("Another PCS backup is already running; this timer check is complete.")
        return 0
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
