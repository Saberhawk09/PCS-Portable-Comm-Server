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
PCS_OPENWRT_AP_ADDR="10.42.0.2"

PCS_SAMBA_SHARE="PCS-Share"
PCS_USB_MOUNT="/mnt/pcs-usb"
PCS_SAMBA_PATH="/mnt/pcs-usb/PCS-Share"

PCS_BACKUP_SHARE="PCS-Backup"
PCS_BACKUP_PATH="/srv/pcs-share-backup"

PCS_CONTROL_SERVICE="pcs-control-panel.service"
PCS_CONTROL_PORT="8080"
PCS_CONTROL_URL="http://127.0.0.1:8080"
PCS_DASHBOARD_REDIRECT_SERVICE="pcs-dashboard-redirect.service"
PCS_DASHBOARD_REDIRECT_PORT="80"
PCS_DASHBOARD_REDIRECT_HEALTH_URL="http://127.0.0.1/health"

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

PCS_CELLULAR_PROFILE_DEFAULT="pcs-cellular-profile"
PCS_CELLULAR_PROFILE_LEGACY="pcs-cellular-tmobile"
PCS_CELLULAR_PROFILE="${PCS_CELLULAR_PROFILE:-${PCS_CELLULAR_PROFILE_DEFAULT}}"

TMP_FILES=()

pcs_cellular_profile_name() {
    local configured="${PCS_CELLULAR_PROFILE:-${PCS_CELLULAR_PROFILE_DEFAULT}}"

    if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${configured}"; then
        echo "${configured}"
        return 0
    fi

    if [[ "${configured}" != "${PCS_CELLULAR_PROFILE_LEGACY}" ]] \
        && nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${PCS_CELLULAR_PROFILE_LEGACY}"; then
        echo "${PCS_CELLULAR_PROFILE_LEGACY}"
        return 0
    fi

    echo "${configured}"
}

make_temp_file() {
    local tmp_file
    tmp_file="$(mktemp)"
    TMP_FILES+=("${tmp_file}")
    echo "${tmp_file}"
}

cleanup_temp_files() {
    if [[ "${#TMP_FILES[@]}" -gt 0 ]]; then
        rm -f "${TMP_FILES[@]}"
    fi
}

trap cleanup_temp_files EXIT


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
    CHRONY_TRACKING_FILE="$(make_temp_file)"
    if chronyc tracking >"${CHRONY_TRACKING_FILE}" 2>/dev/null; then
        pass "chronyc tracking works"
        grep -E "Stratum|Reference ID|Leap status|System time" "${CHRONY_TRACKING_FILE}" || true
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

nmcli device status || true
echo

WIFI_CONNECTED=0
CELLULAR_CONNECTED=0

if nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v dev="${PCS_WIFI_IFACE}" '$1 == dev && $2 == "connected" { found=1 } END { exit !found }'; then
    WIFI_CONNECTED=1
    pass "Wi-Fi uplink ${PCS_WIFI_IFACE} is connected"
else
    echo "[INFO] Wi-Fi uplink ${PCS_WIFI_IFACE} is disconnected"
fi

if nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | awk -F: '$2 == "gsm" && $3 == "connected" { found=1 } END { exit !found }'; then
    CELLULAR_CONNECTED=1
    pass "Cellular/WWAN uplink is connected"
else
    echo "[INFO] Cellular/WWAN uplink is disconnected"
fi

if [[ "${WIFI_CONNECTED}" -eq 1 || "${CELLULAR_CONNECTED}" -eq 1 ]]; then
    pass "At least one internet uplink is connected"
else
    warn "No internet uplink is connected; PCS offline LAN services can still be healthy"
fi

if nmcli -t -f DEVICE,STATE,CONNECTION device status 2>/dev/null | awk -F: -v dev="${PCS_ETH_IFACE}" '$1 == dev && $2 == "connected" && $3 == "pcs-router-wan-share" { found=1 } END { exit !found }'; then
    pass "${PCS_ETH_IFACE} is connected using pcs-router-wan-share"
else
    fail "${PCS_ETH_IFACE} is not connected using pcs-router-wan-share"
fi

if ip -4 addr show "${PCS_ETH_IFACE}" 2>/dev/null | grep -q "${PCS_ETH_ADDR}"; then
    pass "${PCS_ETH_IFACE} has ${PCS_ETH_ADDR}"
else
    fail "${PCS_ETH_IFACE} does not have ${PCS_ETH_ADDR}"
fi

DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"

case "${DEFAULT_IFACE}" in
    "${PCS_WIFI_IFACE}")
        pass "Default route uses Wi-Fi uplink ${PCS_WIFI_IFACE}"
        ;;
    wwan0|cdc-wdm0|ppp0)
        pass "Default route uses cellular uplink ${DEFAULT_IFACE}"
        ;;
    "")
        warn "No default route is present; expected when PCS is running as offline LAN only"
        ;;
    *)
        warn "Default route uses unexpected interface: ${DEFAULT_IFACE}"
        ;;
esac

if ip route show 10.42.0.0/24 2>/dev/null | grep -q "dev ${PCS_ETH_IFACE}"; then
    pass "PCS LAN route exists on ${PCS_ETH_IFACE}"
else
    fail "PCS LAN route missing on ${PCS_ETH_IFACE}"
fi

if ping -c 1 -W 2 "${PCS_OPENWRT_AP_ADDR}" >/dev/null 2>&1; then
    pass "OpenWrt AP responds at ${PCS_OPENWRT_AP_ADDR}"
else
    fail "OpenWrt AP does not respond at ${PCS_OPENWRT_AP_ADDR}"
fi

ROUTER_NEIGHBORS="$(ip neigh show dev "${PCS_ETH_IFACE}" 2>/dev/null | awk '$1 ~ /^10\.42\.0\./ && $NF != "FAILED" && $NF != "INCOMPLETE"' || true)"
CLIENT_NEIGHBORS=""
if [[ -n "${ROUTER_NEIGHBORS}" ]]; then
    CLIENT_NEIGHBORS="$(printf '%s\n' "${ROUTER_NEIGHBORS}" | grep -Ev "^${PCS_OPENWRT_AP_ADDR//./\\.}[[:space:]]" || true)"
fi

if [[ -n "${ROUTER_NEIGHBORS}" ]]; then
    pass "At least one PCS LAN neighbor exists on ${PCS_ETH_IFACE}"
    echo "${ROUTER_NEIGHBORS}"
else
    warn "No PCS LAN neighbors found on ${PCS_ETH_IFACE}"
fi

if [[ -n "${CLIENT_NEIGHBORS}" ]]; then
    pass "Additional PCS client neighbors are visible on ${PCS_ETH_IFACE}"
    echo "${CLIENT_NEIGHBORS}"
else
    echo "[INFO] No additional PCS client neighbors are visible on ${PCS_ETH_IFACE}"
fi

section "Internet / DNS"

if [[ "${WIFI_CONNECTED}" -eq 0 && "${CELLULAR_CONNECTED}" -eq 0 && -z "${DEFAULT_IFACE}" ]]; then
    skip "No connected/default uplink; skipping internet and DNS ping checks"
else
    if ping -c 2 -W 3 8.8.8.8 >/dev/null 2>&1; then
        pass "Internet IP ping works: 8.8.8.8"
    else
        warn "Internet IP ping failed: 8.8.8.8"
    fi

    if ping -c 2 -W 5 google.com >/dev/null 2>&1; then
        pass "DNS and internet hostname ping work: google.com"
    else
        warn "DNS or hostname ping failed: google.com"
    fi
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
elif ! findmnt "${PCS_USB_MOUNT}" >/dev/null 2>&1; then
    fail "Samba primary share path missing because USB storage is not mounted at ${PCS_USB_MOUNT}"
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
    PCS_CONTROL_HTML="$(mktemp)"
    if curl -fsS --max-time 20 "${PCS_CONTROL_URL}" -o "${PCS_CONTROL_HTML}" 2>/dev/null \
        && grep -q "PCS Control Panel" "${PCS_CONTROL_HTML}"; then
        pass "PCS Control Panel HTTP check works at ${PCS_CONTROL_URL}"
    else
        fail "PCS Control Panel HTTP check failed at ${PCS_CONTROL_URL}"
    fi
    rm -f "${PCS_CONTROL_HTML}"
else
    skip "curl not found; skipping PCS Control Panel HTTP check"
fi

section "PCS Dashboard Redirect"

if service_active "${PCS_DASHBOARD_REDIRECT_SERVICE}"; then
    pass "${PCS_DASHBOARD_REDIRECT_SERVICE} is active"
else
    fail "${PCS_DASHBOARD_REDIRECT_SERVICE} is not active"
fi

if service_enabled "${PCS_DASHBOARD_REDIRECT_SERVICE}"; then
    pass "${PCS_DASHBOARD_REDIRECT_SERVICE} is enabled"
else
    fail "${PCS_DASHBOARD_REDIRECT_SERVICE} is not enabled"
fi

if port_listening_tcp "${PCS_DASHBOARD_REDIRECT_PORT}"; then
    pass "TCP port ${PCS_DASHBOARD_REDIRECT_PORT} appears to be listening"
else
    fail "TCP port ${PCS_DASHBOARD_REDIRECT_PORT} does not appear to be listening"
fi

if command_exists curl; then
    if curl -fsS --max-time 5 "${PCS_DASHBOARD_REDIRECT_HEALTH_URL}" >/dev/null 2>&1; then
        pass "PCS dashboard redirect health check works"
    else
        fail "PCS dashboard redirect health check failed"
    fi
else
    skip "curl not found; skipping dashboard redirect HTTP check"
fi

section "Raspberry Pi Connect"

if command_exists rpi-connect; then
    CONNECT_DOCTOR_OUTPUT="$(rpi-connect doctor 2>&1 || true)"
    CONNECT_STATUS_OUTPUT="$(rpi-connect status 2>&1 || true)"
    echo "${CONNECT_DOCTOR_OUTPUT}"

    if printf '%s\n' "${CONNECT_DOCTOR_OUTPUT}" | grep -Eq "Wayland compositor available$"; then
        pass "Raspberry Pi Connect sees Wayland compositor"
    else
        fail "Raspberry Pi Connect does not see Wayland compositor"
    fi

    if printf '%s\n' "${CONNECT_STATUS_OUTPUT}" | grep -Eiq "not running|not signed in|signed out|not connected|no account|run rpi-connect on|run rpi-connect signin"; then
        pass "Raspberry Pi Connect account is not connected; screen sharing is not expected"
    elif printf '%s\n' "${CONNECT_DOCTOR_OUTPUT}" | grep -Eq "Screen sharing services enabled and active$"; then
        pass "Raspberry Pi Connect screen sharing services are active"
    else
        fail "Raspberry Pi Connect screen sharing services are not active"
    fi
else
    skip "rpi-connect command not found"
fi

section "WWAN / GPS"

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

    if echo "${MODEM_LIST}" | grep -q "/Modem/"; then
        pass "ModemManager detected a WWAN modem"

        MODEM_NUM="$(echo "${MODEM_LIST}" | sed -n 's#.*Modem/\([0-9]\+\).*#\1#p' | head -n 1)"

        if [[ -n "${MODEM_NUM}" ]]; then
            MODEM_SAFE="$(mmcli -m "${MODEM_NUM}" 2>/dev/null | grep -Ei "state:|power state:|access tech:|signal quality:|operator name:|registration:|packet service state:" || true)"

            if echo "${MODEM_SAFE}" | grep -qi "state:.*registered\|state:.*connected\|state:.*enabled"; then
                pass "WWAN modem state is usable"
            else
                warn "WWAN modem detected, but state may not be ready"
            fi

            if echo "${MODEM_SAFE}" | grep -qi "registration:.*home\|registration:.*roaming"; then
                pass "WWAN modem is registered on cellular network"
            else
                warn "WWAN modem is not registered on cellular network"
            fi

            ACCESS_TECH="$(echo "${MODEM_SAFE}" | sed -n 's/.*access tech:[[:space:]]*//Ip' | head -n 1)"
            if [[ -n "${ACCESS_TECH}" ]]; then
                pass "WWAN access tech reported: ${ACCESS_TECH}"
            else
                warn "WWAN access tech not reported"
            fi

            SIGNAL_QUALITY="$(echo "${MODEM_SAFE}" | sed -n 's/.*signal quality:[[:space:]]*//Ip' | head -n 1)"
            if [[ -n "${SIGNAL_QUALITY}" ]]; then
                pass "WWAN signal quality reported: ${SIGNAL_QUALITY}"
            else
                warn "WWAN signal quality not reported"
            fi
        else
            warn "Could not determine ModemManager modem number"
        fi

    elif echo "${MODEM_LIST}" | grep -q "No modems were found"; then
        warn "No WWAN modem detected; expected only if adapter/card is not installed"
    else
        fail "Unexpected ModemManager output"
    fi
else
    fail "mmcli command missing"
fi

if nmcli device status 2>/dev/null | awk '$2 == "gsm" { found=1 } END { exit found ? 0 : 1 }'; then
    pass "NetworkManager sees a GSM/WWAN device"
else
    warn "NetworkManager does not currently show a GSM/WWAN device"
fi

if [[ -e /dev/cdc-wdm0 ]]; then
    pass "/dev/cdc-wdm0 exists"
else
    warn "/dev/cdc-wdm0 not present"
fi

if ip link show wwan0 >/dev/null 2>&1; then
    pass "wwan0 network interface exists"
elif ip link show ppp0 >/dev/null 2>&1; then
    pass "ppp0 cellular data interface exists"
else
    warn "No wwan0 or ppp0 cellular data interface is currently present"
fi

PCS_CELLULAR_PROFILE_ACTIVE="$(pcs_cellular_profile_name)"
if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${PCS_CELLULAR_PROFILE_ACTIVE}"; then
    pass "Cellular NetworkManager profile exists: ${PCS_CELLULAR_PROFILE_ACTIVE}"

    AUTOCONNECT="$(nmcli -g connection.autoconnect connection show "${PCS_CELLULAR_PROFILE_ACTIVE}" 2>/dev/null || true)"
    if [[ "${AUTOCONNECT}" == "no" ]]; then
        pass "Cellular profile autoconnect is disabled for manual control"
    else
        warn "Cellular profile autoconnect is not disabled"
    fi
else
    skip "Cellular NetworkManager profile not created yet"
fi

if service_enabled pcs-wwan-gps-nmea; then
    pass "WWAN GPS NMEA starter service is enabled: pcs-wwan-gps-nmea"
else
    warn "WWAN GPS NMEA starter service is not enabled: pcs-wwan-gps-nmea"
fi

if service_active pcs-wwan-gps-nmea; then
    pass "WWAN GPS NMEA starter has completed successfully"
else
    warn "WWAN GPS NMEA starter service is not active/exited"
fi

if [[ -e /dev/ttyUSB1 ]]; then
    pass "WWAN GPS NMEA serial port exists: /dev/ttyUSB1"
else
    warn "WWAN GPS NMEA serial port missing: /dev/ttyUSB1"
fi

if service_active gpsd; then
    pass "gpsd is active"
else
    warn "gpsd is not active"
fi

if service_enabled gpsd; then
    pass "gpsd is enabled"
else
    warn "gpsd is not enabled"
fi

if command_exists gpspipe; then
    GPSPIPE_SAMPLE_FILE="$(make_temp_file)"
    timeout 15 gpspipe -r -n 40 >"${GPSPIPE_SAMPLE_FILE}" 2>/dev/null || true

    if grep -qm1 '^\$G' "${GPSPIPE_SAMPLE_FILE}"; then
        pass "gpsd is receiving NMEA data"

        if awk -F, '
            /^\$..RMC/ && $3 == "A" { found=1 }
            /^\$..GGA/ && $7 ~ /^[1-9]/ { found=1 }
            END { exit found ? 0 : 1 }
        ' "${GPSPIPE_SAMPLE_FILE}"; then
            pass "gpsd NMEA includes a valid GPS fix"
        else
            warn "gpsd NMEA is present, but no valid GPS fix was seen during quick check"
        fi
    else
        warn "gpsd did not report NMEA data during quick check"
    fi
else
    warn "gpspipe command missing"
fi

if grep -Rqi "refclock SHM 0 refid GPS" /etc/chrony /etc/chrony/conf.d 2>/dev/null; then
    pass "Chrony GPS SHM refclock is configured"
else
    warn "Chrony GPS SHM refclock is not configured"
fi

CHRONY_GPS_SOURCE="$(chronyc sources -v 2>/dev/null | awk '$2 == "GPS" { print }' || true)"

if [[ -n "${CHRONY_GPS_SOURCE}" ]]; then
    pass "Chrony sees GPS source"

    CHRONY_GPS_REACH="$(echo "${CHRONY_GPS_SOURCE}" | awk '{ print $5 }' | head -n 1)"

    if [[ "${CHRONY_GPS_REACH}" =~ ^[0-9]+$ ]] && [[ "${CHRONY_GPS_REACH}" -gt 0 ]]; then
        pass "Chrony GPS source has nonzero reach: ${CHRONY_GPS_REACH}"
    else
        warn "Chrony GPS source exists but reach is zero or unknown"
    fi
else
    warn "Chrony does not currently show GPS source"
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
