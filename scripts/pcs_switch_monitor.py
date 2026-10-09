#!/usr/bin/env python3
"""Optional read-only Net-SNMP polling. Never changes links, routes or services."""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import string
import subprocess
import tempfile
import time
import uuid

CONFIG = Path('/etc/pcs/switch-monitor.json')
CACHE = Path('/run/pcs-switch-monitor/status.json')
OIDS = {'sys_descr': '1.3.6.1.2.1.1.1.0', 'sys_object_id': '1.3.6.1.2.1.1.2.0',
        'uptime': '1.3.6.1.2.1.1.3.0', 'descr': '1.3.6.1.2.1.2.2.1.2',
        'admin': '1.3.6.1.2.1.2.2.1.7', 'oper': '1.3.6.1.2.1.2.2.1.8',
        'last_change': '1.3.6.1.2.1.2.2.1.9', 'speed': '1.3.6.1.2.1.2.2.1.5',
        'name': '1.3.6.1.2.1.31.1.1.1.1', 'in_errors': '1.3.6.1.2.1.2.2.1.14',
        'out_errors': '1.3.6.1.2.1.2.2.1.20'}
STATES = {'up', 'down', 'administratively_down', 'unknown', 'stale', 'monitor_unavailable'}
DEFAULTS = dict(version=1, enabled=False, address='10.42.0.4', snmp_version='2c',
                community_file='/etc/pcs/switch-monitor.community', poll_seconds=5,
                timeout_seconds=1, retries=0, stale_seconds=20, mapping=None,
                traps_enabled=False)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_config(path=CONFIG):
    if not Path(path).exists():
        return DEFAULTS.copy()
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) or raw.get('version') != 1:
        raise ValueError('invalid switch configuration schema')
    cfg = DEFAULTS | raw
    if type(cfg['enabled']) is not bool or cfg['traps_enabled'] is not False:
        raise ValueError('polling-only implementation; traps must remain disabled')
    address = ipaddress.IPv4Address(cfg['address'])
    if not any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('switch address must be explicit RFC1918 IPv4')
    if cfg['snmp_version'] not in ('1', '2c'):
        raise ValueError('only SNMPv1 and v2c are supported')
    if cfg['community_file'] != DEFAULTS['community_file']:
        raise ValueError('use the fixed protected credential path')
    for key, low, high in [('poll_seconds', 2, 60), ('timeout_seconds', 1, 5),
                            ('retries', 0, 2), ('stale_seconds', 5, 180)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError('invalid polling bound: ' + key)
    if cfg['stale_seconds'] < 2 * cfg['poll_seconds']:
        raise ValueError('staleness must allow at least two polling intervals')
    mapping = cfg['mapping']
    if mapping is not None:
        if not isinstance(mapping, dict) or set(mapping) != {'physical_port', 'ifindex', 'identity', 'verified_at'}:
            raise ValueError('invalid verified mapping')
        if type(mapping['physical_port']) is not int or not 1 <= mapping['physical_port'] <= 8:
            raise ValueError('invalid physical port')
        if type(mapping['ifindex']) is not int or not 1 <= mapping['ifindex'] <= 65535:
            raise ValueError('invalid SNMP index')
        if not re.fullmatch('[0-9a-f]{64}', str(mapping['identity'])) or not isinstance(mapping['verified_at'], (int, float)):
            raise ValueError('mapping requires verified identity and timestamp')
    return cfg


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temp.chmod(0o640)
    os.replace(temp, path)


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def credential(cfg):
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    path = Path(directory) / 'community' if directory else Path(cfg['community_file'])
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 32:
        raise ValueError('invalid credential file')
    if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid not in (0, os.geteuid())):
        raise ValueError('credential must be private and owned by root or the credential service')
    value = path.read_text(encoding='ascii').strip()
    if not re.fullmatch('[A-Za-z0-9]{1,16}', value):
        raise ValueError('credential must be 1..16 alphanumeric characters')
    return value


def parse_output(text):
    if len(text) > 65536:
        raise ValueError('oversized SNMP response')
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\.?([0-9]+(?:\.[0-9]+)+)\s+(.+)', line.strip())
        if not match:
            raise ValueError('malformed SNMP response')
        oid, value = match.groups()
        if oid in result:
            raise ValueError('duplicate SNMP object')
        if value.startswith(('No Such', 'No more', 'NULL')):
            continue
        quoted = value.startswith('"')
        value = value.strip('"')
        if not quoted and re.fullmatch(r'[0-9]{1,20}', value):
            result[oid] = int(value)
        elif len(value) <= 256 and all(c.isprintable() for c in value):
            result[oid] = value
        else:
            raise ValueError('invalid SNMP value')
    return result


class NetSnmp:
    """Credentials go in a private config, never argv, environment or error logs."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.secret = credential(cfg)

    def request(self, oids, deadline, walk=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('poll budget exhausted')
        with tempfile.TemporaryDirectory(prefix='pcs-snmp-') as directory:
            folder = Path(directory)
            config = folder / 'snmp.conf'
            config.write_text('defCommunity ' + self.secret + '\n', encoding='ascii')
            config.chmod(0o600)
            command = ['/usr/bin/snmpwalk' if walk else '/usr/bin/snmpget', '-v', self.cfg['snmp_version'],
                       '-On', '-Oq', '-Oe', '-Ot', '-m', '', '-r', str(self.cfg['retries']),
                       '-t', str(min(self.cfg['timeout_seconds'], remaining / (self.cfg['retries'] + 1))),
                       'udp:' + self.cfg['address'] + ':161', *oids]
            def limits():
                import resource
                resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
            with (folder / 'output').open('w+b') as output:
                try:
                    result = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL,
                        env={'PATH': '/usr/bin:/bin', 'SNMPCONFPATH': directory, 'HOME': directory, 'MIBS': ''},
                        timeout=remaining, preexec_fn=limits if os.name == 'posix' else None)
                except (OSError, subprocess.SubprocessError):
                    raise TimeoutError('SNMP request unavailable') from None
                if result.returncode:
                    raise TimeoutError('SNMP request unavailable')
                output.seek(0)
                text = output.read(65537).decode('utf-8', errors='replace').replace(self.secret, '<redacted>')
        return parse_output(text)


def sample(cfg, transport, now=None):
    deadline = time.monotonic() + cfg['poll_seconds']
    base = transport.request([OIDS[k] for k in ('sys_descr', 'sys_object_id', 'uptime')], deadline)
    inventory = transport.request([OIDS['descr']], deadline, walk=True)
    ports = {}
    for oid, value in inventory.items():
        if not oid.startswith(OIDS['descr'] + '.'):
            raise ValueError('unexpected interface inventory')
        index = int(oid.rsplit('.', 1)[1])
        if not 1 <= index <= 65535 or not isinstance(value, str):
            raise ValueError('invalid interface inventory')
        ports[str(index)] = {'descr': value}
    if not 1 <= len(ports) <= 128:
        raise ValueError('empty or oversized interface inventory')
    indices = [cfg['mapping']['ifindex']] if cfg['mapping'] else [int(i) for i in ports]
    for index in indices:
        if str(index) not in ports:
            continue
        required = [OIDS[k] + '.' + str(index) for k in ('admin', 'oper')]
        values = transport.request(required, deadline)
        for key in ('admin', 'oper'):
            ports[str(index)][key] = values.get(OIDS[key] + '.' + str(index))
    identity_fields = [base.get(OIDS['sys_descr']), base.get(OIDS['sys_object_id']),
                       {i: p['descr'] for i, p in ports.items()}]
    if not all(isinstance(x, str) and x for x in identity_fields[:2]) or type(base.get(OIDS['uptime'])) is not int:
        raise ValueError('switch identity or uptime unavailable')
    result = dict(identity=digest(identity_fields), ports=ports, uptime=base[OIDS['uptime']],
                  sys_descr=identity_fields[0], sys_object_id=identity_fields[1],
                  address=cfg['address'], sampled_mono=time.monotonic() if now is None else now,
                  sampled_at=time.time(), boot=boot_id())
    # Optional objects are independent: v1 noSuchName must not invalidate core status.
    if cfg['mapping']:
        index = cfg['mapping']['ifindex']
        for key in ('last_change', 'name', 'speed', 'in_errors', 'out_errors'):
            try:
                value = transport.request([OIDS[key] + '.' + str(index)], deadline)
                result[key] = value.get(OIDS[key] + '.' + str(index))
            except (TimeoutError, ValueError):
                result[key] = None
    return result


def verify_mapping(before, disconnected, reconnected, physical_port, ifindex):
    rows = [json.loads(Path(p).read_text(encoding='utf-8')) for p in (before, disconnected, reconnected)]
    first = rows[0]
    if not 1 <= physical_port <= 8 or not 1 <= ifindex <= 65535:
        raise ValueError('invalid mapping coordinates')
    if any(r.get('identity') != first.get('identity') or r.get('boot') != first.get('boot') or r.get('address') != first.get('address') for r in rows):
        raise ValueError('identity changed during cable-cycle verification')
    if not (rows[0]['sampled_mono'] < rows[1]['sampled_mono'] < rows[2]['sampled_mono'] <= rows[0]['sampled_mono'] + 600):
        raise ValueError('require ordered observations within ten minutes')
    if not rows[0]['uptime'] <= rows[1]['uptime'] <= rows[2]['uptime']:
        raise ValueError('switch restarted during verification')
    for position, row in enumerate(rows):
        port = row['ports'].get(str(ifindex), {})
        if port.get('admin') != 1 or port.get('oper') != (2 if position == 1 else 1):
            raise ValueError('selected index did not demonstrate enabled up/down/up')
        if set(row['ports']) != set(first['ports']):
            raise ValueError('interface inventory changed')
        for other, observed in row['ports'].items():
            if other != str(ifindex) and observed != first['ports'][other]:
                raise ValueError('another interface changed; mapping is ambiguous')
    return dict(physical_port=physical_port, ifindex=ifindex, identity=first['identity'], verified_at=time.time())


class Monitor:
    def __init__(self, cfg, boot, previous=None):
        self.cfg, self.boot = cfg, boot
        self.generation = str(uuid.uuid4())
        self.sequence = 0
        self.previous = previous if isinstance(previous, dict) and previous.get('boot') == boot and previous.get('config_digest') == digest(cfg) else {}
        # Continuity is diagnostic only. A fresh process must earn new down confirmation.
        self.confirmed_state = None
        self.since = None
        self.last_uptime = self.previous.get('switch_uptime')
        self.last_confirmed = self.previous.get('last_confirmed_state')

    def update(self, observation, now, wall, error=''):
        self.sequence += 1
        if observation:
            now = observation.get('sampled_mono', now)
        mapping = self.cfg['mapping']
        state = 'unknown'
        if not self.cfg['enabled']:
            state, error = 'monitor_unavailable', 'disabled'
        elif error:
            state = 'unknown'
        elif not mapping or not observation or observation.get('identity') != mapping['identity']:
            error = 'mapping_unverified_or_changed'
        elif self.last_uptime is not None and observation['uptime'] < self.last_uptime:
            error = 'switch_restart_or_uptime_wrap'
        else:
            port = observation.get('ports', {}).get(str(mapping['ifindex']), {})
            if port.get('admin') == 2:
                state = 'administratively_down'
            elif port.get('admin') == 1:
                state = {1: 'up', 2: 'down'}.get(port.get('oper'), 'unknown')
            if state == 'unknown':
                error = 'unsupported_port_status'
        if observation and type(observation.get('uptime')) is int:
            self.last_uptime = observation['uptime']
        if state != self.confirmed_state:
            self.since = now
        transition = self.previous.get('last_transition')
        if state in ('up', 'down', 'administratively_down') and state != self.confirmed_state:
            if self.last_confirmed != state:
                transition = wall
            self.last_confirmed = state
        self.confirmed_state = state
        result = dict(version=1, boot=self.boot, generation=self.generation, sequence=self.sequence,
                      config_digest=digest(self.cfg), enabled=self.cfg['enabled'], state=state,
                      sampled_mono=now, generated_at=wall, state_since_mono=self.since,
                      last_successful_poll=wall if observation and not error else self.previous.get('last_successful_poll'),
                      last_transition=transition, last_confirmed_state=self.last_confirmed, physical_port=mapping['physical_port'] if mapping else None,
                      ifindex=mapping['ifindex'] if mapping else None,
                      switch_uptime=self.last_uptime,
                      speed=observation.get('speed') if observation else None,
                      last_change=observation.get('last_change') if observation else None,
                      in_errors=observation.get('in_errors') if observation else None,
                      out_errors=observation.get('out_errors') if observation else None,
                      reachable=bool(observation), error=error)
        self.previous = result
        return result


def cached_status(path=CACHE, config_path=CONFIG, now=None, boot=None):
    unavailable = dict(enabled=False, state='monitor_unavailable', error='disabled_or_unavailable')
    try:
        cfg = load_config(config_path)
        if not cfg['enabled']:
            return unavailable
        unavailable['enabled'] = True
        path = Path(path)
        if path.stat().st_size > 16384:
            return unavailable
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('boot') != (boot or boot_id()) or data.get('config_digest') != digest(cfg):
            return unavailable
        stamp = data.get('sampled_mono')
        now = time.monotonic() if now is None else now
        if type(stamp) not in (int, float) or not 0 <= now - stamp <= cfg['stale_seconds']:
            return dict(enabled=True, state='stale', error='stale_cache')
        if data.get('state') not in STATES or not cfg['mapping']:
            return dict(enabled=True, state='unknown', error='mapping_unverified')
        if data.get('ifindex') != cfg['mapping']['ifindex'] or data.get('physical_port') != cfg['mapping']['physical_port']:
            return dict(enabled=True, state='unknown', error='mapping_changed')
        if type(data.get('sequence')) is not int or data['sequence'] < 1 or not isinstance(data.get('generation'), str):
            return unavailable
        since = data.get('state_since_mono')
        if type(since) not in (int, float) or not math.isfinite(since) or not 0 <= since <= stamp:
            return unavailable
        return data | {'age_seconds': now - stamp, 'state_seconds': max(0, now - data.get('state_since_mono', now))}
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--output', type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ('run', 'once', 'check', 'discover', 'verify-mapping', 'generate-community'):
        actions.add_argument('--' + name, action='store_true')
    parser.add_argument('--before', type=Path)
    parser.add_argument('--disconnected', type=Path)
    parser.add_argument('--reconnected', type=Path)
    parser.add_argument('--physical-port', type=int, default=2)
    parser.add_argument('--ifindex', type=int)
    args = parser.parse_args(argv)
    if args.generate_community:
        if args.output is None:
            raise ValueError('credential output path required')
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
            output.write(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)) + '\n')
        print('Private community credential created; configure it as read-only on the switch.')
        return 0
    cfg = load_config(args.config)
    if args.verify_mapping:
        if None in (args.before, args.disconnected, args.reconnected, args.ifindex, args.output):
            raise ValueError('mapping verification requires three snapshots, an index and output config')
        cfg['mapping'] = verify_mapping(args.before, args.disconnected, args.reconnected, args.physical_port, args.ifindex)
        cfg['enabled'] = False
        write_json(args.output, cfg)
        print('Verified mapping saved with monitoring disabled.')
        return 0
    if args.check:
        print(json.dumps(cached_status(config_path=args.config), indent=2))
        return 0  # supplementary warning, never a required self-test gate
    if args.discover:
        discovered = sample(cfg | {'mapping': None}, NetSnmp(cfg))
        if args.output:
            write_json(args.output, discovered)
        print(json.dumps(discovered, indent=2))
        return 0
    previous = None
    try:
        previous = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        pass
    monitor = Monitor(cfg, boot_id(), previous)
    while True:
        started = time.monotonic()
        observed, error = None, ''
        if cfg['enabled']:
            try:
                observed = sample(cfg, NetSnmp(cfg))
            except (OSError, ValueError, TimeoutError, KeyError):
                error = 'poll_failed'  # never expose subprocess output or credential text
        value = monitor.update(observed, time.monotonic(), time.time(), error)
        write_json(CACHE, value)
        if args.once or not cfg['enabled']:
            return 0
        time.sleep(max(0.1, cfg['poll_seconds'] - (time.monotonic() - started)))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('Switch monitoring unavailable: check configuration, mapping and protected credentials.')
        raise SystemExit(1)
