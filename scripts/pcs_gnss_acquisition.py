#!/usr/bin/env python3
"""PCS acquisition policy, private navigation archives and benchmark state.

No hardware is opened by this module. Receiver I/O is supplied by the single
exclusive owner. DBD payloads are opaque, firmware-specific and never exported.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import struct
import time
import uuid

import pcs_assistnow as assist
from pcs_gnss import atomic_json, ubx
from pcs_gnss_diagnostics import decode, correction_summary

ROOT = Path('/var/lib/pcs-gnss-acquisition')
CONFIG = Path('/etc/pcs/gnss-acquisition.json')
CONFIRM = 'I understand GNSS positioning and PPS timing will be interrupted'
MASKS = {'hot': 0x0000, 'warm': 0x0001, 'cold': 0xffff}
SOURCES = ('none', 'cached', 'internet', 'database', 'live', 'automatic')
POLICIES = ('automatic', 'retained', 'database', 'cached', 'internet', 'live', 'none')
FALLBACKS = ('local', 'retained', 'none')
COMPARISONS = {'cold-unassisted-predictive': ('cold', 'none', 'cached'),
               'cold-cached-internet': ('cold', 'cached', 'internet'),
               'warm-unassisted-assisted': ('warm', 'none', 'cached'),
               'hot-unassisted-assisted': ('hot', 'none', 'cached'),
               'database-predictive': ('cold', 'database', 'cached')}
DEFAULT = {'version': 1, 'boot_policy': 'automatic', 'fallback': 'local',
           'save_on_shutdown': False, 'restore_next_start': False,
           'database_max_age_hours': 24, 'backup_retention': 5,
           'shutdown_budget_seconds': 30, 'power_hold_seconds': 0}
METRICS = ('receiver_ttff_seconds', 'first_2d_seconds', 'first_3d_seconds',
           'utc_seconds', 'pps_seconds', 'chrony_sync_seconds', 'f10p_seconds')


def private_json(path, default=None):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise assist.AssistanceError('Acquisition metadata contains a symlink')
    if path.exists():
        st = path.lstat()
        if path.is_symlink() or not path.is_file() or st.st_size > 2*1024*1024:
            raise assist.AssistanceError('Invalid acquisition metadata')
        if os.name == 'posix' and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            raise assist.AssistanceError('Acquisition state must be private to its owner')
    return assist.read_json(path, default)


def directory(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise assist.AssistanceError('Acquisition storage contains a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix' and (path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
        raise assist.AssistanceError('Acquisition directory is not private')
    return path


def policy(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise assist.AssistanceError('Unknown boot policy field')
    result = DEFAULT | value
    if result['version'] != 1 or result['boot_policy'] not in POLICIES or result['fallback'] not in FALLBACKS:
        raise assist.AssistanceError('Unsupported boot or fallback policy')
    for key in ('save_on_shutdown', 'restore_next_start'):
        if type(result[key]) is not bool:
            raise assist.AssistanceError('Explicit boolean policy required')
    for key, low, high in (('database_max_age_hours', 1, 168), ('backup_retention', 1, 10),
                           ('shutdown_budget_seconds', 10, 30), ('power_hold_seconds', 0, 600)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise assist.AssistanceError('Acquisition policy outside bounds')
    if result['save_on_shutdown'] and result['power_hold_seconds'] < result['shutdown_budget_seconds'] + 60:
        raise assist.AssistanceError('Shutdown saving requires a verified power-hold budget')
    return result


def load_policy(path=None):
    return policy(private_json(CONFIG if path is None else path, {}))


def qualified(root, identity, capability):
    value = private_json(Path(root)/'qualification.json', {})
    return value.get('identity') == identity and value.get(capability) is True


def reset_frame(mode):
    if mode not in MASKS:
        raise assist.AssistanceError('Unknown reset mode')
    # GNSS-only controlled software reset preserves processor/configuration RAM.
    return ubx(6, 4, struct.pack('<HBB', MASKS[mode], 2, 0))


def control_frame(start):
    return ubx(6, 4, struct.pack('<HBB', 0, 9 if start else 8, 0))


def dbd_frames(raw):
    if not raw or len(raw) > 1024*1024:
        raise assist.AssistanceError('Database size outside bounds')
    result = assist.frames(raw)
    if not 1 <= len(result) <= 8192:
        raise assist.AssistanceError('Database entry count outside bounds')
    if any(f[2:4] != b'\x13\x80' or not 12 <= len(f)-8 <= 164 for f in result):
        raise assist.AssistanceError('Invalid database frame type or length')
    return result


def dbd_completion(payload, count):
    return (len(payload) == 8 and payload[:4] == b'\x01\x00\x00\x80'
            and int.from_bytes(payload[4:8], 'little') == count and count > 0)


class NavigationStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)/'navigation'

    def publish(self, raw, completion, identity, utc, retained=None, retention=5):
        entries = dbd_frames(raw)
        if not dbd_completion(completion, len(entries)) or not utc.get('valid'):
            raise assist.AssistanceError('Incomplete database or independent date unavailable')
        if not 1 <= retention <= 10:
            raise assist.AssistanceError('Invalid backup retention')
        root = directory(self.root)
        objects=list(root.iterdir())
        if (len(objects)>64 or any(p.is_symlink() or not p.is_file() for p in objects)
                or sum(p.stat().st_size for p in objects)+len(raw)+4096>12*1024*1024):
            raise assist.AssistanceError('Navigation storage limit reached; last valid backup retained')
        digest, ident = hashlib.sha256(raw).hexdigest(), uuid.uuid4().hex
        meta = {'version': 1, 'id': ident, 'identity': identity, 'created_at': utc['utc'],
                'time_reference': utc['source'], 'sha256': digest, 'bytes': len(raw),
                'frame_count': len(entries), 'completion_ack': completion.hex(), 'retained': retained or {}}
        # Immutable object then metadata then pointer: interruption keeps last good.
        assist.atomic_bytes(root/(ident+'.ubx'), raw)
        atomic_json(root/(ident+'.json'), meta)
        atomic_json(root/'latest.json', {'id': ident})
        older = sorted((p for p in root.glob('*.json') if re.fullmatch('[a-f0-9]{32}', p.stem)),
                       key=lambda p:p.stat().st_mtime_ns, reverse=True)
        keep = {ident} | {p.stem for p in [p for p in older if p.stem != ident][:retention-1]}
        # Never remove the committed pointer; interrupted orphan objects are bounded
        # at the next successful maintenance save, not during normal HTTP reads.
        for p in root.iterdir():
            if re.fullmatch('[a-f0-9]{32}', p.stem) and p.stem not in keep:
                p.unlink()
        return meta

    def read(self, identity, now, max_age_hours=24):
        pointer = private_json(self.root/'latest.json', {})
        ident = pointer.get('id', '')
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise assist.AssistanceError('Navigation backup missing')
        meta = private_json(self.root/(ident+'.json'), {})
        if meta.get('version') != 1 or meta.get('id') != ident or meta.get('identity') != identity:
            raise assist.AssistanceError('Database receiver or firmware mismatch')
        stamp = meta.get('created_at')
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now-stamp <= max_age_hours*3600:
            raise assist.AssistanceError('Database expired or time uncertain')
        p = self.root/(ident+'.ubx')
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1024*1024:
            raise assist.AssistanceError('Invalid database object')
        if os.name == 'posix' and (p.stat().st_uid != os.geteuid() or p.stat().st_mode & 0o077):
            raise assist.AssistanceError('Database object is not private')
        raw = p.read_bytes()
        entries = dbd_frames(raw)
        try:
            complete = bytes.fromhex(meta.get('completion_ack', ''))
        except (ValueError, TypeError):
            raise assist.AssistanceError('Invalid database completion metadata') from None
        if (meta.get('sha256') != hashlib.sha256(raw).hexdigest() or meta.get('bytes') != len(raw)
                or meta.get('frame_count') != len(entries) or not dbd_completion(complete, len(entries))):
            raise assist.AssistanceError('Database integrity or completeness mismatch')
        return entries, meta

    def status(self, identity=None, now=None, max_age_hours=24):
        now = time.time() if now is None else now
        try:
            if identity is None:
                return {'state': 'Identity unverified'}
            _, m = self.read(identity, now, max_age_hours)
            return {'state': 'Available', **{k:m[k] for k in ('created_at','bytes','frame_count')}, 'age_seconds': now-m['created_at']}
        except ValueError as exc:
            message = str(exc)
            state = ('Missing' if message == 'Navigation backup missing' else
                     'Incompatible' if message == 'Database receiver or firmware mismatch' else
                     'Expired or time uncertain' if message == 'Database expired or time uncertain' else 'Invalid')
            return {'state': state}
        except OSError:
            return {'state': 'Unavailable'}


def independent_time(now, synchronized=False, reference='', rtc=None, uncertainty=None):
    """An acquisition date hint never changes Chrony's receiver UTC gate."""
    good = type(now) in (int, float) and math.isfinite(now) and 1704067200 <= now <= 4102444800
    # Never relabel GNSS-derived starting time as an independent reference.
    network = synchronized is True and reference not in ('F10N','F10P','',None) and good
    rtc_good = (isinstance(rtc, dict) and rtc.get('validated') is True and rtc.get('device') == 'ds1307'
                and type(rtc.get('age_seconds')) in (int,float) and 0 <= rtc['age_seconds'] <= 60
                and type(rtc.get('utc')) in (int,float) and abs(rtc['utc']-now) <= 2)
    seconds = uncertainty if type(uncertainty) in (int,float) and math.isfinite(uncertainty) else None
    return {'valid': bool(good and (network or rtc_good)), 'utc': now if good else None,
            'source': 'Independent Internet NTP' if network else 'Validated DS1307 RTC' if rtc_good else 'No independent UTC reference',
            'uncertainty_seconds': max(1, seconds) if seconds is not None else None,
            'observed_monotonic':time.monotonic()}


def time_frame(hint, monotonic=None):
    """Protocol 40 MGA-INI-TIME_UTC, on receipt, unknown leap seconds.

    Coarse acquisition hint only. trustedSource stays zero; neither the hint nor
    receiver acceptance bypasses PCS's independent UTC freshness/validity gate.
    """
    now=time.monotonic() if monotonic is None else monotonic
    age=now-hint.get('observed_monotonic',float('inf'))
    uncertainty=hint.get('uncertainty_seconds')
    if (not hint.get('valid') or type(uncertainty) not in (int,float) or not math.isfinite(uncertainty)
            or not 0<=age<=600 or not 0<=uncertainty<=600):
        raise assist.AssistanceError('Fresh independently bounded time hint required')
    stamp=hint['utc']+age
    utc=dt.datetime.fromtimestamp(stamp,dt.timezone.utc)
    raw=bytearray(24)
    raw[0],raw[3]=0x10,0x80
    struct.pack_into('<H',raw,4,utc.year)
    raw[6:11]=bytes((utc.month,utc.day,utc.hour,utc.minute,utc.second))
    struct.pack_into('<I',raw,12,int((stamp-math.floor(stamp))*1e9))
    struct.pack_into('<H',raw,16,math.ceil(uncertainty+2+age*0.0001))
    return ubx(0x13,0x40,raw)


def trials(request):
    if not isinstance(request, dict) or request.get('confirm') != CONFIRM:
        raise assist.AssistanceError('Explicit timing-interruption confirmation required')
    permitted = {'operation','confirm','start','source','comparison','repetitions','timeout_seconds','fallback','antenna','paid_consent'}
    if set(request)-permitted:
        raise assist.AssistanceError('Unknown acquisition field')
    repetitions = int(request.get('repetitions', 1))
    timeout = int(request.get('timeout_seconds', 180))
    fallback = request.get('fallback', 'none')
    if not 1 <= repetitions <= 5 or not 15 <= timeout <= 300 or fallback not in FALLBACKS:
        raise assist.AssistanceError('Trial bounds or fallback invalid')
    comparison = request.get('comparison', '')
    if comparison:
        if comparison not in COMPARISONS:
            raise assist.AssistanceError('Unknown comparison')
        mode, a, b = COMPARISONS[comparison]
        sequence = [source for n in range(repetitions) for source in ((a,b) if n % 2 == 0 else (b,a))]
    else:
        mode, source = request.get('start', 'cold'), request.get('source', 'none')
        sequence = [source]*repetitions
    if mode not in MASKS or any(s not in SOURCES for s in sequence):
        raise assist.AssistanceError('Unknown start or assistance source')
    if mode!='cold' and 'database' in sequence:
        raise assist.AssistanceError('Saved receiver database restoration requires an explicitly confirmed software cold test')
    return [{'start':mode, 'source':s, 'fallback':fallback, 'timeout_seconds':timeout,
             'trial':n+1, 'paid_consent':request.get('paid_consent') == 'yes'} for n,s in enumerate(sequence)]


def summary(rows):
    result = {}
    for source in sorted({r.get('requested_source') or 'unknown' for r in rows}):
        group = [r for r in rows if (r.get('requested_source') or 'unknown') == source]
        metrics = {}
        for key in METRICS:
            values = [r['metrics'][key] for r in group if r.get('state') == 'Completed' and r.get('selected_source') == source and
                      type(r.get('metrics',{}).get(key)) in (int,float) and math.isfinite(r['metrics'][key])]
            metrics[key] = {'samples':len(values), 'median':statistics.median(values) if values else None,
                            'fastest':min(values) if values else None, 'slowest':max(values) if values else None}
        result[source] = {'trials':len(group), 'failures':sum(r.get('state') in ('Failed','Cancelled','Interrupted') for r in group),
                          'in_progress':sum(r.get('state') in ('Queued','Running','Recovering') for r in group), 'metrics':metrics}
    improvements = []
    names = list(dict.fromkeys(r.get('requested_source') or 'unknown' for r in rows))
    for index,a in enumerate(names):
        for b in names[index+1:]:
            va, vb = result[a]['metrics']['receiver_ttff_seconds']['median'], result[b]['metrics']['receiver_ttff_seconds']['median']
            if va is not None and vb is not None:
                improvements.append({'baseline':a,'comparison':b,'median_difference_seconds':va-vb,
                                     'improvement_percent':100*(va-vb)/va if va else None,
                                     'equivalence':'Not established: retained data, observation order and RF conditions must be reviewed'})
    return {'sources':result, 'comparisons':improvements}


class TrialMetrics:
    def __init__(self, started, independent, clock=time.monotonic):
        self.started, self.clock, self.independent = started, clock, independent
        self.values = dict.fromkeys(METRICS)
        self.messages, self.first_fix = {}, None
        self.after_reset_seen = False
        self.pps_baseline = None
        self.first_fix_observed = None

    def event(self, cls, msg, payload):
        if (cls,msg) not in ((1,7),(1,3),(1,4),(1,0x32),(1,0x35),(1,0x43)):
            return
        try:
            value = decode(cls,msg,payload)
        except (ValueError, IndexError, struct.error):
            return
        elapsed = max(0, self.clock()-self.started)
        name = {(1,7):'navigation',(1,3):'acquisition',(1,4):'dop',(1,0x32):'sbas',(1,0x35):'satellites',(1,0x43):'signals'}[(cls,msg)]
        self.messages[name] = {'state':'Fresh','data':value,'observed':self.clock()}
        if msg == 3:
            # A low msss proves a new GNSS session; old TTFF must not be replayed.
            self.after_reset_seen |= value['receiver_uptime_ms'] <= elapsed*1000 + 3000
            if self.after_reset_seen and value.get('fix_valid') and value.get('ttff_seconds') is not None:
                self.values['receiver_ttff_seconds'] = value['ttff_seconds']
        if msg == 7 and self.after_reset_seen:
            if value.get('utc_valid') and self.values['utc_seconds'] is None:
                self.values['utc_seconds'] = elapsed
            if value.get('fix_valid') and value['fix_type'] in ('2D','3D'):
                if self.values['first_2d_seconds'] is None:
                    self.values['first_2d_seconds'] = elapsed
                    self.first_fix_observed = self.clock()
                    self.first_fix = {'fix_type':value['fix_type'], 'satellites_used':payload[23],
                        'horizontal_accuracy_m':value.get('horizontal_accuracy_m'), 'pdop':value.get('pdop'),
                        'differential_applied':value.get('differential_applied'), 'correction_source':'Unknown'}
                if value['fix_type'] == '3D' and self.values['first_3d_seconds'] is None:
                    self.values['first_3d_seconds'] = elapsed
        # Join only current epochs. An adjacent first-fix SBAS report can complete
        # source identification without inventing it from an earlier fix.
        if self.first_fix and self.clock()-self.first_fix_observed <= 2 and self.first_fix['correction_source'] == 'Unknown':
            messages = {k:dict(v,state='Fresh' if self.clock()-v['observed'] <= 2 else 'Stale') for k,v in self.messages.items()}
            source = correction_summary(messages)
            if source['source'] != 'Unknown':
                self.first_fix['correction_source'] = source['source']

    def timing(self, state, pps_sequence):
        elapsed = max(0,self.clock()-self.started)
        if self.pps_baseline is None:
            self.pps_baseline = pps_sequence
        elif pps_sequence is not None and pps_sequence != self.pps_baseline and self.values['pps_seconds'] is None:
            self.values['pps_seconds'] = elapsed
        if str(state.get('state','')).lower() != 'fresh':
            return
        if state.get('synchronized') and self.values['chrony_sync_seconds'] is None:
            self.values['chrony_sync_seconds'] = elapsed
        if (state.get('synchronized') and state.get('selected_ref') == 'F10P' and state.get('utc_valid') is True
                and state.get('pps_selection') == '*'
                and state.get('pps_active') is True and state.get('pps_validation') == 'paired' and self.values['f10p_seconds'] is None):
            self.values['f10p_seconds'] = elapsed


def result_export(job, kind='json'):
    """Fixed schema: no raw dumps, exact identity, location or user annotations."""
    safe_rows = []
    for row in job.get('results', [])[-10:]:
        safe = {k:row.get(k) for k in ('trial','start','requested_source','selected_source','state','reset_mask',
            'reset_mode','reset_at','download_seconds','injection_seconds','accepted','rejected','offered','unacknowledged','metrics','first_fix',
            'residual_data','recovery','failure','time_reference','comparability','receiver_verified','firmware','protocol')}
        safe['time_aiding']=row.get('time_aiding') if isinstance(row.get('time_aiding'),str) else None
        for key in list(safe):
            if key not in ('metrics','first_fix') and type(safe[key]) not in (str,int,float,bool,type(None)):
                safe[key]=None
        safe['metrics']={k:v for k,v in (row.get('metrics') or {}).items() if k in METRICS and
                         (v is None or type(v) in (int,float) and math.isfinite(v) and v>=0)}
        safe['first_fix']={k:v for k,v in (row.get('first_fix') or {}).items()
                           if k in ('fix_type','satellites_used','horizontal_accuracy_m','pdop','differential_applied','correction_source')
                           and type(v) in (str,int,float,bool,type(None))}
        safe_rows.append(safe)
    value = {'id':job.get('id'), 'state':job.get('state'), 'created_at':job.get('created_at'),
             'kind':job.get('kind') if job.get('kind') in ('benchmark','snapshot','restore') else None,
             'failure':job.get('failure') if isinstance(job.get('failure'),str) else None,
             'results':safe_rows, 'summary':summary(safe_rows), 'label':'Software GNSS starts; not electrical power-cycle tests'}
    value.update(failure_code=job.get('failure_code') if job.get('failure_code') in
                 ('database-rejected','database-ack-mismatch','database-no-ack','database-deadline') else None,
                 failure_phase=job.get('failure_phase') if isinstance(job.get('failure_phase'),str) else None)
    for key in ('ownership_recovered','timing_recovered'):
        value[key]=job.get(key) if type(job.get(key)) is bool else None
    database=job.get('database_evidence') or {}
    if not isinstance(database,dict):
        database={}
    value['database_evidence']={key:database[key] for key in ('frame_count','offered','accepted','rejected','unacknowledged','failed_frame')
        if type(database.get(key)) is int and 0<=database[key]<=8192}
    if type(database.get('receiver_code')) is int and 0<=database['receiver_code']<=255:
        value['database_evidence']['receiver_code']=database['receiver_code']
    if type(database.get('complete')) is bool:
        value['database_evidence']['complete']=database['complete']
    recovery=job.get('navigation_recovery')
    value['navigation_recovery']={key:recovery[key] for key in ('utc_seconds','pps_seconds','stable_pps_seconds','f10p_seconds')
        if type(recovery.get(key)) in (int,float) and math.isfinite(recovery[key]) and 0<=recovery[key]<=300} if isinstance(recovery,dict) else {}
    if kind == 'json':
        return json.dumps(value, indent=2, allow_nan=False)
    if kind == 'csv':
        out = io.StringIO(newline='')
        extra=('operation','failure_code','failure_phase','ownership_recovered','timing_recovered',
               'database_frame_count','database_accepted','database_rejected','database_offered','database_unacknowledged','failed_frame','receiver_code',
               'recovery_utc_seconds','recovery_pps_seconds','recovery_stable_pps_seconds','recovery_f10p_seconds')
        columns = ('trial','start','requested_source','selected_source','state','download_seconds','injection_seconds','accepted','rejected','offered','unacknowledged') + METRICS + extra
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for row in safe_rows or ([{'state':value['state']}] if value['kind'] in ('snapshot','restore') else []):
            record={key:(row.get('metrics') or {}).get(key) if key in METRICS else row.get(key) for key in columns}
            record.update(operation=value['kind'],**{key:value[key] for key in extra if key in value})
            record.update({key:value['database_evidence'].get(key.removeprefix('database_')) for key in extra
                           if key.startswith('database_') or key in ('receiver_code','failed_frame')})
            record.update({'recovery_'+key:entry for key,entry in value['navigation_recovery'].items()})
            writer.writerow(record)
        return out.getvalue()
    if kind != 'text':
        raise assist.AssistanceError('Unknown report format')
    return ('PCS GNSS acquisition report\nSoftware starts, not electrical power cycles.\n' +
            f"Operation: {value['kind']}; State: {value['state']}\nFailure: {value['failure']}; Phase: {value['failure_phase']}\n" +
            f"UART ownership recovered: {value['ownership_recovered']}; Paired timing observed at completion: {value['timing_recovered']}\n" +
            'Database transfer evidence: '+json.dumps(value['database_evidence'],allow_nan=False)+'\n' +
            'Recovery seconds after handover: '+json.dumps(value['navigation_recovery'],allow_nan=False)+'\n' +
            '\n'.join(f"Trial {r['trial']}: {r['start']} / {r['requested_source']} -> {r['selected_source']}: {r['state']}; TTFF {(r.get('metrics') or {}).get('receiver_ttff_seconds')} s; PPS/NTP recovery {(r.get('metrics') or {}).get('f10p_seconds')} s; {r['residual_data']}" for r in safe_rows) +
            '\nStatistics: ' + json.dumps(value['summary'], allow_nan=False))


class Jobs:
    """One durable job, immutable bounded history, root-only cancellation."""
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def read(self):
        return private_json(self.root/'job.json', {})

    def save(self, value):
        directory(self.root)
        atomic_json(self.root/'job.json', value)

    def queue(self, request, kind='benchmark'):
        if self.read().get('state') in ('Queued','Running','Recovering','Recovery required'):
            raise assist.AssistanceError('An acquisition operation requires completion or recovery')
        sequence = trials(request) if kind == 'benchmark' else []
        if kind not in ('benchmark','snapshot','restore'):
            raise assist.AssistanceError('Unsupported maintenance job')
        if request.get('confirm') != CONFIRM:
            raise assist.AssistanceError('Explicit timing-interruption confirmation required')
        value = {'version':1,'id':uuid.uuid4().hex,'kind':kind,'created_at':time.time(),
                 'state':'Queued','phase':'Awaiting exclusive maintenance','trials':sequence,'results':[],
                 'antenna':str(request.get('antenna',''))[:160]}
        self.save(value)
        (self.root/'cancel.json').unlink(missing_ok=True)
        return value

    def finish(self, value):
        self.save(value)
        history = directory(self.root/'history')
        atomic_json(history/(value['id']+'.json'), value)
        for p in sorted(history.glob('*.json'),key=lambda p:p.stat().st_mtime_ns, reverse=True)[20:]:
            p.unlink()

    def cancel(self):
        directory(self.root)
        atomic_json(self.root/'cancel.json', {'id':self.read().get('id')})

    def cancelled(self, ident):
        return private_json(self.root/'cancel.json', {}).get('id') == ident


def public_status(root=ROOT, config=None, identity=None):
    config = load_policy() if config is None else config
    jobs = Jobs(root)
    value = jobs.read()
    safe = json.loads(result_export(value))
    safe.update(kind=value.get('kind'), phase=value.get('phase'), completed_at=value.get('completed_at'))
    history = []
    path = Path(root)/'history'
    if path.exists():
        for p in sorted(path.glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[:20]:
            item = private_json(p, {})
            history.append({'id':item.get('id'),'state':item.get('state'),'created_at':item.get('created_at'),
                            'kind':item.get('kind'),'trials':len(item.get('results',[]))})
    qualification = {name:qualified(root,identity,name) for name in ('database_capture','database_restore','shutdown')}
    qualification_record=private_json(Path(root)/'qualification.json',{})
    return {'policy':config,'policy_configured':CONFIG.exists(), 'job':safe,'history':history,
            'database':NavigationStore(root).status(identity,max_age_hours=config['database_max_age_hours']),
            'qualification':qualification,
            'qualification_hold':qualification_record.get('identity')==identity and qualification_record.get('requalification_required') is True,
            'shutdown':{k:v for k,v in private_json(Path(root)/'shutdown-result.json',{}).items()
                        if k in ('state','at','duration_seconds')},
            'retained_data':'Receiver-retained and flash-resident predictions may remain; software reset is not a power cycle'}
