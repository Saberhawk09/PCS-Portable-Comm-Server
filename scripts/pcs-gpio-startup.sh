#!/usr/bin/env bash

set -Eeuo pipefail

DRIVER="${PCS_GPIO_DRIVER:-/usr/local/sbin/pcs-gpio}"
LED_PYTHON="${PCS_GPIO_LED_PYTHON:-/opt/pcs-gpio-leds/bin/python}"
MARKER_DIR="${PCS_GPIO_MARKER_DIR:-/etc/pcs/gpio-shutdown}"
TIMEOUT_SECONDS="${PCS_GPIO_STARTUP_TIMEOUT_SECONDS:-90}"
POLL_SECONDS="${PCS_GPIO_STARTUP_POLL_SECONDS:-2}"

if [[ ! "${TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || (( TIMEOUT_SECONDS > 300 )); then
    echo "ERROR: PCS_GPIO_STARTUP_TIMEOUT_SECONDS must be an integer from 0 to 300" >&2
    exit 2
fi
if [[ ! "${POLL_SECONDS}" =~ ^[1-9][0-9]*$ ]] || (( POLL_SECONDS > 30 )); then
    echo "ERROR: PCS_GPIO_STARTUP_POLL_SECONDS must be an integer from 1 to 30" >&2
    exit 2
fi
[[ -x "${DRIVER}" ]] || { echo "ERROR: PCS GPIO driver is missing: ${DRIVER}" >&2; exit 1; }

echo "Showing PCS boot indicators."
write_failures=0
led_animation_pid=""

stop_led_animation() {
    if [[ -n "${led_animation_pid}" ]]; then
        kill "${led_animation_pid}" 2>/dev/null || true
        wait "${led_animation_pid}" 2>/dev/null || true
        led_animation_pid=""
    fi
}

on_exit() {
    result=$?
    stop_led_animation
    trap - EXIT
    exit "${result}"
}

trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -f "${MARKER_DIR}/lcd" ]]; then
    if ! "${DRIVER}" startup-state lcd --hardware --apply; then
        echo "ERROR: LCD boot indicator failed; normal display services will still be released." >&2
        write_failures=1
    fi
fi
if [[ -f "${MARKER_DIR}/leds" ]]; then
    if [[ ! -x "${LED_PYTHON}" ]]; then
        echo "ERROR: WS2812 Python is missing: ${LED_PYTHON}" >&2
        write_failures=1
    else
        "${LED_PYTHON}" "${DRIVER}" startup-state leds --repeat --hardware --apply &
        led_animation_pid=$!
    fi
fi

if [[ -n "${led_animation_pid}" ]] && ! kill -0 "${led_animation_pid}" 2>/dev/null; then
    if ! wait "${led_animation_pid}"; then
        echo "ERROR: WS2812 boot indicator failed; normal display services will still be released." >&2
        write_failures=1
    fi
    led_animation_pid=""
fi
if [[ -f "${MARKER_DIR}/matrix" ]]; then
    if ! "${DRIVER}" startup-state matrix --hardware --apply; then
        echo "ERROR: matrix boot indicator failed; normal display services will still be released." >&2
        write_failures=1
    fi
fi

deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
    if "${DRIVER}" startup-ready >/dev/null; then
        echo "PCS indicator health inputs are ready; handing off to normal status services."
        exit "${write_failures}"
    fi
    sleep "${POLL_SECONDS}"
done

echo "PCS startup grace period expired after ${TIMEOUT_SECONDS}s; handing off so persistent alerts remain visible."
"${DRIVER}" startup-ready || true
exit "${write_failures}"
