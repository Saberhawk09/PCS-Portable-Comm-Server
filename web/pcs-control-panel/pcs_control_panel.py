#!/usr/bin/env python3

import argparse
import base64
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
    "gnss": {"status", "receiver_active", "fix", "satellites", "coordinates", "grid_square", "utc_time"},
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
    network = sanitized.get("network", {})
    if "uplinks" in network:
        fields = {"id", "name", "type", "priority", "link", "address", "address6", "internet", "internet6", "state", "active", "selected", "selected6"}
        rows = network["uplinks"] if isinstance(network["uplinks"], list) else []
        network["uplinks"] = [{**{k: v for k, v in row.items() if k in fields and isinstance(v, (str, int, bool, type(None)))}, "usage": sanitize_wan_usage(row.get("usage"))} for row in rows[:32] if isinstance(row, dict)]
    if "usage" in network:
        network["usage"] = sanitize_wan_usage(network["usage"])
    return sanitized


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
        'timeSync': 'Synchronized' if clock.get('synchronized') is True else 'Not synchronized' if clock.get('synchronized') is False else 'Unknown',
    }
    for field, key, online, unit in [('inputPower','input_power','input_online','W'), ('rail12DistributionPower','rail_12v_distribution_power','rail_12v_online','W'), ('rail12OnlyPower','rail_12v_power','rail_12v_online','W'), ('rail5Power','rail_5v_power','rail_5v_online','W'), ('starlinkPower','starlink_power','starlink_online','W')]:
        values[field] = reading(power.get(key), unit) if power.get('configured') and power.get(online) else 'Not commissioned' if field == 'starlinkPower' and not (power.get('configured') and power.get('starlink_configured')) else 'Unavailable'

    values.update({
        'coordinates': text(gps.get('coordinates')), 'grid': text(gps.get('grid_square')),
        'inputVoltage': values['voltage'],
        'shutdownProtection': 'Enabled' if power.get('shutdown_armed') is True else 'Disabled' if power.get('shutdown_armed') is False else 'Unknown',
        'wanUsage': text(network.get('usage_summary')),
    })
    for field, key in [('powerEnergy', 'total_energy_since_boot_wh'), ('batteryRemaining', 'battery_remaining_wh')]:
        value = power.get(key)
        values[field] = f'{value:.1f}' if type(value) in {int, float} and math.isfinite(value) and value >= 0 else 'Unavailable'
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
        ('Power distribution','Measured branches · source-dependent totals', ''.join(row(title,key) for title,key in [('PCS input','inputPower'),('12V distribution · includes 5V','rail12DistributionPower'),('5V output','rail5Power'),('Starlink passthrough','starlinkPower')]), 'power'),
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
    'network': """<div class="content-grid"><article class="content-panel"><h3>Client LAN and uplinks</h3><div class="pair"><span>Active uplink</span><strong data-field="uplink">Unavailable</strong></div><div class="pair"><span>AP clients</span><strong data-field="clients">Unavailable</strong></div><div class="pair"><span>Gateway</span><strong data-value="network.lan_gateway">Unavailable</strong></div><div class="pair"><span>OpenWrt</span><strong data-value="network.openwrt_online">Unavailable</strong></div><div class="pair"><span>Internet</span><strong data-value="network.internet_available">Unavailable</strong></div><div class="pair"><span>DNS</span><strong data-value="network.dns_available">Unavailable</strong></div></article><article class="content-panel"><h3>Cellular</h3><div class="pair"><span>Carrier</span><strong data-value="cellular.carrier">Unavailable</strong></div><div class="pair"><span>Signal</span><strong data-value="cellular.signal">Unavailable</strong></div><div class="pair"><span>Connected</span><strong data-value="cellular.connected">Unavailable</strong></div></article><article class="content-panel" data-optional="remote_management"><h3>Remote management</h3><div class="pair"><span>Connection</span><strong data-value="remote_management.connection">Unavailable</strong></div><div class="pair"><span>Management address</span><strong data-value="remote_management.management_address">Unavailable</strong></div><div class="pair"><span>Last handshake</span><strong data-value="remote_management.latest_handshake">Unavailable</strong></div></article><article class="content-panel"><h3>WAN traffic since boot</h3><p data-field="wanUsage">Unavailable</p><ul id="wan-breakdown"><li>Uplink details unavailable</li></ul><a href="/starlink/">Starlink telemetry</a> · <a href="/openwrt/">OpenWrt</a></article></div>""",
    'radio': """<div class="content-grid"><article class="content-panel" data-optional="aprs"><h3>APRS</h3><div class="pair"><span>Station</span><strong data-value="aprs.callsign">Unavailable</strong></div><div class="pair"><span>Role</span><strong data-value="aprs.role">Unavailable</strong></div><div class="pair"><span>Service</span><strong data-value="aprs.service">Unavailable</strong></div><div class="pair"><span>Packet engine</span><strong data-value="aprs.engine">Unavailable</strong></div><div class="pair"><span>Unread mailbox</span><strong data-value="aprs.mailbox_unread">Unavailable</strong></div></article><article class="content-panel" data-optional="meshtastic"><h3>Meshtastic</h3><div class="pair"><span>Node</span><strong data-value="meshtastic.node">Unavailable</strong></div><div class="pair"><span>MQTT</span><strong data-value="meshtastic.mqtt">Unavailable</strong></div><div class="pair"><span>Transport</span><strong data-value="meshtastic.transport">Unavailable</strong></div></article><article class="content-panel" data-optional="pistar"><h3>Pi-Star</h3><div class="pair"><span>Status</span><strong data-field="pistar">Unavailable</strong></div></article><article class="content-panel"><h3>Radio services</h3><p>Configured services and aggregate diagnostics. Authenticated mailbox detail stays in Administration.</p><a href="/radio/">Radio service guide</a> · <a href="/pistar/">Pi-Star access</a> · <a href="/status/">Full public diagnostics</a></article></div>""",
    'power': """<div class="content-grid"><article class="content-panel"><h3>Power overview</h3><div class="pair"><span>Source this boot</span><strong data-field="dcSource">Unavailable</strong></div><div class="pair"><span>Total power</span><strong data-field="power">Unavailable</strong></div><div class="pair"><span>Input voltage</span><strong data-field="inputVoltage">Unavailable</strong></div><div class="pair"><span>Low-voltage shutdown</span><strong data-field="shutdownProtection">Unavailable</strong></div><div class="pair"><span>Consumed this boot (Wh)</span><strong data-field="powerEnergy">Unavailable</strong></div><div class="pair"><span>Battery remaining (Wh)</span><strong data-field="batteryRemaining">Unavailable</strong></div></article><article class="content-panel"><h3>Measured branches</h3><div class="pair"><span>12V distribution (includes 5V)</span><strong data-field="rail12DistributionPower">Unavailable</strong></div><div class="pair"><span>12V-only load</span><strong data-field="rail12OnlyPower">Unavailable</strong></div><div class="pair"><span>5V output</span><strong data-field="rail5Power">Unavailable</strong></div><div class="pair"><span>Starlink passthrough</span><strong data-field="starlinkPower">Unavailable</strong></div></article><article class="content-panel"><h3>Accounting and protection</h3><p>Battery totals include PCS and Starlink; Power Supply totals include PCS only. Distribution readings include the downstream 5V converter. Estimates never replace voltage protection.</p><a href="/power/">All four monitors, charge and energy totals</a></article></div>""",
    'storage': """<div class="content-grid"><article class="content-panel"><h3>Storage and backup</h3><div class="pair"><span>Primary share</span><strong data-value="storage.primary_share_available">Unavailable</strong></div><div class="pair"><span>USB mounted</span><strong data-value="storage.usb_mounted">Unavailable</strong></div><div class="pair"><span>USB free</span><strong data-value="storage.usb_free_gb">Unavailable</strong></div><div class="pair"><span>Backup available</span><strong data-value="storage.backup_share_available">Unavailable</strong></div><div class="pair"><span>Backup free</span><strong data-value="storage.backup_free_gb">Unavailable</strong></div></article><article class="content-panel"><h3>Field files</h3><p>Local shares remain accessible without Internet.</p><a href="/files/">File sharing instructions</a> · <a href="/status/">Backup and retention details</a></article></div>""",
    'diagnostics': """<div class="content-grid"><article class="content-panel"><h3>Position and time</h3><div class="pair"><span>GNSS fix</span><strong data-field="gps">Unavailable</strong></div><div class="pair"><span>Satellites</span><strong data-value="gnss.satellites">Unavailable</strong></div><div class="pair"><span>Coordinates</span><strong data-field="coordinates">Unavailable</strong></div><div class="pair"><span>Grid square</span><strong data-field="grid">Unavailable</strong></div><div class="pair"><span>Time source</span><strong data-field="timeSource">Unavailable</strong></div><div class="pair"><span>Synchronized</span><strong data-value="time.synchronized">Unavailable</strong></div></article><article class="content-panel"><h3>System resources</h3><div class="pair"><span>CPU temperature</span><strong data-value="system.cpu_temperature">Unavailable</strong></div><div class="pair"><span>CPU load</span><strong data-value="system.cpu_load">Unavailable</strong></div><div class="pair"><span>Memory</span><strong data-value="system.memory_used">Unavailable</strong></div><div class="pair"><span>System storage</span><strong data-value="system.root_storage_used">Unavailable</strong></div></article><article class="content-panel"><h3>Advanced diagnostics</h3><p>The detailed status page retains every public measurement and optional service field.</p><a href="/status/">Detailed status</a> · <a href="/cockpit/">Cockpit</a> · <a href="/docs/">Offline documentation</a></article></div>""",
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
        "network": [("Wi-Fi and cellular", ["wifi-disconnect", "cellular-status", "cellular-disconnect", "cellular-test"]),
                    ("Starlink", ["starlink-status", "starlink-reboot", "starlink-shutdown"])],
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

    sections = [("overview", "Overview"), ("network", "Network"), ("radio", "Radio"),
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
        "storage": backup_settings_html,
        "power": power_settings_html,
        "diagnostics":  '<section class="card"><h2>Administrator access</h2><p>Password changes invalidate active sessions. Forgotten passwords require the PCS terminal.</p><a class="button secondary" href="/admin/password">Change Admin Password</a></section>'}
    views = ''.join(f'<section class="workspace-view" id="{key}"><h2 class="section-title">{label}</h2>{render_dashboard_section(public_data or {}, key) if key in DASHBOARD_SECTION_TEMPLATES else ""}{content[key]}{"".join(action_groups.get(key, []))}{action_directory if key == "actions" else ""}</section>' for key, label in sections)
    console = result_html
    body = f"""<a class="skip" href="#workspace-main">Skip to content</a><div class="workspace">
    <aside class="workspace-sidebar"><a class="workspace-brand" href="/">PCS<span>CONTROL CENTER</span></a><nav class="workspace-nav" aria-label="Administration sections">{nav}<a class="compact-control" href="/admin/details">Detailed diagnostics</a></nav></aside>
    <div class="workspace-content"><header><div class="nav"><div class="brand-group">{header_health(data)}</div><nav class="navlinks"><a class="compact-control" href="/admin/details">Detailed diagnostics</a><a class="compact-control" href="#storage">Backup Settings</a><a class="compact-control" href="/">Public Homepage</a><a class="compact-control" href="/admin/password">Change Password</a><form class="logout" method="POST" action="/admin/logout"><input type="hidden" name="csrf" value="{esc(csrf)}"><button type="submit">Logout</button></form></nav></div></header>
    <main class="admin-main" id="workspace-main">{health}{console}{views}</main></div></div>"""
    script = """document.querySelectorAll('form[data-confirm]').forEach(function(form){form.addEventListener('submit',function(event){var message=form.dataset.confirm;if(message&&!window.confirm(message)){event.preventDefault();}});});
    function showSection(){var id=location.hash.slice(1)||'overview';if(id==='power-settings')id='power';if(id==='system')id='diagnostics';if(!document.querySelector('.workspace-view#'+CSS.escape(id)))id='overview';document.querySelectorAll('.workspace-view').forEach(function(view){view.hidden=view.id!==id;});document.querySelectorAll('[data-view]').forEach(function(link){if(link.dataset.view===id)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});}
    window.addEventListener('hashchange',showSection);showSection();var result=document.getElementById('action-result');if(result){window.addEventListener('load',function(){result.focus();result.scrollIntoView({block:'nearest'});});}
    """
    return document("PCS Administration", body, script=script, nonce=nonce, workspace_theme=True)


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
        if path in {"/admin/", "/admin/password", "/admin/bandwidth"} and not self.session()[1]:
            self.redirect("/admin/login")
            return
        if path in {"/", "/status/", "/health", "/api/public-status", "/admin/", "/admin/login", "/admin/password", "/admin/bandwidth"}:
            content_type = "application/json; charset=utf-8" if path == "/api/public-status" else "text/html; charset=utf-8"
            self.send_body(200, b"", content_type, head_only=True)
            return
        self.send_body(404, b"", "text/plain; charset=utf-8", head_only=True)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
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
