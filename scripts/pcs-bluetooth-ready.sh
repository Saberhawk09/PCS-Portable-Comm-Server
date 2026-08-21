#!/usr/bin/env bash

set -Eeuo pipefail

power_retries="${PCS_BLUETOOTH_POWER_RETRIES:-20}"
power_delay="${PCS_BLUETOOTH_POWER_DELAY:-1}"

/usr/sbin/rfkill unblock bluetooth

for ((attempt = 1; attempt <= power_retries; attempt++)); do
    if /usr/bin/bluetoothctl show 2>/dev/null | /usr/bin/grep -q '^[[:space:]]*Powered: yes$'; then
        exit 0
    fi

    # BlueZ can briefly return org.bluez.Error.Busy while the controller is
    # finishing initialization after boot. Retry and verify actual state.
    /usr/bin/bluetoothctl power on >/dev/null 2>&1 || true
    /usr/bin/sleep "${power_delay}"
done

echo "ERROR: Bluetooth controller did not become powered after ${power_retries} attempts." >&2
exit 1
