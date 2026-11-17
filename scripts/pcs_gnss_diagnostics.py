#!/usr/bin/env python3
"""Passive F10 SPG 6.00 diagnostics (UBX-23002975 R02, protocol 40.00).

This module has no serial, clock-setting, radio or network-management interface.
All amplitudes and histories are receiver measurements, never interpolations.
"""
from collections import deque
import copy
import datetime as dt
import math
import re
import struct
import time

from pcs_gnss import mon_ver, satellite_counts, SatelliteActivity

CONSTELLATIONS = {0: 'GPS', 1: 'SBAS', 2: 'Galileo', 3: 'BeiDou', 5: 'QZSS', 6: 'GLONASS', 7: 'NavIC'}
# Signal identifiers, section 1.5.4. Unknown IDs are deliberately not guessed.
SIGNALS = {(0, 0): 'L1 C/A', (0, 6): 'L5 I', (0, 7): 'L5 Q',
           (2, 0): 'E1 C', (2, 1): 'E1 B', (2, 3): 'E5a I', (2, 4): 'E5a Q',
           (3, 5): 'B1C P', (3, 6): 'B1C D', (3, 7): 'B2a P', (3, 8): 'B2a D',
           (5, 0): 'L1 C/A', (5, 8): 'L5 I', (5, 9): 'L5 Q', (7, 0): 'L5 A'}
QUALITY = ('No signal', 'Searching', 'Acquired', 'Unusable', 'Code/time locked',
           'Code/carrier/time locked', 'Code/carrier/time locked', 'Code/carrier/time locked')
HEALTH = ('Unknown', 'Healthy', 'Unhealthy', 'Unknown')
JAM = ('Unknown', 'No Indication', 'Warning', 'Critical')
SPOOF = ('Unknown', 'No Indication', 'Warning', 'Critical')
DETECTIONS = ('Simulated signal', 'Abnormal signal', 'INS/GNSS mismatch',
              'Abrupt signal change', 'Broadband interference', 'Narrowband interference')
EVENTS = ('Indication started', 'Indication stopped', 'Indication triggered', 'Indication timed out')
CORRECTION_SOURCES = {0: 'None', 1: 'SBAS', 2: 'BeiDou', 3: 'RTCM 2', 4: 'RTCM 3 OSR',
                      5: 'RTCM 3 SSR', 6: 'QZSS SLAS', 7: 'SPARTN', 8: 'CLAS'}
SBAS_SYSTEMS = {-1: 'Unknown', 0: 'WAAS', 1: 'EGNOS', 2: 'MSAS', 3: 'GAGAN', 16: 'GPS'}
MESSAGES = {(10, 4): 'receiver', (10, 0x38): 'rf', (10, 0x31): 'spectrum',
            (0x27, 9): 'security', (0x27, 0x10): 'security_log', (1, 0x43): 'signals',
            (1, 0x35): 'satellites', (1, 7): 'navigation', (1, 4): 'dop',
            (1, 3): 'acquisition', (1, 0x32): 'sbas', (1, 0x60): 'autonomous', (1, 0x34): 'orbits'}


def sbas_services(flags):
    return [name for bit, name in enumerate(('Ranging', 'Corrections', 'Integrity', 'Test mode', 'Bad signal'))
            if flags & (1 << bit)]


def correction_summary(messages):
    """Keep application, reception and source separate; only join current epochs.

    NAV-SBAS reports no correction age. Its observation age must never be
    presented as the age of a pseudorange correction. Orbits are not DGPS.
    """
    nav_message = messages.get('navigation', {})
    nav = nav_message.get('data') or {}
    state = nav_message.get('state', 'Unknown')
    current = state == 'Fresh' and nav.get('fix_valid') is True
    applied = nav.get('differential_applied') if current else None
    def aligned(name):
        message = messages.get(name, {})
        value = message.get('data') or {}
        a, b = value.get('itow_ms'), nav.get('itow_ms')
        if (message.get('state') != 'Fresh' or not current or type(a) is not int or type(b) is not int
                or not 0 <= a < 604800000 or not 0 <= b < 604800000):
            return {}
        gap = abs(a - b) % 604800000
        return value if min(gap, 604800000 - gap) <= 2000 else {}
    status, sbas = aligned('acquisition'), aligned('sbas')
    sources = set()
    for row in aligned('signals').get('rows', []):
        if row.get('used') and any(row.get(k) for k in ('pseudorange_correction_used', 'carrier_correction_used', 'doppler_correction_used')):
            code = row.get('correction_source')
            if code:
                sources.add(CORRECTION_SOURCES.get(code, 'Unknown'))
    for row in aligned('satellites').get('rows', []):
        if row.get('used'):
            for key, source in (('sbas_correction_used', 'SBAS'), ('rtcm_correction_used', 'RTCM'),
                                ('slas_correction_used', 'QZSS SLAS'), ('spartn_correction_used', 'SPARTN'), ('clas_correction_used', 'CLAS')):
                if row.get(key):
                    sources.add(source)
    # SBAS reception by itself cannot identify the source of a DGPS solution.
    system = sbas.get('system', 'Unknown')
    source = 'Unknown'
    if applied is False:
        source = 'None applied'
    elif applied and sources:
        source = ' / '.join(sorted((system if system != 'Unknown' else 'SBAS (Unknown system)')
                                  if s == 'SBAS' else s for s in sources))
    services = sbas.get('services', [])
    health = ('Stale' if state == 'Stale' else 'Unknown')
    if current:
        health = 'Not applied' if not applied else 'Applied; source unverified' if source == 'Unknown' else 'Applied'
        if 'Bad signal' in services or 'Test mode' in services:
            health = 'Warning: ' + ', '.join(s for s in services if s in ('Bad signal', 'Test mode'))
    return {'state': state, 'applied': applied, 'available': status.get('corrections_available'),
            'source': source, 'health': health, 'reported_system': system,
            'selected_prn': sbas.get('geo_prn'), 'services': services,
            'integrity': sbas.get('integrity', 'Unknown'), 'mode': sbas.get('mode', 'Unknown'),
            'sbas_state': messages.get('sbas', {}).get('state', 'Unknown'),
            'age_seconds': messages.get('sbas', {}).get('age_seconds'),
            'correction_age': 'Not reported by NAV-SBAS', 'rows': sbas.get('rows', [])}


def chrony_source_offsets(text):
    """Read-only `sources` reports; retain only F10N/F10P, never NTP addresses.

    Adjusted offset is outside brackets; measured offset is inside brackets.
    These are Chrony source residuals, not absolute pulse accuracy.
    """
    number = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))(ns|us|ms|s)'
    pattern = re.compile(r'^#([*+?x~\-])\s+(F10[NP])\s+\d+\s+-?\d+\s+([0-7]{1,3})\s+(\d+[mhdy]?)\s+' +
                         number + r'\[\s*' + number + r'\]\s+\+/-\s+' + number + r'\s*$')
    scale = {'ns': 1e-9, 'us': 1e-6, 'ms': 1e-3, 's': 1.0}
    result = {}
    for line in text.splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            continue
        selection, ref, reach, age, adjusted, adjusted_unit, measured, measured_unit, error, error_unit = match.groups()
        age_seconds = int(age[:-1]) * {'m': 60, 'h': 3600, 'd': 86400, 'y': 31536000}[age[-1]] if age[-1].isalpha() else int(age)
        values = [float(v) * scale[u] for v, u in ((adjusted, adjusted_unit), (measured, measured_unit), (error, error_unit))]
        if int(reach, 8) == 0 or not all(math.isfinite(v) for v in values) or values[2] < 0:
            continue
        result[ref] = {'selection': selection, 'reach': reach, 'sample_age_seconds': age_seconds,
                       'adjusted_offset_seconds': values[0], 'measured_offset_seconds': values[1],
                       'root_distance_seconds': values[2], 'state': 'Fresh' if age_seconds <= 5 else 'Stale'}
    return result


class UnsupportedDiagnostic(ValueError):
    pass


def repeated(payload, header, stride, count_at, version_at, version, limit=255):
    if (len(payload) < header or payload[count_at] > limit
            or len(payload) != header + payload[count_at] * stride):
        raise ValueError('Invalid diagnostic length/version/count')
    if payload[version_at] != version:
        raise UnsupportedDiagnostic('Diagnostic message version not supported')
    return range(header, len(payload), stride)


def decode(cls, msg, p):
    """Decode a checksum-validated payload. Reject other-generation layouts."""
    key = cls, msg
    if key == (10, 4):
        if len(p) < 40 or (len(p) - 40) % 30:
            raise ValueError('Invalid receiver identity length')
        return mon_ver(p)
    if key == (10, 0x38):
        rows = []
        for i in repeated(p, 4, 24, 1, 0, 0, 8):
            noise, agc = struct.unpack_from('<HH', p, i + 12)
            rows.append({'path': p[i], 'band': {0: 'L1', 1: 'L5'}.get(p[i], 'Unknown'),
                         'jamming': JAM[p[i + 1] & 3], 'antenna_status': p[i + 2],
                         'antenna_power': p[i + 3], 'noise_per_ms': noise, 'agc_raw': agc,
                         'agc_percent': agc * 100 / 8191 if agc <= 8191 else None,
                         'cw_suppression': p[i + 16]})
        return {'paths': rows}
    if key == (10, 0x31):
        paths = []
        for n, i in enumerate(repeated(p, 4, 272, 1, 0, 0, 8)):
            span, resolution, center = struct.unpack_from('<III', p, i + 256)
            if not span or not resolution or not center:
                raise ValueError('Invalid spectrum frequency metadata')
            paths.append({'path': n, 'band': 'L1' if 1500000000 <= center <= 1650000000 else
                          'L5' if 1100000000 <= center <= 1250000000 else 'Unknown',
                          'center_hz': center, 'span_hz': span, 'resolution_hz': resolution,
                          'pga_db': p[i + 268], 'amplitude_db': [v / 4 for v in p[i:i + 256]],
                          # Manufacturer formula uses bin 127, not bin 128.
                          'frequency_hz': [center + span * (b - 127) / 256 for b in range(256)]})
        return {'paths': paths, 'calibrated': False}
    if key == (0x27, 9):
        if len(p) != 12:
            raise ValueError('Invalid SEC-SIG length')
        if p[0] != 1:
            raise UnsupportedDiagnostic('SEC-SIG version not supported')
        j, s = p[4], p[8]
        return {'jamming': JAM[(j >> 1) & 3] if j & 1 else 'Disabled',
                'spoofing': SPOOF[(s >> 1) & 7] if s & 1 and (s >> 1) & 7 < 4 else
                'Unknown' if s & 1 else 'Disabled',
                'spoofing_code': (s >> 1) & 7, 'authenticity_proven': False}
    if key == (0x27, 0x10):
        events = []
        for i in repeated(p, 8, 8, 1, 0, 0, 16):
            elapsed = struct.unpack_from('<I', p, i)[0]
            detection, event = p[i + 4:i + 6]
            if detection >= len(DETECTIONS) or event >= len(EVENTS):
                raise ValueError('Unknown security event type')
            events.append({'elapsed_seconds': None if elapsed == 0xffffffff else elapsed,
                           'detection_code': detection, 'event_code': event,
                           'detection': DETECTIONS[detection], 'event': EVENTS[event],
                           'severity': 'Warning' if event in (0, 2) else 'No Indication'})
        return {'events': events}
    if key == (1, 0x43):
        rows = []
        for i in repeated(p, 8, 16, 5, 4, 0):
            gnss, sv, sig, freq, residual, cno, quality, corr, iono, flags = struct.unpack_from('<BBBBhBBBBH', p, i)
            if not sv or quality > 7:
                raise ValueError('Invalid signal identity/quality')
            name = SIGNALS.get((gnss, sig), 'Unknown')
            band = 'L5' if name.startswith(('L5', 'E5a', 'B2a')) else 'L1' if name.startswith(('L1', 'E1', 'B1C')) else 'Unknown'
            rows.append({'gnssid': gnss, 'svid': sv, 'signal_id': sig, 'signal': name,
                         'constellation': CONSTELLATIONS.get(gnss, 'Unknown'), 'band': band,
                         'cno_dbhz': cno, 'quality_code': quality, 'quality': QUALITY[quality],
                         'health': HEALTH[flags & 3], 'used': bool(flags & 0x38),
                         'pseudorange_used': bool(flags & 8), 'carrier_used': bool(flags & 16),
                         'doppler_used': bool(flags & 32), 'correction_source': corr,
                         'correction_source_name': CORRECTION_SOURCES.get(corr, 'Unknown'),
                         'pseudorange_residual_m': residual * .1, 'iono_model': iono,
                         'pseudorange_correction_used': bool(flags & 64),
                         'carrier_correction_used': bool(flags & 128), 'doppler_correction_used': bool(flags & 256)})
        return {'itow_ms': struct.unpack_from('<I', p)[0], 'rows': rows}
    if key == (1, 0x35):
        rows = []
        for i in repeated(p, 8, 12, 5, 4, 1):
            gnss, sv, cno, elev, azim, residual, flags = struct.unpack_from('<BBBbhhI', p, i)
            rows.append({'gnssid': gnss, 'svid': sv, 'cno_dbhz': cno,
                         'elevation_deg': elev if -90 <= elev <= 90 else None,
                         'azimuth_deg': azim if -90 <= elev <= 90 and 0 <= azim <= 360 else None,
                         'used': bool(flags & 8), 'quality_code': flags & 7,
                         'health': HEALTH[(flags >> 4) & 3], 'orbit_source': (flags >> 8) & 7,
                         'autonomous_available': bool(flags & (1 << 14)),
                         'predictive_available': bool(flags & (1 << 13)),
                         'corrections_available': bool(flags & (1 << 6)), 'pseudorange_residual_m': residual * .1,
                         **{name: bool(flags & (1 << bit)) for bit, name in
                            ((16, 'sbas_correction_used'), (17, 'rtcm_correction_used'), (18, 'slas_correction_used'),
                             (19, 'spartn_correction_used'), (20, 'pseudorange_correction_used'),
                             (21, 'carrier_correction_used'), (22, 'doppler_correction_used'), (23, 'clas_correction_used'))}})
        return {'itow_ms': struct.unpack_from('<I', p)[0], 'rows': rows}
    if key == (1, 0x32):
        if len(p) < 12 or len(p) != 12 + p[8] * 12:
            raise ValueError('Invalid NAV-SBAS length/count')
        system = struct.unpack_from('<b', p, 6)[0]
        rows = []
        for i in range(12, len(p), 12):
            if not p[i]:
                raise ValueError('Invalid SBAS satellite identity')
            sv_system = struct.unpack_from('<b', p, i + 3)[0]
            rows.append({'svid': p[i], 'udre_code': p[i + 2], 'system_code': sv_system,
                         'system': SBAS_SYSTEMS.get(sv_system, 'Unknown'), 'services': sbas_services(p[i + 4]),
                         'pseudorange_correction_m': struct.unpack_from('<h', p, i + 6)[0] * .01,
                         'ionosphere_correction_m': struct.unpack_from('<h', p, i + 10)[0] * .01})
        return {'itow_ms': struct.unpack_from('<I', p)[0], 'geo_prn': p[4] or None,
                'mode_code': p[5], 'mode': {0: 'Disabled', 1: 'Enabled (integrity)', 3: 'Enabled (test mode)'}.get(p[5], 'Unknown'),
                'system_code': system, 'system': SBAS_SYSTEMS.get(system, 'Unknown'),
                'services': sbas_services(p[7]), 'integrity_code': p[9] & 3,
                'integrity': {0: 'Unknown', 1: 'Unavailable or not enabled',
                              2: 'Only GPS satellites with integrity used'}.get(p[9] & 3, 'Unknown'), 'rows': rows}
    if key == (1, 7):
        if len(p) != 92:
            raise ValueError('Invalid NAV-PVT length')
        fix = p[20]
        valid = bool(p[21] & 1) and fix in (2, 3, 4, 5)
        height, msl, hacc, vacc = struct.unpack_from('<iiII', p, 32)
        return {'itow_ms': struct.unpack_from('<I', p)[0],
                'fix_type': {0: 'No fix', 1: 'Dead reckoning', 2: '2D', 3: '3D', 4: 'GNSS + dead reckoning', 5: 'Time only'}.get(fix, 'Unknown'),
                'differential_applied': bool(p[21] & 2) if valid else False,
                'fix_valid': valid, 'utc_valid': p[11] & 7 == 7,
                'receiver_time_accuracy_ns': struct.unpack_from('<I', p, 12)[0],
                'horizontal_accuracy_m': hacc / 1000 if valid else None,
                'vertical_accuracy_m': vacc / 1000 if valid else None,
                'altitude_msl_m': msl / 1000 if valid and fix in (3, 4) else None,
                'altitude_ellipsoid_m': height / 1000 if valid and fix in (3, 4) else None,
                'pdop': struct.unpack_from('<H', p, 76)[0] / 100}
    if key == (1, 4):
        if len(p) != 18:
            raise ValueError('Invalid DOP length')
        return dict(zip(('gdop', 'pdop', 'tdop', 'vdop', 'hdop', 'ndop', 'edop'),
                        [v / 100 for v in struct.unpack_from('<7H', p, 4)]))
    if key == (1, 3):
        if len(p) != 16:
            raise ValueError('Invalid NAV-STATUS length')
        ttff, uptime = struct.unpack_from('<II', p, 8)
        return {'itow_ms': struct.unpack_from('<I', p)[0], 'fix_valid': bool(p[5] & 1),
                'differential_applied': bool(p[5] & 2), 'corrections_available': bool(p[6] & 1),
                'ttff_seconds': ttff / 1000 if ttff else None, 'receiver_uptime_ms': uptime,
                'acquisition': 'Fix acquired' if p[5] & 1 else 'Acquiring'}
    if key == (1, 0x60):
        if len(p) != 16:
            raise ValueError('Invalid AOPSTATUS length')
        return {'enabled': bool(p[4] & 1), 'activity': 'Generating' if p[5] else 'Idle'}
    if key == (1, 0x34):
        rows = []
        for i in repeated(p, 8, 6, 5, 4, 1):
            gnss, sv, flags, eph, alm, other = p[i:i + 6]
            rows.append({'gnssid': gnss, 'svid': sv, 'health': HEALTH[flags & 3],
                         'ephemeris_usability': eph & 31, 'ephemeris_source': eph >> 5,
                         'orbit_type': other >> 5, 'orbit_usability': other & 31,
                         'usable_prediction': other >> 5 in (1, 2) and 0 < other & 31 < 31})
        return {'rows': rows}
    raise KeyError(key)


class Diagnostics:
    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.values, self.seen = {}, {}
        self.received_at = {}
        self.last_measurement = None
        self.trends = deque(maxlen=120)
        self.rejected = 0
        self.sky = None
        self.sky_activity = SatelliteActivity(clock)
        self.unsupported = set()

    def update_sky(self, report):
        self.sky_activity.update(report)
        counts = self.sky_activity.status()
        self.sky = (counts['satellites_visible'], counts['satellites_used']), self.clock()
        self.last_measurement = self.wall()

    def update(self, cls, msg, payload):
        name = MESSAGES.get((cls, msg))
        if not name:
            return None
        try:
            value = decode(cls, msg, payload)
        except UnsupportedDiagnostic:
            self.unsupported.add(name)
            self.rejected += 1
            return None
        except (ValueError, struct.error, IndexError):
            self.rejected += 1
            return None
        stamp = self.clock()
        self.unsupported.discard(name)
        self.values[name], self.seen[name] = value, stamp
        self.received_at[name] = self.last_measurement = self.wall()
        if name == 'signals':
            counts = {band: sum(row['band'] == band and row['quality_code'] >= 2 for row in value['rows']) for band in ('L1', 'L5')}
            self.trends.append({'timestamp': self.wall(), 'L1': counts['L1'], 'L5': counts['L5']})
        return name, value

    def snapshot(self):
        now = self.clock()
        result = {'messages': {}, 'signal_trends': list(self.trends), 'rejected_messages': self.rejected,
                  'last_measurement_at': self.last_measurement}
        for name in set(MESSAGES.values()):
            age = now - self.seen[name] if name in self.seen else None
            result['messages'][name] = {'state': 'Not Supported' if name in self.unsupported else 'Unknown' if age is None else 'Fresh' if 0 <= age <= 5 else 'Stale',
                                        'availability': 'Not Supported' if name in self.unsupported else 'Available' if age is not None else 'Unknown',
                                        'age_seconds': round(age, 2) if age is not None else None,
                                        'received_at': self.received_at.get(name),
                                        'data': copy.deepcopy(self.values.get(name))}
        signals = self.values.get('signals', {}).get('rows', [])
        # Same v2.4.0 spacecraft identity/counting function; L1/L5 are one SV.
        visible, used = satellite_counts({'satellites': signals})
        result['counts'] = {'visible': visible if 'signals' in self.seen else None,
                            'tracked': len({(r['gnssid'], r['svid']) for r in signals if r['quality_code'] >= 2}) if 'signals' in self.seen else None,
                            'used': used if 'signals' in self.seen else None,
                            'constellations': {name: satellite_counts({'satellites': [r for r in signals if r['gnssid'] == gnss]})[0]
                                               for gnss, name in CONSTELLATIONS.items()} if 'signals' in self.seen else {},
                            'state': result['messages']['signals']['state']}
        if self.sky is not None:
            (visible, used), received = self.sky
            result['counts'].update(visible=visible, used=used,
                                    state='Fresh' if 0 <= now - received <= 5 else 'Stale',
                                    source='GPSD SKY; v2.4.0 spacecraft consolidation')
        else:
            # NAV-SIG includes searched signals: do not call every entry visible.
            result['counts'].update(visible=None, used=used if 'signals' in self.seen else None,
                                    source='NAV-SIG usage; visible requires GPSD SKY')
        return result


class EventHistory:
    """Bounded persisted events. Receiver log ages change on every poll.

    Match elapsed ages against monotonic host occurrence times, with a 3 second
    tolerance for epoch/transport quantization. Persist wall anchors for process
    restart matching. Never claim an exact receiver UTC event timestamp.
    """
    def __init__(self, events=(), clock=time.monotonic, wall=time.time, limit=256):
        self.clock, self.wall = clock, wall
        self.events = list(events)[-limit:]
        self.limit = limit
        self.anchors = {id(e): self.clock() - (self.wall() - e['estimated_timestamp'])
                        for e in self.events if e.get('estimated_timestamp') is not None}
        self.last_security = {}
        for event in self.events:
            if event.get('source') == 'SEC-SIG':
                self.last_security[event['detection']] = event['severity']

    def observe_security(self, security):
        changed = False
        for name in ('jamming', 'spoofing'):
            state = security[name]
            previous = self.last_security.get(name)
            self.last_security[name] = state
            if state == previous or (state not in ('Warning', 'Critical') and previous not in ('Warning', 'Critical')):
                continue
            self.events.append({'source': 'SEC-SIG', 'detection': name, 'severity': state,
                                'event': 'Receiver state changed', 'event_code': 10,
                                'detection_code': 100 if name == 'jamming' else 101,
                                'estimated_timestamp': self.wall(), 'observed_timestamp': self.wall()})
            self.events = self.events[-self.limit:]
            changed = True
        return changed

    def observe(self, log):
        changed = False
        for row in log['events']:
            elapsed = row['elapsed_seconds']
            occurrence = self.wall() - elapsed if elapsed is not None else None
            occurrence_mono = self.clock() - elapsed if elapsed is not None else None
            # Unknown (>45d) timestamps are deduplicated by type. They cannot be
            # assigned a fabricated occurrence time.
            duplicate = any(e['detection_code'] == row['detection_code'] and e['event_code'] == row['event_code']
                            and (occurrence is None and e.get('estimated_timestamp') is None or
                                 occurrence is not None and e.get('estimated_timestamp') is not None
                                 and abs(self.anchors.get(id(e), float('-inf')) - occurrence_mono) <= 3)
                            for e in self.events)
            if duplicate:
                continue
            self.events.append(dict(row, estimated_timestamp=occurrence, observed_timestamp=self.wall(),
                                    timestamp_quality='Host estimate from receiver elapsed seconds' if occurrence else 'Older than 45 days'))
            if occurrence_mono is not None:
                self.anchors[id(self.events[-1])] = occurrence_mono
            self.events = self.events[-self.limit:]
            self.anchors = {id(e): self.anchors[id(e)] for e in self.events if id(e) in self.anchors}
            changed = True
        return changed


class SpectrumCapture:
    """Only records already-output MON-SPAN; never changes receiver settings."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = None
        self.frames = deque(maxlen=60)

    def start(self, seconds, spectrum_fresh):
        if type(seconds) is not int or not 5 <= seconds <= 300:
            raise ValueError('Capture duration must be 5..300 seconds')
        if not spectrum_fresh:
            raise ValueError('MON-SPAN unavailable: receiver message changes require approved maintenance')
        if self.active():
            raise ValueError('Capture already active')
        self.frames.clear()
        self.deadline = self.clock() + seconds

    def stop(self):
        self.deadline = None

    def active(self):
        if self.deadline is not None and self.clock() >= self.deadline:
            self.stop()
        return self.deadline is not None

    def observe(self, spectrum, timestamp):
        if self.active():
            self.frames.append(dict(copy.deepcopy(spectrum), timestamp=timestamp))

    def status(self):
        active = self.active()
        traces = []
        if self.frames:
            for path in self.frames[-1]['paths']:
                matches = [p for f in self.frames for p in f['paths'] if
                           (p['path'], p['center_hz'], p['span_hz'], p['resolution_hz'], p['pga_db']) ==
                           (path['path'], path['center_hz'], path['span_hz'], path['resolution_hz'], path['pga_db'])]
                bins = list(zip(*(p['amplitude_db'] for p in matches)))
                traces.append(dict(path, peak_db=[max(b) for b in bins],
                                   average_db=[sum(b) / len(b) for b in bins], sample_count=len(matches)))
        return {'active': active, 'remaining_seconds': max(0, round(self.deadline - self.clock())) if active else 0,
                'frame_count': len(self.frames), 'traces': traces,
                'last_capture_timestamp': self.frames[-1]['timestamp'] if self.frames else None,
                'average_kind': 'Arithmetic mean of measured dB bins; matching gain and frequency grid only'}
