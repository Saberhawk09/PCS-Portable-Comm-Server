#!/usr/bin/env python3
"""Linux/Windows LAN witness and bounded, session-specific UDP receipt protocol.

Only standard-library dependencies. No remote commands or HTTP bodies are accepted.
The PCS receiver timestamps before/after handshakes on its own monotonic clock;
client clocks are never compared with PCS clocks. Files contain no addresses.
"""
import argparse
import datetime as dt
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

PORT = 39841
MAX_SAMPLES = 310


class Receipts:
    def __init__(self, session):
        if not isinstance(session, str) or not re.fullmatch('[0-9a-f]{32}', session):
            raise ValueError('invalid_session')
        self.session, self.samples, self.pending = session, [], None
        self.invalid = False
        self.finish_after = None

    def receive(self, packet, now):
        """Return a fixed response or ignore unauthenticated/noisy traffic."""
        if not isinstance(packet, dict) or packet.get('session') != self.session:
            return None
        expected = {'version', 'session', 'seq', 'phase'}
        if packet.get('phase') == 'after':
            expected.add('ok')
        seq, phase = packet.get('seq'), packet.get('phase')
        if (set(packet) != expected or type(packet.get('version')) is not int or packet['version'] != 1 or
                type(seq) is not int or not 0 <= seq < MAX_SAMPLES or
                phase not in ('before', 'after') or
                (phase == 'after' and type(packet.get('ok')) is not bool)):
            self.invalid = True
            return None
        if seq < len(self.samples):
            # Idempotent reply to a retransmission; it must not extend coverage.
            sample = self.samples[seq]
            if phase == 'after' and packet['ok'] != sample['ok']:
                self.invalid = True
                return None
            at = sample[phase]
        elif seq != len(self.samples):
            self.invalid = True
            return None
        elif phase == 'before':
            if self.pending is None:
                self.pending = now
            at = self.pending
        else:
            if self.pending is None or not 0 <= now - self.pending <= 3:
                self.invalid = True
                return None
            if self.samples and self.pending - self.samples[-1]['after'] > 3:
                self.invalid = True
            self.samples.append({'seq': seq, 'before': self.pending, 'after': now, 'ok': packet['ok']})
            self.pending = None
            at = now
        done = (phase == 'after' and self.finish_after is not None and at >= self.finish_after)
        return {'version': 1, 'seq': seq, 'phase': phase, 'receipt': at, 'done': done}

    def ready(self, now):
        return (not self.invalid and len(self.samples) >= 2 and
                all(s['ok'] for s in self.samples) and 0 <= now - self.samples[-1]['after'] <= 3)

    def coverage(self, start, end):
        if (self.invalid or not self.samples or self.samples[0]['before'] > start or
                self.samples[-1]['after'] < end):
            return 'INCONCLUSIVE'
        return 'PASS' if all(s['ok'] for s in self.samples) else 'FAIL'


class Receiver:
    def __init__(self, session, server, client):
        self.receipts = Receipts(session)
        self.client = str(ipaddress.IPv4Address(client))
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'eth0\0')
            self.socket.bind((str(ipaddress.IPv4Address(server)), PORT))
            self.socket.settimeout(0.1)
        except BaseException:
            self.socket.close()
            raise

    def poll(self):
        try:
            data, peer = self.socket.recvfrom(1025)
        except socket.timeout:
            return
        if peer[0] != self.client or len(data) > 1024:
            return
        try:
            packet = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            return
        reply = self.receipts.receive(packet, time.monotonic())
        if reply is not None:
            self.socket.sendto(json.dumps(reply, allow_nan=False).encode(), peer)

    def close(self):
        self.socket.close()


def client_route(target, source, interface):
    if sys.platform == 'win32':
        from pcs_qualify_windows import client_route as windows_route
        return windows_route(target, source, interface)
    if not sys.platform.startswith('linux'):
        raise ValueError('unsupported_witness_platform')
    if not re.fullmatch('[A-Za-z0-9_.-]{1,15}', interface) or interface.startswith('-'):
        raise ValueError('invalid_interface')
    dest, local = ipaddress.IPv4Address(target), ipaddress.IPv4Address(source)
    if any(a.is_unspecified or a.is_multicast or a.is_loopback for a in (dest, local)) or dest == local:
        raise ValueError('invalid_lan_addresses')
    result = subprocess.run(['/usr/sbin/ip', '-j', '-4', 'route', 'get', str(dest), 'from', str(local)],
                            capture_output=True, timeout=3, check=True)
    if len(result.stdout) > 65536:
        raise ValueError('route_output_limit')
    rows = json.loads(result.stdout)
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or
            rows[0].get('dev') != interface or rows[0].get('dst') != str(dest) or
            rows[0].get('from') != str(local) or rows[0].get('type', 'unicast') != 'unicast' or
            any(k in rows[0] for k in ('gateway', 'nexthops', 'encap', 'nhid'))):
        raise ValueError('direct_lan_route_required')


def bound_socket(source, interface, kind):
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if sys.platform == 'win32':
            from pcs_qualify_windows import pin_socket
            pin_socket(sock, interface, kind)
        elif sys.platform.startswith('linux'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode('ascii') + b'\0')
        else:
            raise ValueError('unsupported_witness_platform')
        sock.bind((source, 0))
        sock.settimeout(2)
        return sock
    except BaseException:
        sock.close()
        raise


def sample_http(target, source, interface):
    connection = http.client.HTTPConnection(target, 80, timeout=2)
    try:
        connection.sock = bound_socket(source, interface, socket.SOCK_STREAM)
        connection.sock.connect((target, 80))
        connection.request('GET', '/?pcs-witness=' + uuid.uuid4().hex,
                           headers={'Cache-Control': 'no-cache, no-store', 'Connection': 'close'})
        reply = connection.getresponse()
        return reply.status == 200 and bool(reply.read(1))
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def run(session, target, source, interface, duration, output):
    Receipts(session)
    if type(duration) is not int or not 10 <= duration <= 300:
        raise ValueError('invalid_duration')
    identity = client_route(target, source, interface)
    sock = bound_socket(source, interface, socket.SOCK_DGRAM)
    used = 0
    try:
        sock.connect((target, PORT))
        # O_EXCL refuses an existing file/link on both systems. Windows has no
        # O_NOFOLLOW; its file permissions follow the operator's directory ACL.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            def emit(row):
                nonlocal used
                row.update(utc=dt.datetime.now(dt.timezone.utc).isoformat(), monotonic=time.monotonic())
                raw = json.dumps(row, allow_nan=False) + '\n'
                used += len(raw)
                if used > 2 * 1024 * 1024:
                    raise ValueError('witness_limit')
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            def exchange(seq, phase, ok=None):
                packet = {'version': 1, 'session': session, 'seq': seq, 'phase': phase}
                if phase == 'after':
                    packet['ok'] = ok
                sock.send(json.dumps(packet).encode())
                response = json.loads(sock.recv(1025))
                if (not isinstance(response, dict) or set(response) != {'version', 'seq', 'phase', 'receipt', 'done'} or
                        response['version'] != 1 or response['seq'] != seq or response['phase'] != phase or
                        type(response['done']) is not bool or
                        type(response['receipt']) not in (int, float) or
                        not math.isfinite(response['receipt']) or response['receipt'] < 0):
                    raise ValueError('invalid_receipt')
                return response['receipt'], response['done']
            boot = (None if sys.platform == 'win32' else
                    str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip())))
            emit({'version': 1, 'event': 'start', 'session': session, 'boot_id': boot,
                  'clock_id': uuid.uuid4().hex, 'scope': 'lan_http_with_pcs_receipts',
                  'platform': 'windows' if sys.platform == 'win32' else 'linux'})
            started, seq = time.monotonic(), 0
            try:
                while time.monotonic() - started < duration:
                    if client_route(target, source, interface) != identity:
                        raise ValueError('witness_adapter_changed')
                    before, _ = exchange(seq, 'before')
                    ok = sample_http(target, source, interface)
                    if sys.platform == 'win32' and client_route(target, source, interface) != identity:
                        raise ValueError('witness_adapter_changed')
                    after, done = exchange(seq, 'after', ok)
                    emit({'event': 'sample', 'seq': seq, 'ok': ok,
                          'pcs_before': before, 'pcs_after': after})
                    seq += 1
                    if done:
                        break
                    time.sleep(1)
            except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
                emit({'event': 'end', 'samples': seq, 'complete': False})
                raise
            emit({'event': 'end', 'samples': seq, 'complete': True})
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('session', 'target', 'source', 'interface', 'output'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--duration', type=int, default=240)
    args = parser.parse_args()
    try:
        run(args.session, args.target, args.source, args.interface, args.duration, args.output)
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        print('Witness stopped; retain its durable file. The PCS report determines window coverage.')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
