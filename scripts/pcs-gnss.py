#!/usr/bin/env python3
"""Explicit PCS GNSS preparation, enrollment, activation and recovery CLI."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import socket
import stat
import subprocess
import sys
import time

# A source candidate must use its own policy, even when older libraries are
# already installed on PCS. The installed sbin entry point uses the managed lib.
sys.path.insert(0, str(Path(__file__).resolve().parent) if Path(__file__).with_name('pcs_gnss.py').is_file()
                else '/usr/local/lib/pcs')
from pcs_gnss import (CONFIG, RUNTIME, KEYS, OUTPUT_KEYS, BAUDS, LEGACY, NEO, atomic_json, decode_values,
                      load_config, match_receiver, mon_ver, pps_devices, read_status,
                      ubx, UBXStream, validate_config, valget, valset, required_values, export_report)

REPO = Path(__file__).resolve().parent.parent
BACKUPS = Path('/var/backups/pcs-gnss')
STAGES = Path('/opt/pcs-gnss')
ACTIVATION = Path('/etc/pcs/gnss-activation.json')
SERVICES = ('pcs-gnss.service', 'gpsd.service', 'gpsd.socket', 'chrony.service', 'pcs-wwan-gps-nmea.service')
MANAGED = ('/etc/pcs/gnss.json', '/etc/default/gpsd', '/etc/chrony/conf.d/pcs-wwan-gps.conf',
           '/etc/pcs/gnss-activation.json',
           '/etc/chrony/conf.d/pcs-gnss.conf', '/etc/systemd/system/pcs-gnss.service',
           '/etc/systemd/system/gpsd.service.d/pcs-gnss.conf',
           '/etc/systemd/system/chrony.service.d/pcs-gnss.conf',
           '/etc/systemd/system/pcs-wwan-gps-nmea.service.d/pcs-gnss.conf',
           '/etc/udev/rules.d/78-pcs-gnss.rules', '/usr/local/lib/pcs/pcs_gnss.py',
           '/etc/apparmor.d/local/usr.sbin.chronyd', '/etc/apparmor.d/local/usr.sbin.gpsd',
           '/usr/local/lib/pcs/pcs_gnss_runtime.py', '/usr/local/sbin/pcs-gnss')


def command(args, check=True, timeout=10):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return result


def usb_devices():
    devices = []
    for path in sorted(Path('/sys/class/tty').glob('ttyUSB*')):
        result = command(['udevadm', 'info', '--query=property', '--path=' + str(path)], check=False)
        props = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        devices.append({'device': '/dev/' + path.name, 'vid': props.get('ID_VENDOR_ID', ''),
                        'pid': props.get('ID_MODEL_ID', ''), 'serial': props.get('ID_SERIAL_SHORT', ''),
                        'interface': props.get('ID_USB_INTERFACE_NUM', ''),
                        'id_path': props.get('ID_PATH', '')})
    return devices


@contextlib.contextmanager
def serial_port(path, baud=38400):
    import fcntl
    import termios
    if baud not in BAUDS:
        raise ValueError('Unsupported host baud')
    # Check existing open handles before TIOCEXCL (which only prevents future opens).
    if not stat.S_ISCHR(os.stat(path).st_mode):
        raise ValueError('Receiver must be a character device')
    occupied = command(['fuser', str(path)], check=False)
    if occupied.returncode != 1 or occupied.stdout.strip():
        raise ValueError('Serial device is owned; release GPSD/other consumers first')
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    original = termios.tcgetattr(fd)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = getattr(termios, 'B' + str(baud))
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)
        fcntl.ioctl(fd, termios.TIOCNXCL)
        os.close(fd)


class ReceiverTimeout(ValueError):
    pass


def exchange(fd, request, cls, msg, ack=None):
    import select
    os.write(fd, request)
    decoder = UBXStream()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], 0.1)[0]:
            continue
        for group, number, payload in decoder.feed(os.read(fd, 16384)):
            if group == 5 and number == 0 and payload == request[2:4]:
                raise ValueError('Receiver rejected UBX request')
            if (group, number) == (cls, msg) and (ack is None or payload == ack):
                return payload
    raise ReceiverTimeout('Receiver did not answer at selected host baud')


def receiver_values(fd):
    keys = [key for key, _ in KEYS.values()] + list(OUTPUT_KEYS.values())
    values = decode_values(exchange(fd, valget(keys), 6, 0x8B))
    if set(values) != set(keys):
        raise ValueError('Incomplete configuration readback')
    return values


@contextlib.contextmanager
def identified_receiver(path, preferred=115200, fallback=True):
    """Query only the enrolled unowned port, changing host termios, never baud keys."""
    speeds = [preferred] + ([baud for baud in BAUDS if baud != preferred] if fallback else [])
    for baud in speeds:
        with serial_port(path, baud) as fd:
            try:
                identity = mon_ver(exchange(fd, ubx(10, 4), 10, 4))
            except ReceiverTimeout:
                continue
            # A different model/protocol, NAK or owned port is not a baud retry.
            values = receiver_values(fd)
            if values[KEYS['baud'][0]] != baud:
                raise ValueError('UART1 baud readback disagrees with host communication')
            yield fd, identity, values, baud
            return
    raise ValueError('No supported NEO-F10N response at admitted host baud(s)')


def configure_receiver(fd, desired):
    before = receiver_values(fd)
    changes = {key: value for key, value in desired.items() if before.get(key) != value}
    if KEYS['baud'][0] in changes or any(key not in required_values() for key in changes):
        raise ValueError('Only required timing RAM changes are permitted; preserve baud/message profile')
    if not changes:
        return before
    try:
        exchange(fd, valset(changes), 5, 1, b'\x06\x8a')
        after = receiver_values(fd)
        if any(after.get(key) != value for key, value in desired.items()):
            raise ValueError('Receiver RAM readback mismatch')
    except Exception:
        exchange(fd, valset({key: before[key] for key in changes}), 5, 1, b'\x06\x8a')
        raise
    return before


def udev_rule(binding):
    # validate_config has already constrained interpolated udev values.
    match = 'ENV{ID_SERIAL_SHORT}=="' + binding['serial'] + '"' if binding['serial'] else 'ENV{ID_PATH}=="' + binding['id_path'] + '"'
    return ('# Enrolled PCS GNSS interface only; generic CH340 matches are forbidden.\n'
            'ACTION=="add|change", SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="' + binding['vid'] + '", '
            'ENV{ID_MODEL_ID}=="' + binding['pid'] + '", ' + match +
            ', ENV{ID_MM_PORT_IGNORE}="1", SYMLINK+="pcs-gnss-neo"\n')


def chrony_config(config):
    return ('# PCS NEO-F10N; independent of position/manual and LTE state.\n'
            'refclock SOCK /run/chrony/pcs-f10n.sock refid F10N poll 0 filter 1 '
            f'offset {config["serial_offset"]:.9f} delay {config["serial_delay"]:.9f} '
            'precision 1e-1 prefer noselect\n'
            'include /run/pcs-gnss/pps.conf\n')


def prepare_pps():
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    devices = pps_devices()
    alias = RUNTIME / 'pps'
    alias.unlink(missing_ok=True)
    text = '# No uniquely identified GPIO26 PPS; serial/network fallback only.\n'
    if len(devices) == 1:
        alias.symlink_to(devices[0])
        text = 'refclock PPS /run/pcs-gnss/pps refid F10P lock F10N maxlockage 2 poll 0 filter 1 prefer noselect\n'
    write_file(RUNTIME / 'pps.conf', text, 0o644)


def prepare_receiver():
    config = load_config()
    if config['mode'] != NEO:
        raise ValueError('NEO-F10N is not explicitly selected')
    device = match_receiver(usb_devices(), config['receiver'])['device']
    if not Path('/dev/pcs-gnss-neo').exists() or os.stat(device).st_rdev != os.stat('/dev/pcs-gnss-neo').st_rdev:
        raise ValueError('Enrolled USB alias does not match receiver')
    # Remove stale options before probing, so failure cannot reuse old admission.
    (RUNTIME / 'gpsd.env').unlink(missing_ok=True)
    with identified_receiver(device, config.get('baud', 38400)) as (fd, identity, before, baud):
        if identity['protocol'] != config['receiver']['protocol']:
            raise ValueError('Receiver protocol changed')
        # Save before applying, not afterwards; preserve first pre-profile state.
        if not (RUNTIME / 'receiver-before.json').exists():
            atomic_json(RUNTIME / 'receiver-before.json', {str(key): value for key, value in before.items()})
        configure_receiver(fd, required_values())
        write_file(RUNTIME / 'gpsd.env', gpsd_environment(baud))


def gpsd_environment(baud):
    if baud not in BAUDS:
        raise ValueError('Unsupported GPSD host baud')
    return f'GPSD_OPTIONS="-n -b -s {baud} -f 8N1"\n'


def write_file(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apparmor(install=False):
    rules = {'chronyd': '/run/pcs-gnss/pps.conf r,\n', 'gpsd': '/dev/pcs-gnss-neo rw,\n'}
    for daemon, rule in rules.items():
        profile = Path('/etc/apparmor.d/usr.sbin.' + daemon)
        if not profile.exists():
            continue
        local = Path('/etc/apparmor.d/local/usr.sbin.' + daemon)
        if install:
            if '<local/usr.sbin.' + daemon + '>' not in profile.read_text():
                raise ValueError('AppArmor local include missing; review installed profile')
            existing = local.read_text() if local.exists() else ''
            if '# PCS GNSS optional' not in existing:
                write_file(local, existing + '\n# PCS GNSS optional\n' + rule)
        enabled = Path('/sys/module/apparmor/parameters/enabled')
        if enabled.exists() and enabled.read_text().strip() == 'Y':
            command(['apparmor_parser', '-r', str(profile)])


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def file_state(path):
    path = Path(path)
    if path.is_symlink():
        return {'symlink': os.readlink(path)}
    if path.is_file():
        return {'hash': hashlib.sha256(path.read_bytes()).hexdigest(), 'mode': stat.S_IMODE(path.stat().st_mode)}
    if path.exists():
        raise ValueError('Unexpected managed path type')
    return None


def preflight(config, gpio_confirmed=False):
    errors = []
    inventory = usb_devices()
    versions = {}
    for name, args in [('gpsd', ['gpsd', '-V']), ('chrony', ['chronyd', '-v'])]:
        try:
            result = command(args)
            versions[name] = result.stdout.strip() or result.stderr.strip()
            version = re.search(r'(\d+)\.(\d+)', versions[name])
            minimum = (3, 25) if name == 'gpsd' else (4, 6)
            if not version or tuple(map(int, version.groups())) < minimum:
                errors.append(name + ' version below validated capability baseline')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            errors.append(name + ' unavailable')
    try:
        parsed = command(['chronyd', '-p', '-f', '/etc/chrony/chrony.conf']).stdout
        references = [line for line in parsed.splitlines() if line.strip().startswith('refclock ')]
        permitted = re.compile(r'^refclock SHM 0 refid GPS\b')
        if any(not permitted.match(line) for line in references):
            errors.append('Custom/reflicting refclocks require operator review')
        if len(references) > 1:
            errors.append('Duplicate legacy refclocks')
        legacy_file = Path('/etc/chrony/conf.d/pcs-wwan-gps.conf')
        legacy_lines = [line for line in legacy_file.read_text().splitlines() if line.startswith('refclock ')] if legacy_file.exists() else []
        if references != legacy_lines:
            errors.append('Legacy refclock must belong exclusively to the managed WWAN drop-in')
        main_text = Path('/etc/chrony/chrony.conf').read_text()
        if not re.search(r'^\s*confdir\s+/etc/chrony/conf.d\s*(?:#.*)?$', main_text, re.M):
            errors.append('Chrony conf.d include must be present')
        for required in ('pool ', 'local stratum 10', 'rtcsync'):
            if required not in parsed:
                errors.append('Missing existing timing fallback: ' + required)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        errors.append('Chrony configuration cannot be parsed')
    device = None
    if config['mode'] == NEO:
        try:
            device = match_receiver(inventory, config['receiver'])['device']
        except ValueError as exc:
            errors.append(str(exc))
        if len(pps_devices()) != 1:
            errors.append('Unique GPIO26 kernel PPS device required for initial activation')
        if not gpio_confirmed:
            errors.append('Operator physical GPIO26/startup wiring confirmation required')
        try:
            pins = command(['pinctrl', 'get', '26']).stdout
            if not re.search(r'\bip\b', pins) or not re.search(r'\bpn\b', pins):
                errors.append('GPIO26 must be input with no pull')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            errors.append('GPIO26 input/bias could not be verified')
    states = {path: file_state(path) for path in MANAGED}
    return {'version': 1, 'config_digest': config_digest(config), 'gpio_confirmed': gpio_confirmed,
            'device': device, 'pps': pps_devices(), 'versions': versions,
            'files': states, 'blockers': errors, 'created_epoch': time.time(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def stage():
    source = Path(__file__).resolve().parent
    paths = [source / name for name in ('pcs_gnss.py', 'pcs_gnss_runtime.py', 'pcs-gnss.py')]
    paths += [REPO / 'systemd/pcs-gnss.service' if (REPO / 'systemd/pcs-gnss.service').exists() else source / 'pcs-gnss.service']
    digest = hashlib.sha256(b''.join(path.read_bytes() for path in paths)).hexdigest()[:16]
    target = STAGES / digest
    target.mkdir(parents=True, exist_ok=True, mode=0o755)
    for path in paths:
        destination = target / path.name
        if destination.exists():
            if destination.read_bytes() != path.read_bytes():
                raise ValueError('Stage content conflict')
            continue
        shutil.copyfile(path, destination)
        os.chmod(destination, 0o644)
    return target


def backup():
    import uuid
    target = BACKUPS / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    target.mkdir(parents=True, mode=0o700)
    files = {}
    for index, item in enumerate(MANAGED):
        path = Path(item)
        entry = file_state(path)
        if entry is not None:
            entry.update(uid=path.lstat().st_uid, gid=path.lstat().st_gid, backup=str(index))
            if 'hash' in entry:
                shutil.copy2(path, target / str(index))
        files[item] = entry
    services = {}
    for unit in SERVICES:
        services[unit] = {'enabled': command(['systemctl', 'is-enabled', unit], check=False).stdout.strip(),
                          'active': command(['systemctl', 'is-active', unit], check=False).stdout.strip()}
    receipt = {'version': 1, 'files': files, 'services': services, 'complete': False,
               'legacy_receiver': legacy_receiver()}
    atomic_json(target / 'transaction.json', receipt)
    return target, receipt


def legacy_receiver(path=Path('/etc/default/gpsd')):
    """Capture the current EM7565 NMEA port without opening it or executing shell."""
    try:
        match = re.search(r'^DEVICES=(.*)$', path.read_text(), re.M)
        ports = shlex.split(shlex.split(match[1])[0]) if match else []
        if len(ports) != 1:
            return None
        rows = [item for item in usb_devices() if os.path.realpath(item['device']) == os.path.realpath(ports[0]) and item['vid'] == '1199']
        return rows[0] | {'configured_device': ports[0]} if len(rows) == 1 else None
    except (OSError, ValueError, IndexError):
        return None


def legacy_matches(binding):
    if not binding:
        return False
    try:
        current = match_receiver(usb_devices(), binding)
        return (os.path.realpath(current['device']) == os.path.realpath(binding.get('configured_device', binding['device']))
                and current.get('interface') == binding.get('interface'))
    except (ValueError, OSError):
        return False


def restore(target):
    target = target.resolve(strict=True)
    if target.parent != BACKUPS.resolve() or target.is_symlink():
        raise ValueError('Rollback must name a PCS GNSS backup directory')
    receipt = json.loads((target / 'transaction.json').read_text())
    if receipt.get('version') != 1 or set(receipt['files']) != set(MANAGED):
        raise ValueError('Invalid rollback receipt')
    command(['systemctl', 'stop', 'pcs-gnss.service', 'gpsd.service', 'gpsd.socket'], check=False)
    # Restore original receiver RAM before restoring the GPSD configuration.
    profile = target / 'receiver-before.json'
    receiver_restored = not profile.exists()
    try:
        active = load_config()
    except (ValueError, OSError):
        active = None
    if profile.exists() and active and active['mode'] == NEO:
        try:
            device = match_receiver(usb_devices(), active['receiver'])['device']
            with identified_receiver(device, active.get('baud', 38400)) as (fd, identity, values, baud):
                configure_receiver(fd, {int(key): value for key, value in json.loads(profile.read_text()).items()})
            receiver_restored = True
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            # Absence cannot prevent restoring software/network NTP. Keep the
            # private RAM snapshot for a later explicit receiver restoration.
            receiver_restored = False
    for index, (item, entry) in enumerate(receipt['files'].items()):
        path = Path(item)
        path.unlink(missing_ok=True)
        if entry is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if 'symlink' in entry:
                path.symlink_to(entry['symlink'])
                os.lchown(path, entry['uid'], entry['gid'])
            else:
                shutil.copy2(target / entry['backup'], path)
                os.chown(path, entry['uid'], entry['gid'])
    command(['systemctl', 'daemon-reload'])
    command(['udevadm', 'control', '--reload-rules'])
    apparmor()
    legacy_safe = legacy_matches(receipt.get('legacy_receiver'))
    # Never start/restart the WWAN helper (it configures modem GNSS). Its existing
    # active/exited state is preserved because activation never stopped it.
    for unit, state in receipt['services'].items():
        if unit == 'pcs-wwan-gps-nmea.service':
            continue
        if unit in ('gpsd.service', 'gpsd.socket') and not legacy_safe:
            command(['systemctl', 'disable', unit], check=False)
            continue
        if state['enabled'] in ('enabled', 'disabled'):
            command(['systemctl', 'enable' if state['enabled'] == 'enabled' else 'disable', unit], check=False)
        if state['active'] == 'active':
            if unit in ('gpsd.service', 'gpsd.socket') and not legacy_safe:
                # Restore configuration but never open a renumbered/foreign
                # serial port. Network/local timing continues independently.
                continue
            command(['systemctl', 'restart' if unit == 'chrony.service' else 'start', unit])
        else:
            command(['systemctl', 'stop', unit], check=False)
    receipt['receiver_ram_restored'] = receiver_restored
    receipt['legacy_gpsd_restored'] = legacy_safe
    return receipt


def activate(config, receipt_path, confirmed):
    active = load_config()
    if active == config and active['mode'] == NEO:
        marker = json.loads(ACTIVATION.read_text())
        if not marker.get('complete') or marker.get('config_digest') != config_digest(config):
            raise ValueError('Incomplete activation; restore its private transaction backup')
        return {'state': 'already-active', 'changes': False}
    if active['mode'] != LEGACY:
        raise ValueError('Rollback existing activation before changing enrollment')
    previous = json.loads(receipt_path.read_text())
    current = preflight(config, confirmed)
    if current['blockers']:
        raise ValueError('; '.join(current['blockers']))
    if (not 0 <= time.time() - previous['created_epoch'] <= 600
            or any(previous.get(key) != current[key] for key in ('config_digest', 'gpio_confirmed', 'device', 'pps', 'files', 'versions', 'boot_id'))):
        raise ValueError('Preflight receipt expired or environment changed')
    if config['mode'] != NEO:
        raise ValueError('Use rollback to restore legacy configuration')
    staged = stage()
    target, receipt = backup()
    try:
        command(['systemctl', 'stop', 'gpsd.service', 'gpsd.socket'])
        binding = config['receiver']
        device = match_receiver(usb_devices(), binding)['device']
        # Capture receiver RAM before any authorized profile changes.
        with identified_receiver(device, config.get('baud', 38400)) as (fd, identity, values, baud):
            if identity['protocol'] != binding['protocol']:
                raise ValueError('Enrolled receiver changed')
            atomic_json(target / 'receiver-before.json', {str(key): value for key, value in values.items()})
        for name in ('pcs_gnss.py', 'pcs_gnss_runtime.py'):
            write_file(Path('/usr/local/lib/pcs') / name, (staged / name).read_text())
        write_file('/usr/local/sbin/pcs-gnss', (staged / 'pcs-gnss.py').read_text(), 0o755)
        write_file('/etc/systemd/system/pcs-gnss.service', (staged / 'pcs-gnss.service').read_text())
        atomic_json(CONFIG, config)
        write_file('/etc/udev/rules.d/78-pcs-gnss.rules', udev_rule(binding))
        command(['udevadm', 'control', '--reload-rules'])
        command(['udevadm', 'trigger', '--action=add', '/sys/class/tty/' + Path(device).name])
        command(['udevadm', 'settle', '--timeout=5'])
        write_file('/etc/default/gpsd', 'START_DAEMON="true"\n' + gpsd_environment(baud) + 'DEVICES="/dev/pcs-gnss-neo"\nUSBAUTO="false"\nGPSD_SOCKET="/run/gpsd.sock"\n')
        write_file('/etc/systemd/system/gpsd.service.d/pcs-gnss.conf', '[Unit]\nAfter=chrony.service\n[Service]\nEnvironmentFile=-/run/pcs-gnss/gpsd.env\nExecStartPre=/usr/local/sbin/pcs-gnss prepare-receiver\n')
        write_file('/etc/systemd/system/chrony.service.d/pcs-gnss.conf', '[Service]\nExecStartPre=+/usr/local/sbin/pcs-gnss prepare-pps\n')
        write_file('/etc/systemd/system/pcs-wwan-gps-nmea.service.d/pcs-gnss.conf', '[Service]\nExecCondition=/usr/local/sbin/pcs-gnss legacy-condition\n')
        Path('/etc/chrony/conf.d/pcs-wwan-gps.conf').unlink(missing_ok=True)
        write_file('/etc/chrony/conf.d/pcs-gnss.conf', chrony_config(config))
        prepare_pps()
        apparmor(install=True)
        command(['chronyd', '-p', '-f', '/etc/chrony/chrony.conf'])
        command(['systemctl', 'daemon-reload'])
        command(['systemd-analyze', 'verify', '/etc/systemd/system/pcs-gnss.service'])
        command(['systemctl', 'disable', 'gpsd.socket'], check=False)
        command(['systemctl', 'enable', 'gpsd.service', 'pcs-gnss.service'])
        command(['systemctl', 'restart', 'chrony.service'])
        command(['systemctl', 'start', 'gpsd.service'])
        command(['systemctl', 'start', 'pcs-gnss.service'])
        receipt.update(complete=True, mode=NEO)
        atomic_json(target / 'transaction.json', receipt)
        atomic_json(ACTIVATION, {'complete': True, 'config_digest': config_digest(config), 'backup': str(target)})
        return {'state': 'activated-awaiting-acceptance', 'rollback': str(target)}
    except Exception:
        try:
            restore(target)
        except Exception as rollback_error:
            raise RuntimeError('Activation and rollback failed; use private backup ' + str(target)) from rollback_error
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'plan', 'stage', 'probe', 'export-report', 'activate', 'status', 'rollback', 'prepare-receiver', 'prepare-pps', 'legacy-condition'))
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--device')
    parser.add_argument('--baud', choices=('auto', '115200', '38400'), default='auto', help='Host probe speed only; auto tries 115200 then 38400 on this one port')
    parser.add_argument('--stock', type=Path)
    parser.add_argument('--finished', type=Path)
    parser.add_argument('--gpio26-confirmed', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.action in ('activate', 'rollback', 'stage', 'prepare-receiver', 'prepare-pps', 'probe') and os.geteuid() != 0:
        parser.error('This explicit operation requires root')
    if args.action in ('activate', 'rollback'):
        import fcntl
        RUNTIME.mkdir(parents=True, exist_ok=True)
        lock = (RUNTIME / 'installation.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'activate':
            import signal
            def interrupted(*_):
                raise RuntimeError('Installation interrupted; restoring transaction backup')
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
    if args.action == 'legacy-condition':
        return 0 if load_config()['mode'] == LEGACY else 1
    if args.action in ('preflight', 'plan'):
        result = preflight(load_config(args.config), args.gpio26_confirmed)
    elif args.action == 'stage':
        result = {'stage': str(stage()), 'active': False}
    elif args.action == 'status':
        result = read_status()
    elif args.action == 'export-report':
        if not args.stock or not args.finished:
            parser.error('--stock and --finished are required')
        result = export_report(args.stock, args.finished)
    elif args.action == 'probe':
        if not args.device:
            parser.error('--device is required; no automatic serial scanning')
        candidates = [item for item in usb_devices() if item['device'] == args.device]
        if len(candidates) != 1:
            parser.error('Select one discovered USB serial interface')
        preferred = 115200 if args.baud == 'auto' else int(args.baud)
        with identified_receiver(args.device, preferred, args.baud == 'auto') as (fd, identity, values, baud):
            result = {'receiver': {key: candidates[0][key] for key in ('vid', 'pid', 'serial', 'id_path')} | identity,
                      'baud': baud, 'values': values,
                      'required_ram_changes': {str(key): value for key, value in required_values().items() if values.get(key) != value}}
            # Keep the enrollment binding compatible; firmware/hardware are evidence.
            result['identity'] = identity
            result['receiver'] = {key: value for key, value in result['receiver'].items() if key not in ('firmware', 'hardware')}
    elif args.action == 'activate':
        if not args.receipt:
            parser.error('--receipt is required')
        result = activate(load_config(args.config), args.receipt, args.gpio26_confirmed)
    elif args.action == 'rollback':
        if not args.backup:
            parser.error('--backup is required')
        restored = restore(args.backup)
        result = {'state': 'restored' if restored['legacy_gpsd_restored'] else 'restored-awaiting-legacy-admission', 'backup': str(args.backup),
                  'receiver_ram_restored': restored['receiver_ram_restored'],
                  'legacy_gpsd_restored': restored['legacy_gpsd_restored']}
    else:
        prepare_receiver() if args.action == 'prepare-receiver' else prepare_pps()
        result = {'state': 'prepared'}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    if args.action in ('preflight', 'plan') and result['blockers']:
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print('GNSS operation refused: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, subprocess.SubprocessError):
        # No raw receiver data, coordinates, device credentials or command output.
        print('GNSS operation failed; no acceptance claimed. Inspect preflight and private transaction backup.', file=sys.stderr)
        raise SystemExit(1)
