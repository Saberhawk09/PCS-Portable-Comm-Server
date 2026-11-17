#!/usr/bin/env python3
"""Receiver operations for the existing, exclusive GPSD admission owner only.

No daemon opens serial here. Normal web/runtime operations never call these
functions. Maintenance must first release GPSD through a separate operator
workflow. Boot injection is called inside prepare_receiver's existing fd scope.
"""
import datetime as dt
import re
import time

from pcs_gnss import atomic_json, decode_values, ubx, valget, valset
from pcs_assistnow import (AssistanceError, Cache, ROOT, frames, load_config,
                          read_json, receiver_identity, LIVE_LENGTHS)

ANA = 0x10230001
ITFM = 0x1041000D
ACK_AIDING = 0x10110025
ALLOWED_WRITES = {ANA, ITFM, ACK_AIDING}


def verified_binding(exchange, binding=None):
    if binding is None:
        version = exchange(ubx(10, 4), 10, 4)
        unique = exchange(ubx(0x27, 3), 0x27, 3)
        binding = receiver_identity(ubx(10, 4, version), ubx(0x27, 3, unique))['binding']
    if not isinstance(binding, str) or not re.fullmatch('[a-f0-9]{64}', binding):
        raise AssistanceError('Verified receiver identity required for RAM transaction')
    return binding


def read_values(exchange, keys):
    result = decode_values(exchange(valget(keys), 6, 0x8b))
    if set(result) != set(keys):
        raise AssistanceError('Incomplete receiver setting readback')
    return result


def write_values(exchange, desired):
    if set(desired) - ALLOWED_WRITES or any(type(v) is not int or v not in (0, 1) for v in desired.values()):
        raise AssistanceError('Only approved boolean receiver RAM settings permitted')
    exchange(valset(desired), 5, 1, b'\x06\x8a')
    if read_values(exchange, desired) != desired:
        raise AssistanceError('Receiver RAM readback mismatch')


def set_feature(exchange, key, enabled, confirmed, root=ROOT, binding=None):
    if confirmed is not True or key not in (ANA, ITFM) or type(enabled) is not bool:
        raise AssistanceError('Explicit operator authorization required')
    binding = verified_binding(exchange, binding)
    before = read_values(exchange, [key])
    journal = root / 'receiver-transaction.json'
    atomic_json(journal, {'state': 'pending', 'binding': binding, 'before': {str(k): v for k, v in before.items()}})
    try:
        write_values(exchange, {key: int(enabled)})
    except BaseException:
        write_values(exchange, before)
        atomic_json(journal, {'state': 'restored'})
        raise
    atomic_json(journal, {'state': 'committed'})
    return {'state': 'Readback verified', 'enabled': enabled, 'persistent': False}


def restore_pending(exchange, root=ROOT, binding=None):
    journal = read_json(root / 'receiver-transaction.json', {})
    if journal.get('state') == 'pending':
        if not isinstance(journal.get('binding'), str) or not re.fullmatch('[a-f0-9]{64}', journal['binding']):
            raise AssistanceError('Unbound receiver transaction requires explicit maintenance review')
        if journal['binding'] != verified_binding(exchange, binding):
            raise AssistanceError('Pending transaction belongs to a different receiver or firmware')
        before = {int(k): v for k, v in journal['before'].items()}
        write_values(exchange, before)
        atomic_json(root / 'receiver-transaction.json', {'state': 'restored'})


def inject(exchange, selected, deadline_seconds=3, clock=time.monotonic, root=ROOT, binding=None):
    """ACK every frame, bounded, and restore temporary ACK policy even on failure.

    exchange must bound its own wait to the remaining transaction deadline.
    Partial injection is harmless additive volatile aiding; no rollback reset.
    """
    if not selected:
        return {'state': 'No applicable data', 'accepted': 0}
    for frame in selected:
        frames(frame)
        if frame[2] != 0x13 or frame[3] not in (0, 2, 0x20):
            raise AssistanceError('Receiver injection type denied')
        p = frame[6:-2]
        if frame[3] == 0x20:
            if len(p) != 76 or p[:2] != b'\0\0' or p[3] not in (0, 2):
                raise AssistanceError('Unqualified predictive injection')
        elif len(p) < 2 or p[1] != 0 or LIVE_LENGTHS.get((frame[3], p[0])) != len(p):
            raise AssistanceError('Unqualified live injection')
    binding = verified_binding(exchange, binding)
    before = read_values(exchange, [ACK_AIDING])
    journal = root / 'receiver-transaction.json'
    atomic_json(journal, {'state': 'pending', 'binding': binding, 'before': {str(k): v for k, v in before.items()}})
    accepted, deadline = 0, clock() + deadline_seconds
    try:
        if not before[ACK_AIDING]:
            write_values(exchange, {ACK_AIDING: 1})
        for frame in selected:
            if clock() >= deadline:
                return {'state': 'Partial / Deadline exceeded', 'accepted': accepted}
            reply = exchange(frame, 0x13, 0x60)
            if len(reply) != 8 or reply[1] != 0 or reply[3] != frame[3] or reply[4:8] != frame[6:10]:
                raise AssistanceError('Mismatched assistance acknowledgment')
            if reply[0] != 1 or reply[2] != 0:
                return {'state': 'Receiver rejected assistance', 'accepted': accepted, 'receiver_code': reply[2]}
            accepted += 1
        return {'state': 'Accepted', 'accepted': accepted}
    finally:
        write_values(exchange, before)
        atomic_json(journal, {'state': 'restored'})


def boot_assistance(exchange, identity, unique_payload, timing_healthy=False, root=ROOT, settings=None, now=None):
    """No network waits or extra device opens; disabled unless explicitly opted in.

    Receiver RTC/UTC must already be valid. After complete backup-power loss we
    leave ordinary acquisition alone; downloaded service time is never a clock.
    """
    settings = load_config() if settings is None else settings
    now = time.time() if now is None else now
    if not settings['boot_injection'] or timing_healthy:
        return {'state': 'Disabled' if not settings['boot_injection'] else 'Healthy timing / Deferred'}
    bound = receiver_identity(ubx(10, 4, identity), ubx(0x27, 3, unique_payload))
    kind, selected = Cache(root).selected(bound, now, live=settings['live_enabled'])
    if not selected:
        return {'state': 'Autonomous or ordinary acquisition', 'accepted': 0}
    # Receiver-retained date/time is required for cached MGA-ANO. Poll NAV-PVT
    # without treating a host or downloaded date as validated UTC.
    pvt = exchange(ubx(1, 7), 1, 7)
    if len(pvt) != 92 or pvt[11] & 7 != 7:
        return {'state': 'Receiver UTC unavailable / Ordinary acquisition', 'accepted': 0}
    try:
        receiver_day = dt.date(int.from_bytes(pvt[4:6], 'little'), pvt[6], pvt[7])
    except ValueError:
        return {'state': 'Receiver date invalid / Ordinary acquisition', 'accepted': 0}
    if receiver_day != dt.datetime.fromtimestamp(now, dt.timezone.utc).date():
        return {'state': 'Host/receiver date disagreement / Ordinary acquisition', 'accepted': 0}
    # No change to a receiver already reporting a usable fix (e.g. a GPSD-only
    # restart); retain ephemerides and healthy pulse behavior without aiding.
    if pvt[21] & 1:
        return {'state': 'Receiver already fixed / Deferred', 'accepted': 0}
    restore_pending(exchange, root, binding=bound['binding'])
    result = inject(exchange, selected, root=root, binding=bound['binding'])
    return dict(result, source=kind, timestamp=now)
