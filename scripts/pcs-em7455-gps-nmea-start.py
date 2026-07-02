#!/usr/bin/env python3

import os
import select
import subprocess
import sys
import termios
import time

NMEA_PORT = "/dev/ttyUSB1"
NMEA_BAUD = 115200
MODEM_WAIT_SECONDS = 90
NMEA_WAIT_SECONDS = 20


def log(message: str) -> None:
    print(message, flush=True)


def run(command: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        return result.returncode, output
    except Exception as exc:
        return 1, str(exc)


def wait_for_modem() -> bool:
    deadline = time.time() + MODEM_WAIT_SECONDS

    while time.time() < deadline:
        rc, output = run(["mmcli", "-L"])

        if rc == 0 and "/Modem/" in output:
            log("ModemManager modem detected.")
            return True

        log("Waiting for ModemManager modem...")
        time.sleep(5)

    log("ERROR: ModemManager modem was not detected in time.")
    return False


def enable_modem_gps() -> None:
    commands = [
        ["mmcli", "-m", "0", "--location-enable-gps-raw"],
        ["mmcli", "-m", "0", "--location-enable-gps-nmea"],
        ["mmcli", "-m", "0", "--location-set-gps-refresh-rate=5"],
    ]

    for command in commands:
        rc, output = run(command)

        if rc == 0:
            log(f"OK: {' '.join(command)}")
        else:
            log(f"WARN: {' '.join(command)}")
            if output.strip():
                log(output.strip())


def configure_serial(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def open_nmea_port(read_write: bool = True) -> int:
    flags = os.O_NOCTTY | os.O_NONBLOCK
    flags |= os.O_RDWR if read_write else os.O_RDONLY

    fd = os.open(NMEA_PORT, flags)
    configure_serial(fd)
    return fd


def send_gps_start() -> bool:
    if not os.path.exists(NMEA_PORT):
        log(f"ERROR: NMEA port missing: {NMEA_PORT}")
        return False

    try:
        fd = open_nmea_port(read_write=True)
    except Exception as exc:
        log(f"ERROR: Could not open {NMEA_PORT}: {exc}")
        return False

    try:
        os.write(fd, b"$GPS_START\r\n")
        log(f"Sent GPS_START to {NMEA_PORT} at {NMEA_BAUD}.")
        time.sleep(1)
        return True
    finally:
        os.close(fd)


def nmea_seen() -> bool:
    try:
        fd = open_nmea_port(read_write=False)
    except Exception as exc:
        log(f"WARN: Could not read {NMEA_PORT}: {exc}")
        return False

    buffer = b""
    deadline = time.time() + NMEA_WAIT_SECONDS

    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)

            if fd not in ready:
                continue

            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue

            if not chunk:
                continue

            buffer += chunk

            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                line = raw_line.decode("ascii", errors="ignore").strip()

                if line.startswith("$G"):
                    log("NMEA output detected on /dev/ttyUSB1. Location hidden.")
                    return True
    finally:
        os.close(fd)

    log("WARN: No NMEA output detected during quick check.")
    return False


def main() -> int:
    log("PCS EM7455 GPS NMEA starter beginning.")

    if not wait_for_modem():
        return 1

    enable_modem_gps()

    if not send_gps_start():
        return 1

    nmea_seen()

    log("PCS EM7455 GPS NMEA starter complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
