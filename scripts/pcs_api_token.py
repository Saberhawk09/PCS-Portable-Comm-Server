#!/usr/bin/env python3

"""Issue, list, and revoke hashed PCS API device tokens."""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from datetime import datetime, timezone


DEFAULT_TOKEN_FILE = "/etc/pcs-stats-api/api-read-tokens.json"
DEFAULT_ADMIN_FILE = "/etc/pcs-control-panel/admin.json"
TOKEN_ID_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}")
MAX_PAIRING_INPUT_BYTES = 4096
MAX_ADMIN_PASSWORD_LENGTH = 1024


class DuplicateTokenError(ValueError):
    pass


@contextlib.contextmanager
def token_store_lock(path: str):
    """Serialize token-store mutations across helper processes on Linux."""
    if os.name == "nt":
        yield
        return
    import fcntl
    lock_path = path + ".lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def empty_store() -> dict:
    return {"version": 1, "tokens": []}


def load_store(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as source:
            payload = json.load(source)
    except FileNotFoundError:
        return empty_store()
    if not isinstance(payload, dict):
        raise ValueError("token file has an unsupported format")
    if payload.get("version") != 1 or not isinstance(payload.get("tokens"), list):
        raise ValueError("token file has an unsupported format")
    if not all(isinstance(record, dict) for record in payload["tokens"]):
        raise ValueError("token file has an unsupported record")
    return payload


def write_store(path: str, payload: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o750, exist_ok=True)
    try:
        previous_owner = os.stat(path)
    except OSError:
        previous_owner = None
    descriptor, temporary = tempfile.mkstemp(prefix=".api-tokens-", dir=directory, text=True)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o640)
        if previous_owner is not None and hasattr(os, "chown"):
            os.chown(path, previous_owner.st_uid, previous_owner.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _issue_unlocked(path: str, token_id: str, scopes: list[str] | None = None) -> str:
    if TOKEN_ID_PATTERN.fullmatch(token_id) is None:
        raise ValueError("token id must be 1-64 safe letters, digits, dots, underscores, or hyphens")
    granted_scopes = scopes or ["stats:read"]
    if not granted_scopes or any(scope not in {"stats:read", "admin:actions", "admin:password"} for scope in granted_scopes):
        raise ValueError("unsupported token scope")
    payload = load_store(path)
    if any(record.get("id") == token_id for record in payload["tokens"]):
        raise DuplicateTokenError(f"token id already exists: {token_id}")
    raw_token = "pcs_ro_" + secrets.token_urlsafe(32)
    payload["tokens"].append({
        "id": token_id,
        "token_sha256": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        "scopes": granted_scopes,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })
    write_store(path, payload)
    return raw_token


def issue(path: str, token_id: str, scopes: list[str] | None = None) -> str:
    with token_store_lock(path):
        return _issue_unlocked(path, token_id, scopes=scopes)


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def verify_admin_password(path: str, password: str) -> bool:
    if not isinstance(password, str) or not password or len(password) > MAX_ADMIN_PASSWORD_LENGTH:
        return False
    try:
        with open(path, "r", encoding="utf-8") as source:
            record = json.load(source)
        if record.get("version") != 1 or record.get("algorithm") != "pbkdf2-sha256":
            return False
        iterations = int(record["iterations"])
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = _b64decode(record["salt"])
        expected = _b64decode(record["password_hash"])
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)
    except (OSError, KeyError, TypeError, ValueError):
        return False


def pair_from_stdin(path: str, admin_file: str = DEFAULT_ADMIN_FILE) -> dict:
    raw = sys.stdin.buffer.read(MAX_PAIRING_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_PAIRING_INPUT_BYTES:
        raise ValueError("invalid pairing request")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pairing request") from exc
    if not isinstance(payload, dict) or set(payload) != {"admin_password", "device_id"}:
        raise ValueError("invalid pairing request")
    device_id = payload.get("device_id")
    password = payload.get("admin_password")
    if not isinstance(device_id, str) or TOKEN_ID_PATTERN.fullmatch(device_id) is None:
        raise ValueError("invalid pairing request")
    if not verify_admin_password(admin_file, password):
        raise PermissionError("administrator authentication failed")
    with token_store_lock(path):
        scopes = ["stats:read", "admin:actions", "admin:password"]
        raw_token = _issue_unlocked(path, device_id, scopes=scopes)
    os.chmod(path, 0o640)
    if os.name != "nt" and hasattr(os, "chown") and os.geteuid() == 0:
        import grp
        os.chown(path, 0, grp.getgrnam("pcs-api").gr_gid)
    return {
        "device_id": device_id,
        "token": raw_token,
        "token_type": "Bearer",
        "scopes": scopes,
    }


def revoke(path: str, token_id: str) -> None:
    with token_store_lock(path):
        payload = load_store(path)
        matched = False
        for record in payload["tokens"]:
            if record.get("id") == token_id:
                record["enabled"] = False
                record["revoked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                matched = True
        if not matched:
            raise ValueError(f"unknown token id: {token_id}")
        write_store(path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage read-only PCS Stats API tokens")
    parser.add_argument("--file", default=DEFAULT_TOKEN_FILE, help="hashed token store")
    subparsers = parser.add_subparsers(dest="command", required=True)
    issue_parser = subparsers.add_parser("issue", help="issue a token and print it once")
    issue_parser.add_argument("token_id")
    revoke_parser = subparsers.add_parser("revoke", help="revoke a token by id")
    revoke_parser.add_argument("token_id")
    subparsers.add_parser("list", help="list token ids and enabled state")
    subparsers.add_parser("pair-from-stdin", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args.command == "issue":
            raw_token = issue(args.file, args.token_id)
            print("Store this token in the client now; PCS stores only its SHA-256 digest:")
            print(raw_token)
        elif args.command == "revoke":
            revoke(args.file, args.token_id)
            print(f"Revoked token: {args.token_id}")
        elif args.command == "pair-from-stdin":
            print(json.dumps(pair_from_stdin(args.file), separators=(",", ":")))
        else:
            for record in load_store(args.file)["tokens"]:
                state = "enabled" if record.get("enabled") is True else "revoked"
                scopes = record.get("scopes", ["stats:read"])
                if not isinstance(scopes, list):
                    scopes = ["stats:read"]
                print(f"{record.get('id', 'unnamed')}\t{state}\t{','.join(str(scope) for scope in scopes)}")
    except PermissionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    except DuplicateTokenError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
