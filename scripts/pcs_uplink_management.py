#!/usr/bin/env python3
"""Opt-in home-management access through MAC-bound Ethernet uplinks.

Only our commented rules in existing PCS input chains are changed. Existing
LAN/VPN rules, default denies, listeners, certificates and NAT are untouched.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

from pcs_uplink_manager import CONFIG, load_config

POLICY = Path('/etc/pcs/uplink-management.json')
PREFIX = 'pcs-uplink-management:'
TABLE_PORTS = {'pcs_wireguard': '22, 80, 139, 443, 445, 8080, 9090', 'pcs_stats_api': '9443'}


def validate_policy(raw, config):
    if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('trusted'), list):
        raise ValueError('management policy requires version 1 and a trusted list')
    if len(raw['trusted']) > 32:
        raise ValueError('too many management entries')
    uplinks = {u.id: u for u in config.uplinks}
    result = []
    seen = set()
    private = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    for row in raw['trusted']:
        if not isinstance(row, dict) or set(row) != {'uplink', 'sources'}:
            raise ValueError('management entry requires uplink and sources only')
        uid = row['uplink']
        if uid not in uplinks or uid in seen:
            raise ValueError('unknown or duplicate management uplink')
        u = uplinks[uid]
        if u.type != 'ethernet' or not u.mac:
            raise ValueError('management requires an explicitly MAC-bound Ethernet uplink')
        if not isinstance(row['sources'], list) or not 1 <= len(row['sources']) <= 8:
            raise ValueError('configure 1..8 explicit trusted source networks')
        networks = []
        for value in row['sources']:
            net = ipaddress.ip_network(value, strict=True)
            if net.version != 4 or net.prefixlen < 16 or not any(net.subnet_of(n) for n in private):
                raise ValueError('management sources must be RFC1918 IPv4 /16 or narrower')
            if net.overlaps(ipaddress.ip_network('10.42.0.0/24')):
                raise ValueError('Ethernet WAN management must not overlap the PCS LAN')
            if net in networks:
                raise ValueError('duplicate management source')
            networks.append(net)
        result.append((u, networks))
        seen.add(uid)
    return result


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, timeout=15, check=True).stdout


def resolve(entries, devices, permanent_mac):
    rows = []
    for u, networks in entries:
        matches = [d for d in devices if d.get('ifname') not in {'lo', 'eth0'}
                   and d.get('link_type') == 'ether'
                   and (not u.interface or d['ifname'] == u.interface)
                   and permanent_mac(d['ifname']).lower() == u.mac.lower()]
        if len(matches) > 1:
            raise ValueError('ambiguous Ethernet management identity')
        if not matches:
            continue
        d = matches[0]
        index = d['ifindex']
        if type(index) is not int or index <= 0:
            raise ValueError('invalid interface index')
        addresses = [ipaddress.ip_address(a['local']) for a in d.get('addr_info', []) if a.get('family') == 'inet']
        for net in networks:
            # A Starlink/other upstream on a different subnet does not inherit
            # home management merely because it uses the same physical NIC.
            if any(a in net for a in addresses):
                rows.append((u.id, index, str(net)))
    return rows


def transaction(chains, rows):
    commands = []
    for table, rules in chains.items():
        for rule in rules:
            if str(rule.get('comment', '')).startswith(PREFIX):
                commands.append(f'delete rule inet {table} input handle {int(rule["handle"])}')
        for uid, index, network in rows:
            commands.append(f'insert rule inet {table} input meta iif {index} ip saddr {network} tcp dport {{ {TABLE_PORTS[table]} }} accept comment "{PREFIX}{uid}"')
    return '\n'.join(commands) + ('\n' if commands else '')


def apply(clear=False):
    import fcntl
    with open('/run/lock/pcs-uplink-management.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        chains = {}
        for table in TABLE_PORTS:
            result = subprocess.run(['nft', '-j', 'list', 'chain', 'inet', table, 'input'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                chains[table] = [x['rule'] for x in json.loads(result.stdout)['nftables'] if 'rule' in x]
            elif 'No such file or directory' not in result.stderr:
                raise RuntimeError(f'Cannot inspect {table}: {result.stderr.strip()}')
        rows = []
        error = None
        if not clear and POLICY.exists():
            try:
                entries = validate_policy(json.loads(POLICY.read_text()), load_config(CONFIG))
                devices = json.loads(run('ip', '-j', 'address'))
                def mac(name):
                    try:
                        return run('/usr/sbin/ethtool', '-P', name).split()[-1]
                    except (OSError, subprocess.SubprocessError, IndexError):
                        return ''
                rows = resolve(entries, devices, mac)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
                error = exc  # Invalid configuration withdraws only our grants.
        script = transaction(chains, rows)
        if script:
            subprocess.run(['nft', '-c', '-f', '-'], input=script, text=True, capture_output=True, timeout=15, check=True)
            subprocess.run(['nft', '-f', '-'], input=script, text=True, capture_output=True, timeout=15, check=True)
        if error:
            raise ValueError(f'Ethernet management grants withdrawn: {error}')
        print(f'Ethernet management: {len(rows)} trusted interface/source grants; {len(chains)} existing firewall chains')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--clear', action='store_true')
    args = parser.parse_args()
    apply(clear=args.clear)
