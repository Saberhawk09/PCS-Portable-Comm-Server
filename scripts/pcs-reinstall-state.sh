#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_REPO="/home/pi/Projects/PCS-Portable-Comm-Server"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/pcs-reinstall-state.sh --check
  ./scripts/pcs-reinstall-state.sh --export /absolute/path/pcs-reinstall-state.tar.gz.enc
  ./scripts/pcs-reinstall-state.sh --extract /absolute/path/pcs-reinstall-state.tar.gz.enc /root/empty-directory

The export contains credentials and private keys and is encrypted with a
passphrase you enter interactively. Write it only to trusted removable storage,
retain the passphrase separately, and never commit or upload it.
EOF
}

extract_state() {
    local archive="$1"
    local destination="$2"
    local encrypted_temp
    local passphrase

    [[ "${archive}" == /* && "${destination}" == /* ]] \
        || { echo "ERROR: Archive and extraction directory must be absolute." >&2; exit 2; }
    [[ -f "${archive}" && ! -L "${archive}" ]] \
        || { echo "ERROR: Recovery archive must be a regular non-symlink file." >&2; exit 2; }
    sudo test -d "${destination}" && sudo test "$(sudo stat -c '%u:%a' "${destination}")" = "0:700" \
        || { echo "ERROR: Extraction directory must be root-owned mode 0700." >&2; exit 2; }
    if [[ -f "${archive}.sha256" ]]; then
        (cd "$(dirname "${archive}")" && sudo sha256sum --check "$(basename "${archive}.sha256")")
    fi
    encrypted_temp="$(sudo mktemp /run/pcs-reinstall-state.XXXXXX.tar.gz)"
    trap 'sudo rm -f -- "${encrypted_temp:-}"' EXIT
    sudo chmod 0600 "${encrypted_temp}"
    read -r -s -p "Reinstall archive passphrase: " passphrase
    echo
    if ! printf '%s' "${passphrase}" | sudo openssl enc -d -aes-256-cbc -pbkdf2 \
            -pass stdin -in "${archive}" \
            | sudo tee "${encrypted_temp}" >/dev/null; then
        echo "ERROR: Recovery archive decryption failed." >&2
        exit 1
    fi
    unset passphrase
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_archive.py" "${encrypted_temp}" "${destination}" --check
    sudo python3 "${REPO_DIR}/scripts/pcs_reinstall_archive.py" "${encrypted_temp}" "${destination}"
    sudo rm -f -- "${encrypted_temp}"
    trap - EXIT
}

STATE_PATHS=(
    "home/pi/Projects/PCS-Portable-Comm-Server/config/pcs-install.conf"
    "home/pi/Projects/PCS-Portable-Comm-Server/private-config"
    "home/pi/.ssh"
    "etc/pcs"
    "etc/pcs-control-panel"
    "etc/pcs-backup/config.json"
    "etc/pcs-stats-api"
    "etc/direwolf.conf"
    "etc/wireguard"
    "etc/ssh"
    "etc/shadow"
    "etc/samba"
    "etc/NetworkManager/system-connections"
    "var/lib/pcs-aprs-agent"
    "var/lib/graywolf/graywolf.db"
    "var/lib/alsa/asound.state"
    "var/lib/samba/private"
    "var/lib/bluetooth"
)

require_normal_pi_user() {
    if [[ "${EUID}" -eq 0 || "$(id -un)" != "pi" ]]; then
        echo "ERROR: Run this helper as the normal pi user, not with sudo." >&2
        exit 1
    fi
    if [[ "${REPO_DIR}" != "${EXPECTED_REPO}" ]]; then
        echo "ERROR: Expected repository at ${EXPECTED_REPO}; found ${REPO_DIR}." >&2
        exit 1
    fi
}

show_state() {
    local path
    local present=0
    local absent=0

    echo "=== PCS reinstall state inventory ==="
    for path in "${STATE_PATHS[@]}"; do
        if sudo test -e "/${path}"; then
            echo "PRESENT /${path}"
            present=$((present + 1))
        else
            echo "ABSENT  /${path}"
            absent=$((absent + 1))
        fi
    done
    echo "Inventory: ${present} present, ${absent} absent/unused"
    echo "External OpenWrt and Pi-Star native backups must be collected separately."
    echo "Raw Android app tokens are not recoverable; server-side pairing hashes are included."
    echo "The pi account password hash is included without copying other shadow entries on restore."
    echo "Record the archive passphrase separately and retain a known PCS login password."
}

export_state() {
    local archive="$1"
    local parent
    local list_file
    local mount_target
    local archive_name
    local digest
    local passphrase
    local passphrase_confirm
    local path
    local count=0

    [[ "${archive}" == /* ]] || { echo "ERROR: Export path must be absolute." >&2; exit 2; }
    [[ "${archive}" == *.tar.gz.enc ]] || { echo "ERROR: Export filename must end in .tar.gz.enc." >&2; exit 2; }
    case "${archive}" in
        "${REPO_DIR}"/*)
            echo "ERROR: Refusing to place credential-bearing state inside the repository." >&2
            exit 2
            ;;
    esac
    [[ ! -e "${archive}" && ! -e "${archive}.sha256" ]] \
        || { echo "ERROR: Refusing to overwrite ${archive} or its checksum." >&2; exit 2; }

    parent="$(dirname "${archive}")"
    [[ -d "${parent}" ]] || { echo "ERROR: Destination directory does not exist: ${parent}" >&2; exit 2; }
    mount_target="$(findmnt -n -o TARGET -T "${parent}")" \
        || { echo "ERROR: Destination is not on a mounted filesystem: ${parent}" >&2; exit 2; }
    if [[ "${mount_target}" == "/" ]]; then
        echo "ERROR: Refusing to save recovery state on the SD-card root filesystem." >&2
        exit 2
    fi
    command -v openssl >/dev/null 2>&1 \
        || { echo "ERROR: openssl is required for credential-bearing export encryption." >&2; exit 1; }

    list_file="$(mktemp)"
    trap 'rm -f -- "${list_file:-}"' EXIT
    for path in "${STATE_PATHS[@]}"; do
        if sudo test -e "/${path}"; then
            printf '%s\0' "${path}" >> "${list_file}"
            count=$((count + 1))
        fi
    done
    (( count > 0 )) || { echo "ERROR: No PCS reinstall state was found." >&2; exit 1; }

    read -r -s -p "Reinstall archive passphrase: " passphrase
    echo
    read -r -s -p "Confirm reinstall archive passphrase: " passphrase_confirm
    echo
    (( ${#passphrase} >= 12 )) || { echo "ERROR: Passphrase must contain at least 12 characters." >&2; exit 2; }
    [[ "${passphrase}" == "${passphrase_confirm}" ]] \
        || { echo "ERROR: Passphrases did not match." >&2; exit 2; }

    if ! sudo tar --create --gzip --acls --xattrs --numeric-owner \
            --directory=/ --null --files-from="${list_file}" --file=- \
            | PCS_REINSTALL_PASSPHRASE="${passphrase}" openssl enc -aes-256-cbc -pbkdf2 -salt \
                -pass env:PCS_REINSTALL_PASSPHRASE \
            | sudo tee "${archive}" >/dev/null; then
        sudo rm -f -- "${archive}"
        echo "ERROR: Reinstall-state export failed; removed the incomplete archive." >&2
        exit 1
    fi

    if ! PCS_REINSTALL_PASSPHRASE="${passphrase}" openssl enc -d -aes-256-cbc -pbkdf2 \
            -pass env:PCS_REINSTALL_PASSPHRASE -in "${archive}" \
            | tar -tzf - >/dev/null; then
        sudo rm -f -- "${archive}"
        echo "ERROR: Encrypted archive verification failed; removed the unusable archive." >&2
        exit 1
    fi
    unset passphrase passphrase_confirm
    sudo chmod 0600 "${archive}" 2>/dev/null || true

    archive_name="$(basename "${archive}")"
    digest="$(sudo sha256sum "${archive}" | awk '{print $1}')"
    printf '%s  %s\n' "${digest}" "${archive_name}" | sudo tee "${archive}.sha256" >/dev/null
    sudo chmod 0600 "${archive}.sha256" 2>/dev/null || true

    echo "Created credential-bearing reinstall archive: ${archive}"
    echo "Created checksum: ${archive}.sha256"
    echo "Encrypted archive decrypt/tar verification passed."
    echo "Archived ${count} present state paths. Keep the archive, checksum, and passphrase separate from the SD card being wiped."
}

require_normal_pi_user
sudo -n true || {
    echo "ERROR: PCS reinstall-state handling requires noninteractive sudo." >&2
    exit 1
}

case "${1:-}" in
    --check)
        [[ $# -eq 1 ]] || { usage; exit 2; }
        show_state
        ;;
    --export)
        [[ $# -eq 2 ]] || { usage; exit 2; }
        show_state
        export_state "$2"
        ;;
    --extract)
        [[ $# -eq 3 ]] || { usage; exit 2; }
        extract_state "$2" "$3"
        ;;
    *)
        usage
        exit 2
        ;;
esac
