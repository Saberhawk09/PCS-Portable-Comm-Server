#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="/home/pi/Projects/PCS-Portable-Comm-Server"
SAMBA_CONFIG="/etc/samba/smb.conf"
SAMBA_DISCOVERY_CONFIG="/etc/samba/pcs-discovery.conf"
AVAHI_SERVICE="/etc/avahi/services/pcs-smb.service"
WSDD_SRC="${REPO_DIR}/scripts/pcs-wsdd.sh"
WSDD_DST="/usr/local/sbin/pcs-wsdd"
FIREWALL_SRC="${REPO_DIR}/scripts/pcs-wsdd-firewall.sh"
FIREWALL_DST="/usr/local/sbin/pcs-wsdd-firewall"
SERVICE_SRC="${REPO_DIR}/systemd/pcs-wsdd.service"
SERVICE_DST="/etc/systemd/system/pcs-wsdd.service"
BACKUP_DIR=""
BACKUP_FILES=(
    "${SAMBA_CONFIG}"
    "${SAMBA_DISCOVERY_CONFIG}"
    "${AVAHI_SERVICE}"
    "${WSDD_DST}"
    "${FIREWALL_DST}"
    "${SERVICE_DST}"
)

if [[ "${EUID}" -eq 0 ]]; then
    echo "ERROR: Run this script as the normal pi user, not with sudo." >&2
    exit 1
fi

if ! sudo -n true 2>/dev/null; then
    sudo -v
fi

for required in "${WSDD_SRC}" "${FIREWALL_SRC}" "${SERVICE_SRC}" "${SAMBA_CONFIG}"; do
    if [[ ! -f "${required}" ]]; then
        echo "ERROR: missing required file: ${required}" >&2
        exit 1
    fi
done
for executable in /usr/sbin/smbd /usr/bin/testparm /usr/sbin/avahi-daemon /usr/sbin/wsdd2 /usr/sbin/nft; do
    if [[ ! -x "${executable}" ]]; then
        echo "ERROR: required executable is missing: ${executable}" >&2
        exit 1
    fi
done

BACKUP_DIR="$(sudo mktemp -d /var/backups/pcs-share-discovery-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX)"
sudo chown root:root "${BACKUP_DIR}"
sudo chmod 0700 "${BACKUP_DIR}"
for path in "${BACKUP_FILES[@]}"; do
    name="$(printf '%s' "${path}" | sed 's#^/##; s#/#__#g')"
    if sudo test -e "${path}"; then
        sudo cp -a -- "${path}" "${BACKUP_DIR}/${name}"
    else
        sudo touch "${BACKUP_DIR}/${name}.missing"
    fi
done

VENDOR_WSDD_WAS_MASKED="$(systemctl is-enabled wsdd2.service 2>/dev/null || true)"
VENDOR_WSDD_WAS_ACTIVE="$(systemctl is-active wsdd2.service 2>/dev/null || true)"
PCS_WSDD_WAS_ENABLED="$(systemctl is-enabled pcs-wsdd.service 2>/dev/null || true)"
PCS_WSDD_WAS_ACTIVE="$(systemctl is-active pcs-wsdd.service 2>/dev/null || true)"

rollback() {
    local path name
    set +e
    echo "ERROR: PCS share discovery setup failed; restoring the previous configuration." >&2
    sudo systemctl disable --now pcs-wsdd.service >/dev/null 2>&1 || true
    if sudo test -x "${FIREWALL_DST}"; then
        sudo "${FIREWALL_DST}" remove >/dev/null 2>&1 || true
    fi
    for path in "${BACKUP_FILES[@]}"; do
        name="$(printf '%s' "${path}" | sed 's#^/##; s#/#__#g')"
        if sudo test -f "${BACKUP_DIR}/${name}.missing"; then
            sudo rm -f -- "${path}"
        else
            sudo cp -a -- "${BACKUP_DIR}/${name}" "${path}"
        fi
    done
    sudo systemctl daemon-reload || true
    if [[ "${PCS_WSDD_WAS_ENABLED}" == "enabled" ]]; then
        sudo systemctl enable pcs-wsdd.service >/dev/null 2>&1 || true
    fi
    if [[ "${PCS_WSDD_WAS_ACTIVE}" == "active" ]]; then
        sudo systemctl restart pcs-wsdd.service >/dev/null 2>&1 || true
    fi
    if [[ "${VENDOR_WSDD_WAS_MASKED}" != "masked" ]]; then
        sudo systemctl unmask wsdd2.service >/dev/null 2>&1 || true
    fi
    if [[ "${VENDOR_WSDD_WAS_MASKED}" == "enabled" ]]; then
        sudo systemctl enable wsdd2.service >/dev/null 2>&1 || true
    fi
    if [[ "${VENDOR_WSDD_WAS_ACTIVE}" == "active" ]]; then
        sudo systemctl enable --now wsdd2.service >/dev/null 2>&1 || true
    fi
    sudo systemctl restart smbd.service avahi-daemon.service >/dev/null 2>&1 || true
}
trap rollback ERR

sudo install -o root -g root -m 0755 "${WSDD_SRC}" "${WSDD_DST}"
sudo install -o root -g root -m 0755 "${FIREWALL_SRC}" "${FIREWALL_DST}"
sudo install -o root -g root -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"

cat <<'EOF' | sudo tee "${SAMBA_DISCOVERY_CONFIG}" >/dev/null
# Managed by setup-pcs-share-discovery.sh.
netbios name = PCS-FILE-SHARE
server string = Portable Comm Server
EOF
sudo chown root:root "${SAMBA_DISCOVERY_CONFIG}"
sudo chmod 0644 "${SAMBA_DISCOVERY_CONFIG}"

sudo sed -i '/# BEGIN PCS SAMBA DISCOVERY/,/# END PCS SAMBA DISCOVERY/d' "${SAMBA_CONFIG}"
sudo sed -i '0,/^\[global\][[:space:]]*$/{/^\[global\][[:space:]]*$/a\
# BEGIN PCS SAMBA DISCOVERY\
   include = /etc/samba/pcs-discovery.conf\
# END PCS SAMBA DISCOVERY
}' "${SAMBA_CONFIG}"

if ! sudo grep -Fqx "   include = /etc/samba/pcs-discovery.conf" "${SAMBA_CONFIG}"; then
    echo "ERROR: Samba [global] section was not found." >&2
    false
fi

sudo install -d -o root -g root -m 0755 /etc/avahi/services
cat <<'EOF' | sudo tee "${AVAHI_SERVICE}" >/dev/null
<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="no">PCS File Share</name>
  <service>
    <type>_smb._tcp</type>
    <port>445</port>
  </service>
</service-group>
EOF
sudo chown root:root "${AVAHI_SERVICE}"
sudo chmod 0644 "${AVAHI_SERVICE}"

sudo testparm -s >/dev/null
sudo systemctl disable --now wsdd2.service >/dev/null 2>&1 || true
sudo systemctl mask wsdd2.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart smbd.service avahi-daemon.service
sudo systemctl enable pcs-wsdd.service >/dev/null
sudo systemctl restart pcs-wsdd.service

sudo systemctl is-active --quiet smbd.service
sudo systemctl is-active --quiet avahi-daemon.service
sudo systemctl is-active --quiet pcs-wsdd.service
sudo systemctl is-enabled --quiet pcs-wsdd.service
sudo "${FIREWALL_DST}" check
[[ "$(sudo testparm -s --parameter-name='netbios name' 2>/dev/null)" == "PCS-FILE-SHARE" ]]
[[ "$(sudo testparm -s --parameter-name='server string' 2>/dev/null)" == "Portable Comm Server" ]]

trap - ERR
echo "PCS file-share discovery is installed."
echo "Windows discovery name: PCS-FILE-SHARE"
echo "SMB host alias: PCS-FILE-SHARE"
echo "WSD and LLMNR exposure is limited to eth0 and wlan0; WireGuard and cellular are excluded."
echo "Rollback snapshot: ${BACKUP_DIR}"
