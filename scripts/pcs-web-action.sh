#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
USB_MOUNT="/mnt/pcs-usb"
PRIMARY_SHARE="/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE="/srv/pcs-share-backup"
PCS_USER="pi"

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

show_help() {
    cat <<EOF
PCS web action dispatcher

Allowed actions:

  dashboard-json
  status
  self-test
  storage-status
  sync-backup
  mount-usb
  safe-unmount-usb
  restart-services
  restart-samba
  restart-chrony
  restart-logs
EOF
}

ensure_repo() {
    if [[ ! -d "${REPO_DIR}" ]]; then
        echo "ERROR: repo directory missing: ${REPO_DIR}"
        exit 1
    fi
}

ensure_usb_mounted() {
    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "ERROR: USB primary storage is not mounted at ${USB_MOUNT}"
        echo
        echo "Try the mount-usb action first."
        exit 1
    fi
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
    header "Mount USB Primary Share"

    mkdir -p "${USB_MOUNT}"

    systemctl daemon-reload || true

    if findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "${USB_MOUNT} is already mounted."
    else
        mount "${USB_MOUNT}"
    fi

    systemctl restart smbd

    echo
    echo "USB mount:"
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
import shutil
import subprocess
from datetime import datetime

USB_MOUNT = "/mnt/pcs-usb"
PRIMARY_SHARE = "/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE = "/srv/pcs-share-backup"

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

def ip_has_address(device, cidr):
    rc, out, _ = run(["ip", "-4", "addr", "show", "dev", device], timeout=4)
    return rc == 0 and cidr in out

def default_route_iface():
    rc, out, _ = run("ip route | awk '/^default/ {print $5; exit}'", timeout=4)
    return out if rc == 0 else ""

def ping_ok(target):
    rc, _, _ = run(["ping", "-c", "1", "-W", "2", target], timeout=4)
    return rc == 0

def public_wan_ip():
    for cmd in [
        ["curl", "-fsS", "--max-time", "5", "https://api.ipify.org"],
        ["curl", "-fsS", "--max-time", "5", "https://ifconfig.me/ip"],
    ]:
        rc, out, _ = run(cmd, timeout=7)
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

def router_side_clients():
    rc, out, _ = run(["ip", "neigh", "show", "dev", "eth0"], timeout=4)
    clients = []
    if rc != 0:
        return clients

    manual_names = manual_client_names()
    lease_names = dhcp_lease_names()

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
            friendly_name = resolve_client_name(ip, mac, manual_names, lease_names)

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
wan_ip = public_wan_ip()
uplink_info = uplink_route_info()
router_clients = router_side_clients()

chrony = chrony_tracking()
chrony_active = active("chrony")
clock_sync = timedate_value("System clock synchronized")
ntp_service = timedate_value("NTP service")
rtc_local = timedate_value("RTC in local TZ")
chrony_ref = chrony.get("Reference ID", "")
chrony_stratum = chrony.get("Stratum", "")
chrony_local_fallback = chrony_ref.startswith("7F7F0101") or chrony_stratum == "10"

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

mm_rc, mm_out, _ = run(["mmcli", "-L"], timeout=5)
modem_present = "/Modem/" in mm_out
gpsd_active = active("gpsd")

network_status = "ok" if wifi_ok and eth_ok and internet_ok and dns_ok and eth_ip_ok else "bad"
storage_status = "ok" if usb_mount and primary_share_ok and backup_share_ok and last_sync else "warn"
time_status = "ok" if chrony_active and clock_sync == "yes" else "warn"
samba_status = "ok" if smbd_active and primary_share_ok and backup_share_ok else "bad"
services_status = "ok" if all(core_services.values()) else "warn"
hardware_status = "warn" if not modem_present or not gpsd_active else "ok"

cards = [
    {
        "id": "network",
        "title": "Network",
        "status": network_status,
        "summary": "Online and router handoff active" if network_status == "ok" else "Network needs attention",
        "items": [
            {"label": "Wi-Fi uplink", "value": f"{'connected' if wifi_ok else 'not connected'} ({wifi_conn or 'unknown'})"},
            {"label": "Ethernet handoff", "value": f"{'connected' if eth_ok else 'not connected'} ({eth_conn or 'unknown'})"},
            {"label": "Router-side IP", "value": "10.42.0.1/24 present" if eth_ip_ok else "missing"},
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
        "summary": "Chrony synchronized" if time_status == "ok" and not chrony_local_fallback else "Chrony using local fallback" if chrony_local_fallback else "Time needs attention",
        "items": [
            {"label": "Chrony", "value": "active" if chrony_active else "inactive"},
            {"label": "System synchronized", "value": clock_sync or "unknown"},
            {"label": "NTP service", "value": ntp_service or "unknown"},
            {"label": "RTC local TZ", "value": rtc_local or "unknown"},
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
        "title": "Core services",
        "status": services_status,
        "summary": "Core services active" if services_status == "ok" else "One or more services need attention",
        "items": [{"label": name, "value": "active" if ok else "inactive"} for name, ok in core_services.items()],
    },
    {
        "id": "hardware",
        "title": "Hardware placeholders",
        "status": hardware_status,
        "summary": "Waiting for WWAN/GNSS hardware" if hardware_status == "warn" else "Hardware online",
        "items": [
            {"label": "WWAN modem", "value": "detected" if modem_present else "not detected yet"},
            {"label": "GPSD", "value": "active" if gpsd_active else "inactive / not configured yet"},
            {"label": "Expected state", "value": "modem and GPS pending until hardware arrives"},
        ],
    },
]

overall = "ok"
if any(card["status"] == "bad" for card in cards):
    overall = "bad"
elif any(card["status"] == "warn" for card in cards):
    overall = "warn"

data = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "overall": overall,
    "client_info": {
        "router_ip": "10.42.0.1",
        "wan_public_ip": wan_ip or "unavailable",
        "uplink_interface": uplink_info.get("interface") or "unknown",
        "uplink_source_ip": uplink_info.get("source_ip") or "unknown",
        "router_side_clients": router_clients,
    },
    "cards": cards,
}

print(json.dumps(data, indent=2))
PY
}

require_root
ensure_repo

case "${ACTION}" in
    dashboard-json)
        dashboard_json
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

    restart-chrony)
        header "Restart Chrony"
        systemctl restart chrony
        systemctl status chrony --no-pager -l || true
        chronyc tracking || true
        ;;

    restart-logs)
        header "PCS Restart Service Logs"
        journalctl -u pcs-restart-services.service -n 120 --no-pager
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
