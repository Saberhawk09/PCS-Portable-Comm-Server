#!/usr/bin/env python3
"""PCS bandwidth CLI and bounded JSON bridge for the authenticated admin UI."""
import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

from pcs_bandwidth_config import PolicyError, load, validate
from pcs_bandwidth_engine import Engine, atomic_json, plan, topology

CONFIG = Path('/etc/pcs/bandwidth.yaml')
RUNTIME = Path('/run/pcs-bandwidth')


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def update(config, request):
    result = copy.deepcopy(config)
    operation = request.get('operation')
    allowed = {
        'profile': {'operation', 'profile', 'revision'},
        'network': {'operation', 'profile', 'total_down', 'total_up', 'default_down', 'default_up', 'revision'},
        'client': {'operation', 'ip', 'name', 'download', 'upload', 'unlimited', 'revision'},
        'delete-client': {'operation', 'ip', 'revision'},
    }
    if operation not in allowed or set(request) - allowed[operation]:
        raise PolicyError('Unknown settings operation or fields')
    if 'revision' in request and request['revision'] != revision(config):
        raise PolicyError('Settings changed since this page loaded; refresh and retry')
    if operation in ('profile', 'network'):
        selected = request.get('profile')
        if selected not in result['profiles']:
            raise PolicyError('Unknown profile')
        result['profile'] = selected
        if operation == 'network':
            profile = result['profiles'][selected]
            for field in ('total_down', 'total_up', 'default_down', 'default_up'):
                value = request.get(field, '')
                if value:
                    profile[field] = value
                elif field.startswith('total_'):
                    profile.pop(field, None)
                elif profile['enabled']:
                    raise PolicyError('Default client rates are required')
    else:
        address = request.get('ip')
        import ipaddress
        if ipaddress.ip_address(address) not in list(ipaddress.ip_network(result['lan_network']).hosts()):
            raise PolicyError('Client must belong to the LAN')
        result['clients'] = [c for c in result['clients'] if c['ip'] != address]
        if operation == 'client':
            entry = {'ip': address}
            if request.get('name'):
                entry['name'] = request['name']
            if type(request.get('unlimited', False)) is not bool:
                raise PolicyError('unlimited must be boolean')
            if request.get('unlimited'):
                entry['unlimited'] = True
            else:
                for field in ('download', 'upload'):
                    if request.get(field):
                        entry[field] = request[field]
            result['clients'].append(entry)
    return validate(result)


@contextmanager
def locked(runtime):
    import fcntl
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runtime.is_symlink() or runtime.stat().st_uid != 0 or runtime.stat().st_mode & 0o077:
        raise RuntimeError('Runtime directory must be root-owned mode 0700')
    with open(runtime / 'lock', 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def save_config(path, config):
    import yaml
    temporary = path.with_suffix('.new')
    with open(temporary, 'w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        yaml.safe_dump(config, stream, sort_keys=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def display(status):
    network = status['network']
    print('PCS Bandwidth Manager')
    print(f"Status: {status['status']}  Healthy: {status['healthy']}")
    print(f"Profile: {status['profile']}  LAN: {network.get('lan')}  WAN: {network.get('wan')}")
    print('IFBs: ' + (', '.join(status['ifbs']) or 'none'))
    limits = status['limits']
    print(f"WAN ceilings: down {limits.get('total_down', 'none')} / up {limits.get('total_up', 'none')}")
    print(f"Default per client: down {limits.get('default_down', 'none')} / up {limits.get('default_up', 'none')}")
    for row in status['clients']:
        policy = 'Unlimited (within WAN ceiling)' if row['unlimited'] else f"{row['down']} / {row['up']}"
        print(f"{row['ip']:15} {row['name']}  {policy}  RX {row['down_bytes']} / TX {row['up_bytes']} bytes")
    print(status['ipv6']['warning'])
    for issue in status['issues']:
        print('Notice: '+issue)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status')
    status.add_argument('--json', action='store_true')
    for name in ('apply', 'reload', 'stop', 'daemon', 'validate', 'plan', 'web-status', 'web-update'):
        commands.add_parser(name)
    profile = commands.add_parser('profile')
    profile.add_argument('name')
    client = commands.add_parser('client')
    client.add_argument('ip')
    client.add_argument('--name')
    client.add_argument('--down')
    client.add_argument('--up')
    client.add_argument('--unlimited', action='store_true')
    client.add_argument('--remove', action='store_true')
    args = parser.parse_args(argv)
    if args.command.startswith('web-') and (args.config != CONFIG or args.runtime != RUNTIME):
        parser.error('Web commands use fixed paths')
    if args.command == 'validate':
        load(args.config)
        print('Configuration valid')
        return 0
    if os.geteuid() != 0:
        parser.error('Use sudo for kernel status and management')
    os.umask(0o077)
    engine = Engine(args.runtime)
    if args.command == 'daemon':
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while not stopping:
                with locked(args.runtime):
                    try:
                        engine.apply(load(args.config))
                    except Exception as exc:
                        engine.cleanup()
                        engine.save({'status': 'ERROR', 'error': str(exc)})
                        print(str(exc), file=sys.stderr, flush=True)
                for _ in range(10):
                    if stopping:
                        break
                    time.sleep(1)
        finally:
            with locked(args.runtime):
                engine.cleanup()
        return 0
    with locked(args.runtime):
        if args.command == 'stop':
            engine.cleanup()
            return 0
        config = load(args.config)
        if args.command == 'plan':
            print(json.dumps(plan(config, topology(config)), indent=2))
        elif args.command in ('status', 'web-status'):
            status = engine.status(config)
            if args.command == 'web-status':
                status.update(config=config, revision=revision(config))
            if args.command == 'web-status' or args.json:
                print(json.dumps(status))
            else:
                display(status)
        elif args.command in ('apply', 'reload'):
            try:
                engine.apply(config)
            except Exception:
                engine.cleanup()
                raise
            display(engine.status(config))
        else:
            if args.command == 'web-update':
                body = sys.stdin.read(16385)
                if len(body) > 16384:
                    raise PolicyError('Settings request too large')
                request = json.loads(body)
                if not isinstance(request, dict) or 'revision' not in request:
                    raise PolicyError('A settings revision is required')
            elif args.command == 'profile':
                request = dict(operation='profile', profile=args.name)
            else:
                if args.unlimited and (args.up or args.down):
                    raise PolicyError('--unlimited cannot be combined with rates')
                request = dict(operation='delete-client' if args.remove else 'client', ip=args.ip)
                if not args.remove:
                    request.update(name=args.name, download=args.down, upload=args.up, unlimited=args.unlimited)
            candidate = update(config, request)
            try:
                engine.apply(candidate)
                save_config(args.config, candidate)
            except Exception:
                engine.cleanup()
                raise
            print(json.dumps({'ok': True, 'status': engine.state().get('status')}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'Bandwidth manager: {error}', file=sys.stderr)
        sys.exit(1)
