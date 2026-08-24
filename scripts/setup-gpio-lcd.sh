#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_SOURCE="${REPO_DIR}/scripts/pcs_gpio.py"
UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-lcd.service"
SHUTDOWN_UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-shutdown.service"
DRIVER_TARGET="/usr/local/sbin/pcs-gpio"
UNIT_TARGET="/etc/systemd/system/pcs-gpio-lcd.service"
SHUTDOWN_UNIT_TARGET="/etc/systemd/system/pcs-gpio-shutdown.service"
SHUTDOWN_MARKER_DIR="/etc/pcs/gpio-shutdown"
SHUTDOWN_MARKER="${SHUTDOWN_MARKER_DIR}/lcd"

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-gpio-lcd.sh --install|--check

  --install  Install and start the HD44780 live status service.
  --check    Read the driver/service/GPIO state without changing it.
EOF
}

check_state() {
    echo "=== PCS GPIO LCD ==="
    test -x "${DRIVER_TARGET}" && echo "Driver: installed" || echo "Driver: missing"
    test -e /dev/gpiochip0 && echo "GPIO chip: available" || echo "GPIO chip: missing"
    python3 -c 'import gpiozero' 2>/dev/null \
        && echo "Python gpiozero: available" \
        || echo "Python gpiozero: missing"
    systemctl is-enabled pcs-gpio-lcd.service 2>/dev/null || true
    systemctl is-active pcs-gpio-lcd.service 2>/dev/null || true
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
    [[ -f "${SHUTDOWN_UNIT_SOURCE}" ]] || { echo "ERROR: Missing ${SHUTDOWN_UNIT_SOURCE}"; exit 1; }

    if ! python3 -c 'import gpiozero' 2>/dev/null; then
        echo "Installing Python gpiozero support..."
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-gpiozero
    fi

    [[ -e /dev/gpiochip0 ]] || { echo "ERROR: /dev/gpiochip0 is unavailable."; exit 1; }
    python3 -c 'import gpiozero' || { echo "ERROR: Python gpiozero is unavailable."; exit 1; }
    getent group gpio >/dev/null || { echo "ERROR: Raspberry Pi gpio group is unavailable."; exit 1; }

    sudo install -o root -g root -m 0755 "${DRIVER_SOURCE}" "${DRIVER_TARGET}"
    sudo install -o root -g root -m 0644 "${UNIT_SOURCE}" "${UNIT_TARGET}"
    sudo install -o root -g root -m 0644 "${SHUTDOWN_UNIT_SOURCE}" "${SHUTDOWN_UNIT_TARGET}"
    sudo install -d -o root -g root -m 0755 "${SHUTDOWN_MARKER_DIR}"
    sudo install -o root -g root -m 0644 /dev/null "${SHUTDOWN_MARKER}"
    sudo systemctl daemon-reload
    sudo systemctl enable --now pcs-gpio-shutdown.service
    sudo systemctl enable --now pcs-gpio-lcd.service
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
