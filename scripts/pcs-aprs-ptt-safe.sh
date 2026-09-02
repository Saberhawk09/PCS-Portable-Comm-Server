#!/usr/bin/env bash

set -Eeuo pipefail

CONFIG_FILE="${PCS_APRS_PTT_SAFE_CONFIG:-/etc/pcs/aprs/ptt-safe.conf}"
if [[ -r "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
fi

PTT_CHIP="${PCS_APRS_PTT_CHIP:-gpiochip0}"
PTT_LINE="${PCS_APRS_PTT_LINE:-6}"
MODE="${1:---hold}"

if [[ ! "${PTT_CHIP}" =~ ^[A-Za-z0-9_./:-]+$ ]]; then
    echo "ERROR: invalid PTT gpiochip: ${PTT_CHIP}" >&2
    exit 2
fi
if [[ ! "${PTT_LINE}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: invalid PTT GPIO line: ${PTT_LINE}" >&2
    exit 2
fi

case "${MODE}" in
    --hold)
        exec gpioset --chip "${PTT_CHIP}" --consumer pcs-ptt-safe \
            --bias pull-down "${PTT_LINE}=0"
        ;;
    --check)
        gpioinfo --chip "${PTT_CHIP}" "${PTT_LINE}" \
            | grep -F 'output' \
            | grep -F 'consumer="pcs-ptt-safe"'
        echo "PCS APRS PTT guard holds ${PTT_CHIP} line ${PTT_LINE} low."
        ;;
    -h|--help)
        echo "Usage: pcs-aprs-ptt-safe [--hold|--check]"
        ;;
    *)
        echo "Usage: pcs-aprs-ptt-safe [--hold|--check]" >&2
        exit 2
        ;;
esac
