#!/usr/bin/env python3
"""Shared PCS position selection. Receiver GPSD/Chrony are never modified.

The read-only GPSD JSON endpoint at loopback:2948 is for position consumers,
not clock discipline. Exact positions/configuration are root-only on disk.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import sys
import tempfile
import threading
import time

CONFIG = Path('/etc/pcs/location.json')
RUNTIME = Path('/run/pcs-location')
PORT = 2948
FIX_MAX_AGE = 15
RECOVERY_SECONDS = 5
DEFAULT = {'version': 1, 'enabled': False, 'persist': False,
           'latitude': None, 'longitude': None, 'altitude_m': None}


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Position values must be numbers')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Position value outside allowed range')
    return float(value)


def validate_config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT) - {'updated_at'}:
        raise ValueError('Invalid location configuration')
    result = dict(DEFAULT, **value)
    if type(result['version']) is not int or result['version'] != 1:
        raise ValueError('Unsupported location configuration')
    if type(result['enabled']) is not bool or type(result['persist']) is not bool:
        raise ValueError('Location switches must be boolean')
    if result['enabled'] or result['latitude'] is not None or result['longitude'] is not None:
        result['latitude'] = number(result['latitude'], -90, 90)
        result['longitude'] = number(result['longitude'], -180, 180)
    if result['altitude_m'] is not None:
        result['altitude_m'] = number(result['altitude_m'], -500, 10000)
    return result


def load_config(path=CONFIG):
    try:
        return validate_config(json.loads(path.read_text()))
    except FileNotFoundError:
        return dict(DEFAULT)


def atomic_write(path, value, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_config(value, path=CONFIG):
    value = validate_config(value)
    value['updated_at'] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    atomic_write(path, value)


def read_status(path=RUNTIME / 'status.json', now=None):
    """Read non-coordinate state only. Expired service state never clears alerts."""
    now = time.monotonic() if now is None else now
    try:
        value = json.loads(path.read_text())
        age = now - value['updated_monotonic']
        if (value.get('version') != 1 or not 0 <= age <= 10
                or value.get('source') not in {'Manual', 'GPS', 'Unavailable'}
                or type(value.get('available')) is not bool
                or type(value.get('config_error')) is not bool
                or value['available'] != (value['source'] in {'Manual', 'GPS'})):
            return {}
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def apply_dashboard(info, path=RUNTIME):
    """Apply a fresh effective position to privileged collector fields."""
    status = read_status(path / 'status.json')
    if not status:
        return info, status
    result = dict(info)
    if status['source'] == 'Manual':
        result.update(fix_quality='Manual', grid_square='Manual · ' + str(status['grid_square']),
                      lat_lon='Manual (coordinates hidden)', utc='Not a GPS time source')
        result['satellites'] = 'Manual (receiver: ' + str(info.get('satellites', 'unknown')) + ')'
        try:
            position = json.loads((path / 'position.json').read_text())
            if position.get('pcs_source') == 'Manual':
                lat = number(position['lat'], -90, 90)
                lon = number(position['lon'], -180, 180)
                result['lat_lon'] = f'Manual · {lat:.6f}, {lon:.6f}'
        except (OSError, ValueError, KeyError, TypeError):
            pass
    elif status['source'] == 'GPS':
        try:
            position = json.loads((path / 'position.json').read_text())
            if position.get('pcs_source') == 'GPS':
                lat = number(position['lat'], -90, 90)
                lon = number(position['lon'], -180, 180)
                result.update(fix_quality='3D fix' if position.get('mode') == 3 else '2D fix',
                              lat_lon=f'{lat:.6f}, {lon:.6f}', grid_square=status['grid_square'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    else:
        result.update(fix_quality='No fix', lat_lon='not available', grid_square='unknown')
    return result, status


def grid(latitude, longitude):
    # Clamp endpoints for valid Maidenhead fields at +90 / +180.
    x = min(latitude + 90, 179.999999999)
    y = min(longitude + 180, 359.999999999)
    return (chr(65 + int(y // 20)) + chr(65 + int(x // 10))
            + str(int(y % 20 // 2)) + str(int(x % 10))
            + chr(97 + int(y % 2 * 12)) + chr(97 + int(x % 1 * 24)))


class PositionState:
    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.config = dict(DEFAULT)
        self.config_error = False
        self.tpv, self.sky, self.pending = {}, {}, {}
        self.received = self.sky_received = float('-inf')
        self.good_since = None
        self.last_fix_time = None

    def update(self, report):
        with self.lock:
            if report.get('class') == 'SKY':
                self.sky, self.sky_received = report, self.clock()
            if report.get('class') != 'TPV':
                return
            # Partial TPVs are normal. Assemble an epoch without resetting the
            # recovery timer or erasing a recent complete fix for each fragment.
            if report.get('time') and report.get('time') != self.pending.get('time'):
                self.pending = {}
            self.pending.update(report)
            report = dict(self.pending)
            valid = self.valid_fix(report)
            complete = all(key in report for key in ('mode', 'lat', 'lon', 'time'))
            no_fix = type(report.get('mode')) is int and report['mode'] < 2
            if not valid and not complete and not no_fix:
                return
            if not valid or self.clock() - self.received > FIX_MAX_AGE:
                self.good_since = None
            if valid:
                # Repeated cached epochs must not extend GPS freshness.
                epoch = report.get('time')
                if epoch != self.last_fix_time:
                    self.received = self.clock()
                    self.last_fix_time = epoch
                    if self.good_since is None:
                        self.good_since = self.clock()
            self.tpv = report

    def valid_fix(self, report):
        try:
            if type(report.get('mode')) is not int or report['mode'] < 2:
                return False
            number(report.get('lat'), -90, 90)
            number(report.get('lon'), -180, 180)
            stamp = datetime.fromisoformat(report['time'].replace('Z', '+00:00'))
            return stamp.tzinfo is not None and -5 <= self.wall() - stamp.timestamp() <= 30
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    def selection(self):
        with self.lock:
            good = self.valid_fix(self.tpv) and 0 <= self.clock() - self.received <= FIX_MAX_AGE
            recovered = good and self.good_since is not None and self.clock() - self.good_since >= RECOVERY_SECONDS
            manual = self.config['enabled'] and (self.config['persist'] or not recovered)
            source = 'Manual' if manual else 'GPS' if good else 'Unavailable'
            if manual:
                report = {'class': 'TPV', 'device': 'PCS manual position', 'mode': 2,
                          'lat': self.config['latitude'], 'lon': self.config['longitude']}
                if self.config['altitude_m'] is not None:
                    report.update(mode=3, altMSL=self.config['altitude_m'])
                # Deliberately omit time, speed, heading and accuracy. This is
                # a supplied position, not a GNSS observation or clock source.
            elif good:
                report = copy.deepcopy(self.tpv)
            else:
                report = {'class': 'TPV', 'mode': 1}
            report['pcs_source'] = source
            sky = copy.deepcopy(self.sky) if 0 <= self.clock() - self.sky_received <= FIX_MAX_AGE else {'class': 'SKY'}
            sky['pcs_source'] = source
            status = {'version': 1, 'updated_monotonic': self.clock(), 'source': source,
                      'available': manual or good, 'manual_enabled': self.config['enabled'],
                      'persist': self.config['persist'], 'receiver_fix': good,
                      'config_error': self.config_error,
                      'grid_square': grid(report['lat'], report['lon']) if manual or good else None}
            return report, sky, status


def receiver_loop(state, stop, host='127.0.0.1', port=2947):
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=2) as connection:
                connection.settimeout(1)
                connection.sendall(b'?WATCH={"enable":true,"json":true};\n')
                buffer = b''
                while not stop.is_set():
                    try:
                        chunk = connection.recv(8192)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buffer += chunk
                    if len(buffer) > 65536:
                        break
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        try:
                            report = json.loads(line)
                            if isinstance(report, dict):
                                state.update(report)
                        except (ValueError, TypeError):
                            pass
        except OSError:
            pass
        with state.lock:
            state.tpv, state.pending, state.good_since = {}, {}, None
        stop.wait(5)


VERSION = {'class': 'VERSION', 'release': 'PCS-position-1', 'rev': '1', 'proto_major': 3, 'proto_minor': 14}


class PositionHandler(socketserver.BaseRequestHandler):
    """Bounded, read-only WATCH/POLL/DEVICES subset for libgps clients."""
    def handle(self):
        self.request.settimeout(2)
        watching, buffer = False, ''
        decoder = json.JSONDecoder()

        def send(value):
            self.request.sendall((json.dumps(value, allow_nan=False) + '\r\n').encode())

        try:
            send(VERSION)
            idle_since = time.monotonic()
            while not self.server.stop.is_set():
                readable, _, _ = select.select([self.request], [], [], 1)
                if readable:
                    chunk = self.request.recv(2048)
                    if not chunk:
                        return
                    buffer += chunk.decode('ascii')
                    if len(buffer) > 4096:
                        return
                    while buffer.strip(' \r\n;'):
                        buffer = buffer.lstrip(' \r\n;?')
                        match = re.match(r'([A-Z]+)', buffer)
                        if not match:
                            return
                        command, end = match.group(1), match.end()
                        if len(buffer) == end:
                            break
                        options = None
                        if buffer[end] == '=':
                            try:
                                options, consumed = decoder.raw_decode(buffer[end + 1:])
                            except ValueError:
                                break
                            end += 1 + consumed
                        elif buffer[end] != ';':
                            return
                        buffer = buffer[end:].lstrip(';\r\n ')
                        tpv, sky, _ = self.server.state.selection()
                        if command == 'VERSION':
                            send(VERSION)
                        elif command == 'DEVICES':
                            send({'class': 'DEVICES', 'devices': [{'class': 'DEVICE', 'path': 'PCS position', 'activated': 1}]})
                        elif command == 'WATCH':
                            if options is not None and not isinstance(options, dict):
                                return
                            if options is not None and type(options.get('enable', True)) is not bool:
                                return
                            if options and (options.get('raw') or options.get('nmea')):
                                send({'class': 'ERROR', 'message': 'PCS position feed supports JSON only; receiver raw data remains on port 2947'})
                                return
                            watching = options.get('enable', True) if options is not None else watching
                            send({'class': 'WATCH', 'enable': watching, 'json': True})
                        elif command == 'POLL':
                            send({'class': 'POLL', 'time': datetime.now(timezone.utc).isoformat(),
                                  'active': 1, 'tpv': [tpv], 'sky': [sky]})
                        else:
                            send({'class': 'ERROR', 'message': 'Read-only PCS position endpoint'})
                    idle_since = time.monotonic()
                if watching:
                    tpv, sky, _ = self.server.state.selection()
                    send(tpv)
                    send(sky)
                elif time.monotonic() - idle_since > 30:
                    return
        except (OSError, ValueError, UnicodeError):
            return


class PositionServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, address, state, stop):
        self.state, self.stop = state, stop
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, PositionHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def serve():
    state, stop = PositionState(), threading.Event()
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    threading.Thread(target=receiver_loop, args=(state, stop), daemon=True).start()
    server = PositionServer(('127.0.0.1', PORT), state, stop)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ready = False
    try:
        while True:
            with state.lock:
                try:
                    state.config, state.config_error = load_config(), False
                except (OSError, ValueError, TypeError):
                    state.config, state.config_error = dict(DEFAULT), True
            report, _, status = state.selection()
            atomic_write(RUNTIME / 'position.json', report)
            atomic_write(RUNTIME / 'status.json', status, 0o644)
            if not ready:
                address = os.environ.get('NOTIFY_SOCKET')
                if address:
                    if address.startswith('@'):
                        address = '\0' + address[1:]
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                        notify.sendto(b'READY=1', address)
                ready = True
            time.sleep(1)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'show', 'set-from-stdin'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root is required')
    if args.command == 'serve':
        serve()
    elif args.command == 'show':
        print(json.dumps({'config': load_config(), 'status': read_status()}))
    else:
        try:
            payload = sys.stdin.read(8193)
            if len(payload) > 8192:
                raise ValueError('Configuration too large')
            save_config(json.loads(payload))
        except (ValueError, OSError, TypeError):
            parser.error('Invalid location settings; previous configuration preserved')
        print('Location settings saved. Position-dependent services may publish the supplied position under their existing transmission settings.')


if __name__ == '__main__':
    main()
