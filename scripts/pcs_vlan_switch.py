#!/usr/bin/env python3
"""Offline staging and supervised, journaled PCS VLAN migration.

No invocation without an explicit action changes the host. Staging writes only
its destination. Apply/commit/rollback are Linux root operations, never installer
side effects. Hardware isolation requires an independent operator witness.
"""
from __future__ import annotations
import argparse
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/pcs/vlan-switch')
MODE = Path('/etc/pcs/network-mode')
NM_DIR = Path('/etc/NetworkManager/system-connections')
IDS = {name: str(uuid.uuid5(uuid.NAMESPACE_DNS, 'pcs.local/' + name)) for name in
       ('pcs-vlan-parent', 'pcs-lan-vlan', 'pcs-starlink-vlan')}
SERVICES = ('pcs-uplink-manager.service', 'pcs-uplink-management.service',
            'pcs-wireguard-firewall.service', 'pcs-stats-api-firewall.service',
            'pcs-aprs-kiss-firewall.service', 'pcs-wsdd.service')
LIBRARIES = ('pcs_uplink_manager.py', 'pcs_uplink_management.py', 'pcs_starlink.py',
             'pcs_network_clients.py')
HELPERS = ('pcs-wireguard-firewall', 'pcs-stats-api-firewall', 'pcs-aprs-kiss-firewall',
           'pcs-wsdd-firewall', 'pcs-web-action', 'restart-pcs-services', 'pcs-self-test', 'pcs-status')
TIMER = 'pcs-vlan-rollback.timer'
GATES = ('lan_dhcp_dns_ntp', 'wan_isolation_ipv4_ipv6', 'brick_switch_egress',
         'services_wireguard', 'failover_recovery', 'pi_reboot', 'switch_cold_boot')


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True,
                          check=True, timeout=60).stdout.strip()


def write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8', newline='\n')
    temp.chmod(mode)
    os.replace(temp, path)


def json_write(path, value):
    write(path, json.dumps(value, indent=2) + '\n')


def mac(value):
    if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value or ''):
        raise ValueError('provide observed Brick and switch management MAC addresses')
    if value.lower() == '00:00:00:00:00:00' or int(value[:2], 16) & 1:
        raise ValueError('management MAC must be nonzero unicast')
    return value.lower()


def profiles(ipv6='auto'):
    if ipv6 not in ('auto', 'disabled', 'ignore'):
        raise ValueError('unsupported existing WAN IPv6 policy')
    result = {}
    for name, interface, method, tag in (
            ('pcs-vlan-parent', 'eth0', 'disabled', None),
            ('pcs-lan-vlan', 'eth0.10', 'shared', 10),
            ('pcs-starlink-vlan', 'eth0.20', 'auto', 20)):
        text = (f'[connection]\nid={name}\nuuid={IDS[name]}\n'
                f'type={"vlan" if tag else "ethernet"}\ninterface-name={interface}\n'
                'autoconnect=false\nautoconnect-priority=200\n')
        if tag:
            text += f'\n[vlan]\nparent=eth0\nid={tag}\nflags=1\n'
        text += f'\n[ipv4]\nmethod={method}\n'
        if tag == 10:
            text += ('address1=10.42.0.1/24\nnever-default=true\n'
                     'shared-dhcp-range=10.42.0.100,10.42.0.200\n')
        elif tag == 20:
            text += 'route-metric=100\ndns-priority=100\n'
        text += f'\n[ipv6]\nmethod={ipv6 if tag == 20 else "disabled"}\n'
        if tag != 20:
            text += 'never-default=true\n'
        else:
            text += 'route-metric=100\ndns-priority=100\n'
        result[name + '.nmconnection'] = text
    return result


def guard(brick, switch):
    identities = ', '.join((mac(brick), mac(switch)))
    if mac(brick) == mac(switch):
        raise ValueError('Brick and switch management identities must be distinct')
    return f'''table inet pcs_vlan_guard {{
 chain input {{
  type filter hook input priority -30; policy accept;
  iifname "eth0" counter drop comment "pcs-parent-untrusted"
  iifname "eth0.20" ip saddr 10.42.0.0/24 counter drop
  ct state established,related accept
  iifname "eth0.10" ether saddr {{ {identities} }} counter drop comment "pcs-infrastructure-no-proxy"
  iifname "eth0.10" ip saddr {{ 10.42.0.2, 10.42.0.4 }} counter drop
  iifname "eth0.20" udp sport 67 udp dport 68 accept
  iifname "eth0.20" udp sport 547 udp dport 546 accept
  iifname "eth0.20" ip6 hoplimit 255 icmpv6 type {{ nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert }} accept
  iifname "eth0.20" icmpv6 type {{ destination-unreachable, packet-too-big, time-exceeded, parameter-problem }} accept
  iifname "eth0.20" counter drop comment "pcs-wan-input-deny"
 }}
 chain forward {{
  type filter hook forward priority -30; policy accept;
  iifname "eth0" counter drop
  oifname "eth0" counter drop
  iifname "eth0.10" ether saddr {{ {identities} }} counter drop comment "pcs-infrastructure-no-internet"
  iifname "eth0.10" ip saddr {{ 10.42.0.2, 10.42.0.4 }} counter drop
  iifname "eth0.20" ip saddr 10.42.0.0/24 counter drop
  iifname "eth0.20" oifname "eth0.10" ct state established,related accept
  iifname "eth0.20" oifname "eth0.10" counter drop comment "pcs-wan-lan-deny"
 }}
}}
'''


def stage(destination, brick, switch, ipv6):
    destination = Path(destination).resolve()
    # Never stage in NM's live or persistent policy directories.
    if os.name != 'nt' and any(destination == Path(p) or Path(p) in destination.parents
                              for p in ('/etc', '/run', '/usr')):
        raise ValueError('stage must be outside live configuration directories')
    if destination.is_dir() and any(destination.iterdir()) and not (destination / 'manifest.json').is_file():
        raise ValueError('stage requires an empty directory or an existing PCS stage')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = profiles(ipv6) | {'guard.nft': guard(brick, switch)}
    for name, content in files.items():
        if name.endswith('.nmconnection'):
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(content)
            if parser['connection']['autoconnect'] != 'false':
                raise ValueError('stage must remain inactive')
        write(destination / name, content)
    manifest = dict(version=1, brick=mac(brick), switch=mac(switch), ipv6=ipv6,
                    hashes={name: hashlib.sha256(content.encode()).hexdigest()
                            for name, content in files.items()})
    json_write(destination / 'manifest.json', manifest)
    return manifest


def verify_stage(destination):
    destination = Path(destination)
    m = json.loads((destination / 'manifest.json').read_text())
    expected = profiles(m['ipv6']) | {'guard.nft': guard(m['brick'], m['switch'])}
    if m.get('version') != 1 or set(m['hashes']) != set(expected):
        raise ValueError('invalid staged manifest')
    for name, content in expected.items():
        if (destination / name).read_text() != content or hashlib.sha256(content.encode()).hexdigest() != m['hashes'][name]:
            raise ValueError('staged configuration changed; restage for review')
    return m


def preflight():
    from pcs_uplink_manager import load_config
    if MODE.exists() and MODE.read_text().strip() not in ('legacy', 'vlan'):
        raise ValueError('invalid topology marker')
    for command in ('nmcli', 'ip', 'nft', 'systemctl', 'systemd-analyze'):
        if not shutil.which(command):
            raise ValueError('missing prerequisite: ' + command)
    load_config()
    if run('nmcli', '-g', 'connection.type', 'connection', 'show', 'pcs-router-wan-share') != '802-3-ethernet':
        raise ValueError('legacy LAN profile identity mismatch')
    # NM 1.52 introduced this property; refuse to silently change DHCP range.
    run('nmcli', '-g', 'ipv4.shared-dhcp-range', 'connection', 'show', 'pcs-router-wan-share')
    if run('nmcli', '-g', 'ipv4.method', 'connection', 'show', 'pcs-router-wan-share') != 'shared':
        raise ValueError('legacy LAN is not shared')
    run('ip', '-d', '-j', 'link', 'show', 'eth0')
    for identity in run('nmcli', '-g', 'UUID', 'connection', 'show').splitlines():
        kind = run('nmcli', '-g', 'connection.type', 'connection', 'show', identity)
        if kind == '802-3-ethernet':
            bound = run('nmcli', '-g', 'connection.interface-name', 'connection', 'show', identity)
            binding = run('nmcli', '-g', '802-3-ethernet.mac-address', 'connection', 'show', identity)
            if not bound and not binding:
                raise ValueError('unbound Ethernet profile may capture the trunk; explicitly bind it before migration')
    run('nft', '-j', 'list', 'ruleset')
    run('nft', 'list', 'chain', 'ip', 'nm-shared-eth0', 'filter_forward')
    if service_active('pcs-cellular-fallback.service'):
        raise ValueError('install the existing uplink manager before migration')


def service_active(name):
    return subprocess.run(['systemctl', 'is-active', '--quiet', name], capture_output=True).returncode == 0


def targets():
    files = [MODE, Path('/etc/pcs/uplinks.json'), Path('/etc/pcs/vlan-guard.nft'),
             Path('/etc/NetworkManager/dispatcher.d/90-pcs-wireguard-firewall')]
    files += [Path('/usr/local/lib/pcs') / name for name in LIBRARIES]
    files += [Path('/usr/local/sbin') / name for name in HELPERS]
    files += [Path('/etc/systemd/system/pcs-vlan-guard.service'),
              Path('/etc/systemd/system/NetworkManager.service.d/pcs-vlan-guard.conf')]
    files += [NM_DIR / (name + '.nmconnection') for name in IDS]
    return files


def backup():
    folder = STATE / ('backup-' + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    managed = targets()
    # Include every persistent and runtime NM profile (credentials remain local).
    for directory in (NM_DIR, Path('/run/NetworkManager/system-connections'), Path('/etc/pcs'),
                      Path('/etc/pcs-stats-api'), Path('/run/pcs-uplink-manager')):
        if directory.exists():
            managed += [p for p in directory.rglob('*') if p.is_file()]
    manifest = dict(version=1, files=[], services={s: service_active(s) for s in SERVICES},
                    enabled={s: subprocess.run(['systemctl', 'is-enabled', s], capture_output=True, text=True).stdout.strip() for s in SERVICES},
                    active=run('nmcli', '-g', 'UUID', 'connection', 'show', '--active').splitlines(),
                    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for index, path in enumerate(dict.fromkeys(managed)):
        if path.is_symlink():
            raise ValueError('refusing symlink in migration write set: ' + str(path))
        row = dict(path=str(path), saved=str(index), exists=path.exists())
        if path.exists():
            shutil.copy2(path, folder / str(index))
        manifest['files'].append(row)
    write(folder / 'firewall.nft', run('nft', '-s', 'list', 'ruleset') + '\n')
    scoped = []
    for item in json.loads(run('nft', '-j', 'list', 'tables'))['nftables']:
        table = item.get('table', {})
        if table.get('name', '').startswith(('pcs_', 'nm-shared-')):
            scoped.append(run('nft', '-s', 'list', 'table', table['family'], table['name']))
    write(folder / 'pcs-firewall.nft', '\n'.join(scoped) + '\n')
    json_write(folder / 'manifest.json', manifest)
    return folder, manifest


def arm(folder, timeout):
    # Recovery code is copied into root-owned persistent storage, independent of
    # SSH lifetime and repository location. A reboot starts a fresh bounded timer.
    recovery = STATE / 'recovery.py'
    write(recovery, Path(__file__).read_text())
    write('/etc/systemd/system/pcs-vlan-rollback.service',
          '[Unit]\nDescription=Restore unconfirmed PCS network migration\nAfter=NetworkManager.service\n'
          '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 ' + str(recovery) + ' --rollback\n')
    write('/etc/systemd/system/' + TIMER,
          '[Unit]\nDescription=PCS VLAN confirmation deadline\n[Timer]\n'
          f'OnActiveSec={timeout}\nOnBootSec={timeout}\nAccuracySec=1s\n'
          '[Install]\nWantedBy=timers.target\n')
    json_write(STATE / 'pending.json', dict(backup=str(folder), created=time.time(), timeout=timeout))
    run('systemd-analyze', 'verify', '/etc/systemd/system/' + TIMER,
        '/etc/systemd/system/pcs-vlan-rollback.service')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', TIMER)
    run('systemctl', 'is-active', TIMER)


def refresh(manifest):
    for service in SERVICES[1:]:
        if manifest['services'][service]:
            run('systemctl', 'restart', service)


def apply(destination, timeout, authorized, switch_verified):
    from pcs_uplink_manager import load_config
    if not authorized or not switch_verified:
        raise ValueError('apply requires --authorize-network-change and --switch-isolation-verified')
    if not 120 <= timeout <= 7200:
        raise ValueError('rollback timeout must be 120..7200 seconds')
    if (STATE / 'pending.json').exists():
        raise ValueError('a migration is pending; check, commit, or rollback it')
    if MODE.exists() and MODE.read_text().strip() == 'vlan':
        return check()  # repeat deployment does not duplicate profiles or rules
    m = verify_stage(destination)
    preflight()
    existing = json.loads(Path('/etc/pcs/uplinks.json').read_text())
    cfg = load_config()
    old = next((u for u in cfg.uplinks if u.id == 'starlink'), None)
    if not old or old.type != 'ethernet' or not old.profile:
        raise ValueError('migration requires the commissioned physical Starlink uplink')
    current_ipv6 = run('nmcli', '-g', 'ipv6.method', 'connection', 'show', old.profile)
    if m['ipv6'] != current_ipv6:
        raise ValueError('stage IPv6 policy does not match the existing wired WAN')
    # Refuse UUID/name collisions before backing up or changing anything.
    known = run('nmcli', '-g', 'UUID', 'connection', 'show').splitlines()
    names = run('nmcli', '-g', 'NAME', 'connection', 'show').splitlines()
    if set(IDS.values()) & set(known) or set(IDS) & set(names):
        raise ValueError('reserved VLAN profiles already exist; investigate before apply')
    run('nft', '-c', '-f', str(Path(destination) / 'guard.nft'))
    folder, manifest = backup()
    arm(folder, timeout)  # nothing network-affecting occurs before this succeeds
    try:
        run('systemctl', 'stop', 'pcs-uplink-manager.service')
        for name in LIBRARIES:
            write(Path('/usr/local/lib/pcs') / name, (ROOT / 'scripts' / name).read_text(), 0o644)
        for name in HELPERS:
            if (Path('/usr/local/sbin') / name).exists():
                write(Path('/usr/local/sbin') / name, (ROOT / 'scripts' / (name + '.sh')).read_text(), 0o755)
        write('/etc/NetworkManager/dispatcher.d/90-pcs-wireguard-firewall',
              (ROOT / 'networkmanager/90-pcs-wireguard-firewall').read_text(), 0o755)
        write('/etc/pcs/vlan-guard.nft', (Path(destination) / 'guard.nft').read_text())
        write('/etc/systemd/system/pcs-vlan-guard.service',
              '[Unit]\nDescription=PCS VLAN ingress and infrastructure egress guard\n'
              'DefaultDependencies=no\nAfter=local-fs.target\nBefore=NetworkManager.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
              'ExecStart=/usr/sbin/nft -f /etc/pcs/vlan-guard.nft\n')
        write('/etc/systemd/system/NetworkManager.service.d/pcs-vlan-guard.conf',
              '[Unit]\nRequires=pcs-vlan-guard.service\nAfter=pcs-vlan-guard.service\n')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', 'pcs-vlan-guard.service')
        write(MODE, 'vlan\n', 0o644)
        # Preserve non-Starlink rows, priorities, activation ownership and timing.
        row = dict(id=old.id, name=old.name, type='vlan', priority=old.priority,
                   interface='eth0.20', parent='eth0', vlan_id=20,
                   profile=IDS['pcs-starlink-vlan'], activation=old.activation)
        existing['uplinks'] = [row if u['id'] == old.id else u for u in existing['uplinks']]
        json_write('/etc/pcs/uplinks.json', existing)
        load_config()
        for name, content in profiles(m['ipv6']).items():
            write(NM_DIR / name, content)
        run('nmcli', 'connection', 'reload')
        # Prevent every physical-parent Ethernet profile from winning a reboot.
        for connection in known:
            kind = run('nmcli', '-g', 'connection.type', 'connection', 'show', connection)
            bound = run('nmcli', '-g', 'connection.interface-name', 'connection', 'show', connection)
            devices = run('nmcli', '-g', 'GENERAL.DEVICES', 'connection', 'show', connection).split(',')
            if kind == '802-3-ethernet' and (bound == 'eth0' or 'eth0' in devices):
                run('nmcli', 'connection', 'modify', connection, 'connection.autoconnect', 'no')
        run('nmcli', 'connection', 'modify', old.profile, 'connection.autoconnect', 'no')
        if old.profile in manifest['active']:
            run('nmcli', 'connection', 'down', old.profile)
        run('nmcli', 'connection', 'down', 'pcs-router-wan-share')
        for name in ('pcs-vlan-parent', 'pcs-lan-vlan', 'pcs-starlink-vlan'):
            run('nmcli', 'connection', 'modify', IDS[name], 'connection.autoconnect', 'yes')
        run('nmcli', '--wait', '20', 'connection', 'up', IDS['pcs-vlan-parent'])
        run('nmcli', '--wait', '20', 'connection', 'up', IDS['pcs-lan-vlan'])
        run('nmcli', '--wait', '0', 'connection', 'up', IDS['pcs-starlink-vlan'])
        refresh(manifest)
        if manifest['services']['pcs-uplink-manager.service']:
            run('systemctl', 'start', 'pcs-uplink-manager.service')
        print('Pending confirmation; rollback backup: ' + str(folder))
    except BaseException:
        rollback()
        raise


def check():
    from pcs_uplink_manager import load_config, resolve_vlan_interface
    if not MODE.exists() or MODE.read_text().strip() != 'vlan':
        raise ValueError('VLAN mode is not commissioned or pending')
    links = json.loads(run('ip', '-d', '-j', 'link', 'show'))
    parent = next(x for x in links if x['ifname'] == 'eth0')
    for name, tag in (('eth0.10', 10), ('eth0.20', 20)):
        d = next(x for x in links if x['ifname'] == name)
        info = d.get('linkinfo', {})
        if info.get('info_kind') != 'vlan' or info.get('info_data', {}).get('id') != tag or d.get('link_index') != parent['ifindex']:
            raise ValueError('kernel VLAN identity mismatch')
    addresses = json.loads(run('ip', '-j', 'address', 'show'))
    by_name = {r['ifname']: r.get('addr_info', []) for r in addresses}
    if by_name['eth0']:
        raise ValueError('parent has an IP address')
    if not any(a.get('local') == '10.42.0.1' and a.get('prefixlen') == 24 for a in by_name['eth0.10']):
        raise ValueError('LAN gateway address missing')
    for family in ('-4', '-6'):
        routes = json.loads(run('ip', family, '-j', 'route', 'show', 'table', 'all'))
        if any(r.get('dev') == 'eth0.10' and r.get('dst') == 'default' for r in routes):
            raise ValueError('LAN has a default route')
    for a in by_name.get('eth0.20', []):
        if a.get('family') == 'inet' and ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network.overlaps(ipaddress.ip_network('10.42.0.0/24')):
            raise ValueError('WAN subnet overlaps LAN')
    run('nft', 'list', 'chain', 'ip', 'nm-shared-eth0.10', 'filter_forward')
    rules = run('nft', 'list', 'table', 'inet', 'pcs_vlan_guard')
    for marker in ('pcs-parent-untrusted', 'pcs-wan-input-deny', 'pcs-wan-lan-deny', 'pcs-infrastructure-no-internet'):
        if marker not in rules:
            raise ValueError('missing isolation rule: ' + marker)
    for name, identity in IDS.items():
        if run('nmcli', '-g', 'connection.uuid', 'connection', 'show', name) != identity:
            raise ValueError('profile UUID mismatch')
    for profile, fields in {
        'pcs-vlan-parent': {'connection.interface-name': 'eth0', 'ipv4.method': 'disabled', 'ipv6.method': 'disabled'},
        'pcs-lan-vlan': {'connection.type': 'vlan', 'connection.interface-name': 'eth0.10', 'vlan.parent': 'eth0',
                         'vlan.id': '10', 'ipv4.method': 'shared', 'ipv4.never-default': 'yes',
                         'ipv4.shared-dhcp-range': '10.42.0.100,10.42.0.200'},
        'pcs-starlink-vlan': {'connection.type': 'vlan', 'connection.interface-name': 'eth0.20',
                              'vlan.parent': 'eth0', 'vlan.id': '20', 'ipv4.method': 'auto'},
    }.items():
        for key, value in fields.items():
            if run('nmcli', '-g', key, 'connection', 'show', IDS[profile]) != value:
                raise ValueError('saved profile mismatch: ' + profile + ' ' + key)
    u = next(u for u in load_config().uplinks if u.id == 'starlink')
    # WAN absence is a valid offline LAN state. An active WAN must validate fully.
    active = run('nmcli', '-g', 'UUID', 'connection', 'show', '--active').splitlines()
    if u.profile in active:
        resolve_vlan_interface(u)
    for helper, argument in (('pcs-wireguard-firewall', '--check'), ('pcs-stats-api-firewall', '--check'),
                             ('pcs-aprs-kiss-firewall', '--check'), ('pcs-wsdd-firewall', 'check')):
        service = 'pcs-wsdd.service' if helper == 'pcs-wsdd-firewall' else helper + '.service'
        if service_active(service):
            run('/usr/local/sbin/' + helper, argument)
    print('Pi topology checks passed; external isolation and client acceptance still require operator evidence.')


def commit(receipt):
    pending = json.loads((STATE / 'pending.json').read_text())
    if time.time() > pending['created'] + pending['timeout']:
        raise ValueError('confirmation deadline expired; rollback required')
    evidence = json.loads(Path(receipt).read_text())
    if evidence.get('backup') != pending['backup'] or not evidence.get('operator'):
        raise ValueError('receipt must identify this backup and the verifying operator')
    if any(not isinstance(evidence.get(g), str) or not evidence[g].strip() for g in GATES):
        raise ValueError('receipt requires evidence for every commissioning gate: ' + ', '.join(GATES))
    check()
    json_write(STATE / 'committed.json', dict(pending, evidence=evidence))
    (STATE / 'pending.json').unlink()
    run('systemctl', 'disable', '--now', TIMER)
    print('Explicitly verified migration committed; retain the recovery backup and USB adapter.')


def rollback(backup_path=None):
    pending_file = STATE / 'pending.json'
    if not pending_file.exists() and backup_path is None:
        print('No pending migration; nothing changed.')
        return
    folder = Path(backup_path or json.loads(pending_file.read_text())['backup']).resolve()
    if folder.parent != STATE.resolve() or not folder.name.startswith('backup-'):
        raise ValueError('invalid recovery backup path')
    manifest = json.loads((folder / 'manifest.json').read_text())
    allowed = targets()
    roots = (NM_DIR, Path('/run/NetworkManager/system-connections'), Path('/etc/pcs'),
             Path('/etc/pcs-stats-api'), Path('/run/pcs-uplink-manager'))
    for row in manifest['files']:
        path = Path(row['path'])
        if path not in allowed and not any(path.is_relative_to(p) for p in roots):
            raise ValueError('rollback target outside the managed write set')
        if Path(row['saved']).name != row['saved'] or (row['exists'] and not (folder / row['saved']).is_file()):
            raise ValueError('invalid backup entry')
    run('systemctl', 'stop', 'pcs-uplink-manager.service')
    for identity in IDS.values():
        subprocess.run(['nmcli', 'connection', 'down', identity], capture_output=True, timeout=30)
        subprocess.run(['nmcli', 'connection', 'delete', identity], capture_output=True, timeout=30)
    for row in manifest['files']:
        path = Path(row['path'])
        if row['exists']:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / row['saved'], path)
        else:
            path.unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    run('nmcli', 'connection', 'reload')
    # Restore only PCS and NetworkManager sharing tables. Leave unrelated rules intact.
    deletions = []
    for item in json.loads(run('nft', '-j', 'list', 'tables'))['nftables']:
        table = item.get('table', {})
        if table.get('name', '').startswith(('pcs_', 'nm-shared-')):
            deletions.append('delete table ' + table['family'] + ' ' + json.dumps(table['name']))
    run('nft', '-f', '-', input='\n'.join(deletions) + '\n' + (folder / 'pcs-firewall.nft').read_text())
    active = run('nmcli', '-g', 'UUID', 'connection', 'show', '--active').splitlines()
    legacy = run('nmcli', '-g', 'connection.uuid', 'connection', 'show', 'pcs-router-wan-share')
    for identity in manifest['active']:
        if identity in active:
            continue  # cellular session remains untouched
        # Restore Ethernet only; never start a cellular session during rollback.
        kind = run('nmcli', '-g', 'connection.type', 'connection', 'show', identity)
        if identity == legacy or kind == '802-3-ethernet':
            run('nmcli', '--wait', '0', 'connection', 'up', identity)
    refresh(manifest)
    if manifest['services']['pcs-uplink-manager.service']:
        run('systemctl', 'start', 'pcs-uplink-manager.service')
    json_write(folder / 'restored.json', dict(time=time.time()))
    pending_file.unlink(missing_ok=True)
    run('systemctl', 'disable', '--now', TIMER)
    print('Pi settings restored. Reconnect eth0 to the original LAN and USB Ethernet to WAN at the local console.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'stage', 'dry-run', 'apply', 'check', 'commit', 'rollback'):
        action.add_argument('--' + name, action='store_true')
    parser.add_argument('--directory', type=Path, default=Path('./pcs-vlan-stage'))
    parser.add_argument('--brick-mac')
    parser.add_argument('--switch-mac')
    parser.add_argument('--wan-ipv6', choices=('auto', 'disabled', 'ignore'), default='auto')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--authorize-network-change', action='store_true')
    parser.add_argument('--switch-isolation-verified', action='store_true')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--backup', type=Path, help='explicit saved backup for rollback after commit')
    args = parser.parse_args(argv)
    if args.stage:
        print(json.dumps(stage(args.directory, args.brick_mac, args.switch_mac, args.wan_ipv6), indent=2))
        return 0
    if args.dry_run:
        print(json.dumps(verify_stage(args.directory), indent=2))
        print('Apply backs up profiles, policy, runtime, services and firewall; arms rollback; installs guard; switches parent/LAN/WAN. No changes made.')
        return 0
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('this action requires root on the PCS Linux host')
    if args.preflight:
        preflight()
        print('Read-only preflight passed; switch isolation and management IP collision checks remain external gates.')
        return 0
    if args.check:
        check()
        return 0
    import fcntl
    with open('/run/lock/pcs-vlan-switch.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.apply:
            apply(args.directory, args.timeout, args.authorize_network_change, args.switch_isolation_verified)
        elif args.commit:
            if args.receipt is None:
                raise ValueError('--commit requires --receipt')
            commit(args.receipt)
        else:
            rollback(args.backup)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, StopIteration) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
