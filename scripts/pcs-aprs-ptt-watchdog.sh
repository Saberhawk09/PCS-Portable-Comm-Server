#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="${PCS_APRS_PTT_SAFE_CONFIG:-/etc/pcs/aprs/ptt-safe.conf}"
if [[ -r "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
fi

PTT_LINE="${PCS_APRS_PTT_LINE:-6}"
WATCH_SERVICE="${PCS_APRS_PTT_WATCH_SERVICE:-graywolf.service}"
MAX_HIGH_SECONDS="${PCS_APRS_PTT_MAX_HIGH_SECONDS:-8}"
SAMPLE_SECONDS="${PCS_APRS_PTT_SAMPLE_SECONDS:-0.1}"

if [[ ! "${PTT_LINE}" =~ ^[0-9]+$ ]] \
    || [[ ! "${MAX_HIGH_SECONDS}" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "${WATCH_SERVICE}" =~ ^[A-Za-z0-9_.@-]+[.]service$ ]]; then
    echo "ERROR: invalid PCS APRS PTT watchdog configuration." >&2
    exit 2
fi
command -v pinctrl >/dev/null 2>&1 || {
    echo "ERROR: pinctrl is required for the PCS APRS PTT watchdog." >&2
    exit 1
}

high_since=""
while :; do
    if ! systemctl is-active --quiet "${WATCH_SERVICE}"; then
        high_since=""
        sleep 1
        continue
    fi

    state="$(pinctrl get "${PTT_LINE}" 2>/dev/null || true)"
    if [[ "${state}" == *"| hi"* ]]; then
        now="$(date +%s)"
        [[ -n "${high_since}" ]] || high_since="${now}"
        if (( now - high_since >= MAX_HIGH_SECONDS )); then
            logger -p daemon.crit -t pcs-aprs-ptt-watchdog \
                "GPIO${PTT_LINE} stayed high for ${MAX_HIGH_SECONDS}s; forcing ${WATCH_SERVICE} off"
            systemctl kill --signal=SIGKILL --kill-whom=all "${WATCH_SERVICE}" 2>/dev/null || true
            systemctl stop --no-block "${WATCH_SERVICE}" 2>/dev/null || true
            systemctl start pcs-aprs-ptt-safe.service 2>/dev/null || true
            high_since=""
        fi
    else
        high_since=""
    fi
    sleep "${SAMPLE_SECONDS}"
done
