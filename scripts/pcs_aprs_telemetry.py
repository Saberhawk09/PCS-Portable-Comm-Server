#!/usr/bin/env python3

import argparse
import csv
import gzip
import json
import time
from pathlib import Path


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def collect(log_dir: Path, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    cutoff_1h = now - 3600
    cutoff_24h = now - 86400
    packets_1h = 0
    packets_24h = 0
    unique_stations: set[str] = set()
    last_packet_time = 0.0
    last_station = ""
    files_read = 0

    if not log_dir.is_dir():
        return {
            "available": False,
            "packets_1h": 0,
            "packets_24h": 0,
            "unique_stations_24h": 0,
            "last_packet_at": "",
            "last_station": "",
            "files_read": 0,
        }

    candidates = sorted((*log_dir.glob("*.log"), *log_dir.glob("*.log.gz")))
    for path in candidates:
        try:
            with open_text(path) as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or "utime" not in reader.fieldnames:
                    continue
                files_read += 1
                for row in reader:
                    try:
                        packet_time = float(row.get("utime", ""))
                    except (TypeError, ValueError):
                        continue
                    # Dire Wolf uses synthetic channel 999 for its own tracker
                    # transmissions. Dashboard receive metrics count RF input only.
                    if str(row.get("chan", "")).strip() == "999":
                        continue
                    source = str(row.get("source", "")).strip()
                    if packet_time >= cutoff_24h:
                        packets_24h += 1
                        if source:
                            unique_stations.add(source)
                    if packet_time >= cutoff_1h:
                        packets_1h += 1
                    if packet_time > last_packet_time:
                        last_packet_time = packet_time
                        last_station = source
        except (OSError, csv.Error):
            continue

    last_packet_at = ""
    if last_packet_time:
        last_packet_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_packet_time))

    return {
        "available": files_read > 0,
        "packets_1h": packets_1h,
        "packets_24h": packets_24h,
        "unique_stations_24h": len(unique_stations),
        "last_packet_at": last_packet_at,
        "last_station": last_station,
        "files_read": files_read,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Dire Wolf CSV logs for PCS")
    parser.add_argument("--log-dir", default="/var/log/direwolf")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    data = collect(Path(args.log_dir))
    if args.json:
        print(json.dumps(data, separators=(",", ":")))
    else:
        print(f"Packets last hour: {data['packets_1h']}")
        print(f"Packets last 24h:  {data['packets_24h']}")
        print(f"Stations last 24h: {data['unique_stations_24h']}")
        print(f"Last RF packet:     {data['last_packet_at'] or 'not available'}")
        print(f"Last station:       {data['last_station'] or 'not available'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
