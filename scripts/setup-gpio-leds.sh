#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_SOURCE="${REPO_DIR}/scripts/pcs_gpio.py"
UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-leds.service"
STARTUP_SCRIPT_SOURCE="${REPO_DIR}/scripts/pcs-gpio-startup.sh"
STARTUP_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-startup.service"
SHUTDOWN_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-shutdown.service"
DRIVER_TARGET="/usr/local/sbin/pcs-gpio"
UNIT_TARGET="/etc/systemd/system/pcs-gpio-leds.service"
STARTUP_SCRIPT_TARGET="/usr/local/sbin/pcs-gpio-startup"
STARTUP_UNIT_TARGET="/etc/systemd/system/pcs-gpio-startup.service"
SHUTDOWN_UNIT_TARGET="/etc/systemd/system/pcs-gpio-shutdown.service"
SHUTDOWN_MARKER_DIR="/etc/pcs/gpio-shutdown"
SHUTDOWN_MARKER="${SHUTDOWN_MARKER_DIR}/leds"
VENV_DIR="/opt/pcs-gpio-leds"
VENV_PYTHON="${VENV_DIR}/bin/python"
WS281X_VERSION="5.0.0"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-gpio-leds.sh --install|--check

  --install  Install and start the six-pixel GPIO21 WS2812 status service.
  --check    Read the driver/library/service state without writing to the LEDs.

GPIO21 uses the PCM output peripheral. It can coexist with the PCS GPIO18 PWM
fan and USB sound card, but it must not share PCM with an I2S sound device.
EOF
}

check_state() {
    echo "=== PCS GPIO21 WS2812 indicators ==="
    test -x "${DRIVER_TARGET}" && echo "Driver: installed" || echo "Driver: missing"
    test -x "${VENV_PYTHON}" && echo "Python environment: installed" || echo "Python environment: missing"
    if [[ -x "${VENV_PYTHON}" ]]; then
        "${VENV_PYTHON}" -c 'import rpi_ws281x' 2>/dev/null \
            && echo "Python rpi_ws281x: available" \
            || echo "Python rpi_ws281x: missing"
    fi
    echo "Data output: GPIO21 / physical pin 40 / PCM"
    echo "Pixel count: 6"
    systemctl is-enabled pcs-gpio-leds.service 2>/dev/null || true
    systemctl is-active pcs-gpio-leds.service 2>/dev/null || true
    systemctl is-enabled pcs-gpio-startup.service 2>/dev/null || true
    systemctl is-active pcs-gpio-startup.service 2>/dev/null || true
    systemctl is-enabled pcs-gpio-shutdown.service 2>/dev/null || true
    systemctl is-active pcs-gpio-shutdown.service 2>/dev/null || true
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

    if [[ ! -x "${VENV_PYTHON}" ]]; then
        echo "Installing the isolated WS2812 Python environment..."
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
            python3-venv python3-dev build-essential swig
        sudo python3 -m venv "${VENV_DIR}"
    fi
    if ! PCS_WS281X_REQUIRED_VERSION="${WS281X_VERSION}" "${VENV_PYTHON}" -c \
        'import importlib.metadata as metadata, os; assert metadata.version("rpi-ws281x") == os.environ["PCS_WS281X_REQUIRED_VERSION"]' \
        2>/dev/null; then
        sudo "${VENV_PYTHON}" -m pip install --upgrade "rpi-ws281x==${WS281X_VERSION}"
    fi
    sudo "${VENV_PYTHON}" -c 'import rpi_ws281x'

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
    sudo systemctl enable --now pcs-gpio-leds.service
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
