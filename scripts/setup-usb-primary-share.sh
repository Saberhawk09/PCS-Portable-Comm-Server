#!/usr/bin/env bash

set -Eeuo pipefail

USB_UUID="${1:-340B-4403}"
USB_MOUNT="/mnt/pcs-usb"
PRIMARY_SHARE_NAME="PCS-Share"
PRIMARY_SHARE_PATH="${USB_MOUNT}/PCS-Share"
OLD_PRIMARY_PATH="/srv/pcs-share"

SAMBA_CONFIG="/etc/samba/smb.conf"
PCS_USER="${SUDO_USER:-$USER}"

echo
echo "=== PCS USB Primary Share Setup ==="
echo

if [[ "${EUID}" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    ${SUDO} -v
fi

if ! command -v lsblk >/dev/null 2>&1; then
    echo "ERROR: lsblk not found."
    exit 1
fi

if ! command -v blkid >/dev/null 2>&1; then
    echo "ERROR: blkid not found."
    exit 1
fi

if ! command -v findmnt >/dev/null 2>&1; then
    echo "ERROR: findmnt not found."
    exit 1
fi

if ! command -v testparm >/dev/null 2>&1; then
    echo "ERROR: Samba/testparm not found. Run install-dependencies first."
    exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync not found. Installing rsync..."
    ${SUDO} apt-get update
    ${SUDO} apt-get install -y rsync
fi

USB_DEVICE="$(${SUDO} blkid -U "${USB_UUID}" 2>/dev/null || true)"

if [[ -z "${USB_DEVICE}" ]]; then
    echo "ERROR: Could not find a block device with UUID ${USB_UUID}"
    echo
    echo "Available block devices:"
    lsblk -o NAME,MODEL,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RM
    exit 1
fi

USB_TYPE="$(${SUDO} blkid -s TYPE -o value "${USB_DEVICE}" 2>/dev/null || true)"
USB_LABEL="$(${SUDO} blkid -s LABEL -o value "${USB_DEVICE}" 2>/dev/null || true)"

if [[ -z "${USB_TYPE}" ]]; then
    USB_TYPE="$(lsblk -no FSTYPE "${USB_DEVICE}" 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "${USB_LABEL}" ]]; then
    USB_LABEL="$(lsblk -no LABEL "${USB_DEVICE}" 2>/dev/null | head -n 1 || true)"
fi

USB_TYPE="${USB_TYPE,,}"

echo "USB UUID:     ${USB_UUID}"
echo "USB device:   ${USB_DEVICE}"
echo "USB label:    ${USB_LABEL:-unknown}"
echo "USB type:     ${USB_TYPE:-unknown}"
echo "Mount point:  ${USB_MOUNT}"
echo "Share path:   ${PRIMARY_SHARE_PATH}"
echo

if [[ "${USB_DEVICE}" == /dev/mmcblk0* ]]; then
    echo "ERROR: Refusing to use ${USB_DEVICE}; that looks like the Pi SD card."
    exit 1
fi

case "${USB_TYPE}" in
    vfat|exfat|ext4)
        ;;
    *)
        echo "ERROR: Unsupported filesystem type: ${USB_TYPE:-unknown}"
        echo "Supported for this script: vfat, exfat, ext4"
        echo
        echo "Debug info:"
        lsblk -o NAME,MODEL,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RM
        exit 1
        ;;
esac

PCS_UID="$(id -u "${PCS_USER}")"
PCS_GID="$(id -g "${PCS_USER}")"

echo "Creating USB mount point..."
${SUDO} mkdir -p "${USB_MOUNT}"

CURRENT_MOUNT="$(findmnt -n -S "UUID=${USB_UUID}" -o TARGET 2>/dev/null || true)"

if [[ -n "${CURRENT_MOUNT}" && "${CURRENT_MOUNT}" != "${USB_MOUNT}" ]]; then
    echo "USB is currently mounted at: ${CURRENT_MOUNT}"
    echo "Unmounting it so PCS can mount it at ${USB_MOUNT}..."
    ${SUDO} umount "${CURRENT_MOUNT}"
fi

echo
echo "Updating /etc/fstab..."

if [[ ! -f /etc/fstab.pre-pcs-usb-primary ]]; then
    ${SUDO} cp /etc/fstab /etc/fstab.pre-pcs-usb-primary
fi

${SUDO} sed -i '/# BEGIN PCS USB PRIMARY SHARE/,/# END PCS USB PRIMARY SHARE/d' /etc/fstab

case "${USB_TYPE}" in
    vfat)
        FSTAB_OPTIONS="nofail,noatime,uid=${PCS_UID},gid=${PCS_GID},umask=0002,utf8=1"
        ;;
    exfat)
        FSTAB_OPTIONS="nofail,noatime,uid=${PCS_UID},gid=${PCS_GID},umask=0002"
        ;;
    ext4)
        FSTAB_OPTIONS="defaults,nofail,noatime"
        ;;
esac

cat <<EOF | ${SUDO} tee -a /etc/fstab >/dev/null

# BEGIN PCS USB PRIMARY SHARE
UUID=${USB_UUID} ${USB_MOUNT} ${USB_TYPE} ${FSTAB_OPTIONS} 0 0
# END PCS USB PRIMARY SHARE
EOF

echo "Mounting USB..."
${SUDO} mount "${USB_MOUNT}"

if ! findmnt "${USB_MOUNT}" >/dev/null 2>&1; then
    echo "ERROR: ${USB_MOUNT} is not mounted."
    exit 1
fi

echo
echo "Creating primary share directory on USB..."
${SUDO} mkdir -p "${PRIMARY_SHARE_PATH}"
${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${PRIMARY_SHARE_PATH}" || true

if [[ "${USB_TYPE}" == "ext4" ]]; then
    ${SUDO} chmod 2775 "${PRIMARY_SHARE_PATH}"
fi

echo
echo "Copying existing primary share contents to USB, if present..."

if [[ -d "${OLD_PRIMARY_PATH}" ]]; then
    ${SUDO} rsync -avh "${OLD_PRIMARY_PATH}/" "${PRIMARY_SHARE_PATH}/" || true
    ${SUDO} chown -R "${PCS_USER}:${PCS_USER}" "${PRIMARY_SHARE_PATH}" || true
else
    echo "Old primary path not found: ${OLD_PRIMARY_PATH}"
fi

if [[ ! -f "${PRIMARY_SHARE_PATH}/README.txt" ]]; then
    cat <<EOF | ${SUDO} tee "${PRIMARY_SHARE_PATH}/README.txt" >/dev/null
PCS primary file share.

This share is stored on removable USB storage.

Backup mirror share:
  \\\\10.42.0.1\\PCS-Backup
EOF
    ${SUDO} chown "${PCS_USER}:${PCS_USER}" "${PRIMARY_SHARE_PATH}/README.txt" || true
fi

echo
echo "Updating Samba primary share path..."

if [[ ! -f "${SAMBA_CONFIG}.pre-pcs-usb-primary" ]]; then
    ${SUDO} cp "${SAMBA_CONFIG}" "${SAMBA_CONFIG}.pre-pcs-usb-primary"
fi

${SUDO} sed -i '/# BEGIN PCS SAMBA SHARE/,/# END PCS SAMBA SHARE/d' "${SAMBA_CONFIG}"
${SUDO} sed -i '/# BEGIN PCS SAMBA TEST SHARE/,/# END PCS SAMBA TEST SHARE/d' "${SAMBA_CONFIG}"

cat <<EOF | ${SUDO} tee -a "${SAMBA_CONFIG}" >/dev/null

# BEGIN PCS SAMBA SHARE
[${PRIMARY_SHARE_NAME}]
   path = ${PRIMARY_SHARE_PATH}
   browseable = yes
   read only = no
   guest ok = no
   valid users = ${PCS_USER}
   force user = ${PCS_USER}
   create mask = 0664
   directory mask = 2775
# END PCS SAMBA SHARE
EOF

echo
echo "Testing Samba config..."
${SUDO} testparm -s

echo
echo "Restarting Samba..."
${SUDO} systemctl restart smbd
${SUDO} systemctl enable smbd >/dev/null 2>&1 || true

echo
echo "USB primary share setup complete."
echo
echo "Primary share:"
echo "  \\\\10.42.0.1\\${PRIMARY_SHARE_NAME}"
echo
echo "Local path:"
echo "  ${PRIMARY_SHARE_PATH}"
echo
echo "USB mount:"
findmnt "${USB_MOUNT}" || true
echo
