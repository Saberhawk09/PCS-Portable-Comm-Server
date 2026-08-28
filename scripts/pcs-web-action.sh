#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
USB_MOUNT="/mnt/pcs-usb"
PRIMARY_SHARE="/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE="/srv/pcs-share-backup"
PCS_USER="pi"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

CELLULAR_PROFILE_DEFAULT="pcs-cellular-profile"
LEGACY_CELLULAR_PROFILE="pcs-cellular-tmobile"
CELLULAR_PROFILE="${PCS_CELLULAR_PROFILE:-${CELLULAR_PROFILE_DEFAULT}}"
CELLULAR_APN="${PCS_CELLULAR_APN:-fast.t-mobile.com}"
CELLULAR_ROUTE_METRIC="${PCS_CELLULAR_ROUTE_METRIC:-900}"
CELLULAR_FALLBACK_MODE="${PCS_CELLULAR_FALLBACK_MODE:-manual}"
PCS_SETUP_PISTAR="${PCS_SETUP_PISTAR:-no}"
PISTAR_HOST="${PCS_PISTAR_HOST:-10.42.0.3}"
PISTAR_USER="${PCS_PISTAR_USER:-pi-star}"
PISTAR_PAIR_DIR="${PCS_PISTAR_PAIR_DIR:-/etc/pcs/pistar-shutdown}"
PISTAR_PRIVATE_KEY="${PISTAR_PAIR_DIR}/id_ed25519"
PISTAR_KNOWN_HOSTS="${PISTAR_PAIR_DIR}/known_hosts"

ACTION="${1:-}"

header() {
    echo
    echo "=== $* ==="
    echo
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "ERROR: This dispatcher must run as root."
        exit 1
    fi
}

dispatch_host_namespace_action() {
    local dispatcher

    case "${ACTION}" in
        dashboard-public-json|dashboard-json|mount-usb|mount-new-usb|safe-unmount-usb) ;;
        *) return 0 ;;
    esac

    # ProtectSystem=strict gives the API service a private mount namespace.
    # Mount operations performed there can succeed without changing the real
    # PCS host, and status collectors can consequently report stale mounts.
    # Re-enter only the two fixed dashboard collectors and three fixed storage
    # actions through PID 1; the marker prevents recursion in the transient
    # host service.
    if [[ "${PCS_HOST_NAMESPACE_ACTION:-0}" == "1" ]]; then
        return 0
    fi
    if [[ ! -x /usr/bin/systemd-run ]]; then
        echo "ERROR: systemd-run is required for host namespace actions." >&2
        exit 1
    fi

    dispatcher="$(readlink -f "${BASH_SOURCE[0]}")"
    exec /usr/bin/systemd-run \
        --wait \
        --pipe \
        --collect \
        --quiet \
        --service-type=exec \
        /usr/bin/env PCS_HOST_NAMESPACE_ACTION=1 "${dispatcher}" "${ACTION}"
}

request_pistar_poweroff() {
    local output
    local ssh_rc

    if [[ "${PCS_SETUP_PISTAR}" != "yes" ]]; then
        echo "Pi-Star integration is not configured; skipping hotspot shutdown."
        return 0
    fi

    if [[ ! -r "${PISTAR_PRIVATE_KEY}" || ! -r "${PISTAR_KNOWN_HOSTS}" ]]; then
        echo "WARNING: Pi-Star coordinated shutdown is not paired."
        echo "Continuing with PCS shutdown."
        return 0
    fi

    echo "Requesting clean shutdown from Pi-Star at ${PISTAR_HOST}..."

    set +e
    output="$(timeout 15 ssh \
        -i "${PISTAR_PRIVATE_KEY}" \
        -o BatchMode=yes \
        -o ConnectTimeout=6 \
        -o ConnectionAttempts=1 \
        -o IdentitiesOnly=yes \
        -o ServerAliveInterval=2 \
        -o ServerAliveCountMax=2 \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="${PISTAR_KNOWN_HOSTS}" \
        "${PISTAR_USER}@${PISTAR_HOST}" \
        poweroff 2>&1)"
    ssh_rc=$?
    set -e

    if [[ -n "${output}" ]]; then
        printf '%s\n' "${output}"
    fi

    if [[ "${output}" == *"PCS_PISTAR_POWEROFF_ACCEPTED"* ]]; then
        echo "Pi-Star accepted the shutdown request."
        return 0
    fi

    echo "WARNING: Pi-Star did not confirm shutdown (SSH exit ${ssh_rc})."
    echo "Continuing with PCS shutdown."
    return 0
}

show_help() {
    cat <<EOF
PCS web action dispatcher

Allowed actions:

  dashboard-public-json
  dashboard-json
  status
  self-test
  meshtastic-status
  storage-status
  sync-backup
  mount-usb
  mount-new-usb
  safe-unmount-usb
  restart-services
  restart-samba
  restart-modemmanager
  restart-chrony
  restart-gpsd
  restart-meshtastic
  restart-logs
  reboot-system
  shutdown-system
EOF
}

ensure_repo() {
    if [[ ! -d "${REPO_DIR}" ]]; then
        echo "ERROR: repo directory missing: ${REPO_DIR}"
        exit 1
    fi
}

cellular_profile_name() {
    local configured="${CELLULAR_PROFILE:-${CELLULAR_PROFILE_DEFAULT}}"

    if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${configured}"; then
        echo "${configured}"
        return 0
    fi

    if [[ "${configured}" != "${LEGACY_CELLULAR_PROFILE}" ]] \
        && nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${LEGACY_CELLULAR_PROFILE}"; then
        echo "${LEGACY_CELLULAR_PROFILE}"
        return 0
    fi

    echo "${configured}"
}

ensure_usb_mounted() {
    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "ERROR: USB primary storage is not mounted at ${USB_MOUNT}"
        echo
        echo "Try the mount-usb action first."
        exit 1
    fi
}

detect_usb_storage_candidates() {
    local name
    local type
    local fstype
    local pkname
    local rm
    local hotplug
    local tran
    local parent_device
    local parent_tran
    local parent_rm
    local parent_hotplug

    while read -r name type fstype pkname rm hotplug tran; do
        [[ -n "${name}" ]] || continue
        [[ -n "${fstype}" ]] || continue
        [[ "${name}" != /dev/mmcblk0* ]] || continue

        parent_tran="${tran}"
        parent_rm="${rm}"
        parent_hotplug="${hotplug}"

        if [[ "${type}" == "part" && -n "${pkname}" ]]; then
            parent_device="${pkname}"
            if [[ "${parent_device}" != /dev/* ]]; then
                parent_device="/dev/${parent_device}"
            fi

            parent_tran="$(lsblk -dnro TRAN "${parent_device}" 2>/dev/null || true)"
            parent_rm="$(lsblk -dnro RM "${parent_device}" 2>/dev/null || true)"
            parent_hotplug="$(lsblk -dnro HOTPLUG "${parent_device}" 2>/dev/null || true)"
        fi

        if [[ "${parent_tran}" == "usb" || "${parent_rm}" == "1" || "${parent_hotplug}" == "1" ]]; then
            echo "${name}"
        fi
    done < <(lsblk -rpno NAME,TYPE,FSTYPE,PKNAME,RM,HOTPLUG,TRAN 2>/dev/null)
}

configure_detected_usb_primary() {
    local candidates=()
    local candidate

    mapfile -t candidates < <(detect_usb_storage_candidates | sort -u)

    if [[ "${#candidates[@]}" -eq 0 ]]; then
        echo "ERROR: No removable/USB storage filesystem was detected."
        echo
        echo "Attach the PCS USB storage device, then try Mount USB Storage again."
        exit 1
    fi

    if [[ "${#candidates[@]}" -gt 1 ]]; then
        echo "ERROR: Multiple removable/USB storage filesystems were detected."
        echo "Automatic dashboard setup needs exactly one candidate."
        echo

        for candidate in "${candidates[@]}"; do
            echo "  ${candidate}"
            lsblk -no NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "${candidate}" 2>/dev/null || true
        done

        echo
        echo "Run this manually from the Pi with the intended device:"
        echo "  ./scripts/setup-usb-primary-share.sh /dev/sdX1"
        exit 1
    fi

    candidate="${candidates[0]}"
    echo "Detected USB storage candidate: ${candidate}"
    echo "Configuring it as the PCS primary USB share."
    echo

    "${REPO_DIR}/scripts/setup-usb-primary-share.sh" "${candidate}"
}

usb_fstab_source() {
    awk -v target="${USB_MOUNT}" '$2 == target { print $1; exit }' /etc/fstab 2>/dev/null || true
}

usb_fstab_source_present() {
    local source="$1"
    local uuid

    [[ -n "${source}" ]] || return 1

    if [[ "${source}" == UUID=* ]]; then
        uuid="${source#UUID=}"
        blkid -U "${uuid}" >/dev/null 2>&1
        return $?
    fi

    if [[ "${source}" == /dev/* ]]; then
        [[ -b "${source}" ]]
        return $?
    fi

    return 0
}

sync_backup() {
    header "Sync USB Primary to SD Backup"

    ensure_usb_mounted

    if [[ ! -d "${PRIMARY_SHARE}" ]]; then
        echo "ERROR: primary share missing: ${PRIMARY_SHARE}"
        exit 1
    fi

    if [[ ! -d "${BACKUP_SHARE}" ]]; then
        echo "ERROR: backup share missing: ${BACKUP_SHARE}"
        exit 1
    fi

    echo "Primary: ${PRIMARY_SHARE}"
    echo "Backup:  ${BACKUP_SHARE}"
    echo

    rsync -rtvh --delete \
        --modify-window=2 \
        --exclude "LAST_SYNC.txt" \
        "${PRIMARY_SHARE}/" \
        "${BACKUP_SHARE}/"

    chown -R "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}"
    chmod -R u+rwX,g+rwX,o-rwx "${BACKUP_SHARE}"
    find "${BACKUP_SHARE}" -type d -exec chmod 2775 {} \;

    date | tee "${BACKUP_SHARE}/LAST_SYNC.txt" >/dev/null
    chown "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}/LAST_SYNC.txt"

    echo
    echo "Backup sync complete."
    echo "LAST_SYNC:"
    cat "${BACKUP_SHARE}/LAST_SYNC.txt"
}

mount_usb() {
    local fstab_source

    header "Mount USB Primary Share"

    mkdir -p "${USB_MOUNT}"

    systemctl daemon-reload || true
    fstab_source="$(usb_fstab_source)"

    if findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "${USB_MOUNT} is already mounted."
    elif [[ -z "${fstab_source}" ]]; then
        echo "No /etc/fstab entry exists for ${USB_MOUNT}."
        echo "Attempting one-time USB primary share setup from the dashboard action."
        echo
        configure_detected_usb_primary
    else
        if usb_fstab_source_present "${fstab_source}"; then
            mount "${USB_MOUNT}"
        else
            echo "Configured USB storage is not currently present: ${fstab_source}"
            echo
            echo "Use the Mount New USB Storage action to configure a replacement USB device."
            exit 1
        fi
    fi

    systemctl restart smbd

    echo
    echo "USB mount:"
    findmnt "${USB_MOUNT}" || true

    echo
    echo "Samba share paths:"
    testparm -s 2>/dev/null | grep -A8 -E '^\[PCS-Share\]|^\[PCS-Backup\]' || true
}

mount_new_usb() {
    header "Mount New USB Primary Share"

    if findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "ERROR: ${USB_MOUNT} is already mounted."
        echo
        echo "Use Safely Unmount USB before configuring a replacement USB device."
        exit 1
    fi

    mkdir -p "${USB_MOUNT}"

    echo "Scanning for a replacement USB storage filesystem."
    echo "This will update /etc/fstab and the PCS-Share Samba path."
    echo

    configure_detected_usb_primary

    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo
        echo "ERROR: ${USB_MOUNT} is not mounted after replacement setup."
        exit 1
    fi

    systemctl restart smbd

    echo
    echo "New USB mount:"
    findmnt "${USB_MOUNT}" || true

    echo
    echo "Samba share paths:"
    testparm -s 2>/dev/null | grep -A8 -E '^\[PCS-Share\]|^\[PCS-Backup\]' || true
}

safe_unmount_usb() {
    header "Safely Unmount USB Primary Share"

    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "${USB_MOUNT} is not mounted."
        echo "Nothing to unmount."
        exit 0
    fi

    USB_SOURCE="$(findmnt -n -o SOURCE "${USB_MOUNT}" || true)"
    USB_PARENT=""

    if [[ -n "${USB_SOURCE}" ]]; then
        USB_PARENT="$(lsblk -no PKNAME "${USB_SOURCE}" 2>/dev/null | head -n 1 || true)"
    fi

    echo "USB source: ${USB_SOURCE:-unknown}"
    echo "USB parent: ${USB_PARENT:-unknown}"
    echo

    echo "Step 1: syncing primary share to SD backup..."
    sync_backup

    echo
    echo "Step 2: stopping Samba..."
    systemctl stop smbd

    echo
    echo "Step 3: flushing writes..."
    sync

    echo
    echo "Step 4: unmounting ${USB_MOUNT}..."
    umount "${USB_MOUNT}"

    echo
    echo "Step 5: restarting Samba so PCS-Backup remains available..."
    systemctl start smbd

    echo
    echo "Step 6: powering off USB device if possible..."

    if [[ -n "${USB_PARENT}" && -b "/dev/${USB_PARENT}" ]] && command -v udisksctl >/dev/null 2>&1; then
        udisksctl power-off -b "/dev/${USB_PARENT}" || true
    else
        echo "Could not power off USB device automatically."
        echo "It should still be safe to remove after successful unmount."
    fi

    echo
    echo "USB primary share safely unmounted."
    echo "You may remove the USB stick."
}

storage_status() {
    header "PCS Storage Status"

    echo "--- Block Devices ---"
    lsblk -o NAME,MODEL,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RM
    echo

    echo "--- Mounts ---"
    findmnt "${USB_MOUNT}" || echo "${USB_MOUNT} is not mounted."
    echo

    echo "--- Filesystem Usage ---"
    df -h / "${USB_MOUNT}" "${BACKUP_SHARE}" 2>/dev/null || true
    echo

    echo "--- Samba Shares ---"
    testparm -s 2>/dev/null | grep -A8 -E '^\[PCS-Share\]|^\[PCS-Backup\]' || true
    echo

    echo "--- Primary Share ---"
    if [[ -d "${PRIMARY_SHARE}" ]]; then
        ls -la "${PRIMARY_SHARE}"
    else
        echo "Missing: ${PRIMARY_SHARE}"
    fi
    echo

    echo "--- Backup Share ---"
    if [[ -d "${BACKUP_SHARE}" ]]; then
        ls -la "${BACKUP_SHARE}"
        echo
        if [[ -f "${BACKUP_SHARE}/LAST_SYNC.txt" ]]; then
            echo "Last sync:"
            cat "${BACKUP_SHARE}/LAST_SYNC.txt"
        else
            echo "No LAST_SYNC.txt found."
        fi
    else
        echo "Missing: ${BACKUP_SHARE}"
    fi
}

dashboard_json() {
    python3 - <<'PY'
import json
import os
import re
import shlex
import socket
import time
import shutil
import subprocess
from datetime import datetime

INSTALL_CONFIG = os.environ.get(
    "PCS_INSTALL_CONFIG",
    "/home/pi/Projects/PCS-Portable-Comm-Server/config/pcs-install.conf",
)
USB_MOUNT = "/mnt/pcs-usb"
PRIMARY_SHARE = "/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE = "/srv/pcs-share-backup"
APRS_TELEMETRY_HELPER = "/usr/local/sbin/pcs-aprs-telemetry"
MESHTASTIC_STATUS_FILE = "/var/lib/pcs-meshtastic/status.json"
MESHTASTIC_ENV_FILE = "/etc/pcs/meshtastic.env"
CELLULAR_PROFILE_DEFAULT = "pcs-cellular-profile"
LEGACY_CELLULAR_PROFILE = "pcs-cellular-tmobile"
PI_STAR_IP = "10.42.0.3"
PI_STAR_PAIR_DIR = "/etc/pcs/pistar-shutdown"
PUBLIC_VIEW = os.environ.get("PCS_DASHBOARD_VIEW") == "public"

def install_config():
    config = {}

    try:
        with open(INSTALL_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue

                key, raw_value = stripped.split("=", 1)
                key = key.strip()

                try:
                    parts = shlex.split(raw_value, comments=False, posix=True)
                    value = parts[0] if parts else ""
                except Exception:
                    value = raw_value.strip().strip("\"'")

                if key:
                    config[key] = value
    except Exception:
        pass

    return config

CONFIG = install_config()
CELLULAR_PROFILE = CONFIG.get("PCS_CELLULAR_PROFILE", CELLULAR_PROFILE_DEFAULT)
CELLULAR_FALLBACK_MODE = CONFIG.get("PCS_CELLULAR_FALLBACK_MODE", "manual").lower()
PI_STAR_CONFIGURED = CONFIG.get("PCS_SETUP_PISTAR", "no").lower() == "yes"
APRS_STATE = CONFIG.get("PCS_SETUP_APRS", "no").lower()
APRS_STAGED = APRS_STATE == "staged"
APRS_CONFIGURED = APRS_STATE == "yes"
APRS_PREPARED = APRS_STAGED or APRS_CONFIGURED
MESHTASTIC_STATE = CONFIG.get("PCS_SETUP_MESHTASTIC", "no").lower()
MESHTASTIC_STAGED = MESHTASTIC_STATE == "staged"
MESHTASTIC_CONFIGURED = MESHTASTIC_STATE == "yes"
MESHTASTIC_PREPARED = MESHTASTIC_STAGED or MESHTASTIC_CONFIGURED

def environment_config(path):
    config = {}
    try:
        with open(path, "r", encoding="utf-8") as source:
            for line in source:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, raw_value = stripped.split("=", 1)
                key = key.strip()
                try:
                    parts = shlex.split(raw_value, comments=False, posix=True)
                    value = parts[0] if parts else ""
                except Exception:
                    value = raw_value.strip().strip("\"'")
                if key:
                    config[key] = value
    except Exception:
        pass
    return config

def json_object(path):
    try:
        with open(path, "r", encoding="utf-8") as source:
            value = json.load(source)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def bool_value(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def number_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def epoch_age(epoch, now=None):
    numeric = number_value(epoch)
    if numeric is None or numeric <= 0:
        return None
    return max(0, int((time.time() if now is None else now) - numeric))

def age_label(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"

def metric_label(value, suffix="", digits=1):
    numeric = number_value(value)
    return f"{numeric:.{digits}f}{suffix}" if numeric is not None else "unavailable"

def run(cmd, timeout=8):
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=isinstance(cmd, str),
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)

def active(service):
    return subprocess.run(["systemctl", "is-active", "--quiet", service]).returncode == 0

def enabled(service):
    return subprocess.run(["systemctl", "is-enabled", "--quiet", service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def wireguard_runtime():
    service_active = active("wg-quick@wg-pcs.service")
    service_enabled = enabled("wg-quick@wg-pcs.service")
    firewall_active = active("pcs-wireguard-firewall.service")
    firewall_enabled = enabled("pcs-wireguard-firewall.service")
    refresh_active = active("pcs-wireguard-endpoint-refresh.timer")
    refresh_enabled = enabled("pcs-wireguard-endpoint-refresh.timer")
    configured = (
        str(CONFIG.get("PCS_SETUP_WIREGUARD", "no")).lower() == "yes"
        or service_enabled
        or os.path.isfile("/etc/wireguard/wg-pcs.conf")
    )

    address = "unavailable"
    rc, out, _ = run(["ip", "-4", "-o", "address", "show", "dev", "wg-pcs"], timeout=4)
    if rc == 0:
        match = re.search(r"\binet\s+(\S+)", out)
        if match:
            address = match.group(1)

    latest_epoch = 0
    rc, out, _ = run(["wg", "show", "wg-pcs", "latest-handshakes"], timeout=4)
    if rc == 0:
        for line in out.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[-1].isdigit():
                latest_epoch = max(latest_epoch, int(fields[-1]))
    handshake_age = epoch_age(latest_epoch)

    allowed_routes = []
    rc, out, _ = run(["wg", "show", "wg-pcs", "allowed-ips"], timeout=4)
    if rc == 0:
        for line in out.splitlines():
            fields = line.split()
            allowed_routes.extend(fields[1:])

    received = 0
    sent = 0
    rc, out, _ = run(["wg", "show", "wg-pcs", "transfer"], timeout=4)
    if rc == 0:
        for line in out.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[-2].isdigit() and fields[-1].isdigit():
                received += int(fields[-2])
                sent += int(fields[-1])

    return {
        "configured": configured,
        "service_active": service_active,
        "service_enabled": service_enabled,
        "firewall_active": firewall_active,
        "firewall_enabled": firewall_enabled,
        "refresh_active": refresh_active,
        "refresh_enabled": refresh_enabled,
        "address": address,
        "handshake_age": handshake_age,
        "allowed_routes": allowed_routes,
        "received": received,
        "sent": sent,
    }

def byte_count_label(value):
    amount = float(value or 0)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or suffix == "GiB":
            return f"{amount:.0f} {suffix}" if suffix == "B" else f"{amount:.1f} {suffix}"
        amount /= 1024

def port_listening(port):
    rc, out, _ = run("ss -H -ltn | awk '{print $4}'", timeout=4)
    if rc != 0:
        return False
    endings = (f":{port}", f"]:{port}")
    return any(line.endswith(endings) for line in out.splitlines())

def path_exists(path):
    return os.path.exists(path)

def dir_exists(path):
    return os.path.isdir(path)

def file_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""

def disk_usage(path):
    if not os.path.exists(path):
        return None
    try:
        usage = shutil.disk_usage(path)
        used_percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0
        return {
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "used_percent": used_percent,
        }
    except Exception:
        return None

def findmnt(path):
    rc, out, _ = run(["findmnt", "-n", "-o", "SOURCE,FSTYPE,OPTIONS", path], timeout=4)
    if rc != 0 or not out:
        return None
    parts = out.split(maxsplit=2)
    return {
        "source": parts[0] if len(parts) > 0 else "",
        "fstype": parts[1] if len(parts) > 1 else "",
        "options": parts[2] if len(parts) > 2 else "",
    }

def has_samba_share(name):
    rc, out, _ = run("testparm -s 2>/dev/null", timeout=8)
    return rc == 0 and f"[{name}]" in out

def nm_device_connected(device, expected_connection=None):
    rc, out, _ = run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device", "status"], timeout=5)
    if rc != 0:
        return False, ""
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == device:
            state = parts[1]
            connection = parts[2]
            if expected_connection:
                return state == "connected" and connection == expected_connection, connection
            return state == "connected", connection
    return False, ""

def nm_profile_names():
    rc, out, _ = run(["nmcli", "-t", "-f", "NAME", "connection", "show"], timeout=5)
    if rc != 0:
        return []
    return out.splitlines()

def cellular_profile_name():
    names = nm_profile_names()

    if CELLULAR_PROFILE in names:
        return CELLULAR_PROFILE

    if CELLULAR_PROFILE != LEGACY_CELLULAR_PROFILE and LEGACY_CELLULAR_PROFILE in names:
        return LEGACY_CELLULAR_PROFILE

    return CELLULAR_PROFILE

def ip_has_address(device, cidr):
    rc, out, _ = run(["ip", "-4", "addr", "show", "dev", device], timeout=4)
    return rc == 0 and cidr in out

def default_route_iface():
    rc, out, _ = run("ip route | awk '/^default/ {print $5; exit}'", timeout=4)
    return out if rc == 0 else ""

def ping_ok(target):
    rc, _, _ = run(["ping", "-c", "1", "-W", "2", target], timeout=4)
    return rc == 0

def tcp_connect_ok(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def pi_star_health():
    ping = ping_ok(PI_STAR_IP)
    dashboard = tcp_connect_ok(PI_STAR_IP, 80)
    ssh = tcp_connect_ok(PI_STAR_IP, 22)

    return {
        "ping": ping,
        "dashboard": dashboard,
        "ssh": ssh,
        "reachable": ping or dashboard or ssh,
    }

def system_load():
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        return 0.0, 0.0, 0.0

def memory_usage():
    values = {}

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0])
    except Exception:
        return {"used_mb": 0, "total_mb": 0, "percent": 0}

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(total - available, 0)

    if total <= 0:
        percent = 0
    else:
        percent = round((used / total) * 100, 1)

    return {
        "used_mb": round(used / 1024),
        "total_mb": round(total / 1024),
        "percent": percent,
    }

def root_disk_usage():
    try:
        stat = os.statvfs("/")
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used = max(total - free, 0)

        if total <= 0:
            percent = 0
        else:
            percent = round((used / total) * 100, 1)

        return {
            "used_gb": round(used / (1024 ** 3), 1),
            "total_gb": round(total / (1024 ** 3), 1),
            "percent": percent,
        }
    except Exception:
        return {"used_gb": 0, "total_gb": 0, "percent": 0}

def uptime_pretty():
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            seconds = int(float(f.read().split()[0]))
    except Exception:
        return "unknown"

    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def cpu_temperature():
    paths = [
        "/sys/class/thermal/thermal_zone0/temp",
    ]

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()

            if raw:
                c = round(int(raw) / 1000, 1)
                f_temp = round((c * 9 / 5) + 32, 1)
                return f"{c} C / {f_temp} F"
        except Exception:
            continue

    rc, out, _ = run(["vcgencmd", "measure_temp"], timeout=3)
    if rc == 0 and out:
        return out.replace("temp=", "").strip()

    return "unavailable"


def default_route_details():
    rc, out, _ = run(["ip", "route", "show", "default"], timeout=4)
    info = {"interface": "", "gateway": "", "raw": out.strip()}

    if rc != 0 or not out.strip():
        return info

    parts = out.split()
    if "dev" in parts:
        idx = parts.index("dev")
        if idx + 1 < len(parts):
            info["interface"] = parts[idx + 1]

    if "via" in parts:
        idx = parts.index("via")
        if idx + 1 < len(parts):
            info["gateway"] = parts[idx + 1]

    return info

def dns_servers():
    servers = []

    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        servers.append(parts[1])
    except Exception:
        pass

    return servers

def eth0_address():
    rc, out, _ = run(["ip", "-brief", "addr", "show", "eth0"], timeout=4)
    if rc != 0:
        return ""

    for token in out.split():
        if token.startswith("10.42.0."):
            return token

    return ""

def nm_connection_method(connection_name):
    rc, out, _ = run(["nmcli", "-g", "ipv4.method", "connection", "show", connection_name], timeout=5)
    if rc == 0 and out.strip():
        return out.strip()
    return "unknown"

def count_files(path):
    if not os.path.isdir(path):
        return 0

    total = 0
    try:
        for _, _, files in os.walk(path):
            total += len(files)
    except Exception:
        return 0

    return total

def human_age(seconds):
    if seconds is None:
        return "unknown"

    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"

def backup_health():
    primary = "/mnt/pcs-usb/PCS-Share"
    backup = "/srv/pcs-share-backup"
    last_sync = os.path.join(backup, "LAST_SYNC.txt")

    primary_present = os.path.isdir(primary)
    backup_present = os.path.isdir(backup)

    primary_files = count_files(primary)
    backup_files = count_files(backup)

    last_sync_text = "missing"
    last_sync_age = None

    if os.path.isfile(last_sync):
        try:
            with open(last_sync, "r", encoding="utf-8", errors="replace") as f:
                last_sync_text = f.read().strip() or "present"
            last_sync_age = time.time() - os.path.getmtime(last_sync)
        except Exception:
            last_sync_text = "unreadable"

    stale = last_sync_age is None or last_sync_age > 86400

    status = "ok"
    if not primary_present or not backup_present or stale:
        status = "warn"

    return {
        "status": status,
        "primary_present": primary_present,
        "backup_present": backup_present,
        "primary_files": primary_files,
        "backup_files": backup_files,
        "last_sync_text": last_sync_text,
        "last_sync_age": human_age(last_sync_age),
    }

def tcp_port_listening(port):
    rc, out, _ = run(["ss", "-ltn"], timeout=4)
    if rc != 0:
        return False

    needles = [
        f":{port} ",
        f":{port}\n",
        f":{port}\t",
    ]

    return any(needle in out for needle in needles)

def web_admin_status():
    return {
        "control_panel_active": active("pcs-control-panel.service"),
        "redirect_active": active("pcs-dashboard-redirect.service"),
        "cockpit_active": active("cockpit.socket"),
        "port_80": tcp_port_listening(80),
        "port_8080": tcp_port_listening(8080),
        "port_9090": tcp_port_listening(9090),
    }


def modem_number_from_list(mm_list_output):
    for line in mm_list_output.splitlines():
        if "/Modem/" in line:
            tail = line.split("/Modem/", 1)[1].strip()
            number = ""
            for ch in tail:
                if ch.isdigit():
                    number += ch
                else:
                    break
            return number
    return ""

def modem_safe_info(modem_number):
    info = {
        "manufacturer": "unknown",
        "model": "unknown",
        "firmware": "unknown",
        "hardware": "unknown",
        "state": "unknown",
        "power_state": "unknown",
        "access_tech": "unknown",
        "signal_quality": "unknown",
        "operator_name": "unknown",
        "registration": "unknown",
        "packet_service": "unknown",
        "ports": "unknown",
    }

    if not modem_number:
        return info

    rc, out, _ = run(["mmcli", "-m", modem_number], timeout=10)
    if rc != 0 or not out:
        return info

    allowed = {
        "manufacturer": "manufacturer",
        "model": "model",
        "firmware revision": "firmware",
        "h/w revision": "hardware",
        "hardware revision": "hardware",
        "state": "state",
        "power state": "power_state",
        "access tech": "access_tech",
        "signal quality": "signal_quality",
        "operator name": "operator_name",
        "registration": "registration",
        "packet service state": "packet_service",
        "ports": "ports",
    }

    blocked_keys = {
        "equipment id",
        "imei",
        "own",
        "own numbers",
        "subscriber id",
        "sim iccid",
        "primary sim path",
        "sim slot paths",
        "device id",
        "primary port",
    }

    for line in out.splitlines():
        cleaned = line.strip()

        if "|" in cleaned:
            cleaned = cleaned.split("|", 1)[1].strip()

        if ":" not in cleaned:
            continue

        key, value = cleaned.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if key in blocked_keys:
            continue

        if key in allowed and value:
            info[allowed[key]] = value

    return info

def wwan_usb_info(usb_output, model_hint=""):
    info = {
        "id": "",
        "description": "",
    }

    if not usb_output:
        return info

    model_hint = (model_hint or "").lower()
    terms = [
        "sierra",
        "semtech",
        "qualcomm",
        "wwan",
        "em7565",
        "snapdragon",
        "wwan",
        "modem",
        "mbim",
        "qmi",
    ]

    candidates = []
    for line in usb_output.splitlines():
        lowered = line.lower()
        if not any(term in lowered for term in terms):
            continue

        match = re.search(r"\bID\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.+)$", line)
        if match:
            usb_id = match.group(1).lower()
            description = match.group(2).strip()
        else:
            usb_id = ""
            description = line.strip()

        candidates.append({
            "id": usb_id,
            "description": description,
            "line": line.strip(),
        })

    if not candidates:
        return info

    if model_hint and model_hint != "unknown":
        for candidate in candidates:
            if model_hint in candidate["description"].lower() or model_hint in candidate["line"].lower():
                return {
                    "id": candidate["id"],
                    "description": candidate["description"],
                }

    preferred_terms = ["sierra", "semtech", "em7565", "wwan", "snapdragon"]
    for candidate in candidates:
        lowered = candidate["description"].lower()
        if any(term in lowered for term in preferred_terms):
            return {
                "id": candidate["id"],
                "description": candidate["description"],
            }

    candidate = candidates[0]
    return {
        "id": candidate["id"],
        "description": candidate["description"],
    }

def gsm_nm_status():
    status = {
        "device": "none",
        "state": "not present",
        "connection": "none",
    }

    rc, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], timeout=5)
    if rc != 0:
        return status

    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue

        device, dev_type, state, connection = parts

        if dev_type == "gsm":
            status["device"] = device or "unknown"
            status["state"] = state or "unknown"
            status["connection"] = connection or "none"
            rc_iface, out_iface, _ = run(["nmcli", "-g", "GENERAL.IP-IFACE", "device", "show", device], timeout=5)
            status["ip_iface"] = out_iface.strip() if rc_iface == 0 and out_iface.strip() else "none"
            return status

    return status

def cellular_ip_assignment_state(ip_iface):
    if not ip_iface or ip_iface == "none":
        return "no cellular IP interface"

    rc, out, _ = run(["ip", "-br", "addr", "show", ip_iface], timeout=4)
    if rc != 0 or not out:
        return f"{ip_iface}: no address"

    has_ipv4 = False
    has_ipv6 = False

    for token in out.split():
        if "/" not in token:
            continue

        if ":" in token:
            has_ipv6 = True
        elif token[0].isdigit():
            has_ipv4 = True

    if has_ipv4 and has_ipv6:
        return f"{ip_iface}: IPv4 + IPv6 assigned"
    if has_ipv4:
        return f"{ip_iface}: IPv4 assigned"
    if has_ipv6:
        return f"{ip_iface}: IPv6 assigned"

    return f"{ip_iface}: no IP assigned"

def cellular_profile_exists(profile_name):
    return profile_name in nm_profile_names()

def cellular_route_metric(profile_name):
    if not cellular_profile_exists(profile_name):
        return "profile missing"

    rc4, out4, _ = run(["nmcli", "-g", "ipv4.route-metric", "connection", "show", profile_name], timeout=5)
    rc6, out6, _ = run(["nmcli", "-g", "ipv6.route-metric", "connection", "show", profile_name], timeout=5)

    ipv4_metric = out4.strip() if rc4 == 0 and out4.strip() else "unknown"
    ipv6_metric = out6.strip() if rc6 == 0 and out6.strip() else "unknown"

    return f"IPv4 {ipv4_metric}, IPv6 {ipv6_metric}"


def maidenhead_grid(lat, lon, precision=6):
    try:
        lat = float(lat)
        lon = float(lon)
    except Exception:
        return "unknown"

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return "unknown"

    lon += 180
    lat += 90

    # Maidenhead locator pairs:
    # Field: 20 deg lon / 10 deg lat
    # Square: 2 deg lon / 1 deg lat
    # Subsquare: 5 min lon / 2.5 min lat
    letters = "ABCDEFGHIJKLMNOPQRSTUVWX"

    field_lon = int(lon // 20)
    field_lat = int(lat // 10)

    lon -= field_lon * 20
    lat -= field_lat * 10

    square_lon = int(lon // 2)
    square_lat = int(lat // 1)

    lon -= square_lon * 2
    lat -= square_lat * 1

    subsquare_lon = int(lon / (2 / 24))
    subsquare_lat = int(lat / (1 / 24))

    grid = (
        letters[field_lon]
        + letters[field_lat]
        + str(square_lon)
        + str(square_lat)
        + letters[subsquare_lon].lower()
        + letters[subsquare_lat].lower()
    )

    return grid[:precision]

def nmea_coord_to_decimal(value, hemisphere):
    try:
        raw = float(value)
    except Exception:
        return None

    if not value or not hemisphere:
        return None

    degrees = int(raw // 100)
    minutes = raw - (degrees * 100)
    decimal = degrees + (minutes / 60)

    if hemisphere.upper() in {"S", "W"}:
        decimal *= -1

    return decimal

def parse_lat_lon_from_location_output(loc_out):
    lat = None
    lon = None

    for line in loc_out.splitlines():
        cleaned = line.strip().lower()

        if "latitude:" in cleaned:
            try:
                lat = float(cleaned.split("latitude:", 1)[1].strip().split()[0])
            except Exception:
                pass

        if "longitude:" in cleaned:
            try:
                lon = float(cleaned.split("longitude:", 1)[1].strip().split()[0])
            except Exception:
                pass

    if lat is not None and lon is not None:
        return lat, lon

    # Fall back to NMEA RMC/GGA parsing.
    for line in loc_out.splitlines():
        stripped = line.strip()

        if "$GPRMC" in stripped or "$GNRMC" in stripped:
            sentence = stripped[stripped.find("$G"):]
            parts = sentence.split(",")

            if len(parts) >= 7:
                nmea_lat = nmea_coord_to_decimal(parts[3], parts[4])
                nmea_lon = nmea_coord_to_decimal(parts[5], parts[6])

                if nmea_lat is not None and nmea_lon is not None:
                    return nmea_lat, nmea_lon

        if "$GPGGA" in stripped or "$GNGGA" in stripped:
            sentence = stripped[stripped.find("$G"):]
            parts = sentence.split(",")

            if len(parts) >= 6:
                nmea_lat = nmea_coord_to_decimal(parts[2], parts[3])
                nmea_lon = nmea_coord_to_decimal(parts[4], parts[5])

                if nmea_lat is not None and nmea_lon is not None:
                    return nmea_lat, nmea_lon

    return None, None

def modem_gps_safe_info(modem_number):
    info = {
        "capabilities": "unknown",
        "enabled": "unknown",
        "signals": "unknown",
        "refresh_rate": "unknown",
        "gps_data": "not available",
        "nmea": "not available",
        "utc": "not available",
        "coordinates": "not available",
        "lat_lon": "not available",
        "grid_square": "unknown",
        "satellites": "unknown",
        "fix_quality": "unknown",
    }

    if not modem_number:
        return info

    rc, status_out, _ = run(["mmcli", "-m", modem_number, "--location-status"], timeout=5)

    if rc == 0 and status_out:
        for line in status_out.splitlines():
            cleaned = line.strip()

            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[1].strip()

            if ":" not in cleaned:
                continue

            key, value = cleaned.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "capabilities":
                info["capabilities"] = value
            elif key == "enabled":
                info["enabled"] = value
            elif key == "signals":
                info["signals"] = value
            elif key == "refresh rate":
                info["refresh_rate"] = value

    rc, loc_out, _ = run(["mmcli", "-m", modem_number, "--location-get"], timeout=5)

    if rc == 0 and loc_out:
        lower = loc_out.lower()

        if "gps" in lower:
            info["gps_data"] = "present"

        if "$gp" in lower or "$gn" in lower or "nmea:" in lower:
            info["nmea"] = "present"

        if "utc:" in lower:
            info["utc"] = "present"

        lat, lon = parse_lat_lon_from_location_output(loc_out)

        if lat is not None and lon is not None:
            info["coordinates"] = "available"
            info["lat_lon"] = f"{lat:.6f}, {lon:.6f}"
            info["grid_square"] = maidenhead_grid(lat, lon)

        # Pull non-sensitive fix/satellite hints from NMEA text.
        for line in loc_out.splitlines():
            stripped = line.strip()

            if "$GPGGA" in stripped or "$GNGGA" in stripped:
                parts = stripped.split(",")
                if len(parts) > 7:
                    fix_quality = parts[6] or "unknown"
                    satellites = parts[7] or "unknown"

                    fix_map = {
                        "0": "no fix",
                        "1": "GPS fix",
                        "2": "DGPS fix",
                        "4": "RTK fixed",
                        "5": "RTK float",
                    }

                    info["fix_quality"] = fix_map.get(fix_quality, fix_quality)
                    info["satellites"] = satellites

    return info

def gpsd_nmea_safe_info():
    info = {
        "capabilities": "gpsd",
        "enabled": "gpsd NMEA",
        "signals": "unknown",
        "refresh_rate": "unknown",
        "gps_data": "not available",
        "nmea": "not available",
        "utc": "not available",
        "coordinates": "not available",
        "lat_lon": "not available",
        "grid_square": "unknown",
        "satellites": "unknown",
        "fix_quality": "unknown",
    }

    if not shutil.which("gpspipe"):
        return info

    rc, out, _ = run(["gpspipe", "-r", "-n", "6"], timeout=3)
    if rc != 0 or not out:
        return info

    fix_map = {
        "0": "no fix",
        "1": "GPS fix",
        "2": "DGPS fix",
        "4": "RTK fixed",
        "5": "RTK float",
        "6": "estimated",
    }

    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line.startswith("$G"):
            continue

        info["gps_data"] = "present"
        info["nmea"] = "present"

        parts = line.split(",")
        sentence_type = parts[0][-3:] if parts and len(parts[0]) >= 3 else ""

        if sentence_type == "RMC" and len(parts) >= 10:
            if parts[1]:
                info["utc"] = f"{parts[1][0:2]}:{parts[1][2:4]}:{parts[1][4:6]} UTC"

            if parts[2] == "A":
                info["fix_quality"] = "active fix"
            elif parts[2] == "V" and info["fix_quality"] == "unknown":
                info["fix_quality"] = "no fix"

            lat = nmea_coord_to_decimal(parts[3], parts[4])
            lon = nmea_coord_to_decimal(parts[5], parts[6])

            if lat is not None and lon is not None:
                info["coordinates"] = "available"
                info["lat_lon"] = f"{lat:.6f}, {lon:.6f}"
                info["grid_square"] = maidenhead_grid(lat, lon)

        elif sentence_type == "GGA" and len(parts) >= 8:
            if parts[1]:
                info["utc"] = f"{parts[1][0:2]}:{parts[1][2:4]}:{parts[1][4:6]} UTC"

            fix_quality = parts[6] or "unknown"
            satellites = parts[7] or "unknown"
            info["fix_quality"] = fix_map.get(fix_quality, fix_quality)
            info["satellites"] = satellites

            lat = nmea_coord_to_decimal(parts[2], parts[3])
            lon = nmea_coord_to_decimal(parts[4], parts[5])

            if lat is not None and lon is not None:
                info["coordinates"] = "available"
                info["lat_lon"] = f"{lat:.6f}, {lon:.6f}"
                info["grid_square"] = maidenhead_grid(lat, lon)

    return info

def gpsd_json_safe_info():
    info = {
        "capabilities": "gpsd",
        "enabled": "gpsd JSON",
        "signals": "unknown",
        "refresh_rate": "unknown",
        "gps_data": "not available",
        "nmea": "not available",
        "utc": "not available",
        "coordinates": "not available",
        "lat_lon": "not available",
        "grid_square": "unknown",
        "satellites": "unknown",
        "fix_quality": "unknown",
    }

    if not shutil.which("gpspipe"):
        return info

    rc, out, _ = run(["gpspipe", "-w", "-n", "12"], timeout=4)
    if rc != 0 or not out:
        return info

    best_mode = 0
    best_tpv = {}
    best_sky = {}
    best_sky_score = (-1, -1, -1)

    for raw_line in out.splitlines():
        raw_line = raw_line.strip()
        if not raw_line.startswith("{"):
            continue

        try:
            message = json.loads(raw_line)
        except Exception:
            continue

        message_class = message.get("class")

        if message_class == "TPV":
            try:
                mode = int(message.get("mode") or 0)
            except Exception:
                mode = 0

            # Keep the best recent fix mode. This avoids a later mode=1 TPV
            # stomping a valid mode=2/mode=3 TPV during EM7565 sentence churn.
            if mode >= best_mode:
                best_mode = mode
                best_tpv = message

        elif message_class == "SKY":
            satellites = message.get("satellites") or []
            n_sat = message.get("nSat")
            u_sat = message.get("uSat")

            if n_sat is None and satellites:
                n_sat = len(satellites)

            if u_sat is None and satellites:
                u_sat = sum(1 for sat in satellites if sat.get("used"))

            strong_sat = 0
            for sat in satellites:
                try:
                    if float(sat.get("ss") or 0) > 0:
                        strong_sat += 1
                except Exception:
                    pass

            try:
                score = (int(n_sat or 0), int(u_sat or 0), int(strong_sat))
            except Exception:
                score = (0, 0, strong_sat)

            if score > best_sky_score:
                best_sky_score = score
                best_sky = message

    if best_sky:
        info["gps_data"] = "present"
        info["nmea"] = "present"

        satellites = best_sky.get("satellites") or []
        n_sat = best_sky.get("nSat")
        u_sat = best_sky.get("uSat")

        if n_sat is None and satellites:
            n_sat = len(satellites)

        if u_sat is None and satellites:
            u_sat = sum(1 for sat in satellites if sat.get("used"))

        if n_sat is not None and u_sat is not None:
            info["satellites"] = f"{n_sat} in view / {u_sat} used"
            info["signals"] = f"{n_sat} visible, {u_sat} used"
        elif n_sat is not None:
            info["satellites"] = f"{n_sat} in view"
            info["signals"] = f"{n_sat} visible"
        elif u_sat is not None:
            info["satellites"] = f"{u_sat} used"
            info["signals"] = f"{u_sat} used"

    if best_tpv:
        gps_time = best_tpv.get("time")
        if gps_time:
            info["utc"] = gps_time.replace("T", " ").replace("Z", " UTC")

        if best_mode >= 3:
            info["fix_quality"] = "3D fix"
        elif best_mode == 2:
            info["fix_quality"] = "2D fix"
        elif best_mode == 1:
            info["fix_quality"] = "no fix"

        lat = best_tpv.get("lat")
        lon = best_tpv.get("lon")

        if best_mode >= 2 and lat is not None and lon is not None:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                info["coordinates"] = "available"
                info["lat_lon"] = f"{lat_f:.6f}, {lon_f:.6f}"
                info["grid_square"] = maidenhead_grid(lat_f, lon_f)
            except Exception:
                pass
        elif best_mode == 1:
            # Explicitly override stale coordinates from raw NMEA/MM fallback.
            info["coordinates"] = "not available (no valid fix)"
            info["lat_lon"] = "not available (no valid fix)"
            info["grid_square"] = "unknown"

    return info


def merge_gps_info(primary, preferred):
    empty_values = {"", "unknown", "not available"}

    for key, value in preferred.items():
        if str(value) not in empty_values:
            primary[key] = value

    return primary

def public_wan_ip():
    for cmd in [
        ["curl", "-fsS", "--max-time", "3", "https://api.ipify.org"],
        ["curl", "-fsS", "--max-time", "3", "https://ifconfig.me/ip"],
    ]:
        rc, out, _ = run(cmd, timeout=4)
        if rc == 0 and out:
            return out.strip()
    return ""

def uplink_route_info():
    rc, out, _ = run(["ip", "route", "get", "8.8.8.8"], timeout=4)
    info = {"interface": "", "source_ip": "", "raw": out}
    if rc != 0:
        return info

    parts = out.split()
    if "dev" in parts:
        idx = parts.index("dev")
        if idx + 1 < len(parts):
            info["interface"] = parts[idx + 1]
    if "src" in parts:
        idx = parts.index("src")
        if idx + 1 < len(parts):
            info["source_ip"] = parts[idx + 1]
    return info

def manual_client_names():
    names = {}
    path = "/home/pi/Projects/PCS-Portable-Comm-Server/config/local-client-names.tsv"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if "\t" in line:
                    key, name = line.split("\t", 1)
                else:
                    parts = line.split(maxsplit=1)
                    if len(parts) != 2:
                        continue
                    key, name = parts

                names[key.strip().lower()] = name.strip()
    except FileNotFoundError:
        pass

    return names

def dhcp_lease_names():
    names = {}

    lease_paths = [
        "/var/lib/NetworkManager/dnsmasq-eth0.leases",
        "/var/lib/NetworkManager/dnsmasq-wlan0.leases",
        "/var/lib/NetworkManager/dnsmasq.leases",
    ]

    lease_paths.extend([
        os.path.join("/var/lib/NetworkManager", name)
        for name in os.listdir("/var/lib/NetworkManager")
        if name.endswith(".leases")
    ] if os.path.isdir("/var/lib/NetworkManager") else [])

    for path in lease_paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4:
                        mac = parts[1].lower()
                        ip = parts[2]
                        hostname = parts[3]

                        if hostname and hostname != "*":
                            names[ip.lower()] = hostname
                            names[mac] = hostname
        except FileNotFoundError:
            continue
        except PermissionError:
            continue

    return names

def reverse_dns_name(ip):
    rc, out, _ = run(["getent", "hosts", ip], timeout=3)
    if rc != 0 or not out:
        return ""

    parts = out.split()
    if len(parts) >= 2:
        return parts[1].split(".")[0]

    return ""

def netbios_name(ip):
    if shutil.which("nmblookup") is None:
        return ""

    rc, out, _ = run(["nmblookup", "-A", ip], timeout=4)
    if rc != 0 or not out:
        return ""

    for line in out.splitlines():
        line = line.strip()
        if "<00>" in line and "GROUP" not in line:
            return line.split()[0]

    return ""

def resolve_client_name(ip, mac, manual_names, lease_names):
    ip_key = ip.lower()
    mac_key = mac.lower()

    for source in (manual_names, lease_names):
        if ip_key in source:
            return source[ip_key]
        if mac_key in source:
            return source[mac_key]

    dns_name = reverse_dns_name(ip)
    if dns_name:
        return dns_name

    nb_name = netbios_name(ip)
    if nb_name:
        return nb_name

    if mac:
        return mac

    return ip

def router_side_clients(resolve_names=True):
    rc, out, _ = run(["ip", "neigh", "show", "dev", "eth0"], timeout=4)
    clients = []
    if rc != 0:
        return clients

    manual_names = manual_client_names() if resolve_names else {}
    lease_names = dhcp_lease_names() if resolve_names else {}

    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue

        ip = parts[0]
        mac = ""
        state = parts[-1] if len(parts) > 1 else ""

        if "lladdr" in parts:
            idx = parts.index("lladdr")
            if idx + 1 < len(parts):
                mac = parts[idx + 1]

        if ip.startswith("10.42.0."):
            friendly_name = resolve_client_name(ip, mac, manual_names, lease_names) if resolve_names else "LAN client"

            clients.append({
                "ip": ip,
                "mac": mac,
                "state": state,
                "name": friendly_name,
            })

    return clients

def timedate_value(label):
    rc, out, _ = run(["timedatectl"], timeout=5)
    if rc != 0:
        return ""
    for line in out.splitlines():
        if label in line:
            return line.split(":", 1)[1].strip()
    return ""

def chrony_tracking():
    rc, out, _ = run(["chronyc", "tracking"], timeout=5)
    data = {}
    if rc != 0:
        return data
    for line in out.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data

def signal_quality_label(raw):
    match = re.search(r"(\d{1,3})\s*%", str(raw or ""))
    if not match:
        return "unknown"

    percent = max(0, min(100, int(match.group(1))))
    if percent >= 75:
        return "Excellent"
    if percent >= 50:
        return "Good"
    if percent >= 25:
        return "Fair"
    return "Poor"

def card_items_by_id(cards, card_id):
    for card in cards:
        if card.get("id") == card_id:
            return {
                item.get("label", ""): item.get("value", "unknown")
                for item in card.get("items", [])
            }
    return {}

usb_mount = findmnt(USB_MOUNT)
usb_usage = disk_usage(USB_MOUNT) if usb_mount else None
root_usage = disk_usage("/")
backup_usage = disk_usage(BACKUP_SHARE)

wifi_ok, wifi_conn = nm_device_connected("wlan0")
eth_ok, eth_conn = nm_device_connected("eth0", "pcs-router-wan-share")
internet_ok = ping_ok("8.8.8.8")
dns_ok = ping_ok("google.com")
eth_ip_ok = ip_has_address("eth0", "10.42.0.1/24")
default_iface = default_route_iface()
wan_ip = "" if PUBLIC_VIEW else public_wan_ip()
uplink_info = uplink_route_info()
router_clients = router_side_clients(resolve_names=not PUBLIC_VIEW)

load1, load5, load15 = system_load()
cpu_cores = os.cpu_count() or 1
memory = memory_usage()
root_disk = root_disk_usage()
uptime = uptime_pretty()
cpu_temp = cpu_temperature()

system_warn = (
    memory.get("percent", 0) >= 85
    or root_disk.get("percent", 0) >= 85
    or load1 >= (cpu_cores * 1.5)
)
system_status = "warn" if system_warn else "ok"

route_details = default_route_details()
dns_list = dns_servers()
eth0_ip = eth0_address()
eth0_method = nm_connection_method("pcs-router-wan-share")
backup_info = backup_health()
web_admin = web_admin_status()
pi_star = pi_star_health() if PI_STAR_CONFIGURED else {}
openwrt_probe_host = os.environ.get("PCS_OPENWRT_HOST", "10.42.0.2")
openwrt_online = ping_ok(openwrt_probe_host)
router_offline = not openwrt_online

uplink_status = "ok" if route_details.get("interface") else "warn"
client_lan_status = (
    "bad"
    if router_offline
    else "ok"
    if eth0_ip.startswith("10.42.0.1/")
    else "warn"
)
web_admin_core_ok = (
    web_admin["control_panel_active"]
    and web_admin["redirect_active"]
    and web_admin["cockpit_active"]
    and web_admin["port_80"]
    and web_admin["port_8080"]
    and web_admin["port_9090"]
)
pi_star_ok = not PI_STAR_CONFIGURED or (
    pi_star["reachable"]
    and pi_star["dashboard"]
)
web_admin_status_value = "ok" if web_admin_core_ok and pi_star_ok else "warn"

web_admin_items = [
    {"label": "Homepage / admin", "value": "active" if web_admin["control_panel_active"] else "inactive"},
    {"label": "Legacy 8080 redirect", "value": "active" if web_admin["redirect_active"] else "inactive"},
    {"label": "Cockpit", "value": "active" if web_admin["cockpit_active"] else "inactive"},
    {"label": "Port 80", "value": "listening" if web_admin["port_80"] else "not listening"},
    {"label": "Port 8080", "value": "listening" if web_admin["port_8080"] else "not listening"},
    {"label": "Port 9090", "value": "listening" if web_admin["port_9090"] else "not listening"},
]

if PI_STAR_CONFIGURED:
    pi_star_shutdown_paired = (
        os.path.isfile(os.path.join(PI_STAR_PAIR_DIR, "id_ed25519"))
        and os.path.isfile(os.path.join(PI_STAR_PAIR_DIR, "known_hosts"))
    )
    web_admin_items.extend([
        {"label": "Pi-Star ping", "value": "reachable" if pi_star["ping"] else "no reply"},
        {"label": "Pi-Star dashboard", "value": "reachable" if pi_star["dashboard"] else "unavailable"},
        {"label": "Pi-Star SSH", "value": "reachable" if pi_star["ssh"] else "unavailable"},
        {"label": "Pi-Star shutdown", "value": "paired" if pi_star_shutdown_paired else "not paired"},
    ])

if web_admin_status_value != "ok":
    web_admin_summary = "One or more configured web/admin checks failed"
elif PI_STAR_CONFIGURED:
    web_admin_summary = "PCS and Pi-Star interfaces active"
else:
    web_admin_summary = "PCS interfaces active"

chrony = chrony_tracking()
chrony_active = active("chrony")
clock_sync = timedate_value("System clock synchronized")
ntp_service = timedate_value("NTP service")
rtc_local = timedate_value("RTC in local TZ")
chrony_ref = chrony.get("Reference ID", "")
chrony_stratum = chrony.get("Stratum", "")
chrony_local_fallback = chrony_ref.startswith("7F7F0101") or chrony_stratum == "10"
chrony_selected_gps = "(GPS)" in chrony_ref.upper()
rtc_seed_active = active("pcs-rtc-seed.service")

smbd_active = active("smbd")
primary_share_ok = dir_exists(PRIMARY_SHARE) and has_samba_share("PCS-Share")
backup_share_ok = dir_exists(BACKUP_SHARE) and has_samba_share("PCS-Backup")
last_sync = file_text(os.path.join(BACKUP_SHARE, "LAST_SYNC.txt"))

core_services = {
    "NetworkManager": active("NetworkManager"),
    "ModemManager": active("ModemManager"),
    "avahi-daemon": active("avahi-daemon"),
    "smbd": active("smbd"),
    "chrony": active("chrony"),
    "cockpit.socket": active("cockpit.socket"),
    "pcs-control-panel.service": active("pcs-control-panel.service"),
}
if CELLULAR_FALLBACK_MODE == "wifi-fallback":
    core_services["pcs-cellular-fallback.service"] = active("pcs-cellular-fallback.service")

mm_rc, mm_out, _ = run(["mmcli", "-L"], timeout=5)
modem_present = "/Modem/" in mm_out
modem_number = modem_number_from_list(mm_out)
cell_info = modem_safe_info(modem_number)
cell_nm = gsm_nm_status()
cell_ip_state = cellular_ip_assignment_state(cell_nm.get("ip_iface"))
cell_profile_name = cellular_profile_name()
cell_profile_present = cellular_profile_exists(cell_profile_name)
cell_route_metric = cellular_route_metric(cell_profile_name)
cellular_fallback_enabled = CELLULAR_FALLBACK_MODE == "wifi-fallback"
cellular_fallback_service_active = active("pcs-cellular-fallback.service")
cellular_fallback_owned = file_text("/run/pcs-cellular-fallback-owned") == cell_profile_name
gpsd_active = active("gpsd")
gps_info = merge_gps_info(merge_gps_info(modem_gps_safe_info(modem_number), gpsd_nmea_safe_info()), gpsd_json_safe_info())

direwolf_installed = shutil.which("direwolf") is not None
direwolf_active = active("direwolf.service")
direwolf_enabled = enabled("direwolf.service")
direwolf_template = os.path.isfile("/etc/pcs/aprs/direwolf.example.conf")
direwolf_live_config = os.path.isfile("/etc/direwolf.conf")
aprs_audio_input = CONFIG.get("PCS_APRS_AUDIO_INPUT", "auto")
aprs_audio_output = CONFIG.get("PCS_APRS_AUDIO_OUTPUT", "null")
aprs_active_mode = CONFIG.get("PCS_APRS_ACTIVE_MODE", "staged")
aprs_callsign = CONFIG.get("PCS_APRS_CALLSIGN", "not selected")
aprs_role = CONFIG.get("PCS_APRS_ROLE", "monitor")
aprs_frequency = CONFIG.get("PCS_APRS_FREQUENCY", "not selected")
aprs_modem = CONFIG.get("PCS_APRS_MODEM", "1200")
aprs_igate = CONFIG.get("PCS_APRS_IGATE", "no").lower() == "yes"
aprs_igate_server = CONFIG.get("PCS_APRS_IGATE_SERVER", "not configured")
aprs_igate_mode = CONFIG.get("PCS_APRS_IGATE_MODE", "rx-only")
aprs_igate_rf_to_is_filter = CONFIG.get("PCS_APRS_IGATE_RF_TO_IS_FILTER", "all-eligible")
aprs_agw_port = CONFIG.get("PCS_APRS_AGW_PORT", "0")
aprs_kiss_port = CONFIG.get("PCS_APRS_KISS_PORT", "0")
aprs_tx_enabled = aprs_active_mode == "tx" and CONFIG.get("PCS_APRS_TX_ENABLED", "no").lower() == "yes"
aprs_fx25_tx = aprs_active_mode == "tx" and CONFIG.get("PCS_APRS_FX25_TX", "no").lower() == "yes"
aprs_gpsd = CONFIG.get("PCS_APRS_GPSD", "no").lower() == "yes"
aprs_gpsd_host = CONFIG.get("PCS_APRS_GPSD_HOST", "localhost")
aprs_gpsd_port = CONFIG.get("PCS_APRS_GPSD_PORT", "2947")
aprs_beacon = aprs_active_mode == "tx" and CONFIG.get("PCS_APRS_BEACON", "no").lower() == "yes"
aprs_beacon_type = CONFIG.get("PCS_APRS_BEACON_TYPE", "fixed")
aprs_beacon_interval = CONFIG.get("PCS_APRS_BEACON_INTERVAL", "not configured")
aprs_digipeat = aprs_active_mode == "tx" and CONFIG.get("PCS_APRS_DIGIPEAT", "no").lower() == "yes"
aprs_digipeat_mode = CONFIG.get("PCS_APRS_DIGIPEAT_MODE", "standard")
aprs_digipeat_alias = CONFIG.get("PCS_APRS_DIGIPEAT_ALIAS", "not configured")

aprs_role_label = {
    "digi-igate": "digi-IGate / GPS tracker",
    "igate": "IGate",
    "digipeater": "digipeater",
    "tracker": "GPS tracker",
    "monitor": "receive monitor",
}.get(aprs_role, aprs_role)
aprs_modem_label = f"{aprs_modem} baud AFSK" if aprs_modem == "1200" else f"{aprs_modem} baud"
if aprs_igate:
    aprs_igate_scope = "all eligible RF to APRS-IS" if aprs_igate_rf_to_is_filter == "all-eligible" else "filtered RF to APRS-IS"
    aprs_igate_active_mode = "receive-only" if aprs_active_mode == "rx" else aprs_igate_mode
    aprs_igate_label = f"{aprs_igate_active_mode} via {aprs_igate_server}; {aprs_igate_scope}"
else:
    aprs_igate_label = "disabled"
aprs_client_endpoints = []
if aprs_agw_port != "0":
    aprs_client_endpoints.append(f"AGW 10.42.0.1:{aprs_agw_port}")
if aprs_kiss_port != "0":
    aprs_client_endpoints.append(f"KISS 10.42.0.1:{aprs_kiss_port}")
aprs_kiss_label = " / ".join(aprs_client_endpoints) if aprs_client_endpoints else "disabled"
if aprs_beacon:
    aprs_beacon_source = "GPS" if aprs_beacon_type == "gps-tracker" else aprs_beacon_type
    aprs_beacon_interval_label = "10 minutes" if aprs_beacon_interval == "10:00" else aprs_beacon_interval
    aprs_beacon_label = f"{aprs_beacon_source} every {aprs_beacon_interval_label}"
else:
    aprs_beacon_label = "disabled"
aprs_digipeater_label = f"{aprs_digipeat_alias} only ({aprs_digipeat_mode})" if aprs_digipeat else "disabled"
aprs_fx25_label = "enabled" if aprs_fx25_tx else "disabled"
aprs_telemetry = {
    "available": False,
    "packets_1h": 0,
    "packets_24h": 0,
    "unique_stations_24h": 0,
    "last_packet_at": "",
    "last_station": "",
}
if os.path.isfile(APRS_TELEMETRY_HELPER):
    telemetry_rc, telemetry_out, _ = run([APRS_TELEMETRY_HELPER, "--json"], timeout=5)
    if telemetry_rc == 0:
        try:
            loaded_telemetry = json.loads(telemetry_out)
            if isinstance(loaded_telemetry, dict):
                aprs_telemetry.update(loaded_telemetry)
        except (TypeError, ValueError):
            pass
if aprs_telemetry.get("available"):
    aprs_packet_summary = f"{aprs_telemetry.get('packets_1h', 0)} last hour / {aprs_telemetry.get('packets_24h', 0)} last 24h"
    aprs_last_packet = aprs_telemetry.get("last_packet_at") or "none in logs"
else:
    aprs_packet_summary = "not available"
    aprs_last_packet = "not available"

if APRS_STAGED:
    aprs_status = "ok" if direwolf_installed and direwolf_template and not direwolf_active and not direwolf_enabled else "warn"
    aprs_summary = "Dire Wolf software staged; no live radio profile is active"
    aprs_service_label = "staged / disabled" if not direwolf_active else "unexpectedly active"
    aprs_radio_label = "not active during staging"
    aprs_tx_label = "disabled during staging"
elif APRS_CONFIGURED:
    aprs_radio_service = active("pcs-sa818.service")
    aprs_audio_service = active("pcs-aprs-audio.service")
    aprs_firewall_service = active("pcs-aprs-kiss-firewall.service")
    aprs_status = "ok" if all((
        direwolf_installed,
        direwolf_live_config,
        direwolf_active,
        aprs_radio_service,
        aprs_audio_service,
        aprs_firewall_service,
    )) else "warn"
    aprs_summary = "Dire Wolf APRS service active" if aprs_status == "ok" else "Configured APRS service needs attention"
    aprs_service_label = "active" if direwolf_active else "inactive"
    aprs_radio_label = CONFIG.get("PCS_APRS_RADIO", f"{aprs_audio_input} -> {aprs_audio_output}")
    aprs_tx_label = "enabled" if aprs_tx_enabled else "receive-only"
else:
    aprs_status = "ok"
    aprs_summary = "APRS not selected"
    aprs_service_label = "not configured"
    aprs_radio_label = "not configured"
    aprs_tx_label = "disabled"

usb_rc, usb_out, _ = run(["lsusb"], timeout=5)
usb_lower = usb_out.lower()
wwan_usb = wwan_usb_info(usb_out, cell_info.get("model", ""))

wwan_usb_present = any(term in usb_lower for term in [
    "sierra",
    "semtech",
    "qualcomm",
    "wwan",
    "em7565",
    "snapdragon",
    "wwan",
    "modem",
    "mbim",
    "qmi",
])

if wwan_usb.get("description"):
    model_value = cell_info.get("model", "unknown")
    model_lower = str(model_value).lower()
    usb_description = wwan_usb["description"]

    if model_lower in {"", "unknown"} or (
        len(str(model_value)) <= 12 and model_lower in usb_description.lower()
    ):
        cell_info["model"] = usb_description

    if cell_info.get("hardware", "unknown") == "unknown" and wwan_usb.get("id"):
        cell_info["hardware"] = f"USB {wwan_usb['id']}"

gps_device_candidates = [
    "/dev/ttyACM0",
    "/dev/ttyACM1",
    "/dev/ttyUSB0",
    "/dev/ttyUSB1",
    "/dev/ttyAMA0",
    "/dev/serial0",
]

gps_devices = [dev for dev in gps_device_candidates if path_exists(dev)]
pps_present = path_exists("/dev/pps0")

chrony_sources_rc, chrony_sources_out, _ = run(["chronyc", "sources"], timeout=5)
chrony_gps_source_present = any(term in chrony_sources_out.upper() for term in [
    "GPS",
    "PPS",
    "NMEA",
])

storage_status = "ok" if usb_mount and primary_share_ok and backup_share_ok and last_sync else "warn"
time_status = "ok" if chrony_active and clock_sync == "yes" and not chrony_local_fallback else "warn"
samba_status = "ok" if smbd_active and primary_share_ok and backup_share_ok else "bad"
services_status = "ok" if all(core_services.values()) else "warn"
cellular_registered = cell_info.get("registration") in {"home", "roaming"}
cellular_connected = cell_nm.get("state") == "connected"
cellular_status = "ok" if modem_present and (cellular_registered or cellular_connected) else "warn"
cellular_policy_label = (
    "Automatic when Wi-Fi is unavailable"
    if cellular_fallback_enabled
    else "Manual"
)
cellular_summary = (
    "Cellular data connected"
    if cellular_connected
    else "Modem ready; automatic Wi-Fi fallback armed"
    if cellular_registered and cellular_fallback_enabled and cellular_fallback_service_active
    else "Modem ready; fallback configured but service inactive"
    if cellular_registered and cellular_fallback_enabled
    else "Modem ready; cellular data is manual"
    if cellular_registered
    else "Modem detected; waiting for network registration"
    if modem_present
    else "Waiting for WWAN modem hardware"
)
gps_has_modem_data = gps_info.get("gps_data") == "present"
gps_fix_text = str(gps_info.get("fix_quality", "")).lower()
gps_has_valid_fix = any(term in gps_fix_text for term in [
    "2d fix",
    "3d fix",
    "active fix",
    "gps fix",
    "dgps fix",
    "rtk fixed",
    "rtk float",
])
gps_status = "ok" if gps_has_valid_fix else "warn" if gps_has_modem_data or (gpsd_active and (gps_devices or pps_present or chrony_gps_source_present)) else "warn"


# BEGIN PCS ACTIVE UPLINK MODE
active_uplink_ok = bool(default_iface) and default_iface not in {"lo"}
internet_uplink_ok = active_uplink_ok and internet_ok and dns_ok
offline_mode = not internet_uplink_ok and eth_ok and eth_ip_ok

wireguard = wireguard_runtime()
wireguard_handshake_current = (
    wireguard["handshake_age"] is not None
    and wireguard["handshake_age"] <= 180
)
wireguard_core_ok = all((
    wireguard["service_active"],
    wireguard["service_enabled"],
    wireguard["firewall_active"],
    wireguard["firewall_enabled"],
    wireguard["refresh_active"],
    wireguard["refresh_enabled"],
))
if not wireguard["configured"]:
    wireguard_status = "ok"
    wireguard_summary = "Remote management not selected"
elif not wireguard["firewall_active"] or not wireguard["service_enabled"]:
    wireguard_status = "bad"
    wireguard_summary = "Remote-management isolation or boot service is unavailable"
elif wireguard_core_ok and (wireguard_handshake_current or offline_mode):
    wireguard_status = "ok"
    wireguard_summary = "WireGuard remote management connected" if wireguard_handshake_current else "WireGuard ready; waiting for an internet uplink"
else:
    wireguard_status = "warn"
    wireguard_summary = "WireGuard is configured but awaiting a current handshake"
wireguard_connection = (
    "connected"
    if wireguard_handshake_current
    else "waiting for uplink"
    if offline_mode
    else "handshake stale or not observed"
)
wireguard_handshake_label = age_label(wireguard["handshake_age"])

active_uplink_label = (
    "Wi-Fi"
    if default_iface == "wlan0"
    else "Cellular / WWAN"
    if default_iface in {"wwan0", "ppp0"} or str(default_iface).startswith("wwan") or str(default_iface).startswith("ppp")
    else default_iface
    if default_iface
    else "Offline"
)

network_core_ok = eth_ok and eth_ip_ok
network_status = (
    "bad"
    if not network_core_ok or router_offline
    else "warn"
    if not internet_uplink_ok
    else "ok"
)
# END PCS ACTIVE UPLINK MODE

meshtastic_env = environment_config(MESHTASTIC_ENV_FILE)
meshtastic_runtime = json_object(MESHTASTIC_STATUS_FILE)
meshtastic_gateway = meshtastic_runtime.get("gateway", {})
if not isinstance(meshtastic_gateway, dict):
    meshtastic_gateway = {}
meshtastic_device = meshtastic_runtime.get("device", {})
if not isinstance(meshtastic_device, dict):
    meshtastic_device = {}
meshtastic_mesh = meshtastic_runtime.get("mesh", {})
if not isinstance(meshtastic_mesh, dict):
    meshtastic_mesh = {}
meshtastic_case = meshtastic_runtime.get("case_environment", {})
if not isinstance(meshtastic_case, dict):
    meshtastic_case = {}
meshtastic_counters = meshtastic_gateway.get("counters", {})
if not isinstance(meshtastic_counters, dict):
    meshtastic_counters = {}

meshtastic_service_active = active("pcs-meshtastic.service")
meshtastic_service_enabled = enabled("pcs-meshtastic.service")
meshtastic_software = (
    os.path.isfile("/usr/local/sbin/pcs-meshtastic-gateway")
    and os.path.isfile("/usr/local/sbin/pcs_meshtastic_status.py")
)
meshtastic_snapshot_age = epoch_age(meshtastic_runtime.get("collected_at_epoch"))
meshtastic_snapshot_fresh = meshtastic_snapshot_age is not None and meshtastic_snapshot_age <= 60
meshtastic_connected = (
    meshtastic_runtime.get("state") == "connected"
    and bool(meshtastic_gateway.get("ble_connected"))
)
meshtastic_mqtt_connected = bool(meshtastic_gateway.get("mqtt_connected"))
meshtastic_map_mqtt_configured = bool(meshtastic_gateway.get("map_mqtt_configured"))
meshtastic_map_mqtt_connected = bool(meshtastic_gateway.get("map_mqtt_connected"))
meshtastic_radio_mqtt_enabled = bool(meshtastic_gateway.get("radio_mqtt_enabled"))
meshtastic_radio_proxy_enabled = bool(meshtastic_gateway.get("radio_proxy_enabled"))
meshtastic_radio_broker_matches = bool(meshtastic_gateway.get("radio_broker_matches"))
meshtastic_rf_igate_ready = bool(meshtastic_gateway.get("rf_igate_ready"))
meshtastic_map_position_policy_ready = bool(meshtastic_gateway.get("map_position_policy_ready"))
meshtastic_radio_policy_ok = all((
    meshtastic_radio_mqtt_enabled,
    meshtastic_radio_proxy_enabled,
    meshtastic_radio_broker_matches,
))
meshtastic_broker_host = meshtastic_env.get("PCS_MESHTASTIC_MQTT_HOST", "")
meshtastic_broker_port = meshtastic_env.get("PCS_MESHTASTIC_MQTT_PORT", "1883")
meshtastic_tls = bool_value(meshtastic_env.get("PCS_MESHTASTIC_MQTT_TLS"))
meshtastic_broker_label = (
    f"{meshtastic_broker_host}:{meshtastic_broker_port} ({'TLS' if meshtastic_tls else 'plaintext'})"
    if meshtastic_broker_host
    else "not configured"
)
meshtastic_transport = meshtastic_runtime.get("transport") or (
    "usb-serial" if meshtastic_env.get("PCS_MESHTASTIC_PORT") else "bluetooth-le"
    if meshtastic_env.get("PCS_MESHTASTIC_DEVICE") else "not configured"
)
meshtastic_node_name = meshtastic_device.get("long_name") or "unknown"
meshtastic_short_name = meshtastic_device.get("short_name") or "unknown"
meshtastic_node_label = (
    f"{meshtastic_node_name} ({meshtastic_short_name})"
    if meshtastic_short_name != "unknown"
    else meshtastic_node_name
)
meshtastic_hardware_label = " / ".join(
    str(value)
    for value in (meshtastic_device.get("hardware"), meshtastic_device.get("firmware"))
    if value and str(value) != "unknown"
) or "unknown"
meshtastic_power = str(meshtastic_device.get("power") or "unknown")
meshtastic_voltage = number_value(meshtastic_device.get("voltage"))
if meshtastic_voltage is not None:
    meshtastic_power = f"{meshtastic_power}; {meshtastic_voltage:.3f} V"
meshtastic_downlink_filters = int(number_value(meshtastic_gateway.get("downlink_filters")) or 0)
meshtastic_mqtt_activity = (
    f"{int(number_value(meshtastic_counters.get('mqtt_uplink')) or 0)} up / "
    f"{int(number_value(meshtastic_counters.get('mqtt_downlink')) or 0)} down"
)
meshtastic_map_mqtt_activity = (
    f"{'connected' if meshtastic_map_mqtt_connected else 'disconnected'}; "
    f"{int(number_value(meshtastic_counters.get('map_mqtt_uplink')) or 0)} mirrored uplinks"
    if meshtastic_map_mqtt_configured
    else "not configured"
)
meshtastic_radio_policy_label = (
    "enabled / client proxy / broker matched"
    if meshtastic_radio_policy_ok
    else "radio MQTT, Client Proxy, or broker mismatch"
)
meshtastic_rf_igate_label = (
    "ready; public LongFast RF uplink with sender consent"
    if meshtastic_rf_igate_ready
    else "not ready; check LongFast key, uplink, and OK to MQTT"
)
meshtastic_map_enabled = bool(meshtastic_gateway.get("map_reporting_enabled"))
meshtastic_map_location = bool(meshtastic_gateway.get("map_location_opt_in"))
meshtastic_map_interval = int(number_value(meshtastic_gateway.get("map_publish_interval_secs")) or 0)
meshtastic_map_precision = int(number_value(meshtastic_gateway.get("map_position_precision")) or 0)
meshtastic_channel_position_precision = int(number_value(meshtastic_gateway.get("primary_channel_position_precision")) or 0)
meshtastic_map_label = (
    f"enabled; location opted in; every {meshtastic_map_interval // 60}m; "
    f"map/channel precision {meshtastic_map_precision}/{meshtastic_channel_position_precision}"
    if meshtastic_map_enabled and meshtastic_map_location and meshtastic_map_interval
    else "enabled; location not shared"
    if meshtastic_map_enabled
    else "disabled"
)
meshtastic_mesh_activity = (
    f"{int(number_value(meshtastic_mesh.get('received_packets')) or 0)} RX / "
    f"{int(number_value(meshtastic_mesh.get('transmitted_packets')) or 0)} TX"
)
meshtastic_remote_nodes = (
    f"{int(number_value(meshtastic_mesh.get('recent_remote_nodes')) or 0)} recent / "
    f"{int(number_value(meshtastic_mesh.get('known_remote_nodes')) or 0)} observed"
)
meshtastic_position_enabled = bool_value(meshtastic_env.get("PCS_MESHTASTIC_GPSD_POSITION"))
meshtastic_position_updates = int(number_value(meshtastic_counters.get("position_updates")) or 0)
meshtastic_position_age = epoch_age(meshtastic_gateway.get("last_position_update_at_epoch"))
meshtastic_position_label = (
    f"GPSD active; {meshtastic_position_updates} sent; last {age_label(meshtastic_position_age)}"
    if meshtastic_position_enabled
    else "disabled"
)
meshtastic_temperature = metric_label(meshtastic_case.get("temperature_f"), " F")
meshtastic_humidity = metric_label(meshtastic_case.get("humidity_percent"), "%")
meshtastic_environment_label = (
    f"{meshtastic_temperature} / {meshtastic_humidity} RH"
    if meshtastic_temperature != "unavailable" or meshtastic_humidity != "unavailable"
    else "unavailable"
)
meshtastic_utilization_label = (
    f"channel {metric_label(meshtastic_device.get('channel_utilization_percent'), '%')} / "
    f"TX {metric_label(meshtastic_device.get('air_utilization_tx_percent'), '%')}"
)

if MESHTASTIC_STAGED:
    meshtastic_status = "ok" if (
        meshtastic_software
        and not meshtastic_service_active
        and not meshtastic_service_enabled
    ) else "warn"
    meshtastic_summary = "Meshtastic gateway software staged; no node is active"
    meshtastic_service_label = "staged / disabled"
elif MESHTASTIC_CONFIGURED:
    meshtastic_core_ok = all((
        meshtastic_software,
        meshtastic_service_active,
        meshtastic_service_enabled,
        meshtastic_snapshot_fresh,
        meshtastic_connected,
        meshtastic_radio_policy_ok,
    ))
    meshtastic_broker_ok = meshtastic_mqtt_connected or offline_mode
    meshtastic_map_broker_ok = (
        not meshtastic_map_mqtt_configured
        or (
            meshtastic_rf_igate_ready
            and meshtastic_map_position_policy_ready
            and (meshtastic_map_mqtt_connected or offline_mode)
        )
    )
    meshtastic_status = (
        "ok" if meshtastic_core_ok and meshtastic_broker_ok and meshtastic_map_broker_ok
        else "bad" if not meshtastic_service_active or not meshtastic_connected
        else "warn"
    )
    if meshtastic_core_ok and meshtastic_mqtt_connected and meshtastic_map_broker_ok:
        meshtastic_summary = "Meshtastic RF gateway and MQTT map uplinks connected"
    elif meshtastic_core_ok and offline_mode:
        meshtastic_summary = "Meshtastic radio connected; MQTT waiting for an uplink"
    else:
        meshtastic_summary = "Configured Meshtastic gateway needs attention"
    meshtastic_service_label = "active" if meshtastic_service_active else "inactive"
else:
    meshtastic_status = "ok"
    meshtastic_summary = "Meshtastic not selected"
    meshtastic_service_label = "not configured"

cards = [
    {
        "id": "remote-management",
        "title": "Remote Management",
        "status": wireguard_status,
        "summary": wireguard_summary,
        "items": [
            {"label": "Configured", "value": "yes" if wireguard["configured"] else "no"},
            {"label": "Tunnel", "value": "active" if wireguard["service_active"] else "inactive"},
            {"label": "Management address", "value": wireguard["address"]},
            {"label": "Connection", "value": wireguard_connection},
            {"label": "Latest handshake", "value": wireguard_handshake_label},
            {"label": "Boot persistence", "value": "enabled" if wireguard["service_enabled"] else "disabled"},
            {"label": "Isolation firewall", "value": "active" if wireguard["firewall_active"] else "inactive"},
            {"label": "Endpoint refresh", "value": "active" if wireguard["refresh_active"] else "inactive"},
            {"label": "Approved routes", "value": ", ".join(wireguard["allowed_routes"]) if wireguard["allowed_routes"] else "none"},
            {"label": "Tunnel transfer", "value": f"{byte_count_label(wireguard['received'])} received / {byte_count_label(wireguard['sent'])} sent"},
        ],
    },
    {
        "id": "uplink-details",
        "title": "Uplink Details",
        "status": uplink_status,
        "summary": "Internet uplink route present" if uplink_status == "ok" else "No default uplink route detected",
        "items": [
            {"label": "Default interface", "value": route_details.get("interface") or "unknown"},
            {"label": "Gateway", "value": route_details.get("gateway") or "unknown"},
            {"label": "Source IP", "value": uplink_info.get("source_ip") or "unknown"},
            {"label": "Public IP", "value": wan_ip or "unavailable"},
            {"label": "DNS servers", "value": ", ".join(dns_list) if dns_list else "unknown"},
        ],
    },
    {
        "id": "client-lan",
        "title": "Client LAN / DHCP",
        "status": client_lan_status,
        "summary": "PCS client LAN active" if client_lan_status == "ok" else "PCS client LAN warning",
        "items": [
            {"label": "eth0 address", "value": eth0_ip or "missing"},
            {"label": "DHCP mode", "value": eth0_method},
            {"label": "Visible clients", "value": str(len(router_clients))},
            {"label": "Client gateway", "value": "10.42.0.1"},
            {"label": "AP / switch IP", "value": "10.42.0.2 expected"},
        ],
    },
    {
        "id": "backup-health",
        "title": "Backup Health",
        "status": backup_info["status"],
        "summary": "Backup mirror recently updated" if backup_info["status"] == "ok" else "Backup mirror needs attention",
        "items": [
            {"label": "Primary share", "value": "present" if backup_info["primary_present"] else "missing"},
            {"label": "Backup share", "value": "present" if backup_info["backup_present"] else "missing"},
            {"label": "Primary file count", "value": str(backup_info["primary_files"])},
            {"label": "Backup file count", "value": str(backup_info["backup_files"])},
            {"label": "Last sync age", "value": backup_info["last_sync_age"]},
        ],
    },
    {
        "id": "web-admin",
        "title": "Web / Admin Interfaces",
        "status": web_admin_status_value,
        "summary": web_admin_summary,
        "items": web_admin_items,
    },
    {
        "id": "system-stats",
        "title": "System Stats",
        "status": system_status,
        "summary": "System resources normal" if system_status == "ok" else "System resource warning",
        "items": [
            {"label": "Uptime", "value": uptime},
            {"label": "CPU load", "value": f"{load1:.2f}, {load5:.2f}, {load15:.2f}"},
            {"label": "CPU cores", "value": str(cpu_cores)},
            {"label": "RAM used", "value": f"{memory['used_mb']} / {memory['total_mb']} MB ({memory['percent']}%)"},
            {"label": "Root disk used", "value": f"{root_disk['used_gb']} / {root_disk['total_gb']} GB ({root_disk['percent']}%)"},
            {"label": "CPU temp", "value": cpu_temp},
        ],
    },
    {
        "id": "network",
        "title": "Network",
        "status": network_status,
        "summary": (
            f"Online via {active_uplink_label} and router handoff active"
            if network_status == "ok"
            else "OpenWrt AP / switch offline at 10.42.0.2"
            if router_offline
            else "PCS local network healthy; internet uplink offline"
            if offline_mode
            else "Network needs attention"
        ),
        "items": [
            {"label": "Active uplink", "value": active_uplink_label},
            {"label": "Wi-Fi uplink", "value": f"{'connected' if wifi_ok else 'not connected'} ({wifi_conn or 'unknown'})"},
            {"label": "Ethernet handoff", "value": f"{'connected' if eth_ok else 'not connected'} ({eth_conn or 'unknown'})"},
            {"label": "Router-side IP", "value": "10.42.0.1/24 present" if eth_ip_ok else "missing"},
            {"label": "OpenWrt AP / switch", "value": "online at 10.42.0.2" if openwrt_online else "OFFLINE at 10.42.0.2"},
            {"label": "Default route", "value": default_iface or "unknown"},
            {"label": "Internet ping", "value": "ok" if internet_ok else "failed"},
            {"label": "DNS ping", "value": "ok" if dns_ok else "failed"},
            {"label": "WAN/public IP", "value": wan_ip or "unavailable"},
            {"label": "Router-side clients", "value": str(len(router_clients))},
        ],
    },
    {
        "id": "storage",
        "title": "Storage",
        "status": storage_status,
        "summary": "USB primary and SD backup available" if storage_status == "ok" else "Storage needs attention",
        "items": [
            {"label": "USB mount", "value": f"{usb_mount['source']} ({usb_mount['fstype']})" if usb_mount else "not mounted"},
            {"label": "Primary share", "value": "/mnt/pcs-usb/PCS-Share" if primary_share_ok else "missing"},
            {"label": "Backup share", "value": "/srv/pcs-share-backup" if backup_share_ok else "missing"},
            {"label": "Last backup sync", "value": last_sync or "missing"},
        ],
        "metrics": [
            {"label": "USB used", "value": usb_usage["used_percent"] if usb_usage else None, "suffix": "%"},
            {"label": "SD used", "value": root_usage["used_percent"] if root_usage else None, "suffix": "%"},
        ],
    },
    {
        "id": "time",
        "title": "Time / NTP",
        "status": time_status,
        "summary": "Chrony synchronized to preferred GPS" if time_status == "ok" and chrony_selected_gps else "Chrony synchronized to Internet NTP" if time_status == "ok" else "Chrony using RTC holdover" if chrony_local_fallback and rtc_seed_active else "Chrony using local clock fallback" if chrony_local_fallback else "Time needs attention",
        "items": [
            {"label": "Chrony", "value": "active" if chrony_active else "inactive"},
            {"label": "System synchronized", "value": clock_sync or "unknown"},
            {"label": "NTP service", "value": ntp_service or "unknown"},
            {"label": "RTC local TZ", "value": rtc_local or "unknown"},
            {"label": "RTC boot seed", "value": "completed" if rtc_seed_active else "inactive or unavailable"},
            {"label": "Reference", "value": chrony_ref or "unknown"},
            {"label": "Stratum", "value": chrony_stratum or "unknown"},
        ],
    },
    {
        "id": "samba",
        "title": "Samba",
        "status": samba_status,
        "summary": "Primary and backup shares active" if samba_status == "ok" else "Samba needs attention",
        "items": [
            {"label": "smbd", "value": "active" if smbd_active else "inactive"},
            {"label": "PCS-Share", "value": "present" if primary_share_ok else "missing"},
            {"label": "PCS-Backup", "value": "present" if backup_share_ok else "missing"},
            {"label": "Port 445", "value": "listening" if port_listening("445") else "not visible"},
        ],
    },
    {
        "id": "services",
        "title": "Core Services",
        "status": services_status,
        "summary": "Core services active" if services_status == "ok" else "One or more services need attention",
        "items": [{"label": name, "value": "active" if ok else "inactive"} for name, ok in core_services.items()],
    },
    {
        "id": "cellular",
        "title": "Cellular / WWAN",
        "status": cellular_status,
        "summary": cellular_summary,
        "items": [
            {
                "label": "PCS cellular state",
                "value": cellular_summary,
            },
            {"label": "Fallback policy", "value": cellular_policy_label},
            {
                "label": "Fallback service",
                "value": "active" if cellular_fallback_service_active else "inactive",
            },
            {
                "label": "Session ownership",
                "value": "automatic fallback" if cellular_fallback_owned else "manual or disconnected",
            },
            {"label": "ModemManager", "value": "active" if active("ModemManager") else "inactive"},
            {"label": "WWAN modem", "value": "detected" if modem_present else "not detected yet"},
            {"label": "Model", "value": cell_info.get("model", "unknown")},
            {"label": "Hardware", "value": cell_info.get("hardware", "unknown")},
            {"label": "NetworkManager", "value": f"{cell_nm.get('device')} / {cell_nm.get('state')}"},
            {"label": "Profile", "value": cell_nm.get("connection") or "none"},
            {"label": "Operator", "value": cell_info.get("operator_name", "unknown")},
            {"label": "Registration", "value": cell_info.get("registration", "unknown")},
            {"label": "Access tech", "value": cell_info.get("access_tech", "unknown")},
            {"label": "Signal quality", "value": cell_info.get("signal_quality", "unknown")},
            {"label": "Cellular IP", "value": cell_ip_state},
            {"label": "Route metric", "value": cell_route_metric},
        ],
    },
    {
        "id": "gps",
        "title": "GPS / GNSS",
        "status": gps_status,
        "summary": (
            "WWAN NMEA GPS feeding gpsd and Chrony"
            if gpsd_active and chrony_gps_source_present
            else "WWAN GPS available, waiting for Chrony GPS source"
            if gpsd_active
            else "Waiting for WWAN NMEA GPS data"
            if modem_present
            else "Waiting for GPS/GNSS hardware"
        ),
        "items": [
            {"label": "GPS path", "value": "WWAN NMEA -> gpsd -> Chrony"},
            {"label": "Starter service", "value": "active/exited" if active("pcs-wwan-gps-nmea") else "inactive or missing"},
            {"label": "NMEA port", "value": "/dev/ttyUSB1 present" if os.path.exists("/dev/ttyUSB1") else "/dev/ttyUSB1 missing"},
            {"label": "gpsd", "value": "active" if gpsd_active else "inactive"},
            {"label": "NMEA", "value": gps_info.get("nmea", "unknown")},
            {"label": "Lat/Lon", "value": gps_info.get("lat_lon", "not available")},
            {"label": "Grid square", "value": gps_info.get("grid_square", "unknown")},
            {"label": "UTC time", "value": gps_info.get("utc", "unknown")},
            {"label": "Fix quality", "value": gps_info.get("fix_quality", "unknown")},
            {"label": "Satellites", "value": gps_info.get("satellites", "unknown")},
            {"label": "Chrony GPS source", "value": "present" if chrony_gps_source_present else "not shown"},
            {
                "label": "Dashboard GPS source",
                "value": "gpsd NMEA" if gps_info.get("nmea") == "present" else f"ModemManager {gps_info.get('enabled', 'unknown')}",
            },
        ],
    },
]

if APRS_PREPARED:
    cards.append({
        "id": "aprs",
        "title": "APRS / Packet",
        "status": aprs_status,
        "summary": aprs_summary,
        "items": [
            {"label": "PCS state", "value": APRS_STATE},
            {"label": "Active profile", "value": aprs_active_mode},
            {"label": "Dire Wolf package", "value": "installed" if direwolf_installed else "missing"},
            {"label": "Service", "value": aprs_service_label},
            {"label": "Boot enablement", "value": "enabled" if direwolf_enabled else "disabled"},
            {"label": "Radio / audio", "value": aprs_radio_label},
            {"label": "SA818S initialization", "value": "active" if active("pcs-sa818.service") else "inactive"},
            {"label": "ALSA profile", "value": "active" if active("pcs-aprs-audio.service") else "inactive"},
            {"label": "AGW / KISS firewall", "value": "active" if active("pcs-aprs-kiss-firewall.service") else "inactive"},
            {"label": "Client endpoints", "value": aprs_kiss_label},
            {"label": "Frequency", "value": aprs_frequency},
            {"label": "GPS tracker source", "value": f"{aprs_gpsd_host}:{aprs_gpsd_port}" if aprs_gpsd else "disabled"},
            {"label": "APRS-IS", "value": "configured" if aprs_igate else "not configured"},
            {"label": "RF TX", "value": aprs_tx_label},
            {"label": "Packets", "value": aprs_packet_summary},
            {"label": "Unique stations / 24h", "value": str(aprs_telemetry.get("unique_stations_24h", 0)) if aprs_telemetry.get("available") else "not available"},
            {"label": "Last RF packet", "value": aprs_last_packet},
            {"label": "Last station", "value": aprs_telemetry.get("last_station") or "not available"},
        ],
    })

if MESHTASTIC_PREPARED:
    cards.append({
        "id": "meshtastic",
        "title": "Meshtastic / MQTT",
        "status": meshtastic_status,
        "summary": meshtastic_summary,
        "items": [
            {"label": "PCS state", "value": MESHTASTIC_STATE},
            {"label": "Service", "value": meshtastic_service_label},
            {"label": "Boot enablement", "value": "enabled" if meshtastic_service_enabled else "disabled"},
            {"label": "Status freshness", "value": age_label(meshtastic_snapshot_age)},
            {"label": "Node", "value": meshtastic_node_label},
            {"label": "Hardware / firmware", "value": meshtastic_hardware_label},
            {"label": "Transport", "value": f"{meshtastic_transport} / {'connected' if meshtastic_connected else 'disconnected'}"},
            {"label": "Power", "value": meshtastic_power},
            {"label": "MQTT broker", "value": meshtastic_broker_label},
            {"label": "MQTT session", "value": "connected" if meshtastic_mqtt_connected else "disconnected"},
            {"label": "Radio MQTT policy", "value": meshtastic_radio_policy_label},
            {"label": "RF to internet IGate", "value": meshtastic_rf_igate_label},
            {"label": "Public map report", "value": meshtastic_map_label},
            {"label": "Downlink filters", "value": str(meshtastic_downlink_filters)},
            {"label": "MQTT proxy activity", "value": meshtastic_mqtt_activity},
            {"label": "Embedded map mirror", "value": meshtastic_map_mqtt_activity},
            {"label": "Mesh packet counters", "value": meshtastic_mesh_activity},
            {"label": "Remote nodes", "value": meshtastic_remote_nodes},
            {"label": "Last mesh packet", "value": meshtastic_mesh.get("last_heard_at") or "none observed"},
            {"label": "GPSD position feed", "value": meshtastic_position_label},
            {"label": "Case environment", "value": meshtastic_environment_label},
            {"label": "LoRa utilization", "value": meshtastic_utilization_label},
            {"label": "Stored data", "value": "aggregate counters only; no messages, remote identities, positions, or channel keys"},
        ],
    })

card_order = [
    "system-stats",
    "services",
    "storage",
    "web-admin",
    "samba",
    "backup-health",
    "network",
    "remote-management",
    "cellular",
    "uplink-details",
    "client-lan",
    "time",
    "gps",
    "aprs",
    "meshtastic",
]

card_rank = {card_id: index for index, card_id in enumerate(card_order)}
cards.sort(key=lambda card: card_rank.get(card.get("id", ""), len(card_order)))

overall_cards = [
    card
    for card in cards
    if not (
        offline_mode
        and (
            card.get("id") == "uplink-details"
            or (card.get("id") == "network" and openwrt_online)
        )
    )
]
overall = "ok"
if any(card["status"] == "bad" for card in overall_cards):
    overall = "bad"
elif any(card["status"] == "warn" for card in overall_cards):
    overall = "warn"

client_info = {
    "router_ip": "10.42.0.1",
    "openwrt_url": "http://10.42.0.2/",
    "pi_star_configured": PI_STAR_CONFIGURED,
    "aprs_state": APRS_STATE,
    "meshtastic_state": MESHTASTIC_STATE,
    "wan_public_ip": wan_ip or "unavailable",
    "uplink_interface": uplink_info.get("interface") or "unknown",
    "uplink_source_ip": uplink_info.get("source_ip") or "unknown",
    "router_side_clients": router_clients,
}

if PI_STAR_CONFIGURED:
    client_info["pi_star_url"] = "http://10.42.0.3/"

data = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "overall": overall,
    "offline": offline_mode,
    "client_info": client_info,
    "cards": cards,
}

if PUBLIC_VIEW:
    system_items = card_items_by_id(cards, "system-stats")
    network_items = card_items_by_id(cards, "network")
    cellular_items = card_items_by_id(cards, "cellular")
    time_items = card_items_by_id(cards, "time")
    gps_items = card_items_by_id(cards, "gps")

    public_sections = {
        "system": {
            "status": system_status,
            "uptime": system_items.get("Uptime", "unknown"),
            "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cpu_temperature": system_items.get("CPU temp", "unavailable"),
            "cpu_load": system_items.get("CPU load", "unknown"),
            "memory_used": system_items.get("RAM used", "unknown"),
            "root_storage_used": system_items.get("Root disk used", "unknown"),
        },
        "network": {
            "status": network_status,
            "offline": offline_mode,
            "lan_gateway": "10.42.0.1",
            "openwrt_online": openwrt_online,
            "openwrt_url": "http://10.42.0.2/",
            "internet_available": internet_ok and dns_ok,
            "uplink_type": active_uplink_label,
            "connected_client_count": len(router_clients),
        },
        "remote_management": {
            "configured": wireguard["configured"],
            "status": wireguard_status,
            "connection": wireguard_connection,
            "management_address": wireguard["address"],
            "boot_enabled": wireguard["service_enabled"],
            "firewall_active": wireguard["firewall_active"],
            "latest_handshake": wireguard_handshake_label,
        },
        "cellular": {
            "status": cellular_status,
            "modem_present": modem_present,
            "connected": cellular_connected,
            "carrier": cellular_items.get("Operator", "unknown"),
            "access_technology": cellular_items.get("Access tech", "unknown"),
            "signal": signal_quality_label(cellular_items.get("Signal quality", "")),
            "fallback_policy": cellular_policy_label,
            "fallback_active": cellular_fallback_service_active,
        },
        "time": {
            "status": time_status,
            "chrony_active": chrony_active,
            "synchronized": clock_sync == "yes",
            "source": (
                "GNSS"
                if chrony_selected_gps and clock_sync == "yes"
                else "Internet NTP"
                if clock_sync == "yes" and not chrony_local_fallback
                else "RTC holdover"
                if chrony_local_fallback and rtc_seed_active
                else "Local clock fallback"
                if chrony_local_fallback
                else "Unsynchronized"
            ),
            "reference": time_items.get("Reference", "unknown"),
        },
        "gnss": {
            "status": gps_status,
            "receiver_active": gpsd_active or modem_present,
            "fix": gps_items.get("Fix quality", "unknown"),
            "satellites": gps_items.get("Satellites", "unknown"),
            "coordinates": gps_items.get("Lat/Lon", "not available"),
            "grid_square": gps_items.get("Grid square", "unknown"),
            "utc_time": gps_items.get("UTC time", "unknown"),
        },
        "storage": {
            "status": storage_status,
            "usb_mounted": usb_mount is not None,
            "primary_share_available": primary_share_ok,
            "backup_share_available": backup_share_ok,
            "usb_free_gb": f"{usb_usage.get('free_gb')} GB" if usb_usage else "unavailable",
            "backup_free_gb": f"{backup_usage.get('free_gb')} GB" if backup_usage else "unavailable",
        },
        "services": {
            "status": "ok" if smbd_active and web_admin["control_panel_active"] else "warn",
            "homepage_available": web_admin["control_panel_active"] and web_admin["port_80"],
            "file_sharing_available": smbd_active and (primary_share_ok or backup_share_ok),
            "cockpit_available": web_admin["cockpit_active"] and web_admin["port_9090"],
            "gpsd_lan_enabled": CONFIG.get("PCS_SETUP_GPSD_LAN", "auto").lower() != "no",
        },
        "pistar": {
            "configured": PI_STAR_CONFIGURED,
            "online": bool(pi_star.get("reachable")) if PI_STAR_CONFIGURED else False,
            "url": "http://10.42.0.3/" if PI_STAR_CONFIGURED else "",
        },
        "aprs": {
            "configured": APRS_CONFIGURED,
            "status": aprs_status,
            "service": aprs_service_label,
            "callsign": aprs_callsign,
            "role": aprs_role_label,
            "frequency": aprs_frequency,
            "aprs_is": "configured" if aprs_igate else "not configured",
            "aprs_is_profile": aprs_igate_label,
            "modem": aprs_modem_label,
            "beacon": aprs_beacon_label,
            "digipeater": aprs_digipeater_label,
            "kiss": aprs_kiss_label,
            "fx25": aprs_fx25_label,
            "packets": aprs_packet_summary,
            "last_heard": aprs_last_packet,
            "tx_state": aprs_tx_label,
        },
        "meshtastic": {
            "configured": MESHTASTIC_CONFIGURED,
            "status": meshtastic_status,
            "service": meshtastic_service_label,
            "node": meshtastic_node_label,
            "hardware": str(meshtastic_device.get("hardware") or "unknown"),
            "firmware": str(meshtastic_device.get("firmware") or "unknown"),
            "transport": meshtastic_transport,
            "radio_link": "connected" if meshtastic_connected else "disconnected",
            "mqtt": "connected" if meshtastic_mqtt_connected else "waiting for uplink" if offline_mode else "disconnected",
            "broker": meshtastic_broker_label,
            "mqtt_policy": meshtastic_radio_policy_label,
            "rf_igate": meshtastic_rf_igate_label,
            "map_reporting": meshtastic_map_label,
            "downlink_filters": meshtastic_downlink_filters,
            "mqtt_activity": meshtastic_mqtt_activity,
            "map_mqtt": meshtastic_map_mqtt_activity,
            "mesh_activity": meshtastic_mesh_activity,
            "remote_nodes": meshtastic_remote_nodes,
            "last_heard": meshtastic_mesh.get("last_heard_at") or "none observed",
            "gpsd_position": meshtastic_position_label,
            "case_environment": meshtastic_environment_label,
            "utilization": meshtastic_utilization_label,
            "power": meshtastic_power,
        },
    }

    public_overall = "ok"
    public_statuses = [
        section.get("status")
        for section_name, section in public_sections.items()
        if isinstance(section, dict)
        and section.get("status")
        and not (section_name in {"aprs", "meshtastic"} and not section.get("configured"))
        and not (offline_mode and section_name == "network" and openwrt_online)
    ]
    if "bad" in public_statuses:
        public_overall = "bad"
    elif "warn" in public_statuses:
        public_overall = "warn"

    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall": public_overall,
        "offline": offline_mode,
        **public_sections,
    }

print(json.dumps(data, indent=2))
PY
}

dashboard_public_json() {
    PCS_DASHBOARD_VIEW=public dashboard_json
}

meshtastic_status_action() {
    header "Meshtastic / MQTT Status"

    echo "--- Service ---"
    systemctl status pcs-meshtastic.service --no-pager -l || true
    echo

    echo "--- Privacy-safe gateway snapshot ---"
    if [[ -x /usr/local/sbin/pcs_meshtastic_status.py ]]; then
        runuser -u "${PCS_USER}" -- /usr/local/sbin/pcs_meshtastic_status.py --check || true
    elif [[ -r /var/lib/pcs-meshtastic/status.json ]]; then
        python3 -m json.tool /var/lib/pcs-meshtastic/status.json || true
    else
        echo "No Meshtastic gateway snapshot is available."
    fi
}

restart_meshtastic_action() {
    header "Restart Meshtastic / MQTT Gateway"

    if [[ "${PCS_SETUP_MESHTASTIC:-no}" != "yes" ]]; then
        echo "ERROR: Meshtastic is not configured as an active PCS subsystem."
        return 1
    fi
    if ! systemctl cat pcs-meshtastic.service >/dev/null 2>&1; then
        echo "ERROR: pcs-meshtastic.service is not installed."
        return 1
    fi

    systemctl restart pcs-meshtastic.service
    if ! timeout 150 bash -c 'until systemctl is-active --quiet pcs-meshtastic.service; do sleep 2; done'; then
        echo "ERROR: Meshtastic gateway did not become active within 150 seconds."
        systemctl status pcs-meshtastic.service --no-pager -l || true
        return 1
    fi
    sleep 3
    meshtastic_status_action
}

require_root
ensure_repo
dispatch_host_namespace_action

cellular_data_iface() {
    local gsm_dev
    local ip_iface

    gsm_dev="$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | awk -F: '$2 == "gsm" && $3 == "connected" { print $1; exit }')"

    if [[ -n "${gsm_dev}" ]]; then
        ip_iface="$(nmcli -g GENERAL.IP-IFACE device show "${gsm_dev}" 2>/dev/null | head -n 1)"
        if [[ -n "${ip_iface}" && "${ip_iface}" != "--" ]]; then
            echo "${ip_iface}"
            return 0
        fi
    fi

    for ip_iface in wwan0 ppp0; do
        if ip -br addr show "${ip_iface}" 2>/dev/null | awk 'NF > 2 { found=1 } END { exit found ? 0 : 1 }'; then
            echo "${ip_iface}"
            return 0
        fi
    done

    return 1
}

cellular_safe_status() {
    local modem_list
    local modem_num

    echo "=== PCS Cellular Status ==="
    echo

    echo "--- ModemManager modem list ---"
    modem_list="$(mmcli -L 2>/dev/null || true)"
    if [[ -n "${modem_list}" ]]; then
        echo "${modem_list}"
    else
        echo "No modems were found"
    fi

    modem_num="$(echo "${modem_list}" | sed -n 's#.*Modem/\([0-9]\+\).*#\1#p' | head -n 1)"

    if [[ -n "${modem_num}" ]]; then
        echo
        echo "--- Safe modem summary ---"
        mmcli -m "${modem_num}" 2>/dev/null \
            | grep -Ei "manufacturer:|model:|firmware revision:|h/w revision:|hardware revision:|state:|power state:|access tech:|signal quality:|operator name:|registration:|packet service state:|ports:" \
            | sed -E \
                -e 's/(equipment id: ).*/\1[REDACTED]/I' \
                -e 's/(imei: ).*/\1[REDACTED]/I' \
                -e 's/(own numbers?: ).*/\1[REDACTED]/I' \
                -e 's/(own: ).*/\1[REDACTED]/I' \
                -e 's/(subscriber id: ).*/\1[REDACTED]/I' \
                -e 's/(sim iccid: ).*/\1[REDACTED]/I' \
            || true
    fi

    echo
    echo "--- NetworkManager devices ---"
    nmcli device status || true

    echo
    echo "--- Cellular profile ---"
    local cell_con
    cell_con="$(cellular_profile_name)"
    if nmcli -t -f NAME connection show | grep -Fxq -- "${cell_con}"; then
        nmcli connection show "${cell_con}" \
            | grep -E "connection.id|connection.autoconnect|gsm.apn|ipv4.method|ipv6.method|ipv4.route-metric|ipv6.route-metric" \
            || true
    else
        echo "${cell_con} profile does not exist yet"
    fi

    echo
    echo "--- Cellular fallback policy ---"
    echo "Configured policy: ${CELLULAR_FALLBACK_MODE}"
    echo "Fallback service enabled: $(systemctl is-enabled pcs-cellular-fallback.service 2>/dev/null || true)"
    echo "Fallback service active: $(systemctl is-active pcs-cellular-fallback.service 2>/dev/null || true)"
    if [[ -e /run/pcs-cellular-fallback-owned ]]; then
        echo "Session ownership: automatic fallback"
    else
        echo "Session ownership: manual or disconnected"
    fi

    echo
    echo "--- Cellular address state ---"
    local cell_iface
    cell_iface="$(cellular_data_iface || true)"
    if [[ -n "${cell_iface}" ]]; then
        if ip -br addr show "${cell_iface}" | awk 'NF > 2 { found=1 } END { exit found ? 0 : 1 }'; then
            echo "${cell_iface}: IP assigned"
        else
            echo "${cell_iface}: no IP assigned"
        fi
    else
        echo "No active cellular data interface found"
    fi

    echo
    echo "--- Default route interfaces ---"
    ip route show default 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="dev") print "default via " $(i+1)}' || true

    echo
    echo "=== Cellular status complete ==="
}

cellular_ensure_profile() {
    local cell_con
    cell_con="$(cellular_profile_name)"

    if nmcli -t -f NAME connection show | grep -Fxq -- "${cell_con}"; then
        echo "Connection ${cell_con} already exists. Updating it."
    else
        echo "Creating connection ${cell_con}."
        nmcli connection add type gsm ifname "*" con-name "${cell_con}" apn "${CELLULAR_APN}"
    fi

    nmcli connection modify "${cell_con}" \
        gsm.apn "${CELLULAR_APN}" \
        connection.autoconnect no \
        ipv4.method auto \
        ipv6.method auto \
        ipv4.route-metric "${CELLULAR_ROUTE_METRIC}" \
        ipv6.route-metric "${CELLULAR_ROUTE_METRIC}"
}

cellular_nm_state() {
    nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
        | awk -F: '$2 == "gsm" { print $3; exit }'
}

cellular_wait_for_modemmanager() {
    local attempt

    echo "Waiting for ModemManager to rediscover and register the modem..."

    for attempt in $(seq 1 70); do
        if mmcli -m 0 --output-keyvalue 2>/dev/null | grep -Eq "modem\.generic\.state[[:space:]]*:[[:space:]]*(registered|connected)" \
            && nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | awk -F: '$2 == "gsm" { found=1 } END { exit found ? 0 : 1 }'; then
            return 0
        fi

        sleep 1
    done

    return 1
}

cellular_restart_modemmanager() {
    echo "--- Recovering cellular modem state ---"
    echo "Restarting ModemManager to clear the stuck modem/bearer state."
    systemctl restart ModemManager

    if cellular_wait_for_modemmanager; then
        echo "ModemManager sees the GSM device again."
    else
        echo "WARNING: GSM device did not reappear after restarting ModemManager."
        return 1
    fi
}

soft_replug_wwan_usb_device() {
    local iface_path
    local search_path
    local usb_device_path=""

    echo "--- Soft-replug WWAN USB device ---"

    if [[ ! -e /dev/cdc-wdm0 ]]; then
        echo "Cannot soft-replug WWAN USB: /dev/cdc-wdm0 is missing."
        return 1
    fi

    iface_path="$(readlink -f /sys/class/usbmisc/cdc-wdm0/device 2>/dev/null || true)"

    if [[ -z "${iface_path}" ]]; then
        echo "Cannot resolve sysfs path for /dev/cdc-wdm0."
        return 1
    fi

    search_path="${iface_path}"

    while [[ -n "${search_path}" && "${search_path}" != "/" ]]; do
        if [[ -e "${search_path}/authorized" && -e "${search_path}/idVendor" && -e "${search_path}/idProduct" ]]; then
            usb_device_path="${search_path}"
            break
        fi

        search_path="$(dirname "${search_path}")"
    done

    if [[ -z "${usb_device_path}" ]]; then
        echo "Cannot find USB device authorized control."
        echo "Resolved cdc-wdm0 sysfs path:"
        echo "  ${iface_path}"
        return 1
    fi

    echo "Soft-disconnecting modem USB device:"
    echo "  ${usb_device_path}"

    echo 0 > "${usb_device_path}/authorized"
    sleep 5

    echo "Soft-reconnecting modem USB device."
    echo 1 > "${usb_device_path}/authorized"

    echo "Waiting for modem device nodes to return..."
    for attempt in $(seq 1 60); do
        if [[ -e /dev/cdc-wdm0 && -e /dev/ttyUSB0 && -e /dev/ttyUSB1 && -e /dev/ttyUSB2 ]]; then
            udevadm settle --timeout=10 >/dev/null 2>&1 || true
            return 0
        fi

        if (( attempt % 10 == 0 )); then
            echo "Still waiting for complete WWAN device nodes... $((attempt * 2))s"
        fi

        sleep 2
    done

    if [[ -e /dev/cdc-wdm0 && -e /dev/ttyUSB1 ]]; then
        echo "WARNING: Minimum WWAN nodes returned, but the full serial set did not appear in time."
        udevadm settle --timeout=10 >/dev/null 2>&1 || true
        return 0
    fi

    echo "WARNING: WWAN device nodes did not fully return after USB soft replug."
    udevadm settle --timeout=10 >/dev/null 2>&1 || true
    return 1
}

restart_modemmanager_action() {
    local attempt
    local modem_list

    header "Restart ModemManager"

    echo "Stopping ModemManager before WWAN USB recovery."
    systemctl stop ModemManager 2>/dev/null || true

    if ! soft_replug_wwan_usb_device; then
        echo "Continuing with ModemManager restart in case the modem is already usable."
    fi

    echo
    echo "Starting ModemManager."
    systemctl start ModemManager

    echo "Waiting for ModemManager to rediscover modem hardware..."

    for attempt in $(seq 1 36); do
        modem_list="$(mmcli -L 2>/dev/null || true)"

        if echo "${modem_list}" | grep -q "/Modem/"; then
            echo "ModemManager detected modem."
            echo
            echo "--- ModemManager modem list ---"
            echo "${modem_list}"
            echo
            systemctl status ModemManager --no-pager -l || true

            if systemctl cat pcs-wwan-gps-nmea.service >/dev/null 2>&1; then
                echo
                echo "--- Reassert WWAN GPS/NMEA, gpsd, and Chrony ---"
                restart_gpsd_path || true
            fi

            return 0
        fi

        echo "Waiting for ModemManager modem detection... ${attempt}/36"
        mmcli --scan-modems >/dev/null 2>&1 || true
        sleep 5
    done

    echo "WARNING: ModemManager did not detect a modem within 180 seconds after restart."
    echo
    echo "--- ModemManager modem list ---"
    mmcli -L || true
    echo
    systemctl status ModemManager --no-pager -l || true
    return 1
}

cellular_connection_up() {
    local cell_con="$1"
    local wait_seconds="$2"

    nmcli --wait "${wait_seconds}" connection up "${cell_con}"
}

cellular_connect() {
    local cell_con
    local cell_iface
    local gsm_state

    echo "=== PCS Cellular Connect ==="
    echo
    cellular_ensure_profile
    cell_con="$(cellular_profile_name)"

    echo
    gsm_state="$(cellular_nm_state)"
    if [[ "${gsm_state}" == "connected" ]]; then
        echo "--- Cellular connection already active ---"
        nmcli device status | awk '$2 == "gsm" { print $1, $2, $3, $4 }' || true
    else
        if [[ "${gsm_state}" == connecting* ]]; then
            echo "--- Cellular activation appears stuck: ${gsm_state} ---"
            nmcli --wait 5 connection down "${cell_con}" || true
            cellular_restart_modemmanager || true
        fi

        echo "--- Bringing cellular connection up ---"
        if ! cellular_connection_up "${cell_con}" 20; then
            echo
            echo "WARNING: cellular connection did not become active within 20 seconds."
            echo "Trying one ModemManager recovery before giving up."
            echo
            nmcli --wait 5 connection down "${cell_con}" || true
            cellular_restart_modemmanager || true

            echo
            echo "--- Retrying cellular connection ---"
            if ! cellular_connection_up "${cell_con}" 25; then
                echo
                echo "WARNING: cellular connection did not become active after modem recovery."
                echo "The modem may still be registering or waiting for cellular service."
                echo
                cellular_safe_status
                return 1
            fi
        fi
    fi

    echo
    echo "--- Waiting 3 seconds ---"
    sleep 3

    echo
    cell_iface="$(cellular_data_iface || true)"
    echo "--- Cellular ping test through ${cell_iface:-unknown} ---"
    if [[ -n "${cell_iface}" ]]; then
        ping -I "${cell_iface}" -c 3 -W 4 8.8.8.8 || true
    else
        echo "No active cellular data interface found."
    fi

    echo
    cellular_safe_status
}

cellular_disconnect() {
    local cell_con
    cell_con="$(cellular_profile_name)"

    echo "=== PCS Cellular Disconnect ==="
    echo

    if nmcli -t -f NAME connection show | grep -qx "${cell_con}"; then
        nmcli connection down "${cell_con}" || true
        if [[ "${CELLULAR_FALLBACK_MODE}" == "wifi-fallback" ]]; then
            echo "Automatic fallback remains armed and may reconnect cellular while Wi-Fi is unavailable."
        fi
    else
        echo "Connection ${cell_con} does not exist."
    fi

    echo
    cellular_safe_status
}


cellular_test_internet() {
    local cell_iface
    local http_code
    local ping_output

    echo "=== PCS Cellular Internet Test ==="
    echo

    echo "--- GSM device state ---"
    if nmcli device status 2>/dev/null | awk '$2 == "gsm" { found=1 } END { exit found ? 0 : 1 }'; then
        nmcli device status | awk '$2 == "gsm" { print $1, $2, $3, $4 }'
    else
        echo "No GSM/WWAN device shown by NetworkManager."
        echo "Result: FAIL"
        return 1
    fi

    echo
    cell_iface="$(cellular_data_iface || true)"

    echo "--- Cellular data interface state ---"
    if [[ -n "${cell_iface}" ]] && ip link show "${cell_iface}" >/dev/null 2>&1; then
        echo "${cell_iface}: present"
    else
        echo "No active cellular data interface found"
        echo "Result: FAIL"
        return 1
    fi

    if ip -br addr show "${cell_iface}" 2>/dev/null | awk 'NF > 2 { found=1 } END { exit found ? 0 : 1 }'; then
        echo "${cell_iface} IP assignment: present / hidden"
    else
        echo "${cell_iface} IP assignment: missing"
        echo "Result: FAIL"
        return 1
    fi

    echo
    echo "--- Cellular route check ---"
    if ip route show default 2>/dev/null | grep -q "dev ${cell_iface}"; then
        echo "${cell_iface} default route: present"
    else
        echo "${cell_iface} default route: missing"
        echo "Result: FAIL"
        return 1
    fi

    echo
    echo "--- Cellular ping test ---"
    ping_output="$(ping -I "${cell_iface}" -c 3 -W 4 8.8.8.8 2>&1 || true)"

    if echo "${ping_output}" | grep -q " 0% packet loss"; then
        echo "Ping via ${cell_iface}: PASS"
        echo "${ping_output}" | grep -E "packets transmitted|packet loss|rtt" || true
    else
        echo "Ping via ${cell_iface}: FAIL"
        echo "${ping_output}" | grep -E "packets transmitted|packet loss|rtt|Network is unreachable|unknown host|Destination" || true
        echo "Result: FAIL"
        return 1
    fi

    echo
    echo "--- Cellular HTTPS test ---"
    http_code="$(curl --interface "${cell_iface}" -4 --max-time 20 --silent --show-error --output /dev/null --write-out "%{http_code}" https://api.ipify.org 2>/tmp/pcs-cellular-curl.err || true)"

    if [[ "${http_code}" == "200" ]]; then
        echo "HTTPS via ${cell_iface}: PASS"
        echo "Public IP: assigned / hidden"
    else
        echo "HTTPS via ${cell_iface}: FAIL"
        echo "HTTP code: ${http_code:-none}"
        if [[ -s /tmp/pcs-cellular-curl.err ]]; then
            echo "curl error:"
            cat /tmp/pcs-cellular-curl.err
        fi
        rm -f /tmp/pcs-cellular-curl.err
        echo "Result: FAIL"
        return 1
    fi

    rm -f /tmp/pcs-cellular-curl.err

    echo
    echo "Result: PASS"
    echo "Cellular internet access through ${cell_iface} is working."
}


sync_time_now() {
    local tracking_after_sync

    header "Sync Time Now"

    echo "Chrony will prefer usable GPS, then use Internet NTP if GPS is unavailable."
    echo "RTC-seeded local holdover remains the final degraded fallback."
    echo

    echo "--- Chrony tracking before sync ---"
    chronyc tracking || true

    echo
    echo "--- Chrony sources before sync ---"
    chronyc sources -v || true

    echo
    echo "--- Requesting fresh Chrony samples ---"
    chronyc burst 4/4 || true

    echo
    echo "Waiting briefly for fresh samples..."
    sleep 10

    echo
    echo "--- Stepping system clock if Chrony decides it is needed ---"
    chronyc makestep || true

    echo
    echo "--- Waiting for synchronized state ---"
    chronyc waitsync 30 0.5 0.0 2 || true

    tracking_after_sync="$(chronyc tracking 2>/dev/null || true)"

    if [[ -e /dev/rtc0 ]] \
        && grep -qE '^Leap status[[:space:]]*:[[:space:]]*Normal' <<<"${tracking_after_sync}" \
        && ! grep -qE '^Reference ID[[:space:]]*:[[:space:]]*7F7F0101' <<<"${tracking_after_sync}" \
        && ! grep -qE '^Stratum[[:space:]]*:[[:space:]]*10([[:space:]]|$)' <<<"${tracking_after_sync}"; then
        echo
        echo "--- Writing authoritative GPS/Internet time to RTC ---"
        hwclock --systohc --utc || true
    elif [[ -e /dev/rtc0 ]]; then
        echo
        echo "--- RTC write skipped: Chrony has no authoritative GPS/Internet synchronization ---"
    else
        echo
        echo "--- RTC not present; skipping RTC write ---"
    fi

    echo
    echo "--- Chrony tracking after sync ---"
    chronyc tracking || true

    echo
    echo "--- Chrony sources after sync ---"
    chronyc sources -v || true
}

restart_gpsd_path() {
    local nmea_seen

    header "Restart GPSD / WWAN NMEA"

    echo "Reasserting modem GPS/NMEA mode before restarting gpsd."
    echo

    if systemctl cat pcs-wwan-gps-nmea.service >/dev/null 2>&1; then
        systemctl restart pcs-wwan-gps-nmea.service || true
        systemctl status pcs-wwan-gps-nmea.service --no-pager -l || true
    else
        echo "WARNING: pcs-wwan-gps-nmea.service is not installed."
    fi

    echo
    echo "--- Restarting gpsd ---"
    systemctl restart gpsd.service
    systemctl status gpsd.service --no-pager -l || true

    echo
    echo "--- gpsd NMEA quick check ---"
    if command -v gpspipe >/dev/null 2>&1; then
        nmea_seen=0

        for attempt in $(seq 1 4); do
            if timeout 8 gpspipe -r -n 5 2>/dev/null | grep -q '^\$G'; then
                nmea_seen=1
                break
            fi

            echo "Waiting for gpsd NMEA sample... ${attempt}/4"
            sleep 5
        done

        if [[ "${nmea_seen}" -eq 1 ]]; then
            echo "gpsd NMEA: present / location hidden"
        else
            echo "gpsd NMEA: not seen during quick check"
        fi
    else
        echo "gpspipe is not installed; skipping quick NMEA check."
    fi

    echo
    echo "--- Refreshing Chrony GPS/NTP path ---"
    systemctl start chrony
    systemctl status chrony --no-pager -l || true

    echo
    echo "Requesting fresh Chrony samples without restarting the NTP server."
    chronyc burst 4/4 || true
    sleep 15

    echo
    echo "--- Chrony sources after GPSD restart ---"
    chronyc sources -v || true

    echo
    echo "--- Chrony tracking after GPSD restart ---"
    chronyc tracking || true

    echo
    echo "GPSD / Chrony restart complete."
}


wifi_active_connection() {
    local active_conn
    active_conn="$(nmcli -t -f DEVICE,TYPE,CONNECTION device status 2>/dev/null \
        | awk -F: '$1 == "wlan0" && $2 == "wifi" { print $3; exit }')"

    if [[ -n "${active_conn}" && "${active_conn}" != "--" ]]; then
        echo "${active_conn}"
        return 0
    fi
}

wifi_saved_profile_names() {
    nmcli -t --escape no -f NAME,TYPE connection show 2>/dev/null \
        | awk -F: '$2 == "wifi" || $2 == "802-11-wireless" { print $1 }'
}

wifi_profile_ssid() {
    local profile="$1"
    local ssid

    ssid="$(nmcli -g 802-11-wireless.ssid connection show "${profile}" 2>/dev/null | head -n 1)"

    if [[ -n "${ssid}" && "${ssid}" != "--" ]]; then
        echo "${ssid}"
    else
        echo "${profile}"
    fi
}

wifi_visible_known_connection() {
    local visible_file
    local profile
    local ssid
    local line_ssid
    local signal
    local best_profile=""
    local best_signal=-1

    visible_file="$(mktemp)"
    nmcli -t --escape no -f SSID,SIGNAL device wifi list ifname wlan0 --rescan no > "${visible_file}" 2>/dev/null || true

    while IFS= read -r profile; do
        [[ -n "${profile}" ]] || continue
        ssid="$(wifi_profile_ssid "${profile}")"

        while IFS=: read -r line_ssid signal _; do
            [[ -n "${line_ssid}" ]] || continue
            [[ "${line_ssid}" == "${ssid}" ]] || continue
            [[ "${signal}" =~ ^[0-9]+$ ]] || continue

            if (( signal > best_signal )); then
                best_signal="${signal}"
                best_profile="${profile}"
            fi
        done < "${visible_file}"
    done < <(wifi_saved_profile_names)

    rm -f "${visible_file}"

    if [[ -n "${best_profile}" ]]; then
        echo "${best_profile}"
        return 0
    fi

    return 1
}

wifi_known_profiles_table() {
    local profile
    local ssid

    while IFS= read -r profile; do
        [[ -n "${profile}" ]] || continue
        ssid="$(wifi_profile_ssid "${profile}")"
        printf '  %s -> %s\n' "${profile}" "${ssid}"
    done < <(wifi_saved_profile_names)
}

wifi_status() {
    header "Wi-Fi Uplink Status"

    echo "--- NetworkManager device status ---"
    nmcli device status || true

    echo
    echo "--- Wi-Fi radio ---"
    nmcli radio wifi || true

    echo
    echo "--- wlan0 address ---"
    ip -br addr show wlan0 || true

    echo
    echo "--- Default route ---"
    ip route | grep '^default' || true

    echo
    echo "--- Active Wi-Fi uplink ---"
    ACTIVE_WIFI="$(wifi_active_connection || true)"
    echo "${ACTIVE_WIFI:-none active}"

    echo
    echo "--- Saved Wi-Fi profiles ---"
    if wifi_saved_profile_names | grep -q .; then
        wifi_known_profiles_table
    else
        echo "No saved Wi-Fi profiles were found."
    fi
}

wifi_connect() {
    header "Connect Wi-Fi Uplink"

    echo "Turning Wi-Fi radio on..."
    nmcli radio wifi on
    sleep 2

    echo
    echo "--- Saved Wi-Fi profiles ---"
    if wifi_saved_profile_names | grep -q .; then
        wifi_known_profiles_table
    else
        echo "ERROR: no saved Wi-Fi profiles were found."
        echo
        echo "Add a Wi-Fi profile with NetworkManager before using this button."
        return 1
    fi

    echo
    echo "--- Scanning for known access points ---"
    nmcli --wait 15 device wifi rescan ifname wlan0 || true
    sleep 3

    WIFI_CONN="$(wifi_visible_known_connection || true)"

    if [[ -z "${WIFI_CONN}" ]]; then
        echo "ERROR: none of the saved Wi-Fi profiles are currently visible."
        echo
        echo "Visible access points:"
        nmcli -f SSID,SIGNAL,SECURITY device wifi list ifname wlan0 --rescan no || true
        return 1
    fi

    echo "Connecting Wi-Fi profile:"
    echo "  ${WIFI_CONN}"
    nmcli --wait 30 connection up "${WIFI_CONN}"

    echo
    wifi_status
}

wifi_disconnect() {
    header "Disable Wi-Fi Radio"

    ACTIVE_WIFI="$(nmcli -t -f DEVICE,TYPE,CONNECTION device status 2>/dev/null \
        | awk -F: '$1 == "wlan0" && $2 == "wifi" { print $3; exit }')"

    if [[ -n "${ACTIVE_WIFI}" && "${ACTIVE_WIFI}" != "--" ]]; then
        echo "Disconnecting active Wi-Fi profile:"
        echo "  ${ACTIVE_WIFI}"
        nmcli connection down "${ACTIVE_WIFI}" || true
    else
        echo "No active Wi-Fi connection on wlan0."
    fi

    echo
    echo "Turning Wi-Fi radio off..."
    nmcli radio wifi off
    echo
    echo "Wi-Fi radio is disabled. Use Connect Wi-Fi Uplink to scan for saved access points and turn it back on."

    echo
    wifi_status
}

case "${ACTION}" in
    wifi-status)
        wifi_status
        ;;

    wifi-connect)
        wifi_connect
        ;;

    wifi-disconnect)
        wifi_disconnect
        ;;

    cellular-status)
        cellular_safe_status
        ;;

    cellular-connect)
        cellular_connect
        ;;

    cellular-disconnect)
        cellular_disconnect
        ;;

    cellular-test)
        cellular_test_internet
        ;;

    meshtastic-status)
        meshtastic_status_action
        ;;

    restart-meshtastic)
        restart_meshtastic_action
        ;;

    dashboard-json)
        dashboard_json
        ;;

    dashboard-public-json)
        dashboard_public_json
        ;;

    status)
    header "PCS Status"
    cd "${REPO_DIR}"

    echo "Running pcs-status.sh with a 90 second web-panel timeout..."
    echo

    if timeout 90 ./scripts/pcs-status.sh; then
        echo
        echo "PCS status completed."
    else
        echo
        echo "WARNING: pcs-status.sh timed out or exited with an error."
        echo "The full status script may still work normally from a terminal."
        echo
        echo "Try from SSH/terminal:"
        echo "  cd ${REPO_DIR}"
        echo "  ./scripts/pcs-status.sh"
    fi
    ;;

    self-test)
        header "PCS Self-Test"
        runuser -u "${PCS_USER}" -- bash -lc "cd '${REPO_DIR}' && XDG_RUNTIME_DIR=/run/user/1000 ./scripts/pcs-self-test.sh"
        ;;

    storage-status)
        storage_status
        ;;

    sync-backup)
        sync_backup
        ;;

    mount-usb)
        mount_usb
        ;;

    mount-new-usb)
        mount_new_usb
        ;;

    safe-unmount-usb)
        safe_unmount_usb
        ;;

    restart-services)
        header "Restart PCS Services"
        systemctl start pcs-restart-services.service
        systemctl status pcs-restart-services.service --no-pager -l || true
        ;;

    restart-samba)
        header "Restart Samba"
        systemctl restart smbd
        systemctl status smbd --no-pager -l || true
        ;;

    restart-modemmanager)
        restart_modemmanager_action
        ;;

    sync-time)
        sync_time_now
        ;;

    restart-chrony)
        header "Restart Chrony"
        systemctl restart chrony
        systemctl status chrony --no-pager -l || true
        chronyc tracking || true
        ;;

    restart-gpsd)
        restart_gpsd_path
        ;;

    restart-logs)
        header "PCS Restart Service Logs"
        journalctl -u pcs-restart-services.service -n 120 --no-pager
        ;;

    reboot-system)
        header "Reboot PCS"
        echo "Reboot requested from PCS Control Panel."
        echo "The dashboard will disconnect while the Pi restarts."
        systemctl --no-block reboot
        ;;

    shutdown-system)
        header "Shutdown PCS"
        echo "Shutdown requested from PCS Control Panel."
        request_pistar_poweroff
        echo "Wait for the Pi activity LED to settle before removing power."
        systemctl --no-block poweroff
        ;;

    ""|-h|--help|help)
        show_help
        ;;

    *)
        echo "ERROR: unknown or disallowed action: ${ACTION}"
        echo
        show_help
        exit 2
        ;;
esac
