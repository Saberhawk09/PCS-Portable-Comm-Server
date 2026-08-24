#!/usr/bin/env python3

import argparse
import base64
import getpass
import hashlib
import hmac
import html
import json
import os
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOST = os.environ.get("PCS_CONTROL_HOST", "0.0.0.0")
PORT = int(os.environ.get("PCS_CONTROL_PORT", "80"))
DISPATCHER = os.environ.get("PCS_WEB_ACTION", "/usr/local/sbin/pcs-web-action")
PASSWORD_HELPER = os.environ.get(
    "PCS_ADMIN_PASSWORD_HELPER",
    "/usr/local/sbin/pcs-admin-password-helper",
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

ACTIONS = [
    ("status", "View PCS Status", "Show the full PCS status report."),
    ("self-test", "Run Self-Test", "Run the Pi-side health validation."),
    ("storage-status", "View Storage", "Show USB, SD backup, and Samba state."),
    ("wifi-status", "View Wi-Fi", "Show Wi-Fi uplink status and the default route."),
    ("wifi-connect", "Connect Wi-Fi", "Connect to the strongest visible saved Wi-Fi network."),
    ("wifi-disconnect", "Disable Wi-Fi Radio", "Turn off Pi Wi-Fi for offline or cellular-only operation."),
    ("cellular-status", "View Cellular", "Show WWAN modem and cellular connection state."),
    ("cellular-connect", "Connect Cellular", "Bring up the manual cellular data connection."),
    ("cellular-disconnect", "Disconnect Cellular", "Bring down the manual cellular data connection."),
    ("cellular-test", "Test Cellular", "Test cellular-only internet through the WWAN interface."),
    ("meshtastic-status", "View Meshtastic", "Show privacy-safe node, mesh, MQTT, GPSD, and environment status."),
    ("restart-meshtastic", "Restart Meshtastic", "Reconnect the Meshtastic radio transport and MQTT gateway."),
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
    ("reboot-system", "Reboot PCS", "Restart the Raspberry Pi."),
    ("shutdown-system", "Shutdown PCS", "Shut down Pi-Star when paired, then power off PCS."),
]

ACTION_MAP = {name: (label, desc) for name, label, desc in ACTIONS}
ACTION_CONFIRMS = {
    "mount-new-usb": "Configure the attached USB device as PCS primary storage?",
    "safe-unmount-usb": "Sync the backup and safely unmount PCS USB storage?",
    "restart-meshtastic": "Restart the Meshtastic radio and MQTT gateway now?",
    "reboot-system": "Reboot PCS now? The administration page will disconnect during restart.",
    "shutdown-system": "Shutdown PCS now? Physical access is required to power it back on.",
}
ACTION_GROUPS = [
    ("Health", ["status", "self-test", "storage-status", "restart-logs"]),
    ("Network", ["wifi-status", "wifi-connect", "wifi-disconnect"]),
    ("Cellular", ["cellular-status", "cellular-connect", "cellular-disconnect", "cellular-test"]),
    ("Communications", ["meshtastic-status", "restart-meshtastic"]),
    ("Storage", ["sync-backup", "mount-usb", "mount-new-usb", "safe-unmount-usb"]),
    ("Services", ["restart-services", "restart-samba", "restart-modemmanager"]),
    ("Time / GPS", ["sync-time", "restart-chrony", "restart-gpsd"]),
    ("Power", ["reboot-system", "shutdown-system"]),
]
DANGEROUS_ACTIONS = {
    "mount-new-usb",
    "safe-unmount-usb",
    "restart-services",
    "restart-samba",
    "restart-modemmanager",
    "restart-chrony",
    "restart-gpsd",
    "restart-meshtastic",
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
PUBLIC_CACHE = TimedCache()

PUBLIC_FIELDS = {
    "system": {"status", "uptime", "local_time", "cpu_temperature", "cpu_load", "memory_used", "root_storage_used"},
    "network": {"status", "offline", "lan_gateway", "openwrt_online", "openwrt_url", "internet_available", "uplink_type", "connected_client_count"},
    "cellular": {"status", "modem_present", "connected", "carrier", "access_technology", "signal"},
    "time": {"status", "chrony_active", "synchronized", "source", "reference"},
    "gnss": {"status", "receiver_active", "fix", "satellites", "coordinates", "grid_square", "utc_time"},
    "storage": {"status", "usb_mounted", "primary_share_available", "backup_share_available", "usb_free_gb", "backup_free_gb"},
    "services": {"status", "homepage_available", "file_sharing_available", "cockpit_available", "gpsd_lan_enabled"},
    "pistar": {"configured", "online", "url"},
    "aprs": {
        "configured", "status", "service", "callsign", "role", "frequency", "modem",
        "aprs_is", "aprs_is_profile", "beacon", "digipeater", "kiss", "fx25", "packets",
        "last_heard", "tx_state",
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


def dashboard_error(message: str, public: bool) -> dict:
    if public:
        return {
            "generated_at": "unknown",
            "overall": "bad",
            "offline": False,
            "error": message,
            "system": {"status": "bad"},
            "network": {"status": "warn", "lan_gateway": "10.42.0.1"},
            "cellular": {"status": "warn"},
            "time": {"status": "warn"},
            "gnss": {"status": "warn"},
            "storage": {"status": "warn"},
            "services": {"status": "warn"},
            "pistar": {"configured": False},
            "aprs": {"configured": False},
            "meshtastic": {"configured": False},
        }
    return {
        "generated_at": "unknown",
        "overall": "bad",
        "offline": False,
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
    return sanitized


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
.nav{max-width:1450px;margin:auto;padding:.85rem 1.1rem;display:flex;align-items:center;justify-content:space-between;gap:1rem}.brand{font-weight:850;letter-spacing:.02em}.navlinks{display:flex;gap:.55rem;align-items:center}
main{max-width:1450px;margin:auto;padding:1.2rem}.admin-main{max-width:1600px}.hero{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(260px,.65fr);gap:1rem;margin-bottom:1rem}
.hero-main,.admin-entry,.overview,.card,.action-group,.output,.login-panel{background:rgba(20,27,34,.96);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 30px rgba(0,0,0,.14)}
.hero-main{padding:1.5rem}.eyebrow{margin:0 0 .35rem;color:var(--accent);font-size:.78rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.hero h1{font-size:clamp(1.8rem,5vw,3.3rem);line-height:1.05;margin:.1rem 0 .7rem}.hero p{color:var(--muted);max-width:62ch}
.admin-entry{padding:1.25rem;display:flex;flex-direction:column;justify-content:center}.admin-entry h2{margin:0 0 .45rem}.admin-entry p{margin:.1rem 0 1rem;color:var(--muted)}
.button,button{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:9px;background:var(--accent);color:#06121c;padding:.72rem .95rem;font:inherit;font-weight:850;text-decoration:none;cursor:pointer}.button.secondary{background:var(--panel2);border:1px solid var(--line);color:var(--text)}button.danger{background:var(--bad);color:#240707}.button:hover,button:hover{filter:brightness(1.08)}
.overview{padding:1rem 1.1rem;display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-bottom:1rem}.overview h2{margin:0}.overview p{margin:.25rem 0 0;color:var(--muted)}
.badge{display:inline-flex;align-items:center;justify-content:center;min-width:4.2rem;padding:.42rem .65rem;border-radius:999px;font-size:.78rem;font-weight:900}.badge.ok{background:rgba(97,214,155,.14);color:var(--ok)}.badge.warn{background:rgba(242,197,92,.15);color:var(--warn)}.badge.bad{background:rgba(241,119,119,.15);color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:.85rem}.admin-grid{grid-template-columns:repeat(auto-fit,minmax(250px,1fr));margin-bottom:1.25rem}.card{padding:1rem}.card-top{display:flex;align-items:center;justify-content:space-between;gap:.7rem}.card h2,.card h3{margin:0;font-size:1.05rem}.summary{color:var(--muted);margin:.55rem 0 .7rem}.item{display:flex;justify-content:space-between;gap:1rem;border-top:1px solid rgba(255,255,255,.07);padding:.48rem 0}.item span:first-child{color:var(--muted)}.item span:last-child{text-align:right;overflow-wrap:anywhere}.item code{overflow-wrap:anywhere}.metric{margin:.65rem 0}.metric-row{display:flex;justify-content:space-between;color:var(--muted);font-size:.9rem}.bar{height:.55rem;border-radius:99px;background:#090d11;overflow:hidden;margin-top:.25rem}.bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--ok))}
.section-title{margin:1.5rem 0 .75rem}.service-grid{margin-top:.85rem}.service-card a{display:block;margin-top:.7rem}.notice{border-left:4px solid var(--warn);padding:.75rem 1rem;background:rgba(242,197,92,.08);margin:1rem 0;border-radius:7px}.error{border-left-color:var(--bad);background:rgba(241,119,119,.08)}.success{border-left-color:var(--ok);background:rgba(97,214,155,.08)}.password-card{display:flex;align-items:center;justify-content:space-between;gap:1rem}.password-card h2{margin:0 0 .35rem}.password-card p{margin:0;color:var(--muted);max-width:78ch}.password-card .button{flex:none}
.action-group{padding:1rem;margin-bottom:.85rem}.action-group h3{margin:0 0 .7rem;color:var(--muted);font-size:.86rem;text-transform:uppercase;letter-spacing:.09em}.action-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.75rem}.action-card{border:1px solid var(--line);background:rgba(255,255,255,.025);padding:.8rem;border-radius:10px}.action-card button{width:100%}.action-card p{color:var(--muted);margin:.6rem 0 0;font-size:.9rem}
.output{padding:1rem;margin-top:1rem}.output pre{background:#080c10;border-radius:9px;padding:1rem;white-space:pre-wrap;word-break:break-word;max-height:52vh;overflow:auto}.login-wrap{max-width:500px;margin:7vh auto}.login-panel{padding:1.4rem}.login-panel label{display:block;margin:.9rem 0 .35rem}.login-panel input{width:100%;padding:.75rem;background:#090e13;color:var(--text);border:1px solid var(--line);border-radius:8px;font:inherit}.login-panel button{width:100%;margin-top:1rem}.small{font-size:.86rem;color:var(--muted)}
.logout{display:inline}.logout button{padding:.5rem .7rem;background:transparent;border:1px solid var(--line);color:var(--text)}footer{padding:2rem 1rem;text-align:center;color:var(--muted)}
@media(min-width:1300px){.public-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:1500px){.admin-grid{grid-template-columns:repeat(6,minmax(0,1fr))}}
@media(max-width:760px){.hero{grid-template-columns:1fr}.nav{align-items:flex-start}.navlinks{flex-wrap:wrap;justify-content:flex-end}main{padding:.85rem}.item{display:grid;gap:.2rem}.item span:last-child{text-align:left}.password-card{align-items:stretch;flex-direction:column}}
"""


def document(title: str, body: str, script: str = "", nonce: str = "") -> bytes:
    script_html = f'<script nonce="{esc(nonce)}">{script}</script>' if script else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{BASE_CSS}</style></head>
<body>{body}{script_html}<footer>PCS local field network interface</footer></body></html>""".encode("utf-8")


def badge(status: str, label: str | None = None) -> str:
    normalized = status_value(status)
    return f'<span class="badge {normalized}">{esc(label or status_label(normalized))}</span>'


def overall_badge(data: dict) -> str:
    overall = status_value(data.get("overall", "warn"))
    label = status_label(overall)
    if data.get("offline") and overall != "bad":
        label = f"{label} - Offline"
    return badge(overall, label)


def item(label: str, value) -> str:
    return f'<div class="item"><span>{esc(label)}</span><span>{esc(value)}</span></div>'


def public_card(title: str, section: dict, fields: list[tuple[str, str, object]]) -> str:
    status = status_value(section.get("status", "warn"))
    rows = "".join(item(label, section.get(key, fallback)) for label, key, fallback in fields)
    return f'<article class="card"><div class="card-top"><h2>{esc(title)}</h2>{badge(status)}</div><div>{rows}</div></article>'


def render_public_page(data: dict) -> bytes:
    overall = status_value(data.get("overall", "warn"))
    error = data.get("error")
    error_html = f'<div class="notice error">{esc(error)}</div>' if error else ""
    system = data.get("system", {})
    network = data.get("network", {})
    cellular = data.get("cellular", {})
    time_info = data.get("time", {})
    gnss = data.get("gnss", {})
    storage = data.get("storage", {})
    services = data.get("services", {})

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
            ("Connected clients", "connected_client_count", 0),
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
        public_card("Cellular", cellular, [
            ("Modem present", "modem_present", False), ("Connected", "connected", False),
            ("Carrier", "carrier", "unknown"), ("Access technology", "access_technology", "unknown"),
            ("Signal", "signal", "unknown"),
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
        ]).replace(">True<", ">Yes<").replace(">False<", ">No<"),
    ]

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
      <article class="card service-card"><h3>File shares</h3>{item('Primary share', r'\\10.42.0.1\PCS-Share')}{item('Backup share', r'\\10.42.0.1\PCS-Backup')}<p class="small">Windows: enter the share path in File Explorer. Linux: connect with SMB/CIFS.</p></article>
      <article class="card service-card"><h3>Network services</h3>{item('LAN NTP server', '10.42.0.1')}{gpsd_line}{item('Cockpit', 'https://10.42.0.1:9090')}<a href="https://10.42.0.1:9090">Open Cockpit</a></article>
      <article class="card service-card"><h3>Local devices</h3>{item('OpenWrt', 'http://10.42.0.2/')}{item('PCS administration', 'Authentication required')}<a href="http://10.42.0.2/">Open OpenWrt</a></article>
    </section>"""

    body = f"""
    <header><div class="nav"><div class="brand">PCS Field Network</div><nav class="navlinks"><a class="button secondary" href="/">Refresh</a><a class="button" href="/admin/">Admin Login</a></nav></div></header>
    <main><section class="hero"><div class="hero-main"><p class="eyebrow">Portable Communication Server</p><h1>Field Network Status</h1><p>Local services, communications, position, time, and storage information for devices connected on site.</p></div>
    <aside class="admin-entry"><h2>PCS Administration</h2><p>Authorized operators can manage network, cellular, storage, services, time, and power.</p><a class="button" href="/admin/">Admin Login</a></aside></section>
    {error_html}<section class="overview"><div><h2>Overall system health</h2><p>Last refreshed {esc(data.get('generated_at', 'unknown'))}</p></div>{overall_badge(data)}</section>
    <section class="grid public-grid">{''.join(cards)}</section>{service_directory}</main>"""
    return document("PCS Field Network Status", body)


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


def render_admin_page(data: dict, csrf: str, result: str = "", action_name: str = "", return_code: int | None = None, nonce: str = "") -> bytes:
    action_groups = []
    for title, action_names in ACTION_GROUPS:
        forms = []
        for name in action_names:
            label, description = ACTION_MAP[name]
            danger = "danger" if name in DANGEROUS_ACTIONS else ""
            confirmation = ACTION_CONFIRMS.get(name, "")
            forms.append(f"""<form method="POST" action="/admin/run" class="action-card" data-confirm="{esc(confirmation)}">
            <input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="action" value="{esc(name)}">
            <button class="{danger}" type="submit">{esc(label)}</button><p>{esc(description)}</p></form>""")
        action_groups.append(f'<section class="action-group"><h3>{esc(title)}</h3><div class="action-grid">{"".join(forms)}</div></section>')

    result_html = ""
    if result:
        result_html = f'<section class="output"><h2>Result: {esc(action_name)} <span class="small">exit {esc(return_code)}</span></h2><pre>{esc(result)}</pre></section>'

    info = data.get("client_info", {})
    access_rows = [
        item("PCS-Share", r"\\10.42.0.1\PCS-Share"), item("PCS-Backup", r"\\10.42.0.1\PCS-Backup"),
        item("WAN/public IP", info.get("wan_public_ip", "unavailable")), item("Uplink interface", info.get("uplink_interface", "unknown")),
    ]
    for client in info.get("router_side_clients", []):
        access_rows.append(item(client.get("name", "unknown"), f"{client.get('ip', 'unknown')} / {client.get('mac', 'unknown')} / {client.get('state', 'unknown')}"))

    body = f"""
    <header><div class="nav"><div class="brand">PCS Administration</div><nav class="navlinks"><a class="button secondary" href="/">Public Homepage</a><a class="button secondary" href="/admin/password">Change Password</a><form class="logout" method="POST" action="/admin/logout"><input type="hidden" name="csrf" value="{esc(csrf)}"><button type="submit">Logout</button></form></nav></div></header>
    <main class="admin-main"><section class="overview"><div><h2>Administrative health overview</h2><p>Authenticated session · refreshed {esc(data.get('generated_at', 'unknown'))}</p></div>{overall_badge(data)}</section>
    <section class="grid admin-grid">{''.join(render_admin_card(card) for card in data.get('cards', []))}</section>
    <h2 class="section-title">Detailed field access</h2><section class="card">{''.join(access_rows)}</section>
    <h2 class="section-title">Administrator access</h2><section class="card password-card"><div><h2>Admin panel password</h2><p>Change it here while the current password is known. A forgotten password cannot be recovered in the browser; rerun <code>./scripts/setup-pcs-control-panel.sh --reset-admin-password</code> from the Pi terminal.</p></div><a class="button" href="/admin/password">Change Admin Password</a></section>
    <h2 class="section-title">Operator commands</h2>{''.join(action_groups)}{result_html}</main>"""
    script = """document.querySelectorAll('form[data-confirm]').forEach(function(form){form.addEventListener('submit',function(event){var message=form.dataset.confirm;if(message&&!window.confirm(message)){event.preventDefault();}});});"""
    return document("PCS Administration", body, script=script, nonce=nonce)


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
    <main class="login-wrap"><section class="login-panel"><p class="eyebrow">Administrator security</p><h1>Change Admin Password</h1><div class="notice"><strong>Forgotten passwords cannot be changed here.</strong> Rerun <code>./scripts/setup-pcs-control-panel.sh --reset-admin-password</code> from an interactive Pi terminal.</div>{error_html}
    <form method="POST" action="/admin/password"><input type="hidden" name="csrf" value="{esc(csrf)}"><label for="current-password">Current password</label><input id="current-password" name="current_password" type="password" required autocomplete="current-password"><label for="new-password">New password</label><input id="new-password" name="new_password" type="password" required minlength="12" autocomplete="new-password"><label for="confirm-password">Confirm new password</label><input id="confirm-password" name="confirm_password" type="password" required minlength="12" autocomplete="new-password"><button type="submit">Update Admin Password</button></form></section></main>"""
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
        if path in {"/admin/", "/admin/password"} and not self.session()[1]:
            self.redirect("/admin/login")
            return
        if path in {"/", "/health", "/api/public-status", "/admin/", "/admin/login", "/admin/password"}:
            content_type = "application/json; charset=utf-8" if path == "/api/public-status" else "text/html; charset=utf-8"
            self.send_body(200, b"", content_type, head_only=True)
            return
        self.send_body(404, b"", "text/plain; charset=utf-8", head_only=True)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
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
            body = render_admin_page(get_admin_dashboard(), session["csrf"], nonce=nonce)
            self.send_body(200, body, "text/html; charset=utf-8", nonce=nonce)
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
            address = self.client_address[0]
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
            address = self.client_address[0]
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
            body = render_admin_page(get_admin_dashboard(), session["csrf"], output, ACTION_MAP[action][0], code, nonce)
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
