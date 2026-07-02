#!/usr/bin/env bash

set -u

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0

PCS_ETH_IFACE="eth0"
PCS_WIFI_IFACE="wlan0"
PCS_ETH_ADDR="10.42.0.1/24"
PCS_NTP_NET="10.42.0.0/24"

PCS_SAMBA_SHARE="PCS-Share"
PCS_SAMBA_PATH="/mnt/pcs-usb/PCS-Share"

PCS_BACKUP_SHARE="PCS-Backup"
PCS_BACKUP_PATH="/srv/pcs-share-backup"

PCS_CONTROL_SERVICE="pcs-control-panel.service"
PCS_CONTROL_PORT="8080"
PCS_CONTROL_URL="http://127.0.0.1:8080"


echo
echo "=== PCS Pi-Side Self Test ==="
echo

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

skip() {
    echo "[SKIP] $1"
    SKIP_COUNT=$((SKIP_COUNT + 1))
}

section() {
    echo
    echo "--- $1 ---"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

service_active() {
    systemctl is-active --quiet "$1"
}

service_enabled() {
    systemctl is-enabled --quiet "$1" 2>/dev/null
}

port_listening_tcp() {
    local port="$1"
    ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\]:)${port}$"
}

port_listening_udp() {
    local port="$1"
    ss -H -lun 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\]:)${port}$"
}

section "System"

if [[ "$(hostname)" == "pcs-pi" ]]; then
    pass "Hostname is pcs-pi"
else
    fail "Hostname is not pcs-pi: $(hostname)"
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo "Detected OS: ${PRETTY_NAME:-unknown}"
    pass "OS release file exists"
else
    fail "/etc/os-release missing"
fi

if [[ -f /proc/device-tree/model ]]; then
    echo -n "Hardware: "
    tr -d '\0' < /proc/device-tree/model
    echo
    pass "Hardware model detected"
else
    fail "Hardware model not detected"
fi

section "Git Repository"

if command_exists git && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [[ -z "$(git status --porcelain)" ]]; then
        pass "Git working tree is clean"
    else
        warn "Git working tree has uncommitted changes"
        git status --short
    fi

    CURRENT_COMMIT="$(git log --oneline -1 2>/dev/null || true)"
    echo "Current commit: ${CURRENT_COMMIT}"
else
    skip "Not running inside a Git repository"
fi

section "RTC / Time"

if [[ -e /dev/rtc0 ]]; then
    pass "/dev/rtc0 exists"
else
    fail "/dev/rtc0 missing"
fi

if timedatectl | grep -q "RTC in local TZ: no"; then
    pass "RTC is configured for UTC, not local time"
else
    fail "RTC local timezone setting is not expected"
fi

if timedatectl | grep -q "NTP service: active"; then
    pass "NTP service is active"
else
    fail "NTP service is not active"
fi

if timedatectl | grep -q "System clock synchronized: yes"; then
    pass "System clock is synchronized"
else
    fail "System clock is not synchronized"
fi

if service_active chrony; then
    pass "chrony service is active"
else
    fail "chrony service is not active"
fi

if service_enabled chrony; then
    pass "chrony service is enabled"
else
    fail "chrony service is not enabled"
fi

if command_exists chronyc; then
    if chronyc tracking >/tmp/pcs-chronyc-tracking.txt 2>/dev/null; then
        pass "chronyc tracking works"
        grep -E "Stratum|Reference ID|Leap status|System time" /tmp/pcs-chronyc-tracking.txt || true
    else
        fail "chronyc tracking failed"
    fi
else
    fail "chronyc command missing"
fi

section "Chrony LAN NTP"

if grep -q "allow ${PCS_NTP_NET}" /etc/chrony/chrony.conf 2>/dev/null; then
    pass "Chrony allows PCS LAN NTP clients on ${PCS_NTP_NET}"
else
    fail "Chrony does not allow ${PCS_NTP_NET}"
fi

if grep -qE "^[[:space:]]*rtcsync[[:space:]]*$" /etc/chrony/chrony.conf 2>/dev/null; then
    pass "Chrony rtcsync is enabled"
else
    fail "Chrony rtcsync not found"
fi

if grep -qE "^[[:space:]]*local stratum 10[[:space:]]*$" /etc/chrony/chrony.conf 2>/dev/null; then
    pass "Chrony local fallback is enabled"
else
    fail "Chrony local fallback not found"
fi

if port_listening_udp 123; then
    pass "UDP port 123 appears to be listening"
else
    warn "UDP port 123 not visible in ss output; Windows NTP test may still prove service works"
fi

section "Network"

if command_exists nmcli; then
    nmcli device status
    echo

    if nmcli device status | grep -qE "^${PCS_WIFI_IFACE}[[:space:]]+wifi[[:space:]]+connected"; then
        pass "${PCS_WIFI_IFACE} is connected"
    else
        fail "${PCS_WIFI_IFACE} is not connected"
    fi

    if nmcli device status | grep -qE "^${PCS_ETH_IFACE}[[:space:]]+ethernet[[:space:]]+connected[[:space:]]+pcs-router-wan-share"; then
        pass "${PCS_ETH_IFACE} is connected using pcs-router-wan-share"
    else
        fail "${PCS_ETH_IFACE} is not connected using pcs-router-wan-share"
    fi
else
    fail "nmcli command missing"
fi

if ip -4 addr show dev "${PCS_ETH_IFACE}" | grep -q "${PCS_ETH_ADDR}"; then
    pass "${PCS_ETH_IFACE} has ${PCS_ETH_ADDR}"
else
    fail "${PCS_ETH_IFACE} does not have ${PCS_ETH_ADDR}"
fi

if ip route | grep -q "default .* ${PCS_WIFI_IFACE}"; then
    pass "Default route uses ${PCS_WIFI_IFACE}"
else
    fail "Default route does not appear to use ${PCS_WIFI_IFACE}"
fi

if ip route | grep -q "10.42.0.0/24 dev ${PCS_ETH_IFACE}"; then
    pass "PCS router-side route exists on ${PCS_ETH_IFACE}"
else
    fail "PCS router-side route missing on ${PCS_ETH_IFACE}"
fi

ROUTER_NEIGHBORS="$(ip neigh show dev "${PCS_ETH_IFACE}" | grep -E '^10\.42\.0\.' || true)"
if [[ -n "${ROUTER_NEIGHBORS}" ]]; then
    pass "At least one router-side neighbor exists on ${PCS_ETH_IFACE}"
    echo "${ROUTER_NEIGHBORS}"
else
    skip "No router-side neighbor currently visible on ${PCS_ETH_IFACE}"
fi

section "Internet / DNS"

if ping -c 2 -W 3 8.8.8.8 >/dev/null 2>&1; then
    pass "Internet IP ping works: 8.8.8.8"
else
    fail "Internet IP ping failed: 8.8.8.8"
fi

if ping -c 2 -W 5 google.com >/dev/null 2>&1; then
    pass "DNS and internet hostname ping work: google.com"
else
    fail "DNS or hostname ping failed: google.com"
fi

section "Samba"

if service_active smbd; then
    pass "smbd service is active"
else
    fail "smbd service is not active"
fi

if service_enabled smbd; then
    pass "smbd service is enabled"
else
    fail "smbd service is not enabled"
fi

if [[ -d "${PCS_SAMBA_PATH}" ]]; then
    pass "Samba primary share path exists: ${PCS_SAMBA_PATH}"
else
    fail "Samba primary share path missing: ${PCS_SAMBA_PATH}"
fi

if testparm -s 2>/dev/null | grep -q "^\[${PCS_SAMBA_SHARE}\]"; then
    pass "Samba config contains [${PCS_SAMBA_SHARE}]"
else
    fail "Samba config does not contain [${PCS_SAMBA_SHARE}]"
fi

if [[ -d "${PCS_BACKUP_PATH}" ]]; then
    pass "Samba backup share path exists: ${PCS_BACKUP_PATH}"
else
    fail "Samba backup share path missing: ${PCS_BACKUP_PATH}"
fi

if testparm -s 2>/dev/null | grep -q "^\[${PCS_BACKUP_SHARE}\]"; then
    pass "Samba config contains [${PCS_BACKUP_SHARE}]"
else
    fail "Samba config does not contain [${PCS_BACKUP_SHARE}]"
fi

if [[ -f "${PCS_BACKUP_PATH}/LAST_SYNC.txt" ]]; then
    pass "Backup share has LAST_SYNC.txt"
else
    warn "Backup share does not have LAST_SYNC.txt yet"
fi

if port_listening_tcp 445; then
    pass "TCP port 445 appears to be listening"
else
    warn "TCP port 445 not visible in ss output; Windows Samba test may still prove service works"
fi

section "Cockpit"

if service_active cockpit.socket; then
    pass "cockpit.socket is active"
else
    fail "cockpit.socket is not active"
fi

if service_enabled cockpit.socket; then
    pass "cockpit.socket is enabled"
else
    fail "cockpit.socket is not enabled"
fi

if port_listening_tcp 9090; then
    pass "TCP port 9090 appears to be listening"
else
    warn "TCP port 9090 not visible in ss output; cockpit.socket may still activate on demand"
fi

section "PCS Control Panel"

if service_active "${PCS_CONTROL_SERVICE}"; then
    pass "${PCS_CONTROL_SERVICE} is active"
else
    fail "${PCS_CONTROL_SERVICE} is not active"
fi

if service_enabled "${PCS_CONTROL_SERVICE}"; then
    pass "${PCS_CONTROL_SERVICE} is enabled"
else
    fail "${PCS_CONTROL_SERVICE} is not enabled"
fi

if port_listening_tcp "${PCS_CONTROL_PORT}"; then
    pass "TCP port ${PCS_CONTROL_PORT} appears to be listening"
else
    fail "TCP port ${PCS_CONTROL_PORT} does not appear to be listening"
fi

if command_exists curl; then
    if curl -fsS --max-time 5 "${PCS_CONTROL_URL}" >/dev/null 2>&1; then
        pass "PCS Control Panel HTTP check works at ${PCS_CONTROL_URL}"
    else
        fail "PCS Control Panel HTTP check failed at ${PCS_CONTROL_URL}"
    fi
else
    skip "curl not found; skipping PCS Control Panel HTTP check"
fi

section "Raspberry Pi Connect"

if command_exists rpi-connect; then
    CONNECT_DOCTOR_OUTPUT="$(rpi-connect doctor 2>&1 || true)"
    echo "${CONNECT_DOCTOR_OUTPUT}"

    if echo "${CONNECT_DOCTOR_OUTPUT}" | grep -q "Wayland compositor available"; then
        pass "Raspberry Pi Connect sees Wayland compositor"
    else
        fail "Raspberry Pi Connect does not see Wayland compositor"
    fi

    if echo "${CONNECT_DOCTOR_OUTPUT}" | grep -q "Screen sharing services enabled and active"; then
        pass "Raspberry Pi Connect screen sharing services are active"
    else
        fail "Raspberry Pi Connect screen sharing services are not active"
    fi
else
    skip "rpi-connect command not found"
fi

section "Modem / GPS Placeholders"

if service_active ModemManager; then
    pass "ModemManager is active"
else
    fail "ModemManager is not active"
fi

if service_enabled ModemManager; then
    pass "ModemManager is enabled"
else
    fail "ModemManager is not enabled"
fi

if command_exists mmcli; then
    MODEM_LIST="$(mmcli -L 2>&1 || true)"
    echo "${MODEM_LIST}"

    if echo "${MODEM_LIST}" | grep -q "No modems were found"; then
        pass "No modem present yet, as expected before WWAN adapter arrives"
    elif echo "${MODEM_LIST}" | grep -q "/Modem/"; then
        pass "ModemManager detected a modem"
    else
        fail "Unexpected ModemManager output"
    fi
else
    fail "mmcli command missing"
fi

if service_active gpsd; then
    skip "gpsd is active; GPS hardware/config may be under test"
else
    pass "gpsd inactive, expected before GPS/GNSS setup"
fi

section "Summary"

echo "Pass: ${PASS_COUNT}"
echo "Warn: ${WARN_COUNT}"
echo "Fail: ${FAIL_COUNT}"
echo "Skip: ${SKIP_COUNT}"

if [[ "${FAIL_COUNT}" -eq 0 ]]; then
    echo
    echo "PCS Pi-side self-test PASSED."
    if [[ "${WARN_COUNT}" -gt 0 ]]; then
        echo "Warnings were present; review them, but no hard failures occurred."
    fi
    exit 0
else
    echo
    echo "PCS Pi-side self-test FAILED."
    exit 1
fi
