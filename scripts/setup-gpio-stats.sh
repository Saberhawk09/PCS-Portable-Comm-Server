#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_SOURCE="${REPO_DIR}/scripts/pcs_gpio.py"
UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-stats.service"
STARTUP_SCRIPT_SOURCE="${REPO_DIR}/scripts/pcs-gpio-startup.sh"
STARTUP_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-startup.service"
SHUTDOWN_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-shutdown.service"
DRIVER_TARGET="/usr/local/sbin/pcs-gpio"
UNIT_TARGET="/etc/systemd/system/pcs-gpio-stats.service"
STARTUP_SCRIPT_TARGET="/usr/local/sbin/pcs-gpio-startup"
STARTUP_UNIT_TARGET="/etc/systemd/system/pcs-gpio-startup.service"
SHUTDOWN_UNIT_TARGET="/etc/systemd/system/pcs-gpio-shutdown.service"
SHUTDOWN_MARKER_DIR="/etc/pcs/gpio-shutdown"
SHUTDOWN_MARKER="${SHUTDOWN_MARKER_DIR}/matrix"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-gpio-stats.sh --install|--check

  --install  Install and start the MAX7219 stats service.
  --check    Read the driver/service/SPI state without changing it.
EOF
}

check_state() {
    echo "=== PCS GPIO stats ==="
    test -x "${DRIVER_TARGET}" && echo "Driver: installed" || echo "Driver: missing"
    test -e /dev/spidev0.0 && echo "SPI0 CE0: available" || echo "SPI0 CE0: missing"
    python3 -c 'import spidev' 2>/dev/null \
        && echo "Python spidev: available" \
        || echo "Python spidev: missing"
    systemctl is-enabled pcs-gpio-stats.service 2>/dev/null || true
    systemctl is-active pcs-gpio-stats.service 2>/dev/null || true
    systemctl is-enabled pcs-gpio-startup.service 2>/dev/null || true
    systemctl is-active pcs-gpio-startup.service 2>/dev/null || true
    systemctl is-enabled pcs-gpio-shutdown.service 2>/dev/null || true
    systemctl is-active pcs-gpio-shutdown.service 2>/dev/null || true
    if [[ -x "${DRIVER_TARGET}" ]]; then
        "${DRIVER_TARGET}" check
    fi
}

install_service() {
    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Run this script as the normal Pi user, not with sudo."
        exit 1
    fi
    [[ -f "${DRIVER_SOURCE}" ]] || { echo "ERROR: Missing ${DRIVER_SOURCE}"; exit 1; }
    [[ -f "${UNIT_SOURCE}" ]] || { echo "ERROR: Missing ${UNIT_SOURCE}"; exit 1; }
    [[ -f "${STARTUP_SCRIPT_SOURCE}" ]] || { echo "ERROR: Missing ${STARTUP_SCRIPT_SOURCE}"; exit 1; }
    [[ -f "${STARTUP_UNIT_SOURCE}" ]] || { echo "ERROR: Missing ${STARTUP_UNIT_SOURCE}"; exit 1; }
    [[ -f "${SHUTDOWN_UNIT_SOURCE}" ]] || { echo "ERROR: Missing ${SHUTDOWN_UNIT_SOURCE}"; exit 1; }

    if ! python3 -c 'import spidev' 2>/dev/null; then
        echo "Installing Python spidev support..."
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-spidev
    fi

    if [[ ! -e /dev/spidev0.0 ]]; then
        if command -v raspi-config >/dev/null 2>&1; then
            echo "Enabling Raspberry Pi SPI0..."
            sudo raspi-config nonint do_spi 0
            sudo udevadm settle 2>/dev/null || true
            for _attempt in 1 2 3 4 5; do
                [[ -e /dev/spidev0.0 ]] && break
                sleep 1
            done
        else
            echo "ERROR: /dev/spidev0.0 is unavailable and raspi-config was not found."
            echo "Enable SPI0, reboot if requested, then repeat this command."
            exit 1
        fi
    fi

    [[ -e /dev/spidev0.0 ]] || {
        echo "ERROR: SPI0 was enabled but /dev/spidev0.0 is still unavailable."
        echo "Reboot the Pi, then repeat this command."
        exit 1
    }
    python3 -c 'import spidev' || { echo "ERROR: Python spidev is unavailable."; exit 1; }
    getent group spi >/dev/null || { echo "ERROR: Raspberry Pi spi group is unavailable."; exit 1; }

    sudo install -o root -g root -m 0755 "${DRIVER_SOURCE}" "${DRIVER_TARGET}"
    sudo install -o root -g root -m 0755 "${STARTUP_SCRIPT_SOURCE}" "${STARTUP_SCRIPT_TARGET}"
    sudo install -o root -g root -m 0644 "${UNIT_SOURCE}" "${UNIT_TARGET}"
    sudo install -o root -g root -m 0644 "${STARTUP_UNIT_SOURCE}" "${STARTUP_UNIT_TARGET}"
    sudo install -o root -g root -m 0644 "${SHUTDOWN_UNIT_SOURCE}" "${SHUTDOWN_UNIT_TARGET}"
    sudo install -d -o root -g root -m 0755 "${SHUTDOWN_MARKER_DIR}"
    sudo install -o root -g root -m 0644 /dev/null "${SHUTDOWN_MARKER}"
    sudo systemctl daemon-reload
    sudo systemctl enable pcs-gpio-startup.service
    sudo systemctl enable --now pcs-gpio-shutdown.service
    sudo systemctl enable --now pcs-gpio-stats.service
    check_state
}

case "${1:-}" in
    --install)
        install_service
        ;;
    --check)
        check_state
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
