"""FQ-301-v4 pure validation and transaction compilation.

Pure validation/compilation only. No command execution, lease admission, or live
injection API. The fixed scenario's lease owner applies these transactions.
Addresses and device identities returned here are private control data, never
qualification report fields. A successful plan is not permission to inject.
"""
from dataclasses import dataclass
import ipaddress
import re

from pcs_qualify_state import HarnessError, identifier

TABLE = 'pcs_qualification'
OWNER = 'pcs-qualify:FQ-301-v4:'
CELLULAR_OWNER = 'pcs-qualify:FQ-302:'
MANUAL_OWNER = 'pcs-qualify:FQ-303:'


@dataclass(frozen=True)
class Identity:
    name: str
    index: int
    permanent_mac: str


def ethernet_identity(row, permanent_mac):
    """Require the narrow first-scenario Ethernet target, not an arbitrary NIC."""
    if (not isinstance(row, dict) or row.get('ifname') != 'eth1' or
            type(row.get('ifindex')) is not int or not 1 <= row['ifindex'] < 2**31 or
            row.get('link_type') != 'ether' or row.get('operstate') != 'UP' or
            not isinstance(row.get('flags'), list) or
            any(not isinstance(flag, str) for flag in row['flags']) or
            not {'UP', 'LOWER_UP'} <= set(row['flags']) or
            any(k in row for k in ('master', 'link', 'link_index', 'linkinfo'))):
        raise HarnessError('wan_identity_unverified')
    if (not isinstance(permanent_mac, str) or
            not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', permanent_mac) or
            permanent_mac == '00:00:00:00:00:00' or
            int(permanent_mac[:2], 16) & 1 or row.get('address') != permanent_mac):
        raise HarnessError('wan_permanent_identity_unverified')
    return Identity('eth1', row['ifindex'], permanent_mac)


def revalidate(expected, row, permanent_mac):
    if ethernet_identity(row, permanent_mac) != expected:
        raise HarnessError('wan_identity_changed')


def standard_policy(rows):
    """Conservatively reject policy routing, including otherwise harmless extras."""
    expected = [(0, 'local'), (32766, 'main'), (32767, 'default')]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise HarnessError('policy_routing_unsupported')
    actual = []
    for row in rows:
        if (not isinstance(row, dict) or
                set(row) - {'priority', 'src', 'table', 'protocol'} or
                row.get('src') != 'all' or type(row.get('priority')) is not int or
                not isinstance(row.get('table'), str)):
            raise HarnessError('policy_routing_unsupported')
        actual.append((row['priority'], row.get('table')))
    if sorted(actual) != expected:
        raise HarnessError('policy_routing_unsupported')


def control_query(connection):
    """Include the SSH server source; destination-only lookups can be misleading."""
    fields = connection.split() if isinstance(connection, str) else []
    try:
        if len(fields) != 4 or any(not p.isdigit() or not 1 <= int(p) <= 65535
                                   for p in fields[1::2]):
            raise ValueError
        client, server = (ipaddress.ip_address(fields[i]) for i in (0, 2))
        if client.version != 4 or server.version != 4:
            raise ValueError
    except ValueError:
        raise HarnessError('direct_ipv4_lan_ssh_required') from None
    return ['/usr/sbin/ip', '-j', '-4', 'route', 'get', str(client), 'from', str(server)]


def direct_lan_control(connection, routes, rules, lan_cidr):
    query = control_query(connection)
    standard_policy(rules)
    try:
        network = ipaddress.IPv4Network(lan_cidr, strict=True)
        client, server = ipaddress.IPv4Address(query[5]), ipaddress.IPv4Address(query[7])
        if (not 16 <= network.prefixlen <= 30 or client == server or
                any(a not in network or a in (network.network_address, network.broadcast_address)
                    for a in (client, server))):
            raise ValueError
    except (ValueError, TypeError):
        raise HarnessError('direct_ipv4_lan_ssh_required') from None
    if not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict):
        raise HarnessError('control_route_ambiguous')
    route = routes[0]
    if (route.get('dev') != 'eth0' or route.get('dst') != str(client) or
            route.get('from') != str(server) or route.get('type', 'unicast') != 'unicast' or
            route.get('table', 'main') not in ('main', 254) or
            any(k in route for k in ('gateway', 'nexthops', 'encap', 'nhid'))):
        raise HarnessError('independent_direct_lan_control_required')


def create_plan(session, target, seconds, lan_cidr, wan_cidr):
    """Atomic exclusive creation, IPv4 output/forward drop, kernel timeout.

    The caller must arrange and verify independent systemd expiry BEFORE applying
    this transaction. Kernel timeout bounds the effect even if that caller dies.
    On-link networks, DHCP, multicast, and Starlink telemetry remain reachable.
    No input chain, route change, interface-down action, or production table edit.
    """
    identifier(session)
    if (not isinstance(target, Identity) or target.name != 'eth1' or
            type(target.index) is not int or not 1 <= target.index < 2**31 or
            type(seconds) is not int or not 5 <= seconds <= 120):
        raise HarnessError('invalid_wan_plan')
    try:
        lan, wan = (ipaddress.IPv4Network(c, strict=True) for c in (lan_cidr, wan_cidr))
        if (any(not 16 <= n.prefixlen <= 30 for n in (lan, wan)) or
                lan.overlaps(wan) or any(n.overlaps(ipaddress.IPv4Network(r))
                    for n in (lan, wan) for r in ('0.0.0.0/8', '127.0.0.0/8', '224.0.0.0/3'))):
            raise ValueError
    except (ValueError, TypeError):
        raise HarnessError('invalid_preserved_networks') from None
    preserved = ', '.join(map(str, ipaddress.collapse_addresses([
        lan, wan, ipaddress.IPv4Network('127.0.0.0/8'),
        ipaddress.IPv4Network('169.254.0.0/16'), ipaddress.IPv4Network('224.0.0.0/4'),
        ipaddress.IPv4Network('255.255.255.255/32'), ipaddress.IPv4Network('192.168.100.1/32')])))
    return (
        f'create table inet {TABLE} {{\n comment "{OWNER}{session}";\n'
        f' set blocked {{ type iface_index; flags timeout; timeout {seconds}s; '
        f'elements = {{ {target.index} timeout {seconds}s }}; }}\n'
        f' set preserved {{ type ipv4_addr; flags interval; elements = {{ {preserved} }}; }}\n'
        + ''.join(f' chain {hook} {{ type filter hook {hook} priority -5; policy accept;\n'
                  '  meta nfproto ipv4 udp sport 68 udp dport 67 return\n'
                  '  meta nfproto ipv4 meta oif @blocked ip daddr != @preserved counter drop\n'
                  ' }\n' for hook in ('output', 'forward')) + '}\n')


def create_cellular_plan(session, ethernet, wifi, seconds, lan_cidr, ethernet_cidr, wifi_cidr, owner=CELLULAR_OWNER):
    """One atomic table/set transaction; no partial two-interface activation."""
    if (not isinstance(wifi, Identity) or wifi.name != 'wlan0' or
            type(wifi.index) is not int or not 1 <= wifi.index < 2**31 or
            wifi.index == ethernet.index or type(seconds) is not int or not 60 <= seconds <= 180):
        raise HarnessError('invalid_dual_wan_plan')
    # Reuse the accepted Ethernet compiler's network/identity validation. Both
    # on-link WAN networks must survive; overlapping WAN subnets are permitted.
    first = create_plan(session, ethernet, 120, lan_cidr, ethernet_cidr)
    create_plan(session, ethernet, 120, lan_cidr, wifi_cidr)
    networks = [lan_cidr, ethernet_cidr, wifi_cidr, '127.0.0.0/8', '169.254.0.0/16',
                '224.0.0.0/4', '255.255.255.255/32', '192.168.100.1/32']
    preserved = ', '.join(map(str, ipaddress.collapse_addresses(map(ipaddress.IPv4Network, networks))))
    if owner not in (CELLULAR_OWNER, MANUAL_OWNER):
        raise HarnessError('invalid_dual_wan_owner')
    first = first.replace(OWNER, owner).replace('120s', f'{seconds}s')
    first = first.replace(f'elements = {{ {ethernet.index} timeout {seconds}s }}',
                          f'elements = {{ {ethernet.index} timeout {seconds}s, {wifi.index} timeout {seconds}s }}')
    return re.sub(r'set preserved \{[^\n]+',
                  f'set preserved {{ type ipv4_addr; flags interval; elements = {{ {preserved} }}; }}', first)


def owned_handle(document, session, owner=OWNER):
    """Only the exact session marker authorizes cleanup; names alone never do."""
    identifier(session)
    if not isinstance(document, dict) or not isinstance(document.get('nftables'), list):
        raise HarnessError('firewall_ownership_unknown')
    tables = [item['table'] for item in document['nftables']
              if isinstance(item, dict) and 'table' in item]
    if len(tables) != 1 or not isinstance(tables[0], dict):
        raise HarnessError('firewall_ownership_unknown')
    table = tables[0]
    if (table.get('family') != 'inet' or table.get('name') != TABLE or
            owner not in (OWNER, CELLULAR_OWNER, MANUAL_OWNER) or table.get('comment') != owner + session or type(table.get('handle')) is not int or
            not 1 <= table['handle'] < 2**64):
        raise HarnessError('firewall_ownership_unknown')
    return table['handle']


def delete_plan(document, session, owner=OWNER):
    # A replacement table with the same name has a different kernel handle.
    # If replaced between inspection and execution, deletion fails closed.
    return f'delete table inet handle {owned_handle(document, session, owner)}\n'
