import csv
import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "pcs_aprs_telemetry.py"
SPEC = importlib.util.spec_from_file_location("pcs_aprs_telemetry", MODULE_PATH)
telemetry = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telemetry)


FIELDS = [
    "chan", "utime", "isotime", "source", "heard", "level", "error", "dti",
    "name", "symbol", "latitude", "longitude", "speed", "course", "altitude",
    "frequency", "offset", "tone", "system", "status", "telemetry", "comment",
]


class AprsTelemetryTests(unittest.TestCase):
    def test_counts_recent_rf_packets_and_excludes_local_tracker_transmit(self):
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "2033-05-18.log"
            with log_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow({"chan": "0", "utime": now - 60, "source": "W8AAA-1"})
                writer.writerow({"chan": "0", "utime": now - 7200, "source": "W8BBB-2"})
                writer.writerow({"chan": "999", "utime": now - 30, "source": "W8IJC-2"})
                writer.writerow({"chan": "0", "utime": now - 90000, "source": "OLD-1"})

            result = telemetry.collect(Path(temp_dir), now=now)

        self.assertTrue(result["available"])
        self.assertEqual(result["packets_1h"], 1)
        self.assertEqual(result["packets_24h"], 2)
        self.assertEqual(result["unique_stations_24h"], 2)
        self.assertEqual(result["last_station"], "W8AAA-1")

    def test_reads_rotated_gzip_logs_and_handles_missing_directory(self):
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "2033-05-17.log.gz"
            with gzip.open(log_path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow({"chan": "0", "utime": now - 600, "source": "W8ZIP-1"})
            result = telemetry.collect(Path(temp_dir), now=now)

        self.assertEqual(result["packets_24h"], 1)
        self.assertEqual(result["last_station"], "W8ZIP-1")
        self.assertFalse(telemetry.collect(Path(temp_dir) / "missing", now=now)["available"])


if __name__ == "__main__":
    unittest.main()
