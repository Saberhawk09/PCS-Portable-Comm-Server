"""Qualification-specific allowlist, deliberately smaller than public dashboard data."""
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time

from pcs_qualify_state import HarnessError, boot_id, finite, read_json

POWER = Path('/run/pcs-power-monitor/status.json')
UPLINK = Path('/run/pcs-uplink-manager/status.json')
ROLES = ('input', 'rail_5v', 'rail_12v', 'starlink')
SERVICES = ('pcs-power-monitor.service', 'pcs-uplink-manager.service')


def command(argv, timeout=5):
    """Fixed callers only. Bound child output, time, and descriptors; never return stderr."""
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(argv, stdout=output, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, timeout=timeout,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                                    preexec_fn=limits, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise HarnessError('collector_unavailable') from None
        if result.returncode:
            raise HarnessError('collector_failed')
        output.seek(0)
        raw = output.read(65537)
        if len(raw) >= 65536:
            raise HarnessError('collector_truncated')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise HarnessError('collector_invalid') from None


def fresh(value, timestamp, source_boot, now, boot, max_age):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise HarnessError('cache_schema')
    if not finite(timestamp) or not 0 <= now - timestamp <= max_age or source_boot != boot:
        raise HarnessError('cache_stale_or_wrong_boot')


def boolean(value):
    if type(value) is not bool:
        raise HarnessError('cache_schema')
    return value


def health(value):
    if value not in ('ok', 'warn', 'bad'):
        raise HarnessError('cache_schema')
    return value


def sanitize_power(value, now, boot):
    energy = value.get('energy_tracking') if isinstance(value, dict) else None
    fresh(value, value.get('collected_at_epoch') if isinstance(value, dict) else None,
          energy.get('boot_id') if isinstance(energy, dict) else None, now, boot, 10)
    rows = value.get('monitors')
    if not isinstance(rows, dict) or any(role not in rows for role in ROLES):
        raise HarnessError('cache_schema')
    result = {'status': health(value.get('status')), 'monitors': {}}
    for role in ROLES:
        row = rows[role]
        if not isinstance(row, dict):
            raise HarnessError('cache_schema')
        clean = {'status': health(row.get('status'))}
        for field in ('voltage', 'current', 'power'):
            v = row.get(field)
            if v is not None and (not finite(v) or abs(v) > 10000):
                raise HarnessError('cache_schema')
            clean[field] = v
        result['monitors'][role] = clean
    result['age_seconds'] = round(now - value['collected_at_epoch'], 3)
    return result


def sanitize_uplink(value, now, boot):
    fresh(value, value.get('generated_at') if isinstance(value, dict) else None,
          value.get('boot') if isinstance(value, dict) else None, now, boot, 30)
    if value.get('mode') not in ('manual', 'auto'):
        raise HarnessError('cache_schema')
    rows = value.get('uplinks')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise HarnessError('cache_schema')
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get('type') not in ('ethernet', 'wifi', 'cellular'):
            raise HarnessError('cache_schema')
        item = {'slot': len(clean), 'type': row['type']}
        for field in ('link', 'address', 'address6', 'active', 'selected', 'selected6', 'owned', 'suppressed'):
            item[field] = boolean(row.get(field))
        for field in ('internet', 'internet6'):
            item[field] = None if row.get(field) is None else boolean(row[field])
        clean.append(item)
    return {'mode': value['mode'], 'internet': boolean(value.get('internet')),
            'uplinks': clean, 'collector_error': bool(value.get('error')),
            'age_seconds': round(now - value['generated_at'], 3)}


def checkpoint(power=POWER, uplink=UPLINK):
    now, boot = time.time(), boot_id()
    evidence, issues = {}, []
    for name, path, sanitize in (('power', power, sanitize_power), ('uplink', uplink, sanitize_uplink)):
        try:
            evidence[name] = sanitize(read_json(path, 262144), now, boot)
        except (OSError, ValueError, TypeError, HarnessError):
            issues.append(name + '_unavailable')
    services = {}
    for unit in SERVICES:
        try:
            state = command(['/usr/bin/systemctl', 'show', '--value', '--property=ActiveState', unit]).strip()
            if state not in ('active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'):
                raise HarnessError('service_schema')
            services[unit] = state
        except HarnessError:
            issues.append('service_unavailable')
    evidence['services'] = services
    evidence['issues'] = sorted(set(issues))
    return evidence


def assess(evidence):
    if evidence['issues']:
        return 'INCONCLUSIVE'
    if any(v != 'active' for v in evidence['services'].values()):
        return 'FAIL'
    power, uplink = evidence['power'], evidence['uplink']
    if power['status'] == 'bad' or any(r['status'] == 'bad' for r in power['monitors'].values()):
        return 'FAIL'
    if uplink['collector_error'] or any(r['internet'] is None or r['internet6'] is None for r in uplink['uplinks']):
        return 'INCONCLUSIVE'
    if any(r[field] is None for r in power['monitors'].values() for field in ('voltage', 'current', 'power')):
        return 'INCONCLUSIVE'
    if power['status'] == 'warn' or not uplink['internet'] or any(r['status'] == 'warn' for r in power['monitors'].values()):
        return 'PASS WITH OBSERVATION'
    return 'PASS'
