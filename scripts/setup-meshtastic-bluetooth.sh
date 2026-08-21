#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_CONFIG="${PCS_INSTALL_CONFIG:-${REPO_DIR}/config/pcs-install.conf}"
MESHTASTIC_VERSION="2.7.11"
PAHO_MQTT_VERSION="2.1.0"
VENV_DIR="/opt/pcs-meshtastic"
ENV_FILE="/etc/pcs/meshtastic.env"
MQTT_SECRET_FILE="/etc/pcs/meshtastic-mqtt.env"
STATUS_FILE="/var/lib/pcs-meshtastic/status.json"
COLLECTOR_SOURCE="${REPO_DIR}/scripts/pcs_meshtastic_status.py"
COLLECTOR_TARGET="/usr/local/sbin/pcs_meshtastic_status.py"
GATEWAY_SOURCE="${REPO_DIR}/scripts/pcs_meshtastic_gateway.py"
GATEWAY_TARGET="/usr/local/sbin/pcs-meshtastic-gateway"
BLE_SOURCE="${REPO_DIR}/scripts/pcs_meshtastic_ble.py"
BLE_TARGET="/usr/local/sbin/pcs_meshtastic_ble.py"
IMPORT_SOURCE="${REPO_DIR}/scripts/pcs_meshtastic_import_mqtt.py"
IMPORT_TARGET="/usr/local/sbin/pcs-meshtastic-import-mqtt"
BLUETOOTH_READY_SOURCE="${REPO_DIR}/scripts/pcs-bluetooth-ready.sh"
BLUETOOTH_READY_TARGET="/usr/local/sbin/pcs-bluetooth-ready"
RADIO_READY_SOURCE="${REPO_DIR}/scripts/pcs-meshtastic-ready.sh"
RADIO_READY_TARGET="/usr/local/sbin/pcs-meshtastic-ready"
MODEMMANAGER_RULE_SOURCE="${REPO_DIR}/config-examples/99-pcs-meshtastic.rules"
SERVICE_SOURCE="${REPO_DIR}/systemd/pcs-meshtastic.service"
BLUETOOTH_READY_SERVICE_SOURCE="${REPO_DIR}/systemd/pcs-bluetooth-ready.service"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-meshtastic-bluetooth.sh --prepare|--scan|--import-radio-mqtt DEVICE|--configure DEVICE MQTT_HOST [MQTT_PORT]|--configure-usb /dev/ttyACM0 MQTT_HOST [MQTT_PORT]|--enable-gpsd-position|--disable-gpsd-position|--check|--disable

  --prepare           Install pinned Bluetooth/MQTT support; leave gateway disabled.
  --scan              List discoverable Meshtastic BLE devices (10-second scan).
  --import-radio-mqtt DEVICE
                      Copy the radio's broker credentials into PCS without displaying them.
  --configure ...     Record a paired BLE target and broker, then enable the gateway.
  --configure-usb ... Disable radio Bluetooth, use /dev/ttyACM0, and enable the gateway.
  --enable-gpsd-position
                      Send a fresh PCS GPSD fix through the node every five minutes.
  --disable-gpsd-position
                      Stop supplying PCS GPSD fixes to the node.
  --check             Inspect installed, paired, gateway service, and status state.
  --disable           Stop and disable the PCS gateway without changing the radio.

The service keeps the selected radio transport connected continuously. It transparently relays the
radio's MQTT proxy envelopes; it does not send arbitrary text messages or modify
Meshtastic radio/channel configuration. USB configuration makes one security
change by disabling the node's Bluetooth radio. Downlink is disabled until
explicit MQTT subscription filters are added to /etc/pcs/meshtastic.env.
EOF
}

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Run this script as the normal Pi user, not with sudo."
        exit 1
    fi
}

require_prepared() {
    [[ -x "${VENV_DIR}/bin/meshtastic" ]] || {
        echo "ERROR: Meshtastic support is not prepared. Run --prepare first."
        exit 1
    }
}

has_config_value() {
    local key="$1"
    sudo awk -F= -v key="${key}" '$1 == key && length($2) > 0 { found=1 } END { exit found ? 0 : 1 }' "${ENV_FILE}" 2>/dev/null
}

set_install_state() {
    local state="$1"
    local config_dir
    local config_temp
    config_dir="$(dirname "${INSTALL_CONFIG}")"
    mkdir -p "${config_dir}"
    config_temp="$(mktemp "${config_dir}/.pcs-install.XXXXXX")"
    if [[ -f "${INSTALL_CONFIG}" ]]; then
        awk -v value="${state}" '
            BEGIN { replaced=0 }
            /^[[:space:]]*PCS_SETUP_MESHTASTIC=/ {
                printf "PCS_SETUP_MESHTASTIC=%c%s%c\n", 34, value, 34
                replaced=1
                next
            }
            { print }
            END {
                if (!replaced) printf "PCS_SETUP_MESHTASTIC=%c%s%c\n", 34, value, 34
            }
        ' "${INSTALL_CONFIG}" > "${config_temp}"
    else
        printf '# PCS install config\nPCS_SETUP_MESHTASTIC="%s"\n' "${state}" > "${config_temp}"
    fi
    chmod 0600 "${config_temp}"
    mv -f -- "${config_temp}" "${INSTALL_CONFIG}"
}

prepare() {
    require_normal_user
    [[ -f "${COLLECTOR_SOURCE}" ]] || { echo "ERROR: Missing ${COLLECTOR_SOURCE}"; exit 1; }
    [[ -f "${GATEWAY_SOURCE}" ]] || { echo "ERROR: Missing ${GATEWAY_SOURCE}"; exit 1; }
    [[ -f "${BLE_SOURCE}" ]] || { echo "ERROR: Missing ${BLE_SOURCE}"; exit 1; }
    [[ -f "${IMPORT_SOURCE}" ]] || { echo "ERROR: Missing ${IMPORT_SOURCE}"; exit 1; }
    [[ -f "${BLUETOOTH_READY_SOURCE}" ]] || { echo "ERROR: Missing ${BLUETOOTH_READY_SOURCE}"; exit 1; }
    [[ -f "${RADIO_READY_SOURCE}" ]] || { echo "ERROR: Missing ${RADIO_READY_SOURCE}"; exit 1; }
    [[ -f "${MODEMMANAGER_RULE_SOURCE}" ]] || { echo "ERROR: Missing ${MODEMMANAGER_RULE_SOURCE}"; exit 1; }
    [[ -f "${SERVICE_SOURCE}" ]] || { echo "ERROR: Missing ${SERVICE_SOURCE}"; exit 1; }
    [[ -f "${BLUETOOTH_READY_SERVICE_SOURCE}" ]] || { echo "ERROR: Missing ${BLUETOOTH_READY_SERVICE_SOURCE}"; exit 1; }

    echo "Installing BlueZ and Python virtual-environment support..."
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y bluez python3-venv rfkill

    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        sudo python3 -m venv "${VENV_DIR}"
    fi
    sudo "${VENV_DIR}/bin/python" -m pip install --upgrade pip
    sudo "${VENV_DIR}/bin/python" -m pip install \
        "meshtastic[cli]==${MESHTASTIC_VERSION}" \
        "paho-mqtt==${PAHO_MQTT_VERSION}"

    sudo install -o root -g root -m 0644 "${COLLECTOR_SOURCE}" "${COLLECTOR_TARGET}"
    sudo install -o root -g root -m 0755 "${GATEWAY_SOURCE}" "${GATEWAY_TARGET}"
    sudo install -o root -g root -m 0644 "${BLE_SOURCE}" "${BLE_TARGET}"
    sudo install -o root -g root -m 0755 "${IMPORT_SOURCE}" "${IMPORT_TARGET}"
    sudo install -o root -g root -m 0755 "${BLUETOOTH_READY_SOURCE}" "${BLUETOOTH_READY_TARGET}"
    sudo install -o root -g root -m 0755 "${RADIO_READY_SOURCE}" "${RADIO_READY_TARGET}"
    sudo install -o root -g root -m 0644 "${MODEMMANAGER_RULE_SOURCE}" /etc/udev/rules.d/99-pcs-meshtastic.rules
    sudo install -o root -g root -m 0644 "${BLUETOOTH_READY_SERVICE_SOURCE}" /etc/systemd/system/pcs-bluetooth-ready.service
    sudo install -o root -g root -m 0644 "${SERVICE_SOURCE}" /etc/systemd/system/pcs-meshtastic.service
    sudo install -d -o root -g root -m 0755 /etc/pcs

    if [[ ! -e "${ENV_FILE}" ]]; then
        env_temp="$(mktemp)"
        {
            printf 'PCS_MESHTASTIC_DEVICE=\n'
            printf 'PCS_MESHTASTIC_PORT=\n'
            printf 'PCS_MESHTASTIC_MQTT_HOST=\n'
            printf 'PCS_MESHTASTIC_MQTT_PORT=1883\n'
            printf 'PCS_MESHTASTIC_MQTT_TLS=no\n'
            printf 'PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS=\n'
            printf 'PCS_MESHTASTIC_GPSD_POSITION=no\n'
            printf 'PCS_MESHTASTIC_POSITION_INTERVAL=300\n'
            printf 'PCS_MESHTASTIC_POSITION_CHANNEL=0\n'
        } > "${env_temp}"
        sudo install -o root -g root -m 0600 "${env_temp}" "${ENV_FILE}"
        rm -f "${env_temp}"
    fi

    if [[ ! -e "${MQTT_SECRET_FILE}" ]]; then
        secret_temp="$(mktemp)"
        {
            printf 'PCS_MESHTASTIC_MQTT_USERNAME=\n'
            printf 'PCS_MESHTASTIC_MQTT_PASSWORD=\n'
        } > "${secret_temp}"
        sudo install -o root -g root -m 0600 "${secret_temp}" "${MQTT_SECRET_FILE}"
        rm -f "${secret_temp}"
    fi

    sudo systemctl daemon-reload
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=tty
    sudo systemctl disable --now pcs-meshtastic.service >/dev/null 2>&1 || true

    echo "Meshtastic Python ${MESHTASTIC_VERSION} and Paho MQTT ${PAHO_MQTT_VERSION} are staged."
    echo "The gateway remains disabled until the node is paired and --configure is run."
    set_install_state staged
}

scan() {
    require_prepared
    sudo systemctl start bluetooth.service
    "${VENV_DIR}/bin/meshtastic" --ble-scan
}

import_radio_mqtt() {
    require_normal_user
    require_prepared
    device="${1:-}"
    if [[ -z "${device}" || ! "${device}" =~ ^[A-Za-z0-9:_-]{2,128}$ ]]; then
        echo "ERROR: DEVICE must be a Meshtastic BLE name or Bluetooth address."
        exit 2
    fi

    credential_temp="$(mktemp)"
    trap 'rm -f -- "${credential_temp}"' RETURN
    "${VENV_DIR}/bin/python" "${IMPORT_TARGET}" --device "${device}" --output "${credential_temp}"
    sudo install -o root -g root -m 0600 "${credential_temp}" "${MQTT_SECRET_FILE}"
    rm -f -- "${credential_temp}"
    trap - RETURN
    echo "Stored radio MQTT credentials in ${MQTT_SECRET_FILE} (root:root 0600)."
}

configure() {
    require_normal_user
    require_prepared
    device="${1:-}"
    mqtt_host="${2:-}"
    mqtt_port="${3:-1883}"
    if [[ -z "${device}" || ! "${device}" =~ ^[A-Za-z0-9:_-]{2,128}$ ]]; then
        echo "ERROR: DEVICE must be a Meshtastic BLE name or Bluetooth address."
        exit 2
    fi
    if [[ -z "${mqtt_host}" || ! "${mqtt_host}" =~ ^[A-Za-z0-9.-]{1,253}$ ]]; then
        echo "ERROR: MQTT_HOST must be a hostname or IP address without a URL scheme."
        exit 2
    fi
    if [[ ! "${mqtt_port}" =~ ^[0-9]{1,5}$ ]] || (( mqtt_port < 1 || mqtt_port > 65535 )); then
        echo "ERROR: MQTT_PORT must be between 1 and 65535."
        exit 2
    fi

    mqtt_tls="no"
    if [[ "${mqtt_port}" == "8883" ]]; then
        mqtt_tls="yes"
    fi

    env_temp="$(mktemp)"
    {
        printf 'PCS_MESHTASTIC_DEVICE=%s\n' "${device}"
        printf 'PCS_MESHTASTIC_PORT=\n'
        printf 'PCS_MESHTASTIC_MQTT_HOST=%s\n' "${mqtt_host}"
        printf 'PCS_MESHTASTIC_MQTT_PORT=%s\n' "${mqtt_port}"
        printf 'PCS_MESHTASTIC_MQTT_TLS=%s\n' "${mqtt_tls}"
        printf 'PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS=\n'
    } > "${env_temp}"
    sudo install -o root -g root -m 0600 "${env_temp}" "${ENV_FILE}"
    rm -f "${env_temp}"

    sudo systemctl enable --now bluetooth.service
    sudo systemctl enable --now pcs-meshtastic.service
    set_install_state yes
    echo "Configured Meshtastic BLE device: ${device}"
    echo "Configured MQTT broker: ${mqtt_host}:${mqtt_port}"
    echo "PCS will keep the BLE session open continuously. MQTT uplink is active."
    echo "Downlink remains off until explicit subscription filters are configured."
}

configure_usb() {
    require_normal_user
    require_prepared
    port="${1:-}"
    mqtt_host="${2:-}"
    mqtt_port="${3:-1883}"
    if [[ "${port}" != "/dev/ttyACM0" ]]; then
        echo "ERROR: The hardened service exposes only /dev/ttyACM0."
        exit 2
    fi
    if [[ -z "${mqtt_host}" || ! "${mqtt_host}" =~ ^[A-Za-z0-9.-]{1,253}$ ]]; then
        echo "ERROR: MQTT_HOST must be a hostname or IP address without a URL scheme."
        exit 2
    fi
    if [[ ! "${mqtt_port}" =~ ^[0-9]{1,5}$ ]] || (( mqtt_port < 1 || mqtt_port > 65535 )); then
        echo "ERROR: MQTT_PORT must be between 1 and 65535."
        exit 2
    fi

    mqtt_tls="no"
    [[ "${mqtt_port}" == "8883" ]] && mqtt_tls="yes"
    subscriptions="$(sudo awk -F= '$1 == "PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS" { sub(/^[^=]*=/, ""); print; exit }' "${ENV_FILE}" 2>/dev/null || true)"

    sudo systemctl stop pcs-meshtastic.service
    echo "Disabling Bluetooth on the USB-connected Meshtastic node..."
    if ! timeout 45 "${VENV_DIR}/bin/meshtastic" \
        --port "${port}" \
        --set bluetooth.enabled false; then
        echo "ERROR: Could not disable Bluetooth on ${port}; restoring the previous gateway state."
        sudo systemctl start pcs-meshtastic.service || true
        exit 1
    fi

    env_temp="$(mktemp)"
    {
        printf 'PCS_MESHTASTIC_DEVICE=\n'
        printf 'PCS_MESHTASTIC_PORT=%s\n' "${port}"
        printf 'PCS_MESHTASTIC_MQTT_HOST=%s\n' "${mqtt_host}"
        printf 'PCS_MESHTASTIC_MQTT_PORT=%s\n' "${mqtt_port}"
        printf 'PCS_MESHTASTIC_MQTT_TLS=%s\n' "${mqtt_tls}"
        printf 'PCS_MESHTASTIC_MQTT_SUBSCRIPTIONS=%s\n' "${subscriptions}"
    } > "${env_temp}"
    sudo install -o root -g root -m 0600 "${env_temp}" "${ENV_FILE}"
    rm -f "${env_temp}"

    sudo systemctl enable --now pcs-meshtastic.service
    set_install_state yes
    echo "Configured Meshtastic USB port: ${port}"
    echo "Disabled Bluetooth on the Meshtastic node."
    echo "Configured MQTT broker: ${mqtt_host}:${mqtt_port}"
    echo "PCS will keep the serial session open continuously."
}

set_gpsd_position() {
    require_normal_user
    require_prepared
    local value="$1"
    local env_temp
    env_temp="$(mktemp)"
    sudo awk -v value="${value}" '
        BEGIN { replaced=0 }
        /^PCS_MESHTASTIC_GPSD_POSITION=/ {
            print "PCS_MESHTASTIC_GPSD_POSITION=" value
            replaced=1
            next
        }
        { print }
        END {
            if (!replaced) print "PCS_MESHTASTIC_GPSD_POSITION=" value
        }
    ' "${ENV_FILE}" > "${env_temp}"
    sudo install -o root -g root -m 0600 "${env_temp}" "${ENV_FILE}"
    rm -f "${env_temp}"
    sudo systemctl restart pcs-meshtastic.service
    echo "PCS GPSD position feed: ${value}"
}

check() {
    echo "=== PCS Meshtastic Bluetooth ==="
    echo "Pinned client: Meshtastic Python ${MESHTASTIC_VERSION}; Paho MQTT ${PAHO_MQTT_VERSION}"
    echo "Virtual env:   $([[ -x "${VENV_DIR}/bin/python" ]] && echo installed || echo missing)"
    echo "Status helper: $([[ -r "${COLLECTOR_TARGET}" ]] && echo installed || echo missing)"
    echo "Gateway:       $([[ -x "${GATEWAY_TARGET}" ]] && echo installed || echo missing)"
    echo "BLE transport: $([[ -r "${BLE_TARGET}" ]] && echo installed || echo missing)"
    echo "Bluetooth:     $(systemctl is-active bluetooth.service 2>/dev/null || true)"
    echo "BT controller: $(bluetoothctl show 2>/dev/null | awk -F': ' '/^[[:space:]]*Powered:/ { print $2; exit }')"
    echo "BT ready unit: $(systemctl is-active pcs-bluetooth-ready.service 2>/dev/null || true)"
    echo "Radio ready:   $([[ -x "${RADIO_READY_TARGET}" ]] && echo installed || echo missing)"
    echo "Service:       $(systemctl is-active pcs-meshtastic.service 2>/dev/null || true)"
    echo "Enabled:       $(systemctl is-enabled pcs-meshtastic.service 2>/dev/null || true)"
    echo "BLE target:    $(has_config_value PCS_MESHTASTIC_DEVICE && echo configured || echo not-configured)"
    echo "USB target:    $(has_config_value PCS_MESHTASTIC_PORT && echo configured || echo not-configured)"
    echo "MQTT broker:   $(has_config_value PCS_MESHTASTIC_MQTT_HOST && echo configured || echo not-configured)"
    echo "GPSD position:  $(sudo awk -F= '$1 == "PCS_MESHTASTIC_GPSD_POSITION" { print $2; exit }' "${ENV_FILE}" 2>/dev/null || true)"
    if [[ -r "${STATUS_FILE}" ]]; then
        echo "Status:"
        python3 -m json.tool "${STATUS_FILE}" || true
    else
        echo "Status:        no gateway result yet"
    fi
}

disable() {
    require_normal_user
    sudo systemctl disable --now pcs-meshtastic.service >/dev/null 2>&1 || true
    set_install_state staged
    echo "PCS Meshtastic gateway is disabled. Radio configuration was not changed."
}

case "${1:-}" in
    --prepare)
        prepare
        ;;
    --scan)
        scan
        ;;
    --import-radio-mqtt)
        import_radio_mqtt "${2:-}"
        ;;
    --configure)
        configure "${2:-}" "${3:-}" "${4:-1883}"
        ;;
    --configure-usb)
        configure_usb "${2:-}" "${3:-}" "${4:-1883}"
        ;;
    --check)
        check
        ;;
    --enable-gpsd-position)
        set_gpsd_position yes
        ;;
    --disable-gpsd-position)
        set_gpsd_position no
        ;;
    --disable)
        disable
        ;;
    --help|-h|"")
        usage
        ;;
    *)
        echo "ERROR: Unknown option: $1"
        usage
        exit 2
        ;;
esac
