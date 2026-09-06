#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POWER_CONFIG="${PCS_POWER_CONFIG:-/etc/pcs/power-monitor.json}"

usage() {
    echo "Usage: ./scripts/setup-power-audio.sh --install-power|--install-buzzer|--check"
}

check_state() {
    echo "=== PCS power and audible status ==="
    if [[ -r "${POWER_CONFIG}" ]]; then
        /usr/local/sbin/pcs-power-monitor check-config --config "${POWER_CONFIG}" || true
    else
        echo "Power monitor: not configured"
    fi
    [[ -r /run/pcs-power-monitor/status.json ]] && python3 -m json.tool /run/pcs-power-monitor/status.json || true
    systemctl is-enabled pcs-power-monitor.service 2>/dev/null || true
    systemctl is-active pcs-power-monitor.service 2>/dev/null || true
    systemctl is-enabled pcs-buzzer.service 2>/dev/null || true
    systemctl is-active pcs-buzzer.service 2>/dev/null || true
}

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Run this script as the normal Pi user, not with sudo." >&2
        exit 1
    fi
}

install_power() {
    require_normal_user
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y i2c-tools python3-smbus
    if command -v raspi-config >/dev/null 2>&1; then
        sudo raspi-config nonint do_i2c 0
    fi
    [[ -e /dev/i2c-1 ]] || { echo "ERROR: /dev/i2c-1 is unavailable; reboot after enabling I2C, then rerun."; exit 1; }
    sudo install -o root -g root -m 0755 "${REPO_DIR}/scripts/pcs_power_monitor.py" /usr/local/sbin/pcs-power-monitor
    sudo install -o root -g root -m 0644 "${REPO_DIR}/systemd/pcs-power-monitor.service" /etc/systemd/system/pcs-power-monitor.service
    sudo install -d -o root -g root -m 0755 /etc/pcs
    if [[ ! -e "${POWER_CONFIG}" ]]; then
        sudo install -o root -g root -m 0644 "${REPO_DIR}/config/power-monitor.example.json" "${POWER_CONFIG}"
        echo "IMPORTANT: Verify both INA226 addresses and shunt calibrations in ${POWER_CONFIG}."
        echo "Controlled shutdown remains disarmed until allow_shutdown is explicitly set true."
    fi
    if ! sudo /usr/local/sbin/pcs-power-monitor check-config --config "${POWER_CONFIG}"; then
        echo "Power-monitor software and its disabled unit are staged."
        echo "Replace every calibration placeholder in ${POWER_CONFIG}, then rerun this command."
        exit 2
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable --now pcs-power-monitor.service
    check_state
}

install_buzzer() {
    require_normal_user
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-gpiozero
    sudo install -o root -g root -m 0755 "${REPO_DIR}/scripts/pcs_buzzer.py" /usr/local/sbin/pcs-buzzer
    sudo install -o root -g root -m 0644 "${REPO_DIR}/systemd/pcs-buzzer.service" /etc/systemd/system/pcs-buzzer.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now pcs-buzzer.service
    echo "Buzzer GPIO13 support installed active-low. Confirm the external ~10k SIG-to-3.3V pull-up before enabling."
    check_state
}

case "${1:-}" in
    --install-power) install_power ;;
    --install-buzzer) install_buzzer ;;
    --check) check_state ;;
    *) usage; exit 2 ;;
esac
