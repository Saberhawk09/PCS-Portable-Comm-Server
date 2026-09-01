#!/usr/bin/env bash

set -Eeuo pipefail

TEST_ID="${1:-}"
[[ "${TEST_ID}" =~ ^[A-Za-z0-9-]{8,64}$ ]] || {
    echo "Usage: $0 SAFE_UNIQUE_TEST_ID" >&2
    exit 2
}

SOURCE_DIR="/mnt/pcs-usb/PCS-Share/.pcs-backup-timer-acceptance-${TEST_ID}"
ROLLING_DIR="/srv/pcs-share-backup/.pcs-backup-timer-acceptance-${TEST_ID}"
MARKER="marker.txt"
ORIGINAL_POLICY="$(sudo -n /usr/local/sbin/pcs-backup-config show)"
RESTORE_POLICY="$(printf '%s' "${ORIGINAL_POLICY}" | python3 -c '
import json, sys
value = json.load(sys.stdin)
value.pop("version", None)
print(json.dumps(value, separators=(",", ":")))
')"

[[ ! -e "${SOURCE_DIR}" && ! -e "${ROLLING_DIR}" ]] || {
    echo "ERROR: an acceptance-test path already exists; refusing to touch it." >&2
    exit 1
}

cleanup() {
    printf '%s' "${RESTORE_POLICY}" | sudo -n /usr/local/sbin/pcs-backup-config set-from-stdin >/dev/null 2>&1 || true
    rm -f -- "${SOURCE_DIR}/${MARKER}" 2>/dev/null || true
    rmdir -- "${SOURCE_DIR}" 2>/dev/null || true
    sudo -n rm -f -- "${ROLLING_DIR}/${MARKER}" 2>/dev/null || true
    sudo -n rmdir -- "${ROLLING_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

printf '%s' '{"enabled":true,"interval_minutes":1,"keep_history":false}' \
    | sudo -n /usr/local/sbin/pcs-backup-config set-from-stdin >/dev/null

for _ in $(seq 1 30); do
    [[ "$(systemctl is-active pcs-backup.service 2>/dev/null || true)" != "activating" ]] && break
    sleep 1
done

BEFORE="$(stat -c '%Y' /srv/pcs-share-backup/LAST_SYNC.txt)"
install -d -m 0770 "${SOURCE_DIR}"
printf 'automatic-timer-copy\n' >"${SOURCE_DIR}/${MARKER}"

for _ in $(seq 1 24); do
    if [[ -f "${ROLLING_DIR}/${MARKER}" ]] \
        && [[ "$(stat -c '%Y' /srv/pcs-share-backup/LAST_SYNC.txt)" -gt "${BEFORE}" ]]; then
        [[ "$(sudo -n cat "${ROLLING_DIR}/${MARKER}")" == "automatic-timer-copy" ]]
        echo "AUTOMATIC_ONE_MINUTE_TIMER_OK"
        exit 0
    fi
    sleep 5
done

echo "ERROR: automatic timer did not copy the marker within 120 seconds." >&2
exit 1
