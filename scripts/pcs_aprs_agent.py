#!/usr/bin/env python3
"""PCS APRS status and mailbox agent using Dire Wolf's KISS ICHANNEL."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import logging
import os
import re
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


LOG = logging.getLogger("pcs-aprs-agent")

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
KISS_DATA = 0x00
AX25_UI = 0x03
AX25_NO_LAYER_3 = 0xF0
MAX_KISS_FRAME = 4096
MAX_APRS_MESSAGE_TEXT = 67
DEFAULT_CONFIG = "/etc/pcs/aprs-agent.conf"
DEFAULT_STATE_DB = "/var/lib/pcs-aprs-agent/state.sqlite3"
DEFAULT_STATUS_FILE = "/run/pcs-aprs-agent/status.json"
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,6}(?:-(?:[1-9]|1[0-5]))?$")
MESSAGE_ID_RE = re.compile(r"\{([A-Za-z0-9]{1,5})(?:\}([A-Za-z0-9]{1,5}))?$")
RECEIPT_RE = re.compile(r"^(ack|rej)([A-Za-z0-9]{1,5})$", re.IGNORECASE)
COMMANDS = ("PING", "STATUS", "POWER", "LTE", "GPS", "TEMP", "NET", "UPTIME", "HELP", "MSG")
COMMAND_ALIASES = {"S": "STATUS", "H": "HELP"}
MAX_MAILBOX_TEXT = MAX_APRS_MESSAGE_TEXT - len("MSG ")
DEFAULT_OUTBOUND_RETRY_SECONDS = (30, 60, 120, 240)
SendAx25 = Callable[[int, bytes], None]


class ConfigError(ValueError):
    """Raised when the APRS agent configuration is unsafe or invalid."""


def parse_retry_seconds(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if any(not item.strip() for item in parts):
        raise ConfigError("outbound_retry_seconds contains an empty delay")
    try:
        retry_seconds = tuple(int(item.strip()) for item in parts)
    except ValueError as exc:
        raise ConfigError("outbound_retry_seconds must be a comma-separated list of integers") from exc
    if not retry_seconds:
        raise ConfigError("outbound_retry_seconds must contain at least one delay")
    return retry_seconds


def parse_boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "yes", "true", "on"}:
        return True
    if normalized in {"0", "no", "false", "off"}:
        return False
    raise ConfigError(f"{name} must be yes or no")


@dataclass(frozen=True)
class AgentConfig:
    callsign: str = "W8IJC-10"
    tocall: str = "APZPCS"
    kiss_host: str = "127.0.0.1"
    kiss_port: int = 8001
    kiss_channel: int = 8
    rf_enabled: bool = False
    rf_channel: int = 0
    state_db: str = DEFAULT_STATE_DB
    status_file: str = DEFAULT_STATUS_FILE
    dedupe_ttl_seconds: int = 86400
    mailbox_limit: int = 100
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    command_timeout_seconds: float = 3.0
    sender_rate_per_minute: int = 12
    global_rate_per_minute: int = 60
    reconnect_max_seconds: int = 60
    outbound_retry_seconds: tuple[int, ...] = DEFAULT_OUTBOUND_RETRY_SECONDS
    outbound_max_pending: int = 100
    outbound_retention_seconds: int = 604800

    @classmethod
    def load(cls, path: str | Path) -> "AgentConfig":
        parser = configparser.ConfigParser(interpolation=None)
        if not parser.read(path, encoding="utf-8"):
            raise ConfigError(f"configuration file is unavailable: {path}")
        section = parser["agent"] if parser.has_section("agent") else {}
        try:
            config = cls(
                callsign=str(section.get("callsign", cls.callsign)).strip().upper(),
                tocall=str(section.get("tocall", cls.tocall)).strip().upper(),
                kiss_host=str(section.get("kiss_host", cls.kiss_host)).strip(),
                kiss_port=int(section.get("kiss_port", cls.kiss_port)),
                kiss_channel=int(section.get("kiss_channel", cls.kiss_channel)),
                rf_enabled=parse_boolean(
                    str(section.get("rf_enabled", "no")),
                    "rf_enabled",
                ),
                rf_channel=int(section.get("rf_channel", cls.rf_channel)),
                state_db=str(section.get("state_db", cls.state_db)).strip(),
                status_file=str(section.get("status_file", cls.status_file)).strip(),
                dedupe_ttl_seconds=int(section.get("dedupe_ttl_seconds", cls.dedupe_ttl_seconds)),
                mailbox_limit=int(section.get("mailbox_limit", cls.mailbox_limit)),
                gpsd_host=str(section.get("gpsd_host", cls.gpsd_host)).strip(),
                gpsd_port=int(section.get("gpsd_port", cls.gpsd_port)),
                command_timeout_seconds=float(section.get("command_timeout_seconds", cls.command_timeout_seconds)),
                sender_rate_per_minute=int(section.get("sender_rate_per_minute", cls.sender_rate_per_minute)),
                global_rate_per_minute=int(section.get("global_rate_per_minute", cls.global_rate_per_minute)),
                reconnect_max_seconds=int(section.get("reconnect_max_seconds", cls.reconnect_max_seconds)),
                outbound_retry_seconds=parse_retry_seconds(
                    str(
                        section.get(
                            "outbound_retry_seconds",
                            ",".join(str(item) for item in DEFAULT_OUTBOUND_RETRY_SECONDS),
                        )
                    )
                ),
                outbound_max_pending=int(section.get("outbound_max_pending", cls.outbound_max_pending)),
                outbound_retention_seconds=int(
                    section.get("outbound_retention_seconds", cls.outbound_retention_seconds)
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"configuration contains an invalid number: {exc}") from exc
        config.validate()
        return config

    def validate(self) -> None:
        if not CALLSIGN_RE.fullmatch(self.callsign):
            raise ConfigError(f"invalid APRS callsign: {self.callsign!r}")
        if not re.fullmatch(r"[A-Z0-9]{1,6}", self.tocall):
            raise ConfigError(f"invalid APRS destination/tocall: {self.tocall!r}")
        if self.kiss_host.lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("kiss_host must be loopback; the agent may only use the local Dire Wolf instance")
        if self.gpsd_host.lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("gpsd_host must be loopback; GPS status may only use the local receiver")
        state_path = PurePosixPath(self.state_db)
        if not state_path.is_absolute() or state_path.parent != PurePosixPath("/var/lib/pcs-aprs-agent"):
            raise ConfigError("state_db must be a file directly under /var/lib/pcs-aprs-agent")
        status_path = PurePosixPath(self.status_file)
        if not status_path.is_absolute() or status_path.parent != PurePosixPath("/run/pcs-aprs-agent"):
            raise ConfigError("status_file must be a file directly under /run/pcs-aprs-agent")
        if not 1 <= self.kiss_port <= 65535 or not 1 <= self.gpsd_port <= 65535:
            raise ConfigError("TCP ports must be between 1 and 65535")
        if not 1 <= self.kiss_channel <= 15:
            raise ConfigError("kiss_channel must be an Internet-only KISS channel from 1 through 15")
        if self.rf_channel != 0:
            raise ConfigError("rf_channel must be Dire Wolf physical radio channel 0")
        if not 60 <= self.dedupe_ttl_seconds <= 2_592_000:
            raise ConfigError("dedupe_ttl_seconds must be between 60 and 2592000")
        if not 1 <= self.mailbox_limit <= 1000:
            raise ConfigError("mailbox_limit must be between 1 and 1000")
        if not 0.2 <= self.command_timeout_seconds <= 15:
            raise ConfigError("command_timeout_seconds must be between 0.2 and 15")
        if not 1 <= self.sender_rate_per_minute <= 120:
            raise ConfigError("sender_rate_per_minute must be between 1 and 120")
        if not self.sender_rate_per_minute <= self.global_rate_per_minute <= 600:
            raise ConfigError("global_rate_per_minute must cover one sender and be no greater than 600")
        if not 1 <= self.reconnect_max_seconds <= 300:
            raise ConfigError("reconnect_max_seconds must be between 1 and 300")
        if len(self.outbound_retry_seconds) > 10 or any(
            delay < 5 or delay > 3600 for delay in self.outbound_retry_seconds
        ):
            raise ConfigError("outbound_retry_seconds must contain 1 through 10 delays from 5 to 3600 seconds")
        if any(later < earlier for earlier, later in zip(self.outbound_retry_seconds, self.outbound_retry_seconds[1:])):
            raise ConfigError("outbound_retry_seconds must be nondecreasing")
        if not 1 <= self.outbound_max_pending <= 1000:
            raise ConfigError("outbound_max_pending must be between 1 and 1000")
        if not 3600 <= self.outbound_retention_seconds <= 2_592_000:
            raise ConfigError("outbound_retention_seconds must be between 3600 and 2592000")

    @property
    def receive_channels(self) -> tuple[int, ...]:
        return (self.kiss_channel, self.rf_channel) if self.rf_enabled else (self.kiss_channel,)


class KissDecoder:
    """Incrementally decode escaped KISS frames from a TCP stream."""

    def __init__(self, maximum: int = MAX_KISS_FRAME) -> None:
        self.maximum = maximum
        self._frame = bytearray()
        self._escaped = False
        self._discarding = False

    def feed(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for value in data:
            if value == FEND:
                if self._frame and not self._discarding and not self._escaped:
                    frames.append(bytes(self._frame))
                self._frame.clear()
                self._escaped = False
                self._discarding = False
                continue
            if self._discarding:
                continue
            if self._escaped:
                if value == TFEND:
                    value = FEND
                elif value == TFESC:
                    value = FESC
                else:
                    self._frame.clear()
                    self._discarding = True
                    self._escaped = False
                    continue
                self._escaped = False
            elif value == FESC:
                self._escaped = True
                continue
            self._frame.append(value)
            if len(self._frame) > self.maximum:
                self._frame.clear()
                self._discarding = True
        return frames


def encode_kiss(channel: int, payload: bytes) -> bytes:
    if not 0 <= channel <= 15:
        raise ValueError("KISS channel must be between 0 and 15")
    raw = bytes([(channel << 4) | KISS_DATA]) + payload
    escaped = raw.replace(bytes([FESC]), bytes([FESC, TFESC]))
    escaped = escaped.replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + escaped + bytes([FEND])


def split_callsign(value: str) -> tuple[str, int]:
    normalized = value.strip().upper()
    if not CALLSIGN_RE.fullmatch(normalized):
        raise ValueError(f"invalid AX.25 callsign: {value!r}")
    if "-" not in normalized:
        return normalized, 0
    call, ssid_text = normalized.rsplit("-", 1)
    return call, int(ssid_text)


def encode_ax25_address(value: str, *, last: bool) -> bytes:
    call, ssid = split_callsign(value)
    address = bytearray((ord(char) << 1) for char in call.ljust(6))
    address.append(0x60 | (ssid << 1) | int(last))
    return bytes(address)


def decode_ax25_address(raw: bytes) -> tuple[str, bool]:
    if len(raw) != 7:
        raise ValueError("AX.25 address must contain seven bytes")
    if any(value & 0x01 for value in raw[:6]):
        raise ValueError("AX.25 address contains invalid shifted characters")
    call = "".join(chr((value >> 1) & 0x7F) for value in raw[:6]).rstrip()
    if not re.fullmatch(r"[A-Z0-9]{1,6}", call):
        raise ValueError("AX.25 address contains an invalid callsign")
    ssid = (raw[6] >> 1) & 0x0F
    return f"{call}-{ssid}" if ssid else call, bool(raw[6] & 0x01)


@dataclass(frozen=True)
class Ax25Frame:
    destination: str
    source: str
    information: bytes


def decode_ax25_ui(raw: bytes) -> Ax25Frame:
    if len(raw) < 16:
        raise ValueError("AX.25 frame is too short")
    addresses: list[str] = []
    offset = 0
    while True:
        if offset + 7 > len(raw) or len(addresses) >= 10:
            raise ValueError("AX.25 address list is incomplete or excessive")
        address, last = decode_ax25_address(raw[offset : offset + 7])
        addresses.append(address)
        offset += 7
        if last:
            break
    if len(addresses) < 2 or offset + 2 > len(raw):
        raise ValueError("AX.25 frame has no source/destination pair or control fields")
    if raw[offset] != AX25_UI or raw[offset + 1] != AX25_NO_LAYER_3:
        raise ValueError("only AX.25 UI frames with PID F0 are accepted")
    return Ax25Frame(destination=addresses[0], source=addresses[1], information=raw[offset + 2 :])


def unwrap_direwolf_ichannel(frame: Ax25Frame) -> Ax25Frame:
    """Recover the original TNC2 packet from Dire Wolf's ICHANNEL wrapper."""

    if not frame.information.startswith(b"}"):
        return frame
    try:
        packet = frame.information[1:].decode("ascii").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ValueError("Dire Wolf ICHANNEL wrapper is not ASCII") from exc
    header, separator, information = packet.partition(":")
    source, address_separator, route = header.partition(">")
    destination = route.split(",", 1)[0]
    if not separator or not address_separator or not information or not destination:
        raise ValueError("Dire Wolf ICHANNEL wrapper has invalid TNC2 framing")
    if "\r" in packet or "\n" in packet or information.startswith("}"):
        raise ValueError("Dire Wolf ICHANNEL wrapper contains invalid nested data")
    source = source.strip().upper()
    destination = destination.strip().upper()
    split_callsign(source)
    split_callsign(destination)
    return Ax25Frame(
        destination=destination,
        source=source,
        information=information.encode("ascii"),
    )


def encode_ax25_ui(source: str, destination: str, information: bytes) -> bytes:
    if len(information) > 256:
        raise ValueError("AX.25 information field is too large")
    return (
        encode_ax25_address(destination, last=False)
        + encode_ax25_address(source, last=True)
        + bytes([AX25_UI, AX25_NO_LAYER_3])
        + information
    )


@dataclass(frozen=True)
class AprsMessage:
    sender: str
    addressee: str
    body: str
    message_id: str | None
    reply_ack_id: str | None


@dataclass(frozen=True)
class AprsReceipt:
    disposition: str
    message_id: str


@dataclass(frozen=True)
class OutboundMessage:
    message_id: str
    recipient: str
    body: str
    attempts: int
    kiss_channel: int


def parse_aprs_message(frame: Ax25Frame) -> AprsMessage | None:
    try:
        info = frame.information.decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(info) < 11 or info[0] != ":" or info[10] != ":":
        return None
    addressee_field = info[1:10]
    addressee = addressee_field.rstrip().upper()
    if not addressee or not CALLSIGN_RE.fullmatch(addressee):
        return None
    wire_body = info[11:]
    match = MESSAGE_ID_RE.search(wire_body)
    message_id = match.group(1) if match else None
    reply_ack_id = match.group(2) if match else None
    body = wire_body[: match.start()] if match else wire_body
    return AprsMessage(
        sender=frame.source.upper(),
        addressee=addressee,
        body=body,
        message_id=message_id,
        reply_ack_id=reply_ack_id,
    )


def parse_aprs_receipt(body: str) -> AprsReceipt | None:
    match = RECEIPT_RE.fullmatch(body)
    if match is None:
        return None
    return AprsReceipt(
        disposition="acked" if match.group(1).lower() == "ack" else "rejected",
        message_id=match.group(2),
    )


def aprs_ack_information(recipient: str, message_id: str) -> bytes:
    split_callsign(recipient)
    if not re.fullmatch(r"[A-Za-z0-9]{1,5}", message_id):
        raise ValueError("invalid APRS message ID")
    return f":{recipient:<9}:ack{message_id}".encode("ascii")


def normalize_aprs_reply_body(body: str, message_id: str) -> str:
    body = " ".join(body.strip().split())
    if not body:
        raise ValueError("outbound APRS message body is empty")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in body):
        raise ValueError("outbound APRS message body must contain printable ASCII")
    suffix = f"{{{message_id}"
    maximum_body = MAX_APRS_MESSAGE_TEXT - len(suffix)
    if maximum_body < 1:
        raise ValueError("outbound APRS message ID is too long")
    return body[:maximum_body].rstrip()


def aprs_reply_information(recipient: str, body: str, message_id: str) -> bytes:
    split_callsign(recipient)
    if not re.fullmatch(r"[A-Za-z0-9]{1,5}", message_id):
        raise ValueError("invalid APRS message ID")
    body = normalize_aprs_reply_body(body, message_id)
    suffix = f"{{{message_id}"
    return f":{recipient:<9}:{body}{suffix}".encode("ascii", errors="replace")


def mailbox_summary_from_connection(
    connection: sqlite3.Connection,
    *,
    include_messages: bool = False,
    limit: int = 10,
) -> dict[str, object]:
    total, unread, last_received = connection.execute(
        "SELECT COUNT(*), SUM(CASE WHEN read_at IS NULL THEN 1 ELSE 0 END), MAX(received_at) "
        "FROM mailbox_messages"
    ).fetchone()
    result: dict[str, object] = {
        "total": int(total or 0),
        "unread": int(unread or 0),
        "last_received_at": float(last_received) if last_received is not None else None,
    }
    if include_messages:
        rows = connection.execute(
            "SELECT id, sender, message_id, body, received_at, read_at "
            "FROM mailbox_messages ORDER BY id DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        ).fetchall()
        result["messages"] = [
            {
                "id": int(row[0]),
                "sender": str(row[1]),
                "message_id": str(row[2]),
                "body": str(row[3]),
                "received_at": float(row[4]),
                "unread": row[5] is None,
            }
            for row in rows
        ]
    return result


def read_mailbox_snapshot(
    path: str | Path,
    *,
    include_messages: bool = False,
    limit: int = 10,
) -> dict[str, object]:
    database = Path(path)
    if not database.is_file():
        result: dict[str, object] = {"total": 0, "unread": 0, "last_received_at": None}
        if include_messages:
            result["messages"] = []
        return result
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    try:
        return mailbox_summary_from_connection(
            connection,
            include_messages=include_messages,
            limit=limit,
        )
    finally:
        connection.close()


def validate_state_database(path: str | Path, maximum_pending: int) -> None:
    database = Path(path)
    if not database.is_file():
        raise ValueError("state database is unavailable")
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    try:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("state database integrity check failed")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required = {
            "received_messages",
            "state_values",
            "mailbox_messages",
            "outbound_messages",
        }
        if not required.issubset(tables):
            raise ValueError("state database schema is incomplete")
        invalid = connection.execute(
            "SELECT COUNT(*) FROM outbound_messages WHERE "
            "state NOT IN ('pending', 'acked', 'rejected', 'failed') OR attempts < 0 OR "
            "kiss_channel NOT BETWEEN 0 AND 15 OR "
            "(state = 'pending' AND next_attempt_at IS NULL) OR "
            "(state != 'pending' AND next_attempt_at IS NOT NULL)"
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM outbound_messages WHERE state = 'pending'"
        ).fetchone()[0]
        if int(invalid) != 0 or int(pending) > maximum_pending:
            raise ValueError("outbound message state is invalid")
    finally:
        connection.close()


class DedupStore:
    """Persist received identities, the bounded mailbox, and outbound IDs."""

    def __init__(
        self,
        path: str | Path,
        ttl_seconds: int,
        now: Callable[[], float] = time.time,
        legacy_outbound_channel: int = 8,
    ) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS received_messages (
                   sender TEXT NOT NULL,
                   message_id TEXT NOT NULL,
                   body_digest TEXT NOT NULL,
                   received_at REAL NOT NULL,
                   PRIMARY KEY (sender, message_id)
               )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS state_values (
                   name TEXT PRIMARY KEY,
                   value INTEGER NOT NULL
               )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS mailbox_messages (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sender TEXT NOT NULL,
                   message_id TEXT NOT NULL,
                   body TEXT NOT NULL,
                   received_at REAL NOT NULL,
                   read_at REAL
               )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS outbound_messages (
                   message_id TEXT PRIMARY KEY,
                   recipient TEXT NOT NULL,
                   body TEXT NOT NULL,
                   state TEXT NOT NULL,
                   attempts INTEGER NOT NULL,
                   created_at REAL NOT NULL,
                   updated_at REAL NOT NULL,
                   next_attempt_at REAL,
                   last_sent_at REAL,
                   completed_at REAL,
                   kiss_channel INTEGER NOT NULL
               )"""
        )
        outbound_columns = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(outbound_messages)").fetchall()
        }
        if "kiss_channel" not in outbound_columns:
            if not 0 <= legacy_outbound_channel <= 15:
                raise ValueError("legacy outbound KISS channel must be between 0 and 15")
            self.connection.execute(
                "ALTER TABLE outbound_messages ADD COLUMN kiss_channel INTEGER NOT NULL DEFAULT 8"
            )
            self.connection.execute(
                "UPDATE outbound_messages SET kiss_channel = ?",
                (legacy_outbound_channel,),
            )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS outbound_due_idx "
            "ON outbound_messages(state, next_attempt_at)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def claim(self, sender: str, message_id: str, body: str) -> str:
        current = self.now()
        cutoff = current - self.ttl_seconds
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.connection:
            self.connection.execute("DELETE FROM received_messages WHERE received_at < ?", (cutoff,))
            row = self.connection.execute(
                "SELECT body_digest FROM received_messages WHERE sender = ? AND message_id = ?",
                (sender, message_id),
            ).fetchone()
            if row is not None:
                return "duplicate" if row[0] == digest else "conflict"
            self.connection.execute(
                "INSERT INTO received_messages(sender, message_id, body_digest, received_at) VALUES (?, ?, ?, ?)",
                (sender, message_id, digest, current),
            )
        return "new"

    def _next_outbound_id_locked(self) -> str:
        row = self.connection.execute("SELECT value FROM state_values WHERE name = 'outbound_id'").fetchone()
        value = ((int(row[0]) if row else -1) + 1) % (36**4)
        self.connection.execute(
            "INSERT INTO state_values(name, value) VALUES ('outbound_id', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (value,),
        )
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        encoded = ""
        remainder = value
        for _ in range(4):
            encoded = alphabet[remainder % 36] + encoded
            remainder //= 36
        return encoded

    def next_outbound_id(self) -> str:
        with self.connection:
            return self._next_outbound_id_locked()

    def queue_outbound(
        self,
        recipient: str,
        body: str,
        maximum_pending: int,
        kiss_channel: int,
    ) -> OutboundMessage:
        split_callsign(recipient)
        if not 0 <= kiss_channel <= 15:
            raise ValueError("outbound KISS channel must be between 0 and 15")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            pending = self.connection.execute(
                "SELECT COUNT(*) FROM outbound_messages WHERE state = 'pending'"
            ).fetchone()[0]
            if int(pending) >= maximum_pending:
                raise RuntimeError("outbound APRS queue is full")
            message_id = self._next_outbound_id_locked()
            normalized = normalize_aprs_reply_body(body, message_id)
            current = self.now()
            self.connection.execute(
                "INSERT INTO outbound_messages"
                "(message_id, recipient, body, state, attempts, created_at, updated_at, "
                "next_attempt_at, kiss_channel) "
                "VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)",
                (message_id, recipient.upper(), normalized, current, current, current, kiss_channel),
            )
        return OutboundMessage(message_id, recipient.upper(), normalized, 0, kiss_channel)

    def due_outbound(self, limit: int = 10) -> list[OutboundMessage]:
        rows = self.connection.execute(
            "SELECT message_id, recipient, body, attempts, kiss_channel FROM outbound_messages "
            "WHERE state = 'pending' AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, created_at LIMIT ?",
            (self.now(), max(1, min(100, int(limit)))),
        ).fetchall()
        return [
            OutboundMessage(str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]))
            for row in rows
        ]

    def note_outbound_sent(self, message_id: str, retry_seconds: tuple[int, ...]) -> str:
        current = self.now()
        with self.connection:
            row = self.connection.execute(
                "SELECT attempts, state FROM outbound_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is None or row[1] != "pending":
                return "inactive"
            attempts = int(row[0]) + 1
            if attempts <= len(retry_seconds):
                state = "pending"
                next_attempt_at: float | None = current + retry_seconds[attempts - 1]
                completed_at: float | None = None
            else:
                state = "failed"
                next_attempt_at = None
                completed_at = current
            self.connection.execute(
                "UPDATE outbound_messages SET state = ?, attempts = ?, updated_at = ?, "
                "next_attempt_at = ?, last_sent_at = ?, completed_at = ? WHERE message_id = ?",
                (state, attempts, current, next_attempt_at, current, completed_at, message_id),
            )
        return state

    def apply_outbound_receipt(self, sender: str, receipt: AprsReceipt) -> str:
        current = self.now()
        with self.connection:
            row = self.connection.execute(
                "SELECT state FROM outbound_messages WHERE message_id = ? AND recipient = ?",
                (receipt.message_id, sender.upper()),
            ).fetchone()
            if row is None:
                return "unmatched"
            if row[0] in {"acked", "rejected"}:
                return "duplicate"
            self.connection.execute(
                "UPDATE outbound_messages SET state = ?, updated_at = ?, next_attempt_at = NULL, "
                "completed_at = ? WHERE message_id = ? AND recipient = ?",
                (receipt.disposition, current, current, receipt.message_id, sender.upper()),
            )
        return receipt.disposition

    def outbound_summary(self, *, include_messages: bool = False, limit: int = 20) -> dict[str, object]:
        counts = {
            str(state): int(count)
            for state, count in self.connection.execute(
                "SELECT state, COUNT(*) FROM outbound_messages GROUP BY state"
            ).fetchall()
        }
        result: dict[str, object] = {
            "pending": counts.get("pending", 0),
            "acked": counts.get("acked", 0),
            "rejected": counts.get("rejected", 0),
            "failed": counts.get("failed", 0),
        }
        if include_messages:
            rows = self.connection.execute(
                "SELECT message_id, recipient, body, state, attempts, created_at, updated_at, "
                "next_attempt_at, last_sent_at, completed_at, kiss_channel FROM outbound_messages "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
            result["messages"] = [
                {
                    "message_id": str(row[0]),
                    "recipient": str(row[1]),
                    "body": str(row[2]),
                    "state": str(row[3]),
                    "attempts": int(row[4]),
                    "created_at": float(row[5]),
                    "updated_at": float(row[6]),
                    "next_attempt_at": float(row[7]) if row[7] is not None else None,
                    "last_sent_at": float(row[8]) if row[8] is not None else None,
                    "completed_at": float(row[9]) if row[9] is not None else None,
                    "kiss_channel": int(row[10]),
                }
                for row in rows
            ]
        return result

    def purge_outbound_history(self, retention_seconds: int) -> int:
        cutoff = self.now() - retention_seconds
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM outbound_messages WHERE state != 'pending' AND updated_at < ?",
                (cutoff,),
            )
        return max(0, int(cursor.rowcount))

    def store_mailbox_message(
        self,
        sender: str,
        message_id: str,
        body: str,
        limit: int,
    ) -> bool:
        normalized = " ".join(body.split())
        if not normalized or len(normalized) > MAX_MAILBOX_TEXT:
            raise ValueError(f"mailbox text must contain 1 through {MAX_MAILBOX_TEXT} characters")
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in normalized):
            raise ValueError("mailbox text must contain printable APRS ASCII")
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO mailbox_messages"
                "(sender, message_id, body, received_at, read_at) VALUES (?, ?, ?, ?, NULL)",
                (sender, message_id, normalized, self.now()),
            )
            self.connection.execute(
                "DELETE FROM mailbox_messages WHERE id NOT IN "
                "(SELECT id FROM mailbox_messages ORDER BY id DESC LIMIT ?)",
                (limit,),
            )
        return cursor.rowcount == 1

    def mailbox_summary(self, *, include_messages: bool = False, limit: int = 10) -> dict[str, object]:
        return mailbox_summary_from_connection(
            self.connection,
            include_messages=include_messages,
            limit=limit,
        )

    def mark_mailbox_read(self) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE mailbox_messages SET read_at = ? WHERE read_at IS NULL",
                (self.now(),),
            )
        return max(0, int(cursor.rowcount))


class StatusReporter:
    """Publish aggregate, non-sensitive current-session state for PCS displays."""

    def __init__(
        self,
        path: str | Path,
        store: DedupStore,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.store = store
        self.now = now
        self.state = "starting"
        self.packets_received = 0
        self.messages_received = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def packet_received(self) -> None:
        self.packets_received += 1
        self.write()

    def message_received(self) -> None:
        self.messages_received += 1
        self.write()

    def set_state(self, state: str) -> None:
        if state not in {"starting", "connected", "waiting", "stopped"}:
            raise ValueError("invalid APRS agent runtime state")
        self.state = state
        self.write()

    def write(self) -> None:
        mailbox = self.store.mailbox_summary()
        payload = {
            "schema_version": 1,
            "collected_at_epoch": int(self.now()),
            "state": self.state,
            "status": "ok" if self.state == "connected" else "warn",
            "packets_received": self.packets_received,
            "messages_received": self.messages_received,
            "mailbox_total": mailbox["total"],
            "mailbox_unread": mailbox["unread"],
            "last_mailbox_at_epoch": mailbox["last_received_at"],
        }
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as output:
                temporary_name = output.name
                json.dump(payload, output, separators=(",", ":"), sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_name, 0o644)
            os.replace(temporary_name, self.path)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)


class SlidingWindowLimiter:
    def __init__(
        self,
        per_sender: int,
        global_limit: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_sender = per_sender
        self.global_limit = global_limit
        self.now = now
        self.global_events: deque[float] = deque()
        self.sender_events: defaultdict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _expire(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()

    def allow(self, sender: str) -> bool:
        current = self.now()
        cutoff = current - 60.0
        sender_queue = self.sender_events[sender]
        self._expire(self.global_events, cutoff)
        self._expire(sender_queue, cutoff)
        if len(self.global_events) >= self.global_limit or len(sender_queue) >= self.per_sender:
            return False
        self.global_events.append(current)
        sender_queue.append(current)
        return True


class CommandRunner:
    def run(self, arguments: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(arguments, 127, "", str(exc))


def direwolf_service_active(proc_root: str | Path = "/proc") -> bool:
    """Return true only for Dire Wolf running in its exact systemd cgroup."""

    try:
        processes = Path(proc_root).iterdir()
    except OSError:
        return False
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            cgroups = (process / "cgroup").read_text(encoding="ascii").splitlines()
            arguments = [
                field.decode("utf-8", "replace")
                for field in (process / "cmdline").read_bytes().split(b"\0")
                if field
            ]
        except OSError:
            continue
        if not any(
            line.split(":", 2)[-1] == "/system.slice/direwolf.service"
            for line in cgroups
        ):
            continue
        if not arguments or Path(arguments[0]).name != "direwolf":
            continue
        try:
            config_index = arguments.index("-c") + 1
        except ValueError:
            continue
        if config_index < len(arguments) and arguments[config_index] == "/etc/direwolf.conf":
            return True
    return False


class StatusProvider:
    """Collect coarse, privacy-preserving PCS status from read-only sources."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        gpsd_host: str = "127.0.0.1",
        gpsd_port: int = 2947,
        timeout: float = 3.0,
        temperature_path: str | Path = "/sys/class/thermal/thermal_zone0/temp",
        uptime_path: str | Path = "/proc/uptime",
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.gpsd_host = gpsd_host
        self.gpsd_port = gpsd_port
        self.timeout = timeout
        self.temperature_path = Path(temperature_path)
        self.uptime_path = Path(uptime_path)
        self.wall_time = wall_time

    def temperature_c(self) -> int | None:
        try:
            millidegrees = int(self.temperature_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        if not -40_000 <= millidegrees <= 150_000:
            return None
        return round(millidegrees / 1000)

    def temperature_value(self) -> str:
        value = self.temperature_c()
        return "N/A" if value is None else f"{value}C"

    def temperature(self) -> str:
        return f"TEMP {self.temperature_value()}"

    def uptime_value(self) -> str:
        try:
            seconds = max(0, int(float(self.uptime_path.read_text(encoding="ascii").split()[0])))
        except (OSError, ValueError, IndexError):
            return "N/A"
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60
        if days:
            return f"{days}D {hours}H {minutes}M"
        return f"{hours}H {minutes}M"

    def uptime(self) -> str:
        return f"UPTIME {self.uptime_value()}"

    def gps_value(self) -> str:
        deadline = time.monotonic() + self.timeout
        best_mode = 0
        stale_fix = False
        try:
            with socket.create_connection((self.gpsd_host, self.gpsd_port), timeout=self.timeout) as connection:
                connection.settimeout(max(0.1, self.timeout))
                connection.sendall(b'?WATCH={"enable":true,"json":true};\n')
                buffer = b""
                while time.monotonic() < deadline:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        try:
                            report = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if report.get("class") != "TPV":
                            continue
                        try:
                            mode = int(report.get("mode", 0))
                        except (TypeError, ValueError):
                            mode = 0
                        if mode >= 2 and not self._gps_report_is_fresh(report):
                            stale_fix = True
                            continue
                        best_mode = max(best_mode, mode)
                        if best_mode >= 3:
                            return "3D"
        except (OSError, TimeoutError):
            return "UNAVAILABLE"
        if best_mode == 2:
            return "2D"
        if stale_fix:
            return "STALE"
        return "NO FIX"

    def _gps_report_is_fresh(self, report: dict) -> bool:
        value = report.get("time")
        if not isinstance(value, str):
            return False
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        age = self.wall_time() - timestamp.timestamp()
        return -5 <= age <= 30

    def gps(self) -> str:
        return f"GPS {self.gps_value()}"

    def _network_states(self) -> list[tuple[str, str]]:
        result = self.runner.run(["nmcli", "-t", "-f", "TYPE,STATE", "device", "status"], self.timeout)
        if result.returncode != 0:
            return []
        states: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            fields = line.strip().lower().split(":", 1)
            if len(fields) == 2:
                states.append((fields[0], fields[1]))
        return states

    def lte_value(self) -> str:
        states = self._network_states()
        if any(kind == "gsm" and state.startswith("connected") for kind, state in states):
            return "UP"
        modem = self.runner.run(["mmcli", "-L"], self.timeout)
        if modem.returncode == 0 and "/Modem/" in modem.stdout:
            return "STANDBY"
        if modem.returncode == 0:
            return "NO MODEM"
        return "UNKNOWN"

    def lte(self) -> str:
        return f"LTE {self.lte_value()}"

    def network_value(self) -> str:
        states = self._network_states()
        connected = {kind for kind, state in states if state.startswith("connected")}
        if "ethernet" in connected:
            return "ETH"
        if "wifi" in connected or "802-11-wireless" in connected:
            return "WIFI"
        if "gsm" in connected:
            return "CELL"
        route = self.runner.run(["ip", "-4", "route", "show", "default"], self.timeout)
        return "UP" if route.returncode == 0 and route.stdout.strip() else "DOWN"

    def network(self) -> str:
        return f"NET {self.network_value()}"

    def uplink_value(self) -> str:
        route = self.runner.run(["ip", "-4", "route", "get", "8.8.8.8"], self.timeout)
        if route.returncode == 0:
            match = re.search(r"\bdev\s+(\S+)", route.stdout)
            interface = match.group(1).lower() if match else ""
            if interface.startswith("wlan"):
                return "WiFi"
            if interface.startswith(("wwan", "ppp", "cdc", "rmnet")):
                return "LTE"
        states = self._network_states()
        if any(kind == "gsm" and state.startswith("connected") for kind, state in states):
            return "LTE"
        if any(
            kind in {"wifi", "802-11-wireless"} and state.startswith("connected")
            for kind, state in states
        ):
            return "WiFi"
        return "Down"

    @staticmethod
    def power() -> str:
        return "POWER N/A"

    def status(self) -> str:
        uplink = self.uplink_value()
        gps = "3D" if self.gps_value() == "3D" else "NoFX"
        temperature = self.temperature_c()
        health = "BAD" if uplink == "Down" or temperature is None or temperature >= 85 else "OK"
        temperature_label = "N/A" if temperature is None else f"{temperature}C"
        return (
            f"PCS {health} | Uplink - {uplink} | GPS {gps} | "
            f"Pi Temp - {temperature_label}"
        )

    def execute(self, command: str) -> str:
        handlers: dict[str, Callable[[], str]] = {
            "PING": lambda: "PONG",
            "STATUS": self.status,
            "POWER": self.power,
            "LTE": self.lte,
            "GPS": self.gps,
            "TEMP": self.temperature,
            "NET": self.network,
            "UPTIME": self.uptime,
            "HELP": lambda: " ".join(COMMANDS),
        }
        handler = handlers.get(command)
        return handler() if handler else "COMMAND UNKNOWN | COMMAND LIST: HELP"


class AprsAgent:
    def __init__(
        self,
        config: AgentConfig,
        store: DedupStore,
        provider: StatusProvider,
        limiter: SlidingWindowLimiter,
        reporter: StatusReporter | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.provider = provider
        self.limiter = limiter
        self.reporter = reporter

    def set_runtime_state(self, state: str) -> None:
        if self.reporter is not None:
            self.reporter.set_state(state)

    def note_packet_received(self) -> None:
        if self.reporter is not None:
            self.reporter.packet_received()

    def refresh_status(self) -> None:
        if self.reporter is not None:
            self.reporter.write()

    def _transmit_outbound(
        self,
        outbound: OutboundMessage,
        send_ax25: SendAx25,
    ) -> None:
        information = aprs_reply_information(
            outbound.recipient,
            outbound.body,
            outbound.message_id,
        )
        send_ax25(
            outbound.kiss_channel,
            encode_ax25_ui(self.config.callsign, self.config.tocall, information),
        )
        state = self.store.note_outbound_sent(
            outbound.message_id,
            self.config.outbound_retry_seconds,
        )
        LOG.info(
            "Sent APRS message to %s id=%s channel=%d attempt=%d state=%s",
            outbound.recipient,
            outbound.message_id,
            outbound.kiss_channel,
            outbound.attempts + 1,
            state,
        )

    def retry_due(self, send_ax25: SendAx25) -> int:
        due = self.store.due_outbound()
        for outbound in due:
            self._transmit_outbound(outbound, send_ax25)
        return len(due)

    def _record_receipt(self, sender: str, receipt: AprsReceipt) -> None:
        result = self.store.apply_outbound_receipt(sender, receipt)
        if result in {"acked", "rejected"}:
            LOG.info(
                "APRS message %s by %s id=%s",
                result,
                sender,
                receipt.message_id,
            )
        else:
            LOG.warning(
                "Ignored %s APRS receipt from %s id=%s",
                result,
                sender,
                receipt.message_id,
            )

    def process(self, raw_ax25: bytes, ingress_channel: int, send_ax25: SendAx25) -> None:
        if ingress_channel not in self.config.receive_channels:
            LOG.warning("Ignored APRS frame from unconfigured KISS channel %d", ingress_channel)
            return
        try:
            frame = decode_ax25_ui(raw_ax25)
            frame = unwrap_direwolf_ichannel(frame)
        except ValueError as exc:
            LOG.debug("Ignored malformed AX.25 frame: %s", exc)
            return
        message = parse_aprs_message(frame)
        if message is None or message.addressee != self.config.callsign:
            return
        if message.sender == self.config.callsign:
            LOG.warning("Ignored an APRS message claiming the local station as its sender")
            return
        if message.reply_ack_id is not None:
            self._record_receipt(
                message.sender,
                AprsReceipt("acked", message.reply_ack_id),
            )
        receipt = parse_aprs_receipt(message.body) if message.message_id is None else None
        if receipt is not None:
            self._record_receipt(message.sender, receipt)
            return
        if message.message_id is None:
            LOG.info("Ignored unnumbered APRS message from %s", message.sender)
            return
        if self.reporter is not None:
            self.reporter.message_received()
        ack = aprs_ack_information(message.sender, message.message_id)
        send_ax25(
            ingress_channel,
            encode_ax25_ui(self.config.callsign, self.config.tocall, ack),
        )

        if not self.limiter.allow(message.sender):
            LOG.warning("Rate limit suppressed APRS command handling for %s", message.sender)
            return

        normalized_body = " ".join(message.body.strip().split())
        upper_body = normalized_body.upper()
        command_word, separator, mailbox_text = normalized_body.partition(" ")
        if separator and command_word.upper() == "MSG":
            command = "MSG"
        else:
            command = COMMAND_ALIASES.get(upper_body, upper_body)
        claim_body = normalized_body if command == "MSG" else upper_body
        claim = self.store.claim(message.sender, message.message_id, claim_body)
        if claim == "duplicate":
            LOG.info("Acknowledged duplicate APRS message from %s id=%s", message.sender, message.message_id)
            return
        if claim == "conflict":
            LOG.warning("Ignored conflicting APRS message reuse from %s id=%s", message.sender, message.message_id)
            return

        command_label = command if command in COMMANDS else "<unknown>"
        LOG.info(
            "Handling APRS request from %s id=%s channel=%d command=%s",
            message.sender,
            message.message_id,
            ingress_channel,
            command_label,
        )
        if command == "MSG":
            try:
                if not separator or not mailbox_text.strip():
                    response = "MSG FORMAT: MSG <TEXT>"
                else:
                    self.store.store_mailbox_message(
                        message.sender,
                        message.message_id,
                        mailbox_text,
                        self.config.mailbox_limit,
                    )
                    self.refresh_status()
                    response = "MESSAGE STORED"
            except ValueError:
                response = "MSG FORMAT: MSG <TEXT>"
        else:
            try:
                response = self.provider.execute(command)
            except Exception:
                LOG.exception("APRS status provider failed")
                response = "PCS STATUS UNAVAILABLE"
        try:
            self.store.purge_outbound_history(self.config.outbound_retention_seconds)
            outbound = self.store.queue_outbound(
                message.sender,
                response,
                self.config.outbound_max_pending,
                ingress_channel,
            )
        except RuntimeError:
            LOG.error("Outbound APRS queue is full; reply to %s was not queued", message.sender)
            return
        self._transmit_outbound(outbound, send_ax25)


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        agent: AprsAgent,
        engine_active: Callable[[], bool] = direwolf_service_active,
    ) -> None:
        self.config = config
        self.agent = agent
        self.engine_active = engine_active
        self.stop_event = threading.Event()

    def stop(self, *_args: object) -> None:
        self.stop_event.set()

    def _serve_connection(self, connection: socket.socket) -> None:
        decoder = KissDecoder()
        connection.settimeout(1.0)

        def send_ax25(channel: int, frame: bytes) -> None:
            connection.sendall(encode_kiss(channel, frame))

        while not self.stop_event.is_set():
            self.agent.retry_due(send_ax25)
            try:
                chunk = connection.recv(4096)
            except socket.timeout:
                self.agent.refresh_status()
                continue
            if not chunk:
                raise ConnectionError("Dire Wolf closed the KISS connection")
            for kiss_frame in decoder.feed(chunk):
                if not kiss_frame:
                    continue
                command = kiss_frame[0]
                channel = command >> 4
                if (command & 0x0F) != KISS_DATA or channel not in self.config.receive_channels:
                    continue
                self.agent.note_packet_received()
                self.agent.process(kiss_frame[1:], channel, send_ax25)

    def run(self) -> None:
        delay = 1
        self.agent.set_runtime_state("starting")
        while not self.stop_event.is_set():
            if not self.engine_active():
                self.agent.set_runtime_state("waiting")
                LOG.warning(
                    "direwolf.service is inactive; suppressing the APRS Agent KISS connection for %ds",
                    delay,
                )
                self.stop_event.wait(delay)
                delay = min(self.config.reconnect_max_seconds, delay * 2)
                continue
            try:
                with socket.create_connection(
                    (self.config.kiss_host, self.config.kiss_port),
                    timeout=self.config.command_timeout_seconds,
                ) as connection:
                    self.agent.set_runtime_state("connected")
                    LOG.info(
                        "Connected to local Dire Wolf KISS %s:%d on channels %s",
                        self.config.kiss_host,
                        self.config.kiss_port,
                        ",".join(str(channel) for channel in self.config.receive_channels),
                    )
                    delay = 1
                    self._serve_connection(connection)
            except (OSError, ConnectionError) as exc:
                self.agent.set_runtime_state("waiting")
                if self.stop_event.is_set():
                    break
                LOG.warning("Dire Wolf KISS connection unavailable: %s; retrying in %ds", exc, delay)
                self.stop_event.wait(delay)
                delay = min(self.config.reconnect_max_seconds, delay * 2)


def parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--mailbox-json", action="store_true")
    parser.add_argument("--mailbox-summary-json", action="store_true")
    parser.add_argument("--mark-mailbox-read", action="store_true")
    parser.add_argument("--check-state", action="store_true")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    args = parse_args(arguments)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")
    try:
        config = AgentConfig.load(args.config)
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2
    if args.check_config:
        rf_route = f"RF channel {config.rf_channel} enabled" if config.rf_enabled else "RF disabled"
        print(
            f"APRS agent configuration is valid for {config.callsign}; "
            f"local KISS {config.kiss_host}:{config.kiss_port} "
            f"APRS-IS channel {config.kiss_channel}; {rf_route}."
        )
        return 0

    selected_modes = sum(
        bool(value)
        for value in (
            args.mailbox_json,
            args.mailbox_summary_json,
            args.mark_mailbox_read,
            args.check_state,
        )
    )
    if selected_modes > 1:
        LOG.error("select only one mailbox operation")
        return 2
    if args.mailbox_json or args.mailbox_summary_json:
        try:
            snapshot = read_mailbox_snapshot(
                config.state_db,
                include_messages=args.mailbox_json,
            )
        except (OSError, sqlite3.Error) as exc:
            LOG.error("APRS mailbox is unavailable: %s", exc)
            return 1
        print(json.dumps(snapshot, separators=(",", ":")))
        return 0
    if args.check_state:
        try:
            validate_state_database(config.state_db, config.outbound_max_pending)
        except (OSError, sqlite3.Error, ValueError) as exc:
            LOG.error("APRS agent state is invalid: %s", exc)
            return 1
        print("APRS agent state database and outbound queue are valid.")
        return 0
    if args.mark_mailbox_read and not Path(config.state_db).is_file():
        LOG.error("APRS mailbox database is unavailable")
        return 1

    store = DedupStore(
        config.state_db,
        config.dedupe_ttl_seconds,
        legacy_outbound_channel=config.kiss_channel,
    )
    if args.mark_mailbox_read:
        try:
            marked = store.mark_mailbox_read()
            print(f"Marked {marked} APRS mailbox message(s) read.")
        finally:
            store.close()
        return 0

    reporter = StatusReporter(config.status_file, store)
    provider = StatusProvider(
        gpsd_host=config.gpsd_host,
        gpsd_port=config.gpsd_port,
        timeout=config.command_timeout_seconds,
    )
    limiter = SlidingWindowLimiter(config.sender_rate_per_minute, config.global_rate_per_minute)
    agent = AprsAgent(config, store, provider, limiter, reporter)
    runtime = AgentRuntime(config, agent)
    signal.signal(signal.SIGTERM, runtime.stop)
    signal.signal(signal.SIGINT, runtime.stop)
    try:
        runtime.run()
    finally:
        agent.set_runtime_state("stopped")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
