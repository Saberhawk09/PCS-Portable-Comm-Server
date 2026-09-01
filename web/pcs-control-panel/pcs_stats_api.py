#!/usr/bin/env python3

"""Versioned PCS statistics, device-pairing, and administration API.

This service deliberately reuses the fixed dashboard-public-json dispatcher
action. Pairing exchanges a verified administrator password for a revocable
device token. Administrative routes can invoke only the same fixed dispatcher
actions exposed by the PCS web panel. There is no arbitrary command route.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOST = os.environ.get("PCS_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("PCS_API_PORT", "9443"))
DISPATCHER = os.environ.get("PCS_WEB_ACTION", "/usr/local/sbin/pcs-web-action")
TOKEN_FILE = os.environ.get(
    "PCS_API_TOKEN_FILE",
    "/etc/pcs-stats-api/api-read-tokens.json",
)
PAIRING_HELPER = os.environ.get("PCS_API_TOKEN_HELPER", "/usr/local/sbin/pcs-api-token")
PASSWORD_HELPER = os.environ.get(
    "PCS_ADMIN_PASSWORD_HELPER",
    "/usr/local/sbin/pcs-admin-password-helper",
)
BACKUP_CONFIG_HELPER = os.environ.get(
    "PCS_BACKUP_CONFIG_HELPER",
    "/usr/local/sbin/pcs-backup-config",
)
API_ENABLED = os.environ.get("PCS_API_ENABLED", "no").lower() == "yes"
COLLECT_TIMEOUT = int(os.environ.get("PCS_API_COLLECT_TIMEOUT", "30"))
MAX_TOKEN_FILE_BYTES = 64 * 1024
MAX_PAIRING_BODY_BYTES = 4096
MAX_ADMIN_PASSWORD_LENGTH = 1024
MIN_ADMIN_PASSWORD_LENGTH = 12
MAX_ACTION_BODY_BYTES = 4096
MAX_ACTION_OUTPUT_BYTES = 128 * 1024
MAX_PASSWORD_CHANGE_BODY_BYTES = 4096
MAX_BACKUP_SETTINGS_BODY_BYTES = 4096
MIN_BACKUP_INTERVAL_MINUTES = 1
MAX_BACKUP_INTERVAL_MINUTES = 43_200
DEVICE_ID_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}")
API_CONTENT_TYPE = "application/vnd.pcs.v1+json; charset=utf-8"
PROBLEM_CONTENT_TYPE = "application/problem+json; charset=utf-8"

RESOURCE_SECTIONS = {
    "network": "network",
    "cellular": "cellular",
    "time": "time",
    "gps": "gnss",
    "aprs": "aprs",
    "meshtastic": "meshtastic",
    "pistar": "pistar",
    "storage": "storage",
    "services": "services",
}

RESOURCE_PATHS = {
    "status": "/api/v1/status",
    **{resource: f"/api/v1/{resource}" for resource in RESOURCE_SECTIONS},
}

ACTION_GROUPS = {
    "health": {
        "status": ("View PCS Status", "Show the full PCS status report."),
        "self-test": ("Run Self-Test", "Run the Pi-side health validation."),
        "storage-status": ("View Storage", "Show USB, SD backup, and Samba state."),
        "restart-logs": ("View Restart Logs", "Show recent PCS restart service logs."),
    },
    "network": {
        "wifi-status": ("View Wi-Fi", "Show Wi-Fi uplink status and the default route."),
        "wifi-connect": ("Connect Wi-Fi", "Connect to the strongest visible saved Wi-Fi network."),
        "wifi-disconnect": ("Disable Wi-Fi Radio", "Turn off Pi Wi-Fi for offline or cellular-only operation."),
    },
    "cellular": {
        "cellular-status": ("View Cellular", "Show WWAN modem and cellular connection state."),
        "cellular-connect": ("Connect Cellular", "Bring up cellular data without changing the fallback policy."),
        "cellular-disconnect": ("Disconnect Cellular", "Bring down cellular data; automatic mode may reconnect it if Wi-Fi is unavailable."),
        "cellular-test": ("Test Cellular", "Test cellular-only internet through the WWAN interface."),
    },
    "communications": {
        "meshtastic-status": ("View Meshtastic", "Show privacy-safe node, mesh, MQTT, GPSD, and environment status."),
        "restart-meshtastic": ("Restart Meshtastic", "Reconnect the Meshtastic radio transport and MQTT gateway."),
    },
    "storage": {
        "sync-backup": ("Sync USB to SD Backup", "Mirror the USB primary share to the SD backup."),
        "mount-usb": ("Mount USB Storage", "Mount the USB primary share and restart Samba."),
        "mount-new-usb": ("Mount New USB Storage", "Configure a newly attached USB device as primary storage."),
        "safe-unmount-usb": ("Unmount USB Safely", "Sync, stop Samba, unmount USB, and restart Samba."),
    },
    "services": {
        "restart-services": ("Restart PCS Services", "Restart core PCS services through systemd."),
        "restart-samba": ("Restart Samba", "Restart Samba only."),
        "restart-modemmanager": ("Restart ModemManager", "Restart modem detection and reassert GPS."),
    },
    "time-gps": {
        "sync-time": ("Sync Time Now", "Poll time sources, step the clock, and update the RTC."),
        "restart-chrony": ("Restart Chrony", "Restart Chrony only."),
        "restart-gpsd": ("Restart GPSD", "Reassert WWAN NMEA mode and restart gpsd."),
    },
    "power": {
        "reboot-system": ("Reboot PCS", "Restart the Raspberry Pi."),
        "shutdown-system": ("Shutdown PCS", "Shut down Pi-Star when paired, then power off PCS."),
    },
}
ACTION_MAP = {
    name: {"name": name, "label": label, "description": description, "group": group}
    for group, actions in ACTION_GROUPS.items()
    for name, (label, description) in actions.items()
}
READ_ONLY_ACTIONS = {
    "status", "self-test", "storage-status", "restart-logs", "wifi-status",
    "cellular-status", "cellular-test", "meshtastic-status",
}
# Remote API callers receive a challenge gate for every state-changing button,
# including buttons for which the local web panel does not currently prompt.
DANGEROUS_ACTIONS = set(ACTION_MAP) - READ_ONLY_ACTIONS
ACTION_TIMEOUTS = {"self-test": 360, "sync-backup": 900, "safe-unmount-usb": 900}

RESOURCE_CARD_IDS = {
    "network": {"network", "remote-management", "uplink-details", "client-lan"},
    "cellular": {"cellular"},
    "time": {"time"},
    "gps": {"gps"},
    "aprs": {"aprs"},
    "meshtastic": {"meshtastic"},
    "pistar": set(),
    "storage": {"storage", "backup-health", "samba"},
    "services": {"services", "web-admin"},
}

# This is intentionally a second allowlist, independent of the public web
# allowlist. New dashboard fields do not become API fields by accident.
API_FIELDS = {
    "system": {
        "status", "uptime", "cpu_temperature", "cpu_load", "memory_used",
        "root_storage_used",
    },
    "network": {
        "status", "offline", "lan_gateway", "openwrt_online",
        "internet_available", "uplink_type", "connected_client_count",
    },
    "remote_management": {
        "configured", "status", "connection", "management_address",
        "boot_enabled", "firewall_active", "latest_handshake",
    },
    "cellular": {
        "status", "modem_present", "connected", "carrier",
        "access_technology", "signal", "fallback_policy", "fallback_active",
    },
    "time": {
        "status", "chrony_active", "synchronized", "source", "reference",
    },
    "gnss": {
        "status", "receiver_active", "fix", "satellites", "grid_square",
        "utc_time",
    },
    "storage": {
        "status", "usb_mounted", "primary_share_available",
        "backup_share_available", "usb_free_gb", "backup_free_gb",
        "backup_status", "last_backup_age", "automatic_backup_enabled",
        "backup_interval_minutes", "keep_backup_history", "backup_history_count",
    },
    "services": {
        "status", "homepage_available", "file_sharing_available",
        "cockpit_available", "gpsd_lan_enabled",
    },
    "pistar": {"configured", "online"},
    "aprs": {
        "configured", "status", "service", "callsign", "role", "frequency",
        "aprs_is", "aprs_is_profile", "modem", "beacon", "digipeater",
        "kiss", "fx25", "packets", "last_heard", "tx_state",
    },
    "meshtastic": {
        "configured", "status", "service", "hardware", "firmware",
        "transport", "radio_link", "mqtt", "mqtt_policy", "rf_igate",
        "map_reporting", "downlink_filters", "mqtt_activity", "map_mqtt",
        "mesh_activity", "remote_nodes", "last_heard", "gpsd_position",
        "case_environment", "utilization", "power",
    },
}

NULL_STRINGS = {
    "", "unknown", "unavailable", "not available", "not configured",
    "none observed",
}


class CollectionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PairingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ActionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_value(value):
    if isinstance(value, str) and value.strip().lower() in NULL_STRINGS:
        return None
    return value


def utc_timestamp(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def severity(value) -> str:
    candidate = str(value or "warn").lower()
    return candidate if candidate in {"ok", "warn", "bad"} else "warn"


def sanitize_alerts(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    alerts = []
    for entry in value[:32]:
        if not isinstance(entry, dict):
            continue
        alerts.append({
            "severity": severity(entry.get("severity")),
            "component": str(entry.get("component", "System"))[:80],
            "message": str(entry.get("message", "Needs attention"))[:240],
        })
    return alerts


def sanitize_sections(dashboard: dict) -> dict:
    sections = {}
    for section_name, allowed in API_FIELDS.items():
        source = dashboard.get(section_name, {})
        if not isinstance(source, dict):
            source = {}
        sections[section_name] = {
            key: normalize_value(source[key])
            for key in sorted(allowed)
            if key in source
        }
    return sections


def api_document(resource: str, dashboard: dict) -> dict:
    sections = sanitize_sections(dashboard)
    generated_at = utc_timestamp(dashboard.get("generated_at"))
    if resource == "status":
        return {
            "api_version": "v1",
            "schema_version": "1.0",
            "resource": "status",
            "generated_at": generated_at,
            "health": {
                "severity": severity(dashboard.get("overall")),
                "offline": bool(dashboard.get("offline", False)),
            },
            "data": {**sections, "alerts": sanitize_alerts(dashboard.get("alerts", []))},
            "details": None,
            "access": "public",
        }

    section_name = RESOURCE_SECTIONS[resource]
    section = sections[section_name]
    return {
        "api_version": "v1",
        "schema_version": "1.0",
        "resource": resource,
        "generated_at": generated_at,
        "health": {"severity": severity(section.get("status"))},
        "data": {key: value for key, value in section.items() if key != "status"},
        "details": None,
        "access": "public",
    }


def discovery_document(authenticated: bool = False, action_authorized: bool = False) -> dict:
    return {
        "api_version": "v1",
        "schema_version": "1.0",
        "resource": "discovery",
        "access": "authenticated" if authenticated else "public",
        "content_type": API_CONTENT_TYPE.split(";", 1)[0],
        "authentication": {
            "public": "Omit the Authorization header for the public allowlist.",
            "authenticated": "Use Authorization: Bearer <stats:read token> for admin-visible status details; admin-scoped routes additionally require admin:actions or admin:password.",
        },
        "resources": dict(RESOURCE_PATHS),
        "pairing": "/api/v1/pair",
        "actions": "/api/v1/actions",
        "password": "/api/v1/admin/password",
        "methods": ["GET", "HEAD"],
        "write_actions": action_authorized,
    }


def action_catalog_document() -> dict:
    actions = []
    for name, metadata in ACTION_MAP.items():
        actions.append({
            **metadata,
            "dangerous": name in DANGEROUS_ACTIONS,
            "challenge_required": name in DANGEROUS_ACTIONS,
            "execute_path": f"/api/v1/actions/{name}",
            "challenge_path": f"/api/v1/actions/{name}/challenge" if name in DANGEROUS_ACTIONS else None,
        })
    return {
        "api_version": "v1",
        "schema_version": "1.0",
        "resource": "actions",
        "access": "authenticated",
        "required_scope": "admin:actions",
        "actions": actions,
    }


def pair_device(admin_password: str, device_id: str) -> dict:
    payload = json.dumps(
        {"admin_password": admin_password, "device_id": device_id},
        separators=(",", ":"),
    )
    try:
        result = subprocess.run(
            [
                "sudo", "-n", PAIRING_HELPER,
                "--file", TOKEN_FILE,
                "pair-from-stdin",
            ],
            input=payload,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PairingError("pairing_unavailable", "PCS pairing is unavailable.") from exc
    if result.returncode != 0:
        print(
            f"PCS_API_PAIRING_HELPER_FAILED exit_status={result.returncode}",
            file=sys.stderr,
            flush=True,
        )
    if result.returncode == 3:
        raise PairingError("admin_authentication_failed", "Administrator authentication failed.")
    if result.returncode == 4:
        raise PairingError("device_already_paired", "That device ID is already paired.")
    if result.returncode != 0:
        raise PairingError("pairing_failed", "PCS pairing failed.")
    try:
        response = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PairingError("pairing_failed", "PCS pairing returned an invalid response.") from exc
    if (
        not isinstance(response, dict)
        or response.get("device_id") != device_id
        or response.get("token_type") != "Bearer"
        or response.get("scopes") != ["stats:read", "admin:actions", "admin:password"]
        or not isinstance(response.get("token"), str)
        or not response["token"].startswith("pcs_ro_")
    ):
        raise PairingError("pairing_failed", "PCS pairing returned an invalid response.")
    return response


def change_admin_password(current_password: str, new_password: str) -> None:
    payload = json.dumps(
        {"current_password": current_password, "new_password": new_password},
        separators=(",", ":"),
    )
    try:
        result = subprocess.run(
            ["sudo", "-n", PASSWORD_HELPER, "--change-from-stdin"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PairingError("password_change_unavailable", "PCS password change is unavailable.") from exc
    if result.returncode == 3:
        raise PairingError("current_password_incorrect", "Current administrator password was incorrect.")
    if result.returncode == 4:
        raise PairingError("password_change_rejected", "The new administrator password was rejected.")
    if result.returncode != 0:
        raise PairingError("password_change_failed", "PCS password change failed.")


def validated_backup_settings(value: object) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "enabled", "interval_minutes", "keep_history"}
        or value.get("version") != 2
        or not isinstance(value.get("enabled"), bool)
        or isinstance(value.get("interval_minutes"), bool)
        or not isinstance(value.get("interval_minutes"), int)
        or not MIN_BACKUP_INTERVAL_MINUTES <= value["interval_minutes"] <= MAX_BACKUP_INTERVAL_MINUTES
        or not isinstance(value.get("keep_history"), bool)
    ):
        raise ActionError("backup_settings_invalid", "PCS backup settings are invalid.")
    return value


def backup_settings_document(value: dict) -> dict:
    validated = validated_backup_settings(value)
    return {
        "api_version": "v1",
        "schema_version": "1.0",
        "resource": "backup-settings",
        "access": "authenticated",
        "data": {
            "enabled": validated["enabled"],
            "interval_minutes": validated["interval_minutes"],
            "keep_history": validated["keep_history"],
            "minimum_interval_minutes": MIN_BACKUP_INTERVAL_MINUTES,
            "maximum_interval_minutes": MAX_BACKUP_INTERVAL_MINUTES,
            "non_destructive": True,
        },
    }


def run_backup_settings_helper(command: str, request: dict | None = None) -> dict:
    if command not in {"show", "set-from-stdin"}:
        raise ValueError("unsupported backup settings command")
    arguments = ["sudo", "-n", BACKUP_CONFIG_HELPER, command]
    payload = None if request is None else json.dumps(request, separators=(",", ":"))
    try:
        result = subprocess.run(
            arguments,
            input=payload,
            text=True,
            capture_output=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("backup_settings_unavailable", "PCS backup settings are unavailable.") from exc
    if result.returncode != 0:
        raise ActionError("backup_settings_failed", "PCS backup settings could not be applied.")
    try:
        value = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ActionError("backup_settings_invalid", "PCS backup settings returned an invalid response.") from exc
    return validated_backup_settings(value)


BLOCKED_ADMIN_LABELS = {
    "password", "passcode", "private key", "preshared key", "pre-shared key",
    "secret", "credential", "api key", "access token",
}
ADMIN_CLIENT_INFO_FIELDS = {
    "router_ip", "openwrt_url", "pi_star_configured", "pi_star_url",
    "aprs_state", "meshtastic_state", "wan_public_ip", "uplink_interface",
    "uplink_source_ip", "router_side_clients",
}
ADMIN_CLIENT_FIELDS = {"ip", "mac", "state", "name"}


def sanitized_admin_card(card: dict) -> dict | None:
    if not isinstance(card, dict):
        return None
    items = []
    for item in card.get("items", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        if any(blocked in label.lower() for blocked in BLOCKED_ADMIN_LABELS):
            continue
        items.append({
            key: item[key]
            for key in ("label", "value")
            if key in item
        })
    result = {
        key: card[key]
        for key in ("id", "title", "status", "summary", "metrics")
        if key in card
    }
    result["items"] = items
    return result


def add_authenticated_details(document: dict, resource: str, dashboard: dict) -> dict:
    cards = [
        sanitized
        for card in dashboard.get("cards", [])
        if isinstance(card, dict)
        and (resource == "status" or card.get("id") in RESOURCE_CARD_IDS[resource])
        if (sanitized := sanitized_admin_card(card)) is not None
    ]
    details = {"cards": cards}
    if resource in {"status", "network", "pistar"}:
        client_info = dashboard.get("client_info", {})
        if not isinstance(client_info, dict):
            client_info = {}
        sanitized_client_info = {
            key: client_info[key]
            for key in sorted(ADMIN_CLIENT_INFO_FIELDS)
            if key in client_info
        }
        clients = sanitized_client_info.get("router_side_clients")
        if isinstance(clients, list):
            sanitized_client_info["router_side_clients"] = [
                {
                    key: client[key]
                    for key in sorted(ADMIN_CLIENT_FIELDS)
                    if key in client
                }
                for client in clients
                if isinstance(client, dict)
            ]
        details["client_info"] = sanitized_client_info
    document["details"] = details
    document["access"] = "authenticated"
    if resource == "status":
        document["health"]["severity"] = severity(dashboard.get("overall"))
        document["health"]["offline"] = bool(dashboard.get("offline", False))
    return document


class StatusCollector:
    def __init__(self, dispatcher: str = DISPATCHER, timeout: int = COLLECT_TIMEOUT, ttl: int = 10):
        self.dispatcher = dispatcher
        self.timeout = timeout
        self.ttl = ttl
        self.cached = {}
        self.cached_at = {}
        self.lock = threading.Lock()

    def collect(self, action: str = "dashboard-public-json") -> dict:
        if action not in {"dashboard-public-json", "dashboard-json"}:
            raise ValueError("unsupported collector action")
        with self.lock:
            if action in self.cached and time.monotonic() - self.cached_at[action] < self.ttl:
                return self.cached[action]
            try:
                result = subprocess.run(
                    ["sudo", "-n", self.dispatcher, action],
                    text=True,
                    capture_output=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise CollectionError("collector_timeout", "PCS status collection timed out.") from exc
            except OSError as exc:
                raise CollectionError("collector_unavailable", "PCS status collector is unavailable.") from exc
            if result.returncode != 0:
                raise CollectionError(
                    "collector_failed",
                    f"PCS status collection failed with exit code {result.returncode}.",
                )
            try:
                dashboard = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise CollectionError("collector_invalid", "PCS status collector returned invalid JSON.") from exc
            if not isinstance(dashboard, dict):
                raise CollectionError("collector_invalid", "PCS status collector returned the wrong shape.")
            self.cached[action] = dashboard
            self.cached_at[action] = time.monotonic()
            return dashboard

    def invalidate(self) -> None:
        with self.lock:
            self.cached.clear()
            self.cached_at.clear()


class ActionRunner:
    def __init__(self, dispatcher: str = DISPATCHER):
        self.dispatcher = dispatcher
        self.lock = threading.Lock()

    @staticmethod
    def _output_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def run(self, action: str) -> dict:
        if action not in ACTION_MAP:
            raise ValueError("unsupported administrative action")
        if not self.lock.acquire(blocking=False):
            raise ActionError("action_busy", "Another PCS administrative action is already running.")
        started = time.monotonic()
        try:
            try:
                result = subprocess.run(
                    ["sudo", "-n", self.dispatcher, action],
                    text=True,
                    capture_output=True,
                    timeout=ACTION_TIMEOUTS.get(action, 300),
                )
            except subprocess.TimeoutExpired as exc:
                raise ActionError("action_timeout", "PCS administrative action timed out.") from exc
            except OSError as exc:
                raise ActionError("action_unavailable", "PCS administrative action is unavailable.") from exc
            stdout = self._output_text(result.stdout)
            stderr = self._output_text(result.stderr)
            output = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
            encoded = output.encode("utf-8", errors="replace")
            truncated = len(encoded) > MAX_ACTION_OUTPUT_BYTES
            if truncated:
                output = encoded[:MAX_ACTION_OUTPUT_BYTES].decode("utf-8", errors="ignore") + "\n[output truncated]"
            return {
                "exit_code": result.returncode,
                "ok": result.returncode == 0,
                "output": output,
                "output_truncated": truncated,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            self.lock.release()


class ActionChallenges:
    def __init__(self, ttl: int = 60):
        self.ttl = ttl
        self.entries = {}
        self.lock = threading.Lock()

    def issue(self, principal_id: str, action: str) -> tuple[str, int]:
        nonce = secrets.token_urlsafe(32)
        expires = time.monotonic() + self.ttl
        with self.lock:
            self.entries[(principal_id, action)] = (nonce, expires)
        return nonce, self.ttl

    def consume(self, principal_id: str, action: str, nonce: str) -> bool:
        with self.lock:
            expected, expires = self.entries.pop((principal_id, action), ("", 0))
        return bool(expected) and time.monotonic() <= expires and hmac.compare_digest(expected, nonce)


class ApiTokenStore:
    def __init__(self, path: str = TOKEN_FILE):
        self.path = path

    def records(self) -> list[dict]:
        try:
            file_stat = os.lstat(self.path)
            if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
                return []
            if file_stat.st_size <= 0 or file_stat.st_size > MAX_TOKEN_FILE_BYTES:
                return []
            if os.name != "nt" and stat.S_IMODE(file_stat.st_mode) & 0o007:
                return []
            with open(self.path, "r", encoding="utf-8") as source:
                payload = json.load(source)
        except (OSError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        if payload.get("version") != 1 or not isinstance(payload.get("tokens"), list):
            return []
        return payload["tokens"]

    def authenticate(self, authorization: str | None) -> dict | None:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        token = authorization[7:]
        if len(token) < 32 or len(token) > 512 or token.strip() != token:
            return None
        supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched = None
        for record in self.records():
            if not isinstance(record, dict):
                continue
            expected = str(record.get("token_sha256", ""))
            valid_hash = len(expected) == 64
            equal = valid_hash and hmac.compare_digest(supplied, expected)
            if equal and record.get("enabled") is True:
                scopes = record.get("scopes", [])
                if isinstance(scopes, list) and "stats:read" in scopes:
                    matched = {
                        "id": str(record.get("id", "unnamed")),
                        "scopes": tuple(str(scope) for scope in scopes),
                    }
        return matched

    def configured(self) -> bool:
        return any(
            record.get("enabled") is True and "stats:read" in record.get("scopes", [])
            for record in self.records()
            if isinstance(record, dict) and isinstance(record.get("scopes"), list)
        )


class FixedWindowLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.entries = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self.lock:
            start, count = self.entries.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self.entries[key] = (start, count)
            retry_after = max(1, int(self.window - (now - start)))
            return count <= self.limit, retry_after


COLLECTOR = StatusCollector()
TOKENS = ApiTokenStore()
UNAUTH_LIMITER = FixedWindowLimiter(limit=30, window=60)
PUBLIC_LIMITER = FixedWindowLimiter(limit=60, window=60)
AUTH_LIMITER = FixedWindowLimiter(limit=120, window=60)
PAIR_LIMITER = FixedWindowLimiter(limit=5, window=300)
ACTION_LIMITER = FixedWindowLimiter(limit=20, window=60)
ACTION_RUNNER = ActionRunner()
ACTION_CHALLENGES = ActionChallenges(ttl=60)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32
    tls_context: ssl.SSLContext | None = None

    def get_request(self):
        request, client_address = super().get_request()
        if self.tls_context is None:
            return request, client_address
        try:
            request.settimeout(10)
            request = self.tls_context.wrap_socket(
                request,
                server_side=True,
                do_handshake_on_connect=False,
            )
        except Exception:
            request.close()
            raise
        return request, client_address

    def process_request_thread(self, request, client_address) -> None:
        if isinstance(request, ssl.SSLSocket):
            try:
                request.do_handshake()
                request.settimeout(30)
            except (TimeoutError, OSError, ssl.SSLError):
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLEOFError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "PCSStatsAPI/1"

    def version_string(self) -> str:
        return self.server_version

    def common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_json(self, status_code: int, payload: dict, content_type: str = API_CONTENT_TYPE, extra_headers: dict | None = None, head_only: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.common_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def problem(self, status_code: int, code: str, title: str, headers: dict | None = None, head_only: bool = False) -> None:
        self.send_json(
            status_code,
            {
                "type": "about:blank",
                "title": title,
                "status": status_code,
                "code": code,
                "request_id": secrets.token_urlsafe(12),
            },
            content_type=PROBLEM_CONTENT_TYPE,
            extra_headers=headers,
            head_only=head_only,
        )

    def access_context(self, head_only: bool = False) -> tuple[bool, dict | None]:
        address = self.client_address[0]
        authorization = self.headers.get("Authorization")
        if not authorization:
            allowed, retry = PUBLIC_LIMITER.allow(address)
            if not allowed:
                self.problem(429, "rate_limited", "Too many requests.", {"Retry-After": str(retry)}, head_only)
                return False, None
            return True, None
        allowed, retry = UNAUTH_LIMITER.allow(address)
        if not allowed:
            self.problem(429, "rate_limited", "Too many requests.", {"Retry-After": str(retry)}, head_only)
            return False, None
        principal = TOKENS.authenticate(authorization)
        if principal is None:
            self.problem(
                401,
                "unauthorized",
                "A valid read-only PCS API token is required.",
                {"WWW-Authenticate": 'Bearer realm="PCS Stats API"'},
                head_only,
            )
            return False, None
        allowed, retry = AUTH_LIMITER.allow(f"{address}:{principal['id']}")
        if not allowed:
            self.problem(429, "rate_limited", "Too many requests.", {"Retry-After": str(retry)}, head_only)
            return False, None
        return True, principal

    def action_context(self, head_only: bool = False) -> tuple[bool, dict | None]:
        authorized, principal = self.access_context(head_only=head_only)
        if not authorized:
            return False, None
        if principal is None or "admin:actions" not in principal.get("scopes", ()):
            self.problem(
                403,
                "insufficient_scope",
                "An authenticated PCS device token with admin:actions scope is required.",
                {"WWW-Authenticate": 'Bearer realm="PCS Stats API", scope="admin:actions"'},
                head_only=head_only,
            )
            return False, None
        allowed, retry = ACTION_LIMITER.allow(f"{self.client_address[0]}:{principal['id']}")
        if not allowed:
            self.problem(429, "rate_limited", "Too many administrative requests.", {"Retry-After": str(retry)}, head_only=head_only)
            return False, None
        return True, principal

    def password_context(self) -> tuple[bool, dict | None]:
        authorized, principal = self.access_context()
        if not authorized:
            return False, None
        if principal is None or "admin:password" not in principal.get("scopes", ()):
            self.problem(
                403,
                "insufficient_scope",
                "A paired PCS device token with admin:password scope is required.",
                {"WWW-Authenticate": 'Bearer realm="PCS Stats API", scope="admin:password"'},
            )
            return False, None
        allowed, retry = ACTION_LIMITER.allow(f"password:{self.client_address[0]}:{principal['id']}")
        if not allowed:
            self.problem(429, "rate_limited", "Too many administrative requests.", {"Retry-After": str(retry)})
            return False, None
        return True, principal

    def read_json_body(self, maximum: int, invalid_code: str) -> dict | None:
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self.problem(415, "unsupported_media_type", "This request requires application/json.")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > maximum:
            self.problem(400, invalid_code, "Invalid JSON request.")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self.problem(400, invalid_code, "Invalid JSON request.")
            return None
        if not isinstance(payload, dict):
            self.problem(400, invalid_code, "Invalid JSON request.")
            return None
        return payload

    def handle_read(self, head_only: bool = False) -> None:
        parsed = urlsplit(self.path)
        if not API_ENABLED:
            self.problem(404, "not_found", "Not found.", head_only=head_only)
            return
        if parsed.query:
            self.problem(400, "query_not_supported", "Query parameters are not supported.", head_only=head_only)
            return
        if parsed.path in {"/api/v1", "/api/v1/"}:
            authorized, principal = self.access_context(head_only=head_only)
            if not authorized:
                return
            self.send_json(
                200,
                discovery_document(
                    authenticated=principal is not None,
                    action_authorized=principal is not None and "admin:actions" in principal.get("scopes", ()),
                ),
                head_only=head_only,
            )
            return
        if parsed.path in {"/api/v1/actions", "/api/v1/actions/"}:
            authorized, principal = self.action_context(head_only=head_only)
            if not authorized:
                return
            self.send_json(200, action_catalog_document(), head_only=head_only)
            return
        if parsed.path in {"/api/v1/settings/backup", "/api/v1/settings/backup/"}:
            authorized, principal = self.access_context(head_only=head_only)
            if not authorized:
                return
            if principal is None:
                self.problem(
                    401,
                    "authentication_required",
                    "A paired PCS device token is required for backup settings.",
                    {"WWW-Authenticate": 'Bearer realm="PCS Stats API", scope="stats:read"'},
                    head_only=head_only,
                )
                return
            if head_only:
                self.send_json(200, {}, head_only=True)
                return
            try:
                document = backup_settings_document(run_backup_settings_helper("show"))
            except ActionError as exc:
                self.problem(503, exc.code, str(exc))
                return
            self.send_json(200, document)
            return
        prefix = "/api/v1/"
        if not parsed.path.startswith(prefix):
            self.problem(404, "not_found", "Not found.", head_only=head_only)
            return
        resource = parsed.path[len(prefix):]
        if resource not in {"status", *RESOURCE_SECTIONS}:
            self.problem(404, "unknown_resource", "Unknown API resource.", head_only=head_only)
            return
        authorized, principal = self.access_context(head_only=head_only)
        if not authorized:
            return
        if head_only:
            self.send_json(200, {}, head_only=True)
            return
        try:
            document = api_document(resource, COLLECTOR.collect("dashboard-public-json"))
            if principal is not None:
                document = add_authenticated_details(
                    document,
                    resource,
                    COLLECTOR.collect("dashboard-json"),
                )
        except CollectionError as exc:
            status_code = 504 if exc.code == "collector_timeout" else 503
            self.problem(status_code, exc.code, str(exc))
            return
        self.send_json(200, document)

    def do_GET(self):
        self.handle_read()

    def do_HEAD(self):
        self.handle_read(head_only=True)

    def do_POST(self):
        parsed = urlsplit(self.path)
        if not API_ENABLED:
            self.problem(404, "not_found", "Not found.")
            return
        if parsed.query:
            self.problem(400, "query_not_supported", "Query parameters are not supported.")
            return
        if parsed.path == "/api/v1/admin/password":
            self.handle_password_change()
            return
        if parsed.path != "/api/v1/pair":
            if parsed.path.startswith("/api/v1/actions/"):
                self.handle_action_post(parsed.path)
                return
            self.problem(405, "method_not_allowed", "This API route does not accept POST.", {"Allow": "GET, HEAD"})
            return
        allowed, retry = PAIR_LIMITER.allow(self.client_address[0])
        if not allowed:
            self.problem(429, "rate_limited", "Too many pairing attempts.", {"Retry-After": str(retry)})
            return
        payload = self.read_json_body(MAX_PAIRING_BODY_BYTES, "invalid_pairing_request")
        if payload is None:
            return
        if not isinstance(payload, dict) or set(payload) != {"admin_password", "device_id"}:
            self.problem(400, "invalid_pairing_request", "Invalid pairing request.")
            return
        admin_password = payload.get("admin_password")
        device_id = payload.get("device_id")
        if (
            not isinstance(admin_password, str)
            or not admin_password
            or len(admin_password) > MAX_ADMIN_PASSWORD_LENGTH
            or not isinstance(device_id, str)
            or DEVICE_ID_PATTERN.fullmatch(device_id) is None
        ):
            self.problem(400, "invalid_pairing_request", "Invalid pairing request.")
            return
        try:
            pairing = pair_device(admin_password, device_id)
        except PairingError as exc:
            status_code = {
                "admin_authentication_failed": 401,
                "device_already_paired": 409,
            }.get(exc.code, 503)
            headers = {"WWW-Authenticate": 'PCS-Admin realm="PCS Pairing"'} if status_code == 401 else None
            self.problem(status_code, exc.code, str(exc), headers)
            return
        self.send_json(
            201,
            {
                "api_version": "v1",
                "schema_version": "1.0",
                "resource": "pairing",
                "access": "authenticated",
                "device_id": pairing["device_id"],
                "token_type": pairing["token_type"],
                "token": pairing["token"],
                "scopes": pairing["scopes"],
            },
        )

    def do_PUT(self):
        parsed = urlsplit(self.path)
        if not API_ENABLED:
            self.problem(404, "not_found", "Not found.")
            return
        if parsed.query:
            self.problem(400, "query_not_supported", "Query parameters are not supported.")
            return
        if parsed.path.rstrip("/") != "/api/v1/settings/backup":
            self.reject_unsupported_method()
            return
        authorized, principal = self.action_context()
        if not authorized:
            return
        payload = self.read_json_body(MAX_BACKUP_SETTINGS_BODY_BYTES, "invalid_backup_settings_request")
        if payload is None:
            return
        if set(payload) != {"enabled", "interval_minutes", "keep_history"}:
            self.problem(400, "invalid_backup_settings_request", "enabled, interval_minutes, and keep_history are required.")
            return
        enabled = payload.get("enabled")
        interval = payload.get("interval_minutes")
        keep_history = payload.get("keep_history")
        if (
            not isinstance(enabled, bool)
            or isinstance(interval, bool)
            or not isinstance(interval, int)
            or not MIN_BACKUP_INTERVAL_MINUTES <= interval <= MAX_BACKUP_INTERVAL_MINUTES
            or not isinstance(keep_history, bool)
        ):
            self.problem(400, "invalid_backup_settings_request", "Backup settings are outside the supported range.")
            return
        request_id = secrets.token_urlsafe(12)
        print(f"AUDIT setting=backup principal={principal['id']} request_id={request_id} state=started", flush=True)
        try:
            value = run_backup_settings_helper(
                "set-from-stdin",
                {"enabled": enabled, "interval_minutes": interval, "keep_history": keep_history},
            )
        except ActionError as exc:
            print(f"AUDIT setting=backup principal={principal['id']} request_id={request_id} state=failed code={exc.code}", flush=True)
            self.problem(503, exc.code, str(exc))
            return
        COLLECTOR.invalidate()
        print(f"AUDIT setting=backup principal={principal['id']} request_id={request_id} state=completed", flush=True)
        document = backup_settings_document(value)
        document["request_id"] = request_id
        self.send_json(200, document)

    def handle_password_change(self) -> None:
        authorized, principal = self.password_context()
        if not authorized:
            return
        payload = self.read_json_body(MAX_PASSWORD_CHANGE_BODY_BYTES, "invalid_password_change_request")
        if payload is None:
            return
        if set(payload) != {"current_password", "new_password"}:
            self.problem(400, "invalid_password_change_request", "Both current_password and new_password are required.")
            return
        current = payload.get("current_password")
        new = payload.get("new_password")
        if (
            not isinstance(current, str) or not isinstance(new, str)
            or not current or not new
            or len(current) > MAX_ADMIN_PASSWORD_LENGTH
            or len(new) < MIN_ADMIN_PASSWORD_LENGTH
            or len(new) > MAX_ADMIN_PASSWORD_LENGTH
        ):
            self.problem(400, "invalid_password_change_request", "Invalid password values.")
            return
        try:
            change_admin_password(current, new)
        except PairingError as exc:
            status = {
                "current_password_incorrect": 401,
                "password_change_rejected": 409,
            }.get(exc.code, 503)
            headers = {"WWW-Authenticate": 'PCS-Admin realm="Password Change"'} if status == 401 else None
            self.problem(status, exc.code, str(exc), headers)
            return
        self.send_json(200, {
            "api_version": "v1",
            "schema_version": "1.0",
            "resource": "password-change",
            "changed": True,
        })

    def handle_action_post(self, path: str) -> None:
        suffix = path[len("/api/v1/actions/"):]
        challenge_request = suffix.endswith("/challenge")
        action = suffix[:-len("/challenge")] if challenge_request else suffix
        if not action or "/" in action or action not in ACTION_MAP:
            self.problem(404, "unknown_action", "Unknown administrative action.")
            return
        authorized, principal = self.action_context()
        if not authorized:
            return
        payload = self.read_json_body(MAX_ACTION_BODY_BYTES, "invalid_action_request")
        if payload is None:
            return
        if challenge_request:
            if action not in DANGEROUS_ACTIONS or payload:
                self.problem(400, "invalid_action_request", "This action challenge request must be an empty JSON object.")
                return
            nonce, expires_in = ACTION_CHALLENGES.issue(principal["id"], action)
            self.send_json(201, {
                "api_version": "v1",
                "schema_version": "1.0",
                "resource": "action-challenge",
                "action": action,
                "challenge": nonce,
                "expires_in": expires_in,
                "confirmation": action,
            })
            return
        if action in DANGEROUS_ACTIONS:
            if set(payload) != {"confirmation", "challenge"}:
                self.problem(400, "confirmation_required", "This action requires its confirmation and a fresh challenge.")
                return
            confirmation = payload.get("confirmation")
            challenge = payload.get("challenge")
            if (
                confirmation != action
                or not isinstance(challenge, str)
                or not ACTION_CHALLENGES.consume(principal["id"], action, challenge)
            ):
                self.problem(409, "invalid_or_expired_challenge", "The action challenge is invalid, expired, or already used.")
                return
        elif payload:
            self.problem(400, "invalid_action_request", "This action request must be an empty JSON object.")
            return
        request_id = secrets.token_urlsafe(12)
        print(f"AUDIT action={action} principal={principal['id']} request_id={request_id} state=started", flush=True)
        try:
            result = ACTION_RUNNER.run(action)
        except ActionError as exc:
            status_code = {"action_busy": 409, "action_timeout": 504}.get(exc.code, 503)
            print(f"AUDIT action={action} principal={principal['id']} request_id={request_id} state=failed code={exc.code}", flush=True)
            self.problem(status_code, exc.code, str(exc))
            return
        print(
            f"AUDIT action={action} principal={principal['id']} request_id={request_id} "
            f"state=completed exit_code={result['exit_code']}",
            flush=True,
        )
        self.send_json(200, {
            "api_version": "v1",
            "schema_version": "1.0",
            "resource": "action-result",
            "action": action,
            "request_id": request_id,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            **result,
        })

    def allowed_methods(self) -> str:
        path = urlsplit(self.path).path.rstrip("/")
        if path == "/api/v1/settings/backup":
            return "GET, HEAD, PUT"
        if path in {"/api/v1/pair", "/api/v1/admin/password"}:
            return "POST"
        if path.startswith("/api/v1/actions/"):
            return "POST"
        return "GET, HEAD"

    def reject_unsupported_method(self):
        self.problem(
            405,
            "method_not_allowed",
            "This API route does not accept that HTTP method.",
            {"Allow": self.allowed_methods()},
        )

    do_PATCH = reject_unsupported_method
    do_DELETE = reject_unsupported_method
    do_OPTIONS = reject_unsupported_method
    do_TRACE = reject_unsupported_method
    do_CONNECT = reject_unsupported_method

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PCS HTTPS statistics and administration API")
    parser.add_argument("--cert-file", required=True, help="TLS certificate chain")
    parser.add_argument("--key-file", required=True, help="TLS private key")
    args = parser.parse_args()
    if not API_ENABLED:
        parser.error("PCS_API_ENABLED=yes is required")
    if not os.path.isfile(args.cert_file) or not os.path.isfile(args.key_file):
        parser.error("TLS certificate and key files must exist")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert_file, args.key_file)
    server = ReusableThreadingHTTPServer((HOST, PORT), Handler)
    server.tls_context = context
    mode = "public and authenticated" if TOKENS.configured() else "public-only"
    print(f"PCS Stats API listening with TLS on {HOST}:{PORT} ({mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
