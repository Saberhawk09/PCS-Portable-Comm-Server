#!/usr/bin/env bash

set -Eeuo pipefail

MODE="apply"
if [[ "${1:-}" == "--check" ]]; then
    MODE="check"
elif [[ "${1:-}" == "--apply" || -z "${1:-}" ]]; then
    MODE="apply"
else
    echo "Usage: $0 [--apply|--check]"
    exit 2
fi

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Run this script as the normal Pi-Star user, not with sudo."
    echo "The script uses sudo only for the files and services it manages."
    exit 1
fi

PCS_PISTAR_HOSTNAME="${PCS_PISTAR_HOSTNAME:-pcs-hotspot}"
PCS_PISTAR_INTERFACE="${PCS_PISTAR_INTERFACE:-eth0}"
PCS_PISTAR_ETHERNET_DRIVER="${PCS_PISTAR_ETHERNET_DRIVER:-r8152}"
PCS_PISTAR_DISABLE_WIFI="${PCS_PISTAR_DISABLE_WIFI:-yes}"
PCS_PISTAR_WIFI_INTERFACE="${PCS_PISTAR_WIFI_INTERFACE:-wlan0}"
PCS_PISTAR_LINK_STABILITY_SECONDS="${PCS_PISTAR_LINK_STABILITY_SECONDS:-30}"
PCS_PISTAR_ADDRESS="${PCS_PISTAR_ADDRESS:-10.42.0.3/24}"
PCS_PISTAR_GATEWAY="${PCS_PISTAR_GATEWAY:-10.42.0.1}"
PCS_PISTAR_DNS="${PCS_PISTAR_DNS:-10.42.0.1}"
PCS_PISTAR_NTP="${PCS_PISTAR_NTP:-10.42.0.1}"
PCS_PISTAR_FALLBACK_NTP="${PCS_PISTAR_FALLBACK_NTP:-0.debian.pool.ntp.org 1.debian.pool.ntp.org}"
PCS_PISTAR_GPSD_HOST="${PCS_PISTAR_GPSD_HOST:-10.42.0.1}"
PCS_PISTAR_GPSD_PORT="${PCS_PISTAR_GPSD_PORT:-2947}"

DHCPCD_CONF="/etc/dhcpcd.conf"
HOSTNAME_FILE="/etc/hostname"
HOSTS_FILE="/etc/hosts"
TIMESYNCD_DIR="/etc/systemd/timesyncd.conf.d"
TIMESYNCD_CONF="${TIMESYNCD_DIR}/50-pcs.conf"
YSFGATEWAY_CONF="/etc/ysfgateway"
MMDVMHOST_CONF="/etc/mmdvmhost"
MOBILEGPS_CONF="/etc/mobilegps"
FSTAB="/etc/fstab"
BOOT_CONFIG="/boot/config.txt"
RC_LOCAL="/etc/rc.local"
PISTAR_AP_DROPIN_DIR="/etc/systemd/system/pistar-ap.service.d"
PISTAR_AP_DROPIN="${PISTAR_AP_DROPIN_DIR}/50-pcs-wired-only.conf"
DSTAR_DROPIN_DIR="/etc/systemd/system/dstarrepeater.service.d"
DSTAR_DROPIN="${DSTAR_DROPIN_DIR}/50-pcs-mode-guard.conf"
HAVEGED_DROPIN_DIR="/etc/systemd/system/haveged.service.d"
HAVEGED_DROPIN="${HAVEGED_DROPIN_DIR}/50-pcs-arm-syscall.conf"
PCS_BLOCK_BEGIN="# BEGIN PCS HOTSPOT"
PCS_BLOCK_END="# END PCS HOTSPOT"
WIFI_BLOCK_BEGIN="# BEGIN PCS HOTSPOT WIFI"
WIFI_BLOCK_END="# END PCS HOTSPOT WIFI"
RC_WIFI_GUARD_BEGIN="# BEGIN PCS HOTSPOT WIFI BOOT GUARD"
RC_WIFI_GUARD_END="# END PCS HOTSPOT WIFI BOOT GUARD"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
TEMP_FILES=()
RESTORE_ROOT_RO=0
RESTORE_BOOT_RO=0

case "${PCS_PISTAR_DISABLE_WIFI}" in
    yes|no)
        ;;
    *)
        echo "ERROR: PCS_PISTAR_DISABLE_WIFI must be yes or no."
        exit 2
        ;;
esac

if [[ ! "${PCS_PISTAR_LINK_STABILITY_SECONDS}" =~ ^[0-9]+$ ]] || \
   [[ "${PCS_PISTAR_LINK_STABILITY_SECONDS}" -lt 5 ]] || \
   [[ "${PCS_PISTAR_LINK_STABILITY_SECONDS}" -gt 300 ]]; then
    echo "ERROR: PCS_PISTAR_LINK_STABILITY_SECONDS must be an integer from 5 through 300."
    exit 2
fi

pass() {
    echo "[PASS] $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    echo "[WARN] $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

make_temp_file() {
    local temp_file
    temp_file="$(mktemp)"
    TEMP_FILES+=("${temp_file}")
    echo "${temp_file}"
}

remount_root_read_only() {
    local attempt

    for attempt in $(seq 1 30); do
        sync
        if sudo mount -o remount,ro / 2>/dev/null; then
            return 0
        fi

        if [[ "${attempt}" -lt 30 ]]; then
            sleep 2
        fi
    done

    echo "ERROR: Pi-Star root filesystem could not be restored to read-only."
    echo "A package-maintenance job may still be using it. After that job finishes, run:"
    echo "  sync && sudo mount -o remount,ro /"
    return 1
}

remount_boot_read_only() {
    local attempt

    for attempt in $(seq 1 30); do
        sync
        if sudo mount -o remount,ro /boot 2>/dev/null; then
            return 0
        fi

        if [[ "${attempt}" -lt 30 ]]; then
            sleep 2
        fi
    done

    echo "ERROR: Pi-Star boot filesystem could not be restored to read-only."
    echo "After any package-maintenance job finishes, run:"
    echo "  sync && sudo mount -o remount,ro /boot"
    return 1
}

restore_root_mount() {
    if [[ "${MODE}" == "apply" ]]; then
        if [[ "${RESTORE_BOOT_RO}" -eq 1 ]]; then
            remount_boot_read_only || true
        fi
        if [[ "${RESTORE_ROOT_RO}" -eq 1 ]]; then
            remount_root_read_only || true
        fi
    fi
}

cleanup() {
    if [[ "${#TEMP_FILES[@]}" -gt 0 ]]; then
        rm -f "${TEMP_FILES[@]}"
    fi
    restore_root_mount
}

trap cleanup EXIT

require_file() {
    local path="$1"
    if [[ ! -f "${path}" ]]; then
        echo "ERROR: Required Pi-Star file not found: ${path}"
        exit 1
    fi
}

check_ini_value() {
    local file="$1"
    local section="$2"
    local key="$3"
    local expected="$4"
    local actual

    actual="$(python3 - "${file}" "${section}" "${key}" <<'PY'
import configparser
import sys

parser = configparser.ConfigParser(interpolation=None, strict=False)
parser.optionxform = str
parser.read(sys.argv[1])
print(parser.get(sys.argv[2], sys.argv[3], fallback=""))
PY
)"

    if [[ "${actual}" == "${expected}" ]]; then
        pass "${file} [${section}] ${key}=${expected}"
    else
        fail "${file} [${section}] ${key} is '${actual}', expected '${expected}'"
    fi
}

render_ini_section() {
    local source_file="$1"
    local section="$2"
    local output_file="$3"
    shift 3

    python3 - "${source_file}" "${section}" "${output_file}" "$@" <<'PY'
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
section = sys.argv[2]
output = Path(sys.argv[3])
updates = dict(item.split("=", 1) for item in sys.argv[4:])
lines = source.read_text().splitlines()

result = []
inside = False
found_section = False
seen = set()

def add_missing():
    for key, value in updates.items():
        if key not in seen:
            result.append(f"{key}={value}")

for line in lines:
    match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
    if match:
        if inside:
            add_missing()
        inside = match.group(1) == section
        if inside:
            found_section = True
            seen = set()
        result.append(line)
        continue

    if inside:
        key_match = re.match(r"^(\s*)([^#;\s][^=]*?)(\s*)=(.*)$", line)
        if key_match:
            key = key_match.group(2).strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
    result.append(line)

if inside:
    add_missing()

if not found_section:
    if result and result[-1] != "":
        result.append("")
    result.append(f"[{section}]")
    for key, value in updates.items():
        result.append(f"{key}={value}")

output.write_text("\n".join(result) + "\n")
PY
}

check_configuration() {
    local current_hostname
    local device_path
    local default_routes
    local interface_driver
    local interface_addresses
    local managed_block
    local managed_wifi_block
    local expected_block
    local expected_wifi_block
    local expected_rc_wifi_guard
    local expected_ap_dropin
    local actual_ap_dropin
    local expected_dstar_dropin
    local actual_dstar_dropin
    local expected_haveged_dropin
    local actual_haveged_dropin
    local cgroup_fstype
    local managed_rc_wifi_guard
    local root_options
    local boot_options
    local wifi_addresses

    echo
    echo "=== PCS Pi-Star Integration Check ==="
    echo

    current_hostname="$(hostname)"
    if [[ "${current_hostname}" == "${PCS_PISTAR_HOSTNAME}" ]]; then
        pass "Runtime hostname is ${PCS_PISTAR_HOSTNAME}"
    else
        fail "Runtime hostname is ${current_hostname}, expected ${PCS_PISTAR_HOSTNAME}"
    fi

    if [[ "$(tr -d '[:space:]' < "${HOSTNAME_FILE}")" == "${PCS_PISTAR_HOSTNAME}" ]]; then
        pass "${HOSTNAME_FILE} contains ${PCS_PISTAR_HOSTNAME}"
    else
        fail "${HOSTNAME_FILE} does not contain ${PCS_PISTAR_HOSTNAME}"
    fi

    if grep -Eq "^127\\.0\\.1\\.1[[:space:]]+${PCS_PISTAR_HOSTNAME}([[:space:]]|$)" "${HOSTS_FILE}"; then
        pass "${HOSTS_FILE} maps 127.0.1.1 to ${PCS_PISTAR_HOSTNAME}"
    else
        fail "${HOSTS_FILE} does not map 127.0.1.1 to ${PCS_PISTAR_HOSTNAME}"
    fi

    managed_block="$(awk -v begin="${PCS_BLOCK_BEGIN}" -v end="${PCS_BLOCK_END}" '
        $0 == begin { managed=1 }
        managed { print }
        $0 == end { managed=0 }
    ' "${DHCPCD_CONF}")"
    expected_block="$({
        printf '%s\n' "${PCS_BLOCK_BEGIN}"
        printf '%s\n' \
            "interface ${PCS_PISTAR_INTERFACE}" \
            "static ip_address=${PCS_PISTAR_ADDRESS}" \
            "static routers=${PCS_PISTAR_GATEWAY}" \
            "static domain_name_servers=${PCS_PISTAR_DNS}" \
            "${PCS_BLOCK_END}"
    })"

    if [[ "${managed_block}" == "${expected_block}" ]]; then
        pass "${DHCPCD_CONF} contains the managed PCS static-address block"
    else
        fail "${DHCPCD_CONF} does not contain the expected PCS static-address block"
    fi

    managed_wifi_block="$(awk -v begin="${WIFI_BLOCK_BEGIN}" -v end="${WIFI_BLOCK_END}" '
        $0 == begin { managed=1 }
        managed { print }
        $0 == end { managed=0 }
    ' "${BOOT_CONFIG}")"
    if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "yes" ]]; then
        expected_wifi_block="$(printf '%s\n' "${WIFI_BLOCK_BEGIN}" 'dtoverlay=disable-wifi' "${WIFI_BLOCK_END}")"
    else
        expected_wifi_block=""
    fi
    if [[ "${managed_wifi_block}" == "${expected_wifi_block}" ]]; then
        pass "${BOOT_CONFIG} has the expected PCS Wi-Fi policy"
    else
        fail "${BOOT_CONFIG} does not have the expected PCS Wi-Fi policy"
    fi

    managed_rc_wifi_guard="$(awk -v begin="${RC_WIFI_GUARD_BEGIN}" -v end="${RC_WIFI_GUARD_END}" '
        $0 == begin { managed=1 }
        managed { print }
        $0 == end { managed=0 }
    ' "${RC_LOCAL}")"
    expected_rc_wifi_guard=""
    expected_ap_dropin=""
    if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "yes" ]]; then
        expected_rc_wifi_guard="$(printf '%s\n' \
            "${RC_WIFI_GUARD_BEGIN}" \
            "if [ -d \"/sys/class/net/${PCS_PISTAR_WIFI_INTERFACE}\" ]; then" \
            "    /sbin/iwconfig ${PCS_PISTAR_WIFI_INTERFACE} power off" \
            'fi' \
            "${RC_WIFI_GUARD_END}")"
        expected_ap_dropin="$(printf '%s\n' '[Unit]' "ConditionPathExists=/sys/class/net/${PCS_PISTAR_WIFI_INTERFACE}")"
    fi
    if [[ "${managed_rc_wifi_guard}" == "${expected_rc_wifi_guard}" ]]; then
        pass "${RC_LOCAL} has the expected Wi-Fi boot guard"
    else
        fail "${RC_LOCAL} does not have the expected Wi-Fi boot guard"
    fi
    if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "no" ]] && \
       ! grep -Fqx "/sbin/iwconfig ${PCS_PISTAR_WIFI_INTERFACE} power off" "${RC_LOCAL}"; then
        fail "${RC_LOCAL} does not contain the native Wi-Fi power-management command"
    fi
    if [[ -f "${PISTAR_AP_DROPIN}" ]]; then
        actual_ap_dropin="$(cat "${PISTAR_AP_DROPIN}")"
    else
        actual_ap_dropin=""
    fi
    if [[ "${actual_ap_dropin}" == "${expected_ap_dropin}" ]]; then
        pass "Pi-Star AP service has the expected wired-only condition"
    else
        fail "Pi-Star AP service does not have the expected wired-only condition"
    fi

    expected_dstar_dropin="$(printf '%s\n' \
        '[Unit]' \
        'ConditionPathExists=/etc/dstar-radio.dstarrepeater' \
        'ConditionPathExists=!/etc/dstar-radio.mmdvmhost')"
    actual_dstar_dropin="$(cat "${DSTAR_DROPIN}" 2>/dev/null || true)"
    if [[ "${actual_dstar_dropin}" == "${expected_dstar_dropin}" ]]; then
        pass "D-Star service has the expected native-mode guard"
    else
        fail "D-Star service does not have the expected native-mode guard"
    fi

    expected_haveged_dropin="$(printf '%s\n' '[Service]' 'SystemCallFilter=uname')"
    actual_haveged_dropin="$(cat "${HAVEGED_DROPIN}" 2>/dev/null || true)"
    if [[ "${actual_haveged_dropin}" == "${expected_haveged_dropin}" ]]; then
        pass "haveged permits the ARM uname syscall"
    else
        fail "haveged is missing the ARM uname syscall allowance"
    fi

    cgroup_fstype="$(findmnt -no FSTYPE /sys/fs/cgroup 2>/dev/null || true)"
    if [[ "${cgroup_fstype}" == "cgroup2" ]]; then
        pass "Pi-Star uses the cgroup v2 filesystem"
        if awk 'NF >= 3 && $1 !~ /^#/ && $2 == "/sys/fs/cgroup" && $3 == "tmpfs" { found=1 } END { exit !found }' "${FSTAB}"; then
            fail "${FSTAB} still contains the obsolete cgroup tmpfs mount"
        else
            pass "${FSTAB} does not override the active cgroup filesystem"
        fi
    else
        warn "Pi-Star cgroup filesystem is '${cgroup_fstype:-unknown}', so the legacy fstab entry is preserved"
    fi

    if systemctl is-failed --quiet dstarrepeater.service; then
        fail "dstarrepeater.service remains failed"
    else
        pass "dstarrepeater.service is not failed"
    fi
    if systemctl is-active --quiet haveged.service; then
        pass "haveged.service is active"
    else
        fail "haveged.service is not active"
    fi
    if systemctl is-failed --quiet systemd-remount-fs.service; then
        fail "systemd-remount-fs.service remains failed"
    else
        pass "systemd-remount-fs.service is not failed"
    fi

    device_path="$(readlink -f "/sys/class/net/${PCS_PISTAR_INTERFACE}/device" 2>/dev/null || true)"
    interface_driver="$(basename "$(readlink -f "/sys/class/net/${PCS_PISTAR_INTERFACE}/device/driver" 2>/dev/null || true)")"
    if [[ "${device_path}" == *"/usb"* || "${device_path}" == *"/usb/"* ]]; then
        pass "${PCS_PISTAR_INTERFACE} is backed by a USB Ethernet device"
    else
        fail "${PCS_PISTAR_INTERFACE} is not confirmed as a USB Ethernet device"
    fi
    if [[ "${interface_driver}" == "${PCS_PISTAR_ETHERNET_DRIVER}" ]]; then
        pass "${PCS_PISTAR_INTERFACE} uses ${PCS_PISTAR_ETHERNET_DRIVER}"
    else
        fail "${PCS_PISTAR_INTERFACE} uses '${interface_driver:-unknown}', expected '${PCS_PISTAR_ETHERNET_DRIVER}'"
    fi
    if [[ "$(cat "/sys/class/net/${PCS_PISTAR_INTERFACE}/carrier" 2>/dev/null || true)" == "1" ]]; then
        pass "${PCS_PISTAR_INTERFACE} has Ethernet carrier"
    else
        fail "${PCS_PISTAR_INTERFACE} does not have Ethernet carrier"
    fi

    interface_addresses="$(ip -4 address show dev "${PCS_PISTAR_INTERFACE}" 2>/dev/null || true)"
    if grep -Fq "inet ${PCS_PISTAR_ADDRESS}" <<<"${interface_addresses}"; then
        pass "${PCS_PISTAR_INTERFACE} has ${PCS_PISTAR_ADDRESS}"
    else
        warn "${PCS_PISTAR_INTERFACE} does not yet have ${PCS_PISTAR_ADDRESS}; reboot after applying"
    fi

    default_routes="$(ip route show default 2>/dev/null || true)"
    if grep -Eq "^default via ${PCS_PISTAR_GATEWAY//./\\.} dev ${PCS_PISTAR_INTERFACE}([[:space:]]|$)" <<<"${default_routes}"; then
        pass "Default route uses ${PCS_PISTAR_GATEWAY} on ${PCS_PISTAR_INTERFACE}"
    else
        warn "Default route does not yet use ${PCS_PISTAR_GATEWAY} on ${PCS_PISTAR_INTERFACE}"
    fi

    if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "yes" ]]; then
        if ip link show dev "${PCS_PISTAR_WIFI_INTERFACE}" >/dev/null 2>&1; then
            fail "${PCS_PISTAR_WIFI_INTERFACE} still exists; reboot is required or Wi-Fi disablement failed"
        else
            pass "Onboard Wi-Fi hardware is disabled"
        fi
        wifi_addresses="$(ip -4 address show dev "${PCS_PISTAR_WIFI_INTERFACE}" 2>/dev/null || true)"
        if grep -q 'inet ' <<<"${wifi_addresses}"; then
            fail "${PCS_PISTAR_WIFI_INTERFACE} still has an IPv4 address"
        else
            pass "No IPv4 address is assigned to ${PCS_PISTAR_WIFI_INTERFACE}"
        fi
    fi

    root_options="$(findmnt -no OPTIONS / 2>/dev/null || true)"
    boot_options="$(findmnt -no OPTIONS /boot 2>/dev/null || true)"
    if [[ ",${root_options}," == *,ro,* ]]; then
        pass "Pi-Star root filesystem is read-only"
    else
        fail "Pi-Star root filesystem is not read-only"
    fi
    if [[ ",${boot_options}," == *,ro,* ]]; then
        pass "Pi-Star boot filesystem is read-only"
    else
        fail "Pi-Star boot filesystem is not read-only"
    fi

    if grep -Fqx "NTP=${PCS_PISTAR_NTP}" "${TIMESYNCD_CONF}" 2>/dev/null; then
        pass "Pi-Star prefers PCS NTP at ${PCS_PISTAR_NTP}"
    else
        fail "PCS NTP setting is missing from ${TIMESYNCD_CONF}"
    fi

    check_ini_value "${YSFGATEWAY_CONF}" "GPSD" "Enable" "1"
    check_ini_value "${YSFGATEWAY_CONF}" "GPSD" "Address" "${PCS_PISTAR_GPSD_HOST}"
    check_ini_value "${YSFGATEWAY_CONF}" "GPSD" "Port" "${PCS_PISTAR_GPSD_PORT}"
    check_ini_value "${MMDVMHOST_CONF}" "Mobile GPS" "Enable" "0"
    check_ini_value "${MOBILEGPS_CONF}" "Enabled" "Enabled" "0"

    if systemctl is-active --quiet mobilegps; then
        fail "mobilegps.service is active even though no local receiver is configured"
    else
        pass "mobilegps.service is inactive"
    fi

    if systemctl is-enabled --quiet mobilegps 2>/dev/null; then
        warn "mobilegps.service remains enabled; applying the script disables it"
    else
        pass "mobilegps.service is disabled"
    fi

    if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)" == "yes" ]]; then
        pass "Pi-Star system time is synchronized"
    else
        warn "Pi-Star system time is not synchronized yet"
    fi

    if ping -c 1 -W 2 "${PCS_PISTAR_GATEWAY}" >/dev/null 2>&1; then
        pass "PCS server responds at ${PCS_PISTAR_GATEWAY}"
    else
        warn "PCS server does not respond at ${PCS_PISTAR_GATEWAY}"
    fi

    if python3 - "${PCS_PISTAR_GPSD_HOST}" "${PCS_PISTAR_GPSD_PORT}" <<'PY'
import json
import socket
import sys

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=4) as client:
    client.sendall(b"?VERSION;\n")
    reply = client.recv(4096).decode("ascii", "replace")

if json.loads(reply.splitlines()[0]).get("class") != "VERSION":
    raise SystemExit(1)
PY
    then
        pass "GPSD VERSION response received from ${PCS_PISTAR_GPSD_HOST}:${PCS_PISTAR_GPSD_PORT}"
    else
        warn "GPSD did not answer at ${PCS_PISTAR_GPSD_HOST}:${PCS_PISTAR_GPSD_PORT}"
    fi

    if [[ "${FAIL_COUNT}" -eq 0 ]]; then
        echo
        echo "Pi-Star PCS integration check passed (${PASS_COUNT} pass, ${WARN_COUNT} warn)."
        return 0
    fi

    echo
    echo "Pi-Star PCS integration check failed (${PASS_COUNT} pass, ${FAIL_COUNT} fail, ${WARN_COUNT} warn)."
    return 1
}

validate_wired_preflight() {
    local device_path=""
    local interface_driver=""
    local carrier_changes_before=""
    local carrier_changes_after=""
    local second

    if [[ "${PCS_PISTAR_INTERFACE}" == "${PCS_PISTAR_WIFI_INTERFACE}" ]]; then
        echo "ERROR: PCS_PISTAR_INTERFACE must name the USB Ethernet interface, not ${PCS_PISTAR_WIFI_INTERFACE}."
        return 1
    fi
    if ! ip link show dev "${PCS_PISTAR_INTERFACE}" >/dev/null 2>&1; then
        echo "ERROR: wired interface ${PCS_PISTAR_INTERFACE} does not exist."
        return 1
    fi
    device_path="$(readlink -f "/sys/class/net/${PCS_PISTAR_INTERFACE}/device" 2>/dev/null || true)"
    interface_driver="$(basename "$(readlink -f "/sys/class/net/${PCS_PISTAR_INTERFACE}/device/driver" 2>/dev/null || true)")"
    if [[ "${device_path}" != *"/usb"* ]]; then
        echo "ERROR: ${PCS_PISTAR_INTERFACE} is not backed by a USB device (${device_path:-unknown})."
        return 1
    fi
    if [[ "${interface_driver}" != "${PCS_PISTAR_ETHERNET_DRIVER}" ]]; then
        echo "ERROR: ${PCS_PISTAR_INTERFACE} uses ${interface_driver:-unknown}; expected ${PCS_PISTAR_ETHERNET_DRIVER}."
        return 1
    fi
    carrier_changes_before="$(cat "/sys/class/net/${PCS_PISTAR_INTERFACE}/carrier_changes" 2>/dev/null || true)"
    echo "Verifying continuous wired carrier and PCS reachability for ${PCS_PISTAR_LINK_STABILITY_SECONDS} seconds..."
    for ((second = 1; second <= PCS_PISTAR_LINK_STABILITY_SECONDS; second++)); do
        if [[ "$(cat "/sys/class/net/${PCS_PISTAR_INTERFACE}/carrier" 2>/dev/null || true)" != "1" ]]; then
            echo "ERROR: ${PCS_PISTAR_INTERFACE} lost Ethernet carrier during the stability window."
            return 1
        fi
        if ! ping -I "${PCS_PISTAR_INTERFACE}" -c 1 -W 1 "${PCS_PISTAR_GATEWAY}" >/dev/null 2>&1; then
            echo "ERROR: ${PCS_PISTAR_INTERFACE} lost PCS reachability during the stability window."
            return 1
        fi
        if [[ "${second}" -lt "${PCS_PISTAR_LINK_STABILITY_SECONDS}" ]]; then
            sleep 1
        fi
    done
    carrier_changes_after="$(cat "/sys/class/net/${PCS_PISTAR_INTERFACE}/carrier_changes" 2>/dev/null || true)"
    if [[ -n "${carrier_changes_before}" && "${carrier_changes_after}" != "${carrier_changes_before}" ]]; then
        echo "ERROR: ${PCS_PISTAR_INTERFACE} carrier changed during the stability window."
        return 1
    fi

    echo "Verified ${PCS_PISTAR_INTERFACE}: USB ${PCS_PISTAR_ETHERNET_DRIVER}, stable carrier, PCS reachable."
}

if [[ ! -f /etc/pistar-release ]]; then
    echo "ERROR: /etc/pistar-release was not found; this does not appear to be Pi-Star."
    exit 1
fi

for required in python3 systemctl findmnt ip ping readlink basename; do
    if ! command -v "${required}" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: ${required}"
        exit 1
    fi
done

for required_file in \
    "${DHCPCD_CONF}" \
    "${HOSTNAME_FILE}" \
    "${HOSTS_FILE}" \
    "${FSTAB}" \
    "${BOOT_CONFIG}" \
    "${RC_LOCAL}" \
    "${YSFGATEWAY_CONF}" \
    "${MMDVMHOST_CONF}" \
    "${MOBILEGPS_CONF}" \
    "/lib/systemd/system/dstarrepeater.service" \
    "/lib/systemd/system/haveged.service"; do
    require_file "${required_file}"
done

if ! systemctl is-active --quiet dhcpcd; then
    echo "ERROR: dhcpcd is not active. This script supports the tested Pi-Star dhcpcd network stack."
    exit 1
fi

if [[ "${MODE}" == "check" ]]; then
    check_configuration
    exit $?
fi

validate_wired_preflight

echo
echo "=== PCS Pi-Star Integration Setup ==="
echo
echo "This manages PCS hostname, network, time, GPS, and narrow native-service compatibility guards."
echo "It preserves Wi-Fi credentials, callsigns, radio modes, and network accounts."
echo
echo "  Hostname:    ${PCS_PISTAR_HOSTNAME}"
echo "  Interface:   ${PCS_PISTAR_INTERFACE}"
echo "  USB driver:  ${PCS_PISTAR_ETHERNET_DRIVER}"
echo "  Link check:  ${PCS_PISTAR_LINK_STABILITY_SECONDS} continuous seconds"
echo "  Disable Wi-Fi: ${PCS_PISTAR_DISABLE_WIFI}"
echo "  Address:     ${PCS_PISTAR_ADDRESS}"
echo "  Gateway/DNS: ${PCS_PISTAR_GATEWAY}"
echo "  NTP:         ${PCS_PISTAR_NTP}"
echo "  GPSD:        ${PCS_PISTAR_GPSD_HOST}:${PCS_PISTAR_GPSD_PORT}"
echo

if [[ "${PCS_ASSUME_YES:-}" == "1" || "${PCS_PISTAR_CONFIRM:-}" == "yes" ]]; then
    answer="yes"
    echo "Apply this Pi-Star integration? [Y/N] yes"
else
    read -r -p "Apply this Pi-Star integration? [Y/N] " answer
fi

case "${answer}" in
    y|Y|yes|YES|Yes)
        ;;
    *)
        echo "Aborted."
        exit 0
        ;;
esac

root_options="$(findmnt -no OPTIONS /)"
if [[ ",${root_options}," == *,ro,* ]]; then
    sudo mount -o remount,rw /
    RESTORE_ROOT_RO=1
fi
boot_options="$(findmnt -no OPTIONS /boot)"
if [[ ",${boot_options}," == *,ro,* ]]; then
    sudo mount -o remount,rw /boot
    RESTORE_BOOT_RO=1
fi

backup_dir="/root/pcs-pistar-backups/$(date +%Y%m%d-%H%M%S)"
sudo mkdir -p "${backup_dir}"
for path in \
    "${DHCPCD_CONF}" \
    "${HOSTNAME_FILE}" \
    "${HOSTS_FILE}" \
    "${FSTAB}" \
    "${BOOT_CONFIG}" \
    "${RC_LOCAL}" \
    "${YSFGATEWAY_CONF}" \
    "${MMDVMHOST_CONF}" \
    "${MOBILEGPS_CONF}"; do
    sudo cp -a "${path}" "${backup_dir}/"
done
if [[ -f "${TIMESYNCD_CONF}" ]]; then
    sudo cp -a "${TIMESYNCD_CONF}" "${backup_dir}/50-pcs.conf"
fi
if [[ -f "${PISTAR_AP_DROPIN}" ]]; then
    sudo cp -a "${PISTAR_AP_DROPIN}" "${backup_dir}/50-pcs-wired-only.conf"
fi
if [[ -f "${DSTAR_DROPIN}" ]]; then
    sudo cp -a "${DSTAR_DROPIN}" "${backup_dir}/50-pcs-mode-guard.conf"
fi
if [[ -f "${HAVEGED_DROPIN}" ]]; then
    sudo cp -a "${HAVEGED_DROPIN}" "${backup_dir}/50-pcs-arm-syscall.conf"
fi

hostname_temp="$(make_temp_file)"
printf '%s\n' "${PCS_PISTAR_HOSTNAME}" > "${hostname_temp}"
sudo install -o root -g root -m 0644 "${hostname_temp}" "${HOSTNAME_FILE}"

hosts_temp="$(make_temp_file)"
awk -v hostname="${PCS_PISTAR_HOSTNAME}" '
    BEGIN { replaced=0 }
    /^127[.]0[.]1[.]1([[:space:]]|$)/ {
        if (!replaced) {
            print "127.0.1.1 " hostname
            replaced=1
        }
        next
    }
    { print }
    END {
        if (!replaced) {
            print "127.0.1.1 " hostname
        }
    }
' "${HOSTS_FILE}" > "${hosts_temp}"
sudo install -o root -g root -m 0644 "${hosts_temp}" "${HOSTS_FILE}"

fstab_temp="$(make_temp_file)"
cgroup_fstype="$(findmnt -no FSTYPE /sys/fs/cgroup 2>/dev/null || true)"
python3 - "${FSTAB}" "${fstab_temp}" "${cgroup_fstype}" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
cgroup_fstype = sys.argv[3]
rendered = []
for line in source.read_text().splitlines():
    fields = line.split()
    if (
        cgroup_fstype == "cgroup2"
        and len(fields) >= 3
        and not line.lstrip().startswith("#")
        and fields[1:3] == ["/sys/fs/cgroup", "tmpfs"]
    ):
        rendered.append(f"# PCS disabled legacy cgroup tmpfs: {line}")
    else:
        rendered.append(line)
destination.write_text("\n".join(rendered).rstrip() + "\n")
PY
sudo install -o root -g root -m 0644 "${fstab_temp}" "${FSTAB}"

dhcpcd_temp="$(make_temp_file)"
awk -v begin="${PCS_BLOCK_BEGIN}" -v end="${PCS_BLOCK_END}" '
    $0 == begin { managed=1; next }
    $0 == end { managed=0; next }
    !managed { print }
' "${DHCPCD_CONF}" > "${dhcpcd_temp}"
python3 - "${dhcpcd_temp}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(path.read_text().rstrip() + "\n")
PY
{
    printf '\n%s\n' "${PCS_BLOCK_BEGIN}"
    printf 'interface %s\n' "${PCS_PISTAR_INTERFACE}"
    printf 'static ip_address=%s\n' "${PCS_PISTAR_ADDRESS}"
    printf 'static routers=%s\n' "${PCS_PISTAR_GATEWAY}"
    printf 'static domain_name_servers=%s\n' "${PCS_PISTAR_DNS}"
    printf '%s\n' "${PCS_BLOCK_END}"
} >> "${dhcpcd_temp}"
sudo install -o root -g root -m 0664 "${dhcpcd_temp}" "${DHCPCD_CONF}"

boot_temp="$(make_temp_file)"
awk -v begin="${WIFI_BLOCK_BEGIN}" -v end="${WIFI_BLOCK_END}" '
    $0 == begin { managed=1; next }
    $0 == end { managed=0; next }
    !managed { print }
' "${BOOT_CONFIG}" > "${boot_temp}"
python3 - "${boot_temp}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(path.read_text().rstrip() + "\n")
PY
if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "yes" ]]; then
    {
        printf '\n%s\n' "${WIFI_BLOCK_BEGIN}"
        echo 'dtoverlay=disable-wifi'
        printf '%s\n' "${WIFI_BLOCK_END}"
    } >> "${boot_temp}"
fi
sudo install -o root -g root -m 0644 "${boot_temp}" "${BOOT_CONFIG}"

rc_local_temp="$(make_temp_file)"
python3 - \
    "${RC_LOCAL}" \
    "${rc_local_temp}" \
    "${PCS_PISTAR_DISABLE_WIFI}" \
    "${PCS_PISTAR_WIFI_INTERFACE}" \
    "${RC_WIFI_GUARD_BEGIN}" \
    "${RC_WIFI_GUARD_END}" <<'PY'
from pathlib import Path
import sys

source, destination, disable_wifi, wifi_interface, begin, end = sys.argv[1:]
original = Path(source).read_text().splitlines()
native = f"/sbin/iwconfig {wifi_interface} power off"
guard = [
    begin,
    f'if [ -d "/sys/class/net/{wifi_interface}" ]; then',
    f"    {native}",
    "fi",
    end,
]
replacement = guard if disable_wifi == "yes" else [native]
rendered = []
managed = False
replaced = False

for line in original:
    if line == begin:
        if managed or replaced:
            raise SystemExit("invalid or duplicate PCS Wi-Fi boot guard")
        managed = True
        replaced = True
        rendered.extend(replacement)
        continue
    if line == end:
        if not managed:
            raise SystemExit("unmatched PCS Wi-Fi boot guard end")
        managed = False
        continue
    if managed:
        continue
    if line.strip() == native:
        if replaced:
            raise SystemExit("duplicate native Wi-Fi power-management command")
        replaced = True
        rendered.extend(replacement)
        continue
    rendered.append(line)

if managed:
    raise SystemExit("unterminated PCS Wi-Fi boot guard")
if not replaced:
    raise SystemExit("native Wi-Fi power-management command was not found")
Path(destination).write_text("\n".join(rendered).rstrip() + "\n")
PY
sudo install -o root -g root -m 0755 "${rc_local_temp}" "${RC_LOCAL}"

if [[ "${PCS_PISTAR_DISABLE_WIFI}" == "yes" ]]; then
    pistar_ap_temp="$(make_temp_file)"
    printf '%s\n' '[Unit]' "ConditionPathExists=/sys/class/net/${PCS_PISTAR_WIFI_INTERFACE}" > "${pistar_ap_temp}"
    sudo mkdir -p "${PISTAR_AP_DROPIN_DIR}"
    sudo install -o root -g root -m 0644 "${pistar_ap_temp}" "${PISTAR_AP_DROPIN}"
else
    sudo rm -f "${PISTAR_AP_DROPIN}"
    sudo rmdir "${PISTAR_AP_DROPIN_DIR}" 2>/dev/null || true
fi

dstar_temp="$(make_temp_file)"
printf '%s\n' \
    '[Unit]' \
    'ConditionPathExists=/etc/dstar-radio.dstarrepeater' \
    'ConditionPathExists=!/etc/dstar-radio.mmdvmhost' > "${dstar_temp}"
sudo mkdir -p "${DSTAR_DROPIN_DIR}"
sudo install -o root -g root -m 0644 "${dstar_temp}" "${DSTAR_DROPIN}"

haveged_temp="$(make_temp_file)"
printf '%s\n' '[Service]' 'SystemCallFilter=uname' > "${haveged_temp}"
sudo mkdir -p "${HAVEGED_DROPIN_DIR}"
sudo install -o root -g root -m 0644 "${haveged_temp}" "${HAVEGED_DROPIN}"

sudo systemctl daemon-reload
sudo systemctl reset-failed dstarrepeater.service systemd-remount-fs.service >/dev/null 2>&1 || true
sudo systemctl restart haveged.service

timesync_temp="$(make_temp_file)"
{
    echo "[Time]"
    echo "NTP=${PCS_PISTAR_NTP}"
    echo "FallbackNTP=${PCS_PISTAR_FALLBACK_NTP}"
} > "${timesync_temp}"
sudo mkdir -p "${TIMESYNCD_DIR}"
sudo install -o root -g root -m 0644 "${timesync_temp}" "${TIMESYNCD_CONF}"

ysf_temp="$(make_temp_file)"
render_ini_section "${YSFGATEWAY_CONF}" "GPSD" "${ysf_temp}" \
    "Enable=1" \
    "Address=${PCS_PISTAR_GPSD_HOST}" \
    "Port=${PCS_PISTAR_GPSD_PORT}"
sudo install -o root -g root -m 0644 "${ysf_temp}" "${YSFGATEWAY_CONF}"

mmdvm_temp="$(make_temp_file)"
render_ini_section "${MMDVMHOST_CONF}" "Mobile GPS" "${mmdvm_temp}" "Enable=0"
sudo install -o root -g root -m 0644 "${mmdvm_temp}" "${MMDVMHOST_CONF}"

mobilegps_temp="$(make_temp_file)"
render_ini_section "${MOBILEGPS_CONF}" "Enabled" "${mobilegps_temp}" "Enabled=0"
sudo install -o www-data -g www-data -m 0644 "${mobilegps_temp}" "${MOBILEGPS_CONF}"

sudo systemctl disable --now mobilegps.service >/dev/null 2>&1 || true
sudo hostname "${PCS_PISTAR_HOSTNAME}"
sync

if [[ "${RESTORE_BOOT_RO}" -eq 1 ]]; then
    if remount_boot_read_only; then
        RESTORE_BOOT_RO=0
    else
        RESTORE_BOOT_RO=0
        exit 1
    fi
fi

if [[ "${RESTORE_ROOT_RO}" -eq 1 ]]; then
    if remount_root_read_only; then
        RESTORE_ROOT_RO=0
    else
        RESTORE_ROOT_RO=0
        exit 1
    fi
fi

sudo systemctl restart systemd-timesyncd.service
sudo systemctl try-restart mmdvmhost.service ysfgateway.service

echo
echo "PCS Pi-Star integration applied."
echo "Backup created on Pi-Star at: ${backup_dir}"
echo
echo "Reboot Pi-Star so the wired address and Wi-Fi policy are applied cleanly:"
echo "  sudo reboot"
echo
echo "After reboot, verify from this script's directory:"
echo "  ./setup-pistar-pcs.sh --check"
