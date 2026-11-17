#!/usr/bin/env python3

import argparse
import base64
import datetime as dt
import getpass
import hashlib
import hmac
import html
import ipaddress
import json
import math
import re
import os
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOST = os.environ.get("PCS_CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PCS_CONTROL_PORT", "8081"))
TRUST_PROXY = os.environ.get("PCS_CONTROL_TRUST_PROXY", "0") == "1"
DISPATCHER = os.environ.get("PCS_WEB_ACTION", "/usr/local/sbin/pcs-web-action")
PASSWORD_HELPER = os.environ.get(
    "PCS_ADMIN_PASSWORD_HELPER",
    "/usr/local/sbin/pcs-admin-password-helper",
)
BACKUP_CONFIG_HELPER = os.environ.get(
    "PCS_BACKUP_CONFIG_HELPER",
    "/usr/local/sbin/pcs-backup-config",
)
CREDENTIAL_FILE = os.environ.get(
    "PCS_ADMIN_CREDENTIAL_FILE",
    "/etc/pcs-control-panel/admin.json",
)
SESSION_KEY_FILE = os.environ.get(
    "PCS_SESSION_KEY_FILE",
    "/etc/pcs-control-panel/session.key",
)
SESSION_TTL = int(os.environ.get("PCS_SESSION_TTL", "1800"))
COOKIE_SECURE = os.environ.get("PCS_COOKIE_SECURE", "no").lower() == "yes"
PASSWORD_ITERATIONS = 310_000
MAX_FORM_BYTES = 64 * 1024
MAX_PASSWORD_LENGTH = 1024
MIN_BACKUP_INTERVAL_MINUTES = 1
MAX_BACKUP_INTERVAL_MINUTES = 43_200

ACTIONS = [
    ('starlink-status', 'Starlink Telemetry', 'Read cached Mini diagnostics.'),
    ('starlink-reboot', 'Reboot Starlink Mini', 'Requires an explicitly paired and armed Mini; interrupts its Internet connection.'),
    ('starlink-shutdown', 'Starlink Shutdown (staged)', 'Not available until DC power switching is commissioned; does not stow or sleep the Mini.'),

    ("status", "View PCS Status", "Show the full PCS status report."),
    ("self-test", "Run Self-Test", "Run the Pi-side health validation."),
    ("storage-status", "View Storage", "Show USB, SD backup, and Samba state."),
    ("wifi-status", "View Wi-Fi", "Show Wi-Fi uplink status and the default route."),
    ("wifi-connect", "Connect Wi-Fi", "Connect to the strongest visible saved Wi-Fi network."),
    ("wifi-disconnect", "Disable Wi-Fi Radio", "Turn off Pi Wi-Fi for offline or cellular-only operation."),
    ("cellular-status", "View Cellular", "Show WWAN modem and cellular connection state."),
    ("cellular-connect", "Connect Cellular", "Bring up cellular data without changing the fallback policy."),
    ("cellular-disconnect", "Disconnect Cellular", "Disconnect cellular and pause automatic cellular activation until Connect Cellular or operator resume."),
    ("cellular-test", "Test Cellular", "Test cellular-only internet through the WWAN interface."),
    ("meshtastic-status", "View Meshtastic", "Show privacy-safe node, mesh, MQTT, GPSD, and environment status."),
    ("restart-meshtastic", "Restart Meshtastic", "Reconnect the Meshtastic radio transport and MQTT gateway."),
    ("aprs-mailbox-read", "Mark APRS Mailbox Read", "Clear the unread APRS mailbox notification without deleting messages."),
    ("sync-backup", "Sync USB to SD Backup", "Mirror the USB primary share to the SD backup."),
    ("mount-usb", "Mount USB Storage", "Mount the USB primary share and restart Samba."),
    ("mount-new-usb", "Mount New USB Storage", "Configure a newly attached USB device as primary storage."),
    ("safe-unmount-usb", "Unmount USB Safely", "Sync, stop Samba, unmount USB, and restart Samba."),
    ("restart-services", "Restart PCS Services", "Restart core PCS services through systemd."),
    ("restart-samba", "Restart Samba", "Restart Samba only."),
    ("restart-modemmanager", "Restart ModemManager", "Restart modem detection and reassert GPS."),
    ("sync-time", "Sync Time Now", "Poll time sources, step the clock, and update the RTC."),
    ("restart-chrony", "Restart Chrony", "Restart Chrony only."),
    ("restart-gpsd", "Restart GPSD", "Reassert WWAN NMEA mode and restart gpsd."),
    ("restart-logs", "View Restart Logs", "Show recent PCS restart service logs."),
    ("buzzer-mute", "Mute Audible Warnings", "Mute repeating WARN/BAD sounds without changing visual status; low voltage remains audible."),
    ("buzzer-unmute", "Enable Audible Warnings", "Re-enable repeating WARN/BAD sounds."),
    ("reboot-system", "Reboot PCS", "Restart PCS and request a Mini reboot when explicitly paired."),
    ("shutdown-system", "Shutdown PCS", "Shut down Pi-Star when paired, run the optional Mini follow hook, then power off PCS. Mini power removal is not yet supported."),
]

ACTION_MAP = {name: (label, desc) for name, label, desc in ACTIONS}
ACTION_CONFIRMS = {
    "starlink-reboot": "Reboot the paired Mini? Its Internet connection will be interrupted.",
    "starlink-shutdown": "Starlink power-off is staged and currently unsupported. Check its status?",
    "mount-new-usb": "Configure the attached USB device as PCS primary storage?",
    "safe-unmount-usb": "Sync the backup and safely unmount PCS USB storage?",
    "restart-meshtastic": "Restart the Meshtastic radio and MQTT gateway now?",
    "aprs-mailbox-read": "Mark every stored APRS mailbox message as read? Messages will be retained.",
    "reboot-system": "Reboot PCS now? The administration page will disconnect during restart.",
    "shutdown-system": "Shutdown PCS now? Physical access is required to power it back on.",
}
ACTION_GROUPS = [
    ("Starlink", ["starlink-status", "starlink-reboot", "starlink-shutdown"]),
    ("Health", ["status", "self-test", "storage-status", "restart-logs"]),
    ("Network", ["wifi-status", "wifi-connect", "wifi-disconnect"]),
    ("Cellular", ["cellular-status", "cellular-connect", "cellular-disconnect", "cellular-test"]),
    ("Communications", ["restart-meshtastic", "aprs-mailbox-read"]),
    ("Storage", ["sync-backup", "mount-usb", "mount-new-usb", "safe-unmount-usb"]),
    ("Services", ["restart-services", "restart-samba", "restart-modemmanager"]),
    ("Time / GPS", ["sync-time", "restart-chrony", "restart-gpsd"]),
    ("Power", ["buzzer-mute", "buzzer-unmute", "reboot-system", "shutdown-system"]),
]
DANGEROUS_ACTIONS = {
    "starlink-reboot", "starlink-shutdown",
    "mount-new-usb",
    "safe-unmount-usb",
    "restart-services",
    "restart-samba",
    "restart-modemmanager",
    "restart-chrony",
    "restart-gpsd",
    "restart-meshtastic",
    "aprs-mailbox-read",
    "reboot-system",
    "shutdown-system",
}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def status_value(value) -> str:
    value = str(value or "warn").lower()
    return value if value in {"ok", "warn", "bad"} else "warn"


def status_label(value) -> str:
    return {"ok": "OK", "warn": "WARN", "bad": "BAD"}[status_value(value)]


def yes_no(value) -> str:
    return "Yes" if bool(value) else "No"


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def make_password_record(password: str, iterations: int = PASSWORD_ITERATIONS) -> dict:
    if len(password) < 12:
        raise ValueError("Admin password must be at least 12 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "version": 1,
        "algorithm": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": b64encode(salt),
        "password_hash": b64encode(digest),
    }


def verify_password_record(password: str, record: dict) -> bool:
    try:
        if record.get("algorithm") != "pbkdf2-sha256":
            return False
        iterations = int(record["iterations"])
        salt = b64decode(record["salt"])
        expected = b64decode(record["password_hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def write_password_record(path: str, password: str) -> None:
    record = make_password_record(password)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o750, exist_ok=True)
    try:
        previous_owner = os.stat(path)
    except OSError:
        previous_owner = None
    temporary = f"{path}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(record, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o640)
        if previous_owner is not None and hasattr(os, "chown"):
            os.chown(path, previous_owner.st_uid, previous_owner.st_gid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AuthManager:
    def __init__(self, credential_file: str):
        self.credential_file = credential_file

    def configured(self) -> bool:
        return os.path.isfile(self.credential_file)

    def verify(self, password: str) -> bool:
        try:
            with open(self.credential_file, "r", encoding="utf-8") as source:
                record = json.load(source)
        except (OSError, ValueError):
            return False
        return verify_password_record(password, record)


def load_session_key(path: str) -> bytes:
    try:
        with open(path, "rb") as source:
            key = source.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    # This fallback keeps the public page available but invalidates sessions
    # whenever the process restarts. The installer creates the persistent key.
    return secrets.token_bytes(32)


class SessionStore:
    def __init__(self, signing_key: bytes, ttl: int = SESSION_TTL):
        self.signing_key = signing_key
        self.ttl = ttl
        self.sessions = {}
        self.lock = threading.Lock()

    def _signature(self, session_id: str, expires: int) -> str:
        payload = f"{session_id}.{expires}".encode("ascii")
        return b64encode(hmac.new(self.signing_key, payload, hashlib.sha256).digest())

    def create(self) -> tuple[str, dict]:
        now = int(time.time())
        session_id = secrets.token_urlsafe(32)
        session = {
            "expires": now + self.ttl,
            "csrf": secrets.token_urlsafe(32),
        }
        with self.lock:
            self._purge_locked(now)
            self.sessions[session_id] = session
        cookie = f"{session_id}.{session['expires']}.{self._signature(session_id, session['expires'])}"
        return cookie, session

    def validate(self, cookie: str | None) -> tuple[str | None, dict | None]:
        if not cookie:
            return None, None
        try:
            session_id, raw_expires, signature = cookie.rsplit(".", 2)
            expires = int(raw_expires)
        except (TypeError, ValueError):
            return None, None
        if not hmac.compare_digest(signature, self._signature(session_id, expires)):
            return None, None
        now = int(time.time())
        with self.lock:
            self._purge_locked(now)
            session = self.sessions.get(session_id)
            if not session or session.get("expires") != expires or expires <= now:
                return None, None
            return session_id, dict(session)

    def destroy(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self.lock:
            self.sessions.pop(session_id, None)

    def destroy_all(self) -> None:
        with self.lock:
            self.sessions.clear()

    def _purge_locked(self, now: int) -> None:
        expired = [key for key, value in self.sessions.items() if value.get("expires", 0) <= now]
        for key in expired:
            self.sessions.pop(key, None)


class LoginLimiter:
    def __init__(self, limit: int = 5, window: int = 300, lockout: int = 60):
        self.limit = limit
        self.window = window
        self.lockout = lockout
        self.failures = {}
        self.lock = threading.Lock()

    def allowed(self, address: str) -> bool:
        now = time.time()
        with self.lock:
            attempts = [stamp for stamp in self.failures.get(address, []) if now - stamp < self.window]
            self.failures[address] = attempts
            return len(attempts) < self.limit or now - attempts[-1] >= self.lockout

    def failed(self, address: str) -> None:
        with self.lock:
            self.failures.setdefault(address, []).append(time.time())

    def succeeded(self, address: str) -> None:
        with self.lock:
            self.failures.pop(address, None)


class TimedCache:
    def __init__(self, ttl: int = 10):
        self.ttl = ttl
        self.value = None
        self.created = 0.0
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if self.value is not None and time.time() - self.created < self.ttl:
                return self.value
        return None

    def set(self, value):
        with self.lock:
            self.value = value
            self.created = time.time()


AUTH = AuthManager(CREDENTIAL_FILE)
SESSIONS = SessionStore(load_session_key(SESSION_KEY_FILE))
LOGIN_LIMITER = LoginLimiter()
PUBLIC_CACHE = TimedCache(ttl=1)

PUBLIC_FIELDS = {
    "starlink": {'available', 'downlink_bps', 'uplink_bps', 'latency_ms', 'obstructed', 'sample_age_seconds', 'state', 'status', 'alerts_summary', 'configured', 'obstruction_percent', 'packet_loss_percent', 'uptime_seconds'},
    "system": {"status", "uptime", "local_time", "cpu_temperature", "cpu_load", "memory_used", "root_storage_used"},
    "network": {"status", "offline", "lan_gateway", "openwrt_online", "openwrt_url", "internet_available", "ip_internet_available", "dns_available", "uplink_type", "connected_client_count", "ap_client_count", "uplinks", "usage", "usage_summary"},
    "remote_management": {"configured", "status", "connection", "management_address", "boot_enabled", "firewall_active", "latest_handshake"},
    "cellular": {"status", "modem_present", "connected", "carrier", "access_technology", "signal", "fallback_policy", "fallback_active"},
    "time": {"status", "chrony_active", "synchronized", "source", "reference"},
    "gnss": {"status", "receiver_active", "fix", "satellites", "coordinates", "grid_square", "utc_time", "receiver"},
    "storage": {
        "status", "usb_mounted", "primary_share_available", "backup_share_available",
        "usb_free_gb", "backup_free_gb", "backup_status", "last_backup_age",
        "automatic_backup_enabled", "backup_interval_minutes",
        "keep_backup_history", "backup_history_count",
    },
    "services": {"status", "homepage_available", "file_sharing_available", "cockpit_available", "gpsd_lan_enabled"},
    "power": {
        "dc_source", "battery_capacity_wh", "battery_remaining_wh", "battery_remaining_percent", "battery_capacity_warning",
        "total_power", "total_charge_since_boot_mah", "total_energy_since_boot_wh",
        "configured", "status", "input_online", "input_voltage",
        "input_current", "input_power", "rail_5v_online",
        "input_charge_since_boot_mah", "input_energy_since_boot_wh",
        "rail_5v_voltage", "rail_5v_current", "rail_5v_power",
        "rail_5v_charge_since_boot_mah", "rail_5v_energy_since_boot_wh",
        "rail_12v_online", "rail_12v_voltage", "rail_12v_current", "rail_12v_power", "rail_12v_distribution_power",
        "starlink_configured", "starlink_online", "starlink_voltage", "starlink_current", "starlink_power",
        "rail_12v_charge_since_boot_mah", "rail_12v_energy_since_boot_wh", "rail_12v_distribution_energy_since_boot_wh",
        "starlink_charge_since_boot_mah", "starlink_energy_since_boot_wh",
        "estimated_non_5v_power", "low_voltage_active",
        "energy_tracking_elapsed_seconds", "shutdown_armed", "shutdown_remaining_seconds",
    },
    "pistar": {"configured", "online", "url"},
    "aprs": {
        "configured", "status", "service", "callsign", "role", "frequency", "modem",
        "aprs_is", "aprs_is_profile", "beacon", "digipeater", "kiss", "fx25", "packets",
        "last_heard", "tx_state", "engine", "graywolf_url", "graywolf_gain",
        "graywolf_tx_timing", "graywolf_igate", "graywolf_beacon_path",
        "agent_enabled", "agent_status", "agent_connection",
        "agent_packets_received", "agent_messages_received", "mailbox_messages",
        "mailbox_unread", "last_mailbox_message",
    },
    "meshtastic": {
        "configured", "status", "service", "node", "hardware", "firmware",
        "transport", "radio_link", "mqtt", "broker", "downlink_filters",
        "mqtt_policy", "rf_igate", "map_reporting",
        "mqtt_activity", "map_mqtt", "mesh_activity", "remote_nodes", "last_heard",
        "gpsd_position", "case_environment", "utilization", "power",
    },
}


def run_dispatcher(action: str, timeout: int = 300) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["sudo", "-n", DISPATCHER, action],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        output = result.stdout or ""
        if result.stderr:
            output += "\n--- STDERR ---\n" + result.stderr
        return result.returncode, output
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") + "\nERROR: action timed out.\n"
    except Exception as exc:
        return 1, f"ERROR: {exc}\n"


def change_admin_password(current_password: str, new_password: str) -> tuple[bool, str]:
    payload = json.dumps({
        "current_password": current_password,
        "new_password": new_password,
    })
    try:
        result = subprocess.run(
            ["sudo", "-n", PASSWORD_HELPER, "--change-from-stdin"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "Password update helper was unavailable."
    if result.returncode != 0:
        return False, "Password update was rejected."
    return True, "Password updated. Sign in again with the new password."


def load_backup_settings() -> tuple[dict, str]:
    try:
        result = subprocess.run(
            ["sudo", "-n", BACKUP_CONFIG_HELPER, "show"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else None
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
            raise ValueError("invalid helper response")
        return value, ""
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return {"version": 2, "enabled": False, "interval_minutes": 10, "keep_history": False}, "Automatic backup settings are unavailable."


def update_backup_settings(enabled: bool, interval_minutes: int, keep_history: bool) -> tuple[bool, str]:
    payload = json.dumps({
        "enabled": enabled,
        "interval_minutes": interval_minutes,
        "keep_history": keep_history,
    })
    try:
        result = subprocess.run(
            ["sudo", "-n", BACKUP_CONFIG_HELPER, "set-from-stdin"],
            input=payload,
            text=True,
            capture_output=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "Automatic backup settings helper was unavailable."
    if result.returncode != 0:
        return False, "Automatic backup settings were rejected; the previous configuration was preserved."
    PUBLIC_CACHE.set(None)
    return True, "Automatic backup settings updated."


def dashboard_error(message: str, public: bool) -> dict:
    if public:
        return {
            "generated_at": "unknown",
            "overall": "bad",
            "offline": False,
            "alerts": [{"severity": "bad", "component": "Dashboard", "message": message}],
            "error": message,
            "system": {"status": "bad"},
            "network": {"status": "warn", "lan_gateway": "10.42.0.1"},
            "remote_management": {"configured": False, "status": "warn"},
            "cellular": {"status": "warn"},
            "time": {"status": "warn"},
            "gnss": {"status": "warn"},
            "storage": {"status": "warn"},
            "services": {"status": "warn"},
            "power": {"configured": False},
            "pistar": {"configured": False},
            "aprs": {"configured": False},
            "meshtastic": {"configured": False},
        }
    return {
        "generated_at": "unknown",
        "overall": "bad",
        "offline": False,
        "alerts": [{"severity": "bad", "component": "Dashboard", "message": message}],
        "cards": [{
            "id": "dashboard",
            "title": "Dashboard",
            "status": "bad",
            "summary": message,
            "items": [],
        }],
        "client_info": {},
    }


def sanitize_public_dashboard(data: dict) -> dict:
    sanitized = {
        "generated_at": data.get("generated_at", "unknown"),
        "overall": status_value(data.get("overall", "warn")),
        "offline": bool(data.get("offline", False)),
        "alerts": sanitize_alerts(data.get("alerts", [])),
    }
    if data.get("error"):
        sanitized["error"] = str(data["error"])
    for section_name, allowed_fields in PUBLIC_FIELDS.items():
        source = data.get(section_name, {})
        if not isinstance(source, dict):
            source = {}
        sanitized[section_name] = {
            key: source[key]
            for key in allowed_fields
            if key in source
        }
    gnss = sanitized.get("gnss", {})
    if "receiver" in gnss:
        gnss["receiver"] = sanitize_gnss_receiver(gnss["receiver"])
        if gnss["receiver"].get("mode") in {"neo-f10n", "invalid"}:
            gnss["coordinates"] = "Coordinates hidden"
    network = sanitized.get("network", {})
    if "uplinks" in network:
        fields = {"id", "name", "type", "priority", "link", "address", "address6", "internet", "internet6", "state", "active", "selected", "selected6"}
        rows = network["uplinks"] if isinstance(network["uplinks"], list) else []
        network["uplinks"] = [{**{k: v for k, v in row.items() if k in fields and isinstance(v, (str, int, bool, type(None)))}, "usage": sanitize_wan_usage(row.get("usage"))} for row in rows[:32] if isinstance(row, dict)]
    if "usage" in network:
        network["usage"] = sanitize_wan_usage(network["usage"])
    return sanitized


def sanitize_gnss_receiver(value):
    # An explicit scalar projection, never an arbitrary nested receiver report.
    fields = {"version", "mode", "model", "connected", "usb_connected", "fix_valid", "utc_valid", "satellites_visible",
              "signals_reported", "signal_age_seconds", "timeutc_age_seconds", "tim_tp_age_seconds",
              "satellites_used", "pps_present", "pps_active", "pps_age_seconds", "pps_validation",
              "source", "synchronized", "selected_ref", "pps_selection", "offset_seconds",
              "uncertainty_seconds", "diagnostic", "antenna_path", "state"}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in fields
            and type(item) in (str, int, float, bool, type(None))
            and (not isinstance(item, str) or len(item) <= 160)
            and (not isinstance(item, float) or math.isfinite(item))}


def sanitize_wan_usage(value):
    if not isinstance(value, dict):
        return None
    result = {k: v for k, v in value.items() if k in {"rx_bytes", "tx_bytes", "total_bytes"} and type(v) is int and v >= 0}
    if "partial" in value:
        result["partial"] = value["partial"] is True
    return result


def sanitize_alerts(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    alerts = []
    for entry in value[:32]:
        if not isinstance(entry, dict):
            continue
        severity = status_value(entry.get("severity"))
        component = str(entry.get("component", "System"))[:80]
        message = str(entry.get("message", "Needs attention"))[:240]
        alerts.append({"severity": severity, "component": component, "message": message})
    return alerts


def load_dashboard(action: str, public: bool, timeout: int = 75) -> dict:
    code, output = run_dispatcher(action, timeout=timeout)
    if code != 0:
        return dashboard_error(f"Status collection failed (exit {code}).", public)
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            return dashboard_error("Status data had the wrong shape.", public)
        return sanitize_public_dashboard(data) if public else data
    except json.JSONDecodeError:
        return dashboard_error("Status data was invalid.", public)


def get_public_dashboard() -> dict:
    cached = PUBLIC_CACHE.get()
    if cached is not None:
        return cached
    dashboard = load_dashboard("dashboard-public-json", public=True)
    PUBLIC_CACHE.set(dashboard)
    return dashboard


def get_admin_dashboard() -> dict:
    return load_dashboard("dashboard-json", public=False)


def run_action(action: str) -> tuple[int, str]:
    if action not in ACTION_MAP:
        return 2, f"Unknown action: {action}\n"
    return run_dispatcher(action)


BASE_CSS = """
:root{color-scheme:dark;--bg:#0b1015;--panel:#141b22;--panel2:#1b2530;--line:#2b3947;--text:#eef5fa;--muted:#a8b5c0;--accent:#68b7ff;--ok:#61d69b;--warn:#f2c55c;--bad:#f17777}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top right,#162536 0,#0b1015 34rem);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}
a{color:var(--accent)}header{position:sticky;top:0;z-index:10;background:rgba(11,16,21,.94);border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}
.nav{max-width:1450px;margin:auto;padding:.85rem 1.1rem;display:flex;align-items:center;justify-content:space-between;gap:1rem}.brand-group{display:flex;align-items:center;gap:.7rem;min-width:0}.brand{font-weight:850;letter-spacing:.02em;white-space:nowrap}.header-health{display:flex;align-items:center;gap:.45rem;min-width:0}.header-alerts{display:flex;align-items:center;gap:.4rem;min-width:0;overflow:hidden}.header-alert{font-size:.78rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:34rem}.header-alert.warn{color:var(--warn)}.header-alert.bad{color:var(--bad)}.navlinks{display:flex;gap:.55rem;align-items:center}
main{max-width:1450px;margin:auto;padding:1.2rem}.admin-main{max-width:1600px}.hero{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(260px,.65fr);gap:1rem;margin-bottom:1rem}
.hero-main,.admin-entry,.overview,.card,.action-group,.output,.login-panel{background:rgba(20,27,34,.96);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 30px rgba(0,0,0,.14)}
.hero-main{padding:1.5rem}.eyebrow{margin:0 0 .35rem;color:var(--accent);font-size:.78rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.hero h1{font-size:clamp(1.8rem,5vw,3.3rem);line-height:1.05;margin:.1rem 0 .7rem}.hero p{color:var(--muted);max-width:62ch}
.admin-entry{padding:1.25rem;display:flex;flex-direction:column;justify-content:center}.admin-entry h2{margin:0 0 .45rem}.admin-entry p{margin:.1rem 0 1rem;color:var(--muted)}
.button,button{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:9px;background:var(--accent);color:#06121c;padding:.72rem .95rem;font:inherit;font-weight:850;text-decoration:none;cursor:pointer}.button.secondary,button.secondary{background:var(--panel2);border:1px solid var(--line);color:var(--text)}button.danger{background:var(--bad);color:#240707}.button:hover,button:hover{filter:brightness(1.08)}
.overview{padding:1rem 1.1rem;display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-bottom:1rem}.overview h2{margin:0}.overview p{margin:.25rem 0 0;color:var(--muted)}
.badge{display:inline-flex;align-items:center;justify-content:center;min-width:4.2rem;padding:.42rem .65rem;border-radius:999px;font-size:.78rem;font-weight:900}.badge.ok{background:rgba(97,214,155,.14);color:var(--ok)}.badge.warn{background:rgba(242,197,92,.15);color:var(--warn)}.badge.bad{background:rgba(241,119,119,.15);color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:.85rem}.admin-grid{grid-template-columns:repeat(auto-fit,minmax(250px,1fr));margin-bottom:1.25rem}.card{padding:1rem}.card-top{display:flex;align-items:center;justify-content:space-between;gap:.7rem}.card h2,.card h3{margin:0;font-size:1.05rem}.summary{color:var(--muted);margin:.55rem 0 .7rem}.item{display:flex;justify-content:space-between;gap:1rem;border-top:1px solid rgba(255,255,255,.07);padding:.48rem 0}.item span:first-child{color:var(--muted)}.item span:last-child{text-align:right;overflow-wrap:anywhere}.item code{overflow-wrap:anywhere}.metric{margin:.65rem 0}.metric-row{display:flex;justify-content:space-between;color:var(--muted);font-size:.9rem}.bar{height:.55rem;border-radius:99px;background:#090d11;overflow:hidden;margin-top:.25rem}.bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--ok))}
.section-title{margin:1.5rem 0 .75rem}.service-grid{margin-top:.85rem}.service-card a{display:block;margin-top:.7rem}.notice{border-left:4px solid var(--warn);padding:.75rem 1rem;background:rgba(242,197,92,.08);margin:1rem 0;border-radius:7px}.error{border-left-color:var(--bad);background:rgba(241,119,119,.08)}.success{border-left-color:var(--ok);background:rgba(97,214,155,.08)}.password-card{display:flex;align-items:center;justify-content:space-between;gap:1rem}.password-card h2{margin:0 0 .35rem}.password-card p{margin:0;color:var(--muted);max-width:78ch}.password-card .button{flex:none}
.action-group{padding:1rem;margin-bottom:.85rem}.action-group h3{margin:0 0 .7rem;color:var(--muted);font-size:.86rem;text-transform:uppercase;letter-spacing:.09em}.action-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.75rem}.action-card{border:1px solid var(--line);background:rgba(255,255,255,.025);padding:.8rem;border-radius:10px}.action-card button{width:100%}.action-card p{color:var(--muted);margin:.6rem 0 0;font-size:.9rem}
.output{padding:1rem;margin-top:1rem}.output pre{background:#080c10;border-radius:9px;padding:1rem;white-space:pre-wrap;word-break:break-word;max-height:52vh;overflow:auto}.login-wrap{max-width:500px;margin:7vh auto}.login-panel{padding:1.4rem}.login-panel label{display:block;margin:.9rem 0 .35rem}.login-panel input{width:100%;padding:.75rem;background:#090e13;color:var(--text);border:1px solid var(--line);border-radius:8px;font:inherit}.login-panel button{width:100%;margin-top:1rem}.settings-form{display:grid;grid-template-columns:1fr 1fr;align-items:end;gap:1rem}.settings-form label{display:grid;gap:.4rem;color:var(--muted)}.settings-form input[type=number]{padding:.68rem;background:#090e13;color:var(--text);border:1px solid var(--line);border-radius:8px;font:inherit}.check-line{display:flex!important;justify-content:flex-start;align-items:center;gap:.55rem}.check-line input{width:1.15rem;height:1.15rem}.small{font-size:.86rem;color:var(--muted)}dialog{width:min(680px,calc(100vw - 2rem));color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.25rem;box-shadow:0 24px 80px rgba(0,0,0,.65)}dialog::backdrop{background:rgba(0,0,0,.72);backdrop-filter:blur(3px)}.dialog-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1rem}.dialog-head h2{margin:0}
.logout{display:inline}.logout button{padding:.5rem .7rem;background:transparent;border:1px solid var(--line);color:var(--text)}footer{padding:2rem 1rem;text-align:center;color:var(--muted)}
@media(min-width:1300px){.public-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:1500px){.admin-grid{grid-template-columns:repeat(6,minmax(0,1fr))}}
@media(max-width:760px){.hero{grid-template-columns:1fr}.nav{align-items:flex-start}.brand-group{align-items:flex-start;flex-direction:column;gap:.35rem}.header-health{align-items:flex-start}.header-alerts{display:block;max-width:52vw}.header-alert{display:block}.navlinks{flex-wrap:wrap;justify-content:flex-end}main{padding:.85rem}.item{display:grid;gap:.2rem}.item span:last-child{text-align:left}.password-card{align-items:stretch;flex-direction:column}.settings-form{grid-template-columns:1fr}}

/* PCS modernization: local, responsive presentation only. */
:root{--bg:#090f17;--panel:#131d29;--panel2:#172331;--line:#293749;--accent:#70d8d1;--muted:#a5b6c7}
:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.skip{position:absolute;left:12px;top:-100px;z-index:100}.skip:focus{top:12px}
.workspace{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:100vh}
.workspace-sidebar{padding:26px 18px;background:#0d1520;border-right:1px solid var(--line)}
.workspace-brand{display:block;font-size:25px;font-weight:850;text-decoration:none;margin-bottom:38px}.workspace-brand span{display:block;color:var(--muted);font-size:10px;letter-spacing:.18em}
.workspace-nav{display:flex;flex-direction:column;gap:6px}.workspace-nav a{padding:12px;border:1px solid transparent;border-radius:9px;text-decoration:none;color:var(--muted);min-height:44px}
.workspace-nav a:hover,.workspace-nav a[aria-current=page]{background:#1a333e;border-color:#2c5d63;color:#9af3e8}
[hidden]{display:none!important}.workspace-content{min-width:0}.workspace header{position:static}.workspace .nav{flex-wrap:wrap}.workspace .header-alerts{display:none}
.workspace main{padding:24px 32px}.workspace h1{font-size:28px;margin:0}.workspace .overview{align-items:flex-start}.workspace .overview ul{padding-left:20px;color:var(--warn)}
.workspace .admin-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.workspace .kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:20px}.kpi p{overflow-wrap:anywhere}
.workspace-view>.card,.workspace-view>details{margin-bottom:18px}.quicklinks{display:flex;flex-wrap:wrap;gap:16px;margin:15px 0}.quicklinks a{min-height:44px;display:inline-flex;align-items:center}
.workspace .output{margin:0 0 20px}.workspace .output h2{font-size:16px}.workspace .output pre{max-height:250px}.workspace .action-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.action-card .eyebrow{display:block;font-size:10px;margin-bottom:9px}
details{border:1px solid var(--line);padding:14px;border-radius:12px}summary{cursor:pointer;font-weight:650;min-height:30px}.workspace details .grid{margin-top:15px}
input,select{max-width:100%;min-height:44px}select{background:var(--bg);color:var(--text);padding:10px;border:1px solid var(--line);border-radius:8px}
.card,.action-card,.grid>*{min-width:0}p,code,.summary{overflow-wrap:anywhere}.nav,.navlinks{flex-wrap:wrap}.navlinks>a{min-height:44px;display:inline-flex;align-items:center}
@media(max-width:1100px){.workspace{grid-template-columns:185px minmax(0,1fr)}.workspace .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.workspace main{padding:20px}}
@media(max-width:760px){.workspace{display:block}.workspace-sidebar{padding:10px 12px;position:static;border-bottom:1px solid var(--line)}.workspace-brand,.workspace-sidebar>.eyebrow,.workspace-sidebar>.small{display:none}.workspace-nav{flex-direction:row;overflow-x:auto;white-space:nowrap}.workspace-nav a{padding:10px}.workspace main{padding:14px}.workspace .admin-grid,.workspace .action-grid{grid-template-columns:1fr}.workspace .overview{display:block}.workspace .badge{margin-top:10px}.workspace .nav{gap:8px}.workspace .navlinks{justify-content:flex-start}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}

"""


def document(title: str, body: str, script: str = "", nonce: str = "", public_theme: bool = False, footer: str = "PCS local field network interface", workspace_theme: bool = False) -> bytes:
    theme_html = '<link rel="stylesheet" href="/assets/css/pcs.css">' if public_theme or workspace_theme else ""
    body_class = ' class="public-status"' if public_theme else ""
    script_html = f'<script nonce="{esc(nonce)}">{script}</script>' if script else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{BASE_CSS}</style>{theme_html}</head>
<body{body_class}>{body}{script_html}<footer>{footer}</footer></body></html>""".encode("utf-8")


def badge(status: str, label: str | None = None) -> str:
    normalized = status_value(status)
    return f'<span class="badge {normalized}">{esc(label or status_label(normalized))}</span>'


def overall_badge(data: dict) -> str:
    overall = status_value(data.get("overall", "warn"))
    label = status_label(overall)
    if data.get("offline") and overall != "bad":
        label = f"{label} - Offline"
    return badge(overall, label)


def header_health(data: dict) -> str:
    alerts = sanitize_alerts(data.get("alerts", []))
    shown = alerts[:3]
    alert_html = "".join(
        f'<span class="header-alert {entry["severity"]}">{esc(entry["component"])}: {esc(entry["message"])}</span>'
        for entry in shown
    )
    if len(alerts) > len(shown):
        alert_html += f'<span class="header-alert warn">+{len(alerts) - len(shown)} more</span>'
    return f'<div class="header-health">{overall_badge(data)}<div class="header-alerts">{alert_html}</div></div>'


def item(label: str, value) -> str:
    return f'<div class="item"><span>{esc(label)}</span><span>{esc(value)}</span></div>'


def public_card(title: str, section: dict, fields: list[tuple[str, str, object]]) -> str:
    status = status_value(section.get("status", "warn"))
    rows = "".join(item(label, section.get(key, fallback)) for label, key, fallback in fields)
    return f'<article class="card"><div class="card-top"><h2>{esc(title)}</h2>{badge(status)}</div><div>{rows}</div></article>'


def graywolf_access_card(section: dict, admin: bool = False) -> str:
    if str(section.get("engine", "")).lower() != "graywolf":
        return ""
    rows = [
        item("Management address", "10.42.0.1:8070"),
        item("Service", section.get("service", "unknown")),
        item("Output drive", section.get("graywolf_gain", "unknown")),
        item("TX timing", section.get("graywolf_tx_timing", "unknown")),
        item("iGate / APRS-IS", section.get("graywolf_igate", "unknown")),
        item("Beacon destination", section.get("graywolf_beacon_path", "unknown")),
    ]
    audience = "operator configuration and diagnostics" if admin else "local packet dashboard"
    return (
        '<article class="card service-card"><div class="card-top"><h2>Graywolf APRS</h2>'
        f'{badge(section.get("status", "warn"))}</div><p class="summary">Graywolf {esc(audience)}.</p>'
        f'{"".join(rows)}<a class="button secondary" href="http://10.42.0.1:8070/">Open Graywolf dashboard</a></article>'
    )


def render_public_page(data: dict) -> bytes:
    overall = status_value(data.get("overall", "warn"))
    error = data.get("error")
    error_html = f'<div class="notice error">{esc(error)}</div>' if error else ""
    system = data.get("system", {})
    network = data.get("network", {})
    cellular = data.get("cellular", {})
    remote_management = data.get("remote_management", {})
    time_info = data.get("time", {})
    gnss = data.get("gnss", {})
    storage = data.get("storage", {})
    services = data.get("services", {})
    power = data.get("power", {})

    cards = [
        public_card("System", system, [
            ("Uptime", "uptime", "unknown"), ("Local time", "local_time", "unknown"),
            ("CPU temperature", "cpu_temperature", "unavailable"), ("CPU load", "cpu_load", "unknown"),
            ("Memory used", "memory_used", "unknown"), ("System storage", "root_storage_used", "unknown"),
        ]),
        public_card("Network", network, [
            ("LAN gateway", "lan_gateway", "10.42.0.1"),
            ("OpenWrt AP online", "openwrt_online", False),
            ("Internet available", "internet_available", False),
            ("Active uplink", "uplink_type", "None"),
            ("WAN traffic since boot", "usage_summary", "Unavailable"),
            ("AP clients", "ap_client_count", "unavailable"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
    ]

    cards.append(public_card("Starlink Telemetry", data.get("starlink", {}), [
        ("Dish state", "state", "Unavailable"), ("Latency (ms)", "latency_ms", "Unavailable"),
        ("Packet loss (%)", "packet_loss_percent", "Unavailable"), ("Obstruction (%)", "obstruction_percent", "Unavailable"),
        ("Download (bps)", "downlink_bps", "Unavailable"), ("Upload (bps)", "uplink_bps", "Unavailable"),
        ("Dish uptime (seconds)", "uptime_seconds", "Unavailable"), ("Dish alerts", "alerts_summary", "Unavailable"),
        ("Sample age (seconds)", "sample_age_seconds", "Unavailable"),
    ]))
    for uplink in network.get("uplinks", []):
        if not isinstance(uplink, dict):
            continue
        usage = uplink.get("usage") or {}
        traffic = " / ".join(f"{label} {usage[key] / 1_000_000:.3f} MB" for key, label in [("rx_bytes", "Down"), ("tx_bytes", "Up"), ("total_bytes", "Total")] if type(usage.get(key)) is int)
        cards.append(public_card(str(uplink.get("name", "WAN")), {"status": "ok" if uplink.get("internet") is True else "warn", "state": uplink.get("state", "unknown"), "active": uplink.get("active", False), "traffic": traffic or "Unavailable"}, [("Internet health", "state", "unknown"), ("Active", "active", False), ("Traffic since boot", "traffic", "Unavailable")]))

    if remote_management.get("configured"):
        cards.append(public_card("Remote Management", remote_management, [
            ("Connection", "connection", "unknown"),
            ("Management address", "management_address", "unavailable"),
            ("Latest handshake", "latest_handshake", "unknown"),
            ("Starts on boot", "boot_enabled", False),
            ("Isolation firewall", "firewall_active", False),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"))

    cards.extend([
        public_card("Cellular", cellular, [
            ("Modem present", "modem_present", False), ("Connected", "connected", False),
            ("Carrier", "carrier", "unknown"), ("Access technology", "access_technology", "unknown"),
            ("Signal", "signal", "unknown"), ("Fallback policy", "fallback_policy", "Manual"),
            ("Fallback service active", "fallback_active", False),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
        public_card("GNSS Position", gnss, [
            ("Receiver active", "receiver_active", False), ("Fix", "fix", "unknown"),
            ("Satellites", "satellites", "unknown"), ("Coordinates", "coordinates", "not available"),
            ("Grid square", "grid_square", "unknown"), ("GNSS UTC", "utc_time", "unknown"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
        public_card("Time", time_info, [
            ("Chrony active", "chrony_active", False), ("Synchronized", "synchronized", False),
            ("Current source", "source", "Unsynchronized"), ("Reference", "reference", "unknown"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
        public_card("GNSS timing diagnostics", sanitize_gnss_receiver(gnss.get("receiver")), [
            ("Receiver", "model", "Legacy EM7565"), ("State", "diagnostic", "Legacy timing"),
            ("USB connected", "usb_connected", "not configured"), ("Receiver communicating", "connected", "not configured"), ("UTC valid", "utc_valid", "legacy"),
            ("PPS device", "pps_present", "not configured"), ("PPS active", "pps_active", "not configured"),
            ("Pulse age (seconds)", "pps_age_seconds", "unknown"), ("PPS validation", "pps_validation", "unknown"),
            ("Signals reported", "signals_reported", "unknown"), ("Signal report age", "signal_age_seconds", "unknown"),
            ("UTC report age", "timeutc_age_seconds", "unknown"), ("Timepulse metadata age", "tim_tp_age_seconds", "unknown"),
            ("Selected source", "source", "legacy"), ("Clock offset (seconds)", "offset_seconds", "unknown"),
            ("Uncertainty (seconds)", "uncertainty_seconds", "unknown"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
        public_card("Storage & Sharing", storage, [
            ("USB mounted", "usb_mounted", False), ("PCS-Share available", "primary_share_available", False),
            ("PCS-Backup available", "backup_share_available", False), ("USB free", "usb_free_gb", "unknown"),
            ("Backup free", "backup_free_gb", "unknown"),
            ("Backup health", "backup_status", "unknown"),
            ("Last backup", "last_backup_age", "unknown"),
            ("Automatic backups", "automatic_backup_enabled", False),
            ("Backup interval (minutes)", "backup_interval_minutes", "unknown"),
            ("Retain every snapshot", "keep_backup_history", False),
            ("Retained snapshots", "backup_history_count", 0),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
    ])

    if power.get("configured"):
        cards.append(public_card("PCS Power", power, [
            ("DC source", "dc_source", "unknown"),
            ("Total consumption (W)", "total_power", "unavailable"),
            ("Total charge since boot (mAh)", "total_charge_since_boot_mah", "unavailable"),
            ("Total energy since boot (Wh)", "total_energy_since_boot_wh", "unavailable"),
            ("Battery capacity (Wh)", "battery_capacity_wh", "not entered"),
            ("Estimated remaining (Wh)", "battery_remaining_wh", "unavailable"),
            ("Estimated remaining (%)", "battery_remaining_percent", "unavailable"),
            ("Input monitor", "input_online", False),
            ("Input voltage", "input_voltage", "unavailable"),
            ("Total input current", "input_current", "unavailable"),
            ("Total PCS input power", "input_power", "unavailable"),
            ("Input charge since boot (mAh)", "input_charge_since_boot_mah", "unavailable"),
            ("Input energy since boot (Wh)", "input_energy_since_boot_wh", "unavailable"),
            ("5V monitor", "rail_5v_online", False),
            ("5V rail voltage", "rail_5v_voltage", "unavailable"),
            ("5V rail current", "rail_5v_current", "unavailable"),
            ("5V rail power", "rail_5v_power", "unavailable"),
            ("5V charge since boot (mAh)", "rail_5v_charge_since_boot_mah", "unavailable"),
            ("5V energy since boot (Wh)", "rail_5v_energy_since_boot_wh", "unavailable"),
            ("12V monitor", "rail_12v_online", False),
            ("12V rail voltage", "rail_12v_voltage", "unavailable"),
            ("12V distribution current, includes 5V converter", "rail_12v_current", "unavailable"),
            ("12V distribution power, includes 5V converter", "rail_12v_distribution_power", "unavailable"),
            ("12V-only power, 5V output subtracted", "rail_12v_power", "unavailable"),
            ("12V distribution charge since boot (mAh)", "rail_12v_charge_since_boot_mah", "unavailable"),
            ("12V distribution energy since boot (Wh)", "rail_12v_distribution_energy_since_boot_wh", "unavailable"),
            ("12V-only energy since boot, 5V output subtracted (Wh)", "rail_12v_energy_since_boot_wh", "unavailable"),
            ("Starlink monitor", "starlink_online", False),
            ("Starlink branch voltage", "starlink_voltage", "unavailable"),
            ("Starlink branch current", "starlink_current", "unavailable"),
            ("Starlink branch power", "starlink_power", "unavailable"),
            ("Starlink charge since boot (mAh)", "starlink_charge_since_boot_mah", "unavailable"),
            ("Starlink energy since boot (Wh)", "starlink_energy_since_boot_wh", "unavailable"),
            ("Estimated non-5V load", "estimated_non_5v_power", "unavailable"),
            ("Low voltage alarm", "low_voltage_active", False),
            ("Automatic shutdown", "shutdown_armed", False),
            ("Shutdown countdown (s)", "shutdown_remaining_seconds", "inactive"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"))

    pistar = data.get("pistar", {})
    if pistar.get("configured"):
        pistar_section = {"status": "ok" if pistar.get("online") else "warn", **pistar}
        cards.append(public_card("Pi-Star", pistar_section, [
            ("Online", "online", False), ("Dashboard", "url", "http://10.42.0.3/"),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"))

    aprs = data.get("aprs", {})
    if aprs.get("configured"):
        cards.append(public_card("APRS / Packet", aprs, [
            ("Station", "callsign", "unknown"), ("Role", "role", "unknown"),
            ("Frequency", "frequency", "unknown"), ("Modem", "modem", "unknown"),
            ("APRS-IS profile", "aprs_is_profile", "disabled"),
            ("Digipeater", "digipeater", "disabled"), ("Beacon", "beacon", "disabled"),
            ("Network TNC", "kiss", "disabled"), ("FX.25 TX", "fx25", "disabled"),
            ("Packet activity", "packets", "not available"),
            ("Last RF packet", "last_heard", "not available"),
            ("Service", "service", "unknown"), ("RF TX", "tx_state", "Safe"),
        ]))
        if aprs.get("agent_enabled"):
            mailbox_section = {**aprs, "status": aprs.get("agent_status", "warn")}
            cards.append(public_card("APRS Mailbox", mailbox_section, [
                ("Messaging agent", "agent_connection", "unavailable"),
                ("Agent packets (session)", "agent_packets_received", 0),
                ("Addressed messages (session)", "agent_messages_received", 0),
                ("Stored messages", "mailbox_messages", 0),
                ("Unread messages", "mailbox_unread", 0),
                ("Last message", "last_mailbox_message", "none received"),
            ]))
        graywolf_card = graywolf_access_card(aprs)
        if graywolf_card:
            cards.append(graywolf_card)

    meshtastic = data.get("meshtastic", {})
    if meshtastic.get("configured"):
        cards.append(public_card("Meshtastic / MQTT", meshtastic, [
            ("Node", "node", "unknown"),
            ("Hardware", "hardware", "unknown"),
            ("Firmware", "firmware", "unknown"),
            ("Radio transport", "transport", "unknown"),
            ("Radio link", "radio_link", "unknown"),
            ("MQTT", "mqtt", "unknown"),
            ("Broker", "broker", "not configured"),
            ("Radio MQTT policy", "mqtt_policy", "unknown"),
            ("RF to internet IGate", "rf_igate", "not ready"),
            ("Public map report", "map_reporting", "disabled"),
            ("Embedded map mirror", "map_mqtt", "not configured"),
            ("Downlink filters", "downlink_filters", 0),
            ("MQTT activity", "mqtt_activity", "not available"),
            ("Mesh activity", "mesh_activity", "not available"),
            ("Remote nodes", "remote_nodes", "not available"),
            ("Last mesh packet", "last_heard", "none observed"),
            ("GPSD position feed", "gpsd_position", "disabled"),
            ("Case environment", "case_environment", "unavailable"),
            ("LoRa utilization", "utilization", "unavailable"),
            ("Power", "power", "unknown"),
            ("Service", "service", "unknown"),
        ]))

    gpsd_line = item("GPSD", "10.42.0.1:2947" if services.get("gpsd_lan_enabled") else "not enabled")
    service_directory = f"""
    <h2 class="section-title">Field Access</h2><section class="grid service-grid">
      <article class="card service-card"><h3>File shares</h3>{item('Windows discovery', 'PCS-FILE-SHARE')}{item('Primary share', r'\\10.42.0.1\PCS-Share')}{item('Backup share', r'\\10.42.0.1\PCS-Backup')}<p class="small">Windows: open Network or enter the share path in File Explorer. Linux: connect with SMB/CIFS.</p></article>
      <article class="card service-card"><h3>Network services</h3>{item('LAN NTP server', '10.42.0.1')}{gpsd_line}</article>
      <article class="card service-card"><h3>Local devices</h3>{item('OpenWrt', 'http://10.42.0.2/')}{item('PCS administration', 'Authentication required')}<a href="http://10.42.0.2/">Open OpenWrt</a></article>
    </section>"""

    body = f"""
    <header><div class="nav"><div class="brand-group"><div class="brand">PCS Field Network</div>{header_health(data)}</div><nav class="navlinks"><a class="button secondary" href="/">PCS Home</a><a class="button secondary" href="/status/">Refresh</a></nav></div></header>
    <main><section class="hero"><div class="hero-main"><p class="eyebrow">Portable Communication Server</p><h1>Field Network Status</h1><p>Local services, communications, position, time, and storage information for devices connected on site.</p></div></section>
    {error_html}<section class="overview"><div><h2>Overall system health</h2><p>Last refreshed {esc(data.get('generated_at', 'unknown'))}</p></div>{overall_badge(data)}</section>
    <section class="grid public-grid">{''.join(cards)}</section>{service_directory}</main>"""
    return document("PCS Field Network Status", body, public_theme=True, footer='PCS local field network interface · <a href="/admin/">Admin Login</a>').replace(b"</head>", b'<meta http-equiv="refresh" content="10"></head>')


def render_metric(metric: dict) -> str:
    try:
        percent = max(0.0, min(100.0, float(metric.get("value"))))
        text = f"{percent:g}{esc(metric.get('suffix', ''))}"
    except (TypeError, ValueError):
        percent = 0
        text = "unknown"
    return f'<div class="metric"><div class="metric-row"><span>{esc(metric.get("label", "Metric"))}</span><span>{text}</span></div><div class="bar"><div class="bar-fill" style="width:{percent}%"></div></div></div>'


def render_admin_card(card: dict) -> str:
    rows = "".join(item(entry.get("label", ""), entry.get("value", "")) for entry in card.get("items", []))
    metrics = "".join(render_metric(metric) for metric in card.get("metrics", []))
    return f'<article class="card"><div class="card-top"><h2>{esc(card.get("title", "Status"))}</h2>{badge(card.get("status", "warn"))}</div><p class="summary">{esc(card.get("summary", ""))}</p>{metrics}{rows}</article>'



def dashboard_values(data: dict) -> dict:
    """Common public/admin overview, populated only from the public projection.

    The static homepage contains this same structure with unknown initial values;
    pcs.js refreshes its explicit data-field bindings from the public endpoint.
    Authentication-only controls are supplied by render_admin_page.
    """
    data = sanitize_public_dashboard(data)
    section = lambda key: data.get(key, {})
    network, power, gps = section('network'), section('power'), section('gnss')
    aprs, mesh, storage, clock = (section(key) for key in ('aprs', 'meshtastic', 'storage', 'time'))

    def text(value, fallback='Unavailable'):
        if isinstance(value, bool):
            return 'Yes' if value else 'No'
        return str(value) if isinstance(value, (str, int, float)) and str(value).strip() else fallback

    def reading(value, unit, decimals=1):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return f'{value:.{decimals}f} {unit}'
        if isinstance(value, str) and re.fullmatch(r'-?\d+(?:\.\d+)?\s*' + re.escape(unit), value.strip(), re.I):
            return value
        return 'Unavailable'

    def optional(value):
        return 'Not configured' if value.get('configured') is False else status_label(value['status']) if value.get('configured') is True and value.get('status') in {'ok', 'warn', 'bad'} else 'Unknown'

    def link_state(kinds):
        path = next((u for u in network.get('uplinks', []) if u.get('type') in kinds), None)
        return ('Selected · ' if path.get('active') else '') + text(path.get('state'), 'Unknown') if path else 'Not reported'

    active_wan = next((u for u in network.get('uplinks', []) if u.get('active') is True), {})
    sl_ready = section('starlink').get('configured') is True and section('starlink').get('available') is True
    uplink_name = text(network.get('uplink_type'))
    if active_wan.get('type') in {'ethernet', 'vlan'}:
        uplink_name = 'Starlink' if active_wan.get('id') == 'starlink' and sl_ready else 'Ethernet WAN'
    elif uplink_name == 'Starlink' and not sl_ready:
        uplink_name = 'Ethernet WAN'
    values = {
        'uplink': 'Offline' if data.get('offline') or network.get('internet_available') is False else uplink_name,
        'power': reading(power.get('total_power') if 'total_power' in power else power.get('input_power') if power.get('input_online') else None, 'W') if power.get('configured') else 'Unavailable',
        'voltage': reading(power.get('input_voltage'), 'V', 2) if power.get('configured') and power.get('input_online') else 'Unavailable',
        'gps': text(gps.get('fix')), 'satellites': text(gps.get('satellites')), 'clients': str(network['ap_client_count']) if type(network.get('ap_client_count')) is int and network['ap_client_count'] >= 0 else 'Unavailable',
        'dcSource': {'battery':'Battery', 'power_supply':'Power Supply'}.get(power.get('dc_source'), 'Unknown'),
        'internet': 'Available' if network.get('internet_available') is True else 'Offline' if network.get('internet_available') is False else 'Unknown',
        'apStatus': 'Connected' if network.get('openwrt_online') is True else 'Offline' if network.get('openwrt_online') is False else 'Unknown',
        'overviewEthernet': link_state({'ethernet', 'vlan'}), 'overviewWifi': link_state({'wifi'}),
        'overviewCellular': link_state({'cellular'}), 'carrier': text(section('cellular').get('carrier')),
        'aprs': optional(aprs), 'aprsDetail': 'Not configured' if aprs.get('configured') is False else ' · '.join(text(aprs.get(key), 'Unknown') for key in ('callsign','engine','frequency')),
        'mesh': optional(mesh), 'meshDetail': 'Not configured' if mesh.get('configured') is False else ' · '.join(text(mesh.get(key), 'Unknown') for key in ('node','transport','mqtt')),
        'pistar': 'Not configured' if section('pistar').get('configured') is False else 'Online' if section('pistar').get('online') is True else 'Offline' if section('pistar').get('online') is False else 'Unknown',
        'primaryState': text(storage.get('primary_share_available')), 'backupState': text(storage.get('backup_share_available')),
        'backupAge': text(storage.get('last_backup_age')), 'timeSource': text(clock.get('source')),
        'gnssTiming': text(gps.get('receiver', {}).get('diagnostic'), 'Legacy timing'),
        'timeSync': 'Synchronized' if clock.get('synchronized') is True else 'Not synchronized' if clock.get('synchronized') is False else 'Unknown',
    }
    for prefix, source, online in [('input', 'input', 'input_online'), ('rail12', 'rail_12v', 'rail_12v_online'),
                                   ('rail5', 'rail_5v', 'rail_5v_online'), ('starlink', 'starlink', 'starlink_online')]:
        commissioned = power.get('configured') is True and (prefix != 'starlink' or power.get('starlink_configured') is True)
        available = commissioned and power.get(online) is True
        values[prefix + 'Monitor'] = 'Not commissioned' if not commissioned else 'Online' if available else 'Unavailable'
        for suffix, key, unit, decimals in [('Power', 'power', 'W', 1), ('Voltage', 'voltage', 'V', 2),
                                            ('Current', 'current', 'A', 3), ('Charge', 'charge_since_boot_mah', 'mAh', 1),
                                            ('Energy', 'energy_since_boot_wh', 'Wh', 3)]:
            values[prefix + suffix] = reading(power.get(source + '_' + key), unit, decimals) if available else 'Not commissioned' if prefix == 'starlink' and suffix == 'Power' and not commissioned else 'Unavailable'
    for field, key, unit, decimals in [('rail12DistributionPower', 'rail_12v_distribution_power', 'W', 1),
                                      ('rail12OnlyPower', 'rail_12v_power', 'W', 1),
                                      ('rail12DistributionEnergy', 'rail_12v_distribution_energy_since_boot_wh', 'Wh this boot', 3),
                                      ('rail12OnlyEnergy', 'rail_12v_energy_since_boot_wh', 'Wh this boot', 3)]:
        values[field] = reading(power.get(key), unit, decimals) if power.get('configured') is True and power.get('rail_12v_online') is True else 'Unavailable'
    starlink = section('starlink')
    values['starlinkState'] = 'Not commissioned' if starlink.get('configured') is False else text(starlink.get('state')) if sl_ready else 'Unavailable'
    values['starlinkAlerts'] = text(starlink.get('alerts_summary')) if sl_ready else 'Unavailable'
    for field, key, unit, scale in [('starlinkLatency', 'latency_ms', 'ms', 1), ('starlinkLoss', 'packet_loss_percent', '%', 1),
                                   ('starlinkObstruction', 'obstruction_percent', '%', 1), ('starlinkDown', 'downlink_bps', 'Mbps', 1e6),
                                   ('starlinkUp', 'uplink_bps', 'Mbps', 1e6), ('starlinkUptime', 'uptime_seconds', 's', 1),
                                   ('starlinkAge', 'sample_age_seconds', 's', 1)]:
        value = starlink.get(key)
        values[field] = f'{value / scale:.1f}{" " if unit != "%" else ""}{unit}' if sl_ready and type(value) in {int, float} and math.isfinite(value) and value >= 0 else 'Unavailable'
    values['batteryWarning'] = 'Estimated capacity is at or below 10%. Voltage protection remains authoritative.' if power.get('battery_capacity_warning') is True else 'Capacity is an estimate; voltage protection operates independently.'
    values['non5vPower'] = reading(power.get('estimated_non_5v_power'), 'W') if power.get('configured') is True else 'Unavailable'
    values['lowVoltage'] = 'Yes' if power.get('low_voltage_active') is True else 'No' if power.get('low_voltage_active') is False else 'Unknown'
    for field, key in [('powerElapsed', 'energy_tracking_elapsed_seconds'), ('shutdownRemaining', 'shutdown_remaining_seconds')]:
        value = power.get(key)
        values[field] = reading(value, 's', 0) if type(value) in {int, float} and value >= 0 else 'Unavailable'

    values.update({
        'coordinates': text(gps.get('coordinates')), 'grid': text(gps.get('grid_square')),
        'inputVoltage': values['voltage'],
        'shutdownProtection': 'Enabled' if power.get('shutdown_armed') is True else 'Disabled' if power.get('shutdown_armed') is False else 'Unknown',
        'wanUsage': text(network.get('usage_summary')),
    })
    for field, key in [('powerCharge', 'total_charge_since_boot_mah'), ('powerEnergy', 'total_energy_since_boot_wh'), ('batteryRemaining', 'battery_remaining_wh'), ('batteryCapacity', 'battery_capacity_wh'), ('batteryPercent', 'battery_remaining_percent')]:
        value = power.get(key)
        values[field] = f'{value:.0f}' if field == 'powerCharge' and type(value) in {int, float} and math.isfinite(value) and value >= 0 else f'{value:.1f}' if type(value) in {int, float} and math.isfinite(value) and value >= 0 else 'Unavailable'
    return values


def render_overview(data: dict, controls: dict | None = None) -> str:
    values = dashboard_values(data)
    controls = controls or {}
    def field(key, tag='strong'):
        return f'<{tag} data-field="{key}">{esc(values[key])}</{tag}>'

    def row(title, key, detail=''):
        return f'<div class="subsystem-row"><div><strong>{title}</strong>{field(detail, "small") if detail else ""}</div>{field(key)}</div>'

    kpis = ''.join(f'<article class="kpi"><span class="metric-label">{title}</span>{field(key)}<span class="metric-note">{detail}</span></article>' for title,key,detail in [
        ('ACTIVE UPLINK','uplink',field('internet','span')),
        ('TOTAL POWER','power','Input '+field('voltage','span')+' · '+field('dcSource','span')),
        ('GNSS','gps',field('satellites','span')+' · '+field('timeSync','span')),
        ('AP CLIENTS','clients','OpenWrt '+field('apStatus','span'))])
    panels = [
        ('Connectivity','WAN paths and current route',row('Ethernet WAN','overviewEthernet')+row('Wi-Fi','overviewWifi')+row('Cellular','overviewCellular','carrier'), 'network'),
        ('Radio services','Packet, mesh and local hotspot',row('APRS','aprs','aprsDetail')+row('Meshtastic','mesh','meshDetail')+row('Pi-Star','pistar'), 'radio'),
        ('Power distribution','Measured branches · source-dependent totals', ''.join(row(title,key) for title,key in [('PCS input','inputPower'),('12V rail','rail12OnlyPower'),('5V rail','rail5Power'),('Starlink passthrough','starlinkPower')]), 'power'),
        ('Storage & time','Field files, backup and time reference',row('PCS-Share available','primaryState')+row('PCS-Backup available','backupState')+row('Last backup','backupAge')+row('Time reference','timeSource'), 'storage')]
    return '<div class="overview-kpis">'+kpis+'</div><h2 class="subsystems-title">Subsystems</h2><div class="subsystem-grid">'+''.join(
        f'<article class="subsystem-panel" data-panel="{key}"><div class="panel-heading"><div><h3>{title}</h3><p>{subtitle}</p></div><a class="compact-control" href="#{key}">Details <span aria-hidden="true">↗</span></a></div>{rows}{controls.get(key, "")}</article>'
        for title,subtitle,rows,key in panels)+'</div>'



def render_dashboard_services() -> str:
    return """    <section id="services" aria-labelledby="services-title">
      <div class="section-heading"><h2 id="services-title">Quick access</h2><span>SELECT A SERVICE TO CONTINUE</span></div>
      <div class="service-grid">
        <a class="tile" href="/docs/"><span class="tile-top"><span class="icon" aria-hidden="true">≡</span><span class="tag">OFFLINE HELP</span></span><h3>Documentation <span aria-hidden="true">↗</span></h3><p>Quick start, file shares, service addresses and field reference.</p></a>
        <a class="tile" href="/status/"><span class="tile-top"><span class="icon" aria-hidden="true">▤</span><span class="tag">PUBLIC</span></span><h3>Status <span aria-hidden="true">↗</span></h3><p>Detailed health, position, storage and network information.</p></a>
        <a class="tile" href="/openwrt/"><span class="tile-top"><span class="icon" aria-hidden="true">⌁</span><span class="tag">LOCAL NETWORK</span></span><h3>OpenWrt <span aria-hidden="true">↗</span></h3><p>Open the PCS access point and router interface.</p></a>
        <a class="tile" href="/pistar/" id="pistar-tile"><span class="tile-top"><span class="icon" aria-hidden="true">◉</span><span class="tag" data-field="pistar">Unknown</span></span><h3>Pi-Star <span aria-hidden="true">↗</span></h3><p>Open the paired digital voice hotspot dashboard.</p></a>
        <a class="tile" href="/radio/#meshtastic"><span class="tile-top"><span class="icon" aria-hidden="true">⋈</span><span class="tag" data-field="mesh">Unknown</span></span><h3>Meshtastic <span aria-hidden="true">↗</span></h3><p>Mesh radio link and MQTT gateway information.</p></a>
        <a class="tile" href="/radio/#aprs"><span class="tile-top"><span class="icon" aria-hidden="true">⌁</span><span class="tag" data-field="aprs">Unknown</span></span><h3>APRS <span aria-hidden="true">↗</span></h3><p>Packet radio station and messaging service information.</p></a>
      </div>
    </section>"""



DASHBOARD_SECTION_TEMPLATES = {
    'starlink': """<div class="content-grid"><article class="content-panel"><h3>Starlink connection</h3><div class="pair"><span>State</span><strong data-field="starlinkState">Unavailable</strong></div><div class="pair"><span>Latency</span><strong data-field="starlinkLatency">Unavailable</strong></div><div class="pair"><span>Packet loss</span><strong data-field="starlinkLoss">Unavailable</strong></div><div class="pair"><span>Obstruction</span><strong data-field="starlinkObstruction">Unavailable</strong></div><div class="pair"><span>Dish uptime</span><strong data-field="starlinkUptime">Unavailable</strong></div><div class="pair"><span>Sample age</span><strong data-field="starlinkAge">Unavailable</strong></div></article><article class="content-panel"><h3>Throughput & alerts</h3><div class="pair"><span>Download</span><strong data-field="starlinkDown">Unavailable</strong></div><div class="pair"><span>Upload</span><strong data-field="starlinkUp">Unavailable</strong></div><div class="pair"><span>Dish alerts</span><strong data-field="starlinkAlerts">Unavailable</strong></div><p class="small">PCS verifies Internet access independently. Missing dish telemetry does not disable the LAN or prevent uplink failover.</p></article><article class="content-panel"><h3>Measured branch power</h3><div class="pair"><span>Monitor</span><strong data-field="starlinkMonitor">Unavailable</strong></div><div class="pair"><span>Power</span><strong data-field="starlinkPower">Unavailable</strong></div><div class="pair"><span>Voltage</span><strong data-field="starlinkVoltage">Unavailable</strong></div><div class="pair"><span>Current</span><strong data-field="starlinkCurrent">Unavailable</strong></div><div class="pair"><span>Charge this boot</span><strong data-field="starlinkCharge">Unavailable</strong></div><div class="pair"><span>Energy this boot</span><strong data-field="starlinkEnergy">Unavailable</strong></div><p class="small">Measured by the fourth INA226. The Power tab includes all four monitors.</p></article></div>""",
    'network': """<div class="content-grid"><article class="content-panel"><h3>Client LAN and uplinks</h3><div class="pair"><span>Active uplink</span><strong data-field="uplink">Unavailable</strong></div><div class="pair"><span>AP clients</span><strong data-field="clients">Unavailable</strong></div><div class="pair"><span>Gateway</span><strong data-value="network.lan_gateway">Unavailable</strong></div><div class="pair"><span>OpenWrt</span><strong data-value="network.openwrt_online">Unavailable</strong></div><div class="pair"><span>Internet</span><strong data-value="network.internet_available">Unavailable</strong></div><div class="pair"><span>DNS</span><strong data-value="network.dns_available">Unavailable</strong></div></article><article class="content-panel"><h3>Cellular</h3><div class="pair"><span>Carrier</span><strong data-value="cellular.carrier">Unavailable</strong></div><div class="pair"><span>Signal</span><strong data-value="cellular.signal">Unavailable</strong></div><div class="pair"><span>Connected</span><strong data-value="cellular.connected">Unavailable</strong></div></article><article class="content-panel" data-optional="remote_management"><h3>Remote management</h3><div class="pair"><span>Connection</span><strong data-value="remote_management.connection">Unavailable</strong></div><div class="pair"><span>Management address</span><strong data-value="remote_management.management_address">Unavailable</strong></div><div class="pair"><span>Last handshake</span><strong data-value="remote_management.latest_handshake">Unavailable</strong></div></article><article class="content-panel"><h3>WAN traffic since boot</h3><p data-field="wanUsage">Unavailable</p><ul id="wan-breakdown"><li>Uplink details unavailable</li></ul><a href="#starlink">Starlink telemetry</a> · <a href="/openwrt/">OpenWrt</a></article></div>""",
    'radio': """<div class="content-grid"><article class="content-panel" data-optional="aprs"><h3>APRS</h3><div class="pair"><span>Station</span><strong data-value="aprs.callsign">Unavailable</strong></div><div class="pair"><span>Role</span><strong data-value="aprs.role">Unavailable</strong></div><div class="pair"><span>Service</span><strong data-value="aprs.service">Unavailable</strong></div><div class="pair"><span>Packet engine</span><strong data-value="aprs.engine">Unavailable</strong></div><div class="pair"><span>Unread mailbox</span><strong data-value="aprs.mailbox_unread">Unavailable</strong></div></article><article class="content-panel" data-optional="meshtastic"><h3>Meshtastic</h3><div class="pair"><span>Node</span><strong data-value="meshtastic.node">Unavailable</strong></div><div class="pair"><span>MQTT</span><strong data-value="meshtastic.mqtt">Unavailable</strong></div><div class="pair"><span>Transport</span><strong data-value="meshtastic.transport">Unavailable</strong></div></article><article class="content-panel" data-optional="pistar"><h3>Pi-Star</h3><div class="pair"><span>Status</span><strong data-field="pistar">Unavailable</strong></div></article><article class="content-panel"><h3>Radio services</h3><p>Configured services and aggregate diagnostics. Authenticated mailbox detail stays in Administration.</p><a href="/radio/">Radio service guide</a> · <a href="/pistar/">Pi-Star access</a> · <a href="/status/">Full public diagnostics</a></article></div>""",
    'power': """<div class="content-grid power-readings"><article class="content-panel"><h3>Power overview</h3><div class="pair"><span>Source this boot</span><strong data-field="dcSource">Unavailable</strong></div><div class="pair"><span>Total power</span><strong data-field="power">Unavailable</strong></div><div class="pair"><span>Charge this boot (mAh)</span><strong data-field="powerCharge">Unavailable</strong></div><div class="pair"><span>Energy this boot (Wh)</span><strong data-field="powerEnergy">Unavailable</strong></div><div class="pair"><span>Tracking elapsed</span><strong data-field="powerElapsed">Unavailable</strong></div><div class="pair"><span>Non-5V load, including conversion loss</span><strong data-field="non5vPower">Unavailable</strong></div><p class="small">Battery totals include PCS and Starlink; Power Supply totals include PCS only. All counters begin at boot.</p></article><article class="content-panel"><h3>Battery & protection</h3><div class="pair"><span>Nominal capacity (Wh)</span><strong data-field="batteryCapacity">Unavailable</strong></div><div class="pair"><span>Estimated remaining (Wh)</span><strong data-field="batteryRemaining">Unavailable</strong></div><div class="pair"><span>Estimated remaining (%)</span><strong data-field="batteryPercent">Unavailable</strong></div><div class="pair"><span>Low-voltage shutdown</span><strong data-field="shutdownProtection">Unavailable</strong></div><div class="pair"><span>Low voltage detected</span><strong data-field="lowVoltage">Unavailable</strong></div><div class="pair"><span>Shutdown countdown</span><strong data-field="shutdownRemaining">Unavailable</strong></div><p class="small"><span data-field="batteryWarning">Capacity is an estimate; voltage protection operates independently.</span></p></article><article class="content-panel"><h3>PCS input</h3><div class="pair"><span>Monitor</span><strong data-field="inputMonitor">Unavailable</strong></div><div class="pair"><span>Power</span><strong data-field="inputPower">Unavailable</strong></div><div class="pair"><span>Voltage</span><strong data-field="inputVoltage">Unavailable</strong></div><div class="pair"><span>Current</span><strong data-field="inputCurrent">Unavailable</strong></div><div class="pair"><span>Charge this boot</span><strong data-field="inputCharge">Unavailable</strong></div><div class="pair"><span>Energy this boot</span><strong data-field="inputEnergy">Unavailable</strong></div></article><article class="content-panel"><h3>12V distribution</h3><div class="pair"><span>Monitor</span><strong data-field="rail12Monitor">Unavailable</strong></div><div class="pair"><span>Power, including 5V</span><strong data-field="rail12DistributionPower">Unavailable</strong></div><div class="pair"><span>Voltage</span><strong data-field="rail12Voltage">Unavailable</strong></div><div class="pair"><span>Current</span><strong data-field="rail12Current">Unavailable</strong></div><div class="pair"><span>Charge this boot</span><strong data-field="rail12Charge">Unavailable</strong></div><div class="pair"><span>Energy this boot, including 5V</span><strong data-field="rail12DistributionEnergy">Unavailable</strong></div><div class="pair"><span>12V-only power</span><strong data-field="rail12OnlyPower">Unavailable</strong></div><div class="pair"><span>12V-only energy this boot</span><strong data-field="rail12OnlyEnergy">Unavailable</strong></div><p class="small">The distribution reading includes the downstream 5V converter. 12V-only values subtract measured 5V output to prevent double-counting.</p></article><article class="content-panel"><h3>5V output</h3><div class="pair"><span>Monitor</span><strong data-field="rail5Monitor">Unavailable</strong></div><div class="pair"><span>Power</span><strong data-field="rail5Power">Unavailable</strong></div><div class="pair"><span>Voltage</span><strong data-field="rail5Voltage">Unavailable</strong></div><div class="pair"><span>Current</span><strong data-field="rail5Current">Unavailable</strong></div><div class="pair"><span>Charge this boot</span><strong data-field="rail5Charge">Unavailable</strong></div><div class="pair"><span>Energy this boot</span><strong data-field="rail5Energy">Unavailable</strong></div></article><article class="content-panel"><h3>Starlink passthrough</h3><div class="pair"><span>Monitor</span><strong data-field="starlinkMonitor">Unavailable</strong></div><div class="pair"><span>Power</span><strong data-field="starlinkPower">Unavailable</strong></div><div class="pair"><span>Voltage</span><strong data-field="starlinkVoltage">Unavailable</strong></div><div class="pair"><span>Current</span><strong data-field="starlinkCurrent">Unavailable</strong></div><div class="pair"><span>Charge this boot</span><strong data-field="starlinkCharge">Unavailable</strong></div><div class="pair"><span>Energy this boot</span><strong data-field="starlinkEnergy">Unavailable</strong></div><p class="small">Independent fourth INA226 branch monitor; dish telemetry is on the Starlink tab.</p></article></div>""",
    'storage': """<div class="content-grid"><article class="content-panel"><h3>Storage and backup</h3><div class="pair"><span>Primary share</span><strong data-value="storage.primary_share_available">Unavailable</strong></div><div class="pair"><span>USB mounted</span><strong data-value="storage.usb_mounted">Unavailable</strong></div><div class="pair"><span>USB free</span><strong data-value="storage.usb_free_gb">Unavailable</strong></div><div class="pair"><span>Backup available</span><strong data-value="storage.backup_share_available">Unavailable</strong></div><div class="pair"><span>Backup free</span><strong data-value="storage.backup_free_gb">Unavailable</strong></div></article><article class="content-panel"><h3>Field files</h3><p>Local shares remain accessible without Internet.</p><a href="/files/">File sharing instructions</a> · <a href="/status/">Backup and retention details</a></article></div>""",
    'diagnostics': """<div class="content-grid"><article class="content-panel"><h3>Position and time</h3><div class="pair"><span>GNSS fix</span><strong data-field="gps">Unavailable</strong></div><div class="pair"><span>Satellites</span><strong data-value="gnss.satellites">Unavailable</strong></div><div class="pair"><span>Coordinates</span><strong data-field="coordinates">Unavailable</strong></div><div class="pair"><span>Grid square</span><strong data-field="grid">Unavailable</strong></div><div class="pair"><span>Time source</span><strong data-field="timeSource">Unavailable</strong></div><div class="subsystem-row"><div><strong>GNSS / PPS</strong></div><strong data-field="gnssTiming">Legacy timing</strong></div><div class="pair"><span>Synchronized</span><strong data-value="time.synchronized">Unavailable</strong></div></article><article class="content-panel"><h3>System resources</h3><div class="pair"><span>CPU temperature</span><strong data-value="system.cpu_temperature">Unavailable</strong></div><div class="pair"><span>CPU load</span><strong data-value="system.cpu_load">Unavailable</strong></div><div class="pair"><span>Memory</span><strong data-value="system.memory_used">Unavailable</strong></div><div class="pair"><span>System storage</span><strong data-value="system.root_storage_used">Unavailable</strong></div></article><article class="content-panel"><h3>Advanced diagnostics</h3><p>The detailed status page retains every public measurement and optional service field.</p><a href="/status/">Detailed status</a> · <a href="/cockpit/">Cockpit</a> · <a href="/docs/">Offline documentation</a></article></div>""",
}


def render_dashboard_section(data: dict, key: str) -> str:
    public = sanitize_public_dashboard(data)
    values = dashboard_values(public)
    def substitute(match):
        tag, attr, field = match.group(1), match.group(2), match.group(3)
        if attr == 'data-value':
            section, name = field.split('.')
            section = public.get(section, {})
            value = section.get(name)
            value = 'Not configured' if section.get('configured') is False else 'Yes' if value is True else 'No' if value is False else value if isinstance(value, (str, int, float)) else 'Unavailable'
        else:
            value = values.get(field, 'Unavailable')
        return f'<{tag} {attr}="{field}">{esc(value)}</{tag}>'
    markup = re.sub(r'<(strong|span|p) (data-value|data-field)="([^"]+)">[^<]*</\1>', substitute, DASHBOARD_SECTION_TEMPLATES[key])
    if key == 'network':
        rows = []
        for uplink in public.get('network', {}).get('uplinks', []):
            usage = uplink.get('usage') or {}
            def amount(name):
                value = usage.get(name)
                return f'{value / 1_000_000:.3f} MB' if type(value) in {int, float} and math.isfinite(value) and value >= 0 else 'Unavailable'
            name = uplink.get('name', 'WAN')
            if uplink.get('type') in {'ethernet', 'vlan'}:
                name = 'Starlink' if uplink.get('id') == 'starlink' and public.get('starlink', {}).get('available') is True else 'Ethernet WAN'
            label = f"{name}: {uplink.get('state', 'unknown')}{' / active' if uplink.get('active') is True else ''} · Down {amount('rx_bytes')} / Up {amount('tx_bytes')} / Total {amount('total_bytes')}"
            rows.append(f'<li>{esc(label)}</li>')
        if rows:
            markup = markup.replace('<li>Uplink details unavailable</li>', ''.join(rows))
    return re.sub(r'data-optional="([^"]+)"', lambda m: m.group(0) + (' hidden' if public.get(m.group(1), {}).get('configured') is False else ''), markup)


def render_admin_details_page(data: dict, csrf: str) -> bytes:
    """Authenticated read-only diagnostics; no privileged data in clean cards."""
    groups = {'network': {'network', 'uplink-details', 'client-lan', 'cellular', 'remote-management', 'starlink'},
              'radio': {'aprs', 'aprs-mailbox', 'meshtastic'}, 'power': {'power'},
              'storage': {'storage', 'backup-health', 'samba'},
              'diagnostics': {'system-stats', 'system', 'time', 'gps', 'services', 'web-admin'}}
    grouped = {key: [] for key in groups}
    other = []
    for card in data.get('cards', []):
        group = next((key for key, ids in groups.items() if card.get('id') in ids), None)
        (grouped[group] if group else other).append(render_admin_card(card))
    sections = ''.join(f'<section id="{key}"><h2>{key.title()}</h2><div class="grid admin-grid">{"".join(cards)}</div></section>' for key, cards in grouped.items())
    info = data.get('client_info', {})
    clients = ''.join(item(client.get('name', 'Unknown'), f"{client.get('ip', 'unknown')} / {client.get('mac', 'unknown')} / {client.get('state', 'unknown')}") for client in info.get('router_side_clients', []))
    access = item('WAN/public IP', info.get('wan_public_ip', 'Unavailable')) + item('Uplink interface', info.get('uplink_interface', 'Unknown'))
    access += item('PCS-Share', r'\\10.42.0.1\PCS-Share') + item('PCS-Backup', r'\\10.42.0.1\PCS-Backup')
    access += item('Windows discovery', 'PCS-FILE-SHARE') + item('PCS-Backup login', 'pcs-admin / current admin password')
    alerts = ''.join(f'<li>{esc(a["component"])}: {esc(a["message"])}</li>' for a in sanitize_alerts(data.get('alerts', [])))
    error = f'<p class="notice error">{esc(data["error"])}</p>' if data.get('error') else ''
    graywolf = graywolf_access_card(data.get('aprs', {}), admin=True)
    body = f"""<a class="skip" href="#workspace-main">Skip to content</a><div class="workspace">
    <aside class="workspace-sidebar"><a class="workspace-brand" href="/admin/">PCS<span>CONTROL CENTER</span></a><nav class="workspace-nav"><a class="compact-control" href="/admin/">Dashboard</a><a class="compact-control" href="/admin/details" aria-current="page">Detailed diagnostics</a></nav></aside>
    <div class="workspace-content"><header><div class="nav">{header_health(data)}<a class="compact-control" href="/admin/">Back to dashboard</a><form class="logout" method="POST" action="/admin/logout"><input type="hidden" name="csrf" value="{esc(csrf)}"><button type="submit">Logout</button></form></div></header>
    <main class="admin-main" id="workspace-main"><h1>Detailed diagnostics</h1><p class="small">Collected {esc(data.get('generated_at', 'unknown'))} · {esc(data.get('snapshot', {}).get('state', 'unknown'))}</p>{error}<ul>{alerts}</ul>{sections}{graywolf}<section class="card"><h2>Detailed field access and clients</h2>{access}{clients}</section><section><h2>Advanced / Other diagnostics</h2><div class="grid admin-grid">{"".join(other)}</div></section></main></div></div>"""
    return document('PCS Detailed Diagnostics', body, workspace_theme=True)

def render_admin_page(data: dict, csrf: str, result: str = "", action_name: str = "", return_code: int | None = None, nonce: str = "", backup_settings: dict | None = None, settings_notice: str = "", settings_error: str = "", public_data: dict | None = None) -> bytes:
    # Presentation grouping only: the dispatcher and security boundary stay authoritative.
    section_actions = {
        "actions": [("Frequently used", ["self-test", "wifi-connect", "cellular-connect", "sync-backup"])],
        "overview": [("Overview tools", ["wifi-status", "meshtastic-status", "storage-status", "status"])],
        "network": [("Wi-Fi and cellular", ["wifi-disconnect", "cellular-status", "cellular-disconnect", "cellular-test"])],
        "starlink": [("Starlink operations", ["starlink-status", "starlink-reboot", "starlink-shutdown"])],
        "radio": [("Radio operations", ["restart-meshtastic", "aprs-mailbox-read"])],
        "storage": [("Storage operations", ["mount-usb", "mount-new-usb", "safe-unmount-usb", "restart-samba"])],
        "diagnostics": [("Services and time", ["restart-services", "restart-modemmanager", "sync-time", "restart-chrony", "restart-gpsd", "restart-logs", "buzzer-mute", "buzzer-unmute"]),
                   ("Dangerous operations", ["reboot-system", "shutdown-system"])],
    }
    overview_controls = {}
    action_groups = {key: [] for key in section_actions}
    for section, groups in section_actions.items():
        for title, action_names in groups:
            forms = []
            for name in action_names:
                label, description = ACTION_MAP[name]
                danger = "danger" if name in DANGEROUS_ACTIONS else "secondary"
                confirmation = ACTION_CONFIRMS.get(name, "")
                kind = "Read-only" if name.endswith("-status") or name in {"status", "restart-logs"} else "Operator action"
                if name == "starlink-shutdown":
                    kind = "Staged / unsupported · status check only"
                forms.append(f"""<form method="POST" action="/admin/run#{section}" class="action-card" data-confirm="{esc(confirmation)}">
                <input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="action" value="{esc(name)}">
                <span class="eyebrow">{kind}</span><button class="{danger}" type="submit">{esc(label)}</button><p>{esc(description)}</p></form>""")
            if section == "overview":
                for destination, form in zip(['network', 'radio', 'storage', 'power'], forms):
                    overview_controls[destination] = '<div class="panel-controls">' + form + '</div>'
                continue
            action_groups[section].append(f'<section class="action-group"><h3>{esc(title)}</h3><div class="action-grid">{"".join(forms)}</div></section>')

    power = data.get("power", {})
    supply = power.get("dc_source") == "power_supply"
    capacity = power.get("battery_capacity_wh")
    power_settings_html = f'''<section class="card" id="power-settings"><h2>DC source</h2>
    <form method="POST" action="/admin/power-settings#power" class="settings-form">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <label for="dc-source">Source for this boot</label><select id="dc-source" name="dc_source">
    <option value="battery" {"" if supply else "selected"}>Battery</option>
    <option value="power_supply" {"selected" if supply else ""}>Power Supply</option></select>
    <label for="battery-capacity">Nominal battery capacity (Wh, optional)</label>
    <input id="battery-capacity" name="battery_capacity_wh" type="number" min="0.01" max="1000000" step="any" value="{esc(capacity if capacity is not None else '')}" placeholder="500">
    <p>Battery enables the configured voltage protection and includes Starlink in totals. Power Supply disables automatic low-voltage shutdown and keeps PCS totals separate. Switching back to Battery re-enables protection. Every boot starts in Battery mode.</p>
    <p>Capacity estimates deduct consumption already recorded this boot. Saving never resets energy counters.</p>
    <button type="submit">Apply DC source</button></form></section>'''
    result_html = ""
    if return_code is not None or result:
        result_html = f'<section class="output" id="action-result" tabindex="-1"><h2>Result: {esc(action_name)} <span class="small">exit {esc(return_code)}</span></h2><pre>{esc(result)}</pre></section>'

    backup_settings = backup_settings or {"enabled": False, "interval_minutes": 10, "keep_history": False}
    checked = " checked" if backup_settings.get("enabled") else ""
    history_checked = " checked" if backup_settings.get("keep_history") else ""
    settings_notice_html = f'<div class="notice success">{esc(settings_notice)}</div>' if settings_notice else ""
    settings_error_html = f'<div class="notice error">{esc(settings_error)}</div>' if settings_error else ""
    backup_settings_html = f"""{settings_notice_html}{settings_error_html}
    <section class="card" id="backup-settings-dialog"><h2>Backup settings</h2>
    <form class="settings-form" method="POST" action="/admin/backup-settings#storage">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <label class="check-line"><input type="checkbox" name="enabled" value="yes"{checked}> Enable automatic backups</label>
      <label>Interval in minutes (1–43,200)<input type="number" name="interval_minutes" min="1" max="43200" step="1" required value="{esc(backup_settings.get('interval_minutes', 10))}"></label>
      <label class="check-line"><input type="checkbox" name="keep_history" value="yes"{history_checked}> Keep every prior backup snapshot</label>
      <button type="submit">Save backup settings</button>
    </form><p class="small">The rolling backup is additive: files removed from PCS-Share remain on PCS-Backup. Snapshot history preserves each dated version and is never pruned automatically, so storage use will grow.</p></section>"""

    sections = [("overview", "Overview"), ("network", "Network"), ("starlink", "Starlink"), ("radio", "Radio"),
                ("power", "Power"), ("storage", "Storage"), ("diagnostics", "Diagnostics"), ("actions", "Actions")]
    nav = ''.join(f'<a class="compact-control" href="#{key}" data-view="{key}">{label}</a>' for key, label in sections)
    snapshot = data.get("snapshot", {})
    freshness = f'{snapshot.get("state", "unknown")} · age {snapshot.get("age_seconds", "unknown")} s'
    alerts = ''.join(f'<li>{esc(a["component"])}: {esc(a["message"])}</li>' for a in sanitize_alerts(data.get("alerts", [])))
    error_html = f'<p class="notice error">{esc(data["error"])}</p>' if data.get("error") else ""
    health = f'<section class="overview health-banner"><div><h1>System overview</h1><p>Snapshot {esc(freshness)} · collected {esc(data.get("generated_at", "unknown"))}</p>{error_html}<ul>{alerts}</ul></div>{overall_badge(data)}</section>'
    overview = render_overview(public_data or {}, overview_controls) + render_dashboard_services()
    action_directory = ''.join(
        f'<details><summary>{esc(title)}</summary><ul>' + ''.join(
            f'<li><a href="#{section}">{esc(ACTION_MAP[name][0])}</a> — {esc(ACTION_MAP[name][1])}</li>' for name in names
        ) + '</ul></details>'
        for section, groups in section_actions.items() if section not in {"actions", "overview"} for title, names in groups)
    content = {"overview": overview,
        "actions": '<p>Frequently used operations are below. Browse all other commands by category and open their contextual controls.</p>',
        "network": '<section class="card"><h2>Bandwidth</h2><p>Whole-network ceilings, default client speeds and trusted-device exceptions.</p><a class="button secondary" href="/admin/bandwidth">Bandwidth settings</a></section>',
        "radio":  '<p><a href="/pistar/">Optional Pi-Star access</a> · <a href="#diagnostics">Time / GPS controls</a></p>',
        "starlink": "",
        "storage": backup_settings_html,
        "power": power_settings_html,
        "diagnostics":  '<section class="card"><h2>Administrator access</h2><p>Password changes invalidate active sessions. Forgotten passwords require the PCS terminal.</p><a class="button secondary" href="/admin/password">Change Admin Password</a></section>'}
    content['diagnostics'] = '<section class="card"><h2>GNSS Information</h2><p>Receiver, RF, spectrum and AssistNow diagnostics.</p><a class="button secondary" href="/admin/gnss">GNSS Information</a></section><section class="card"><h2>Location</h2><p>Set a manual fallback or persistent position.</p><a class="button secondary" href="/admin/location">Location settings</a></section>' + content['diagnostics']
    views = ''.join(f'<section class="workspace-view" id="{key}"><h2 class="section-title">{label}</h2>{render_dashboard_section(public_data or {}, key) if key in DASHBOARD_SECTION_TEMPLATES else ""}{content[key]}{"".join(action_groups.get(key, []))}{action_directory if key == "actions" else ""}</section>' for key, label in sections)
    console = result_html
    body = f"""<a class="skip" href="#workspace-main">Skip to content</a><div class="workspace">
    <aside class="workspace-sidebar"><a class="workspace-brand" href="/">PCS<span>CONTROL CENTER</span></a><nav class="workspace-nav" aria-label="Administration sections">{nav}<a class="compact-control" href="/admin/gnss">GNSS Information</a><a class="compact-control" href="/admin/location">Location settings</a><a class="compact-control" href="/admin/details">Detailed diagnostics</a></nav></aside>
    <div class="workspace-content"><header><div class="nav"><div class="brand-group">{header_health(data)}</div><nav class="navlinks"><a class="compact-control" href="/admin/location">Location settings</a><a class="compact-control" href="/admin/details">Detailed diagnostics</a><a class="compact-control" href="#storage">Backup Settings</a><a class="compact-control" href="/">Public Homepage</a><a class="compact-control" href="/admin/password">Change Password</a><form class="logout" method="POST" action="/admin/logout"><input type="hidden" name="csrf" value="{esc(csrf)}"><button type="submit">Logout</button></form></nav></div></header>
    <main class="admin-main" id="workspace-main">{health}{console}{views}</main></div></div>"""
    script = """document.querySelectorAll('form[data-confirm]').forEach(function(form){form.addEventListener('submit',function(event){var message=form.dataset.confirm;if(message&&!window.confirm(message)){event.preventDefault();}});});
    function showSection(){var id=location.hash.slice(1)||'overview';if(id==='power-settings')id='power';if(id==='system')id='diagnostics';if(!document.querySelector('.workspace-view#'+CSS.escape(id)))id='overview';document.querySelectorAll('.workspace-view').forEach(function(view){view.hidden=view.id!==id;});document.querySelectorAll('[data-view]').forEach(function(link){if(link.dataset.view===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});}
    window.addEventListener('hashchange',showSection);showSection();var result=document.getElementById('action-result');if(result){window.addEventListener('load',function(){result.focus();result.scrollIntoView({block:'nearest'});});}
    """
    return document("PCS Administration", body, script=script, nonce=nonce, workspace_theme=True)


def location_request(payload=None):
    result = subprocess.run(
        ['sudo', '-n', '/usr/local/sbin/pcs-location', 'show' if payload is None else 'set-from-stdin'],
        input=None if payload is None else json.dumps(payload, allow_nan=False),
        text=True, capture_output=True, timeout=10, check=False,
    )
    if result.returncode:
        raise ValueError('Location settings unavailable or rejected; previous settings preserved.')
    return json.loads(result.stdout) if payload is None else {}


def render_location_page(data, csrf, error='', notice=''):
    config, status = data.get('config', {}), data.get('status', {})
    messages = (f'<p class="notice error">{esc(error)}</p>' if error else '') + (f'<p class="notice success">{esc(notice)}</p>' if notice else '')
    def value(key):
        return esc(config.get(key)) if config.get(key) is not None else ''
    body = f'''<header><div class="nav"><div class="brand">PCS Location</div><a class="button secondary" href="/admin/#diagnostics">Dashboard</a></div></header>
    <main class="admin-main"><section class="card"><h1>Manual position fallback</h1>
    <p>Current position source: <strong>{esc(status.get('source', 'Unavailable'))}</strong></p>
    {messages}
    <form method="POST" action="/admin/location" class="settings-form">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <label>Latitude (decimal degrees)<input type="number" name="latitude" min="-90" max="90" step="any" value="{value('latitude')}"></label>
    <label>Longitude (decimal degrees)<input type="number" name="longitude" min="-180" max="180" step="any" value="{value('longitude')}"></label>
    <label>Elevation above sea level (metres, optional)<input type="number" name="altitude_m" min="-500" max="10000" step="any" value="{value('altitude_m')}"></label>
    <label class="check-line"><input type="checkbox" name="enabled" value="yes"{' checked' if config.get('enabled') else ''}>Enable manual fallback</label>
    <label class="check-line"><input type="checkbox" name="persist" value="yes"{' checked' if config.get('persist') else ''}>Persist manual position even when GPS recovers</label>
    <label class="check-line"><input type="checkbox" name="confirm" value="yes" required>I understand existing APRS/Meshtastic sharing may publish this position.</label>
    <button type="submit">Save location settings</button></form>
    <p>With fallback enabled, PCS uses Manual while GPS is unavailable, and returns to GPS after a fresh fix remains usable for five seconds. Persist keeps Manual selected until you change it. GPS stays monitored in both modes.</p>
    <p>Time remains independent: Chrony prefers usable GPS time and falls back to network NTP. Manual position supplies no time, speed, heading or satellite fix. Other faults and unsynchronized time still need attention.</p>
    <p>Saved {esc(config.get('updated_at', 'not yet'))}. Coordinates are stored privately on PCS; the public dashboard shows Manual and its grid locator.</p>
    </section></main>'''
    return document('PCS Location', body)


def gnss_request(payload=None):
    """Fixed privilege boundary. GET reads snapshots; POST queues bounded work."""
    result = subprocess.run(['sudo', '-n', '/usr/local/sbin/pcs-gnss-info',
                             'web-status' if payload is None else 'web-update'],
                            input=None if payload is None else json.dumps(payload),
                            text=True, capture_output=True, timeout=5, check=False)
    if result.returncode:
        raise ValueError('GNSS information service unavailable; check staged installation and operator guide.')
    return json.loads(result.stdout)


def gnss_redacted(data):
    """Export by schema: future internal fields cannot silently become public."""
    def scalars(names):
        return dict.fromkeys(names.split())
    def project(value, schema):
        if schema is None:
            return value if type(value) in (str, int, float, bool, type(None)) else None
        if isinstance(schema, list):
            return [project(v, schema[0]) for v in value] if isinstance(value, list) else []
        return {key: project(value[key], sub) for key, sub in schema.items() if key in value} if isinstance(value, dict) else {}
    path = scalars('path band center_hz span_hz resolution_hz pga_db sample_count') | {
        key: [None] for key in ('amplitude_db', 'frequency_hz', 'peak_db', 'average_db')}
    # Azimuth/elevation plus ephemerides can reconstruct location, so geometry
    # stays on the authenticated page and is excluded from shareable exports.
    row = scalars('gnssid svid signal_id signal constellation band cno_dbhz quality_code quality health used pseudorange_used carrier_used doppler_used correction_source correction_source_name corrections_available pseudorange_correction_used carrier_correction_used doppler_correction_used sbas_correction_used rtcm_correction_used slas_correction_used spartn_correction_used clas_correction_used orbit_source autonomous_available predictive_available ephemeris_usability ephemeris_source orbit_type orbit_usability usable_prediction')
    sbas_row = scalars('gnssid svid raw_svid constellation udre_code system_code system pseudorange_correction_m ionosphere_correction_m') | {'services': [None]}
    payloads = {
        'receiver': scalars('model firmware hardware protocol'),
        'navigation': scalars('itow_ms fix_type fix_valid differential_applied utc_valid receiver_time_accuracy_ns horizontal_accuracy_m vertical_accuracy_m pdop'),
        'sbas': scalars('itow_ms geo_prn mode_code mode system_code system integrity_code integrity') | {'services': [None], 'rows': [sbas_row]},
        'rf': {'paths': [scalars('path band jamming antenna_status antenna_power noise_per_ms agc_raw agc_percent cw_suppression')]},
        'spectrum': {'paths': [path], 'calibrated': None},
        'security': scalars('jamming spoofing spoofing_code authenticity_proven'),
        'security_log': {'events': [scalars('elapsed_seconds detection_code event_code detection event severity')]},
        'signals': {'itow_ms': None, 'rows': [row]}, 'satellites': {'itow_ms': None, 'rows': [row]},
        'dop': scalars('gdop pdop tdop vdop hdop ndop edop'), 'acquisition': scalars('itow_ms fix_valid differential_applied corrections_available ttff_seconds receiver_uptime_ms acquisition'),
        'autonomous': scalars('enabled activity'), 'orbits': {'rows': [row]},
    }
    schema = scalars('state updated_at last_measurement_at connection rejected_messages') | {
        'messages': {name: scalars('state availability age_seconds received_at') | {'data': payload} for name, payload in payloads.items()},
        'receiver': scalars('model firmware hardware protocol baud verified_at'), 'serial': scalars('device baud owner'),
        'counts': scalars('visible tracked used state source') | {'constellations': scalars('GPS Galileo BeiDou SBAS QZSS GLONASS NavIC')},
        'corrections': scalars('state applied available source health reported_system selected_prn integrity mode sbas_state age_seconds correction_age') | {'services': [None], 'rows': [sbas_row]},
        'timing': scalars('mode state utc_valid pps_present pps_active pps_age_seconds pps_selection pps_validation selected_ref source synchronized offset_seconds uncertainty_seconds'),
        'chrony_sources': {name: scalars('selection reach sample_age_seconds adjusted_offset_seconds measured_offset_seconds root_distance_seconds state') for name in ('F10N', 'F10P')},
        'signal_trends': [scalars('timestamp L1 L5')],
        'capture': scalars('active remaining_seconds frame_count last_capture_timestamp average_kind') | {'traces': [path]},
        'events': [scalars('source detection_code event_code detection event severity elapsed_seconds estimated_timestamp observed_timestamp timestamp_quality')],
        'injection': scalars('state timestamp source accepted receiver_code'), 'maintenance_request': scalars('state requested_at'),
        'assistance': scalars('credentials live_entitlement live_state registration_profile') | {
            'settings': scalars('version automatic_refresh window_days refresh_hours allow_metered boot_injection live_enabled live_cost_authorized predictive_requests_day live_requests_day requests_month bytes_day'),
            'usage': scalars('state day month last_success last_uplink last_attempt next_refresh predictive_requests live_requests requests_month downloaded_bytes downloaded_bytes_day rejected retry_after'),
            **{kind: scalars('state downloaded_at expires_at bytes') | {'days': [None], 'constellations': [None]} for kind in ('predictive', 'live')},
        },
    }
    return project(data, schema)


def gnss_plot(series, xlabel, ylabel, title):
    """SVG measured points only: gaps stay gaps; no smoothing or interpolation."""
    colors = ('#50b8ff', '#ffbb55', '#86dca3', '#dc9cff', '#ff7580', '#c6d364')
    points = [(x, y) for _, values in series for x, y in values
              if type(x) in (int, float) and type(y) in (int, float) and math.isfinite(x) and math.isfinite(y)]
    if not points:
        return '<p>No measured samples available.</p>'
    xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
    ymin, ymax = min(y for _, y in points), max(y for _, y in points)
    if xmax == xmin:
        xmax += 1
    if ymax == ymin:
        ymax += 1
    marks, legend = [], []
    for n, (label, values) in enumerate(series):
        color = colors[n % len(colors)]
        legend.append(f'<span style="color:{color}">{esc(label)}</span>')
        for x, y in values:
            if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y):
                continue
            px, py = 55 + 400 * (x - xmin) / (xmax - xmin), 175 - 145 * (y - ymin) / (ymax - ymin)
            marks.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1.8" fill="{color}"><title>{esc(label)}: {x:g}, {y:g}</title></circle>')
    return f'''<figure><figcaption>{esc(title)} · {' · '.join(legend)}</figcaption>
    <svg viewBox="0 0 480 230" role="img" aria-label="{esc(title)}" style="width:100%;max-height:350px;background:var(--panel)">
    <path d="M55 20V175H455" stroke="currentColor" fill="none"/>
    <g fill="currentColor" font-size="12"><text x="5" y="35">{ymax:g}</text><text x="5" y="175">{ymin:g}</text>
    <text x="55" y="195">{xmin:g}</text><text x="455" y="195" text-anchor="end">{xmax:g}</text><text x="255" y="220" text-anchor="middle">{esc(xlabel)}</text>
    <text x="55" y="15">{esc(ylabel)}</text></g>{''.join(marks)}</svg></figure>'''


GNSS_SECTIONS = {'overview': 'Overview', 'satellites': 'Satellites', 'rf': 'RF Diagnostics',
                 'assistnow': 'AssistNow', 'advanced': 'Advanced'}


def gnss_action_section(operation):
    return ('rf' if operation in ('start-spectrum', 'stop-spectrum', 'snapshot') else
            'advanced' if operation.startswith(('autonomous-','acquisition-')) else 'assistnow')


GNSS_INTERRUPTION_CONFIRM = 'I understand GNSS positioning and PPS timing will be interrupted'


def render_gnss_confirmation(form, csrf):
    operation=form.get('operation','')
    target={'acquisition-review':'acquisition-start','navigation-snapshot-review':'navigation-snapshot',
            'navigation-restore-review':'navigation-restore'}.get(operation)
    if target is None:
        raise ValueError('Unknown review action')
    allowed={'start','source','comparison','repetitions','timeout_seconds','fallback','paid_consent','antenna'}
    values={k:v for k,v in form.items() if k in allowed} if target=='acquisition-start' else {}
    hidden=''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k,v in values.items())
    rows=''.join(f'<dt>{esc(k.replace("_"," ").title())}</dt><dd>{esc(v)}</dd>' for k,v in values.items())
    body=f'''<style>{GNSS_CSS}</style><main class="gnss-app"><section class="card"><h1>Confirm GNSS timing interruption</h1>
    <p>This operation temporarily interrupts GNSS positioning and PPS-disciplined timing. PCS withdraws its GNSS Chrony references, releases GPSD ownership, and uses an exclusive maintenance session. Internet NTP or holdover remains available.</p>
    <p>A software cold start is not an electrical power cycle. Autonomous and flash-resident data may remain. Keep the antenna location unchanged for comparisons.</p><dl>{rows}</dl>
    <p>Live Orbits can be chargeable and requires existing entitlement, activation, limits and per-experiment paid-service consent. A fresh response is shared across repetitions.</p>
    <form method="POST" action="/admin/gnss"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="operation" value="{target}">{hidden}
    <label><input type="checkbox" name="confirm" value="{esc(GNSS_INTERRUPTION_CONFIRM)}" required> {esc(GNSS_INTERRUPTION_CONFIRM)}</label>
    <button type="submit">Confirm and Start Maintenance</button></form><a class="button" href="/admin/gnss?section=advanced">Return without starting</a></section></main>'''
    return document('Confirm PCS GNSS maintenance',body)


def gnss_epochs_match(a, b):
    if type(a) is not int or type(b) is not int or not 0 <= a < 604800000 or not 0 <= b < 604800000:
        return False
    gap = abs(a - b)
    return min(gap, 604800000 - gap) <= 2000

GNSS_CSS = """
.gnss-app{max-width:1440px;padding:20px 24px 40px;--radius:14px;font-size:15px;line-height:1.5}
.gnss-app *{box-sizing:border-box;min-width:0}.gnss-app h1{font-size:26px;margin:0}.gnss-app h2{font-size:17px;margin:0 0 12px}.gnss-app h3{font-size:15px;margin:12px 0 8px}
.gnss-app p{margin:8px 0;color:var(--muted)}.gnss-app a{color:var(--accent)}
.gnss-heading,.gnss-titleline{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.gnss-eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 3px}
.gnss-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;padding:5px;border:1px solid var(--line);border-radius:12px;background:var(--panel);margin:18px 0}
.gnss-tabs a{display:flex;align-items:center;justify-content:center;min-height:44px;padding:8px 6px;text-align:center;color:var(--muted);text-decoration:none;font-weight:600;border-radius:8px;font-size:14px}.gnss-tabs a[aria-current=page]{color:var(--text);background:var(--panel2);box-shadow:inset 0 -2px var(--accent)}
.gnss-app .card{padding:18px;margin:0;border-radius:var(--radius);border:1px solid var(--line);background:var(--panel);overflow-wrap:anywhere}
.gnss-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:16px}.gnss-metric{padding:16px!important}.gnss-metric .label{font-size:12px;color:var(--muted);display:block}.gnss-metric strong{display:block;font-size:23px;line-height:1.25;margin:8px 0}.gnss-metric small{font-size:12px;color:var(--muted);display:block}
.gnss-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.gnss-grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}.gnss-stack{display:grid;gap:16px}.gnss-app dl{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:10px 16px;margin:0}.gnss-app dt{color:var(--muted);font-size:13px}.gnss-app dd{margin:0;text-align:right;overflow-wrap:anywhere;font-size:13px}
.gnss-app details>summary{cursor:pointer;min-height:44px;display:flex;align-items:center;gap:8px;font-weight:600}.gnss-app details>summary:before{content:'＋';color:var(--muted)}.gnss-app details[open]>summary:before{content:'−'}.gnss-app details[open]>summary{margin-bottom:12px}.gnss-app details{border-top:1px solid var(--line);margin-top:14px;padding-top:6px}
.gnss-app .table-wrap{max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}.gnss-app table{width:100%;border-collapse:collapse;font-size:13px}.gnss-app th,.gnss-app td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}.gnss-app th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.gnss-app figure{margin:12px 0 0}.gnss-app figcaption{color:var(--muted);font-size:12px;margin:0 0 8px}.gnss-app svg{display:block;max-width:100%;height:auto}.gnss-app .sky{width:100%;max-height:360px;margin:auto}
.gnss-app form{display:flex;flex-wrap:wrap;align-items:end;gap:10px;margin:14px 0}.gnss-app label{font-size:12px;color:var(--muted);display:grid;gap:5px;flex:1 1 140px}.gnss-app label:has(input[type=checkbox]){display:flex;align-items:center;flex-basis:100%;line-height:1.5}.gnss-app input,.gnss-app select{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px;min-height:44px;width:100%;font-size:14px}.gnss-app input[type=checkbox]{width:20px;min-height:20px;flex:none}
.gnss-app button,.gnss-app .button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:8px;min-height:44px;padding:10px 14px;font-size:13px;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.gnss-app button:disabled{opacity:.45;cursor:not-allowed}.gnss-app button:hover:not(:disabled),.gnss-app .button:hover{border-color:var(--accent)}.gnss-app .badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;background:var(--panel2);padding:5px 9px;border-radius:20px;color:var(--muted)}.gnss-app .badge.good{color:var(--ok)}.gnss-app .badge.warn{color:var(--warn)}.gnss-app .badge.bad{color:var(--bad)}.gnss-app .notice{margin:12px 0;padding:12px;font-size:13px}.gnss-app .fine{font-size:12px}.gnss-app .section-intro{margin-bottom:16px}.gnss-app .controls{border-left:3px solid var(--line);padding-left:14px;margin-top:18px}.gnss-app .hero-value{font-size:28px;font-weight:600;color:var(--text)}.gnss-app .pills{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.gnss-app .event-list{padding-left:20px;font-size:13px}.gnss-app .event-list li{margin:12px 0}
@media(max-width:1000px){.gnss-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.gnss-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.gnss-grid>.card:last-child:nth-child(3){grid-column:1/-1}}
@media(max-width:600px){.gnss-app{padding:16px 12px 28px;font-size:14px}.gnss-app h1{font-size:22px}.gnss-heading .button{padding:8px 10px}.gnss-tabs{gap:2px;padding:3px;margin:16px 0}.gnss-tabs a{font-size:11px;padding:7px 3px;line-height:1.25}.gnss-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:12px}.gnss-metric{padding:12px!important}.gnss-metric strong{font-size:21px}.gnss-metric .label,.gnss-metric small{font-size:11px}.gnss-grid,.gnss-grid.two{grid-template-columns:minmax(0,1fr);gap:12px}.gnss-app .card{padding:14px}.gnss-app dl{gap:9px 12px}.gnss-app .hero-value{font-size:25px}.gnss-app form>button{flex:1 1 auto}.gnss-app .section-intro{font-size:13px}.gnss-app .gnss-titleline{align-items:flex-start}}
"""


def gnss_sky_plot(rows):
    colors = {0: '#68b7ff', 1: '#f2c55c', 2: '#61d69b', 3: '#dc9cff'}
    marks = []
    for row in rows:
        elevation, azimuth = row.get('elevation_deg'), row.get('azimuth_deg')
        if type(elevation) not in (int, float) or type(azimuth) not in (int, float) or not 0 <= elevation <= 90 or not 0 <= azimuth <= 360:
            continue
        radius, angle = (90 - elevation) * 1.4, math.radians(azimuth)
        x, y = 160 + math.sin(angle) * radius, 160 - math.cos(angle) * radius
        color = colors.get(row.get('gnssid'), '#a8b5c0')
        label = f"{row.get('gnssid')}:{row.get('svid')}"
        marks.append(f'<g><title>{esc(label)} · {elevation}° elevation · {azimuth}° azimuth · used {row.get("used", False)}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="10" stroke="{color}" fill="{color if row.get("used") else "var(--panel)"}"/><text x="{x:.1f}" y="{y+3:.1f}" text-anchor="middle" fill="{ "var(--bg)" if row.get("used") else color}" font-size="8">{esc(row.get('svid'))}</text></g>')
    if not marks:
        return '<p>No measured satellite geometry available.</p>'
    return '<svg class="sky" viewBox="0 0 320 320" role="img" aria-label="Satellite sky plot: north up, filled satellites used in navigation"><g fill="none" stroke="var(--line)"><circle cx="160" cy="160" r="126"/><circle cx="160" cy="160" r="84"/><circle cx="160" cy="160" r="42"/><path d="M34 160H286M160 34V286"/></g><g fill="var(--muted)" font-size="12" text-anchor="middle"><text x="160" y="22">N</text><text x="302" y="164">E</text><text x="160" y="308">S</text><text x="18" y="164">W</text></g>' + ''.join(marks) + '</svg>'


def render_gnss_page(data, csrf, notice='', error='', view='L1', trace='current', section='overview', constellation='all', band='all', used_only=False, search=''):
    messages = data.get('messages', {})
    def message(name):
        return messages.get(name, {})
    def measured(name):
        return message(name).get('data') or {}
    def freshness(name):
        value = message(name)
        age = value.get('age_seconds')
        state = 'Not Supported' if value.get('availability') == 'Not Supported' else value.get('state', 'Unknown')
        return esc(state) + (f' · {age:g}s old' if type(age) in (int, float) else '')
    def table(values):
        def readable(value):
            if value is None:
                return 'Unknown'
            if isinstance(value, dict):
                return ' · '.join(str(k).replace('_', ' ').title() + ': ' + readable(v) for k, v in value.items()) or 'Unknown'
            if isinstance(value, (list, tuple)):
                return ', '.join(readable(v) for v in value) or 'None'
            if type(value) is bool:
                return 'Yes' if value else 'No'
            return str(value)
        return '<dl>' + ''.join(f'<dt>{esc(label)}</dt><dd>{esc(readable(value))}</dd>' for label, value in values) + '</dl>'
    def timestamp(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            return 'Unknown'
        try:
            return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        except (ValueError, OverflowError, OSError):
            return 'Unknown'
    def form(operation, label, fields='', confirmation=False, disabled=False):
        wording = ('I authorize this potentially chargeable Live Orbits operation.' if operation in ('live', 'refresh-live') else
                   'Request separately authorized maintenance to disable Autonomous; receiver firmware may delete retained observation data.' if operation == 'autonomous-disable' else
                   'Request separately authorized receiver RAM configuration maintenance.' if operation == 'autonomous-enable' else
                   'I confirm this assistance setting change.')
        confirm = f'<label><input type="checkbox" name="confirm" value="yes" required> {wording}</label>' if confirmation else ''
        return f'''<form method="POST" action="/admin/gnss"><input type="hidden" name="csrf" value="{esc(csrf)}">
        <input type="hidden" name="operation" value="{esc(operation)}">{fields}{confirm}
        <button type="submit" {'disabled' if disabled else ''}>{esc(label)}</button></form>'''

    section = section if section in GNSS_SECTIONS else 'overview'
    view = view if view in ('L1', 'L5', 'compare') else 'L1'
    trace = trace if trace in ('current', 'peak', 'average') else 'current'
    def card(title, contents, extra=''):
        return f'<section class="card {extra}"><h2>{esc(title)}</h2>{contents}</section>'
    def details(title, contents):
        return f'<details><summary>{esc(title)}</summary>{contents}</details>'
    def select_field(name,label,choices,selected=''):
        return f'<label>{esc(label)}<select name="{esc(name)}">'+''.join(f'<option value="{esc(value)}" {"selected" if value==selected else ""}>{esc(text)}</option>' for value,text in choices)+'</select></label>'
    def rows_table(headers, rows):
        return '<div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable data table"><table><thead><tr>' + ''.join(f'<th scope="col">{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc("Unknown" if v is None else "Yes" if v is True else "No" if v is False else v)}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    def badge(text, good=False):
        return f'<span class="badge {"good" if good else "warn"}">{esc(text)}</span>'
    nav, counts, timing = measured('navigation'), data.get('counts', {}), data.get('timing', {})
    correction = data.get('corrections', {})
    acquisition, assistance = measured('acquisition'), data.get('assistance', {})
    cache, usage, policy = assistance.get('predictive', {}), assistance.get('usage', {}), assistance.get('settings', {})
    pps_source = data.get('chrony_sources', {}).get('F10P', {})
    fix = nav.get('fix_type', 'Unknown')
    if nav.get('fix_valid') is False:
        fix = 'No valid fix'
    elif nav.get('differential_applied') and fix in ('2D', '3D'):
        fix += ' DGPS'
    sync = timing.get('synchronized') is True and str(timing.get('state', '')).lower() == 'fresh'
    selected = timing.get('selected_ref', timing.get('source', 'Unknown'))
    navigation_fields = [('Fix', fix), ('Acquisition', acquisition.get('acquisition')), ('Horizontal accuracy', f"{nav['horizontal_accuracy_m']:g} m" if nav.get('horizontal_accuracy_m') is not None else None), ('Altitude MSL (m)', nav.get('altitude_msl_m')), ('TTFF (s)', acquisition.get('ttff_seconds')), ('Navigation telemetry', freshness('navigation'))]
    correction_fields = [('Corrections applied', correction.get('applied')), ('Availability flag (NAV-STATUS)', correction.get('available')), ('Active source', correction.get('source', 'Unknown')), ('Health', correction.get('health', 'Unknown')), ('Reported SBAS system', correction.get('reported_system', 'Unknown')), ('Selected SBAS PRN', correction.get('selected_prn')), ('Services', correction.get('services')), ('Integrity', correction.get('integrity')), ('SBAS telemetry', freshness('sbas'))]
    def yesno(value):
        return 'Yes' if value is True else 'No' if value is False else 'Unknown'
    time_fields = [('Synchronized', timing.get('synchronized')), ('Selected reference', selected), ('UTC validity gate', timing.get('utc_valid')), ('PPS present / active', f"{yesno(timing.get('pps_present'))} / {yesno(timing.get('pps_active'))}"), ('PPS selection', timing.get('pps_selection')), ('Timing telemetry', timing.get('state'))]
    if section == 'overview':
        def metric(label, value, hint):
            return f'<article class="card gnss-metric"><span class="label">{esc(label)}</span><strong>{esc(value)}</strong><small>{esc(hint)}</small></article>'
        metrics = metric('GNSS fix', fix, message('navigation').get('state', 'Unknown'))
        metrics += metric('Satellites · used / visible', f"{counts.get('used') if counts.get('used') is not None else '—'} / {counts.get('visible') if counts.get('visible') is not None else '—'}", counts.get('state', 'Unknown') + ' · unique spacecraft')
        metrics += metric('Horizontal accuracy', f"{nav['horizontal_accuracy_m']:.2f} m" if nav.get('horizontal_accuracy_m') is not None else 'Unknown', 'Receiver position estimate')
        metrics += metric('Chrony / PPS', str(selected) if sync else 'Not synced' if timing.get('synchronized') is False else 'Unknown', 'Synchronized' if sync else str(timing.get('state', 'Unknown')))
        metrics += metric('Correction source', correction.get('source', 'Unknown'), correction.get('health', 'Unknown'))
        metrics += metric('AssistNow cache', cache.get('state', 'Unknown'), f"{len(cache.get('days', []))} prediction days" if cache.get('days') else 'No verified prediction dates')
        content = '<div class="gnss-metrics">' + metrics + '</div><div class="gnss-grid">'
        content += card('Navigation', table(navigation_fields) + '<p class="fine"><a href="?section=satellites">Explore satellite reception →</a></p>')
        content += card('Corrections', table(correction_fields[:1] + correction_fields[2:4] + correction_fields[5:6] + correction_fields[8:]) + details('Services and correction validity', table(correction_fields[1:2] + correction_fields[4:5] + correction_fields[6:8] + [('Receiver SBAS mode', correction.get('mode')), ('Correction age', correction.get('correction_age', 'Not reported')), ('Meaning', 'Applied solution comes from NAV-PVT; the NAV-STATUS availability flag is reported independently. Telemetry age is not correction age. AssistNow orbit predictions are not differential corrections.')])) )
        content += card('Time synchronization', table(time_fields) + details('Timing measurements', table([('Receiver time accuracy (ns)', nav.get('receiver_time_accuracy_ns')), ('PPS source offset (s)', pps_source.get('measured_offset_seconds')), ('PPS offset freshness', pps_source.get('state')), ('Chrony system offset (s)', timing.get('offset_seconds'))]) + '<p class="fine">Position accuracy, receiver time accuracy, PPS accuracy and Chrony offset describe different measurements. Faster acquisition does not establish better absolute PPS accuracy.</p>')) + '</div>'
    elif section == 'satellites':
        signal_data, sat = measured('signals'), measured('satellites')
        choices = ('all', 'GPS', 'Galileo', 'BeiDou', 'SBAS', 'QZSS', 'GLONASS', 'NavIC', 'Unknown')
        constellation = constellation if constellation in choices else 'all'
        band = band if band in ('all', 'L1', 'L5', 'Unknown') else 'all'
        search = str(search)[:64].strip().casefold()
        signals = [r for r in signal_data.get('rows', []) if (constellation == 'all' or r.get('constellation') == constellation) and (band == 'all' or r.get('band') == band) and (not used_only or r.get('used')) and (not search or search in f"{r.get('constellation')} {r.get('svid')} {r.get('signal')}".casefold())]
        sky_rows = [r for r in sat.get('rows', []) if (constellation == 'all' or {'GPS': 0, 'SBAS': 1, 'Galileo': 2, 'BeiDou': 3, 'QZSS': 5, 'GLONASS': 6, 'NavIC': 7}.get(constellation) == r.get('gnssid')) and (not used_only or r.get('used')) and (band == 'all' and not search or (r.get('gnssid'), r.get('svid')) in {(s.get('gnssid'), s.get('svid')) for s in signals})]
        filters = '<form method="GET" action="/admin/gnss"><input type="hidden" name="section" value="satellites">'
        for name, label, values, selected_value in (('constellation', 'Constellation', choices, constellation), ('band', 'Band', ('all', 'L1', 'L5', 'Unknown'), band)):
            filters += f'<label>{label}<select name="{name}">' + ''.join(f'<option value="{v}" {"selected" if v == selected_value else ""}>{"All" if v == "all" else v}</option>' for v in values) + '</select></label>'
        filters += f'<label>Find satellite or signal<input name="q" value="{esc(search)}" maxlength="64" placeholder="GPS 4, E5a…"></label><label><input type="checkbox" name="used" value="1" {"checked" if used_only else ""}> Used in navigation only</label><button>Apply filters</button></form>'
        pills = '<div class="pills">' + ''.join(f'<span class="badge">{esc(k)} {esc(v)}</span>' for k, v in counts.get('constellations', {}).items()) + '</div>'
        signal_plot = gnss_plot([(b, [(i + 1, r.get('cno_dbhz')) for i, r in enumerate(signals) if r.get('band') == b]) for b in ('L1', 'L5', 'Unknown')], 'Filtered signal row', 'C/N0 (dB-Hz)', 'Measured L1 / L5 signal strength')
        tracked = counts.get('tracked')
        content = card('Satellite reception', f'<p>{freshness("signals")} · {esc(tracked if tracked is not None else "Unknown")} tracked spacecraft · counts {esc(counts.get("state", "Unknown"))}. L1/L5 measurements count as one spacecraft.</p>' + pills + filters)
        content += '<div class="gnss-grid two">' + card('Satellite sky', f'<p class="fine">{freshness("satellites")} · North up; filled markers used. GPS blue · Galileo green · BeiDou purple · SBAS amber.</p>' + gnss_sky_plot(sky_rows)) + card('Signal strength', signal_plot + '<p class="fine">Each point is one measured signal. Gaps are not interpolated.</p>') + '</div>'
        geometry = {(r.get('gnssid'), r.get('svid')): r for r in sat.get('rows', [])} if message('satellites').get('state') == 'Fresh' and message('signals').get('state') == 'Fresh' and gnss_epochs_match(signal_data.get('itow_ms'), sat.get('itow_ms')) else {}
        rows = []
        for r in signals:
            g = geometry.get((r.get('gnssid'), r.get('svid')), {})
            corrections_used = ', '.join(label for key, label in (('pseudorange_correction_used', 'PR'), ('carrier_correction_used', 'Carrier'), ('doppler_correction_used', 'Doppler')) if r.get(key)) or 'None'
            rows.append((r.get('constellation'), r.get('svid'), r.get('signal'), r.get('band'), r.get('cno_dbhz'), r.get('quality'), r.get('used'), r.get('health'), g.get('elevation_deg'), g.get('azimuth_deg'), r.get('correction_source_name', 'Unknown'), corrections_used))
        content += card('Signal details', details(f'Show {len(rows)} matching signal measurements', rows_table(('GNSS', 'SV', 'Signal', 'Band', 'dB-Hz', 'Tracking', 'Used', 'Health', 'Elevation °', 'Azimuth °', 'Correction source', 'Corrections used'), rows)))
        trends = data.get('signal_trends', [])
        content += card('Reception history', details('Measured L1 / L5 signal-count trend', gnss_plot([(b, [(r['timestamp'], r[b]) for r in trends]) for b in ('L1', 'L5')], 'Host timestamp (Unix s)', 'Tracked signals', 'Measured signal counts')))
    elif section == 'rf':
        security, capture = measured('security'), data.get('capture', {})
        content = '<div class="gnss-grid">' + card('Signal security', table([('Jamming', security.get('jamming', 'Unknown')), ('Spoofing', security.get('spoofing', 'Unknown')), ('Telemetry', freshness('security'))]) + '<p class="fine">No spoofing indication does not prove authentic satellite signals.</p>')
        for path in measured('rf').get('paths', []):
            content += card(f"{path.get('band', 'Unknown')} receiver path", table([('Jamming', path.get('jamming')), ('AGC', f"{path['agc_percent']:.1f}%" if path.get('agc_percent') is not None else 'Unknown'), ('Noise / ms', path.get('noise_per_ms')), ('CW suppression', path.get('cw_suppression')), ('Telemetry', freshness('rf'))]) + details('RF hardware readings', table(list(path.items()))))
        content += '</div>'
        paths = measured('spectrum').get('paths', []) if trace == 'current' else capture.get('traces', [])
        chosen = [p for p in paths if view == 'compare' or p.get('band') == view]
        amplitude_key = {'peak': 'peak_db', 'average': 'average_db'}.get(trace, 'amplitude_db')
        if view == 'compare':
            series = [(p.get('band', 'Unknown'), list(zip([(f-p['center_hz'])/1e6 for f in p['frequency_hz']], p.get(amplitude_key, [])))) for p in chosen]
            plot = gnss_plot(series, 'Offset from reported center (MHz)', 'Receiver amplitude (dB)', 'L1 / L5 path comparison · ' + trace)
        else:
            plot = gnss_plot([(p.get('band', 'Unknown'), list(zip([f/1e6 for f in p['frequency_hz']], p.get(amplitude_key, [])))) for p in chosen], 'Frequency (MHz)', 'Receiver amplitude (dB)', view + ' measured spectrum · ' + trace)
        fields = '<form method="GET" action="/admin/gnss"><input type="hidden" name="section" value="rf">'
        for name, label, values, selected_value in (('view', 'RF path', ('L1', 'L5', 'compare'), view), ('trace', 'Trace', ('current', 'peak', 'average'), trace)):
            fields += f'<label>{label}<select name="{name}">' + ''.join(f'<option {"selected" if v == selected_value else ""}>{v}</option>' for v in values) + '</select></label>'
        fields += '<button>View spectrum</button></form>'
        spectrum_metadata = table([(f"{p.get('band')} path {p.get('path')}", f"Center {p.get('center_hz')} Hz · span {p.get('span_hz')} Hz · resolution {p.get('resolution_hz')} Hz · PGA {p.get('pga_db')} dB") for p in chosen])
        controls = form('start-spectrum', 'Start Spectrum Capture', '<label>Duration (5–300 s)<input type="number" name="seconds" min="5" max="300" value="60"></label>', disabled=message('spectrum').get('state') != 'Fresh' or capture.get('active', False))
        controls += form('stop-spectrum', 'Stop Spectrum Capture') + form('snapshot', 'Capture Snapshot', disabled=message('spectrum').get('state') != 'Fresh')
        trace_time = timestamp(message('spectrum').get('received_at') if trace == 'current' else capture.get('last_capture_timestamp'))
        content += card('Spectrum monitor', fields + f'<p class="fine">{freshness("spectrum")} live telemetry · {esc(trace)} trace captured {esc(trace_time)}</p>' + plot + details('Frequency and capture metadata', spectrum_metadata + table([('Capture active', capture.get('active')), ('Remaining (s)', capture.get('remaining_seconds')), ('Measured frames', capture.get('frame_count')), ('Last capture', timestamp(capture.get('last_capture_timestamp')))])) + '<div class="controls"><h3>Administrator · passive capture</h3>' + controls + '</div><p class="fine">Receiver diagnostic, not a calibrated spectrum analyzer. Capture records existing MON-SPAN output for at most 300 seconds; peak and average use at most 60 matching measured frames. Ten snapshots are retained. No serial writes or timing-service restarts.</p>')
        event_rows = ''.join(f'<li><strong>{esc(e.get("severity"))} · {esc(e.get("detection"))}</strong><br>{esc(e.get("event"))}<br>Observed {esc(timestamp(e.get("observed_timestamp")))} · estimated {esc(timestamp(e.get("estimated_timestamp")))}</li>' for e in reversed(data.get('events', [])))
        content += card('RF event history', f'<p>{len(data.get("events", []))} retained events · {freshness("security_log")}</p>' + details('View RF Event History', '<ol class="event-list">' + event_rows + '</ol>' if event_rows else '<p>No events observed.</p>') + '<p class="fine">Compare timestamps with LTE, Starlink, radio and regulator activity logs. Diagnostics never transmit or change power settings. Unknown, Not Supported, Disabled, No Indication, Warning and Critical remain distinct states.</p>')
    elif section in ('assistnow', 'advanced'):
        assistance = data.get('assistance', {})
        policy = assistance.get('settings', {})
        aop = measured('autonomous')
        orbit_rows = measured('orbits').get('rows', [])
        available = sum(r.get('usable_prediction') is True and r.get('orbit_type') == 2 for r in orbit_rows)
        cache, live_cache = assistance.get('predictive', {}), assistance.get('live', {})
        usage, injection = assistance.get('usage', {}), data.get('injection', {})
        assistance_html = table([('Autonomous enabled', aop.get('enabled')), ('Autonomous activity', aop.get('activity')),
            ('Autonomous freshness', freshness('autonomous')), ('Usable Autonomous predictions (observed)', available if orbit_rows else None),
            ('Orbit freshness', freshness('orbits')), ('Predictive cache', cache.get('state', 'Unknown')),
            ('Cache downloaded', timestamp(cache.get('downloaded_at'))), ('Cache expires', timestamp(cache.get('expires_at'))),
            ('Available prediction days', cache.get('days')), ('Cache constellations', cache.get('constellations')),
            ('Live entitlement', assistance.get('live_entitlement')), ('Live activation', assistance.get('live_state')),
            ('Account profile / evaluation access', assistance.get('registration_profile', 'Unknown; verify the Thingstream device profile')),
            ('Live cache', live_cache.get('state', 'Unknown')), ('Live cache expires', timestamp(live_cache.get('expires_at'))),
            ('Download status', usage.get('state')), ('Last online success', timestamp(usage.get('last_success'))),
            ('Selected uplink used', usage.get('last_uplink')), ('Next refresh', timestamp(usage.get('next_refresh')) if policy.get('automatic_refresh') else 'Automatic refresh disabled'),
            ('Usage accounting day', usage.get('day')), ('Predictive requests on that day', usage.get('predictive_requests')),
            ('Live requests on that day', usage.get('live_requests')), ('Usage accounting month', usage.get('month')),
            ('All requests in that month', usage.get('requests_month')), ('Downloaded payload bytes', usage.get('downloaded_bytes')),
            ('Downloaded payload bytes on accounting day', usage.get('downloaded_bytes_day')), ('Rejected/failed downloads', usage.get('rejected')),
            ('Last injection outcome', injection.get('state', 'Not attempted')), ('Last injection time', timestamp(injection.get('timestamp'))),
            ('Accepted assistance frames', injection.get('accepted')), ('Maintenance request', data.get('maintenance_request', {}).get('state', 'None'))])
        for operation, label in [('autonomous-enable', 'Request Enable Autonomous'), ('autonomous-disable', 'Request Disable Autonomous')]:
            assistance_html += form(operation, label, confirmation=True)
        assistance_html += form('refresh-predictive', 'Refresh Predictive Orbits Now')
        for operation, label, setting in [('automatic-refresh', 'Automatic Refresh', 'automatic_refresh'), ('allow-metered', 'Metered / Unknown Uplink Downloads', 'allow_metered'),
                                          ('boot-injection', 'Boot Cache Injection', 'boot_injection'), ('live', 'Live Orbits (potentially chargeable)', 'live_enabled')]:
            enabled = policy.get(setting, False)
            fields = f'<input type="hidden" name="enabled" value="{"no" if enabled else "yes"}">'
            assistance_html += form(operation, ('Disable ' if enabled else 'Enable ') + label, fields, confirmation=True)
        fields = '<label>Prediction window<select name="window_days">' + ''.join(f'<option value="{day}" {"selected" if policy.get("window_days", 7) == day else ""}>{day} days</option>' for day in (1, 3, 7, 14)) + '</select></label>'
        for name, label, default in [('refresh_hours', 'Refresh interval (hours)', 72), ('predictive_requests_day', 'Predictive requests/day (max 2)', 2),
                                      ('live_requests_day', 'Live requests/day (max 10)', 1), ('requests_month', 'All requests/month (max 300)', 250),
                                      ('bytes_day', 'Data reservation limit/day (bytes)', 1048576)]:
            fields += f'<label>{esc(label)}<input type="number" name="{name}" value="{esc(policy.get(name, default))}" min="0" required></label>'
        assistance_html += form('policy', 'Save Prediction Window and Limits', fields, confirmation=True)
        assistance_html += form('refresh-live', 'Request Live Orbits Now (potentially chargeable)', confirmation=True, disabled=not policy.get('live_enabled'))
        assistance_html += form('clear-expired', 'Clear Expired Cache')
        assistance_html += '<p><button disabled>Backup Receiver Assistance Data</button> <button disabled>Restore Receiver Assistance Data</button> Not qualified; separate maintenance and firmware-specific validation required.</p>'
        maintenance_at = assistance_html.index('<form')
        # Receiver operations belong in Advanced; host cache controls stay here.
        first_host_action = assistance_html.index('<form', assistance_html.index('Request Disable Autonomous'))
        maintenance_controls = assistance_html[maintenance_at:first_host_action]
        host_controls = assistance_html[first_host_action:].partition('<p><button disabled>Backup Receiver Assistance Data')[0]
        if section == 'assistnow':
            next_refresh = timestamp(usage.get('next_refresh')) if policy.get('automatic_refresh') else 'Automatic refresh disabled'
            content = '<div class="gnss-grid">'
            days = cache.get('days', [])
            prediction_range = f'{len(days)} days · {min(days)} → {max(days)}' if days else 'Unknown'
            content += card('Predictive Orbits', f'<div class="hero-value">{esc(cache.get("state", "Unknown"))}</div><p>Next refresh · {esc(next_refresh)}</p>' + table([('Prediction validity', prediction_range), ('Expires', timestamp(cache.get('expires_at'))), ('Constellations', cache.get('constellations')), ('Downloaded', timestamp(cache.get('downloaded_at')))]))
            content += card('Autonomous', table([('Enabled', aop.get('enabled')), ('Activity', aop.get('activity')), ('Usable predictions observed', available if orbit_rows else None), ('Telemetry', freshness('autonomous')), ('Orbit telemetry', freshness('orbits'))]) + '<p class="fine">Receiver-generated predictions. Retention after complete power loss is not guaranteed.</p>')
            content += card('Live Orbits', table([('Entitlement', assistance.get('live_entitlement')), ('Activation', assistance.get('live_state')), ('Account profile', assistance.get('registration_profile')), ('Cache', live_cache.get('state', 'Unknown'))]) + '<p class="fine">Potentially chargeable. Explicit activation and request limits still apply.</p>') + '</div>'
            content += card('Downloads and startup readiness', table([('Last success', timestamp(usage.get('last_success'))), ('Uplink used', usage.get('last_uplink')), ('Download state', usage.get('state')), ('Last injection', injection.get('state', 'Not attempted')), ('Injection timestamp', timestamp(injection.get('timestamp')))]) + details('Usage, validity and request accounting', assistance_html[:maintenance_at]) + '<p class="fine">Offline startup can use valid cache and Autonomous data alongside ordinary acquisition. Healthy PPS timing defers injection. AssistNow provides orbit assistance, not DGPS/SBAS corrections. GPS/Galileo predictions are qualified; BeiDou assistance is excluded pending B1C compatibility verification.</p>')
            content += card('Assistance settings', '<p class="fine">Administrator controls · confirmations required for policy changes.</p>' + details('Manage refresh, cache and service limits', host_controls))
            bench=data.get('acquisition',{})
            boot=bench.get('policy',{})
            qualification=bench.get('qualification',{})
            if bench.get('qualification_hold'):
                content+=card('Navigation database qualification on hold','<p class="fine">Unattended database restoration and shutdown snapshots require renewed hardware qualification. Confirmed manual maintenance remains available. Review the failed operation reports before changing automation settings.</p>')
            fields=select_field('boot_policy','Normal boot assistance',[(v,t) for v,t in [('automatic','Automatic · qualified database → cache → retained'),('retained','Receiver-retained only'),('database','Saved receiver database · qualification required'),('cached','Cached Predictive Orbits'),('internet','Internet Predictive Orbits · bounded, asynchronous'),('live','Live Orbits · potentially chargeable'),('none','No host assistance')]],boot.get('boot_policy','automatic'))
            fields+=select_field('fallback','Fallback policy',[('local','Valid local assistance, then retained'),('retained','Receiver-retained only'),('none','No fallback')],boot.get('fallback','local'))
            fields+=select_field('save_on_shutdown','Save navigation on clean shutdown · qualified hardware only',[('no','Disabled'),('yes','Enabled')],'yes' if boot.get('save_on_shutdown') else 'no')
            fields+=select_field('restore_next_start','Restore database on next eligible startup',[('no','Normal policy'),('yes','One successful restore · qualified hardware only')],'yes' if boot.get('restore_next_start') else 'no')
            for name,label,default in [('database_max_age_hours','Maximum database age (hours)',24),('backup_retention','Successful backups retained',5),('shutdown_budget_seconds','Shutdown collection budget (seconds)',30),('power_hold_seconds','Verified power hold interval (seconds)',0)]:
                fields+=f'<label>{label}<input type="number" name="{name}" value="{esc(boot.get(name,default))}" required></label>'
            content+=card('Boot assistance policy',table([('Policy configured',bench.get('policy_configured')),('Active policy',boot.get('boot_policy') if bench.get('policy_configured') else 'Legacy cache-only admission'),('Fallback',boot.get('fallback') if bench.get('policy_configured') else 'Existing retained/ordinary acquisition'),('Saved database',bench.get('database',{}).get('state','Unknown')),('Backup creation',timestamp(bench.get('database',{}).get('created_at'))),('Backup size (bytes)',bench.get('database',{}).get('bytes')),('Database restore qualified',qualification.get('database_restore')),('Shutdown saving qualified',qualification.get('shutdown')),('Last shutdown backup',bench.get('shutdown',{}).get('state','Not attempted'))])+details('Configure normal startup and shutdown',form('boot-policy','Save Boot Assistance Policy',fields,confirmation=True))+'<p class="fine">Installation preserves existing startup behavior until a policy is saved. Internet downloads never delay ordinary GNSS acquisition. Healthy fix/PPS defers injection. Shutdown saving defaults off and requires hardware qualification and a verified power-hold interval.</p>')
            content+=card('Pi-hosted receiver navigation database', '<p class="fine">Separate from the Thingstream orbit cache. Exact receiver and firmware binding, complete-transfer validation, maximum age and bounded retention apply. Snapshot and restore interrupt GNSS timing.</p>'+form('navigation-snapshot-review','Review Save Navigation Snapshot Now')+form('navigation-restore-review','Review Restore Saved Navigation Database')+'<p class="fine">Receiver-retained data and Autonomous predictions are internal; their survival after complete power loss is not guaranteed. Unqualified periodic snapshots remain disabled.</p>')
        else:
            receiver = measured('receiver') or data.get('receiver', {})
            serial = data.get('serial', {})
            content = '<div class="gnss-grid two">' + card('Receiver and connection', table([(k.title(), receiver.get(k)) for k in ('model', 'firmware', 'hardware', 'protocol')]) + details('Enrollment and UART', table([('Device', serial.get('device')), ('Baud', serial.get('baud')), ('Owner', serial.get('owner')), ('USB connected', timing.get('usb_connected')), ('Receiver connected', timing.get('connected')), ('Identity verified', timestamp(receiver.get('verified_at'))), ('Monitor connection', data.get('connection'))])))
            content += card('Accuracy and timing', table([('Horizontal accuracy (m)', nav.get('horizontal_accuracy_m')), ('Vertical accuracy (m)', nav.get('vertical_accuracy_m')), ('MSL altitude (m)', nav.get('altitude_msl_m')), ('Ellipsoid altitude (m)', nav.get('altitude_ellipsoid_m')), ('Receiver UTC validity', nav.get('utc_valid')), ('Receiver time accuracy (ns)', nav.get('receiver_time_accuracy_ns'))]) + details('DOP and Chrony source measurements', table([(k.upper(), v) for k, v in (measured('dop') or {'pdop': nav.get('pdop')}).items()] + [('Active timing source', timing.get('source')), ('PPS validation', timing.get('pps_validation')), ('PPS age (s)', timing.get('pps_age_seconds')), ('PPS source freshness', pps_source.get('state')), ('PPS source sample age (s)', pps_source.get('sample_age_seconds')), ('Measured PPS source offset (s)', pps_source.get('measured_offset_seconds')), ('Adjusted PPS source offset (s)', pps_source.get('adjusted_offset_seconds')), ('Chrony system offset (s)', timing.get('offset_seconds')), ('Chrony uncertainty (s)', timing.get('uncertainty_seconds'))]))) + '</div>'
            sbas = measured('sbas')
            correction_rows = [(r.get('constellation'), r.get('svid'), r.get('raw_svid'), r.get('system'), ', '.join(r.get('services', [])), r.get('udre_code'), r.get('pseudorange_correction_m'), r.get('ionosphere_correction_m')) for r in sbas.get('rows', [])]
            content += card('Correction diagnostics', table(correction_fields + [('Receiver mode', correction.get('mode')), ('Correction age', correction.get('correction_age'))]) + details('Per-satellite SBAS corrections', f'<p class="fine">{freshness("sbas")}. NAV-SBAS correction values are reception reports; application is indicated separately by NAV-PVT / NAV-SIG / NAV-SAT. UDRE is the receiver-reported monitoring code. Single-SV identifiers are mapped using the documented constellation numbering; unknown IDs remain unknown.</p>' + rows_table(('GNSS', 'SV', 'Raw SV ID', 'Reported system', 'Services', 'UDRE code', 'Pseudorange (m)', 'Ionosphere (m)'), correction_rows)))
            telemetry = [(name, msg.get('availability', 'Unknown'), msg.get('state', 'Unknown'), msg.get('age_seconds'), timestamp(msg.get('received_at'))) for name, msg in sorted(messages.items())]
            content += card('Receiver diagnostics', details('Message availability and freshness', rows_table(('Message', 'Availability', 'State', 'Age (s)', 'Received UTC'), telemetry)) + details('Raw decoded diagnostics', '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px">' + esc(json.dumps(messages, indent=2, ensure_ascii=False)) + '</pre>') + table([('Rejected messages', data.get('rejected_messages')), ('Last receiver update', timestamp(data.get('last_measurement_at')))]))
            content += card('Receiver maintenance', '<p class="fine">Privileged · changes require confirmation and a separate authorized ownership handover. GPSD owns the UART during normal operation; routine page access never writes receiver configuration.</p>' + details('Autonomous configuration requests', maintenance_controls) + '<p class="fine">Pending request: ' + esc(data.get('maintenance_request', {}).get('state', 'None')) + '</p><p><a href="?section=assistnow">Navigation snapshot and restoration →</a></p>')
            bench=data.get('acquisition',{})
            job=bench.get('job',{})
            busy=job.get('state') in ('Queued','Running','Recovering','Recovery required')
            fields=select_field('start','Software GNSS start',[('cold','Full cold · 0xFFFF'),('warm','Warm · 0x0001'),('hot','Hot · 0x0000')],'cold')
            fields+=select_field('source','Assistance source',[('none','No external assistance'),('cached','Stored Predictive Orbits · offline'),('internet','Fresh Internet Predictive Orbits'),('database','Saved receiver navigation database'),('live','Live Orbits · potentially chargeable'),('automatic','Configured normal boot policy')])
            fields+=select_field('comparison','Comparison · overrides individual start/source',[('','Single trial or repeated source'),('cold-unassisted-predictive','Cold unassisted / cached predictions'),('cold-cached-internet','Cold cached / fresh predictions'),('warm-unassisted-assisted','Warm unassisted / cached predictions'),('hot-unassisted-assisted','Hot unassisted / cached predictions'),('database-predictive','Cold database / cached predictions')])
            fields+=select_field('fallback','Experiment fallback',[('none','No fallback · recommended for comparisons'),('local','Valid local assistance'),('retained','Receiver-retained only')])
            fields+='<label>Repetitions (A/B pairs when comparing)<input type="number" name="repetitions" min="1" max="5" value="1" required></label><label>Acquisition timeout per trial (seconds)<input type="number" name="timeout_seconds" min="15" max="300" value="180" required></label><label>Antenna / conditions · private operator note<input name="antenna" maxlength="160"></label><label><input type="checkbox" name="paid_consent" value="yes"> Authorize potentially chargeable Live requests for this experiment, within existing limits</label>'
            progress=table([('Operation',job.get('kind')),('State',job.get('state','Not started')),('Progress',job.get('phase')),('GNSS UTC valid',timing.get('utc_valid')),('PPS pairing',timing.get('pps_validation')),('Selected timing reference',timing.get('selected_ref'))])
            if job.get('failure'):
                progress+=table([('Failure',job.get('failure')),('Failure phase',job.get('failure_phase')),('UART ownership recovered',job.get('ownership_recovered')),('Paired timing observed at completion',job.get('timing_recovered'))])
            database=job.get('database_evidence',{})
            if database:
                progress+=details('Navigation database transfer evidence',table([('Entries in backup',database.get('frame_count')),('Offered',database.get('offered')),('Accepted',database.get('accepted')),('Rejected',database.get('rejected')),('Unacknowledged',database.get('unacknowledged')),('Failed entry',database.get('failed_frame')),('Receiver status code',database.get('receiver_code')),('Transfer complete',database.get('complete'))])+'<p class="fine">Unknown receiver status codes remain unclassified and fail the transfer. UART ownership recovery and valid UTC/PPS recovery are separate observations.</p>')
            results=[]
            for row in job.get('results',[]):
                metric=row.get('metrics') or {}
                results.append((row.get('trial'),row.get('start'),row.get('requested_source'),row.get('selected_source'),row.get('state'),row.get('download_seconds'),metric.get('receiver_ttff_seconds'),metric.get('first_3d_seconds'),metric.get('f10p_seconds')))
            histories=''
            for item in bench.get('history',[]):
                exports=''.join(form('acquisition-export',kind.upper(),f'<input type="hidden" name="id" value="{esc(item.get("id"))}"><input type="hidden" name="format" value="{kind}">') for kind in ('json','csv','text'))
                histories+=details(f'{timestamp(item.get("created_at"))} · {item.get("kind")} · {item.get("state")}',exports)
            comparison=job.get('summary',{})
            content=card('Acquisition Testing', '<p class="fine">Disruptive administrator controls. Timing interruption requires a separate confirmation screen. Software resets preserve receiver configuration and PPS electrical settings.</p>'+progress+('<p><a class="button" href="?section=advanced">Update Test Progress</a></p>' if busy else '')+details('Configure a test or A/B comparison',form('acquisition-review','Review Acquisition Test',fields,disabled=busy))+form('acquisition-cancel','Cancel and Recover',disabled=not busy)+form('acquisition-recover','Recover Interrupted Maintenance')+details('Measured trial results',rows_table(('Trial','Start','Requested','Selected','Result','Download s','Receiver TTFF s','Host 3D s','F10P recovery s'),results)+ '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(json.dumps(comparison,indent=2))+'</pre>')+details('History and redacted reports',histories)+'<p class="fine">Download latency, receiver TTFF and PPS-disciplined NTP recovery are separate measurements. Retained Autonomous/flash data and warm/hot trial carry-over prevent a claim of equivalent fully unassisted conditions. A/B order alternates. Never move the antenna between trials.</p>')+content
            content += card('Diagnostic exports', f'<p class="fine">Exports omit credentials, device identity, coordinates, altitude and satellite geometry.</p><form method="POST" action="/admin/gnss/export"><input type="hidden" name="csrf" value="{esc(csrf)}"><button>Export Redacted Diagnostic and Assistance Report</button></form>')
    links = ''.join(f'<a href="?section={key}" {"aria-current=page" if key == section else ""}>{label}</a>' for key, label in GNSS_SECTIONS.items())
    message_text = error or notice
    notification = f'<p class="notice" role="status">{esc(message_text)}</p>' if message_text else ''
    state = data.get('state', 'Unavailable')
    body = f'<style>{GNSS_CSS}</style><main class="gnss-app"><div class="gnss-heading"><div><div class="gnss-eyebrow">PCS · Navigation &amp; timing</div><h1>GNSS Information</h1></div><a class="button" href="/admin/">Administration</a></div><nav class="gnss-tabs" aria-label="GNSS sections">{links}</nav><div class="gnss-titleline section-intro"><div>{badge(state, state == "Fresh")} <span class="fine">Last receiver update {esc(timestamp(data.get("last_measurement_at")))}</span></div><a class="button" href="?section={section}&amp;view={view}&amp;trace={trace}">Refresh</a></div>{notification}<div class="gnss-stack">{content}</div><p class="fine">Read-only telemetry · authenticated administrator controls · missing and stale data remain explicitly marked.</p></main>'
    result=document('PCS GNSS Information', body)
    if section=='advanced' and data.get('acquisition',{}).get('job',{}).get('state') in ('Queued','Running','Recovering'):
        result=result.replace(b'</head>',b'<meta http-equiv="refresh" content="5"></head>',1)
    return result


def bandwidth_request(payload=None):
    """Fixed sudo allowlist entries; no user-controlled command arguments."""
    command = 'web-status' if payload is None else 'web-update'
    result = subprocess.run(
        ['sudo', '-n', '/usr/local/sbin/pcs-bandwidth', command],
        input=None if payload is None else json.dumps(payload),
        text=True, capture_output=True, timeout=90, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip()[:500] or 'Bandwidth manager unavailable')
    return json.loads(result.stdout)


def render_bandwidth_page(data: dict, csrf: str, error: str = '', notice: str = '') -> bytes:
    messages = (f'<p class="notice error">{esc(error)}</p>' if error else '')
    messages += f'<p class="notice success">{esc(notice)}</p>' if notice else ''
    config = data.get('config', {})
    token = f'<input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="revision" value="{esc(data.get("revision", ""))}">'

    def field(name, label, value='', required=False):
        return f'<label>{esc(label)}<input type="text" name="{name}" value="{esc(value)}" maxlength="100" {"required" if required else ""}></label>'

    def usage_text(value):
        return f'{value / 1_000_000:.2f} MB' if isinstance(value, (int, float)) else '—'

    network_forms = []
    for name, profile in config.get('profiles', {}).items():
        active = ' · selected' if name == config.get('profile') else ''
        fields = ''
        if profile.get('enabled'):
            for key, label in [('total_down', 'Network download ceiling'), ('total_up', 'Network upload ceiling'),
                               ('default_down', 'Default download per client'), ('default_up', 'Default upload per client')]:
                fields += field(key, label, profile.get(key, ''), key.startswith('default'))
        else:
            fields = '<p>Remove all client and network rate limits.</p>'
        network_forms.append(f'''<details class="card" {"open" if active else ""}><summary>{esc(name)}{active}</summary>
        <form class="settings-form" method="POST" action="/admin/bandwidth">{token}
        <input type="hidden" name="operation" value="network"><input type="hidden" name="profile" value="{esc(name)}">
        {fields}<button type="submit">Save and activate {esc(name)}</button></form></details>''')
    usage = {c['ip']: c for c in data.get('clients', [])}
    rows = []
    for client in config.get('clients', []):
        stats = usage.get(client['ip'], {})
        policy = 'Unlimited within network ceiling' if client.get('unlimited') else f"{client.get('download', 'default')} / {client.get('upload', 'default')}"
        rows.append(f'''<tr><td>{esc(client.get('name', client['ip']))}<br>{esc(client['ip'])}</td><td>{esc(policy)}</td>
        <td>{usage_text(stats.get('down_bytes'))} / {usage_text(stats.get('up_bytes'))}</td><td>
        <details><summary>Edit</summary><form class="settings-form" method="POST" action="/admin/bandwidth">{token}
        <input type="hidden" name="operation" value="client"><input type="hidden" name="ip" value="{esc(client['ip'])}">
        {field('name', 'Name', client.get('name', ''))}{field('download', 'Download', client.get('download', ''))}{field('upload', 'Upload', client.get('upload', ''))}
        <label><input type="checkbox" name="unlimited" value="yes" {"checked" if client.get('unlimited') else ""}> No individual limit</label>
        <button>Save client</button></form></details>
        <form method="POST" action="/admin/bandwidth">{token}<input type="hidden" name="operation" value="delete-client">
        <input type="hidden" name="ip" value="{esc(client['ip'])}"><button class="secondary">Use defaults</button></form></td></tr>''')
    unknown = ''.join(f"<li>{esc(c['ip'])}: RX {usage_text(c['down_bytes'])} / TX {usage_text(c['up_bytes'])}</li>"
                      for c in data.get('clients', []) if not c.get('explicit'))
    controls = ''
    if config:
        controls = f'''<h2>Whole network</h2><p>Rates accept values such as 5M or 5mbit. Leave a network ceiling blank for no aggregate cap. Profile selection is manual.</p>
        <div class="grid">{''.join(network_forms)}</div>
        <section class="card"><h2>Individual clients</h2><div style="overflow-x:auto"><table><thead><tr><th>Client</th><th>Download / upload</th><th>RX / TX</th><th>Policy</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
        <details><summary>Add or replace a client override</summary><form class="settings-form" method="POST" action="/admin/bandwidth">{token}
        <input type="hidden" name="operation" value="client">
        {field('ip', 'Client IPv4 address', required=True)}{field('name', 'Device name (optional)')}
        {field('download', 'Download limit (blank uses profile default)')}{field('upload', 'Upload limit (blank uses profile default)')}
        <label><input type="checkbox" name="unlimited" value="yes"> Trusted client: no individual limit</label>
        <button type="submit">Save client policy</button></form></details></section>
        <section class="card"><h2>Other client usage</h2><ul>{unknown or '<li>No traffic recorded.</li>'}</ul></section>'''
    issues = '; '.join(data.get('issues', []))
    body = f'''<header><div class="nav"><div class="brand">PCS Bandwidth</div><nav class="navlinks"><a class="button secondary" href="/admin/">Administration</a><a class="button secondary" href="/admin/bandwidth">Refresh</a></nav></div></header>
    <style>.bandwidth-page .grid{{align-items:start;margin-bottom:1rem}}.bandwidth-page .card{{margin-bottom:1rem}}.bandwidth-page .settings-form{{display:grid;grid-template-columns:1fr;gap:.8rem;margin-top:1rem}}.bandwidth-page input[type=text]{{display:block;width:100%;padding:.65rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);margin-top:.35rem}}.bandwidth-page summary{{cursor:pointer;font-weight:650}}.bandwidth-page table{{width:100%;border-collapse:collapse;text-align:left}}.bandwidth-page th,.bandwidth-page td{{padding:.7rem;border-bottom:1px solid var(--line);vertical-align:top}}.bandwidth-page td form{{margin-top:.65rem}}</style>
    <main class="admin-main bandwidth-page">{messages}<section class="overview"><div><h1>Bandwidth settings</h1><p>{esc(data.get('status', 'Unavailable'))} · Profile: {esc(config.get('profile', '—'))}</p>
    <p>{esc(issues)}</p></div></section>{controls}<p>Local PCS services stay outside these limits. IPv6 is not limited or counted in this version. Usage counters may reset when settings change or PCS restarts.</p></main>'''
    return document('PCS Bandwidth', body)


def render_login_page(csrf: str, error: str = "", notice: str = "") -> bytes:
    error_html = f'<div class="notice error">{esc(error)}</div>' if error else ""
    notice_html = f'<div class="notice success">{esc(notice)}</div>' if notice else ""
    configured_note = "" if AUTH.configured() else '<div class="notice">Administration is locked until an admin password is configured from the PCS terminal.</div>'
    body = f"""
    <header><div class="nav"><div class="brand">PCS Administration</div><nav class="navlinks"><a class="button secondary" href="/">Public Homepage</a></nav></div></header>
    <main class="login-wrap"><section class="login-panel"><p class="eyebrow">Authorized operators</p><h1>Admin Login</h1><p class="small">Enter the local PCS administrator password to access controls and detailed diagnostics.</p>{configured_note}{notice_html}{error_html}
    <form method="POST" action="/admin/login"><input type="hidden" name="csrf" value="{esc(csrf)}"><label for="password">Admin password</label><input id="password" name="password" type="password" required autocomplete="current-password"><button type="submit">Sign in to PCS Administration</button></form></section></main>"""
    return document("PCS Admin Login", body)


def render_password_page(csrf: str, error: str = "") -> bytes:
    error_html = f'<div class="notice error">{esc(error)}</div>' if error else ""
    body = f"""
    <header><div class="nav"><div class="brand">Change Admin Password</div><nav class="navlinks"><a class="button secondary" href="/admin/">Back to Administration</a></nav></div></header>
    <main class="login-wrap"><section class="login-panel"><p class="eyebrow">Administrator security</p><h1>Change Admin Password</h1><div class="notice"><strong>Forgotten passwords cannot be changed here.</strong> Rerun <code>./scripts/setup-pcs-control-panel.sh --reset-admin-password</code> from an interactive Pi terminal.</div><p class="small">This also updates the <code>pcs-admin</code> login for <code>PCS-Backup</code>.</p>{error_html}
    <form method="POST" action="/admin/password"><input type="hidden" name="csrf" value="{esc(csrf)}"><label for="current-password">Current password</label><input id="current-password" name="current_password" type="password" required autocomplete="current-password"><label for="new-password">New password</label><input id="new-password" name="new_password" type="password" required minlength="12" autocomplete="new-password"><label for="confirm-password">Confirm new password</label><input id="confirm-password" name="confirm_password" type="password" required minlength="12" autocomplete="new-password"><button type="submit">Update Admin and Backup Password</button></form></section></main>"""
    return document("Change PCS Admin Password", body)


def cookie_value(header: str | None, name: str) -> str | None:
    if not header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(header)
        return cookie[name].value if name in cookie else None
    except Exception:
        return None


def cookie_header(name: str, value: str, path: str, max_age: int | None = None, http_only: bool = True) -> str:
    parts = [f"{name}={value}", f"Path={path}", "SameSite=Strict"]
    if http_only:
        parts.append("HttpOnly")
    if COOKIE_SECURE:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "PCSWeb/2"

    def security_headers(self, nonce: str = "") -> None:
        script_policy = f"'nonce-{nonce}'" if nonce else "'none'"
        self.send_header("Content-Security-Policy", f"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src {script_policy}; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")

    def send_body(self, status: int, body: bytes, content_type: str, nonce: str = "", cookies: list[str] | None = None, head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.security_headers(nonce)
        for value in cookies or []:
            self.send_header("Set-Cookie", value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def redirect(self, location: str, cookies: list[str] | None = None) -> None:
        body = f'<p>Continue to <a href="{esc(location)}">{esc(location)}</a>.</p>'.encode("utf-8")
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        for value in cookies or []:
            self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(body)

    def session(self) -> tuple[str | None, dict | None]:
        raw = cookie_value(self.headers.get("Cookie"), "pcs_admin_session")
        return SESSIONS.validate(raw)

    def client_ip(self) -> str:
        """Trust only nginx's overwritten single address, from loopback peers."""
        peer = self.client_address[0]
        if TRUST_PROXY and peer in {"127.0.0.1", "::1"}:
            forwarded = self.headers.get("X-Real-IP", "")
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return peer

    def read_form(self) -> dict[str, str] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_FORM_BYTES:
            return None
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        values = parse_qs(raw, keep_blank_values=True)
        return {key: entries[0] for key, entries in values.items() if entries}

    def require_session(self) -> tuple[str | None, dict | None]:
        session_id, session = self.session()
        if not session:
            self.redirect("/admin/login")
            return None, None
        return session_id, session

    def csrf_valid(self, form: dict[str, str], session: dict) -> bool:
        supplied = form.get("csrf", "")
        expected = session.get("csrf", "")
        return bool(supplied and expected and hmac.compare_digest(supplied, expected))

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path == "/admin":
            self.redirect("/admin/")
            return
        if path in {"/admin/", "/admin/password", "/admin/bandwidth", "/admin/gnss"} and not self.session()[1]:
            self.redirect("/admin/login")
            return
        if path in {"/", "/status/", "/health", "/api/public-status", "/admin/", "/admin/login", "/admin/password", "/admin/bandwidth", "/admin/gnss"}:
            content_type = "application/json; charset=utf-8" if path == "/api/public-status" else "text/html; charset=utf-8"
            self.send_body(200, b"", content_type, head_only=True)
            return
        self.send_body(404, b"", "text/plain; charset=utf-8", head_only=True)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == '/admin/gnss':
            _, session = self.require_session()
            if not session:
                return
            try:
                data, error = gnss_request(), ''
            except (ValueError, OSError, subprocess.SubprocessError):
                data, error = {}, 'GNSS information unavailable. Check optional service installation.'
            query = parse_qs(parsed.query)
            view, trace = query.get('view', ['L1'])[0], query.get('trace', ['current'])[0]
            view = view if view in ('L1', 'L5', 'compare') else 'L1'
            trace = trace if trace in ('current', 'peak', 'average') else 'current'
            section = query.get('section', ['overview'])[0]
            section = section if section in GNSS_SECTIONS else 'overview'
            notice = 'Request accepted. Check the current outcome below.' if query.get('saved') == ['1'] else ''
            self.send_body(200, render_gnss_page(data, session['csrf'], notice, error, view, trace, section,
                query.get('constellation', ['all'])[0], query.get('band', ['all'])[0],
                query.get('used') == ['1'], query.get('q', [''])[0][:64]), 'text/html; charset=utf-8')
            return
        if path in {"/", "/status/"}:
            self.send_body(200, render_public_page(get_public_dashboard()), "text/html; charset=utf-8")
            return
        if path == "/health":
            body = json.dumps({"status": "ok", "service": "pcs-control-panel"}).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/public-status":
            body = json.dumps(get_public_dashboard(), indent=2).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")
            return
        if path == "/admin":
            self.redirect("/admin/")
            return
        if path == "/admin/login":
            if self.session()[1]:
                self.redirect("/admin/")
                return
            token = secrets.token_urlsafe(32)
            login_cookie = cookie_header("pcs_login_csrf", token, "/admin", max_age=600)
            changed = parse_qs(parsed.query).get("changed") == ["1"]
            notice = "Password updated. Sign in again with the new password." if changed else ""
            self.send_body(200, render_login_page(token, notice=notice), "text/html; charset=utf-8", cookies=[login_cookie])
            return
        if path == "/admin/":
            _, session = self.require_session()
            if not session:
                return
            nonce = secrets.token_urlsafe(18)
            backup_settings, settings_error = load_backup_settings()
            body = render_admin_page(
                get_admin_dashboard(), session["csrf"], nonce=nonce,
                backup_settings=backup_settings, settings_error=settings_error, public_data=get_public_dashboard(),
            )
            self.send_body(200, body, "text/html; charset=utf-8", nonce=nonce)
            return
        if path == "/admin/details":
            _, session = self.require_session()
            if not session:
                return
            self.send_body(200, render_admin_details_page(get_admin_dashboard(), session['csrf']), 'text/html; charset=utf-8')
            return
        if path == '/admin/location':
            _, session = self.require_session()
            if not session:
                return
            try:
                data, error = location_request(), ''
            except (ValueError, OSError, subprocess.SubprocessError):
                data, error = {}, 'Location settings are unavailable.'
            notice = 'Location settings saved.' if parse_qs(parsed.query).get('saved') == ['1'] else ''
            self.send_body(200, render_location_page(data, session['csrf'], error, notice), 'text/html; charset=utf-8')
            return
        if path == "/admin/bandwidth":
            _, session = self.require_session()
            if not session:
                return
            try:
                data, error = bandwidth_request(), ''
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                data, error = {}, str(exc)
            notice = 'Bandwidth settings applied.' if parse_qs(parsed.query).get('saved') == ['1'] else ''
            self.send_body(200, render_bandwidth_page(data, session['csrf'], error, notice), 'text/html; charset=utf-8')
            return
        if path == "/admin/password":
            _, session = self.require_session()
            if not session:
                return
            self.send_body(200, render_password_page(session["csrf"]), "text/html; charset=utf-8")
            return
        self.send_body(404, b"Not found\n", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlsplit(self.path).path
        form = self.read_form()
        if form is None:
            self.send_body(400, b"Invalid form submission\n", "text/plain; charset=utf-8")
            return

        if path in ('/admin/gnss', '/admin/gnss/export'):
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b'CSRF validation failed\n', 'text/plain; charset=utf-8')
                return
            section = gnss_action_section(form.get('operation', ''))
            try:
                if form.get('operation') in ('acquisition-review','navigation-snapshot-review','navigation-restore-review'):
                    self.send_body(200,render_gnss_confirmation(form,session['csrf']),'text/html; charset=utf-8')
                    return
                if form.get('operation')=='acquisition-export':
                    result=gnss_request({k:v for k,v in form.items() if k!='csrf'})
                    kind=form.get('format','json')
                    mime={'json':'application/json','csv':'text/csv','text':'text/plain'}.get(kind,'text/plain')
                    self.send_body(200,result['report'].encode(),mime+'; charset=utf-8')
                    return
                if path.endswith('/export'):
                    body = json.dumps(gnss_redacted(gnss_request()), indent=2, allow_nan=False).encode()
                    self.send_body(200, body, 'application/json; charset=utf-8')
                    return
                gnss_request({key: value for key, value in form.items() if key != 'csrf'})
            except (ValueError, OSError, subprocess.SubprocessError):
                self.send_body(400, render_gnss_page({}, session['csrf'], error='Request unavailable. Verify confirmation, limits, diagnostic freshness and maintenance requirements.', section=section), 'text/html; charset=utf-8')
                return
            self.redirect('/admin/gnss?section=' + section + '&saved=1')
            return

        if path == "/admin/login":
            expected = cookie_value(self.headers.get("Cookie"), "pcs_login_csrf") or ""
            supplied = form.get("csrf", "")
            address = self.client_ip()
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                self.send_body(403, render_login_page(secrets.token_urlsafe(32), "Login request expired. Reload and try again."), "text/html; charset=utf-8")
                return
            if not LOGIN_LIMITER.allowed(address):
                self.send_body(429, render_login_page(supplied, "Too many failed attempts. Wait one minute and try again."), "text/html; charset=utf-8")
                return
            password = form.get("password", "")
            if len(password) > MAX_PASSWORD_LENGTH or not AUTH.verify(password):
                LOGIN_LIMITER.failed(address)
                self.send_body(401, render_login_page(supplied, "Incorrect administrator password."), "text/html; charset=utf-8")
                return
            LOGIN_LIMITER.succeeded(address)
            session_cookie, _ = SESSIONS.create()
            cookies = [
                cookie_header("pcs_admin_session", session_cookie, "/admin", max_age=SESSION_TTL),
                cookie_header("pcs_login_csrf", "", "/admin", max_age=0),
            ]
            self.redirect("/admin/", cookies=cookies)
            return

        if path == "/admin/logout":
            session_id, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b"CSRF validation failed\n", "text/plain; charset=utf-8")
                return
            SESSIONS.destroy(session_id)
            expired = cookie_header("pcs_admin_session", "", "/admin", max_age=0)
            self.redirect("/", cookies=[expired])
            return

        if path == "/admin/password":
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b"CSRF validation failed\n", "text/plain; charset=utf-8")
                return

            current_password = form.get("current_password", "")
            new_password = form.get("new_password", "")
            confirmation = form.get("confirm_password", "")
            address = self.client_ip()
            if not LOGIN_LIMITER.allowed(address):
                self.send_body(429, render_password_page(session["csrf"], "Too many failed attempts. Wait one minute and try again."), "text/html; charset=utf-8")
                return
            if len(current_password) > MAX_PASSWORD_LENGTH or not AUTH.verify(current_password):
                LOGIN_LIMITER.failed(address)
                self.send_body(401, render_password_page(session["csrf"], "Current administrator password was incorrect."), "text/html; charset=utf-8")
                return
            if new_password != confirmation:
                self.send_body(400, render_password_page(session["csrf"], "New password and confirmation did not match."), "text/html; charset=utf-8")
                return
            if len(new_password) < 12:
                self.send_body(400, render_password_page(session["csrf"], "New password must be at least 12 characters."), "text/html; charset=utf-8")
                return
            if len(new_password) > MAX_PASSWORD_LENGTH:
                self.send_body(400, render_password_page(session["csrf"], "New password was too long."), "text/html; charset=utf-8")
                return
            if hmac.compare_digest(current_password, new_password):
                self.send_body(400, render_password_page(session["csrf"], "New password must be different from the current password."), "text/html; charset=utf-8")
                return

            updated, message = change_admin_password(current_password, new_password)
            if not updated:
                self.send_body(500, render_password_page(session["csrf"], message), "text/html; charset=utf-8")
                return
            LOGIN_LIMITER.succeeded(address)
            SESSIONS.destroy_all()
            expired = cookie_header("pcs_admin_session", "", "/admin", max_age=0)
            self.redirect("/admin/login?changed=1", cookies=[expired])
            return

        if path == "/admin/bandwidth":
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b'CSRF validation failed\n', 'text/plain; charset=utf-8')
                return
            payload = {key: value for key, value in form.items() if key != 'csrf'}
            if payload.get('operation') == 'client':
                payload['unlimited'] = payload.get('unlimited') == 'yes'
            try:
                bandwidth_request(payload)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                try:
                    data = bandwidth_request()
                except (ValueError, OSError, subprocess.SubprocessError):
                    data = {}
                self.send_body(400, render_bandwidth_page(data, session['csrf'], str(exc)), 'text/html; charset=utf-8')
                return
            self.redirect('/admin/bandwidth?saved=1')
            return

        if path == '/admin/location':
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b'CSRF validation failed\n', 'text/plain; charset=utf-8')
                return
            try:
                if form.get('confirm') != 'yes':
                    raise ValueError('Confirm position sharing before saving.')
                payload = {'version': 1, 'enabled': form.get('enabled') == 'yes',
                           'persist': form.get('persist') == 'yes'}
                for key, low, high in [('latitude', -90, 90), ('longitude', -180, 180), ('altitude_m', -500, 10000)]:
                    text = form.get(key, '').strip()
                    value = float(text) if text else None
                    if value is not None and (not math.isfinite(value) or not low <= value <= high):
                        raise ValueError('Invalid position value.')
                    payload[key] = value
                if payload['enabled'] and (payload['latitude'] is None or payload['longitude'] is None):
                    raise ValueError('Latitude and longitude are required for manual fallback.')
                location_request(payload)
            except (ValueError, OSError, subprocess.SubprocessError):
                self.send_body(400, render_location_page({'config': {}}, session['csrf'], 'Invalid or unavailable location settings; previous configuration preserved.'), 'text/html; charset=utf-8')
                return
            self.redirect('/admin/location?saved=1')
            return

        if path == "/admin/power-settings":
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b"CSRF validation failed\n", "text/plain; charset=utf-8")
                return
            try:
                capacity_text = form.get("battery_capacity_wh", "").strip()
                capacity = float(capacity_text) if capacity_text else None
                source = form.get("dc_source")
                if source not in {"battery", "power_supply"} or (capacity is not None and not 0 < capacity <= 1_000_000):
                    raise ValueError("Invalid DC source or battery capacity")
                result = subprocess.run(
                    ["sudo", "-n", "/usr/local/sbin/pcs-power-monitor", "set-session"],
                    input=json.dumps({"dc_source": source, "battery_capacity_wh": capacity}),
                    text=True, capture_output=True, timeout=10, check=False,
                )
                if result.returncode:
                    raise ValueError("Could not apply DC source settings")
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                self.send_body(400, (str(error) + "\n").encode(), "text/plain; charset=utf-8")
                return
            self.redirect("/admin/#power-settings")
            return

        if path == "/admin/backup-settings":
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b"CSRF validation failed\n", "text/plain; charset=utf-8")
                return
            try:
                interval_minutes = int(form.get("interval_minutes", ""))
            except ValueError:
                interval_minutes = 0
            enabled_value = form.get("enabled", "")
            history_value = form.get("keep_history", "")
            valid = (
                enabled_value in {"", "yes"}
                and history_value in {"", "yes"}
                and MIN_BACKUP_INTERVAL_MINUTES <= interval_minutes <= MAX_BACKUP_INTERVAL_MINUTES
            )
            if not valid:
                updated, message = False, "Backup interval must be a whole number from 1 through 43,200 minutes."
            else:
                updated, message = update_backup_settings(
                    enabled_value == "yes", interval_minutes, history_value == "yes"
                )
            backup_settings, load_error = load_backup_settings()
            nonce = secrets.token_urlsafe(18)
            body = render_admin_page(
                get_admin_dashboard(), session["csrf"], nonce=nonce,
                backup_settings=backup_settings,
                settings_notice=message if updated else "",
                settings_error=load_error or ("" if updated else message), public_data=get_public_dashboard(),
            )
            self.send_body(200 if updated else 400, body, "text/html; charset=utf-8", nonce=nonce)
            return

        if path == "/admin/run":
            _, session = self.require_session()
            if not session:
                return
            if not self.csrf_valid(form, session):
                self.send_body(403, b"CSRF validation failed\n", "text/plain; charset=utf-8")
                return
            action = form.get("action", "")
            if action not in ACTION_MAP:
                self.send_body(400, b"Unknown administrative action\n", "text/plain; charset=utf-8")
                return
            code, output = run_action(action)
            nonce = secrets.token_urlsafe(18)
            backup_settings, settings_error = load_backup_settings()
            body = render_admin_page(
                get_admin_dashboard(), session["csrf"], output, ACTION_MAP[action][0], code, nonce,
                backup_settings=backup_settings, settings_error=settings_error, public_data=get_public_dashboard(),
            )
            self.send_body(200, body, "text/html; charset=utf-8", nonce=nonce)
            return

        self.send_body(404, b"Not found\n", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        print(f"{self.client_address[0]} - {fmt % args}")


def set_password_interactively(path: str) -> int:
    print(f"Configuring PCS administrator credential: {path}")
    password = getpass.getpass("New admin password: ")
    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        print("ERROR: passwords did not match.")
        return 1
    try:
        write_password_record(path, password)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print("PCS administrator password updated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PCS public homepage and administrative control panel")
    parser.add_argument("--set-password", action="store_true", help="securely create or replace the admin password hash")
    parser.add_argument("--credential-file", default=CREDENTIAL_FILE, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.set_password:
        return set_password_interactively(args.credential_file)

    server = ReusableThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PCS homepage and control panel listening on http://{HOST}:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
