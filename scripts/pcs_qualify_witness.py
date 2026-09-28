#!/usr/bin/env python3
"""Standalone, source-bound LAN HTTP witness; Python standard library only.

Run on a separate LAN client. Never records target addresses or HTTP bodies.
No proxy, redirect following, credentials, DNS lookup, or cached connection reuse.
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
import stat
import time
import uuid

MAX_BYTES = 2 * 1024 * 1024


def clock():
    return {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'monotonic': time.monotonic()}


def client_boot():
    try:
        return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    except (OSError, ValueError):
        return None  # Portable clients still have a per-process clock identity.


def probe(target, source, port):
    connection = http.client.HTTPConnection(target, port, timeout=2, source_address=(source, 0))
    start = time.monotonic()
    try:
        connection.request('GET', '/?pcs-witness=' + uuid.uuid4().hex,
                           headers={'Cache-Control': 'no-cache, no-store', 'Connection': 'close'})
        response = connection.getresponse()
        # A healthy HTTP entry page is the only success. Redirects are failures.
        # Reading at most one byte verifies a body without storing it or draining a stream.
        success = response.status == 200 and bool(response.read(1))
        return success, round(time.monotonic() - start, 6)
    except (OSError, http.client.HTTPException):
        return False, round(time.monotonic() - start, 6)
    finally:
        connection.close()


def run(session, target, source, port, duration, interval, output):
    if not re.fullmatch('[0-9a-f]{32}', session):
        raise ValueError('invalid_session')
    dest, local = ipaddress.ip_address(target), ipaddress.ip_address(source)
    if dest.version != local.version or dest.is_unspecified or local.is_unspecified or dest.is_multicast:
        raise ValueError('invalid_binding')
    if not 5 <= duration <= 600 or not 1 <= interval <= 10 or not 1 <= port <= 65535:
        raise ValueError('invalid_bounds')
    # Detect a missing/non-local source before creating a misleading record.
    with socket.socket(socket.AF_INET6 if local.version == 6 else socket.AF_INET) as check:
        check.bind((str(local), 0))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(output, flags, 0o600)
    used = 0
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        def emit(row):
            nonlocal used
            raw = json.dumps(row, sort_keys=True, allow_nan=False) + '\n'
            used += len(raw.encode())
            if used > MAX_BYTES:
                raise ValueError('witness_limit')
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        token = uuid.uuid4().hex  # Process clock identity; not a claim about client's OS boot.
        emit({'version': 1, 'event': 'start', 'session': session, 'clock_id': token, 'boot_id': client_boot(),
              'duration': duration, 'interval': interval, **clock()})
        start, seq = time.monotonic(), 0
        while time.monotonic() - start < duration:
            before = clock()
            ok, latency = probe(str(dest), str(local), port)
            emit({'event': 'sample', 'seq': seq, 'ok': ok, 'latency': latency, **before})
            seq += 1
            time.sleep(max(0, min(interval, start + duration - time.monotonic())))
        emit({'event': 'end', 'samples': seq, **clock()})


def validate(path, session):
    """Return only fixed typed summary; arbitrary imported strings never enter reports."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('invalid_witness_file')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
        raise ValueError('incomplete_witness')
    try:
        rows = [json.loads(line) for line in raw.splitlines()]
    except (UnicodeError, RecursionError):
        raise ValueError('invalid_witness') from None
    if not 3 <= len(rows) <= 605:
        raise ValueError('invalid_witness')
    head, tail = rows[0], rows[-1]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError('invalid_witness')
    if (set(head) != {'version', 'event', 'session', 'clock_id', 'boot_id', 'duration', 'interval', 'utc', 'monotonic'} or
            head['event'] != 'start' or head['version'] != 1 or head['session'] != session or
            not isinstance(head['clock_id'], str) or not re.fullmatch('[0-9a-f]{32}', head['clock_id']) or
            type(head['duration']) is not int or not 5 <= head['duration'] <= 600 or
            type(head['interval']) is not int or not 1 <= head['interval'] <= 10):
        raise ValueError('invalid_witness')
    if head['boot_id'] is not None:
        if not isinstance(head['boot_id'], str) or str(uuid.UUID(head['boot_id'])) != head['boot_id']:
            raise ValueError('invalid_witness_boot')
    if set(tail) != {'event', 'samples', 'utc', 'monotonic'} or tail['event'] != 'end' or tail['samples'] != len(rows) - 2:
        raise ValueError('incomplete_witness')
    previous, start_utc, failures, gap = None, None, 0, 0
    for index, row in enumerate(rows):
        mono = row.get('monotonic')
        if type(mono) not in (int, float) or not math.isfinite(mono) or mono < 0:
            raise ValueError('invalid_witness_clock')
        utc = dt.datetime.fromisoformat(row['utc'])
        if utc.tzinfo is None:
            raise ValueError('invalid_witness_clock')
        if previous is None:
            start_utc = utc
        else:
            delta = mono - previous
            if delta < 0 or abs((utc - start_utc).total_seconds() - (mono - head['monotonic'])) > 1:
                raise ValueError('invalid_witness_clock')
            gap = max(gap, delta)
        previous = mono
        if 0 < index < len(rows) - 1:
            if (set(row) != {'event', 'seq', 'ok', 'latency', 'utc', 'monotonic'} or
                    row['event'] != 'sample' or type(row['seq']) is not int or row['seq'] != index - 1 or
                    type(row['ok']) is not bool or type(row['latency']) not in (int, float) or
                    not math.isfinite(row['latency']) or not 0 <= row['latency'] <= 5):
                raise ValueError('invalid_witness_sample')
            failures += not row['ok']
    elapsed = tail['monotonic'] - head['monotonic']
    if not head['duration'] <= elapsed <= head['duration'] + 5 or gap > head['interval'] + 3:
        raise ValueError('incomplete_witness')
    return {'samples': len(rows) - 2, 'failures': failures, 'max_gap_seconds': round(gap, 6),
            'elapsed_seconds': round(elapsed, 6), 'result': 'FAIL' if failures else 'PASS',
            'scope': 'independent_http_sampling_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('session', 'target', 'source', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--interval', type=int, default=1)
    args = parser.parse_args()
    try:
        run(args.session, args.target, args.source, args.port, args.duration, args.interval, args.output)
    except (OSError, ValueError, KeyboardInterrupt):
        print('Witness incomplete; retain the partial file. No continuity result is available.')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
