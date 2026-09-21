#!/usr/bin/env python3
"""Repeatable, offline-capable PCS uplink installation. Never activates LAN profiles."""
import argparse
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from pcs_uplink_manager import CONFIG, load_config, atomic_json, interface_name, read_json

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = Path('/var/lib/pcs/uplink-backups')
INSTALL_FILES = [CONFIG, Path('/etc/systemd/system/pcs-uplink-manager.service'), Path('/usr/local/sbin/pcs-uplink-manager'), Path('/usr/local/lib/pcs/pcs_uplink_manager.py')]
MANAGEMENT_FILES = {
    'scripts/pcs_uplink_management.py': Path('/usr/local/lib/pcs/pcs_uplink_management.py'),
    'systemd/pcs-uplink-management.service': Path('/etc/systemd/system/pcs-uplink-management.service'),
    'networkmanager/92-pcs-uplink-management': Path('/etc/NetworkManager/dispatcher.d/92-pcs-uplink-management'),
}
MANAGEMENT_POLICY = Path('/etc/pcs/uplink-management.json')
INSTALL_FILES += list(MANAGEMENT_FILES.values()) + [MANAGEMENT_POLICY, Path('/usr/local/sbin/pcs-uplink-management'), Path('/usr/local/sbin/pcs-wireguard-firewall'), Path('/usr/local/sbin/pcs-stats-api-firewall')]


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=30).stdout.strip()


def candidates():
    rows = []
    for base in Path('/sys/class/net').iterdir():
        name = base.name
        if not interface_name(name) or name == 'wlan0' or (base / 'wireless').exists() or not (base / 'device').exists():
            continue
        try:
            kind = run('nmcli', '-g', 'GENERAL.TYPE', 'device', 'show', name)
            if kind != 'ethernet':
                continue
            # Debian omits sbin from an ordinary operator's PATH. Discovery
            # must work without sudo just as installation does with sudo.
            mac = run(shutil.which('ethtool') or '/usr/sbin/ethtool', '-P', name).split()[-1]
            if mac == '00:00:00:00:00:00':
                continue
            rows.append({'interface': name, 'mac': mac})
        except (OSError, subprocess.SubprocessError):
            continue
    return rows


def profile_uuid(name):
    return run('nmcli', '-g', 'connection.uuid', 'connection', 'show', name)


def service_state(name):
    return {key: subprocess.run(['systemctl', 'is-' + key, '--quiet', name], capture_output=True).returncode == 0 for key in ('enabled', 'active')}


def backup_installation(profiles):
    backup = BACKUPS / str(time.time_ns())
    backup.mkdir(parents=True, mode=0o700)
    files = list(INSTALL_FILES)
    import dbus
    bus = dbus.SystemBus()
    settings = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager/Settings'), 'org.freedesktop.NetworkManager.Settings')
    new_profiles = []
    for uuid in profiles:
        try:
            path = settings.GetConnectionByUuid(uuid)
        except dbus.DBusException as exc:
            if exc.get_dbus_name().endswith('InvalidConnection'):
                new_profiles.append(uuid)
                continue
            # Resolve missing profiles separately; permission errors must abort.
            if 'No connection' in str(exc) or 'not found' in str(exc):
                new_profiles.append(uuid)
                continue
            raise
        props = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', path), 'org.freedesktop.DBus.Properties')
        filename = str(props.Get('org.freedesktop.NetworkManager.Settings.Connection', 'Filename'))
        if not filename or not Path(filename).is_file():
            raise ValueError('setup requires persistent profiles so rollback can preserve their settings')
        files.append(Path(filename))
    manifest = {'files': [], 'new_profiles': new_profiles, 'services': {name: service_state(name) for name in ('pcs-cellular-fallback.service', 'pcs-uplink-manager.service', 'pcs-uplink-management.service')}}
    for index, path in enumerate(dict.fromkeys(files)):
        row = {'path': str(path), 'backup': str(index), 'exists': path.exists()}
        if path.exists():
            shutil.copy2(path, backup / str(index))
        manifest['files'].append(row)
    atomic_json(backup / 'manifest.json', manifest)
    return backup


def rollback(backup):
    backup = Path(backup).resolve(strict=True)
    if not backup.is_relative_to(BACKUPS.resolve()) or backup == BACKUPS.resolve():
        raise ValueError('rollback must name an installation backup directory')
    manifest = read_json(backup / 'manifest.json')
    if not isinstance(manifest, dict):
        raise ValueError('invalid rollback manifest')
    # Validate the entire write set before touching services/files.
    for row in manifest['files']:
        path = Path(row['path'])
        allowed_nm = any(path.parent == Path(root) for root in ('/etc/NetworkManager/system-connections', '/run/NetworkManager/system-connections'))
        if path not in INSTALL_FILES and not allowed_nm:
            raise ValueError('rollback target is outside managed paths')
        if Path(row['backup']).name != row['backup']:
            raise ValueError('invalid backup filename')
    subprocess.run(['systemctl', 'disable', '--now', 'pcs-uplink-manager.service'], capture_output=True)
    subprocess.run(['systemctl', 'disable', '--now', 'pcs-cellular-fallback.service'], capture_output=True)
    subprocess.run(['systemctl', 'disable', '--now', 'pcs-uplink-management.service'], capture_output=True)
    if Path('/usr/local/sbin/pcs-uplink-management').exists():
        run('/usr/local/sbin/pcs-uplink-management', '--clear')
    if CONFIG.exists() and Path('/usr/local/sbin/pcs-uplink-manager').exists():
        run('/usr/local/sbin/pcs-uplink-manager', '--restore-runtime')
    active_profiles = set(run('nmcli', '-g', 'UUID', 'connection', 'show', '--active').splitlines())
    for uuid in manifest['new_profiles']:
        if uuid in active_profiles:
            print('Retaining an active new WAN profile during rollback; session ownership is unknown.')
        else:
            subprocess.run(['nmcli', 'connection', 'delete', uuid], capture_output=True)
    for row in manifest['files']:
        path = Path(row['path'])
        if row['exists']:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / row['backup'], path)
        elif path.exists():
            path.unlink()
    run('nmcli', 'connection', 'reload')
    # The legacy marker cannot safely identify an activation after migration.
    Path('/run/pcs-cellular-fallback-owned').unlink(missing_ok=True)
    run('systemctl', 'daemon-reload')
    # Never restore both competing controllers, even from a broken snapshot.
    selected = 'pcs-uplink-manager.service' if CONFIG.exists() else 'pcs-cellular-fallback.service'
    saved = manifest['services'][selected]
    if saved['enabled']:
        run('systemctl', 'enable', selected)
    if saved['active']:
        run('systemctl', 'start', selected)
    management = manifest['services'].get('pcs-uplink-management.service', {})
    if management.get('enabled'):
        run('systemctl', 'enable', 'pcs-uplink-management.service')
        run('systemctl', 'start', 'pcs-uplink-management.service')
    print('Restored previous installation; ambiguous legacy ownership was cleared.')


def install(cfg, args, create):
    subprocess.run(['systemctl', 'disable', '--now', 'pcs-cellular-fallback.service'], capture_output=True, check=False)
    if service_state('pcs-cellular-fallback.service')['active']:
        raise RuntimeError('legacy fallback controller did not stop; no profiles were changed')
    run('systemctl', 'stop', 'pcs-uplink-manager.service') if Path('/etc/systemd/system/pcs-uplink-manager.service').exists() else None
    if create:
        uuid, mac = create
        try:
            profile_uuid(uuid)
        except subprocess.CalledProcessError:
            run('nmcli', 'connection', 'add', 'type', 'ethernet', 'ifname', '*', 'con-name', args.profile, 'connection.uuid', uuid, '802-3-ethernet.mac-address', mac, 'connection.autoconnect', 'no')
        run('nmcli', 'connection', 'modify', uuid, 'connection.interface-name', '', '802-3-ethernet.mac-address', mac, 'ipv4.method', 'auto', 'ipv4.addresses', '', 'ipv4.gateway', '', 'ipv4.routes', '', 'ipv4.never-default', 'no', 'ipv4.route-metric', '100', 'ipv6.method', 'auto', 'ipv6.addresses', '', 'ipv6.gateway', '', 'ipv6.routes', '', 'ipv6.never-default', 'no', 'ipv6.route-metric', '100', 'connection.autoconnect', 'yes')
    for u in cfg['uplinks']:
        if u['type'] == 'cellular' and u.get('profile'):
            run('nmcli', 'connection', 'modify', u['profile'], 'connection.autoconnect', 'no')
    target = Path('/usr/local/lib/pcs')
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'scripts/pcs_uplink_manager.py', target / 'pcs_uplink_manager.py')
    (target / 'pcs_uplink_manager.py').chmod(0o644)
    helper = Path('/usr/local/sbin/pcs-uplink-manager')
    helper.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/pcs/pcs_uplink_manager.py "$@"\n')
    helper.chmod(0o755)
    atomic_json(args.config, cfg)
    if getattr(args, 'management_policy', None) is not None:
        atomic_json(MANAGEMENT_POLICY, args.management_policy)
    for source, destination in MANAGEMENT_FILES.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, destination)
        destination.chmod(0o755 if 'dispatcher.d' in str(destination) else 0o644)
    management_helper = Path('/usr/local/sbin/pcs-uplink-management')
    management_helper.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/pcs/pcs_uplink_management.py "$@"\n')
    management_helper.chmod(0o755)
    for name in ('pcs-wireguard-firewall', 'pcs-stats-api-firewall'):
        destination = Path('/usr/local/sbin') / name
        if destination.exists():
            shutil.copy2(ROOT / 'scripts' / (name + '.sh'), destination)
            destination.chmod(0o755)
    shutil.copy2(ROOT / 'systemd/pcs-uplink-manager.service', '/etc/systemd/system/pcs-uplink-manager.service')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'pcs-uplink-management.service')
    started = time.time()
    run('systemctl', 'enable', '--now', 'pcs-uplink-manager.service')
    deadline = time.monotonic() + 30
    while True:
        run('systemctl', 'is-active', '--quiet', 'pcs-uplink-manager.service')
        status = read_json('/run/pcs-uplink-manager/status.json', {})
        if status.get('generated_at', 0) >= started and status.get('mode') == cfg['mode']:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('uplink observer did not publish a fresh status after installation')
        time.sleep(0.2)


def legacy_defaults(env, path=Path('/etc/pcs/cellular-fallback.conf')):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    def value(new, old, default):
        return env.get(new, parser.get('fallback', old, fallback=default))
    return {
        'version': 1,
        'mode': 'auto' if env.get('PCS_CELLULAR_FALLBACK_MODE', 'manual') in ('auto', 'wifi-fallback') else 'manual',
        'poll_seconds': int(value('PCS_CELLULAR_FALLBACK_POLL_SECONDS', 'poll_seconds', '10')),
        'failure_seconds': int(value('PCS_CELLULAR_FALLBACK_WIFI_LOSS_SECONDS', 'wifi_loss_seconds', '30')),
        'recovery_seconds': int(value('PCS_CELLULAR_FALLBACK_WIFI_RECOVERY_SECONDS', 'wifi_recovery_seconds', '30')),
        'uplinks': [
            {'id': 'wifi', 'name': 'Wi-Fi', 'type': 'wifi', 'priority': 2, 'interface': value('PCS_WIFI_IFACE', 'wifi_interface', 'wlan0')},
        ],
    }


def inherit_legacy_mode(existing, env, requested):
    """An explicit new policy always wins over an enabled legacy service."""
    return not existing and not requested and not env.get('PCS_UPLINK_MODE') and 'PCS_CELLULAR_FALLBACK_MODE' not in env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--list', action='store_true', help='List non-LAN Ethernet candidates without selecting one')
    parser.add_argument('--mode', choices=['auto', 'manual'])
    parser.add_argument('--interface', help='Explicit Ethernet NIC selection; use --list first')
    parser.add_argument('--mac', help='Permanent NIC MAC; permits staging absent hardware')
    parser.add_argument('--profile', default=os.environ.get('PCS_STARLINK_PROFILE', 'pcs-starlink-uplink'))
    parser.add_argument('--priority', help='Ordered comma-separated configured uplink IDs')
    parser.add_argument('--allow-management-from', action='append', help='Opt in Starlink Ethernet to a trusted private IPv4 subnet; repeat for more sources')
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--rollback', type=Path, help='Restore a backup made by this installer')
    args = parser.parse_args()
    if args.rollback:
        if os.geteuid() != 0:
            raise SystemExit('Rollback requires root.')
        rollback(args.rollback)
        return 0
    if args.list:
        print(json.dumps(candidates(), indent=2))
        return 0
    if args.check:
        cfg = load_config(args.config)
        print(f'Valid uplink configuration: {cfg.mode}; ' + ', '.join(u.name for u in cfg.uplinks))
        for u in cfg.uplinks:
            if u.profile:
                try:
                    print(f'{u.id}: profile {profile_uuid(u.profile)}')
                except subprocess.SubprocessError:
                    print(f'WARN: {u.id}: optional profile/device unavailable')
        print(json.dumps(read_json('/run/pcs-uplink-manager/status.json', {}), indent=2))
        return 0
    if os.geteuid() != 0:
        raise SystemExit('Run setup as root, or use setup-uplink-manager.sh.')
    if args.config != CONFIG:
        raise ValueError('custom --config paths are supported for --check only')
    # Verify dependencies before stopping any existing controller.
    import dbus  # noqa: F401
    run('nmcli', '--version')
    run('nft', '--version')
    env = os.environ
    existing = read_json(args.config)
    cfg = existing or legacy_defaults(env)
    cfg['mode'] = args.mode or env.get('PCS_UPLINK_MODE') or cfg['mode']
    if inherit_legacy_mode(existing, env, args.mode):
        old_enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', 'pcs-cellular-fallback.service'], check=False).returncode == 0
        if old_enabled:
            cfg['mode'] = 'auto'
    if not any(u['id'] == 'cellular' for u in cfg['uplinks']):
        legacy = configparser.ConfigParser(interpolation=None)
        legacy.read('/etc/pcs/cellular-fallback.conf')
        cell_name = env.get('PCS_CELLULAR_PROFILE', legacy.get('fallback', 'cellular_profile', fallback='pcs-cellular-profile'))
        try:
            uuid = profile_uuid(cell_name)
        except subprocess.SubprocessError:
            uuid = ''
        if uuid:
            cfg['uplinks'].append({'id': 'cellular', 'name': 'Cellular', 'type': 'cellular', 'priority': max(u['priority'] for u in cfg['uplinks']) + 1, 'profile': uuid, 'activation': 'fallback'})
    iface = args.interface or env.get('PCS_STARLINK_IFACE', '')
    mac = args.mac or env.get('PCS_STARLINK_MAC', '')
    if not iface and not mac and env.get('PCS_STARLINK_AUTODETECT', '').lower() in ('1', 'true', 'yes'):
        detected = candidates()
        if len(detected) > 1:
            raise ValueError('multiple Ethernet WAN candidates found; set PCS_STARLINK_MAC explicitly')
        if detected:
            iface = detected[0]['interface']
            mac = detected[0]['mac']
            print(f'Auto-detected commissioned Ethernet uplink: {iface} ({mac})')
        else:
            raise ValueError('no non-LAN Ethernet WAN candidate found; attach Starlink or set PCS_STARLINK_MAC')
    create = None
    if iface or mac:
        if iface and not interface_name(iface):
            raise ValueError('eth0 and invalid interface names cannot be WANs')
        devices = candidates()
        matches = [d for d in devices if (not iface or d['interface'] == iface) and (not mac or d['mac'].lower() == mac.lower())]
        if len(matches) > 1:
            raise ValueError('ambiguous NIC identity; select an interface and MAC explicitly')
        if iface and Path('/sys/class/net', iface).exists() and not matches:
            raise ValueError('selected interface is not an eligible Ethernet NIC')
        if matches:
            mac = matches[0]['mac']
        if not mac:
            raise ValueError('an absent NIC requires its MAC for persistent binding')
        # Reject the physical LAN identity even if someone renamed eth0.
        lan = Path('/sys/class/net/eth0/address')
        if lan.exists() and lan.read_text().strip().lower() == mac.lower():
            raise ValueError('the PCS LAN NIC cannot be selected as WAN')
        try:
            uuid = profile_uuid(args.profile)
            kind = run('nmcli', '-g', 'connection.type', 'connection', 'show', uuid)
            bound = run('nmcli', '-g', 'connection.interface-name', 'connection', 'show', uuid)
            method = run('nmcli', '-g', 'ipv4.method', 'connection', 'show', uuid)
            devices = run('nmcli', '-g', 'GENERAL.DEVICES', 'connection', 'show', uuid).split(',')
            if kind != '802-3-ethernet' or bound == 'eth0' or 'eth0' in devices or method == 'shared':
                raise ValueError('refusing to overwrite a non-WAN profile')
        except subprocess.CalledProcessError:
            import uuid as uuid_module
            uuid = str(uuid_module.uuid4())
        row = {'id': 'starlink', 'name': 'Starlink', 'type': 'ethernet', 'priority': 1, 'mac': mac.lower(), 'profile': uuid}
        old = next((u for u in cfg['uplinks'] if u['id'] == 'starlink'), None)
        if old:
            row['priority'] = old['priority']
            cfg['uplinks'] = [row if u['id'] == 'starlink' else u for u in cfg['uplinks']]
        else:
            cfg['uplinks'] = [row] + sorted(cfg['uplinks'], key=lambda u: u['priority'])
            for index, u in enumerate(cfg['uplinks'], 1):
                u['priority'] = index
        create = (uuid, mac)
    priority = args.priority or env.get('PCS_UPLINK_PRIORITY')
    if priority:
        ids = priority.split(',')
        configured = {u['id'] for u in cfg['uplinks']}
        # Optional Starlink may be listed in the reusable install config.
        ids = [i for i in ids if i != 'starlink' or i in configured]
        if len(ids) != len(set(ids)) or set(ids) != configured:
            raise ValueError('priority must name every configured uplink exactly once')
        for u in cfg['uplinks']:
            u['priority'] = ids.index(u['id']) + 1
    with tempfile.TemporaryDirectory() as folder:
        proposed = Path(folder) / 'uplinks.json'
        atomic_json(proposed, cfg)
        checked = load_config(proposed)
        if args.allow_management_from:
            from pcs_uplink_management import validate_policy
            policy = read_json(MANAGEMENT_POLICY, {'version': 1, 'trusted': []})
            policy['trusted'] = [r for r in policy['trusted'] if r.get('uplink') != 'starlink'] + [{'uplink': 'starlink', 'sources': args.allow_management_from}]
            validate_policy(policy, checked)
            args.management_policy = policy
    for u in cfg['uplinks']:
        if u['type'] == 'cellular' and u.get('profile'):
            if run('nmcli', '-g', 'connection.type', 'connection', 'show', u['profile']) != 'gsm':
                raise ValueError('configured cellular profile is not a GSM profile')
    profiles = {u['profile'] for u in cfg['uplinks'] if u.get('profile') and (u['type'] == 'cellular' or create and u['profile'] == create[0])}
    backup = backup_installation(profiles)
    try:
        install(cfg, args, create)
    except Exception:
        rollback(backup)
        raise
    print(f'Uplink manager installed in {cfg["mode"]} mode; backup: {backup}')
    print('Legacy sessions were preserved without adopting their ownership.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
