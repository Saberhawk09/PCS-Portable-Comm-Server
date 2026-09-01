#!/usr/bin/env python3

"""Root-owned helper for securely setting the PCS web administrator password."""

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys


CREDENTIAL_FILE = "/etc/pcs-control-panel/admin.json"
SAMBA_USERNAME = "pcs-admin"
SMBPASSWD_COMMAND = "/usr/bin/smbpasswd"
SYSTEMD_RUN_COMMAND = "/usr/bin/systemd-run"
PASSWORD_ITERATIONS = 310_000
MINIMUM_PASSWORD_LENGTH = 12
MAX_INPUT_BYTES = 8192
MAXIMUM_PASSWORD_LENGTH = 1024


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def make_password_record(password: str) -> dict:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"Admin password must be at least {MINIMUM_PASSWORD_LENGTH} characters.")
    if len(password) > MAXIMUM_PASSWORD_LENGTH:
        raise ValueError(f"Admin password must be at most {MAXIMUM_PASSWORD_LENGTH} characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return {
        "version": 1,
        "algorithm": "pbkdf2-sha256",
        "iterations": PASSWORD_ITERATIONS,
        "salt": b64encode(salt),
        "password_hash": b64encode(digest),
    }


def verify_password(password: str, record: dict) -> bool:
    try:
        if record.get("version") != 1 or record.get("algorithm") != "pbkdf2-sha256":
            return False
        iterations = int(record["iterations"])
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = b64decode(record["salt"])
        expected = b64decode(record["password_hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def read_record(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as source:
            value = json.load(source)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_record(path: str, password: str) -> None:
    record = make_password_record(password)
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o750, exist_ok=True)
    try:
        previous = os.stat(path)
    except OSError:
        previous = None

    temporary = f"{path}.tmp.{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o640)
        if previous is not None and hasattr(os, "fchown"):
            os.fchown(descriptor, previous.st_uid, previous.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(record, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o640)
        if previous is not None and not hasattr(os, "fchown") and hasattr(os, "chown"):
            os.chown(path, previous.st_uid, previous.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_samba_password(password: str) -> None:
    """Set the dedicated PCS-Backup Samba password without exposing it in argv."""
    if any(character in password for character in ("\x00", "\r", "\n")):
        raise ValueError("PCS administrator passwords cannot contain NUL or line breaks.")
    try:
        result = subprocess.run(
            [
                SYSTEMD_RUN_COMMAND,
                "--quiet",
                "--pipe",
                "--wait",
                "--collect",
                "--service-type=exec",
                f"--unit=pcs-admin-samba-password-{os.getpid()}",
                "--property=NoNewPrivileges=yes",
                "--property=PrivateTmp=yes",
                "--property=ProtectHome=yes",
                "--property=ProtectSystem=full",
                SMBPASSWD_COMMAND,
                "-s",
                "-a",
                SAMBA_USERNAME,
            ],
            input=f"{password}\n{password}\n",
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("PCS-Backup credential update was unavailable.") from exc
    if result.returncode != 0:
        raise RuntimeError("PCS-Backup credential update failed.")


def restore_record(path: str, previous: bytes | None) -> None:
    if previous is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return

    try:
        previous_owner = os.stat(path)
    except OSError:
        previous_owner = None
    temporary = f"{path}.restore.{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o640)
        if previous_owner is not None and hasattr(os, "fchown"):
            os.fchown(descriptor, previous_owner.st_uid, previous_owner.st_gid)
        with os.fdopen(descriptor, "wb") as output:
            output.write(previous)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o640)
        if previous_owner is not None and not hasattr(os, "fchown") and hasattr(os, "chown"):
            os.chown(path, previous_owner.st_uid, previous_owner.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_web_and_samba_password(path: str, password: str, rollback_password: str | None = None) -> None:
    """Update both credentials or restore the previous web credential on failure."""
    try:
        with open(path, "rb") as source:
            previous = source.read()
    except FileNotFoundError:
        previous = None

    write_record(path, password)
    try:
        set_samba_password(password)
    except (OSError, RuntimeError, ValueError):
        restore_record(path, previous)
        if rollback_password is not None:
            try:
                set_samba_password(rollback_password)
            except (OSError, RuntimeError, ValueError):
                pass
        raise


def set_interactively() -> int:
    print("Set the password used by the PCS Admin Login panel.")
    print(f"The password must be at least {MINIMUM_PASSWORD_LENGTH} characters.")
    password = getpass.getpass("New admin password: ")
    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        print("ERROR: passwords did not match.", file=sys.stderr)
        return 1
    try:
        update_web_and_samba_password(CREDENTIAL_FILE, password)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("PCS administrator and PCS-Backup passwords updated.")
    return 0


def sync_samba_interactively() -> int:
    record = read_record(CREDENTIAL_FILE)
    if record is None:
        print("ERROR: no PCS administrator password is configured.", file=sys.stderr)
        return 1
    password = getpass.getpass("Current PCS admin password: ")
    if len(password) > MAXIMUM_PASSWORD_LENGTH or not verify_password(password, record):
        print("ERROR: PCS administrator password was incorrect.", file=sys.stderr)
        return 3
    try:
        set_samba_password(password)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("PCS-Backup now uses the PCS administrator password.")
    return 0


def change_from_stdin() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        print("ERROR: password update request was too large.", file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        print("ERROR: invalid password update request.", file=sys.stderr)
        return 2
    if not isinstance(payload, dict) or set(payload) != {"current_password", "new_password"}:
        print("ERROR: invalid password update fields.", file=sys.stderr)
        return 2
    current_password = payload.get("current_password")
    new_password = payload.get("new_password")
    if not isinstance(current_password, str) or not isinstance(new_password, str):
        print("ERROR: invalid password update values.", file=sys.stderr)
        return 2
    if len(current_password) > MAXIMUM_PASSWORD_LENGTH or len(new_password) > MAXIMUM_PASSWORD_LENGTH:
        print("ERROR: password update value was too long.", file=sys.stderr)
        return 2

    record = read_record(CREDENTIAL_FILE)
    if record is None or not verify_password(current_password, record):
        print("ERROR: current administrator password was incorrect.", file=sys.stderr)
        return 3
    if hmac.compare_digest(current_password, new_password):
        print("ERROR: new password must be different from the current password.", file=sys.stderr)
        return 4
    try:
        update_web_and_samba_password(CREDENTIAL_FILE, new_password, current_password)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    print("PCS administrator and PCS-Backup passwords updated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the PCS web administrator password")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--set-password", action="store_true", help="set or reset the password interactively")
    mode.add_argument("--sync-samba-password", action="store_true", help="synchronize PCS-Backup interactively")
    mode.add_argument("--change-from-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.set_password:
        return set_interactively()
    if args.sync_samba_password:
        return sync_samba_interactively()
    return change_from_stdin()


if __name__ == "__main__":
    raise SystemExit(main())
