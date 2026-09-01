#!/usr/bin/env bash

set -u

PCS_ETH_IFACE="eth0"
PCS_WIFI_IFACE="wlan0"
PCS_ROUTER_PROFILE="pcs-router-wan-share"
PCS_ROUTER_IP="10.42.0.1"
PCS_ROUTER_CIDR="10.42.0.1/24"

PCS_SHARE_NAME="PCS-Share"
PCS_SHARE_PATH="/mnt/pcs-usb/PCS-Share"

PCS_BACKUP_SHARE_NAME="PCS-Backup"
PCS_BACKUP_SHARE_PATH="/srv/pcs-share-backup"

PCS_HOSTNAME="$(hostname 2>/dev/null || echo pcs-pi)"

PCS_CONTROL_SERVICE="pcs-control-panel.service"
PCS_CONTROL_PORT="80"
PCS_CONTROL_URL="http://10.42.0.1"
PCS_ADMIN_URL="http://10.42.0.1/admin/"
PCS_DASHBOARD_REDIRECT_SERVICE="pcs-dashboard-redirect.service"
PCS_DASHBOARD_REDIRECT_URL="http://10.42.0.1:8080"
PCS_WIREGUARD_CONFIG="${PCS_WIREGUARD_CONFIG:-/etc/pcs/wireguard-management.conf}"
PCS_WIREGUARD_INTERFACE="wg-pcs"
PCS_WIREGUARD_RUNTIME_CONFIG="/etc/wireguard/wg-pcs.conf"

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"

if [[ -f "${INSTALL_CONFIG}" ]]; then
    # shellcheck source=/dev/null
    source "${INSTALL_CONFIG}"
fi

PCS_CELLULAR_PROFILE_DEFAULT="pcs-cellular-profile"
PCS_CELLULAR_PROFILE_LEGACY="pcs-cellular-tmobile"
PCS_CELLULAR_PROFILE="${PCS_CELLULAR_PROFILE:-${PCS_CELLULAR_PROFILE_DEFAULT}}"
PCS_CELLULAR_FALLBACK_MODE="${PCS_CELLULAR_FALLBACK_MODE:-manual}"
PCS_CELLULAR_FALLBACK_MARKER="/run/pcs-cellular-fallback-owned"
PCS_SETUP_APRS="${PCS_SETUP_APRS:-no}"
PCS_APRS_ENGINE="${PCS_APRS_ENGINE:-direwolf}"
PCS_GRAYWOLF_HTTP_ADDRESS="${PCS_GRAYWOLF_HTTP_ADDRESS:-10.42.0.1}"
PCS_GRAYWOLF_HTTP_PORT="${PCS_GRAYWOLF_HTTP_PORT:-8070}"
PCS_SETUP_MESHTASTIC="${PCS_SETUP_MESHTASTIC:-no}"
PCS_SETUP_GPIO_LCD="${PCS_SETUP_GPIO_LCD:-no}"
PCS_SETUP_GPIO_LEDS="${PCS_SETUP_GPIO_LEDS:-no}"
PCS_SETUP_GPIO_STATS="${PCS_SETUP_GPIO_STATS:-no}"
PCS_SETUP_GPIO_FAN="${PCS_SETUP_GPIO_FAN:-no}"
PCS_APRS_ACTIVE_MODE="${PCS_APRS_ACTIVE_MODE:-staged}"
PCS_APRS_AUDIO_INPUT="${PCS_APRS_AUDIO_INPUT:-auto}"
PCS_APRS_AUDIO_OUTPUT="${PCS_APRS_AUDIO_OUTPUT:-null}"
PCS_APRS_FREQUENCY="${PCS_APRS_FREQUENCY:-not selected}"
PCS_APRS_GPSD="${PCS_APRS_GPSD:-no}"
PCS_APRS_GPSD_HOST="${PCS_APRS_GPSD_HOST:-localhost}"
PCS_APRS_GPSD_PORT="${PCS_APRS_GPSD_PORT:-2947}"
PCS_APRS_AGW_PORT="${PCS_APRS_AGW_PORT:-0}"
PCS_APRS_KISS_PORT="${PCS_APRS_KISS_PORT:-0}"

case "${PCS_APRS_ENGINE}" in
    graywolf)
        PCS_APRS_ENGINE_LABEL="Graywolf"
        PCS_APRS_ENGINE_COMMAND="graywolf"
        PCS_APRS_ENGINE_SERVICE="graywolf.service"
        ;;
    *)
        PCS_APRS_ENGINE_LABEL="Dire Wolf"
        PCS_APRS_ENGINE_COMMAND="direwolf"
        PCS_APRS_ENGINE_SERVICE="direwolf.service"
        ;;
esac

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

echo "=== PCS System Status ==="
echo

echo "--- Hostname ---"
echo "${PCS_HOSTNAME}"
echo

echo "--- OS ---"
if [[ -f /etc/os-release ]]; then
    grep -E 'PRETTY_NAME|VERSION=' /etc/os-release
else
    echo "/etc/os-release not found"
fi
echo

echo "--- Hardware ---"
if [[ -f /proc/device-tree/model ]]; then
    tr -d '\0' < /proc/device-tree/model
    echo
else
    echo "Hardware model not found"
fi
echo

echo "--- Kernel ---"
uname -a
echo

echo "--- Uptime ---"
uptime
echo

echo "--- Time / NTP / RTC ---"
if command -v timedatectl >/dev/null 2>&1; then
    timedatectl
else
    echo "timedatectl not available"
fi
echo

echo "--- RTC Devices ---"
ls -l /dev/rtc* 2>/dev/null || echo "No RTC devices found"
if [[ -e /dev/rtc0 ]] && command -v hwclock >/dev/null 2>&1; then
    sudo -n hwclock --rtc=/dev/rtc0 --show --utc 2>/dev/null || echo "RTC value unavailable without elevated access"
fi
systemctl is-enabled pcs-rtc-seed.service 2>/dev/null || true
systemctl is-active pcs-rtc-seed.service 2>/dev/null || true
echo

echo "--- Chrony Tracking ---"
if command -v chronyc >/dev/null 2>&1; then
    chronyc tracking 2>/dev/null || echo "chronyc tracking failed"
    echo
    chronyc sources -v 2>/dev/null || echo "chronyc sources failed"
else
    echo "chronyc not available"
fi
echo

echo "--- NetworkManager Devices ---"
if command -v nmcli >/dev/null 2>&1; then
    nmcli device status
else
    echo "nmcli not available"
fi
echo

echo "--- Active Network Connections ---"
if command -v nmcli >/dev/null 2>&1; then
    timeout 8 nmcli --wait 5 connection show --active || echo "nmcli active connection check timed out or failed"
else
    echo "nmcli not available"
fi
echo

echo "--- IP Addresses ---"
ip -brief addr 2>/dev/null || ip addr
echo

echo "--- Routes ---"
ip route 2>/dev/null || echo "ip route unavailable"
echo

echo "--- WireGuard Remote Management ---"
if [[ ! -f "${PCS_WIREGUARD_RUNTIME_CONFIG}" ]]; then
    if [[ -f "${PCS_WIREGUARD_CONFIG}" ]]; then
        echo "Deployment inputs present; no activated runtime configuration"
    else
        echo "Not configured"
    fi
else
    echo "Firewall service: $(systemctl is-active pcs-wireguard-firewall.service 2>/dev/null || true)"
    echo "Tunnel service:   $(systemctl is-active "wg-quick@${PCS_WIREGUARD_INTERFACE}.service" 2>/dev/null || true)"
    if ip link show dev "${PCS_WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
        ip -brief addr show dev "${PCS_WIREGUARD_INTERFACE}" 2>/dev/null || true
        echo "Allowed management routes:"
        sudo -n wg show "${PCS_WIREGUARD_INTERFACE}" allowed-ips 2>/dev/null \
            || echo "  unavailable without elevated access"
        echo "Latest handshake:"
        sudo -n wg show "${PCS_WIREGUARD_INTERFACE}" latest-handshakes 2>/dev/null \
            || echo "  unavailable without elevated access"
        if ip -4 route show default dev "${PCS_WIREGUARD_INTERFACE}" | grep -q . \
            || ip -6 route show default dev "${PCS_WIREGUARD_INTERFACE}" 2>/dev/null | grep -q .; then
            echo "SAFETY ERROR: a default route uses ${PCS_WIREGUARD_INTERFACE}"
        else
            echo "Default route: unchanged (split tunnel)"
        fi
    else
        echo "Interface ${PCS_WIREGUARD_INTERFACE} is absent"
    fi
fi
echo

echo "--- DNS ---"
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl status 2>/dev/null || cat /etc/resolv.conf
else
    cat /etc/resolv.conf
fi
echo

echo "--- Router-Side Neighbors on ${PCS_ETH_IFACE} ---"
ip neigh show dev "${PCS_ETH_IFACE}" 2>/dev/null || echo "No neighbor data for ${PCS_ETH_IFACE}"
echo

echo "--- USB Devices ---"
if command -v lsusb >/dev/null 2>&1; then
    lsusb
else
    echo "lsusb not available"
fi
echo

echo "--- I2C Bus 1 ---"
I2CDETECT_BIN="$(command -v i2cdetect 2>/dev/null || true)"
if [[ -z "${I2CDETECT_BIN}" && -x /usr/sbin/i2cdetect ]]; then
    I2CDETECT_BIN="/usr/sbin/i2cdetect"
fi
if [[ -n "${I2CDETECT_BIN}" ]]; then
    sudo "${I2CDETECT_BIN}" -y 1
else
    echo "i2c-tools not installed"
fi
echo
echo "--- WWAN / Cellular ---"

if systemctl is-active --quiet ModemManager; then
    echo "ModemManager: active"
else
    echo "ModemManager: inactive"
fi

if systemctl is-enabled --quiet ModemManager 2>/dev/null; then
    echo "ModemManager enabled: yes"
else
    echo "ModemManager enabled: no"
fi

if command -v mmcli >/dev/null 2>&1; then
    MODEM_LIST="$(mmcli -L 2>&1 || true)"

    echo
    echo "[ModemManager list]"
    echo "${MODEM_LIST}"

    if echo "${MODEM_LIST}" | grep -q "/Modem/"; then
        MODEM_NUM="$(echo "${MODEM_LIST}" | sed -n 's#.*Modem/\([0-9]\+\).*#\1#p' | head -n 1)"

        if [[ -n "${MODEM_NUM}" ]]; then
            echo
            echo "[Safe modem summary]"
            mmcli -m "${MODEM_NUM}" 2>/dev/null \
                | grep -Ei "manufacturer:|model:|firmware revision:|h/w revision:|state:|power state:|access tech:|signal quality:|operator name:|registration:|packet service state:|ports:" \
                | sed -E \
                    -e 's/(equipment id: ).*/\1[REDACTED]/I' \
                    -e 's/(imei: ).*/\1[REDACTED]/I' \
                    -e 's/(own numbers?: ).*/\1[REDACTED]/I' \
                    -e 's/(own: ).*/\1[REDACTED]/I' \
                    -e 's/(subscriber id: ).*/\1[REDACTED]/I' \
                    -e 's/(sim iccid: ).*/\1[REDACTED]/I' \
                || true
        fi
    fi
else
    echo "mmcli: missing"
fi

echo
echo "[NetworkManager GSM device]"
if nmcli device status 2>/dev/null | awk '$2 == "gsm" { found=1 } END { exit found ? 0 : 1 }'; then
    nmcli device status | awk '$2 == "gsm" { print }'
else
    echo "No GSM/WWAN device shown by NetworkManager"
fi

echo
echo "[Cellular profile]"
PCS_CELLULAR_PROFILE_ACTIVE="$(pcs_cellular_profile_name)"
if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq -- "${PCS_CELLULAR_PROFILE_ACTIVE}"; then
    nmcli connection show "${PCS_CELLULAR_PROFILE_ACTIVE}" \
        | grep -E "connection.id|connection.autoconnect|gsm.apn|ipv4.method|ipv6.method|ipv4.route-metric|ipv6.route-metric" \
        || true
else
    echo "${PCS_CELLULAR_PROFILE_ACTIVE} profile does not exist"
fi

echo
echo "[Cellular fallback policy]"
echo "Configured policy: ${PCS_CELLULAR_FALLBACK_MODE}"
if systemctl is-enabled --quiet pcs-cellular-fallback.service 2>/dev/null; then
    echo "pcs-cellular-fallback.service enabled: yes"
else
    echo "pcs-cellular-fallback.service enabled: no"
fi
if systemctl is-active --quiet pcs-cellular-fallback.service 2>/dev/null; then
    echo "pcs-cellular-fallback.service active: yes"
else
    echo "pcs-cellular-fallback.service active: no"
fi
if [[ -e "${PCS_CELLULAR_FALLBACK_MARKER}" ]]; then
    echo "Cellular session ownership: automatic fallback"
else
    echo "Cellular session ownership: manual or disconnected"
fi

echo
echo "[WWAN interfaces]"
if [[ -e /dev/cdc-wdm0 ]]; then
    echo "/dev/cdc-wdm0: present"
else
    echo "/dev/cdc-wdm0: missing"
fi

for cell_iface in wwan0 ppp0; do
    if ip link show "${cell_iface}" >/dev/null 2>&1; then
        echo "${cell_iface}: present"
        ip -br addr show "${cell_iface}" || true
    else
        echo "${cell_iface}: missing"
    fi
done

echo
echo "[Cellular route preference]"
if ip route show default 2>/dev/null | grep -Eq "dev (wwan0|ppp0)"; then
    ip route show default | grep -E "dev (wwan0|ppp0)" || true
else
    echo "No default route through wwan0 or ppp0 currently active"
fi

echo
echo "--- GPS / GNSS ---"

echo "[WWAN modem GPS NMEA starter]"
if systemctl is-enabled --quiet pcs-wwan-gps-nmea.service 2>/dev/null; then
    echo "pcs-wwan-gps-nmea.service enabled: yes"
else
    echo "pcs-wwan-gps-nmea.service enabled: no"
fi

if systemctl is-active --quiet pcs-wwan-gps-nmea.service 2>/dev/null; then
    echo "pcs-wwan-gps-nmea.service state: active/exited"
else
    echo "pcs-wwan-gps-nmea.service state: inactive or missing"
fi

systemctl status pcs-wwan-gps-nmea.service --no-pager -l 2>/dev/null \
    | grep -E "Active:|NMEA output detected|GPS NMEA starter complete|Sent GPS_START|ModemManager modem detected" \
    || true

echo
echo "[NMEA serial port]"
if [[ -e /dev/ttyUSB1 ]]; then
    echo "/dev/ttyUSB1: present"
else
    echo "/dev/ttyUSB1: missing"
fi

echo
echo "[gpsd]"
if systemctl is-active --quiet gpsd.service 2>/dev/null; then
    echo "gpsd.service: active"
else
    echo "gpsd.service: inactive"
fi

if systemctl is-enabled --quiet gpsd.service 2>/dev/null; then
    echo "gpsd.service enabled: yes"
else
    echo "gpsd.service enabled: no"
fi

systemctl status gpsd.service --no-pager -l 2>/dev/null \
    | grep -E "Active:|/usr/sbin/gpsd" \
    || true

echo
echo "[gpsd NMEA quick check]"
if command -v gpspipe >/dev/null 2>&1; then
    if timeout 10 gpspipe -r 2>/dev/null | grep -qm1 '^\$G'; then
        echo "gpsd NMEA: present / location hidden"
    else
        echo "gpsd NMEA: not seen during quick check"
    fi
else
    echo "gpspipe: missing"
fi

echo
echo "[Chrony GPS source]"
CHRONY_GPS_SOURCE="$(chronyc sources -v 2>/dev/null | awk '$2 == "GPS" { print }' || true)"

if [[ -n "${CHRONY_GPS_SOURCE}" ]]; then
    echo "${CHRONY_GPS_SOURCE}"
    CHRONY_GPS_REACH="$(echo "${CHRONY_GPS_SOURCE}" | awk '{ print $5 }' | head -n 1)"

    if [[ "${CHRONY_GPS_REACH}" =~ ^[0-9]+$ ]] && [[ "${CHRONY_GPS_REACH}" -gt 0 ]]; then
        echo "Chrony GPS reach: ${CHRONY_GPS_REACH}"
    else
        echo "Chrony GPS reach: zero or unknown"
    fi
else
    echo "Chrony GPS source: not shown"
fi

echo
echo "--- Samba Shares ---"
if command -v testparm >/dev/null 2>&1; then
    testparm -s 2>/dev/null | grep -E "^\[.*\]" || echo "No Samba shares found by testparm"
else
    echo "testparm not available"
fi
echo

echo "--- Samba Storage Paths ---"
for path in "${PCS_SHARE_PATH}" "${PCS_BACKUP_SHARE_PATH}"; do
    echo
    echo "[${path}]"
    if [[ -d "${path}" ]]; then
        df -h "${path}" || true
        ls -la "${path}" || true
    else
        echo "Missing"
    fi
done
echo

echo "--- ${PCS_APRS_ENGINE_LABEL} / APRS ---"
echo "PCS state: ${PCS_SETUP_APRS}"
echo "APRS engine: ${PCS_APRS_ENGINE}"
case "${PCS_SETUP_APRS}" in
    staged|yes)
        if command -v "${PCS_APRS_ENGINE_COMMAND}" >/dev/null 2>&1; then
            if [[ "${PCS_APRS_ENGINE}" == "graywolf" ]]; then
                echo "Graywolf: installed ($(graywolf version 2>/dev/null || true))"
            else
                echo "Dire Wolf: installed ($(direwolf -t 0 -h 2>&1 | sed -n '1p' || true))"
            fi
        else
            echo "${PCS_APRS_ENGINE_LABEL}: missing"
        fi
        echo "Service active:  $(systemctl is-active "${PCS_APRS_ENGINE_SERVICE}" 2>/dev/null || true)"
        echo "Service enabled: $(systemctl is-enabled "${PCS_APRS_ENGINE_SERVICE}" 2>/dev/null || true)"
        if [[ "${PCS_APRS_ENGINE}" == "graywolf" ]]; then
            echo "Management UI:   http://${PCS_GRAYWOLF_HTTP_ADDRESS}:${PCS_GRAYWOLF_HTTP_PORT}/"
        fi
        echo "Active profile:  ${PCS_APRS_ACTIVE_MODE}"
        echo "Audio capture:   ${PCS_APRS_AUDIO_INPUT}"
        echo "Audio playback:  ${PCS_APRS_AUDIO_OUTPUT}"
        echo "Frequency:       ${PCS_APRS_FREQUENCY}"
        echo "GPS tracker:     ${PCS_APRS_GPSD} (${PCS_APRS_GPSD_HOST}:${PCS_APRS_GPSD_PORT})"
        echo "AGW endpoint:    10.42.0.1:${PCS_APRS_AGW_PORT}"
        echo "KISS endpoint:   10.42.0.1:${PCS_APRS_KISS_PORT}"
        echo "Radio init:      $(systemctl is-active pcs-sa818.service 2>/dev/null || true)"
        echo "Audio profile:   $(systemctl is-active pcs-aprs-audio.service 2>/dev/null || true)"
        echo "Client firewall: $(systemctl is-active pcs-aprs-kiss-firewall.service 2>/dev/null || true)"
        if [[ "${PCS_APRS_ENGINE}" == "direwolf" && -x /usr/local/sbin/pcs-aprs-telemetry ]]; then
            /usr/local/sbin/pcs-aprs-telemetry || true
        elif [[ "${PCS_APRS_ENGINE}" == "graywolf" && "${PCS_SETUP_APRS}" == "yes" ]]; then
            if curl -fsS --max-time 3 "http://${PCS_GRAYWOLF_HTTP_ADDRESS}:${PCS_GRAYWOLF_HTTP_PORT}/api/health" >/dev/null 2>&1; then
                echo "Packet telemetry: Graywolf API healthy; metrics at /metrics"
            else
                echo "Packet telemetry: Graywolf API unavailable"
            fi
        else
            echo "Packet telemetry: unavailable during staging"
        fi
        if [[ "${PCS_SETUP_APRS}" == "staged" ]]; then
            echo "RF TX:           disabled during software staging"
        else
            echo "RF TX:           ${PCS_APRS_TX_ENABLED:-no}"
        fi
        ;;
    *)
        echo "APRS software is not selected."
        ;;
esac
echo

echo "--- Meshtastic USB/Bluetooth MQTT Gateway ---"
echo "PCS state: ${PCS_SETUP_MESHTASTIC}"
case "${PCS_SETUP_MESHTASTIC}" in
    staged|yes)
        echo "Client:          $([[ -x /opt/pcs-meshtastic/bin/meshtastic ]] && echo installed || echo missing)"
        echo "Gateway:         $([[ -x /usr/local/sbin/pcs-meshtastic-gateway ]] && echo installed || echo missing)"
        echo "Service active:  $(systemctl is-active pcs-meshtastic.service 2>/dev/null || true)"
        echo "Service enabled: $(systemctl is-enabled pcs-meshtastic.service 2>/dev/null || true)"
        if [[ -x /usr/local/sbin/pcs_meshtastic_status.py ]]; then
            echo "Status:"
            /usr/local/sbin/pcs_meshtastic_status.py --check || true
        elif [[ -r /var/lib/pcs-meshtastic/status.json ]]; then
            echo "Status:"
            python3 -m json.tool /var/lib/pcs-meshtastic/status.json || true
        else
            echo "Status:          unavailable"
        fi
        ;;
    *)
        echo "Meshtastic USB/Bluetooth gateway is not selected."
        ;;
esac
echo

echo "--- 16x2 HD44780 LCD ---"
echo "PCS state: ${PCS_SETUP_GPIO_LCD}"
if [[ "${PCS_SETUP_GPIO_LCD}" == "yes" ]]; then
    echo "Driver:          $([[ -x /usr/local/sbin/pcs-gpio ]] && echo installed || echo missing)"
    echo "GPIO chip:       $([[ -e /dev/gpiochip0 ]] && echo available || echo missing)"
    echo "Python gpiozero: $(python3 -c 'import gpiozero' 2>/dev/null && echo available || echo missing)"
    echo "Service active:  $(systemctl is-active pcs-gpio-lcd.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-lcd.service 2>/dev/null || true)"
else
    echo "HD44780 LCD status display is not selected."
fi
echo

echo "--- Six-Pixel WS2812 Status Indicators ---"
echo "PCS state: ${PCS_SETUP_GPIO_LEDS}"
if [[ "${PCS_SETUP_GPIO_LEDS}" == "yes" ]]; then
    echo "Driver:          $([[ -x /usr/local/sbin/pcs-gpio ]] && echo installed || echo missing)"
    echo "GPIO / pixels:   GPIO21 PCM / 6"
    echo "Python library:  $([[ -x /opt/pcs-gpio-leds/bin/python ]] && /opt/pcs-gpio-leds/bin/python -c 'import rpi_ws281x' 2>/dev/null && echo available || echo missing)"
    echo "Service active:  $(systemctl is-active pcs-gpio-leds.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-leds.service 2>/dev/null || true)"
else
    echo "WS2812 status indicators are not selected."
fi
echo

echo "--- MAX7219 LED Matrix ---"
echo "PCS state: ${PCS_SETUP_GPIO_STATS}"
if [[ "${PCS_SETUP_GPIO_STATS}" == "yes" ]]; then
    echo "Driver:          $([[ -x /usr/local/sbin/pcs-gpio ]] && echo installed || echo missing)"
    echo "SPI0 CE0:        $([[ -e /dev/spidev0.0 ]] && echo available || echo missing)"
    echo "Service active:  $(systemctl is-active pcs-gpio-stats.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-stats.service 2>/dev/null || true)"
else
    echo "LED matrix statistics display is not selected."
fi
echo

echo "--- GPIO Boot Indicators ---"
if [[ "${PCS_SETUP_GPIO_LCD}" == "yes" \
    || "${PCS_SETUP_GPIO_LEDS}" == "yes" \
    || "${PCS_SETUP_GPIO_STATS}" == "yes" ]]; then
    echo "Runner:          $([[ -x /usr/local/sbin/pcs-gpio-startup ]] && echo installed || echo missing)"
    echo "Service active:  $(systemctl is-active pcs-gpio-startup.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-startup.service 2>/dev/null || true)"
    echo "Grace timeout:   90 seconds maximum; healthy handoff may occur earlier"
else
    echo "No visual status hardware is selected."
fi
echo

echo "--- Latched GPIO Shutdown Indicators ---"
if [[ "${PCS_SETUP_GPIO_LCD}" == "yes" \
    || "${PCS_SETUP_GPIO_LEDS}" == "yes" \
    || "${PCS_SETUP_GPIO_STATS}" == "yes" ]]; then
    echo "Service active:  $(systemctl is-active pcs-gpio-shutdown.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-shutdown.service 2>/dev/null || true)"
    echo "LCD registered:  $([[ -f /etc/pcs/gpio-shutdown/lcd ]] && echo yes || echo no)"
    echo "LEDs registered: $([[ -f /etc/pcs/gpio-shutdown/leds ]] && echo yes || echo no)"
    echo "Matrix marker:    $([[ -f /etc/pcs/gpio-shutdown/matrix ]] && echo yes || echo no)"
else
    echo "No visual status hardware is selected."
fi
echo

echo "--- GPIO18 Hardware PWM Fan ---"
echo "PCS state: ${PCS_SETUP_GPIO_FAN}"
if [[ "${PCS_SETUP_GPIO_FAN}" == "yes" ]]; then
    echo "PWM0 interface:  $([[ -d /sys/class/pwm/pwmchip0 ]] && echo available || echo reboot-required)"
    echo "Service active:  $(systemctl is-active pcs-gpio-fan.service 2>/dev/null || true)"
    echo "Service enabled: $(systemctl is-enabled pcs-gpio-fan.service 2>/dev/null || true)"
    if [[ -r /run/pcs-gpio-fan/status.json ]]; then
        echo -n "Controller:      "
        cat /run/pcs-gpio-fan/status.json
    fi
else
    echo "GPIO18 hardware PWM fan control is not selected."
fi

case "${PCS_SETUP_MESHTASTIC}" in
    staged)
        MESHTASTIC_STATUS="software staged / gateway disabled"
        ;;
    yes)
        if systemctl is-active --quiet pcs-meshtastic.service; then
            MESHTASTIC_STATUS="persistent gateway active"
        else
            MESHTASTIC_STATUS="configured / inactive"
        fi
        ;;
    *)
        MESHTASTIC_STATUS="not configured"
        ;;
esac
echo

echo "--- Key Services ---"
for service in NetworkManager ModemManager avahi-daemon smbd pcs-wsdd gpsd chrony cockpit.socket pcs-control-panel.service pcs-dashboard-redirect.service; do
    echo
    echo "[$service]"
    echo -n "enabled: "
    systemctl is-enabled "$service" 2>/dev/null || true
    echo -n "active:  "
    systemctl is-active "$service" 2>/dev/null || true
done
echo

echo "--- Raspberry Pi Connect ---"
if command -v rpi-connect >/dev/null 2>&1; then
    echo
    echo "[rpi-connect status]"
    rpi-connect status || true

    echo
    echo "[rpi-connect doctor]"
    rpi-connect doctor || true
else
    echo "rpi-connect command not found"
fi
echo

WWAN_SUMMARY="unknown"
if systemctl is-active --quiet ModemManager; then
    MODEM_LIST_SUMMARY="$(mmcli -L 2>/dev/null || true)"
    if echo "${MODEM_LIST_SUMMARY}" | grep -q "/Modem/"; then
        if nmcli device status 2>/dev/null | awk '$2 == "gsm" && $3 == "connected" { found=1 } END { exit found ? 0 : 1 }'; then
            WWAN_SUMMARY="modem detected, cellular connected"
        else
            if [[ "${PCS_CELLULAR_FALLBACK_MODE}" == "wifi-fallback" ]]; then
                WWAN_SUMMARY="modem detected, cellular standby (automatic Wi-Fi fallback)"
            else
                WWAN_SUMMARY="modem detected, cellular disconnected/manual"
            fi
        fi
    else
        WWAN_SUMMARY="ModemManager active, no modem detected"
    fi
else
    WWAN_SUMMARY="ModemManager inactive"
fi

GPS_SUMMARY="unknown"
if systemctl is-active --quiet gpsd.service 2>/dev/null; then
    if timeout 6 gpspipe -r 2>/dev/null | grep -qm1 '^\$G'; then
        GPS_SUMMARY="gpsd active, NMEA present"
    else
        GPS_SUMMARY="gpsd active, no quick NMEA sample"
    fi
else
    GPS_SUMMARY="gpsd inactive"
fi

echo "=== PCS Quick Summary ==="
echo

WIFI_STATE="unknown"
ETH_STATE="unknown"
DEFAULT_ROUTE="unknown"
ETH_ADDR_FOUND="no"
MODEM_STATUS="unknown"
GPSD_STATUS="unknown"
SAMBA_STATUS="unknown"
COCKPIT_STATUS="unknown"
CHRONY_STATUS="unknown"
CONTROL_PANEL_STATUS="unknown"
DASHBOARD_REDIRECT_STATUS="unknown"
RTC_STATUS="unknown"
PRIMARY_SHARE_STATUS="unknown"
BACKUP_SHARE_STATUS="unknown"
BACKUP_SYNC_STATUS="unknown"
WIREGUARD_STATUS="not configured"

if command -v nmcli >/dev/null 2>&1; then
    WIFI_STATE="$(nmcli -t -f DEVICE,STATE device status | awk -F: -v dev="${PCS_WIFI_IFACE}" '$1 == dev {print $2}')"
    ETH_STATE="$(nmcli -t -f DEVICE,STATE,CONNECTION device status | awk -F: -v dev="${PCS_ETH_IFACE}" '$1 == dev {print $2 " (" $3 ")"}')"
fi

if ip route 2>/dev/null | grep -q "default .* ${PCS_WIFI_IFACE}"; then
    DEFAULT_ROUTE="${PCS_WIFI_IFACE}"
else
    DEFAULT_ROUTE="$(ip route 2>/dev/null | awk '/^default/ {print $5; exit}')"
fi

if ip -4 addr show dev "${PCS_ETH_IFACE}" 2>/dev/null | grep -q "${PCS_ROUTER_CIDR}"; then
    ETH_ADDR_FOUND="yes"
fi

if [[ -e /dev/rtc0 ]]; then
    RTC_STATUS="present"
else
    RTC_STATUS="missing"
fi

if systemctl is-active --quiet ModemManager; then
    if command -v mmcli >/dev/null 2>&1 && mmcli -L 2>&1 | grep -q "No modems were found"; then
        MODEM_STATUS="ModemManager active, no modem detected yet"
    else
        MODEM_STATUS="ModemManager active"
    fi
else
    MODEM_STATUS="ModemManager inactive"
fi

if systemctl is-active --quiet gpsd; then
    GPSD_STATUS="active"
else
    GPSD_STATUS="inactive / not configured yet"
fi

if systemctl is-active --quiet smbd; then
    SAMBA_STATUS="active"
else
    SAMBA_STATUS="inactive"
fi

if systemctl is-active --quiet cockpit.socket; then
    COCKPIT_STATUS="active"
else
    COCKPIT_STATUS="inactive"
fi

if systemctl is-active --quiet chrony; then
    CHRONY_STATUS="active"
else
    CHRONY_STATUS="inactive"
fi

if systemctl is-active --quiet "${PCS_CONTROL_SERVICE}"; then
    CONTROL_PANEL_STATUS="active"
else
    CONTROL_PANEL_STATUS="inactive"
fi

if systemctl is-active --quiet "${PCS_DASHBOARD_REDIRECT_SERVICE}"; then
    DASHBOARD_REDIRECT_STATUS="active"
else
    DASHBOARD_REDIRECT_STATUS="inactive"
fi

case "${PCS_SETUP_APRS}" in
    staged)
        if command -v "${PCS_APRS_ENGINE_COMMAND}" >/dev/null 2>&1 \
            && ! systemctl is-active --quiet "${PCS_APRS_ENGINE_SERVICE}" \
            && ! systemctl is-enabled --quiet "${PCS_APRS_ENGINE_SERVICE}" 2>/dev/null; then
            APRS_STATUS="software staged / RF disabled"
        else
            APRS_STATUS="staging needs attention"
        fi
        ;;
    yes)
        if systemctl is-active --quiet "${PCS_APRS_ENGINE_SERVICE}"; then
            APRS_STATUS="active"
        else
            APRS_STATUS="configured / inactive"
        fi
        ;;
    *)
        APRS_STATUS="not configured"
        ;;
esac

if [[ "${PCS_SETUP_GPIO_LCD}" == "yes" ]]; then
    if systemctl is-active --quiet pcs-gpio-lcd.service; then
        GPIO_LCD_STATUS="active"
    else
        GPIO_LCD_STATUS="configured / inactive"
    fi
else
    GPIO_LCD_STATUS="not configured"
fi

if [[ "${PCS_SETUP_GPIO_STATS}" == "yes" ]]; then
    if systemctl is-active --quiet pcs-gpio-stats.service; then
        GPIO_STATS_STATUS="active"
    else
        GPIO_STATS_STATUS="configured / inactive"
    fi
else
    GPIO_STATS_STATUS="not configured"
fi

if [[ "${PCS_SETUP_GPIO_LEDS}" == "yes" ]]; then
    if systemctl is-active --quiet pcs-gpio-leds.service; then
        GPIO_LEDS_STATUS="active"
    else
        GPIO_LEDS_STATUS="configured / inactive"
    fi
else
    GPIO_LEDS_STATUS="not configured"
fi

if [[ "${PCS_SETUP_GPIO_FAN}" == "yes" ]]; then
    if systemctl is-active --quiet pcs-gpio-fan.service; then
        GPIO_FAN_STATUS="active"
    elif [[ ! -d /sys/class/pwm/pwmchip0 ]]; then
        GPIO_FAN_STATUS="configured / reboot required"
    else
        GPIO_FAN_STATUS="configured / inactive"
    fi
else
    GPIO_FAN_STATUS="not configured"
fi

if [[ -d "${PCS_SHARE_PATH}" ]] && testparm -s 2>/dev/null | grep -q "^\[${PCS_SHARE_NAME}\]"; then
    PRIMARY_SHARE_STATUS="present"
else
    PRIMARY_SHARE_STATUS="missing"
fi

if [[ -d "${PCS_BACKUP_SHARE_PATH}" ]] && testparm -s 2>/dev/null | grep -q "^\[${PCS_BACKUP_SHARE_NAME}\]"; then
    BACKUP_SHARE_STATUS="present"
else
    BACKUP_SHARE_STATUS="missing"
fi

if [[ -f "${PCS_BACKUP_SHARE_PATH}/LAST_SYNC.txt" ]]; then
    BACKUP_SYNC_STATUS="$(cat "${PCS_BACKUP_SHARE_PATH}/LAST_SYNC.txt" 2>/dev/null || echo unknown)"
else
    BACKUP_SYNC_STATUS="no sync timestamp found"
fi

if [[ -f "${PCS_WIREGUARD_RUNTIME_CONFIG}" ]]; then
    if systemctl is-active --quiet pcs-wireguard-firewall.service \
        && systemctl is-active --quiet "wg-quick@${PCS_WIREGUARD_INTERFACE}.service" \
        && ip link show dev "${PCS_WIREGUARD_INTERFACE}" >/dev/null 2>&1; then
        if ip -4 route show default dev "${PCS_WIREGUARD_INTERFACE}" | grep -q . \
            || ip -6 route show default dev "${PCS_WIREGUARD_INTERFACE}" 2>/dev/null | grep -q .; then
            WIREGUARD_STATUS="SAFETY ERROR: default route installed"
        else
            WIREGUARD_STATUS="active split tunnel"
        fi
    else
        WIREGUARD_STATUS="configured / inactive"
    fi
fi

echo "Hostname:                 ${PCS_HOSTNAME}"
echo "Wi-Fi uplink ${PCS_WIFI_IFACE}:       ${WIFI_STATE}"
echo "Ethernet handoff ${PCS_ETH_IFACE}:    ${ETH_STATE}"
echo "Default route interface:  ${DEFAULT_ROUTE}"
echo "Router-side IP present:   ${ETH_ADDR_FOUND} (${PCS_ROUTER_CIDR})"
echo "RTC:                      ${RTC_STATUS}"
echo "Chrony/NTP:               ${CHRONY_STATUS}"
echo "Samba:                    ${SAMBA_STATUS}"
echo "Primary share:            ${PRIMARY_SHARE_STATUS} (${PCS_SHARE_NAME})"
echo "Backup share:             ${BACKUP_SHARE_STATUS} (${PCS_BACKUP_SHARE_NAME})"
echo "Last backup sync:         ${BACKUP_SYNC_STATUS}"
echo "Cockpit:                  ${COCKPIT_STATUS}"
echo "PCS Homepage/Admin:       ${CONTROL_PANEL_STATUS} (${PCS_CONTROL_URL})"
echo "Legacy Admin Redirect:    ${DASHBOARD_REDIRECT_STATUS} (${PCS_DASHBOARD_REDIRECT_URL})"
echo "WireGuard Management:     ${WIREGUARD_STATUS}"
echo "Dire Wolf / APRS:         ${APRS_STATUS}"
echo "Meshtastic USB/BLE MQTT:  ${MESHTASTIC_STATUS}"
echo "HD44780 LCD:              ${GPIO_LCD_STATUS}"
echo "WS2812 Indicators:        ${GPIO_LEDS_STATUS}"
echo "MAX7219 LED Matrix:       ${GPIO_STATS_STATUS}"
echo "GPIO18 PWM Fan:           ${GPIO_FAN_STATUS}"
printf "%-27s %s
" "WWAN modem:" "${WWAN_SUMMARY}"
echo "GPSD:                     ${GPSD_STATUS}"
echo

echo "--- PCS Client Access Info ---"
echo
echo "Use these from a client connected to the PCS AP/switch:"
echo
echo "Primary file share:"
echo "  \\\\${PCS_ROUTER_IP}\\${PCS_SHARE_NAME}"
echo
echo "Backup file share:"
echo "  \\\\${PCS_ROUTER_IP}\\${PCS_BACKUP_SHARE_NAME}"
echo
echo "Cockpit web UI:"
echo "  https://${PCS_ROUTER_IP}:9090"
echo

echo "PCS public homepage:"
echo "  http://${PCS_ROUTER_IP}/"
echo
echo "PCS Admin Login:"
echo "  http://${PCS_ROUTER_IP}/admin/"
echo
echo "LAN NTP server:"
echo "  ${PCS_ROUTER_IP}"
echo
echo "Primary share path on Pi:"
echo "  ${PCS_SHARE_PATH}"
echo
echo "Backup share path on Pi:"
echo "  ${PCS_BACKUP_SHARE_PATH}"
echo

echo "--- Windows Client Test Commands ---"
echo
echo "Internet:"
echo "  ping 8.8.8.8"
echo "  ping google.com"
echo
echo "Pi/router-side access:"
echo "  ping ${PCS_ROUTER_IP}"
echo
echo "NTP:"
echo "  w32tm /stripchart /computer:${PCS_ROUTER_IP} /samples:5 /dataonly"
echo
echo "Primary file share:"
echo "  explorer \\\\${PCS_ROUTER_IP}\\${PCS_SHARE_NAME}"
echo
echo "Backup file share:"
echo "  explorer \\\\${PCS_ROUTER_IP}\\${PCS_BACKUP_SHARE_NAME}"
echo

echo "PCS public homepage:"
echo "  start http://${PCS_ROUTER_IP}/"
echo
echo "PCS Admin Login:"
echo "  start http://${PCS_ROUTER_IP}/admin/"
echo

echo "--- Current PCS Topology ---"
echo
echo "Field LAN path:"
echo "  Client → OpenWrt AP/switch Wi-Fi/LAN → Pi ${PCS_ETH_IFACE} (${PCS_ROUTER_IP})"
echo
echo "Optional internet path:"
echo "  Client → OpenWrt AP/switch → Pi ${PCS_ETH_IFACE} → selected Pi Wi-Fi/cellular uplink"
echo

echo "--- Notes ---"
echo
echo "- ${PCS_ROUTER_IP} is the stable router-side Pi address for PCS clients."
echo "- ${PCS_HOSTNAME}.local may not resolve on every client because mDNS behavior varies across bridged AP and client-isolation configurations."
echo "- ${PCS_SHARE_NAME} is the current primary/test share."
echo "- ${PCS_BACKUP_SHARE_NAME} is the SD-card backup mirror share."
echo "- Run ./scripts/sync-pcs-share-to-backup.sh to manually mirror the primary share to backup."
echo "- PCS public status is available at http://${PCS_ROUTER_IP}/ on the router-side network."
echo "- Authenticated operator controls are available through the visible Admin Login panel or http://${PCS_ROUTER_IP}/admin/."
echo "- WWAN modem and GPS NMEA are supported and tested."
echo "- GPSD is expected to be active when WWAN modem GPS setup is installed."
if [[ "${PCS_CELLULAR_FALLBACK_MODE}" == "wifi-fallback" ]]; then
    echo "- EM7565 LTE registration, NMEA, GPSD, and GPS-disciplined Chrony are validated; cellular data is armed as an automatic Wi-Fi fallback."
else
    echo "- EM7565 LTE registration, NMEA, GPSD, and GPS-disciplined Chrony are validated; cellular data remains manually controlled."
fi
echo

echo "=== End PCS Status ==="
