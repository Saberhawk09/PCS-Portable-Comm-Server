#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER_SOURCE="${REPO_DIR}/scripts/pcs_gpio.py"
UNIT_SOURCE="${REPO_DIR}/systemd/pcs-gpio-fan.service"
DRIVER_TARGET="/usr/local/sbin/pcs-gpio"
UNIT_TARGET="/etc/systemd/system/pcs-gpio-fan.service"
PWM_OVERLAY="dtoverlay=pwm,pin=18,func=2"

if [[ -f /boot/firmware/config.txt ]]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
    BOOT_CONFIG="/boot/config.txt"
else
    BOOT_CONFIG=""
fi

usage() {
    cat <<'EOF'
Usage: ./scripts/setup-gpio-fan.sh --install|--check

  --install  Configure GPIO18 PWM0 and install the fail-safe thermal fan service.
  --check    Read the boot overlay, PWM, service, and current controller state.

The first install normally requires a reboot before hardware PWM is available.
EOF
}

check_state() {
    echo "=== PCS GPIO18 PWM fan ==="
    test -x "${DRIVER_TARGET}" && echo "Driver: installed" || echo "Driver: missing"
    test -n "${BOOT_CONFIG}" && echo "Boot config: ${BOOT_CONFIG}" || echo "Boot config: missing"
    if [[ -n "${BOOT_CONFIG}" ]]; then
        grep -Fqx "dtparam=audio=off" "${BOOT_CONFIG}" \
            && echo "Onboard analog audio: disabled for PWM0" \
            || echo "Onboard analog audio: not disabled"
        grep -Fqx "${PWM_OVERLAY}" "${BOOT_CONFIG}" \
            && echo "GPIO18 PWM0 overlay: configured" \
            || echo "GPIO18 PWM0 overlay: missing"
    fi
    test -d /sys/class/pwm/pwmchip0 \
        && echo "Hardware PWM chip: available" \
        || echo "Hardware PWM chip: unavailable; reboot may be required"
    if command -v pinctrl >/dev/null 2>&1; then
        pinctrl get 18 || true
    fi
    systemctl is-enabled pcs-gpio-fan.service 2>/dev/null || true
    systemctl is-active pcs-gpio-fan.service 2>/dev/null || true
    if [[ -r /run/pcs-gpio-fan/status.json ]]; then
        echo "Controller status:"
        cat /run/pcs-gpio-fan/status.json
    fi
}

append_boot_line() {
    local line="$1"
    if ! grep -Fqx "${line}" "${BOOT_CONFIG}"; then
        printf '%s\n' "${line}" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
    fi
}

install_service() {
    local conflicting_pwm

    if [[ "${EUID}" -eq 0 ]]; then
        echo "ERROR: Run this script as the normal Pi user, not with sudo."
        exit 1
    fi
    [[ -f "${DRIVER_SOURCE}" ]] || { echo "ERROR: Missing ${DRIVER_SOURCE}"; exit 1; }
    [[ -f "${UNIT_SOURCE}" ]] || { echo "ERROR: Missing ${UNIT_SOURCE}"; exit 1; }
    [[ -n "${BOOT_CONFIG}" ]] || { echo "ERROR: Raspberry Pi boot config was not found."; exit 1; }

    if grep -Eq '^dtoverlay=(gpio-fan|pwm-gpio-fan)(,|$)' "${BOOT_CONFIG}"; then
        echo "ERROR: An existing fan overlay already owns a GPIO/PWM channel."
        echo "Remove or reconcile it before installing PCS fan control."
        exit 1
    fi
    conflicting_pwm="$(grep -E '^dtoverlay=pwm(-2chan)?(,|$)' "${BOOT_CONFIG}" \
        | grep -Fvx "${PWM_OVERLAY}" || true)"
    if [[ -n "${conflicting_pwm}" ]]; then
        echo "ERROR: A conflicting hardware PWM overlay is already configured:"
        echo "${conflicting_pwm}"
        exit 1
    fi

    if [[ ! -f "${BOOT_CONFIG}.pcs-gpio-fan.bak" ]]; then
        sudo cp --preserve=mode,ownership,timestamps "${BOOT_CONFIG}" "${BOOT_CONFIG}.pcs-gpio-fan.bak"
    fi
    append_boot_line ""
    append_boot_line "# PCS GPIO18 hardware PWM fan control"
    append_boot_line "dtparam=audio=off"
    append_boot_line "${PWM_OVERLAY}"

    sudo install -o root -g root -m 0755 "${DRIVER_SOURCE}" "${DRIVER_TARGET}"
    sudo install -o root -g root -m 0644 "${UNIT_SOURCE}" "${UNIT_TARGET}"
    sudo systemctl daemon-reload
    sudo systemctl enable pcs-gpio-fan.service

    if [[ ! -d /sys/class/pwm/pwmchip0 ]]; then
        echo
        echo "GPIO18 hardware PWM is configured for the next boot."
        echo "Reboot, then repeat --check; the enabled fan service will start automatically."
        check_state
        return 0
    fi

    sudo systemctl restart pcs-gpio-fan.service
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
