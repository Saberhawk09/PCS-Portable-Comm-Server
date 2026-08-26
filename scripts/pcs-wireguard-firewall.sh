#!/usr/bin/env bash

set -Eeuo pipefail

# Keep direct validation and NetworkManager/systemd invocations independent of
# the caller's PATH. Raspberry Pi OS installs nft under /usr/sbin.
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export PATH

CONFIG_FILE="${PCS_WIREGUARD_CONFIG:-/etc/pcs/wireguard-management.conf}"
MODE="${1:---apply}"

if [[ "${MODE}" == "--clear" || "${MODE}" == "-h" || "${MODE}" == "--help" ]]; then
    :
elif [[ -r "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
else
    echo "ERROR: PCS WireGuard config is not readable: ${CONFIG_FILE}" >&2
    exit 1
fi

WG_INTERFACE="${PCS_WG_INTERFACE:-wg-pcs}"
LAN_INTERFACE="${PCS_WG_LAN_INTERFACE:-eth0}"
LAN_NETWORK="${PCS_WG_LAN_NETWORK:-10.42.0.0/24}"
WG_ADDRESS="${PCS_WG_ADDRESS:-}"
WG_ALLOWED_IPS="${PCS_WG_ALLOWED_IPS:-}"
WG_ADMIN_SOURCES="${PCS_WG_ADMIN_SOURCES:-}"
PROTECTED_TCP_PORTS="${PCS_WG_PROTECTED_TCP_PORTS:-22,80,139,443,445,8080,9090}"
PCS_TABLE="pcs_wireguard"
NM_TABLE="nm-shared-${LAN_INTERFACE}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: $1 is required for PCS WireGuard isolation." >&2
        exit 1
    fi
}

validate_interface_name() {
    local label="$1"
    local value="$2"

    if [[ ! "${value}" =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; then
        echo "ERROR: invalid ${label} interface name: ${value}" >&2
        exit 2
    fi
}

validate_config() {
    validate_interface_name "WireGuard" "${WG_INTERFACE}"
    validate_interface_name "LAN" "${LAN_INTERFACE}"
    if [[ "${WG_INTERFACE}" != "wg-pcs" || "${LAN_INTERFACE}" != "eth0" ]]; then
        echo "ERROR: PCS WireGuard requires the fixed wg-pcs and eth0 interfaces." >&2
        exit 2
    fi

    python3 - \
        "${LAN_NETWORK}" \
        "${WG_ADDRESS}" \
        "${WG_ALLOWED_IPS}" \
        "${WG_ADMIN_SOURCES}" \
        "${PROTECTED_TCP_PORTS}" <<'PY'
import ipaddress
import sys

lan_text, address_text, allowed_text, admin_text, ports_text = sys.argv[1:]

try:
    lan = ipaddress.ip_network(lan_text, strict=True)
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid PCS LAN network: {exc}")
if str(lan) != "10.42.0.0/24":
    raise SystemExit("ERROR: PCS WireGuard isolation requires the commissioned 10.42.0.0/24 LAN")

try:
    address = ipaddress.ip_interface(address_text)
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid PCS WireGuard address: {exc}")
if address.version != 4 or address.network.prefixlen != 32:
    raise SystemExit("ERROR: PCS WireGuard address must be one IPv4 /32")
if address.ip in lan:
    raise SystemExit("ERROR: WireGuard address overlaps the PCS LAN")
management_supernet = ipaddress.ip_network(f"{address.ip}/24", strict=False)

def parse_host_routes(label, text):
    values = []
    for raw in text.split(","):
        raw = raw.strip()
        if not raw:
            raise SystemExit(f"ERROR: {label} contains an empty entry")
        try:
            network = ipaddress.ip_network(raw, strict=True)
        except ValueError as exc:
            raise SystemExit(f"ERROR: invalid {label} entry {raw!r}: {exc}")
        if network.version != 4 or network.prefixlen != 32:
            raise SystemExit(f"ERROR: {label} entries must be explicit IPv4 /32 host routes")
        if network.overlaps(lan):
            raise SystemExit(f"ERROR: {label} must not overlap the PCS LAN")
        if network.network_address not in management_supernet:
            raise SystemExit(f"ERROR: {label} entries must share the WireGuard address's management /24")
        values.append(str(network))
    return values

allowed = parse_host_routes("PCS_WG_ALLOWED_IPS", allowed_text)
admins = parse_host_routes("PCS_WG_ADMIN_SOURCES", admin_text)
if not set(admins).issubset(allowed):
    raise SystemExit("ERROR: every admin source must also appear in PCS_WG_ALLOWED_IPS")

ports = []
for raw in ports_text.split(","):
    raw = raw.strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise SystemExit(f"ERROR: invalid protected TCP port: {raw!r}")
    ports.append(int(raw))
if len(set(ports)) != len(ports):
    raise SystemExit("ERROR: protected TCP ports must be unique")
required_ports = {22, 80, 139, 443, 445, 8080, 9090}
if set(ports) != required_ports:
    raise SystemExit("ERROR: protected TCP ports must exactly cover SSH, web/admin, Samba, redirect, and Cockpit")
PY
}

delete_nm_compat_rules() {
    local handle

    if ! nft list chain ip "${NM_TABLE}" filter_forward >/dev/null 2>&1; then
        return 0
    fi

    while read -r handle; do
        [[ "${handle}" =~ ^[0-9]+$ ]] || continue
        nft delete rule ip "${NM_TABLE}" filter_forward handle "${handle}"
    done < <(
        nft -a list chain ip "${NM_TABLE}" filter_forward \
            | awk '/comment "pcs-wg-(to-lan|from-lan)"/ { print $NF }'
    )
}

install_nm_compat_rules() {
    local admin_source

    if ! nft list chain ip "${NM_TABLE}" filter_forward >/dev/null 2>&1; then
        echo "NetworkManager shared-LAN table ${NM_TABLE} is not active; dispatcher will add compatibility rules when ${LAN_INTERFACE} comes up."
        return 0
    fi

    delete_nm_compat_rules

    nft insert rule ip "${NM_TABLE}" filter_forward \
        iifname "${LAN_INTERFACE}" oifname "${WG_INTERFACE}" \
        ct state established,related accept \
        comment "pcs-wg-from-lan"

    IFS=',' read -r -a admin_sources <<<"${WG_ADMIN_SOURCES}"
    for admin_source in "${admin_sources[@]}"; do
        admin_source="${admin_source//[[:space:]]/}"
        nft insert rule ip "${NM_TABLE}" filter_forward \
            iifname "${WG_INTERFACE}" oifname "${LAN_INTERFACE}" \
            ip saddr "${admin_source}" ip daddr "${LAN_NETWORK}" \
            ct state new,established,related accept \
            comment "pcs-wg-to-lan"
    done
}

clear_rules() {
    delete_nm_compat_rules
    nft delete table inet "${PCS_TABLE}" 2>/dev/null || true
}

apply_rules() {
    local admin_elements
    local port_elements

    admin_elements="${WG_ADMIN_SOURCES//,/ , }"
    port_elements="${PROTECTED_TCP_PORTS//,/ , }"

    nft delete table inet "${PCS_TABLE}" 2>/dev/null || true
    nft -f - <<EOF
table inet ${PCS_TABLE} {
    set admin_sources {
        type ipv4_addr
        flags interval
        elements = { ${admin_elements} }
    }

    set protected_tcp_ports {
        type inet_service
        elements = { ${port_elements} }
    }

    chain input {
        type filter hook input priority -20; policy accept;
        tcp dport @protected_tcp_ports iifname "lo" accept
        tcp dport @protected_tcp_ports iifname "${LAN_INTERFACE}" ip saddr ${LAN_NETWORK} accept
        tcp dport @protected_tcp_ports iifname "${WG_INTERFACE}" ip saddr @admin_sources accept
        tcp dport @protected_tcp_ports drop
    }

    chain forward {
        type filter hook forward priority -20; policy accept;
        iifname "${LAN_INTERFACE}" oifname "${WG_INTERFACE}" ct state established,related accept
        iifname "${LAN_INTERFACE}" oifname "${WG_INTERFACE}" drop
        iifname "${WG_INTERFACE}" oifname "${LAN_INTERFACE}" ip saddr @admin_sources ip daddr ${LAN_NETWORK} ct state new,established,related accept
        iifname "${WG_INTERFACE}" drop
        oifname "${WG_INTERFACE}" drop
    }
}
EOF

    install_nm_compat_rules
    echo "PCS WireGuard isolation is active: trusted management may enter; PCS LAN cannot initiate through ${WG_INTERFACE}."
}

check_rules() {
    local rules
    local nm_rules
    local admin_source

    rules="$(nft list table inet "${PCS_TABLE}")"
    grep -Fq 'set admin_sources' <<<"${rules}"
    grep -Fq 'set protected_tcp_ports' <<<"${rules}"
    grep -Fq "iifname \"${LAN_INTERFACE}\" oifname \"${WG_INTERFACE}\" drop" <<<"${rules}"
    grep -Fq "iifname \"${WG_INTERFACE}\" drop" <<<"${rules}"
    grep -Fq 'tcp dport @protected_tcp_ports drop' <<<"${rules}"

    if nft list chain ip "${NM_TABLE}" filter_forward >/dev/null 2>&1; then
        nm_rules="$(nft list chain ip "${NM_TABLE}" filter_forward)"
        grep -F "iifname \"${LAN_INTERFACE}\"" <<<"${nm_rules}" \
            | grep -F "oifname \"${WG_INTERFACE}\"" \
            | grep -Fq 'comment "pcs-wg-from-lan"'
        IFS=',' read -r -a admin_sources <<<"${WG_ADMIN_SOURCES}"
        for admin_source in "${admin_sources[@]}"; do
            admin_source="${admin_source//[[:space:]]/}"
            grep -F "iifname \"${WG_INTERFACE}\"" <<<"${nm_rules}" \
                | grep -F "oifname \"${LAN_INTERFACE}\"" \
                | grep -F "ip saddr ${admin_source%/32}" \
                | grep -F "ip daddr ${LAN_NETWORK}" \
                | grep -Fq 'comment "pcs-wg-to-lan"'
        done
        echo "NetworkManager shared-LAN compatibility rules are present."
    else
        echo "NetworkManager shared-LAN table is currently absent; compatibility rules are not required until ${LAN_INTERFACE} is active."
    fi

    echo "PCS WireGuard firewall checks passed."
}

case "${MODE}" in
    --apply)
        require_command nft
        require_command python3
        validate_config
        apply_rules
        ;;
    --clear)
        require_command nft
        validate_interface_name "WireGuard" "${WG_INTERFACE}"
        validate_interface_name "LAN" "${LAN_INTERFACE}"
        clear_rules
        ;;
    --check)
        require_command nft
        require_command python3
        validate_config
        check_rules
        ;;
    --refresh-networkmanager)
        require_command nft
        require_command python3
        validate_config
        if ! nft list table inet "${PCS_TABLE}" >/dev/null 2>&1; then
            echo "ERROR: refusing to add NetworkManager compatibility rules without the PCS isolation table." >&2
            exit 1
        fi
        install_nm_compat_rules
        ;;
    --validate-config)
        require_command nft
        require_command python3
        validate_config
        echo "PCS WireGuard firewall configuration is valid."
        ;;
    -h|--help)
        echo "Usage: pcs-wireguard-firewall [--apply|--clear|--check|--refresh-networkmanager|--validate-config]"
        ;;
    *)
        echo "Usage: pcs-wireguard-firewall [--apply|--clear|--check|--refresh-networkmanager|--validate-config]" >&2
        exit 2
        ;;
esac
