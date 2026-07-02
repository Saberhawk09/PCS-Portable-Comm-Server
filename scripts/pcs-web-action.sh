#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
USB_MOUNT="/mnt/pcs-usb"
PRIMARY_SHARE="/mnt/pcs-usb/PCS-Share"
BACKUP_SHARE="/srv/pcs-share-backup"
PCS_USER="pi"

ACTION="${1:-}"

header() {
    echo
    echo "=== $* ==="
    echo
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "ERROR: This dispatcher must run as root."
        exit 1
    fi
}

show_help() {
    cat <<EOF
PCS web action dispatcher

Allowed actions:

  status
  self-test
  storage-status
  sync-backup
  mount-usb
  safe-unmount-usb
  restart-services
  restart-samba
  restart-chrony
  restart-logs
EOF
}

ensure_repo() {
    if [[ ! -d "${REPO_DIR}" ]]; then
        echo "ERROR: repo directory missing: ${REPO_DIR}"
        exit 1
    fi
}

ensure_usb_mounted() {
    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "ERROR: USB primary storage is not mounted at ${USB_MOUNT}"
        echo
        echo "Try the mount-usb action first."
        exit 1
    fi
}

sync_backup() {
    header "Sync USB Primary to SD Backup"

    ensure_usb_mounted

    if [[ ! -d "${PRIMARY_SHARE}" ]]; then
        echo "ERROR: primary share missing: ${PRIMARY_SHARE}"
        exit 1
    fi

    if [[ ! -d "${BACKUP_SHARE}" ]]; then
        echo "ERROR: backup share missing: ${BACKUP_SHARE}"
        exit 1
    fi

    echo "Primary: ${PRIMARY_SHARE}"
    echo "Backup:  ${BACKUP_SHARE}"
    echo

    rsync -rtvh --delete \
        --modify-window=2 \
        --exclude "LAST_SYNC.txt" \
        "${PRIMARY_SHARE}/" \
        "${BACKUP_SHARE}/"

    chown -R "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}"
    chmod -R u+rwX,g+rwX,o-rwx "${BACKUP_SHARE}"
    find "${BACKUP_SHARE}" -type d -exec chmod 2775 {} \;

    date | tee "${BACKUP_SHARE}/LAST_SYNC.txt" >/dev/null
    chown "${PCS_USER}:${PCS_USER}" "${BACKUP_SHARE}/LAST_SYNC.txt"

    echo
    echo "Backup sync complete."
    echo "LAST_SYNC:"
    cat "${BACKUP_SHARE}/LAST_SYNC.txt"
}

mount_usb() {
    header "Mount USB Primary Share"

    mkdir -p "${USB_MOUNT}"

    systemctl daemon-reload || true

    if findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "${USB_MOUNT} is already mounted."
    else
        mount "${USB_MOUNT}"
    fi

    systemctl restart smbd

    echo
    echo "USB mount:"
    findmnt "${USB_MOUNT}" || true

    echo
    echo "Samba share paths:"
    testparm -s 2>/dev/null | grep -A8 -E '^\[PCS-Share\]|^\[PCS-Backup\]' || true
}

safe_unmount_usb() {
    header "Safely Unmount USB Primary Share"

    if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
        echo "${USB_MOUNT} is not mounted."
        echo "Nothing to unmount."
        exit 0
    fi

    USB_SOURCE="$(findmnt -n -o SOURCE "${USB_MOUNT}" || true)"
    USB_PARENT=""

    if [[ -n "${USB_SOURCE}" ]]; then
        USB_PARENT="$(lsblk -no PKNAME "${USB_SOURCE}" 2>/dev/null | head -n 1 || true)"
    fi

    echo "USB source: ${USB_SOURCE:-unknown}"
    echo "USB parent: ${USB_PARENT:-unknown}"
    echo

    echo "Step 1: syncing primary share to SD backup..."
    sync_backup

    echo
    echo "Step 2: stopping Samba..."
    systemctl stop smbd

    echo
    echo "Step 3: flushing writes..."
    sync

    echo
    echo "Step 4: unmounting ${USB_MOUNT}..."
    umount "${USB_MOUNT}"

    echo
    echo "Step 5: restarting Samba so PCS-Backup remains available..."
    systemctl start smbd

    echo
    echo "Step 6: powering off USB device if possible..."

    if [[ -n "${USB_PARENT}" && -b "/dev/${USB_PARENT}" ]] && command -v udisksctl >/dev/null 2>&1; then
        udisksctl power-off -b "/dev/${USB_PARENT}" || true
    else
        echo "Could not power off USB device automatically."
        echo "It should still be safe to remove after successful unmount."
    fi

    echo
    echo "USB primary share safely unmounted."
    echo "You may remove the USB stick."
}

storage_status() {
    header "PCS Storage Status"

    echo "--- Block Devices ---"
    lsblk -o NAME,MODEL,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RM
    echo

    echo "--- Mounts ---"
    findmnt "${USB_MOUNT}" || echo "${USB_MOUNT} is not mounted."
    echo

    echo "--- Filesystem Usage ---"
    df -h / "${USB_MOUNT}" "${BACKUP_SHARE}" 2>/dev/null || true
    echo

    echo "--- Samba Shares ---"
    testparm -s 2>/dev/null | grep -A8 -E '^\[PCS-Share\]|^\[PCS-Backup\]' || true
    echo

    echo "--- Primary Share ---"
    if [[ -d "${PRIMARY_SHARE}" ]]; then
        ls -la "${PRIMARY_SHARE}"
    else
        echo "Missing: ${PRIMARY_SHARE}"
    fi
    echo

    echo "--- Backup Share ---"
    if [[ -d "${BACKUP_SHARE}" ]]; then
        ls -la "${BACKUP_SHARE}"
        echo
        if [[ -f "${BACKUP_SHARE}/LAST_SYNC.txt" ]]; then
            echo "Last sync:"
            cat "${BACKUP_SHARE}/LAST_SYNC.txt"
        else
            echo "No LAST_SYNC.txt found."
        fi
    else
        echo "Missing: ${BACKUP_SHARE}"
    fi
}

require_root
ensure_repo

case "${ACTION}" in
    status)
        header "PCS Status"
        cd "${REPO_DIR}"
        ./scripts/pcs-status.sh
        ;;

    self-test)
        header "PCS Self-Test"
        runuser -u "${PCS_USER}" -- bash -lc "cd '${REPO_DIR}' && XDG_RUNTIME_DIR=/run/user/1000 ./scripts/pcs-self-test.sh"
        ;;

    storage-status)
        storage_status
        ;;

    sync-backup)
        sync_backup
        ;;

    mount-usb)
        mount_usb
        ;;

    safe-unmount-usb)
        safe_unmount_usb
        ;;

    restart-services)
        header "Restart PCS Services"
        systemctl start pcs-restart-services.service
        systemctl status pcs-restart-services.service --no-pager -l || true
        ;;

    restart-samba)
        header "Restart Samba"
        systemctl restart smbd
        systemctl status smbd --no-pager -l || true
        ;;

    restart-chrony)
        header "Restart Chrony"
        systemctl restart chrony
        systemctl status chrony --no-pager -l || true
        chronyc tracking || true
        ;;

    restart-logs)
        header "PCS Restart Service Logs"
        journalctl -u pcs-restart-services.service -n 120 --no-pager
        ;;

    ""|-h|--help|help)
        show_help
        ;;

    *)
        echo "ERROR: unknown or disallowed action: ${ACTION}"
        echo
        show_help
        exit 2
        ;;
esac
