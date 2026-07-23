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
PCS_PISTAR_INTERFACE="${PCS_PISTAR_INTERFACE:-wlan0}"
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
PCS_BLOCK_BEGIN="# BEGIN PCS HOTSPOT"
PCS_BLOCK_END="# END PCS HOTSPOT"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
TEMP_FILES=()
RESTORE_ROOT_RO=0

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

restore_root_mount() {
    if [[ "${MODE}" == "apply" && "${RESTORE_ROOT_RO}" -eq 1 ]]; then
        remount_root_read_only || true
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
    local managed_block
    local expected_block

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
    expected_block="$(
        printf '%s\n' \
            "${PCS_BLOCK_BEGIN}" \
            "interface ${PCS_PISTAR_INTERFACE}" \
            "static ip_address=${PCS_PISTAR_ADDRESS}" \
            "static routers=${PCS_PISTAR_GATEWAY}" \
            "static domain_name_servers=${PCS_PISTAR_DNS}" \
            "${PCS_BLOCK_END}"
    )"

    if [[ "${managed_block}" == "${expected_block}" ]]; then
        pass "${DHCPCD_CONF} contains the managed PCS static-address block"
    else
        fail "${DHCPCD_CONF} does not contain the expected PCS static-address block"
    fi

    if ip -4 address show dev "${PCS_PISTAR_INTERFACE}" 2>/dev/null | grep -Fq "inet ${PCS_PISTAR_ADDRESS}"; then
        pass "${PCS_PISTAR_INTERFACE} has ${PCS_PISTAR_ADDRESS}"
    else
        warn "${PCS_PISTAR_INTERFACE} does not yet have ${PCS_PISTAR_ADDRESS}; reboot after applying"
    fi

    if ip route show default 2>/dev/null | grep -Eq "^default via ${PCS_PISTAR_GATEWAY//./\\.} dev ${PCS_PISTAR_INTERFACE}([[:space:]]|$)"; then
        pass "Default route uses ${PCS_PISTAR_GATEWAY} on ${PCS_PISTAR_INTERFACE}"
    else
        warn "Default route does not yet use ${PCS_PISTAR_GATEWAY} on ${PCS_PISTAR_INTERFACE}"
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

    if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes; then
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

if [[ ! -f /etc/pistar-release ]]; then
    echo "ERROR: /etc/pistar-release was not found; this does not appear to be Pi-Star."
    exit 1
fi

for required in python3 systemctl findmnt ip; do
    if ! command -v "${required}" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: ${required}"
        exit 1
    fi
done

for required_file in \
    "${DHCPCD_CONF}" \
    "${HOSTNAME_FILE}" \
    "${HOSTS_FILE}" \
    "${YSFGATEWAY_CONF}" \
    "${MMDVMHOST_CONF}" \
    "${MOBILEGPS_CONF}"; do
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

echo
echo "=== PCS Pi-Star Integration Setup ==="
echo
echo "This manages only the PCS hostname, network, time, and GPS integration."
echo "It does not modify Wi-Fi credentials, callsigns, radio modes, or network accounts."
echo
echo "  Hostname:    ${PCS_PISTAR_HOSTNAME}"
echo "  Interface:   ${PCS_PISTAR_INTERFACE}"
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

backup_dir="/root/pcs-pistar-backups/$(date +%Y%m%d-%H%M%S)"
sudo mkdir -p "${backup_dir}"
for path in \
    "${DHCPCD_CONF}" \
    "${HOSTNAME_FILE}" \
    "${HOSTS_FILE}" \
    "${YSFGATEWAY_CONF}" \
    "${MMDVMHOST_CONF}" \
    "${MOBILEGPS_CONF}"; do
    sudo cp -a "${path}" "${backup_dir}/"
done
if [[ -f "${TIMESYNCD_CONF}" ]]; then
    sudo cp -a "${TIMESYNCD_CONF}" "${backup_dir}/50-pcs.conf"
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
echo "Reboot Pi-Star so the static address is applied cleanly:"
echo "  sudo reboot"
echo
echo "After reboot, verify from this script's directory:"
echo "  ./setup-pistar-pcs.sh --check"
