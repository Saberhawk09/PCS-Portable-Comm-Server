#!/usr/bin/env bash
# Installing this component does not enable beacons, RF, or position sharing.
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in
    --install|--stage) ;;
    *) echo "Usage: $0 --stage|--install (install also starts the position service)"; exit 2 ;;
esac
if [[ "${EUID}" -eq 0 ]]; then
    echo "Run as the normal PCS user; sudo is used for fixed installation paths." >&2
    exit 1
fi
sudo install -d -o root -g root -m 0755 /usr/local/lib/pcs /etc/pcs
sudo install -o root -g root -m 0644 "${REPO_DIR}/scripts/pcs_location.py" /usr/local/lib/pcs/pcs_location.py
sudo install -o root -g root -m 0755 "${REPO_DIR}/scripts/pcs_location.py" /usr/local/sbin/pcs-location
sudo install -o root -g root -m 0644 "${REPO_DIR}/systemd/pcs-location.service" /etc/systemd/system/pcs-location.service
temporary="$(mktemp)"
trap 'rm -f -- "${temporary}"' EXIT
echo 'pi ALL=(root) NOPASSWD: /usr/local/sbin/pcs-location show, /usr/local/sbin/pcs-location set-from-stdin' >"${temporary}"
sudo visudo -cf "${temporary}"
sudo install -o root -g root -m 0440 "${temporary}" /etc/sudoers.d/pcs-location
sudo systemctl daemon-reload
if [[ "${1}" == "--install" ]]; then
    sudo systemctl enable --now pcs-location.service
    echo 'Position feed started at 127.0.0.1:2948. Raw receiver/time feed remains at 127.0.0.1:2947.'
else
    echo 'Position service staged, not enabled or started.'
fi
echo 'Existing APRS/Graywolf/Meshtastic configurations must use the position feed before enabling fallback.'
echo 'See docs/manual-position.md for the scoped upgrade and rollback steps. No radio service was restarted.'
