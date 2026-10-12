#!/usr/bin/env python3
"""Inspect, plan and stage PCS recovery offline. There is deliberately no activate command."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import uuid

import pcs_recovery_manifest as manifest
from pcs_reinstall_restore import plan_restore, recovered_settings, INSTALL_CONFIG

MARKER = '.pcs-recovery-workspace.json'
SERVICES = {
    'uplinks': ['pcs-uplink-manager.service'],
    'management': ['pcs-uplink-management.service'],
    'bandwidth': ['pcs-bandwidth.service'],
    'snmp': ['pcs-switch-monitor.service'],
    'power': ['pcs-power-monitor.service'],
}


def no_links(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink in recovery path')


def private_directory(path):
    no_links(path)
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or
            os.name == 'posix' and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700)):
        raise ValueError('staging directories must be owned by the invoking user and mode 0700')


def private_file(path):
    no_links(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            os.name == 'posix' and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600)):
        raise ValueError('staged files must be private regular files with one link')


def write_private(path, data):
    no_links(path)
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def topology(request, inventory, payload):
    fields = {'schema', 'mode', 'lan_uuid', 'wan_uuid', 'parent_uuid', 'interfaces'}
    if not isinstance(request, dict) or set(request) != fields or type(request['schema']) is not int or request['schema'] != 1:
        raise ValueError('explicit version 1 topology request required')
    if request['mode'] not in ('legacy', 'vlan'):
        raise ValueError('unsupported requested topology')
    interfaces = request['interfaces']
    if not isinstance(interfaces, dict) or set(interfaces) != {'lan', 'wan', 'parent'}:
        raise ValueError('explicit LAN/WAN/parent interfaces required')
    for role in ('lan', 'wan'):
        if not isinstance(interfaces[role], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interfaces[role]) or interfaces[role] == 'lo':
            raise ValueError('invalid requested interface')
    if inventory['invalid_profiles']:
        raise ValueError('backup has invalid NetworkManager metadata')
    profiles = inventory['profiles']
    by_uuid = {p['uuid']: p for p in profiles}
    if len(by_uuid) != len(profiles):
        raise ValueError('duplicate NetworkManager UUID')
    selected = {}
    for role in ('lan', 'wan', 'parent'):
        identity = request[role + '_uuid']
        if role == 'parent' and request['mode'] == 'legacy':
            if identity is not None or interfaces[role] is not None:
                raise ValueError('legacy mode has no VLAN parent')
            continue
        if not isinstance(identity, str) or identity not in by_uuid:
            raise ValueError('requested profile UUID is missing')
        selected[role] = by_uuid[identity]
        if selected[role]['interface'] != interfaces[role]:
            raise ValueError('requested interface/profile identity mismatch')
    if len({p['uuid'] for p in selected.values()}) != len(selected):
        raise ValueError('LAN, WAN and parent must have distinct profiles')
    if interfaces['lan'] == interfaces['wan']:
        raise ValueError('LAN and WAN must differ')
    mode = inventory['topology']['mode']
    if mode == 'invalid' or mode not in ('unknown', request['mode']):
        raise ValueError('captured topology conflicts with request')
    if selected['lan']['ipv4'] != 'shared' or selected['wan']['ipv4'] != 'auto':
        raise ValueError('PCS requires shared LAN and automatic Ethernet WAN IPv4')
    import configparser
    lan_cfg = configparser.ConfigParser(interpolation=None)
    lan_cfg.read_string(payload[selected['lan']['path']].decode())
    if lan_cfg.get('ipv4', 'address1', fallback='') != '10.42.0.1/24':
        raise ValueError('unsupported PCS LAN address')
    ethernet = {'ethernet', '802-3-ethernet'}
    if request['mode'] == 'vlan':
        if interfaces != {'lan': 'eth0.10', 'wan': 'eth0.20', 'parent': 'eth0'}:
            raise ValueError('only the implemented native Ethernet VLAN layout is supported')
        parent = selected['parent']
        if parent['type'] not in ethernet or parent['ipv4'] != 'disabled' or parent['ipv6'] != 'disabled':
            raise ValueError('VLAN parent must have no IP configuration')
        for role, tag in (('lan', 10), ('wan', 20)):
            child = selected[role]
            if child['type'] != 'vlan' or child['vlan_id'] != tag or child['parent'] != interfaces['parent']:
                raise ValueError('VLAN child/parent relationship mismatch')
        # Runtime PCS validators require parent interface, not parent UUID aliases.
    else:
        if interfaces['lan'] != 'eth0' or interfaces['wan'] in ('eth0', 'eth0.10', 'eth0.20'):
            raise ValueError('unsupported legacy PCS interface layout')
        if any(p['type'] not in ethernet for p in selected.values()):
            raise ValueError('legacy LAN/WAN must be explicit Ethernet profiles')
    return selected, by_uuid


def validate_configs(payload, selected, by_uuid, request):
    """Reuse pure configuration parsers only; never instantiate hardware/adapters."""
    packages = set()
    services = set()
    paths = set()
    with tempfile.TemporaryDirectory(prefix='pcs-recovery-validate-') as temp:
        root = Path(temp)
        for path, data in payload.items():
            # Paths were allowlisted and links rejected before this point.
            write_private(root / path, data)
        for path in manifest.CONFIGS:
            if path.endswith('.json') and path in payload:
                manifest.parse_json(payload[path])
        uplinks = None
        if 'etc/pcs/uplinks.json' in payload:
            from pcs_uplink_manager import load_config
            uplinks = load_config(root / 'etc/pcs/uplinks.json')
            if uplinks.startup_grace_enabled and uplinks.startup_grace_uplink not in {u.id for u in uplinks.uplinks}:
                raise ValueError('startup grace names a missing uplink')
            if uplinks.switch_assist and 'etc/pcs/switch-monitor.json' not in payload:
                raise ValueError('switch assistance requires captured SNMP configuration')
            for u in uplinks.uplinks:
                if u.type == 'vlan' and (request['mode'] != 'vlan' or u.profile != selected['wan']['uuid']):
                    raise ValueError('uplink VLAN identity conflicts with topology')
                if u.interface in (request['interfaces']['lan'], request['interfaces']['parent']):
                    raise ValueError('uplink uses protected LAN/parent interface')
                if u.profile:
                    p = by_uuid.get(u.profile)
                    types = {'vlan': {'vlan'}, 'ethernet': {'ethernet', '802-3-ethernet'},
                             'wifi': {'wifi', '802-11-wireless'}, 'cellular': {'gsm'}}
                    if p is None or p['type'] not in types[u.type] or u.interface and p['interface'] != u.interface:
                        raise ValueError('uplink profile missing or inconsistent')
                    if u.type in ('ethernet', 'vlan') and p['uuid'] != selected['wan']['uuid']:
                        raise ValueError('additional Ethernet WAN requires a separate recovery design')
                    paths.add(p['path'])
                elif u.type in ('ethernet', 'vlan') and u.interface != request['interfaces']['wan']:
                    # A MAC-only policy cannot be resolved safely offline.
                    raise ValueError('Ethernet uplink requires explicit matching interface or profile')
        if 'etc/pcs/uplink-management.json' in payload:
            from pcs_uplink_management import validate_policy
            if uplinks is None:
                raise ValueError('management policy requires uplink configuration')
            validate_policy(manifest.parse_json(payload['etc/pcs/uplink-management.json']), uplinks)
        if 'etc/pcs/bandwidth.yaml' in payload:
            from pcs_bandwidth_config import load
            config = load(root / 'etc/pcs/bandwidth.yaml')
            for role in ('lan', 'wan'):
                # Null means runtime topology discovery in the existing engine.
                if config[role + '_interface'] not in (None, request['interfaces'][role]):
                    raise ValueError('bandwidth interface conflicts with topology')
            if config['lan_network'] != '10.42.0.0/24':
                raise ValueError('bandwidth network conflicts with PCS LAN')
        if 'etc/pcs/switch-monitor.json' in payload:
            from pcs_switch_monitor import load_config
            config = load_config(root / 'etc/pcs/switch-monitor.json')
            if config['enabled'] and 'etc/pcs/switch-monitor.community' not in payload:
                raise ValueError('enabled SNMP requires captured community')
        if 'etc/pcs/switch-monitor.community' in payload:
            value = payload['etc/pcs/switch-monitor.community'].decode('ascii').strip()
            if not re.fullmatch('[A-Za-z0-9]{1,16}', value):
                raise ValueError('invalid captured SNMP credential')
            if 'etc/pcs/switch-monitor.json' not in payload:
                raise ValueError('orphan SNMP credential requires manual review')
        if 'etc/pcs/power-monitor.json' in payload:
            from pcs_power_monitor import load_config
            load_config(root / 'etc/pcs/power-monitor.json')
        # Existing Wi-Fi and strict WireGuard management/key selection remains intact.
        legacy_network = plan_restore(root, 'network')
        paths.update(path for path, _, _ in legacy_network)
        if any('wireguard' in path for path in paths):
            packages.update(('wireguard-tools', 'nftables'))
            services.add('pcs-wireguard-firewall.service')
        if INSTALL_CONFIG in payload:
            recovered_settings(root)
            paths.add(INSTALL_CONFIG)
        for path, (component, deps) in manifest.CONFIGS.items():
            if path in payload:
                paths.add(path)
                packages.update(deps)
                services.update(SERVICES.get(component, []))
    return paths, packages, services


def build_plan(archive, request, target=None, expected_sha256=None):
    inspected, payload = manifest.inspect(archive, expected_sha256)
    inventory = inspected['inventory']
    selected, by_uuid = topology(request, inventory, payload)
    try:
        paths, packages, services = validate_configs(payload, selected, by_uuid, request)
    except Exception as exc:
        # Third-party YAML/config parsers may put entire secret-bearing lines in
        # exception text. Do not forward those diagnostics to CLI output.
        raise ValueError('captured component configuration failed offline validation; check required packages and private configuration') from exc
    paths.update(p['path'] for p in selected.values())
    paths.add(manifest.MODE)
    services.add('NetworkManager.service')
    generated = []
    if request['mode'] == 'vlan':
        packages.update(('nftables', 'iproute2'))
        services.add('pcs-vlan-guard.service')
        generated = ['etc/pcs/vlan-guard.nft', 'etc/systemd/system/pcs-vlan-guard.service',
                     'etc/systemd/system/NetworkManager.service.d/pcs-vlan-guard.conf']
    if any(p['type'] == 'gsm' and p['path'] in paths for p in inventory['profiles']):
        packages.add('modemmanager')
        services.add('ModemManager.service')
    if manifest.MODE not in payload:
        # Explicit operator input, never inferred from the machine or adapters.
        payload[manifest.MODE] = (request['mode'] + '\n').encode()
    conflicts, files = [], []
    if target is not None:
        target = Path(target)
        if not target.is_absolute() or target == Path(target.anchor):
            raise ValueError('target must be an explicit offline root, not the live root')
        no_links(target)
        target = target.resolve()
        if target == Path(target.anchor):
            raise ValueError('target resolves to the live root')
        if not target.is_dir():
            raise ValueError('offline target root is unavailable')
    for path in sorted(paths):
        existing = 'not-assessed' if target is None else 'absent'
        if target is not None:
            candidate = target / path
            no_links(candidate)
            for parent in candidate.parents:
                if parent == target:
                    break
                if parent.exists() and not parent.is_dir():
                    conflicts.append(path)
            if candidate.exists():
                if not candidate.is_file() or manifest.file_digest(candidate) != manifest.digest(payload[path]):
                    conflicts.append(path)
                    existing = 'conflict'
                else:
                    existing = 'identical'
        files.append(dict(path=path, sha256=manifest.digest(payload[path]), size=len(payload[path]),
                          stage_mode='0600', intended_owner='root:root',
                          install_mode='0644' if path == 'etc/pcs/wireguard-management.conf' else '0600',
                          origin='explicit-request' if path == manifest.MODE and inventory['topology']['mode'] == 'unknown' else 'archive',
                          target_status=existing))
    if target is not None:
        directory = target / manifest.NM
        no_links(directory)
        if directory.exists():
            for path in directory.iterdir():
                no_links(path)
                if not path.is_file():
                    raise ValueError('unsupported target profile entry')
                p = manifest.profile(path.read_bytes(), path.name)
                for chosen in (p for p in inventory['profiles'] if p['path'] in paths):
                    core_interface = chosen in selected.values() and p['interface'] == chosen['interface']
                    if (p['uuid'] == chosen['uuid'] or core_interface) and path != target / chosen['path']:
                        conflicts.append(manifest.NM + path.name)
    excluded = [dict(path=row['path'], reason='outside-this-staged-network-policy-phase')
                for row in inventory['files'] if row['path'] not in paths]
    plan = dict(schema=1, archive_sha256=inspected['archive_sha256'],
                manifest_status=inspected['manifest_status'], request=request,
                target_root=str(target) if target else None, files=files,
                profiles=[p for p in inventory['profiles'] if p['path'] in paths],
                dependencies=sorted(packages | {'python3', 'network-manager'}),
                required_services=sorted(services), exclusions=excluded,
                required_generated_files=generated,
                conflicts=sorted(set(conflicts)), activation=False,
                gates=manifest.EXCLUSIONS)
    return plan, {path: payload[path] for path in paths}


def initialize(workspace):
    if not workspace.is_absolute():
        raise ValueError('workspace must be absolute')
    no_links(workspace)
    workspace.mkdir(mode=0o700)  # Exclusive; never adopt an existing/live directory.
    write_private(workspace / MARKER, manifest.json_bytes({'schema': 1, 'purpose': 'inactive-pcs-recovery'}))


def check_workspace(workspace):
    private_directory(workspace)
    private_file(workspace / MARKER)
    if manifest.parse_json((workspace / MARKER).read_bytes()) != {'schema': 1, 'purpose': 'inactive-pcs-recovery'}:
        raise ValueError('not an initialized recovery workspace')


@contextmanager
def locked(workspace):
    check_workspace(workspace)
    lock = workspace / '.lock'
    write_private(lock, b'locked\n')
    try:
        yield
    finally:
        lock.unlink()


def validate_generation(generation, plan, payload):
    private_directory(generation)
    root = generation / 'rootfs'
    private_directory(root)
    expected = set(payload)
    found = set()
    for path in root.rglob('*'):
        no_links(path)
        if path.is_dir():
            private_directory(path)
        else:
            private_file(path)
            relative = path.relative_to(root).as_posix()
            if relative not in expected or path.read_bytes() != payload[relative]:
                raise ValueError('staged data differs from validated plan')
            found.add(relative)
    if found != expected:
        raise ValueError('staged files are missing')
    if {p.name for p in generation.iterdir()} != {'rootfs', 'plan.json'}:
        raise ValueError('unexpected staged metadata')
    private_file(generation / 'plan.json')
    if manifest.parse_json((generation / 'plan.json').read_bytes()) != plan:
        raise ValueError('staged receipt differs from plan')


def validate_keyfiles(generation):
    """Optional deeper validation with libnm; never opens a NetworkManager client."""
    import gi
    gi.require_version('NM', '1.0')
    from gi.repository import GLib, NM
    count = 0
    for path in (generation / 'rootfs' / manifest.NM).iterdir():
        try:
            text = path.read_text()
            keyfile = GLib.KeyFile.new()
            keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
            connection = NM.keyfile_read(keyfile, '/', NM.KeyfileHandlerFlags.NONE, None, None)
            if not connection.verify():
                raise ValueError('profile verification failed')
        except Exception as exc:
            raise ValueError('libnm rejected staged profile; inspect private keyfile locally') from exc
        count += 1
    return count


def verified_plan(archive, plan):
    if not isinstance(plan, dict) or plan.get('schema') != 1:
        raise ValueError('invalid restoration plan')
    rebuilt, payload = build_plan(archive, plan['request'], plan['target_root'], plan['archive_sha256'])
    if rebuilt != plan or plan['conflicts']:
        raise ValueError('plan changed, is incomplete, or has target conflicts')
    return payload


def stage(archive, plan, workspace):
    payload = verified_plan(archive, plan)
    with locked(workspace):
        generation = workspace / 'staged'
        if generation.exists() or generation.is_symlink():
            validate_generation(generation, plan, payload)
            return 'unchanged'
        pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=workspace))
        try:
            for path, data in payload.items():
                write_private(pending / 'rootfs' / path, data)
            write_private(pending / 'plan.json', manifest.json_bytes(plan))
            validate_generation(pending, plan, payload)
            pending.rename(generation)
        finally:
            # Only this invocation's fresh directory; never old evidence or target state.
            if pending.exists() and pending.parent == workspace and pending.name.startswith('.pending-'):
                shutil.rmtree(pending)
    return 'staged-inactive'


def rollback(workspace):
    with locked(workspace):
        source = workspace / 'staged'
        private_directory(source)
        name = 'retired-' + uuid.uuid4().hex
        source.rename(workspace / name)
    return name  # Preserves staged evidence; does not touch any live system.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('init', 'rollback'):
        cmd = commands.add_parser(name)
        cmd.add_argument('workspace', type=Path)
    cmd = commands.add_parser('plan')
    cmd.add_argument('archive', type=Path)
    cmd.add_argument('--request', type=Path, required=True)
    cmd.add_argument('--target-root', type=Path)
    cmd.add_argument('--sha256')
    cmd.add_argument('--output', type=Path, required=True)
    for name in ('stage', 'validate'):
        cmd = commands.add_parser(name)
        cmd.add_argument('archive', type=Path)
        cmd.add_argument('plan', type=Path)
        cmd.add_argument('workspace', type=Path)
        if name == 'stage':
            cmd.add_argument('--dry-run', action='store_true')
        else:
            cmd.add_argument('--libnm', action='store_true', help='Require read-only libnm keyfile verification')
    args = parser.parse_args()
    try:
        if args.action == 'init':
            initialize(args.workspace)
            print('Initialized inactive workspace')
        elif args.action == 'rollback':
            print(rollback(args.workspace))
        elif args.action == 'plan':
            plan, _ = build_plan(args.archive, manifest.parse_json(args.request.read_bytes()), args.target_root, args.sha256)
            write_private(args.output, manifest.json_bytes(plan))
            print('Plan written; conflicts=' + str(len(plan['conflicts'])))
            return int(bool(plan['conflicts']))
        else:
            plan = manifest.parse_json(args.plan.read_bytes())
            if args.action == 'validate':
                payload = verified_plan(args.archive, plan)
                check_workspace(args.workspace)
                validate_generation(args.workspace / 'staged', plan, payload)
                if args.libnm:
                    print('libnm profiles verified: ' + str(validate_keyfiles(args.workspace / 'staged')))
                print('Inactive staged payload validated; activation not implemented')
            elif args.dry_run:
                verified_plan(args.archive, plan)
                check_workspace(args.workspace)
                print('Dry run passed; no staging writes')
            else:
                print(stage(args.archive, plan, args.workspace))
    except ValueError as exc:
        parser.exit(1, 'ERROR: ' + str(exc) + '\n')
    except (OSError, KeyError, TypeError, ImportError, tarfile.TarError):
        parser.exit(1, 'ERROR: recovery request rejected; check integrity, topology, dependencies, conflicts and private staging permissions.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
