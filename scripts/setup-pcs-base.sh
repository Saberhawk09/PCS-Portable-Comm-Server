#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo."
    echo "Run it as the normal Pi user. The individual setup steps will ask for sudo when needed."
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
echo "  - Router WAN handoff setup"
echo "  - Temporary Samba test share setup"
echo "  - Chrony LAN NTP setup"
echo "  - Cockpit/systemd restart button install"
echo
echo "It will not configure:"
echo "  - WWAN/cellular modem connection"
echo "  - GPS/GNSS time source"
echo "  - Final removable-storage Samba share"
echo
echo "Those require hardware that may not be installed yet."
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
ensure_executable "scripts/setup-test-samba-share.sh"
ensure_executable "scripts/setup-chrony-lan-ntp.sh"
ensure_executable "scripts/restart-pcs-services.sh"
ensure_executable "scripts/pcs-self-test.sh"
ensure_executable "scripts/pcs-status.sh"

run_step "Install dependencies" "./scripts/install-dependencies.sh"

run_step "Configure RTC" "./scripts/setup-rtc.sh"

run_step "Configure router WAN handoff" "./scripts/setup-router-wan-share.sh"

echo
echo "============================================================"
echo "STEP: Configure temporary Samba test share"
echo "============================================================"
echo
echo "This step will ask you to set or confirm a Samba password."
echo "This password is not stored in the repository."
echo

./scripts/setup-test-samba-share.sh

run_step "Configure Chrony LAN NTP" "./scripts/setup-chrony-lan-ntp.sh"

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
    echo "It is intended to be started manually from Cockpit, not run automatically."
    sudo systemctl disable pcs-restart-services.service >/dev/null 2>&1 || true

    echo "Installed Cockpit service button:"
    echo "  Services → pcs-restart-services.service → Start"
else
    echo "WARNING: systemd/pcs-restart-services.service not found."
    echo "Skipping Cockpit/systemd restart button install."
fi

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
echo "Useful client access addresses from behind the PCS/test router:"
echo
echo "File share:"
echo "  \\\\10.42.0.1\\PCS-Share"
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
