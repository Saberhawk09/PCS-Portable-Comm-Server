#!/usr/bin/env python3
"""Optional Starlink diagnostics and explicitly paired lifecycle controls.

The collector only reads status. No dependency from WAN policy to this module.
RPC/reflection runs in a deadline-limited child over a device-bound TCP relay.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import threading
import time

CONFIG = Path('/etc/pcs/starlink.json')
CACHE = Path('/run/pcs-starlink/status.json')
TARGET = ('192.168.100.1', 9200)
PUBLIC_FIELDS = frozenset(('configured', 'available', 'status', 'state', 'sample_age_seconds',
    'uptime_seconds', 'latency_ms', 'packet_loss_percent', 'downlink_bps', 'uplink_bps',
    'obstruction_percent', 'obstructed', 'alerts_summary'))
ALERTS = frozenset(('thermal_shutdown', 'thermal_throttle', 'is_heating',
    'power_supply_thermal_throttle', 'slow_ethernet_speeds', 'install_pending',
    'unexpected_location', 'roaming', 'mast_not_near_vertical'))
STATES = frozenset(('CONNECTED', 'UNKNOWN', 'BOOTING', 'SEARCHING', 'STOWED',
    'THERMAL_SHUTDOWN', 'NO_SATS', 'OBSTRUCTED', 'NO_DOWNLINK', 'NO_PINGS',
    'NO_SCHEDULE', 'DISABLED', 'UNAVAILABLE'))


def load_config(path=CONFIG):
    defaults = dict(version=1, enabled=False, uplink_id='starlink', poll_seconds=15,
                    allow_reboot=False, follow_pcs_reboot=False, follow_pcs_shutdown=False,
                    paired_device_id='')
    if not path.exists():
        return defaults
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or set(raw) - set(defaults) or raw.get('version') != 1:
        raise ValueError('Invalid Starlink configuration schema')
    cfg = defaults | raw
    for key in ('enabled', 'allow_reboot', 'follow_pcs_reboot', 'follow_pcs_shutdown'):
        if type(cfg[key]) is not bool:
            raise ValueError('Starlink enable/control fields must be booleans')
    if type(cfg['poll_seconds']) is not int or not 10 <= cfg['poll_seconds'] <= 300:
        raise ValueError('Starlink poll interval must be 10..300 seconds')
    if not isinstance(cfg['uplink_id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', cfg['uplink_id']):
        raise ValueError('Invalid configured uplink ID')
    identity = cfg['paired_device_id']
    if not isinstance(identity, str) or identity and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
        raise ValueError('Invalid paired device ID')
    if cfg['allow_reboot'] and (not cfg['enabled'] or not identity):
        raise ValueError('Reboot requires enabled telemetry and an explicitly paired device')
    if cfg['follow_pcs_reboot'] and not cfg['allow_reboot']:
        raise ValueError('Following PCS reboot requires allow_reboot')
    return cfg


def resolve_interface(cfg):
    from pcs_uplink_manager import load_config as load_uplinks
    uplink = next((u for u in load_uplinks().uplinks if u.id == cfg['uplink_id']), None)
    if uplink is None or uplink.type != 'ethernet' or not uplink.mac:
        raise ValueError('Starlink requires a configured MAC-bound Ethernet uplink')
    matches = []
    for device in Path('/sys/class/net').iterdir():
        if device.name in {'lo', 'eth0'} or not (device / 'device').exists():
            continue
        if uplink.interface and device.name != uplink.interface:
            continue
        result = subprocess.run(['/usr/sbin/ethtool', '-P', device.name], capture_output=True,
                                text=True, timeout=2)
        if result.returncode == 0 and result.stdout.split() and result.stdout.split()[-1].lower() == uplink.mac.lower():
            matches.append(device.name)
    if len(matches) != 1:
        raise ValueError('Configured Starlink Ethernet device is absent or ambiguous')
    return matches[0]


def number(value, maximum=1e12):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,16}', value):
        value = int(value)  # protobuf JSON represents uint64 values as strings
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        return None
    return round(value, 3)


def normalize(raw):
    """Only allow named metrics, never device ID, location, raw errors or descriptors."""
    device = raw.get('device_state', {})
    obstruction = raw.get('obstruction_stats', {})
    outage = raw.get('outage')
    state = 'CONNECTED' if outage is None else outage.get('cause', 'UNKNOWN') if isinstance(outage, dict) else 'UNKNOWN'
    state = state if state in STATES else 'UNKNOWN'
    alerts = raw.get('alerts', {})
    active = sorted(k for k in ALERTS if isinstance(alerts, dict) and alerts.get(k) is True)
    loss = number(raw.get('pop_ping_drop_rate'), 1)
    fraction = number(obstruction.get('fraction_obstructed'), 1) if isinstance(obstruction, dict) else None
    blocked = obstruction.get('currently_obstructed') if isinstance(obstruction, dict) else None
    return dict(state=state, status='ok' if state == 'CONNECTED' and not active else 'warn',
        uptime_seconds=number(device.get('uptime_s')) if isinstance(device, dict) else None,
        latency_ms=number(raw.get('pop_ping_latency_ms'), 60000),
        packet_loss_percent=None if loss is None else round(loss * 100, 3),
        downlink_bps=number(raw.get('downlink_throughput_bps')),
        uplink_bps=number(raw.get('uplink_throughput_bps')),
        obstruction_percent=None if fraction is None else round(fraction * 100, 3),
        obstructed=blocked if type(blocked) is bool else None,
        alerts_summary=', '.join(active) if active else 'None reported')


def sanitize(value):
    return {k: v for k, v in value.items() if k in PUBLIC_FIELDS
            and (v is None or type(v) in (str, bool, int, float))}


def cached_status(path=CACHE, now=None):
    try:
        value = json.loads(path.read_text())
        age = (time.time() if now is None else now) - value['collected_at']
        if not 0 <= age <= 2 * value['poll_seconds'] + 15:
            raise ValueError('stale')
        return sanitize(value | {'sample_age_seconds': round(age, 1)})
    except (OSError, ValueError, KeyError, TypeError):
        # Optional hardware/cache absence is informational, not an appliance warning.
        return dict(configured=CONFIG.exists(), available=False, status='ok', state='UNAVAILABLE')


def connected_without_internet(uplinks, uplink_id='starlink'):
    """Carrier-present WAN failure differs from an unplugged optional Mini.

    Use the uplink manager's probes, never gRPC availability as Internet health.
    A stale observer cannot establish physical presence or a current failure.
    """
    if uplinks.get('available') is not True:
        return False
    return any(isinstance(row, dict) and row.get('id') == uplink_id
               and row.get('type') == 'ethernet' and row.get('link') is True
               and row.get('internet') is False for row in uplinks.get('uplinks', []))


def rpc_request(stub, request_class, operation, expected_id=''):
    """No raw arbitrary RPC entry point; verify identity immediately before reboot."""
    response = stub.Handle(request_class(get_status={}), timeout=3)
    if not response.HasField('dish_get_status'):
        raise ValueError('Not a dish status response')
    status = response.dish_get_status
    if operation == 'reboot':
        if not expected_id or status.device_info.id != expected_id:
            raise ValueError('Paired Mini identity mismatch; reboot refused')
        # Absence from the reflected schema fails before the RPC is submitted.
        reply = stub.Handle(request_class(reboot={}), timeout=3)
        return {'result': 'request_accepted'} if reply is not None else {'result': 'unconfirmed'}
    if operation == 'identify':
        return {'device_id': status.device_info.id}
    if operation != 'status':
        raise ValueError('Unsupported RPC operation')
    # Preserve field names and missing values; no defaults invented for missing measurements.
    from google.protobuf.json_format import MessageToDict
    return normalize(MessageToDict(status, preserving_proto_field_name=True, always_print_fields_with_no_presence=True))


def worker(operation):
    import grpc
    from yagrc.reflector import GrpcReflectionClient
    cfg = load_config()
    if not cfg['enabled']:
        raise ValueError('Telemetry disabled')
    if operation == 'reboot' and not cfg['allow_reboot']:
        raise ValueError('Reboot not armed')
    interface = resolve_interface(cfg)
    # gRPC cannot select SO_BINDTODEVICE itself. Relay exactly one local channel
    # to the fixed dish endpoint through the selected NIC, even during failover.
    with socket.socket() as upstream, socket.socket() as listener:
        upstream.settimeout(3)
        upstream.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
        upstream.connect(TARGET)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(3)
        def relay():
            try:
                downstream, _ = listener.accept()
                with downstream:
                    downstream.settimeout(3)
                    transferred = 0
                    while transferred < 4 * 1024 * 1024:
                        ready, _, _ = select.select([downstream, upstream], [], [], 3)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            transferred += len(data)
                            (upstream if source is downstream else downstream).sendall(data)
            except OSError:
                return
        threading.Thread(target=relay, daemon=True).start()
        with grpc.insecure_channel(f'127.0.0.1:{listener.getsockname()[1]}', options=[
                ('grpc.enable_http_proxy', 0), ('grpc.max_receive_message_length', 1024 * 1024)]) as channel:
            reflection = GrpcReflectionClient()
            reflection.load_protocols(channel, symbols=['SpaceX.API.Device.Device'])
            request_class = reflection.message_class('SpaceX.API.Device.Request')
            stub = reflection.service_stub_class('SpaceX.API.Device.Device')(channel)
            return rpc_request(stub, request_class, operation, cfg['paired_device_id'])


def invoke(operation):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_worker', operation],
                            capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


def control(operation, cfg=None, call=invoke):
    cfg = load_config() if cfg is None else cfg
    if operation == 'shutdown':
        return {'result': 'unsupported', 'message': 'Mini power-off requires the planned DC switching hardware; no stow or sleep substituted.'}
    if operation != 'reboot':
        raise ValueError('Unknown control')
    if not cfg['enabled'] or not cfg['allow_reboot'] or not cfg['paired_device_id']:
        return {'result': 'disabled', 'message': 'Mini reboot is not commissioned and paired.'}
    try:
        return call('reboot')
    except (OSError, ValueError, subprocess.SubprocessError):
        # No retries: a timeout after submission could mean the Mini is already restarting.
        return {'result': 'unconfirmed', 'message': 'Mini reboot not confirmed; not retried.'}


def follow(operation, cfg=None, call=invoke):
    cfg = load_config() if cfg is None else cfg
    key = 'follow_pcs_reboot' if operation == 'reboot' else 'follow_pcs_shutdown'
    if not cfg[key]:
        return {'result': 'disabled', 'message': 'Mini lifecycle following is not armed.'}
    return control(operation, cfg, call)


def sample(cfg, call=invoke):
    result = dict(configured=cfg['enabled'], available=False, status='ok',
                  state='UNAVAILABLE' if cfg['enabled'] else 'DISABLED')
    if cfg['enabled']:
        try:
            result.update(sanitize(call('status')))
            result['available'] = True
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return result | {'collected_at': time.time(), 'poll_seconds': cfg['poll_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['service', 'status', 'identify', 'control', 'follow', 'check-config', '_worker'])
    parser.add_argument('operation', nargs='?', choices=['status', 'identify', 'reboot', 'shutdown'])
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(cached_status()))
        return 0
    if args.command == 'check-config':
        load_config()
        print('Starlink configuration valid; shutdown is staged/unsupported.')
        return 0
    if os.geteuid() != 0:
        raise ValueError('Collector and controls require the installed root service/helper')
    if args.command == '_worker':
        print(json.dumps(worker(args.operation)))
        return 0
    if args.command == 'identify':
        print(json.dumps(invoke('identify')))
        return 0
    if args.command in {'control', 'follow'}:
        if args.operation not in {'reboot', 'shutdown'}:
            parser.error('Control requires reboot or shutdown')
        result = (control if args.command == 'control' else follow)(args.operation)
        print(json.dumps(result))
        return 0 if result['result'] == 'request_accepted' else 1
    from pcs_uplink_manager import atomic_json
    while True:
        try:
            cfg = load_config()
            atomic_json(CACHE, sample(cfg))
            time.sleep(cfg['poll_seconds'])
        except (OSError, ValueError, TypeError):
            atomic_json(CACHE, dict(configured=True, available=False, status='warn', state='UNAVAILABLE',
                                    collected_at=time.time(), poll_seconds=15))
            time.sleep(15)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        # Never log raw dish RPC responses, descriptors, location or identifiers.
        print('Starlink operation unavailable or configuration invalid.', file=sys.stderr)
        raise SystemExit(1)
