#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == "--check" ]]; then
    /usr/local/sbin/pcs-starlink check-config
    /usr/local/sbin/pcs-starlink status
    exit
fi
if [[ "${1:-}" != "--install" || "${EUID}" -ne 0 ]]; then
    echo "Usage: sudo $0 --install | --check" >&2
    exit 2
fi
python3 "${ROOT}/scripts/pcs_starlink.py" check-config
# Dependencies finish before any running PCS helper is replaced. They are
# isolated from system Python; repeat installation preserves operator config.
if ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -q 'install ok installed'; then
    apt-get update
    apt-get install -y python3-venv
fi
python3 -m venv /opt/pcs-starlink
/opt/pcs-starlink/bin/python -m pip install -r "${ROOT}/config/starlink-requirements.txt"
install -d -m 0755 /usr/local/lib/pcs /etc/pcs
install -o root -g root -m 0644 "${ROOT}/scripts/pcs_starlink.py" /usr/local/lib/pcs/pcs_starlink.py
install -o root -g root -m 0644 "${ROOT}/scripts/pcs_uplink_manager.py" /usr/local/lib/pcs/pcs_uplink_manager.py
install -o root -g root -m 0755 "${ROOT}/scripts/pcs_starlink_lifecycle.py" /usr/local/sbin/pcs-starlink-lifecycle
cat > /usr/local/sbin/pcs-starlink <<'WRAPPER'
#!/bin/sh
exec /opt/pcs-starlink/bin/python /usr/local/lib/pcs/pcs_starlink.py "$@"
WRAPPER
chmod 0755 /usr/local/sbin/pcs-starlink
if [[ ! -e /etc/pcs/starlink.json ]]; then
    install -o root -g root -m 0600 "${ROOT}/config/starlink.example.json" /etc/pcs/starlink.json
fi
/usr/local/sbin/pcs-starlink check-config
install -o root -g root -m 0644 "${ROOT}/systemd/pcs-starlink.service" /etc/systemd/system/pcs-starlink.service
systemctl daemon-reload
systemctl enable pcs-starlink.service
systemctl restart pcs-starlink.service
echo "Starlink telemetry installed. Existing config preserved; fresh installs are disabled."
echo "Install the matching control-panel release to expose cards and staged API actions."
