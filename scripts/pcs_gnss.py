#!/usr/bin/env python3
"""PCS optional GNSS policy, UBX decoding and coordinate-free diagnostics.

Importing this module never opens hardware or changes configuration.
"""
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import time

CONFIG = Path('/etc/pcs/gnss.json')
RUNTIME = Path('/run/pcs-gnss')
LEGACY = 'legacy-em7565'
NEO = 'neo-f10n'
DEFAULT = {'version': 1, 'mode': LEGACY}
MAX_AGE = 2.0
RECOVERY = 5.0
BAUDS = (115200, 38400)
# UBX-23002975, F10 SPG 6.00. Deliberately use the documented configuration
# database, not legacy CFG-TP5 payloads or another u-blox generation's keys.
KEYS = {
    'baud': (0x40520001, 115200),
    'measurement_ms': (0x30210001, 1000),
    'navigation_ratio': (0x30210002, 1),
    'ubx_out': (0x10740001, 1),
    'nmea_out': (0x10740002, 1),
    'pvt_uart1': (0x20910007, 1),
    'timeutc_uart1': (0x2091005C, 1),
    'timels_uart1': (0x20910061, 0),
    'tp_enabled': (0x10050007, 1),
    'tp_sync': (0x10050008, 1),
    'tp_period': (0x40050002, 1000000),
    'tp_period_locked': (0x40050003, 1000000),
    'tp_length': (0x40050004, 0),
    'tp_length_locked': (0x40050005, 100000),
    'tp_use_locked': (0x10050009, 1),
    'tp_align': (0x1005000A, 1),
    'tp_polarity': (0x1005000B, 1),
    'tp_grid': (0x2005000C, 0),
    'tp_pulse_def': (0x20050023, 0),
    'tp_length_def': (0x20050030, 1),
}
# Read-only diagnostics: retain the operator's per-port message rates.
OUTPUT_KEYS = {'sat_uart1': 0x20910016, 'status_uart1': 0x2091001B,
               'posecef_uart1': 0x20910025, 'posllh_uart1': 0x2091002A,
               'dop_uart1': 0x20910039, 'clock_uart1': 0x20910066,
               'sig_uart1': 0x20910346, 'timtp_uart1': 0x2091017E,
               'gga_uart1': 0x209100BB, 'gsa_uart1': 0x209100C0,
               'gsv_uart1': 0x209100C5, 'rmc_uart1': 0x209100AC,
               'gll_uart1': 0x209100CA, 'vtg_uart1': 0x209100B1}


def required_values():
    # Baud is discovered, never changed. NAV-TIMELS is not used by this gate.
    return {key: value for name, (key, value) in KEYS.items()
            if name not in ('baud', 'timels_uart1')}


def ucf_layers(path):
    """Read an operator export as evidence only; this is never a write profile."""
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value.get('type') != 'CFG-ITEM' or not isinstance(value.get('items'), list):
        raise ValueError('Unsupported u-center export')
    layers = {0: {}, 1: {}, 2: {}}
    for row in value['items']:
        key, layer, number = int(row['key'], 16), row['layer'], row['value']
        kind = row.get('valueType', '')
        if kind == 'X8' and isinstance(number, str) and re.fullmatch(r'BI:\d+', number):
            number = int(number[3:])
        bits = 8 * key_size(key)
        numeric = (type(number) is int and (-(2 ** (bits - 1)) if kind.startswith('I') else 0) <= number <
                   2 ** (bits - 1 if kind.startswith('I') else bits))
        if kind in ('R4', 'R8'):
            numeric = type(number) in (int, float) and math.isfinite(number)
        if (type(layer) is not int or layer not in layers or key in layers[layer]
                or row.get('action') != 'Set' or not numeric
                or (key >> 28 == 1 and number not in (0, 1))):
            raise ValueError('Invalid/duplicate export item')
        layers[layer][key] = number
    return layers


def export_report(stock, finished):
    before, after = ucf_layers(stock), ucf_layers(finished)
    ram = {f'0x{key:08x}': {'stock': before[0].get(key), 'finished': after[0].get(key)}
           for key in sorted(before[0].keys() | after[0].keys()) if before[0].get(key) != after[0].get(key)}
    return {'stock_layers': {str(k): len(v) for k, v in before.items()},
            'finished_layers': {str(k): len(v) for k, v in after.items()},
            'ram_changes': ram, 'unchanged_ram': sum(after[0].get(k) == v for k, v in before[0].items()),
            'persistent_evidence': {str(k): {f'0x{key:08x}': value for key, value in after[k].items()} for k in (1, 2)},
            'required_ram_changes': {f'0x{key:08x}': value for key, value in required_values().items() if after[0].get(key) != value},
            'applied': False}


def validate_config(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Unsupported GNSS configuration')
    if set(value) - {'version', 'mode', 'receiver', 'serial_offset', 'serial_delay', 'ram_profile', 'baud', 'pps_enabled'}:
        raise ValueError('Unknown GNSS setting')
    if value.get('mode') not in (LEGACY, NEO):
        raise ValueError('Invalid GNSS mode')
    result = dict(value)
    if result['mode'] == NEO:
        if type(result.get('pps_enabled', True)) is not bool:
            raise ValueError('PPS enablement must be an explicit boolean')
        if type(result.get('baud', 38400)) is not int or result.get('baud', 38400) not in BAUDS:
            raise ValueError('Unsupported verified UART baud')
        binding = result.get('receiver', {})
        if (not isinstance(binding, dict) or set(binding) != {'vid', 'pid', 'id_path', 'serial', 'model', 'protocol'}
                or binding.get('model') != 'NEO-F10N'
                or not re.fullmatch(r'\d+\.\d+', str(binding.get('protocol', '')))):
            raise ValueError('Verified NEO-F10N enrollment required')
        for key in ('vid', 'pid'):
            if not re.fullmatch(r'[0-9a-f]{4}', str(binding[key])):
                raise ValueError('Invalid USB identifier')
        for key in ('serial', 'id_path'):
            if not isinstance(binding[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:+/-]{0,180}', binding[key]):
                raise ValueError('Invalid persistent USB identity')
        if not binding['serial'] and not binding['id_path']:
            raise ValueError('USB topology enrollment required without a unique serial')
        for key, limit in (('serial_offset', 0.45), ('serial_delay', 0.45)):
            number = result.get(key)
            if type(number) not in (int, float) or not math.isfinite(number) or abs(number) > limit:
                raise ValueError('Measured serial offset and uncertainty required')
        if result['serial_delay'] < 0.01:
            raise ValueError('Serial uncertainty must include USB and message latency')
        if result.get('ram_profile') is not True:
            raise ValueError('Explicit volatile receiver profile consent required')
    return result


def load_config(path=CONFIG):
    try:
        return validate_config(json.loads(path.read_text()))
    except FileNotFoundError:
        return dict(DEFAULT)


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def match_receiver(devices, binding):
    matches = [item for item in devices if item.get('vid') == binding['vid']
               and item.get('pid') == binding['pid']
               and (item.get('serial') == binding['serial'] if binding['serial'] else
                    item.get('id_path') == binding['id_path'])]
    if len(matches) != 1:
        raise ValueError('Receiver absent or USB identity ambiguous')
    return matches[0]


def ubx(message_class, message_id, payload=b''):
    body = bytes((message_class, message_id)) + struct.pack('<H', len(payload)) + payload
    a = b = 0
    for byte in body:
        a = (a + byte) & 255
        b = (b + a) & 255
    return b'\xb5\x62' + body + bytes((a, b))


class UBXStream:
    def __init__(self):
        self.buffer = b''

    def feed(self, data):
        self.buffer = (self.buffer + data)[-65536:]
        messages = []
        while True:
            start = self.buffer.find(b'\xb5\x62')
            if start < 0:
                self.buffer = self.buffer[-1:]
                break
            self.buffer = self.buffer[start:]
            if len(self.buffer) < 6:
                break
            size = struct.unpack_from('<H', self.buffer, 4)[0]
            if size > 8192:
                self.buffer = self.buffer[2:]
                continue
            if len(self.buffer) < size + 8:
                break
            frame = self.buffer[:size + 8]
            if frame == ubx(frame[2], frame[3], frame[6:-2]):
                messages.append((frame[2], frame[3], frame[6:-2]))
                self.buffer = self.buffer[size + 8:]
            else:
                self.buffer = self.buffer[2:]
        return messages


def mon_ver(payload):
    if len(payload) < 40:
        raise ValueError('Invalid MON-VER response')
    fields = [payload[i:i + 30].rstrip(b'\0').decode('ascii', errors='replace')
              for i in range(40, len(payload), 30)]
    if not any(field == 'MOD=NEO-F10N' for field in fields):
        raise ValueError('Receiver model is not positively identified as NEO-F10N')
    protocol = next((field[8:] for field in fields if field.startswith('PROTVER=')), '')
    if protocol != '40.00':
        raise ValueError('Unsupported F10 protocol; review against its interface document')
    return {'model': 'NEO-F10N', 'protocol': protocol,
            'firmware': payload[:30].rstrip(b'\0').decode('ascii', errors='replace'),
            'hardware': payload[30:40].rstrip(b'\0').decode('ascii', errors='replace')}


def key_size(key):
    return {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}[key >> 28]


def valget(keys, layer=0):
    return ubx(6, 0x8B, struct.pack('<BBH', 0, layer, 0) + b''.join(struct.pack('<I', key) for key in keys))


def decode_values(payload):
    if len(payload) < 4 or payload[0] != 1:
        raise ValueError('Invalid VALGET response')
    values, pos = {}, 4
    while pos < len(payload):
        if pos + 4 > len(payload):
            raise ValueError('Truncated configuration key')
        key = struct.unpack_from('<I', payload, pos)[0]
        size = key_size(key)
        pos += 4
        if pos + size > len(payload):
            raise ValueError('Truncated configuration value')
        values[key] = int.from_bytes(payload[pos:pos + size], 'little')
        pos += size
    return values


def valset(changes, persistent=False):
    # Persistent writes are a separate CLI operation, never a boot default.
    layer = 7 if persistent else 1
    payload = struct.pack('<BBBB', 0, layer, 0, 0)
    for key, value in changes.items():
        payload += struct.pack('<I', key) + int(value).to_bytes(key_size(key), 'little')
    return ubx(6, 0x8A, payload)


class MessageActivity:
    """Fresh, coordinate-free metadata; never authorizes a clock reference."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.seen, self.signals = clock, {}, None

    def update(self, cls, msg, payload):
        if (cls, msg) == (1, 0x43):  # NAV-SIG, protocol 40.00 version 0
            if len(payload) < 8 or payload[4] != 0 or len(payload) != 8 + payload[5] * 16:
                return
            self.signals = payload[5]
            self.seen['signal'] = self.clock()
        elif (cls, msg) == (1, 0x21) and len(payload) == 20:
            self.seen['timeutc'] = self.clock()
        elif (cls, msg) == (13, 1) and len(payload) == 16:
            # TIM-TP describes the next pulse, not an authenticated UTC fix.
            self.seen['tim_tp'] = self.clock()

    def status(self):
        now = self.clock()
        ages = {name + '_age_seconds': round(now - self.seen[name], 2) if name in self.seen else None
                for name in ('signal', 'timeutc', 'tim_tp')}
        age = ages['signal_age_seconds']
        return ages | {'signals_reported': self.signals if age is not None and 0 <= age <= MAX_AGE else None}


def pvt_time(payload):
    if len(payload) != 92:
        raise ValueError('Invalid NAV-PVT length')
    itow, year, month, day, hour, minute, second, flags, accuracy, nano = struct.unpack_from('<IHBBBBBBIi', payload)
    fix_type, fix_flags = payload[20:22]
    if itow >= 604800000:
        raise ValueError('Invalid GPS time of week')
    # Confirmed bits are not supported by every protocol; never assume them.
    if flags & 7 != 7 or not fix_flags & 1 or fix_type not in (2, 3, 4, 5):
        raise ValueError('Receiver UTC/fix is not valid and fully resolved')
    if accuracy > 100000000:  # require <0.1 s, well inside second pairing window
        raise ValueError('Receiver UTC uncertainty exceeds pairing budget')
    if second == 60:
        raise ValueError('Leap transition held out until an unambiguous UTC epoch')
    epoch = dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc).timestamp() + nano / 1e9
    if not 1704067200 <= epoch <= 4102444800:
        raise ValueError('Receiver UTC outside supported date range')
    return itow, epoch, accuracy / 1e9


class TimeGate:
    """Bounded epoch matching; repeated reports cannot refresh a time solution."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.emitted = None
        self.last_epoch = None
        self.last_itow = None
        self.received = float('-inf')
        self.good_since = None
        self.valid = False
        self.reason = 'Waiting for validated UTC'

    def invalidate(self, reason):
        self.valid = False
        self.good_since = None
        self.reason = reason

    def update(self, payload):
        try:
            itow, epoch, accuracy = pvt_time(payload)
        except (ValueError, OverflowError, struct.error):
            self.invalidate('Receiver UTC invalid')
            return
        now = self.clock()
        if self.last_epoch is not None:
            delta = epoch - self.last_epoch
            elapsed = now - self.received
            if delta <= 0:
                self.invalidate('Repeated or regressed receiver epoch')
                return
            tow_delta = ((itow - self.last_itow) % 604800000) / 1000
            if abs(delta - elapsed) > 1.0 or abs(tow_delta - delta) > 0.002:
                self.invalidate('Receiver epoch continuity failure')
                self.last_epoch, self.last_itow, self.received = epoch, itow, now
                return
        if now - self.received > MAX_AGE:
            self.good_since = None
        self.last_epoch, self.last_itow, self.received = epoch, itow, now
        self.accuracy = accuracy
        if self.good_since is None:
            self.good_since = now
        self.valid = True
        self.reason = 'Receiver UTC validated'

    def fresh(self):
        return self.valid and 0 <= self.clock() - self.received <= MAX_AGE

    def ready(self):
        return self.fresh() and self.good_since is not None and self.clock() - self.good_since >= RECOVERY

    def sample(self, report, device):
        if not self.ready() or report.get('device') != device:
            return None
        try:
            numbers = [report[key] for key in ('real_sec', 'real_nsec', 'clock_sec', 'clock_nsec')]
            if any(type(number) is not int for number in numbers):
                return None
            real_sec, real_ns, clock_sec, clock_ns = numbers
            if not (0 <= real_ns < 1000000000 and 0 <= clock_ns < 1000000000):
                return None
            epoch = real_sec + real_ns / 1e9
            if abs(epoch - self.last_epoch) > 0.02:
                return None
            # One timing sample per epoch, even when GPSD repeats a TOFF.
            if getattr(self, 'emitted', None) == epoch:
                return None
            self.emitted = epoch
            stamp = clock_sec + clock_ns / 1e9
            return stamp, epoch - stamp
        except (KeyError, TypeError, ValueError):
            return None


def pps_devices(sysroot=Path('/sys')):
    result = []
    for item in sorted((sysroot / 'class/pps').glob('pps*')):
        try:
            parent = item / 'device/of_node'
            if parent.exists():
                node = parent.resolve(strict=True)
            else:
                # Raspberry Pi kernels can expose PPS as virtual class nodes,
                # without a device parent. pps-gpio's kernel-generated name is
                # pdev->name + '.' + pdev->id (-1 for these OF devices). Pair it
                # with exactly one device bound to the actual pps-gpio driver,
                # then apply the same GPIO-controller/line/edge validation.
                name = (item / 'name').read_text().strip()
                driver = sysroot / 'bus/platform/drivers/pps-gpio'
                matches = [path for path in driver.glob('*')
                           if (path / 'of_node').exists() and name == path.name + '.-1']
                if len(matches) != 1:
                    continue
                node = (matches[0] / 'of_node').resolve(strict=True)
            compatible = (node / 'compatible').read_bytes().split(b'\0')
            gpios = (node / 'gpios').read_bytes()
            # Pi 4: phandle, BCM line, flags. Reject a different GPIO controller.
            phandle, line, flags = struct.unpack('>III', gpios)
            controllers = [path.parent for path in (sysroot / 'firmware/devicetree/base').rglob('phandle')
                           if path.read_bytes() == struct.pack('>I', phandle)]
            supported_controller = (len(controllers) == 1 and bool(
                {b'brcm,bcm2835-gpio', b'brcm,bcm2711-gpio'} &
                set((controllers[0] / 'compatible').read_bytes().split(b'\0'))))
            if (b'pps-gpio' in compatible and line == 26 and flags == 0 and not (node / 'assert-falling-edge').exists() and len(controllers) == 1
                    and supported_controller):
                result.append('/dev/' + item.name)
        except (OSError, ValueError, struct.error):
            continue
    return result


def parse_chrony(tracking, sources):
    fields = dict(line.split(':', 1) for line in tracking.splitlines() if ':' in line)
    fields = {key.strip(): value.strip() for key, value in fields.items()}
    selected, states = None, {}
    for line in sources.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 2 and parts[0][0] in '#^=':
            states[parts[1]] = parts[0][1]
            if parts[0][1] == '*':
                selected = parts[1]
    reference = fields.get('Reference ID', '')
    local = reference.startswith('7F7F0101')
    sync = fields.get('Leap status') == 'Normal' and bool(selected) and not local
    source = ('NEO-F10N PPS' if selected == 'F10P' else 'NEO-F10N USB' if selected == 'F10N'
              else 'EM7565 GNSS' if selected == 'GPS' else 'Internet NTP' if sync
              else 'Local holdover' if local else 'Unsynchronized')
    def metric(name):
        try:
            value = float(fields[name].split()[0])
            return value if math.isfinite(value) else None
        except (KeyError, ValueError, IndexError):
            return None
    offset, dispersion, delay = metric('System time'), metric('Root dispersion'), metric('Root delay')
    if offset is not None and 'slow' in fields.get('System time', ''):
        offset = -abs(offset)
    uncertainty = (dispersion + max(0, delay or 0) / 2 + abs(offset or 0)) if dispersion is not None else None
    return {'source': source, 'synchronized': sync, 'selected_ref': selected,
            'pps_selection': states.get('F10P', '?'), 'offset_seconds': offset,
            'uncertainty_seconds': uncertainty}


def diagnostic(state):
    if state.get('mode') != NEO:
        return 'Legacy EM7565; optional NEO-F10N not active'
    if not state.get('connected'):
        return 'USB present; receiver communication unavailable' if state.get('usb_connected') else 'NEO-F10N not connected'
    if not state.get('utc_valid'):
        return 'PPS present; UTC invalid' if state.get('pps_active') else 'Receiver connected; no fix / UTC unavailable' if not state.get('fix_valid') else 'Receiver fix; UTC unavailable'
    if state.get('pps_validation') == 'disabled':
        return 'Valid USB GNSS time; PPS awaiting commissioning'
    if not state.get('pps_present') or not state.get('pps_active'):
        return 'Valid USB GNSS time; PPS unavailable'
    if state.get('source') == 'NEO-F10N PPS' and state.get('synchronized'):
        return 'Synchronized using validated PPS'
    return 'PPS available; not selected by Chrony'


PUBLIC_FIELDS = {'version', 'mode', 'model', 'connected', 'usb_connected', 'fix_valid', 'utc_valid', 'satellites_visible',
                 'signals_reported', 'signal_age_seconds', 'timeutc_age_seconds', 'tim_tp_age_seconds',
                 'satellites_used', 'pps_present', 'pps_active', 'pps_age_seconds', 'pps_validation',
                 'source', 'synchronized', 'selected_ref', 'pps_selection', 'offset_seconds',
                 'uncertainty_seconds', 'diagnostic', 'antenna_path', 'state'}


def read_status(runtime=RUNTIME, config=CONFIG, now=None):
    try:
        mode = load_config(config)['mode']
    except (OSError, ValueError, TypeError):
        return {'mode': 'invalid', 'state': 'configuration-error', 'diagnostic': 'Invalid GNSS configuration'}
    if mode == LEGACY:
        return {'version': 1, 'mode': LEGACY, 'state': 'legacy', 'antenna_path': 'Unknown (not queried)',
                'diagnostic': diagnostic({'mode': LEGACY})}
    try:
        value = json.loads((runtime / 'status.json').read_text())
        age = (time.monotonic() if now is None else now) - value['updated_monotonic']
        if not isinstance(value, dict) or value.get('mode') != NEO or not 0 <= age <= 5:
            raise ValueError('stale status')
        # Allowlist is intentional: raw reports/coordinates can never transit here.
        return {key: value[key] for key in PUBLIC_FIELDS if key in value
                and type(value[key]) in (str, int, float, bool, type(None))
                and (not isinstance(value[key], str) or len(value[key]) <= 160)
                and (not isinstance(value[key], float) or math.isfinite(value[key]))}
    except (OSError, ValueError, KeyError, TypeError):
        return {'mode': NEO, 'state': 'stale', 'utc_valid': False, 'pps_validation': 'unknown',
                'diagnostic': 'GNSS telemetry unavailable; inspect timing service'}


def time_label(status, fallback):
    return status.get('source', fallback) if status.get('mode') == NEO and status.get('state') == 'fresh' else fallback
