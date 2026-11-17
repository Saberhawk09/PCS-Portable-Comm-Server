#!/usr/bin/env python3
"""Thingstream client and host cache. Never opens the receiver or sets UTC.

Secrets enter via root-only files, never arguments, URLs in logs or exports.
Network and receiver transactions are deliberately separate.
"""
import datetime as dt
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from pcs_gnss import atomic_json, mon_ver, ubx

ROOT = Path('/var/lib/pcs-assistnow')
CONFIG = Path('/etc/pcs/assistnow.json')
CREDENTIAL = Path('/etc/pcs/assistnow/credentials.json')
SERVICE = 'https://assistnow.services.u-blox.com/GetAssistNowData.ashx'
ZTP = 'https://api.thingstream.io/ztp/assistnow/credentials'
MAX_PAYLOAD = 512 * 1024
DEFAULT = {'version': 1, 'automatic_refresh': False, 'window_days': 7,
           'refresh_hours': 72, 'allow_metered': False, 'boot_injection': False,
           'live_enabled': False, 'live_cost_authorized': False,
           'predictive_requests_day': 2, 'live_requests_day': 1,
           'requests_month': 250, 'bytes_day': 1024 * 1024}


class AssistanceError(ValueError):
    """Only fixed, secret-free messages may cross this boundary."""


def config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT) or value.get('version', 1) != 1:
        raise AssistanceError('Invalid assistance settings')
    result = DEFAULT | value
    for key in ('automatic_refresh', 'allow_metered', 'boot_injection', 'live_enabled', 'live_cost_authorized'):
        if type(result[key]) is not bool:
            raise AssistanceError('Invalid boolean assistance setting')
    if type(result['window_days']) is not int or result['window_days'] not in (1, 3, 7, 14):
        raise AssistanceError('Prediction window must be 1, 3, 7 or 14 days')
    for key, low, high in (('refresh_hours', 12, 336), ('predictive_requests_day', 0, 2),
                           ('live_requests_day', 0, 10), ('requests_month', 0, 300),
                           ('bytes_day', MAX_PAYLOAD, 10 * 1024 * 1024)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise AssistanceError('Assistance request/data limit outside bounds')
    if result['live_enabled'] and not result['live_cost_authorized']:
        raise AssistanceError('Live Orbits requires explicit cost authorization')
    return result


def read_json(path, default=None, private=False):
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PAYLOAD * 2:
            raise AssistanceError('Invalid local assistance file')
        if private and os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077):
            raise AssistanceError('Assistance credentials/settings require root ownership and mode 0600')
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, AssistanceError):
            raise
        raise AssistanceError('Cannot read assistance file') from None


def load_config(path=CONFIG):
    return config(read_json(path, {}, private=True))


def valid_credentials(value):
    if not isinstance(value, dict):
        return False
    identity = value.get('identity')
    return (isinstance(identity, dict) and identity.get('model') == 'NEO-F10N'
            and identity.get('protocol') == '40.00'
            and isinstance(identity.get('firmware'), str) and identity['firmware'].startswith('EXT SPGL1L5 6.00')
            and isinstance(identity.get('binding'), str) and bool(re.fullmatch('[a-f0-9]{64}', identity['binding']))
            and value.get('service_url') == SERVICE and isinstance(value.get('chipcode'), str)
            and bool(re.fullmatch(r'[A-Za-z0-9+/=_-]{8,512}', value['chipcode']))
            and isinstance(value.get('allowed_data'), list) and len(value['allowed_data']) <= 32
            and all(isinstance(v, str) and len(v) < 40 for v in value['allowed_data']))


def load_credentials(path=None):
    try:
        value = read_json(CREDENTIAL if path is None else path, private=True)
    except AssistanceError:
        return {'invalid': True}
    return value if value is None or valid_credentials(value) else {'invalid': True}


def frames(data):
    """Strict whole-payload validation; no stream resynchronization of downloads."""
    if not isinstance(data, bytes) or not 8 <= len(data) <= MAX_PAYLOAD:
        raise AssistanceError('Invalid assistance payload size')
    result, offset = [], 0
    while offset < len(data):
        if offset + 8 > len(data) or data[offset:offset + 2] != b'\xb5\x62':
            raise AssistanceError('Malformed UBX assistance frame')
        size = struct.unpack_from('<H', data, offset + 4)[0]
        if size > 8192 or offset + size + 8 > len(data):
            raise AssistanceError('Truncated UBX assistance frame')
        frame = data[offset:offset + size + 8]
        if frame != ubx(frame[2], frame[3], frame[6:-2]):
            raise AssistanceError('UBX assistance checksum mismatch')
        result.append(frame)
        offset += size + 8
    return result


def receiver_identity(version_frame, unique_frame):
    v, u = frames(version_frame), frames(unique_frame)
    if len(v) != 1 or len(u) != 1 or v[0][2:4] != b'\x0a\x04' or u[0][2:4] != b'\x27\x03':
        raise AssistanceError('Verified receiver response frames required')
    if len(v[0][6:-2]) < 40 or (len(v[0][6:-2]) - 40) % 30:
        raise AssistanceError('Malformed receiver version extensions')
    p = u[0][6:-2]
    # F10 R02 documents v1/40-bit; the commissioned F10 also emits the
    # manufacturer's v2/48-bit form documented by M10 and Thingstream ZTP.
    # Keep exact version/length pairs and bind every ID byte, never truncate.
    if (len(p), p[0] if p else None) not in ((9, 1), (10, 2)) or p[1:4] != b'\0\0\0' or not any(p[4:]):
        raise AssistanceError('Unsupported unique receiver identity')
    identity = mon_ver(v[0][6:-2])
    if not identity['firmware'].startswith('EXT SPGL1L5 6.00'):
        raise AssistanceError('Unqualified receiver firmware')
    # Include full firmware/hardware/protocol: replacement or update needs rebind.
    binding = hashlib.sha256(p[4:] + json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity | {'binding': binding}


def validate_predictive(data, today, window=14):
    result, dates, constellations = [], set(), set()
    for frame in frames(data):
        p = frame[6:-2]
        if frame[2:4] != b'\x13\x20' or len(p) != 76 or p[:2] != b'\0\0':
            raise AssistanceError('Only protocol 40 MGA-ANO predictions accepted')
        gnss, sv = p[3], p[2]
        if gnss not in (0, 2) or not 1 <= sv <= (32 if gnss == 0 else 36):
            raise AssistanceError('Unsupported constellation assistance; BeiDou compatibility unverified')
        try:
            date = dt.date(2000 + p[4], p[5], p[6])
        except ValueError:
            raise AssistanceError('Invalid prediction date') from None
        if not today - dt.timedelta(days=1) <= date <= today + dt.timedelta(days=window):
            raise AssistanceError('Expired or out-of-window prediction')
        dates.add(date.isoformat())
        constellations.add('GPS' if gnss == 0 else 'Galileo')
        result.append((date, frame))
    if not any(date >= today for date, _ in result):
        raise AssistanceError('Expired prediction archive')
    return result, sorted(dates), sorted(constellations)


# Message subtype/length allowlist from F10 protocol 40.00. No flash/DBD,
# position aiding, BeiDou D1/D2, reset, or configuration command is admissible.
LIVE_LENGTHS = {(0, 1): 68, (0, 2): 36, (0, 4): 40, (0, 5): 20, (0, 6): 16,
                (2, 1): 76, (2, 2): 32, (2, 3): 12, (2, 5): 20, (0x40, 0x10): 24}


def validate_live(data, now):
    result = frames(data)
    has_time, has_ephemeris = False, False
    for frame in result:
        p = frame[6:-2]
        if (frame[2] != 0x13 or len(p) < 2 or p[1] != 0
                or LIVE_LENGTHS.get((frame[3], p[0])) != len(p)):
            raise AssistanceError('Unsupported Live Orbits assistance type')
        if frame[3] in (0, 2) and p[0] in (1, 2) and not 1 <= p[2] <= (32 if frame[3] == 0 else 36):
            raise AssistanceError('Invalid assistance spacecraft')
        if frame[3] == 0x40:
            # This validates remote freshness only. This value never sets the
            # system clock and is never admitted as a Chrony reference.
            try:
                year = struct.unpack_from('<H', p, 4)[0]
                epoch = dt.datetime(year, *p[6:11], tzinfo=dt.timezone.utc).timestamp()
            except (ValueError, OverflowError):
                raise AssistanceError('Invalid service time aiding') from None
            if abs(now - epoch) > 600:
                raise AssistanceError('Expired Live Orbits response')
            has_time = True
        has_ephemeris |= frame[3] in (0, 2) and p[0] == 1
    if not has_time or not has_ephemeris or result[0][3] != 0x40:
        raise AssistanceError('Live Orbits requires initial time and ephemeris')
    return result


def atomic_bytes(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Cache:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def publish(self, data, identity, kind, now, window=7):
        today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
        if kind == 'predictive':
            _, days, constellations = validate_predictive(data, today, window)
            expires = dt.datetime.combine(dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp()
        elif kind == 'live':
            validate_live(data, now)
            days, constellations, expires = [], ['GPS', 'Galileo'], now + 2 * 3600
        else:
            raise AssistanceError('Invalid assistance cache kind')
        digest = hashlib.sha256(data).hexdigest()
        manifest = {'version': 1, 'binding': identity['binding'], 'kind': kind, 'sha256': digest,
                    'downloaded_at': now, 'expires_at': expires, 'days': days,
                    'constellations': constellations, 'bytes': len(data)}
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_bytes(self.root / (digest + '.ubx'), data)
        # A single manifest is the commit point. Interruption cannot expose a
        # partial archive or erase the previous usable manifest.
        atomic_bytes(self.root / (kind + '.json'), json.dumps(manifest, sort_keys=True).encode())
        self.prune(now)
        return manifest

    def read(self, identity, kind, now):
        manifest = read_json(self.root / (kind + '.json'))
        if manifest is None:
            raise AssistanceError('Assistance cache missing')
        if (not isinstance(manifest, dict) or manifest.get('version') != 1
                or any(type(manifest.get(key)) not in (int, float) or not math.isfinite(manifest[key])
                       or not 1704067200 <= manifest[key] <= 4102444800
                       for key in ('downloaded_at', 'expires_at'))):
            raise AssistanceError('Invalid assistance cache metadata')
        digest = manifest.get('sha256', '')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or manifest.get('binding') != identity['binding'] or manifest.get('kind') != kind:
            raise AssistanceError('Assistance cache receiver identity mismatch')
        if not manifest.get('downloaded_at', now + 1) <= now < manifest.get('expires_at', 0):
            raise AssistanceError('Assistance cache expired or clock uncertain')
        path = self.root / (digest + '.ubx')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PAYLOAD:
            raise AssistanceError('Invalid cache payload')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise AssistanceError('Corrupt cache payload')
        if kind == 'predictive':
            # Old days in an otherwise usable archive are normal. Validate at
            # original download date, then select only today's MGA-ANO frames.
            original = dt.datetime.fromtimestamp(manifest['downloaded_at'], dt.timezone.utc).date()
            validate_predictive(data, original)
        else:
            validate_live(data, manifest['downloaded_at'])
        return data, manifest

    def selected(self, identity, now, live=False):
        if live:
            try:
                data, _ = self.read(identity, 'live', now)
                # Download-time absolute UTC becomes stale in a host cache.
                # The admission path already requires receiver-resolved UTC;
                # never replay old MGA-INI-TIME into that receiver.
                return 'live', [frame for frame in frames(data) if frame[3] != 0x40]
            except (AssistanceError, OSError, ValueError, TypeError, OverflowError):
                pass
        try:
            data, _ = self.read(identity, 'predictive', now)
            today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
            selected = [f for f in frames(data) if f[10:13] == bytes((today.year - 2000, today.month, today.day))]
            if selected:
                return 'predictive', selected
        except (AssistanceError, OSError, ValueError, TypeError, OverflowError):
            pass
        return 'autonomous-or-ordinary', []

    def prune(self, now):
        keep = set()
        for kind in ('predictive', 'live'):
            try:
                manifest = read_json(self.root / (kind + '.json'), {})
                if (not isinstance(manifest, dict) or type(manifest.get('expires_at', 0)) not in (int, float)
                        or not math.isfinite(manifest.get('expires_at', 0))
                        or not isinstance(manifest.get('sha256', ''), str)):
                    raise AssistanceError('Invalid cache metadata')
            except AssistanceError:
                # Remove only this subsystem's unusable manifest, then prune
                # payloads which no remaining valid manifest references.
                (self.root / (kind + '.json')).unlink(missing_ok=True)
                continue
            if manifest.get('expires_at', 0) <= now:
                (self.root / (kind + '.json')).unlink(missing_ok=True)
            elif re.fullmatch('[a-f0-9]{64}', manifest.get('sha256', '')):
                keep.add(manifest['sha256'] + '.ubx')
        for path in self.root.glob('*.ubx'):
            if re.fullmatch(r'[a-f0-9]{64}\.ubx', path.name) and path.name not in keep:
                path.unlink()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AssistanceError('Assistance redirect refused')


def https(url, body=None):
    """Fixed vendor endpoints, verified TLS, no proxies/redirect credential leaks."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ('assistnow.services.u-blox.com', 'api.thingstream.io'):
        raise AssistanceError('Untrusted assistance endpoint')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, data=body, headers={'User-Agent': 'PCS-AssistNow/2.4.1',
                                      'Content-Type': 'application/json'} if body is not None else {'User-Agent': 'PCS-AssistNow/2.4.1'})
    try:
        with opener.open(request, timeout=8) as response:
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_PAYLOAD):
                raise AssistanceError('Invalid or excessive assistance response length')
            chunks, size, deadline = [], 0, time.monotonic() + 12
            while True:
                if time.monotonic() >= deadline:
                    raise AssistanceError('Assistance response deadline exceeded')
                chunk = response.read1(min(16384, MAX_PAYLOAD + 1 - size))
                if not chunk:
                    if declared is not None and size != int(declared):
                        raise AssistanceError('Interrupted assistance response')
                    return b''.join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_PAYLOAD:
                    raise AssistanceError('Assistance response exceeds size limit')
    except urllib.error.HTTPError as exc:
        # Never propagate HTTPError/reason/body/request URL: chipcode is in query.
        raise AssistanceError('Assistance HTTP ' + str(int(exc.code))) from None
    except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException):
        raise AssistanceError('Assistance network unavailable or TLS verification failed') from None


def enrollment_credentials(response, identity):
    """Normalize documented arrays and the service's observed CSV entitlement form."""
    if not isinstance(response, dict):
        raise AssistanceError('Invalid enrollment response')
    allowed = response.get('allowedData')
    if isinstance(allowed, str) and len(allowed) <= 1280:
        allowed = [item.strip(' ') for item in allowed.split(',')] if allowed else []
    if (not isinstance(response, dict) or response.get('serviceUrl') != SERVICE
            or not isinstance(allowed, list) or len(allowed) > 32
            or not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_]{1,39}', x) for x in allowed)
            or not isinstance(response.get('chipcode'), str)
            or not re.fullmatch(r'[A-Za-z0-9+/=_-]{8,512}', response['chipcode'])):
        raise AssistanceError('Invalid enrollment credentials or service endpoint')
    return {'version': 1, 'identity': identity, 'chipcode': response['chipcode'],
            'allowed_data': allowed, 'service_url': SERVICE}


def enroll(token, version_frame, unique_frame, request=https):
    identity = receiver_identity(version_frame, unique_frame)
    if not isinstance(token, str) or not 8 <= len(token) <= 4096:
        raise AssistanceError('Invalid enrollment token')
    payload = {'token': token, 'messages': {'UBX-MON-VER': version_frame.hex(), 'UBX-SEC-UNIQID': unique_frame.hex()}}
    try:
        response = json.loads(request(ZTP, json.dumps(payload).encode()))
    except (ValueError, UnicodeError):
        raise AssistanceError('Invalid enrollment response') from None
    return enrollment_credentials(response, identity)


def network(snapshot, now=None):
    """Consume the existing networking decision. No connection activation API."""
    now = time.monotonic() if now is None else now
    age = now - snapshot.get('collected_monotonic', float('-inf'))
    status = snapshot.get('data', {}).get('network', {})
    online = 0 <= age <= 45 and status.get('internet_available') is True
    uplink = status.get('uplink_type', 'Unknown')
    # Unknown metering is conservative; Wi-Fi can be a phone hotspot.
    metered = status.get('metered') is not False
    return {'online': online, 'uplink': uplink, 'metered': metered}


class Downloader:
    def __init__(self, cache, request=https, clock=time.time):
        self.cache, self.request, self.clock = cache, request, clock

    def refresh(self, settings, credentials, uplink, usage, kind='predictive', force=False):
        settings = config(settings)
        now = self.clock()
        usage = dict(usage)
        def done(state):
            usage.update(state=state)
            return usage
        if kind not in ('predictive', 'live'):
            raise AssistanceError('Invalid request type')
        if not uplink['online']:
            return done('Offline / Using Cache')
        if uplink['metered'] and not settings['allow_metered']:
            return done('Metered or unknown uplink / Using Cache')
        if credentials is None:
            return done('Missing Credentials / Using Cache')
        if not valid_credentials(credentials):
            return done('Invalid Credentials / Using Cache')
        if kind == 'live' and not settings['live_enabled']:
            return done('Live Orbits Disabled')
        types = (['uporb_' + str(settings['window_days'])] if kind == 'predictive' else
                 ['ulorb_l1'] + [item for item in ('usvht', 'utime', 'ukion') if item in credentials['allowed_data']])
        if any(item not in credentials.get('allowed_data', []) for item in types):
            return done('Unsupported or Missing Entitlement / Using Cache')
        if credentials.get('service_url') != SERVICE or not re.fullmatch(r'[A-Za-z0-9+/=_-]{8,512}', credentials.get('chipcode', '')):
            return done('Invalid Credentials / Using Cache')
        if now < usage.get('last_attempt', 0) or now < usage.get(kind + '_retry_after', 0):
            return done('Backoff / Using Cache')
        # Duplicate suppression is independent of manual force. A forced refresh
        # may bypass the 72h cache age, never the 12h/limits/backoff constraints.
        minimum = 43200 if kind == 'predictive' else 7200
        if now - usage.get(kind + '_attempt', 0) < minimum:
            return done('Request Suppressed / Using Cache')
        if not force and now - usage.get(kind + '_success', 0) < settings['refresh_hours'] * 3600:
            return done('Cache Current')
        date = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        day, month = date.strftime('%Y-%m-%d'), date.strftime('%Y-%m')
        if usage.get('day') != day:
            usage.update(day=day, predictive_requests=0, live_requests=0, reserved_bytes=0, downloaded_bytes_day=0)
        if usage.get('month') != month:
            usage.update(month=month, requests_month=0)
        if (usage.get(kind + '_requests', 0) >= settings[kind + '_requests_day']
                or usage.get('requests_month', 0) >= settings['requests_month']
                or usage.get('reserved_bytes', 0) + MAX_PAYLOAD > settings['bytes_day']):
            return done('Usage Limit / Using Cache')
        usage.update(last_attempt=now, **{kind + '_attempt': now, kind + '_requests': usage.get(kind + '_requests', 0) + 1},
                     requests_month=usage.get('requests_month', 0) + 1,
                     reserved_bytes=usage.get('reserved_bytes', 0) + MAX_PAYLOAD)
        self.cache.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Reserve before network I/O. Crash/timeout consumes its request budget.
        atomic_json(self.cache.root / 'usage.json', usage)
        query = urllib.parse.urlencode({'chipcode': credentials['chipcode'], 'gnss': 'gps,gal', 'data': ','.join(types)})
        try:
            data = self.request(SERVICE + '?' + query)
            manifest = self.cache.publish(data, credentials['identity'], kind, now, settings['window_days'])
        except (AssistanceError, OSError, ValueError):
            # Do not serialize exceptions from HTTP, parsers, or injected clients.
            failures = min(usage.get(kind + '_failures', 0) + 1, 8)
            retry = now + min(86400, 900 * 2 ** failures)
            usage.update(**{kind + '_failures': failures, kind + '_retry_after': retry}, retry_after=retry,
                         rejected=usage.get('rejected', 0) + 1)
            if kind == 'predictive':
                usage['next_refresh'] = max(retry, now + minimum)
            return done('Download Rejected or Unavailable / Using Cache')
        usage.update(retry_after=0, last_success=now, **{kind + '_success': now, kind + '_failures': 0, kind + '_retry_after': 0},
                     last_uplink=uplink['uplink'], downloaded_bytes=usage.get('downloaded_bytes', 0) + len(data),
                     downloaded_bytes_day=usage.get('downloaded_bytes_day', 0) + len(data))
        if kind == 'predictive':
            usage['next_refresh'] = now + settings['refresh_hours'] * 3600
        return done('Available / Cached for next safe startup')


def public_status(settings, credentials, usage, cache, now):
    valid = valid_credentials(credentials)
    result = {'settings': settings, 'credentials': 'Configured' if valid else 'Invalid' if credentials else 'Missing',
              'live_entitlement': 'Available' if valid and 'ulorb_l1' in credentials.get('allowed_data', []) else 'Unavailable',
              'live_state': 'Enabled (potentially chargeable)' if settings['live_enabled'] else 'Disabled',
              'usage': {key: usage[key] for key in ('state', 'day', 'month', 'last_success', 'last_uplink', 'last_attempt', 'next_refresh',
                        'predictive_requests', 'live_requests', 'requests_month', 'downloaded_bytes',
                        'downloaded_bytes_day', 'rejected', 'retry_after') if key in usage}}
    for kind in ('predictive', 'live'):
        try:
            value = read_json(cache.root / (kind + '.json'), {})
            if (not isinstance(value, dict) or type(value.get('expires_at', 0)) not in (int, float)
                    or not math.isfinite(value.get('expires_at', 0))):
                raise AssistanceError('Invalid cache metadata')
        except AssistanceError:
            result[kind] = {'state': 'Invalid'}
            continue
        # No identity, digest, frames, chipcode or service query in this projection.
        result[kind] = {key: value[key] for key in ('downloaded_at', 'expires_at', 'days', 'constellations', 'bytes') if key in value}
        result[kind]['state'] = 'Missing' if not value else 'Expired' if now >= value.get('expires_at', 0) else 'Available'
        if value and not valid:
            result[kind]['state'] = 'Identity Unverified' if result[kind]['state'] != 'Expired' else 'Expired'
        elif value and value.get('binding') != credentials['identity']['binding']:
            result[kind]['state'] = 'Receiver Mismatch'
    result['registration_profile'] = 'Check Thingstream profile: evaluation and commercial limits differ; local caps are conservative'
    return result
