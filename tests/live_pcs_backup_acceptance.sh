#!/usr/bin/env bash

set -Eeuo pipefail

TEST_ID="${1:-}"
[[ "${TEST_ID}" =~ ^[A-Za-z0-9-]{8,64}$ ]] || {
    echo "Usage: $0 SAFE_UNIQUE_TEST_ID" >&2
    exit 2
}

SOURCE_DIR="/mnt/pcs-usb/PCS-Share/.pcs-backup-acceptance-${TEST_ID}"
ROLLING_DIR="/srv/pcs-share-backup/.pcs-backup-acceptance-${TEST_ID}"
HISTORY_DIR="/srv/pcs-share-backup/PCS-Backup-History"
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

printf '%s' '{"enabled":false,"interval_minutes":10,"keep_history":true}' \
    | sudo -n /usr/local/sbin/pcs-backup-config set-from-stdin >/dev/null

install -d -m 0770 "${SOURCE_DIR}"
printf 'version-one\n' >"${SOURCE_DIR}/${MARKER}"
sudo -n /usr/local/sbin/pcs-web-action sync-backup >/tmp/pcs-backup-acceptance-1.log
SNAPSHOT_ONE="$(sudo -n find "${HISTORY_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.incomplete-*' -printf '%p\n' | LC_ALL=C sort | tail -n 1)"
[[ "$(sudo -n cat "${SNAPSHOT_ONE}/.pcs-backup-acceptance-${TEST_ID}/${MARKER}")" == "version-one" ]]

sleep 1
printf 'version-two\n' >"${SOURCE_DIR}/${MARKER}"
sudo -n /usr/local/sbin/pcs-web-action sync-backup >/tmp/pcs-backup-acceptance-2.log
SNAPSHOT_TWO="$(sudo -n find "${HISTORY_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.incomplete-*' -printf '%p\n' | LC_ALL=C sort | tail -n 1)"
[[ "${SNAPSHOT_TWO}" != "${SNAPSHOT_ONE}" ]]
[[ "$(sudo -n cat "${SNAPSHOT_ONE}/.pcs-backup-acceptance-${TEST_ID}/${MARKER}")" == "version-one" ]]
[[ "$(sudo -n cat "${SNAPSHOT_TWO}/.pcs-backup-acceptance-${TEST_ID}/${MARKER}")" == "version-two" ]]

sleep 1
rm -f -- "${SOURCE_DIR}/${MARKER}"
rmdir -- "${SOURCE_DIR}"
sudo -n /usr/local/sbin/pcs-web-action sync-backup >/tmp/pcs-backup-acceptance-3.log
SNAPSHOT_THREE="$(sudo -n find "${HISTORY_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.incomplete-*' -printf '%p\n' | LC_ALL=C sort | tail -n 1)"
[[ "${SNAPSHOT_THREE}" != "${SNAPSHOT_TWO}" ]]
[[ "$(sudo -n cat "${ROLLING_DIR}/${MARKER}")" == "version-two" ]]
[[ ! -e "${SNAPSHOT_THREE}/.pcs-backup-acceptance-${TEST_ID}/${MARKER}" ]]

echo "ADDITIVE_DELETE_PROTECTION_OK"
echo "RETAINED_VERSION_HISTORY_OK"
basename "${SNAPSHOT_ONE}"
basename "${SNAPSHOT_TWO}"
basename "${SNAPSHOT_THREE}"
