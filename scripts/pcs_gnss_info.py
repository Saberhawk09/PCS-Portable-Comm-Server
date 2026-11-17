#!/usr/bin/env python3
"""PCS GNSS information service and fixed privileged action boundary."""
import argparse
import contextlib
import importlib.util
import importlib.machinery
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time

from pcs_gnss import (NEO, UBXStream, atomic_json, load_config as gnss_config,
                      match_receiver, read_status, ubx)
from pcs_gnss_diagnostics import Diagnostics, EventHistory, SpectrumCapture, chrony_source_offsets, correction_summary
import pcs_assistnow as assist

RUNTIME = Path('/run/pcs-gnss-info')
SOCKET = RUNTIME / 'control.sock'
ALIAS = '/dev/pcs-gnss-neo'
MAINTENANCE = 'I authorize GNSS timing maintenance'


@contextlib.contextmanager
def lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def read_snapshot(path, max_age=10):
    value = assist.read_json(path, {})
    age = time.monotonic() - value.get('updated_monotonic', float('-inf'))
    if not 0 <= age <= max_age:
        value['state'] = 'Stale' if value else 'Unavailable'
        for message in value.get('messages', {}).values():
            message['state'] = 'Stale'
        if 'capture' in value:
            value['capture']['active'] = False
        if 'counts' in value:
            value['counts']['state'] = 'Stale'
    else:
        value['state'] = 'Fresh'
    return value


def status():
    value = read_snapshot(RUNTIME / 'status.json')
    value['corrections'] = correction_summary(value.get('messages', {}))
    value['timing'] = read_status()
    for reference in value.get('chrony_sources', {}).values():
        age = time.monotonic() - reference.get('collected_monotonic', float('-inf'))
        if age < 0:
            reference['state'] = 'Stale'
        else:
            reference['sample_age_seconds'] += age
            reference['state'] = 'Fresh' if reference['sample_age_seconds'] <= 5 else 'Stale'
    value['assistance'] = assist.read_json(assist.ROOT / 'public-status.json', {})
    for kind in ('predictive', 'live'):
        cache = value['assistance'].get(kind, {})
        if cache.get('state') == 'Available' and time.time() >= cache.get('expires_at', 0):
            cache['state'] = 'Expired'
    value['injection'] = assist.read_json(assist.ROOT / 'injection.json', {'state': 'Not attempted'})
    value['maintenance_request'] = assist.read_json(assist.ROOT / 'maintenance-request.json', {})
    import pcs_gnss_acquisition as acquisition
    facts = assist.read_json(assist.ROOT / 'receiver-facts.json', {}, private=True)
    try:
        value['acquisition'] = acquisition.public_status(identity=facts.get('identity'))
    except (ValueError,OSError,KeyError,TypeError):
        value['acquisition'] = {'job':{'state':'Unavailable','phase':'Private acquisition state needs operator review'}}
    return value


def control(request):
    raw = json.dumps(request).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(3)
        channel.connect(str(SOCKET))
        channel.sendall(raw + b'\n')
        reply = b''
        while b'\n' not in reply and len(reply) <= 4096:
            chunk = channel.recv(4096)
            if not chunk:
                break
            reply += chunk
    result = json.loads(reply)
    if result.get('error'):
        raise assist.AssistanceError(result['error'])
    return result


def save_assist_status(settings=None, credentials=None, usage=None):
    settings = assist.load_config() if settings is None else settings
    credentials = assist.load_credentials() if credentials is None else credentials
    usage = assist.read_json(assist.ROOT / 'usage.json', {}) if usage is None else usage
    value = assist.public_status(settings, credentials, usage, assist.Cache(), time.time())
    atomic_json(assist.ROOT / 'public-status.json', value)
    return value


def web_update(request):
    if not isinstance(request, dict):
        raise assist.AssistanceError('Invalid GNSS action')
    operation = request.get('operation')
    if operation in ('acquisition-start','acquisition-cancel','acquisition-recover','navigation-snapshot',
                     'navigation-restore','boot-policy','acquisition-export'):
        return acquisition_update(request)
    allowed = {'operation', 'seconds', 'confirm', 'enabled', 'window_days', 'refresh_hours',
               'predictive_requests_day', 'live_requests_day', 'requests_month', 'bytes_day'}
    if set(request) - allowed:
        raise assist.AssistanceError('Unknown GNSS action field')
    if operation in ('start-spectrum', 'stop-spectrum', 'snapshot'):
        return control({'operation': operation, 'seconds': int(request.get('seconds', 60))})
    if operation in ('autonomous-enable', 'autonomous-disable'):
        if request.get('confirm') != 'yes':
            raise assist.AssistanceError('Confirm receiver configuration maintenance request')
        with lock(assist.ROOT / 'download.lock'):
            atomic_json(assist.ROOT / 'maintenance-request.json', {'operation': operation, 'requested_at': time.time(),
                        'state': 'Pending separately authorized maintenance; receiver unchanged'})
        return {'state': 'Maintenance request saved; receiver unchanged'}
    if operation in ('refresh-predictive', 'refresh-live'):
        settings = assist.load_config()
        if operation == 'refresh-live' and (not settings['live_enabled'] or request.get('confirm') != 'yes'):
            raise assist.AssistanceError('Live Orbits requires entitlement, enablement and per-request cost confirmation')
        # Fixed units, no shell or arbitrary arguments; network runs out of band.
        unit = 'pcs-assistnow-live.service' if operation == 'refresh-live' else 'pcs-assistnow-manual.service'
        result = subprocess.run(['systemctl', 'start', '--no-block', unit], capture_output=True, timeout=3, check=False)
        if result.returncode:
            raise assist.AssistanceError('Assistance refresh could not be queued')
        return {'state': 'Refresh queued on existing uplink'}
    with lock(assist.ROOT / 'download.lock'):
        settings = assist.load_config()
        if operation == 'clear-expired':
            assist.Cache().prune(time.time())
        else:
            if request.get('confirm') != 'yes':
                raise assist.AssistanceError('Confirm assistance configuration change')
            if operation in ('automatic-refresh', 'allow-metered', 'boot-injection', 'live'):
                if request.get('enabled') not in ('yes', 'no'):
                    raise assist.AssistanceError('Explicit enable/disable choice required')
                name = {'automatic-refresh': 'automatic_refresh', 'allow-metered': 'allow_metered',
                        'boot-injection': 'boot_injection', 'live': 'live_enabled'}[operation]
                settings[name] = request['enabled'] == 'yes'
                if operation == 'live':
                    credentials = assist.load_credentials()
                    if settings[name] and (not assist.valid_credentials(credentials) or 'ulorb_l1' not in credentials.get('allowed_data', [])):
                        raise assist.AssistanceError('Verified Live Orbits entitlement unavailable')
                    settings['live_cost_authorized'] = settings[name]
            elif operation == 'policy':
                for name in ('window_days', 'refresh_hours', 'predictive_requests_day',
                             'live_requests_day', 'requests_month', 'bytes_day'):
                    settings[name] = int(request.get(name, settings[name]))
            else:
                raise assist.AssistanceError('Unsupported GNSS action')
            atomic_json(assist.CONFIG, assist.config(settings))
        save_assist_status(settings)
    return {'state': 'Settings saved'}


def serve():
    # Separate process failure cannot affect the authoritative UTC/PPS gate.
    if gnss_config()['mode'] != NEO:
        return
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    assist.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(RUNTIME / 'daemon.lock'):
        diagnostics, capture = Diagnostics(), SpectrumCapture()
        events = EventHistory(assist.read_json(assist.ROOT / 'rf-events.json', []))
        chrony_sources, last_chrony = {}, float('-inf')
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        SOCKET.unlink(missing_ok=True)
        listener.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        listener.listen(4)
        listener.setblocking(False)
        gpsd, json_feed, json_buffer, decoder, retry, last_write = None, None, b'', UBXStream(), 0, 0
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        def snapshot():
            value = diagnostics.snapshot()
            metadata = assist.read_json('/run/pcs-gnss/receiver-info.json', {})
            if not metadata:
                facts = assist.read_json(assist.ROOT / 'receiver-facts.json', {})
                metadata = dict(facts.get('identity', {}), verified_at=facts.get('timestamp'))
            value['receiver'] = {k: metadata[k] for k in ('model', 'firmware', 'hardware', 'protocol', 'baud', 'verified_at') if k in metadata}
            value.update(updated_monotonic=time.monotonic(), updated_at=time.time(),
                         capture=capture.status(), events=events.events,
                         chrony_sources=chrony_sources,
                         serial={'device': ALIAS, 'baud': gnss_config().get('baud'), 'owner': 'GPSD (read-only)'},
                         connection='Connected' if gpsd else 'Offline / Waiting for GPSD')
            return value
        try:
            while not stopping:
                now = time.monotonic()
                if now - last_chrony >= 5:
                    # Force monitoring over loopback, where Chrony forbids
                    # state-changing commands. No private command socket access.
                    try:
                        report = subprocess.run(['chronyc', '-h', '127.0.0.1', '-n', 'sources'],
                                                capture_output=True, text=True, timeout=1, check=False,
                                                env=dict(os.environ, LC_ALL='C'))
                        chrony_sources = chrony_source_offsets(report.stdout) if report.returncode == 0 else {}
                        for reference in chrony_sources.values():
                            reference['collected_monotonic'] = time.monotonic()
                    except (OSError, subprocess.SubprocessError):
                        chrony_sources = {}
                    last_chrony = now
                if gpsd is None and now >= retry:
                    retry = now + 5
                    try:
                        gpsd = socket.create_connection(('127.0.0.1', 2947), timeout=1)
                        gpsd.sendall(('?WATCH=' + json.dumps({'enable': True, 'raw': 2, 'device': ALIAS}) + ';\n').encode())
                        gpsd.setblocking(False)
                        json_feed = socket.create_connection(('127.0.0.1', 2947), timeout=1)
                        json_feed.sendall(('?WATCH=' + json.dumps({'enable': True, 'json': True, 'device': ALIAS}) + ';\n').encode())
                        json_feed.setblocking(False)
                        json_buffer = b''
                        decoder = UBXStream()
                    except OSError:
                        if gpsd:
                            gpsd.close()
                        if json_feed:
                            json_feed.close()
                        json_feed = None
                        gpsd = None
                ready, _, _ = select.select([listener] + ([gpsd, json_feed] if gpsd else []), [], [], 0.2)
                if json_feed in ready:
                    try:
                        raw = json_feed.recv(16384)
                        if not raw:
                            raise ConnectionError('GPSD JSON disconnected')
                        json_buffer = (json_buffer + raw)[-65536:]
                        while b'\n' in json_buffer:
                            line, json_buffer = json_buffer.split(b'\n', 1)
                            try:
                                report = json.loads(line)
                            except (ValueError, UnicodeError):
                                continue
                            if isinstance(report, dict) and report.get('class') == 'SKY' and report.get('device') == ALIAS:
                                diagnostics.update_sky(report)
                    except OSError:
                        json_feed.close()
                        gpsd.close()
                        gpsd, json_feed = None, None
                if gpsd in ready:
                    try:
                        data = gpsd.recv(16384)
                        if not data:
                            raise ConnectionError('GPSD disconnected')
                        for cls, msg, payload in decoder.feed(data):
                            updated = diagnostics.update(cls, msg, payload)
                            if updated:
                                name, value = updated
                                if name == 'security_log' and events.observe(value):
                                    atomic_json(assist.ROOT / 'rf-events.json', events.events)
                                if name == 'security' and events.observe_security(value):
                                    atomic_json(assist.ROOT / 'rf-events.json', events.events)
                                if name == 'spectrum':
                                    capture.observe(value, time.time())
                    except OSError:
                        gpsd.close()
                        if json_feed:
                            json_feed.close()
                        json_feed = None
                        gpsd = None
                if listener in ready:
                    channel, _ = listener.accept()
                    with channel:
                        channel.settimeout(0.5)
                        try:
                            raw = b''
                            while b'\n' not in raw and len(raw) < 4096:
                                part = channel.recv(4096)
                                if not part:
                                    break
                                raw += part
                            request = json.loads(raw)
                            operation = request.get('operation')
                            if operation == 'start-spectrum':
                                fresh = diagnostics.snapshot()['messages']['spectrum']['state'] == 'Fresh'
                                capture.start(request.get('seconds'), fresh)
                            elif operation == 'stop-spectrum':
                                capture.stop()
                            elif operation == 'snapshot':
                                value = snapshot()
                                if value['messages']['spectrum']['state'] != 'Fresh':
                                    raise ValueError('Fresh spectrum unavailable')
                                if len(json.dumps(value).encode()) > 1024 * 1024:
                                    raise ValueError('Diagnostic snapshot exceeds one MiB limit')
                                snapshots = sorted(assist.ROOT.glob('spectrum-[0-9]*.json'))
                                atomic_json(assist.ROOT / ('spectrum-' + str(time.time_ns()) + '.json'), value)
                                for old in snapshots[:-9]:
                                    old.unlink()
                            else:
                                raise ValueError('Unsupported diagnostic action')
                            reply = {'state': 'Applied'}
                        except (ValueError, OSError, TypeError):
                            reply = {'error': 'Diagnostic action unavailable; check freshness, duration and capture state'}
                        try:
                            channel.sendall(json.dumps(reply).encode() + b'\n')
                        except OSError:
                            pass
                if now - last_write >= 2:
                    atomic_json(RUNTIME / 'status.json', snapshot())
                    last_write = now
        finally:
            capture.stop()
            atomic_json(RUNTIME / 'status.json', snapshot())
            if gpsd:
                gpsd.close()
            if json_feed:
                json_feed.close()
            listener.close()
            SOCKET.unlink(missing_ok=True)


def selected_network():
    value = assist.network(assist.read_json('/run/pcs-dashboard/public.json', {}))
    if not value['online']:
        return value
    # Metering is a read-only property of the already-selected NetworkManager
    # device. Never infer an unmetered data plan from Wi-Fi/Ethernet alone.
    runtime = assist.read_json('/run/pcs-uplink-manager/status.json', {})
    if not 0 <= time.time() - runtime.get('generated_at', 0) <= 45:
        return value
    rows = [row for row in runtime.get('uplinks', []) if isinstance(row, dict)
            and row.get('id') == runtime.get('active_id') and row.get('active') is True and row.get('internet') is True]
    if len(rows) != 1:
        return value
    interface = rows[0].get('interface', '')
    if not isinstance(interface, str) or not re.fullmatch('[A-Za-z0-9_.-]{1,15}', interface) or interface in ('lo', 'eth0', 'eth0.10'):
        return value
    try:
        result = subprocess.run(['nmcli', '--get-values', 'GENERAL.METERED', 'device', 'show', interface],
                                capture_output=True, text=True, timeout=2, check=False,
                                env=dict(os.environ, LC_ALL='C'))
        if result.returncode == 0 and result.stdout.strip().lower() == 'no':
            value['metered'] = False
    except (OSError, subprocess.SubprocessError):
        pass
    return value


def refresh(kind='predictive', force=False):
    import pcs_gnss_acquisition as acquisition
    if acquisition.Jobs().read().get('state') in ('Queued','Running','Recovering','Recovery required'):
        return  # No external download during an explicitly unassisted experiment.
    assist.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(assist.ROOT / 'download.lock'):
        settings = assist.load_config()
        credentials = assist.load_credentials()
        usage = assist.read_json(assist.ROOT / 'usage.json', {})
        if force or settings['automatic_refresh']:
            uplink = selected_network()
            usage = assist.Downloader(assist.Cache()).refresh(settings, credentials, uplink, usage, kind, force)
            atomic_json(assist.ROOT / 'usage.json', usage)
        save_assist_status(settings, credentials, usage)


def maintenance(operation, confirm, token_file=None):
    if confirm != MAINTENANCE:
        raise assist.AssistanceError('Explicit maintenance confirmation required')
    if operation == 'enroll':
        if token_file is None:
            raise assist.AssistanceError('Root-only token file required')
        facts = assist.read_json(assist.ROOT / 'receiver-facts.json', {}, private=True)
        if not 0 <= time.time() - facts.get('timestamp', 0) < 86400:
            raise assist.AssistanceError('Fresh verified receiver identity required; run maintenance-inspect')
        token = assist.read_json(token_file, {}, private=True).get('token')
        credentials = assist.enroll(token, bytes.fromhex(facts['version_frame']), bytes.fromhex(facts['unique_frame']))
        atomic_json(assist.CREDENTIAL, credentials)
        save_assist_status()
        return {'state': 'Enrolled; Live Orbits remains operator controlled'}
    # Import existing serial ownership/admission implementation, never invent a
    # second background writer. Check services before opening the enrolled alias.
    for unit in ('gpsd.service', 'gpsd.socket', 'pcs-gnss.service'):
        result = subprocess.run(['systemctl', 'show', '--property=ActiveState', '--value', unit],
                                capture_output=True, text=True, timeout=3, check=False)
        if result.returncode or result.stdout.strip() not in ('inactive', 'failed'):
            raise assist.AssistanceError('Maintenance denied: GPSD/timing ownership must be released by operator')
    candidate = Path(__file__).with_name('pcs-gnss.py')
    if not candidate.exists():
        candidate = Path('/usr/local/sbin/pcs-gnss')
    spec = importlib.util.spec_from_loader('pcs_gnss_admission', importlib.machinery.SourceFileLoader('pcs_gnss_admission', str(candidate)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import pcs_gnss_manager as manager
    with lock(Path('/run/pcs-gnss/installation.lock')):
        config = gnss_config()
        device = match_receiver(module.usb_devices(), config['receiver'])['device']
        if config['mode'] != NEO or os.stat(device).st_rdev != os.stat(ALIAS).st_rdev or config.get('baud') != 115200:
            raise assist.AssistanceError('Commissioned enrolled receiver required')
        with module.identified_receiver(ALIAS, 115200, fallback=False) as (fd, identity, before, baud):
            exchange = lambda request, cls, msg, ack=None: module.exchange(fd, request, cls, msg, ack)
            version = exchange(ubx(10, 4), 10, 4)
            unique = exchange(ubx(0x27, 3), 0x27, 3)
            identity = assist.receiver_identity(ubx(10, 4, version), ubx(0x27, 3, unique))
            manager.restore_pending(exchange, binding=identity['binding'])
            if operation == 'maintenance-inspect':
                atomic_json(assist.ROOT / 'receiver-facts.json', {'timestamp': time.time(), 'identity': identity,
                            'version_frame': ubx(10, 4, version).hex(), 'unique_frame': ubx(0x27, 3, unique).hex()})
                settings = manager.read_values(exchange, [manager.ANA, manager.ITFM])
                atomic_json(assist.ROOT / 'receiver-features.json', {'timestamp': time.time(),
                            'autonomous': bool(settings[manager.ANA]), 'itfm': bool(settings[manager.ITFM])})
                return {'state': 'Identity and RAM configuration readback verified'}
            if operation == 'maintenance-apply':
                request = assist.read_json(assist.ROOT / 'maintenance-request.json', {})
                name = request.get('operation')
                if name not in ('autonomous-enable', 'autonomous-disable'):
                    raise assist.AssistanceError('No approved Autonomous request')
                result = manager.set_feature(exchange, manager.ANA, name == 'autonomous-enable', True, binding=identity['binding'])
                atomic_json(assist.ROOT / 'maintenance-request.json', dict(request, state='Applied to RAM; readback verified'))
                return result
    raise assist.AssistanceError('Unsupported maintenance operation')


def acquisition_update(request):
    import pcs_gnss_acquisition as acq
    operation=request.get('operation')
    if operation=='acquisition-export':
        if set(request)-{'operation','id','format'}:
            raise assist.AssistanceError('Unknown export field')
        ident=request.get('id','')
        if not re.fullmatch('[a-f0-9]{32}',ident):
            raise assist.AssistanceError('Invalid report identifier')
        value=acq.private_json(acq.ROOT/'history'/(ident+'.json'),{})
        if value.get('id')!=ident:
            raise assist.AssistanceError('Report unavailable')
        return {'report':acq.result_export(value,request.get('format','json'))}
    with lock(acq.ROOT/'queue.lock'):
        jobs=acq.Jobs()
        if operation in ('acquisition-cancel','acquisition-recover'):
            if set(request)-{'operation'}:
                raise assist.AssistanceError('Unknown cancellation field')
            jobs.cancel()
            unit='pcs-gnss-acquisition.service' if jobs.read().get('state') in ('Running','Queued') else 'pcs-gnss-acquisition-recover.service'
            verb='stop' if unit=='pcs-gnss-acquisition.service' else 'start'
            result=subprocess.run(['systemctl',verb,'--no-block',unit],capture_output=True,timeout=3)
        elif operation=='boot-policy':
            if jobs.read().get('state') in ('Queued','Running','Recovering','Recovery required'):
                raise assist.AssistanceError('Finish or recover maintenance before changing normal policy')
            allowed={'operation','confirm','boot_policy','fallback','save_on_shutdown','restore_next_start',
                     'database_max_age_hours','backup_retention','power_hold_seconds','shutdown_budget_seconds'}
            if set(request)-allowed or request.get('confirm')!='yes':
                raise assist.AssistanceError('Confirm boot policy change')
            with lock(Path('/run/pcs-gnss/installation.lock')):
                cfg=acq.load_policy()
                for name in allowed-{'operation','confirm'}:
                    if name in request:
                        cfg[name]=(request[name]=='yes') if name in ('save_on_shutdown','restore_next_start') else (request[name] if name in ('boot_policy','fallback') else int(request[name]))
                cfg=acq.policy(cfg)
                bound=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True).get('identity')
                if (cfg['save_on_shutdown'] and not acq.qualified(acq.ROOT,bound,'shutdown') or
                    (cfg['restore_next_start'] or cfg['boot_policy']=='database') and not acq.qualified(acq.ROOT,bound,'database_restore')):
                    raise assist.AssistanceError('Database automation requires exact-receiver hardware qualification')
                if cfg['boot_policy']=='live':
                    settings=assist.load_config()
                    credentials=assist.load_credentials()
                    if not (settings['live_enabled'] and settings['live_cost_authorized'] and assist.valid_credentials(credentials)
                            and 'ulorb_l1' in credentials.get('allowed_data',[])):
                        raise assist.AssistanceError('Live entitlement and explicit cost activation required')
                atomic_json(acq.CONFIG,cfg)
            return {'state':'Boot policy saved; no receiver reset or immediate injection'}
        else:
            kind={'acquisition-start':'benchmark','navigation-snapshot':'snapshot','navigation-restore':'restore'}[operation]
            if kind!='benchmark' and set(request)-{'operation','confirm'}:
                raise assist.AssistanceError('Unknown navigation maintenance field')
            if request.get('confirm')!=acq.CONFIRM:
                raise assist.AssistanceError('Explicit timing-interruption confirmation required')
            if kind=='benchmark':
                acq.trials(request)
            active=subprocess.run(['systemctl','show','pcs-gnss-acquisition.service','-p','ActiveState','--value'],
                                  capture_output=True,text=True,timeout=3)
            if active.returncode or active.stdout.strip() not in ('inactive','failed'):
                raise assist.AssistanceError('Previous acquisition service has not finished cleanup')
            jobs.queue(request,kind)
            result=subprocess.run(['systemctl','start','--no-block','pcs-gnss-acquisition.service'],capture_output=True,timeout=3)
            if result.returncode:
                value=jobs.read()
                value.update(state='Failed',phase='Service queue rejected')
                jobs.finish(value)
        if result.returncode:
            raise assist.AssistanceError('Acquisition service action unavailable')
    return {'state':'Acquisition operation queued'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('serve', 'web-status', 'web-update', 'refresh', 'refresh-manual',
                        'refresh-live', 'maintenance-inspect', 'maintenance-apply', 'enroll',
                        'acquisition-worker','acquisition-recover','navigation-shutdown','boot-refresh',
                        'qualification-record','qualification-arm-shutdown','record-owner'))
    parser.add_argument('--maintenance-confirm')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--evidence-file',type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise assist.AssistanceError('Root required')
    assist.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.action == 'serve':
        serve()
        return
    if args.action=='record-owner':
        from pcs_gnss_runtime import record_owner
        record_owner()
        return
    if args.action.startswith('qualification-'):
        import pcs_gnss_maintenance as maintenance_ops
        if args.evidence_file is None:
            raise assist.AssistanceError('Private hardware evidence file required')
        print(json.dumps(maintenance_ops.qualify(args.evidence_file,args.maintenance_confirm,
                                               args.action=='qualification-arm-shutdown')))
        return
    if args.action in ('acquisition-worker','acquisition-recover','navigation-shutdown','boot-refresh'):
        import pcs_gnss_maintenance as maintenance_ops
        {'acquisition-worker':maintenance_ops.worker,'acquisition-recover':maintenance_ops.recover_job,
         'navigation-shutdown':maintenance_ops.shutdown_backup,'boot-refresh':maintenance_ops.boot_refresh}[args.action]()
        return
    if args.action == 'web-status':
        result = status()
    elif args.action == 'web-update':
        result = web_update(json.loads(sys.stdin.read(8193)))
    elif args.action.startswith('refresh'):
        refresh('live' if args.action == 'refresh-live' else 'predictive', args.action != 'refresh')
        result = {'state': 'Complete'}
    else:
        result = maintenance(args.action, args.maintenance_confirm, args.token_file)
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        # Exception objects may contain credentials or private receiver frames.
        print('GNSS operation unavailable; inspect private state and operator guide.', file=sys.stderr)
        sys.exit(1)
