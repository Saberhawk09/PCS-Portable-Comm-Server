#!/usr/bin/env python3
"""Single PCS telemetry writer and fast, audience-separated snapshot reader."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import signal
import tempfile
import time

RUNTIME = Path('/run/pcs-dashboard')
DISPATCHER = '/usr/local/sbin/pcs-web-action'
INTERVAL = 10
STALE_AFTER = 45


def unavailable(public, message='Telemetry is loading; the first snapshot is not available yet.'):
    value = {'generated_at': 'unknown', 'overall': 'warn', 'offline': False,
             'alerts': [{'severity': 'warn', 'component': 'Dashboard', 'message': message}],
             'error': message, 'snapshot': {'state': 'unavailable', 'age_seconds': None}}
    if public:
        for section in ('system', 'network', 'remote_management', 'cellular', 'time', 'gnss',
                        'storage', 'services', 'power', 'pistar', 'aprs', 'meshtastic'):
            value[section] = {'status': 'warn'}
        value['network']['lan_gateway'] = '10.42.0.1'
    else:
        value.update(cards=[{'id': 'dashboard', 'title': 'Dashboard', 'status': 'warn',
                             'summary': message, 'items': []}], client_info={})
    return value


def read_snapshot(public, runtime=RUNTIME, now=None):
    now = time.monotonic() if now is None else now
    try:
        envelope = json.loads((runtime / ('public.json' if public else 'admin.json')).read_text())
        data = copy.deepcopy(envelope['data'])
        age = now - envelope['collected_monotonic']
        if not isinstance(data, dict) or age < 0:
            raise ValueError('invalid snapshot')
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable(public)
    stale = age > STALE_AFTER
    data['snapshot'] = {'state': 'stale' if stale else 'fresh', 'age_seconds': round(age, 1)}
    if stale:
        message = 'Telemetry is stale; displayed values are from the last successful collection.'
        data['error'] = message
        data.setdefault('alerts', []).insert(0, {'severity': 'warn', 'component': 'Dashboard', 'message': message})
        if data.get('overall') == 'ok':
            data['overall'] = 'warn'
        if not public:
            data.setdefault('cards', []).insert(0, {'id': 'dashboard', 'title': 'Stale telemetry',
                                                   'status': 'warn', 'summary': message, 'items': []})
    return data


def atomic_write(path, data):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def collect_output(command):
    # Killing just the dispatcher shell leaves its Python collector orphaned.
    # A dedicated process group bounds all child probes on timeout.
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
        return output


def collect_once(runtime=RUNTIME, dispatcher=DISPATCHER):
    # Each audience uses the established collector projection. Never derive public
    # data by removing a few fields from the privileged response.
    for public in (True, False):
        started = time.monotonic()
        action = 'dashboard-public-collect' if public else 'dashboard-admin-collect'
        try:
            data = json.loads(collect_output([dispatcher, action]))
            if not isinstance(data, dict) or not isinstance(data.get('generated_at'), str):
                raise ValueError('invalid collector output')
            atomic_write(runtime / ('public.json' if public else 'admin.json'),
                         {'collected_monotonic': started, 'data': data})
            print(f'{"public" if public else "admin"} collection seconds={time.monotonic() - started:.3f}', flush=True)
        except (subprocess.SubprocessError, OSError, ValueError):
            # Preserve last good data and never log collector output or private fields.
            print(f'{"public" if public else "admin"} collection failed; retaining previous snapshot', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read', choices=('public', 'admin'))
    args = parser.parse_args()
    if args.read:
        print(json.dumps(read_snapshot(args.read == 'public')))
        return
    import fcntl
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / 'collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            started = time.monotonic()
            collect_once()
            time.sleep(max(1, INTERVAL - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
