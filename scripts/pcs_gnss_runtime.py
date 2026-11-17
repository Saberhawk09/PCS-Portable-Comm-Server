#!/usr/bin/env python3
"""GPSD-to-Chrony validity gate. No modem, networking or GPIO write API."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import time

from pcs_gnss import (CONFIG, RUNTIME, NEO, MAX_AGE, TimeGate, UBXStream, MessageActivity,
                      atomic_json, diagnostic, load_config, parse_chrony, pps_devices, SatelliteActivity)

SERIAL_ALIAS = '/dev/pcs-gnss-neo'
PPS_ALIAS = RUNTIME / 'pps'
SOCK = '/run/chrony/pcs-f10n.sock'


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
    return result.returncode, result.stdout


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.sendto(message.encode(), address)


class Selection:
    def __init__(self, command=run):
        self.command = command
        self.current = {}

    def set(self, ref, enabled, force=False):
        if force or self.current.get(ref) != enabled:
            rc, output = self.command(['chronyc', 'selectopts', ref, '-noselect' if enabled else '+noselect'])
            if not enabled and 'No such source' in output:
                self.current[ref] = False
                return
            if rc:
                # Never mark the transition complete if Chrony did not accept it.
                raise RuntimeError('Chrony reference gate failed')
            rc, _ = self.command(['chronyc', 'reselect'])
            if rc:
                raise RuntimeError('Chrony reference reselection failed')
            self.current[ref] = enabled

    def withdraw(self):
        errors = []
        for ref in ('F10P', 'F10N'):
            try:
                self.set(ref, False, force=True)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('Unable to withdraw GNSS references')


def send_sample(sample, path=SOCK):
    stamp, offset = sample
    seconds = int(stamp)
    micros = round((stamp - seconds) * 1e6)
    if micros >= 1000000:
        seconds, micros = seconds + 1, 0
    # chrony refclock_sock.c native ABI: timeval, double, int pps/leap/pad/magic.
    # Native long is essential for a 64-bit Pi; do not hard-code a 32-bit layout.
    data = struct.pack('@lldiiii', seconds, micros, offset, 0, 0, 0, 0x534F434B)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
        connection.sendto(data, path)


class PPSActivity:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.device = None
        self.sequence = None
        self.changed = float('-inf')

    def observe(self, device, text):
        try:
            stamp, sequence = text.strip().split('#')
            stamp, sequence = float(stamp), int(sequence)
            if not stamp > 0 or sequence < 0:
                raise ValueError('Invalid PPS event')
            if self.device != device:
                self.device, self.sequence, self.changed = device, sequence, float('-inf')
            elif self.sequence != sequence:
                self.sequence, self.changed = sequence, self.clock()
        except (ValueError, TypeError):
            self.device, self.sequence, self.changed = None, None, float('-inf')

    def status(self, sysroot=Path('/sys')):
        devices = pps_devices(sysroot)
        if len(devices) != 1:
            self.device, self.sequence, self.changed = None, None, float('-inf')
            return {'pps_present': False, 'pps_active': False, 'pps_age_seconds': None}
        try:
            self.observe(devices[0], (sysroot / 'class/pps' / Path(devices[0]).name / 'assert').read_text())
        except OSError:
            self.device, self.sequence, self.changed = None, None, float('-inf')
        age = self.clock() - self.changed
        return {'pps_present': self.device is not None, 'pps_active': 0 <= age <= MAX_AGE,
                'pps_age_seconds': round(age, 2) if age < 1000000 else None}


def connect(raw=False):
    connection = socket.create_connection(('127.0.0.1', 2947), timeout=2)
    watch = {'enable': True, 'device': SERIAL_ALIAS}
    watch.update({'raw': 2} if raw else {'json': True, 'pps': True})
    connection.sendall(('?WATCH=' + json.dumps(watch, separators=(',', ':')) + ';\n').encode())
    connection.setblocking(False)
    return connection


def select_time_sources(selection, utc_valid, pps_active, pps_enabled=True):
    # An unwired PPS input can register noise. Software commissioning consent
    # is independent of device presence and UTC validity.
    paired = bool(utc_valid and pps_active and pps_enabled)
    if not paired:
        selection.set('F10P', False)
    selection.set('F10N', bool(utc_valid))
    if paired:
        selection.set('F10P', True)


def serve():
    config = load_config()
    if config['mode'] != NEO:
        return
    selection = Selection()
    selection.withdraw()
    gate, binary, pulse = TimeGate(), UBXStream(), PPSActivity()
    activity = MessageActivity()
    satellites = SatelliteActivity()
    connections, buffers, pending = {}, {}, []
    state = {'version': 1, 'mode': NEO, 'model': 'NEO-F10N', 'antenna_path': 'Dedicated L1/L5 (operator wiring)',
             'connected': False, 'fix_valid': False, 'satellites_visible': None, 'satellites_used': None}
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_probe, last_sample, last_generation = float('-inf'), float('-inf'), None
    last_report = float('-inf')
    maintenance_id = None
    last_pps = PPS_ALIAS.exists()
    pps_enabled = config.get('pps_enabled', True)
    notify('READY=1')
    try:
        while not stopping:
            now = time.monotonic()
            # A maintenance lease is fail-closed, including malformed/stale
            # leases. Only the exclusive owner's verified cleanup releases it.
            lease = RUNTIME / 'maintenance.json'
            if lease.exists():
                try:
                    request = json.loads(lease.read_text())
                    ident = request['id']
                    if not isinstance(ident, str) or len(ident) != 32:
                        raise ValueError('Invalid maintenance identifier')
                except (OSError, ValueError, KeyError, TypeError):
                    ident = 'invalid'
                if maintenance_id != ident:
                    selection.withdraw()
                    gate.reset()
                    for connection in connections:
                        connection.close()
                    connections, buffers, pending = {}, {}, []
                    binary, activity, satellites = UBXStream(), MessageActivity(), SatelliteActivity()
                    last_report = last_sample = last_probe = float('-inf')
                    maintenance_id = ident
                state.update(pulse.status())
                state.update(utc_valid=False, fix_valid=False, connected=False, pps_validation='maintenance',
                             state='fresh', diagnostic='Authorized receiver maintenance; GNSS references withdrawn',
                             updated_monotonic=now)
                _, tracking = run(['chronyc', '-n', 'tracking'])
                _, sources = run(['chronyc', '-n', 'sources'])
                state.update(parse_chrony(tracking, sources))
                atomic_json(RUNTIME / 'status.json', state, 0o644)
                atomic_json(RUNTIME / 'maintenance-ack.json', {'id':ident, 'pid':os.getpid(), 'withdrawn':True,
                                                             'updated_monotonic':time.monotonic()})
                notify('WATCHDOG=1')
                time.sleep(0.2)
                continue
            if maintenance_id is not None:
                # Discard all pre-maintenance timing evidence; fresh UTC/TOFF
                # pairing is required before either reference can return.
                gate.reset()
                maintenance_id = None
                (RUNTIME / 'maintenance-ack.json').unlink(missing_ok=True)
            try:
                generation = (os.stat(SERIAL_ALIAS).st_rdev, os.stat(SERIAL_ALIAS).st_ino)
            except OSError:
                generation = None
            if generation != last_generation:
                selection.withdraw()
                gate.reset()
                state.update(connected=False, fix_valid=False, satellites_visible=None, satellites_used=None)
                for connection in connections:
                    connection.close()
                connections, buffers, pending = {}, {}, []
                binary = UBXStream()
                activity = MessageActivity()
                satellites = SatelliteActivity()
                last_sample = float('-inf')
                last_report = float('-inf')
                last_generation = generation
                # Only the enrolled receiver's GPSD path is restarted. Its
                # ExecStartPre verifies model/profile again, before opening it.
                if generation is not None:
                    # A two-speed admission may take longer than the watchdog.
                    # Queue only; the gate stays withdrawn while GPSD prepares.
                    run(['systemctl', 'restart', '--no-block', 'gpsd.service'])
            if not connections and now - last_probe >= 5:
                last_probe = now
                try:
                    for raw in (False, True):
                        connection = connect(raw)
                        connections[connection] = raw
                        buffers[connection] = b''
                except OSError:
                    for connection in connections:
                        connection.close()
                    connections, buffers = {}, {}
            if connections:
                ready, _, _ = select.select(list(connections), [], [], 0.2)
                for connection in ready:
                    data = connection.recv(16384)
                    if not data:
                        raise ConnectionError('GPSD disconnected')
                    if connections[connection]:
                        for cls, msg, payload in binary.feed(data):
                            activity.update(cls, msg, payload)
                            if (cls, msg) == (1, 7):
                                last_report = time.monotonic()
                                gate.update(payload)
                    else:
                        buffers[connection] = (buffers[connection] + data)[-65536:]
                        while b'\n' in buffers[connection]:
                            line, buffers[connection] = buffers[connection].split(b'\n', 1)
                            try:
                                report = json.loads(line)
                            except (ValueError, UnicodeError):
                                continue
                            if report.get('device') != SERIAL_ALIAS:
                                continue
                            if report.get('class') in ('TOFF', 'TPV', 'SKY'):
                                last_report = time.monotonic()
                            if report.get('class') == 'TOFF':
                                pending.append((time.monotonic(), report))
                                pending = pending[-4:]
                            elif report.get('class') == 'TPV':
                                state['fix_valid'] = type(report.get('mode')) is int and report['mode'] >= 2
                                if not state['fix_valid']:
                                    gate.invalidate('No receiver fix')
                            elif report.get('class') == 'SKY':
                                satellites.update(report)
            else:
                time.sleep(0.2)
            now = time.monotonic()
            for received, report in pending[:]:
                sample = gate.sample(report, SERIAL_ALIAS)
                if sample:
                    send_sample(sample)
                    last_sample = now
                    pending.remove((received, report))
                elif now - received > MAX_AGE:
                    pending.remove((received, report))
            state.update(pulse.status())
            state.update(activity.status())
            state.update(satellites.status())
            if pps_enabled and state['pps_present'] != last_pps:
                # Chrony opens the kernel PPS handle at startup. Its fixed
                # ExecStartPre rebuilds the include/alias, including USB-only
                # startup when the optional kernel device is absent.
                selection.withdraw()
                run(['systemctl', 'restart', 'chrony.service'])
                selection.current.clear()
                gate.reset()
                last_sample = float('-inf')
                last_pps = state['pps_present']
            state['usb_connected'] = generation is not None
            state['connected'] = generation is not None and now - last_report <= MAX_AGE
            if not state['connected']:
                state.update(fix_valid=False, satellites_visible=None, satellites_used=None)
            state['utc_valid'] = gate.ready() and now - last_sample <= MAX_AGE
            select_time_sources(selection, state['utc_valid'], state['pps_active'], pps_enabled)
            _, tracking = run(['chronyc', '-n', 'tracking'])
            _, sources = run(['chronyc', '-n', 'sources'])
            state.update(parse_chrony(tracking, sources))
            state['pps_validation'] = ('disabled' if not pps_enabled else
                                       'paired' if state['utc_valid'] and state['pps_active'] else 'unpaired')
            state.update(state='fresh', updated_monotonic=time.monotonic())
            state['diagnostic'] = diagnostic(state)
            atomic_json(RUNTIME / 'status.json', state, 0o644)
            notify('WATCHDOG=1')
    finally:
        for connection in connections:
            connection.close()
        selection.withdraw()


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['withdraw']:
        if load_config()['mode'] == NEO:
            Selection().withdraw()
    elif not sys.argv[1:]:
        serve()
    else:
        raise SystemExit('Usage: pcs-gnss-runtime [withdraw]')
