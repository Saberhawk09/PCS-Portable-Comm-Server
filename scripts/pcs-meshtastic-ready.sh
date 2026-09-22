#!/usr/bin/env bash

set -Eeuo pipefail

# Never let a failed or still-starting gateway inherit a connected status from
# the previous boot. The gateway recreates this file after it has established
# the current radio and MQTT sessions.
status_file="${PCS_MESHTASTIC_STATUS_FILE:-/var/lib/pcs-meshtastic/status.json}"
rm -f -- "${status_file}"

port="${PCS_MESHTASTIC_PORT:-}"
[[ -n "${port}" ]] || exit 0
[[ "${port}" == "/dev/ttyACM0" ]] || {
    echo "ERROR: unsupported Meshtastic serial port: ${port}"
    exit 1
}

device_deadline=$((SECONDS + 60))
while [[ ! -c "${port}" ]] && (( SECONDS < device_deadline )); do
    sleep 1
done
[[ -c "${port}" ]] || {
    echo "ERROR: Meshtastic radio did not appear on ${port} within 60 seconds."
    exit 1
}

# Opening the RAK repeatedly while its nRF52 stack is cold-booting can restart
# the serial configuration exchange. Wait once, without touching the port.
minimum_boot_seconds=110
boot_seconds="$(cut -d. -f1 /proc/uptime)"
if (( boot_seconds < minimum_boot_seconds )); then
    sleep $((minimum_boot_seconds - boot_seconds))
fi
