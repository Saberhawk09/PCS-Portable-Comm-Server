#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal Pi user. The individual setup steps will ask for sudo when needed."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USB_UUID_DEFAULT="340B-4403"

echo
echo "=== PCS Base Setup ==="
echo
echo "Repository: ${REPO_DIR}"
echo
echo "This script configures the current Raspberry Pi OS install for PCS baseline use."
echo
echo "It will run:"
echo "  - Dependency installer"
echo "  - RTC setup"
echo "  - Client LAN / AP handoff setup on eth0"
echo "  - Manual cellular profile setup"
echo "  - Samba bootstrap share setup"
echo "  - Samba SD-card backup share setup"
echo "  - Optional USB primary share setup, if USB storage is present"
echo "  - Chrony LAN NTP setup"
echo "  - Optional EM7455/DW5811e NMEA GPS setup, if WWAN GPS hardware is present"
echo "  - Cockpit/systemd restart button install"
echo "  - PCS Control Panel setup"
echo "  - Port 80 dashboard redirect setup"
echo "  - Final PCS status and self-test"
echo
echo "It will not automatically configure:"
echo "  - Cellular data autoconnect"
echo "  - Future EM7565-specific settings"
echo
echo "EM7455/DW5811e GPS can be configured as an optional hardware step if the modem is present."
echo

read -r -p "Continue with PCS base setup? [y/N] " answer

case "${answer}" in
    y|Y|yes|YES)
        ;;
    *)
        echo "Aborted."
        exit 0
        ;;
esac

sudo -v
cd "${REPO_DIR}"

run_step() {
    local name="$1"
    local command="$2"

    echo
    echo "============================================================"
    echo "STEP: ${name}"
    echo "============================================================"
    echo

    bash -c "${command}"
}

run_optional_step() {
    local name="$1"
    local command="$2"

    echo
    echo "============================================================"
    echo "OPTIONAL STEP: ${name}"
    echo "============================================================"
    echo

    if bash -c "${command}"; then
        echo
        echo "Optional step completed: ${name}"
    else
        echo
        echo "WARNING: Optional step failed or was skipped: ${name}"
        echo "Continuing PCS base setup."
    fi
}

ensure_executable() {
    local script="$1"

    if [[ -f "${script}" ]]; then
        chmod +x "${script}"
    else
        echo "ERROR: Missing script: ${script}"
        exit 1
    fi
}

echo
echo "Making PCS scripts executable..."

ensure_executable "scripts/install-dependencies.sh"
ensure_executable "scripts/setup-rtc.sh"
ensure_executable "scripts/setup-router-wan-share.sh"
ensure_executable "scripts/setup-cellular-profile.sh"
ensure_executable "scripts/setup-test-samba-share.sh"
ensure_executable "scripts/setup-samba-backup-share.sh"
ensure_executable "scripts/setup-usb-primary-share.sh"
ensure_executable "scripts/setup-chrony-lan-ntp.sh"
ensure_executable "scripts/restart-pcs-services.sh"
ensure_executable "scripts/setup-pcs-control-panel.sh"
ensure_executable "scripts/setup-dashboard-redirect.sh"
ensure_executable "scripts/setup-em7455-gps-nmea.sh"
ensure_executable "scripts/pcs-em7455-gps-nmea-start.py"
ensure_executable "scripts/pcs-web-action.sh"
ensure_executable "scripts/sync-pcs-share-to-backup.sh"
ensure_executable "scripts/pcs-self-test.sh"
ensure_executable "scripts/pcs-status.sh"

if [[ -d "web/pcs-control-panel" ]]; then
    chmod +x web/pcs-control-panel/*.py 2>/dev/null || true
fi

run_step "Install dependencies" "./scripts/install-dependencies.sh"

run_step "Configure RTC" "./scripts/setup-rtc.sh"

run_step "Configure client LAN/AP handoff on eth0" "./scripts/setup-router-wan-share.sh"

run_step "Configure manual cellular profile" "./scripts/setup-cellular-profile.sh"

echo
echo "============================================================"
echo "STEP: Configure Samba bootstrap share"
echo "============================================================"
echo
echo "This step may ask you to set or confirm a Samba password."
echo "This password is not stored in the repository."
echo

./scripts/setup-test-samba-share.sh

run_step "Configure Samba SD-card backup share" "./scripts/setup-samba-backup-share.sh"

echo
echo "============================================================"
echo "OPTIONAL STEP: Configure USB primary Samba share"
echo "============================================================"
echo

USB_DEVICE=""
USB_DEVICE_REASON=""

detect_usb_storage_candidates() {
    while read -r name type fstype pkname rm hotplug tran; do
        [[ -z "${name}" ]] && continue
        [[ -z "${fstype}" ]] && continue

        parent_tran="${tran}"
        parent_rm="${rm}"
        parent_hotplug="${hotplug}"

        if [[ "${type}" == "part" && -n "${pkname}" ]]; then
            parent_device="${pkname}"
            if [[ "${parent_device}" != /dev/* ]]; then
                parent_device="/dev/${parent_device}"
            fi

            parent_tran="$(lsblk -dnro TRAN "${parent_device}" 2>/dev/null || true)"
            parent_rm="$(lsblk -dnro RM "${parent_device}" 2>/dev/null || true)"
            parent_hotplug="$(lsblk -dnro HOTPLUG "${parent_device}" 2>/dev/null || true)"
        fi

        if [[ "${parent_tran}" == "usb" || "${parent_rm}" == "1" || "${parent_hotplug}" == "1" ]]; then
            echo "${name}"
        fi
    done < <(lsblk -rpno NAME,TYPE,FSTYPE,PKNAME,RM,HOTPLUG,TRAN 2>/dev/null)
}

echo "Scanning for USB storage..."

if sudo blkid -U "${USB_UUID_DEFAULT}" >/dev/null 2>&1; then
    USB_DEVICE="$(sudo blkid -U "${USB_UUID_DEFAULT}")"
    USB_DEVICE_REASON="matched default UUID ${USB_UUID_DEFAULT}"
else
    USB_CANDIDATES=()

    for usb_scan_attempt in $(seq 1 12); do
        udevadm settle --timeout=5 >/dev/null 2>&1 || true
        mapfile -t USB_CANDIDATES < <(detect_usb_storage_candidates | sort -u)

        if [[ "${#USB_CANDIDATES[@]}" -gt 0 ]]; then
            break
        fi

        if [[ "${usb_scan_attempt}" -lt 12 ]]; then
            echo "No USB storage filesystem detected yet; waiting... ${usb_scan_attempt}/12"
            sleep 5
        fi
    done

    if [[ "${#USB_CANDIDATES[@]}" -eq 1 ]]; then
        USB_DEVICE="${USB_CANDIDATES[0]}"
        USB_DEVICE_REASON="only detected removable/USB storage filesystem"
    elif [[ "${#USB_CANDIDATES[@]}" -gt 1 ]]; then
        echo "Multiple removable/USB storage filesystems were detected:"
        echo

        for i in "${!USB_CANDIDATES[@]}"; do
            device="${USB_CANDIDATES[$i]}"
            printf "  %s) %s\n" "$((i + 1))" "${device}"
            lsblk -no NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS "${device}" 2>/dev/null || true
        done

        echo
        read -r -p "Select USB filesystem number for PCS-Share, or press Enter to skip: " usb_choice

        if [[ "${usb_choice}" =~ ^[0-9]+$ ]] \
            && (( usb_choice >= 1 )) \
            && (( usb_choice <= ${#USB_CANDIDATES[@]} )); then
            USB_DEVICE="${USB_CANDIDATES[$((usb_choice - 1))]}"
            USB_DEVICE_REASON="selected from detected removable/USB storage filesystems"
        else
            echo "No valid USB filesystem selected."
        fi
    fi
fi

if [[ -n "${USB_DEVICE}" ]]; then
    echo
    echo "USB storage candidate:"
    echo "  Device: ${USB_DEVICE}"
    echo "  Reason: ${USB_DEVICE_REASON}"
    echo
    lsblk -o NAME,PATH,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,TRAN "${USB_DEVICE}" 2>/dev/null || true
    echo

    read -r -p "Configure this USB device as PCS-Share primary storage? [Y/n] " usb_answer

    case "${usb_answer}" in
        n|N|no|NO)
            echo "Skipping USB primary share setup."
            ;;
        *)
            ./scripts/setup-usb-primary-share.sh "${USB_DEVICE}"
            ;;
    esac
else
    echo "No removable/USB storage filesystem was detected."
    echo "Skipping USB primary share setup."
    echo "You can run this later with a specific device, for example:"
    echo "  ./scripts/setup-usb-primary-share.sh /dev/sda1"
fi

run_step "Configure Chrony LAN NTP" "./scripts/setup-chrony-lan-ntp.sh"

echo
echo "============================================================"
echo "OPTIONAL STEP: Configure EM7455/DW5811e NMEA GPS"
echo "============================================================"
echo
echo "This optional step configures:"
echo "  - EM7455/DW5811e GPS NMEA on /dev/ttyUSB1"
echo "  - gpsd reading /dev/ttyUSB1"
echo "  - Chrony reading gpsd SHM refclock 0"
echo
echo "Use this only when the EM7455/DW5811e WWAN modem and GPS antenna are installed."
echo

if [[ -x "./scripts/setup-em7455-gps-nmea.sh" ]]; then
    read -r -p "Configure EM7455/DW5811e NMEA GPS now? [y/N] " gps_answer

    case "${gps_answer}" in
        y|Y|yes|YES)
            if ./scripts/setup-em7455-gps-nmea.sh; then
                echo "EM7455/DW5811e NMEA GPS setup completed."
            else
                echo
                echo "WARNING: EM7455/DW5811e NMEA GPS setup failed."
                echo "Continuing PCS base setup so dashboard/control panel installation can still complete."
                echo "You can retry GPS setup later with:"
                echo "  ./scripts/setup-em7455-gps-nmea.sh"
            fi
            ;;
        *)
            echo "Skipping EM7455/DW5811e NMEA GPS setup."
            echo "You can run this later:"
            echo "  ./scripts/setup-em7455-gps-nmea.sh"
            ;;
    esac
else
    echo "WARNING: scripts/setup-em7455-gps-nmea.sh not found or not executable."
    echo "Skipping EM7455/DW5811e NMEA GPS setup."
fi

echo
echo "============================================================"
echo "STEP: Install Cockpit/systemd restart button"
echo "============================================================"
echo

if [[ -f "systemd/pcs-restart-services.service" ]]; then
    echo "Installing pcs-restart-services.service..."
    sudo cp systemd/pcs-restart-services.service /etc/systemd/system/pcs-restart-services.service
    sudo systemctl daemon-reload

    echo "Disabling pcs-restart-services.service for boot."
    echo "It is intended to be started manually, not run automatically."
    sudo systemctl disable pcs-restart-services.service >/dev/null 2>&1 || true

    echo "Installed service restart action:"
    echo "  pcs-restart-services.service"
else
    echo "WARNING: systemd/pcs-restart-services.service not found."
    echo "Skipping Cockpit/systemd restart button install."
fi

run_step "Install PCS Control Panel" "./scripts/setup-pcs-control-panel.sh"

run_optional_step "Install port 80 dashboard redirect" "./scripts/setup-dashboard-redirect.sh"

echo
echo "============================================================"
echo "STEP: Final PCS status"
echo "============================================================"
echo

./scripts/pcs-status.sh || true

echo
echo "============================================================"
echo "STEP: PCS self-test"
echo "============================================================"
echo

if ./scripts/pcs-self-test.sh; then
    echo
    echo "PCS base setup completed and self-test passed."
else
    echo
    echo "PCS base setup completed, but self-test reported warnings or failures."
    echo "Review the output above."
fi

echo
echo "=== PCS Base Setup Complete ==="
echo
echo "Useful client access addresses from behind the PCS access point:"
echo
echo "PCS Dashboard:"
echo "  http://10.42.0.1"
echo
echo "PCS Control Panel:"
echo "  http://10.42.0.1:8080"
echo
echo "Primary file share:"
echo "  \\\\10.42.0.1\\PCS-Share"
echo
echo "Backup file share:"
echo "  \\\\10.42.0.1\\PCS-Backup"
echo
echo "Cockpit:"
echo "  https://10.42.0.1:9090"
echo
echo "LAN NTP:"
echo "  10.42.0.1"
echo
echo "Windows NTP test:"
echo "  w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly"
echo
echo "Recommended reboot validation:"
echo "  sudo reboot"
echo "  cd ${REPO_DIR}"
echo "  ./scripts/pcs-self-test.sh"
echo "  ./scripts/pcs-status.sh"
echo
