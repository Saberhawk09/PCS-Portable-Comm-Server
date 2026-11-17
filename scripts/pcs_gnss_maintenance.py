#!/usr/bin/env python3
"""Bounded, journaled maintenance using the commissioned admission owner.

No serial descriptor exists until the timing gate acknowledges withdrawal and
the same read-only GPSD process releases the enrolled alias. Network preparation
is separate and completes before the installation/receiver lock is acquired.
"""
import contextlib
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import time

import pcs_assistnow as assist
import pcs_gnss_acquisition as acq
import pcs_gnss_manager as manager
from pcs_gnss import RUNTIME, UBXStream, atomic_json, load_config, match_receiver, read_status, ubx

ALIAS = '/dev/pcs-gnss-neo'
LEASE = RUNTIME/'maintenance.json'
JOURNAL = acq.ROOT/'hardware-journal.json'


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class Cancelled(ValueError):
    pass


def command(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False,
                       env=dict(os.environ, LC_ALL='C'))
    if p.returncode:
        raise assist.AssistanceError('Maintenance command unavailable')
    return p.stdout.strip()


@contextlib.contextmanager
def lock(path):
    import fcntl
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def owners():
    p = subprocess.run(['fuser',ALIAS], capture_output=True,text=True,timeout=3)
    if p.returncode not in (0,1):
        raise assist.AssistanceError('Receiver ownership unknown')
    return p.stdout.split()


def control(sign):
    if sign not in ('+','-'):
        raise ValueError('Invalid GPSD control')
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect('/run/gpsd.sock')
        s.sendall((sign+ALIAS+'\n').encode())
        raw=s.recv(4096)
        if not (raw.startswith(b'OK') or json.loads(raw).get('class') == 'ACK'):
            raise assist.AssistanceError('GPSD handover refused')


def admission():
    candidate=Path(__file__).with_name('pcs-gnss.py')
    if not candidate.exists():
        candidate=Path('/usr/local/sbin/pcs-gnss')
    loader=importlib.machinery.SourceFileLoader('pcs_acquisition_admission',str(candidate))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    result=importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


def enrolled(module):
    cfg=load_config()
    device=match_receiver(module.usb_devices(),cfg['receiver'])['device']
    if cfg['mode'] != 'neo-f10n' or cfg.get('baud') != 115200 or os.stat(device).st_rdev != os.stat(ALIAS).st_rdev:
        raise assist.AssistanceError('Commissioned receiver enrollment required')


def identity(exchange):
    version=exchange(ubx(10,4),10,4)
    unique=exchange(ubx(0x27,3),0x27,3)
    result=assist.receiver_identity(ubx(10,4,version),ubx(0x27,3,unique))
    expected=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True).get('identity')
    if result != expected:
        raise assist.AssistanceError('Exact receiver and firmware binding required')
    return result


def time_hint():
    # Only independently synchronized network time qualifies automatically.
    # A raw DS1307 reading alone is not evidence of a validated RTC.
    tracking=command('chronyc','-n','tracking')
    sources=command('chronyc','-n','sources')
    network=any(line.startswith('^*') for line in sources.splitlines())
    normal='Leap status     : Normal' in tracking or any('Leap status' in l and l.split(':')[-1].strip()=='Normal' for l in tracking.splitlines())
    hint=acq.independent_time(time.time(),network and normal,'network' if network else '',uncertainty=1)
    if not hint['valid']:
        # Chrony may prefer F10P while still measuring good Internet sources.
        # Require two fresh public-IP measurements agreeing within a broad,
        # explicitly coarse bound. This reads cached measurements, not the WAN.
        from pcs_gnss_diagnostics import chrony_source_offsets
        offsets=[]
        for line in sources.splitlines():
            columns=line.split()
            if len(columns)<7 or columns[0] not in ('^*','^+','^-'):
                continue
            try:
                if not ipaddress.ip_address(columns[1]).is_global or not 1<=int(columns[2])<=14:
                    continue
                # Reuse the existing tested units/reach/age decoder; only the
                # local parser sees this synthetic reference label.
                normalized='#'+line[1:]
                normalized=normalized.replace(columns[1],'F10P',1)
                row=chrony_source_offsets(normalized).get('F10P',{})
                age=row.get('sample_age_seconds',1e9)
                max_age=min(3600,max(180,2*(2**int(columns[3]))))
                if (age<=max_age and row.get('root_distance_seconds',1e9)<=.5
                        and abs(row.get('adjusted_offset_seconds',1e9))<=.5):
                    offsets.append((columns[1],row['adjusted_offset_seconds'],age))
            except (ValueError,IndexError):
                continue
        if len({a for a,_,_ in offsets})>=2 and max(v for _,v,_ in offsets)-min(v for _,v,_ in offsets)<=.5:
            hint=acq.independent_time(time.time(),True,'network',uncertainty=1+max(age for _,_,age in offsets)*.0002)
            hint['source']='Independent Internet NTP cross-check (two sources)'
    rtc=Path('/sys/class/rtc/rtc0')
    validation_path=acq.ROOT/'rtc-validation.json'
    try:
        if 'ds1307' not in (rtc/'name').read_text().lower():
            return hint
        epoch=int((rtc/'since_epoch').read_text())
        if hint['valid'] and abs(epoch-hint['utc'])<=2:
            atomic_json(validation_path,{'utc':hint['utc'],'rtc_utc':epoch,'source':hint['source']})
        elif not hint['valid']:
            previous=acq.private_json(validation_path,{})
            elapsed=epoch-previous.get('rtc_utc',float('inf'))
            # A recent independent cross-check, monotone plausible RTC and host
            # agreement qualify a coarse date, never the precision UTC gate.
            if 0<=elapsed<=7*86400 and abs(epoch-time.time())<=2:
                hint=acq.independent_time(time.time(),rtc={'validated':True,'device':'ds1307',
                    'age_seconds':0,'utc':epoch},uncertainty=3+elapsed*.0002)
    except (OSError,ValueError,TypeError):
        pass
    return hint


def pps_sequence():
    try:
        target=(RUNTIME/'pps').resolve(strict=True).name
        return int((Path('/sys/class/pps')/target/'assert').read_text().strip().split('#')[1])
    except (OSError,ValueError,IndexError):
        return None


def paired_timing():
    state=read_status()
    return (state.get('state')=='fresh' and state.get('pps_validation')=='paired' and
            state.get('selected_ref')=='F10P' and state.get('pps_selection')=='*' and
            state.get('utc_valid') is True and state.get('pps_active') is True and state.get('synchronized') is True)


class Channel:
    def __init__(self,fd,check=lambda:None,event=lambda *args:None):
        self.fd,self.check,self.event=fd,check,event
        self.decoder=UBXStream()

    def send(self,frame):
        self.check()
        deadline=time.monotonic()+2
        pending=memoryview(frame)
        while pending:
            self.check()
            if time.monotonic()>=deadline:
                raise assist.AssistanceError('Receiver write timeout')
            if select.select([],[self.fd],[],.1)[1]:
                try:
                    n=os.write(self.fd,pending)
                except BlockingIOError:
                    continue
                if n<=0:
                    raise assist.AssistanceError('Receiver write failed')
                pending=pending[n:]

    def poll(self,seconds=.2):
        self.check()
        if not select.select([self.fd],[],[],seconds)[0]:
            return []
        try:
            data=os.read(self.fd,16384)
        except BlockingIOError:
            return []
        if not data:
            raise assist.AssistanceError('Receiver disconnected')
        values=self.decoder.feed(data)
        for value in values:
            self.event(*value)
        return values

    def exchange(self,request,cls,msg,ack=None,timeout=2):
        self.send(request)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            for group,number,payload in self.poll(.05):
                if (group,number)==(5,0) and payload==request[2:4]:
                    raise assist.AssistanceError('Receiver rejected request')
                if (group,number)==(cls,msg) and (ack is None or payload==ack):
                    # Discard late assistance ACKs for other in-flight messages.
                    if (cls,msg)==(0x13,0x60) and (len(payload)!=8 or payload[3]!=request[3] or payload[4:8]!=request[6:10]):
                        continue
                    return payload
        raise assist.AssistanceError('Receiver response timeout')


def wait(predicate, seconds, check=lambda:None):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        check()
        if predicate():
            return
        time.sleep(.1)
    raise assist.AssistanceError('Maintenance phase timeout')


class Owner:
    def __init__(self,ident,check=lambda:None):
        self.ident,self.check=ident,check
        self.bound=None

    def lease_ack(self):
        value=assist.read_json(RUNTIME/'maintenance-ack.json',{})
        pid=command('systemctl','show','pcs-gnss.service','-p','MainPID','--value')
        return (value.get('id')==self.ident and value.get('withdrawn') is True and str(value.get('pid'))==pid
                and 0<=time.monotonic()-value.get('updated_monotonic',-1e20)<=2)

    @contextlib.contextmanager
    def acquire(self):
        with lock(RUNTIME/'installation.lock'):
            if LEASE.exists() or acq.private_json(JOURNAL,{}).get('state')=='pending':
                raise assist.AssistanceError('Previous receiver handover requires recovery')
            module=admission()
            enrolled(module)
            pid=command('systemctl','show','gpsd.service','-p','MainPID','--value')
            if pid=='0' or owners()!=[pid] or b'-b\0' not in Path('/proc/'+pid+'/cmdline').read_bytes():
                raise assist.AssistanceError('Expected read-only GPSD must own UART exclusively')
            journal={'state':'pending','id':self.ident,'gpsd_pid':pid,'stopped':False,'identity':None,
                     'boot_id':boot_id()}
            atomic_json(JOURNAL,journal)
            atomic_json(LEASE,{'id':self.ident})
            try:
                wait(self.lease_ack,5,self.check)
                control('-')
                wait(lambda:not owners(),3,self.check)
                with module.identified_receiver(ALIAS,115200,False) as (fd,_,_,_):
                    channel=Channel(fd,self.check)
                    self.bound=identity(channel.exchange)
                    journal['identity']=self.bound
                    atomic_json(JOURNAL,journal)
                    manager.restore_pending(channel.exchange,binding=self.bound['binding'])
                    try:
                        yield channel,self.bound
                    finally:
                        # Cleanup must run even after cancellation; bounded I/O.
                        channel.check=lambda:None
                        manager.restore_pending(channel.exchange,binding=self.bound['binding'])
                        pending=acq.private_json(JOURNAL,{})
                        if pending.get('stopped'):
                            channel.send(acq.control_frame(True))
                            channel.exchange(ubx(10,4),10,4)
                            atomic_json(JOURNAL,dict(pending,stopped=False))
            finally:
                recover(locked=True)


def recover(locked=False):
    """StopPost also calls this after SIGKILL; never opens an occupied UART."""
    if not locked:
        with lock(RUNTIME/'installation.lock'):
            return recover(True)
    value=acq.private_json(JOURNAL,{})
    if value.get('state')!='pending':
        if LEASE.exists():
            raise assist.AssistanceError('Unjournaled maintenance lease requires review')
        return
    pid=command('systemctl','show','gpsd.service','-p','MainPID','--value')
    if pid != value.get('gpsd_pid'):
        raise assist.AssistanceError('GPSD generation changed during maintenance; references remain withdrawn')
    current=owners()
    pending=assist.read_json(assist.ROOT/'receiver-transaction.json',{}).get('state')=='pending'
    if value.get('stopped') or pending:
        if current:
            raise assist.AssistanceError('Cannot recover receiver settings while UART is owned')
        module=admission()
        enrolled(module)
        with module.identified_receiver(ALIAS,115200,False) as (fd,_,_,_):
            channel=Channel(fd)
            bound=identity(channel.exchange)
            if bound != value.get('identity'):
                raise assist.AssistanceError('Recovery identity mismatch')
            manager.restore_pending(channel.exchange,binding=bound['binding'])
            if value.get('stopped'):
                channel.send(acq.control_frame(True))
                channel.exchange(ubx(10,4),10,4)
    if not current:
        control('+')
        wait(lambda:owners()==[pid],5)
    if owners()!=[pid]:
        raise assist.AssistanceError('GPSD failed to regain sole ownership')
    atomic_json(JOURNAL,dict(value,state='recovered',stopped=False))
    LEASE.unlink(missing_ok=True)


def stopped(channel):
    # Journal intent before the command, including power/process interruption.
    value=acq.private_json(JOURNAL,{})
    atomic_json(JOURNAL,dict(value,stopped=True))
    channel.send(acq.control_frame(False))
    channel.exchange(ubx(10,4),10,4)
    # Allow pending NAV output to drain; stop has no required ACK.
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        channel.poll(.05)


def started(channel):
    channel.send(acq.control_frame(True))
    channel.exchange(ubx(10,4),10,4)
    value=acq.private_json(JOURNAL,{})
    atomic_json(JOURNAL,dict(value,stopped=False))


def dump_database(channel,deadline,binding):
    # F10 SPG 6.00 suppresses the dump-completion MGA-ACK when ACKAIDING
    # is disabled. Preserve the setting and enable it in RAM for this transfer.
    before=manager.read_values(channel.exchange,[manager.ACK_AIDING])
    journal=assist.ROOT/'receiver-transaction.json'
    atomic_json(journal,{'state':'pending','binding':binding,'before':{str(k):v for k,v in before.items()}})
    try:
        manager.write_values(channel.exchange,{manager.ACK_AIDING:1})
        return collect_database(channel,deadline)
    finally:
        manager.write_values(channel.exchange,before)
        atomic_json(journal,{'state':'restored'})


def collect_database(channel,deadline):
    channel.send(ubx(0x13,0x80))
    frames=[]
    while time.monotonic()<deadline:
        for cls,msg,payload in channel.poll(.05):
            if (cls,msg)==(0x13,0x80):
                frame=ubx(cls,msg,payload)
                acq.dbd_frames(frame)
                frames.append(frame)
                if len(frames)>8192 or sum(map(len,frames))>1024*1024:
                    raise assist.AssistanceError('Database dump exceeded bounds')
            elif (cls,msg)==(0x13,0x60) and len(payload)==8 and payload[3]==0x80:
                if not acq.dbd_completion(payload,len(frames)):
                    raise assist.AssistanceError('Database completion count or ACK invalid')
                return b''.join(frames),payload
    raise assist.AssistanceError('Database transfer incomplete')


def restore_database(exchange,frames,binding,deadline_seconds=20):
    acq.dbd_frames(b''.join(frames))
    before=manager.read_values(exchange,[manager.ACK_AIDING])
    journal=assist.ROOT/'receiver-transaction.json'
    atomic_json(journal,{'state':'pending','binding':binding,'before':{str(k):v for k,v in before.items()}})
    accepted=0
    deadline=time.monotonic()+deadline_seconds
    try:
        manager.write_values(exchange,{manager.ACK_AIDING:1})
        for frame in frames:
            if time.monotonic()>=deadline:
                raise assist.AssistanceError('Database restore deadline')
            ack=exchange(frame,0x13,0x60)
            if len(ack)!=8 or ack[:4]!=b'\x01\0\0\x80' or ack[4:8]!=frame[6:10]:
                raise assist.AssistanceError('Database restore rejected or mismatched ACK')
            accepted+=1
        return {'state':'Accepted','accepted':accepted,'rejected':0}
    finally:
        manager.write_values(exchange,before)
        atomic_json(journal,{'state':'restored'})


def inject_time(exchange,hint,binding):
    frame=acq.time_frame(hint)
    before=manager.read_values(exchange,[manager.ACK_AIDING])
    journal=assist.ROOT/'receiver-transaction.json'
    atomic_json(journal,{'state':'pending','binding':binding,'before':{str(k):v for k,v in before.items()}})
    try:
        manager.write_values(exchange,{manager.ACK_AIDING:1})
        ack=exchange(frame,0x13,0x60)
        if len(ack)!=8 or ack[:4]!=b'\1\0\0\x40' or ack[4:8]!=frame[6:10]:
            raise assist.AssistanceError('Coarse acquisition time hint rejected')
    finally:
        manager.write_values(exchange,before)
        atomic_json(journal,{'state':'restored'})


def prepare(source,bound,hint,fallback='none',paid=False):
    began=time.monotonic()
    try:
        return _prepare(source,bound,hint,fallback,paid)
    except (ValueError,OSError,subprocess.SubprocessError):
        if fallback=='none':
            raise
        if fallback=='local':
            try:
                result=_prepare('automatic',bound,hint,'none',False,_local=True)
                return dict(result,download_seconds=time.monotonic()-began if source in ('internet','live') else 0)
            except (ValueError,OSError,subprocess.SubprocessError):
                pass
        return {'source':'retained','frames':[],'metadata':{},
                'download_seconds':time.monotonic()-began if source in ('internet','live') else 0}


def _prepare(source,bound,hint,fallback='none',paid=False,_local=False):
    """No receiver or Chrony locks; existing downloader owns all quota logic."""
    started=time.monotonic()
    config=acq.load_policy()
    if source=='automatic' and not _local:
        source=config['boot_policy'] if assist.load_config()['boot_injection'] else 'retained'
    source='retained' if source=='none-host' else source
    selected,frames,meta=source,[],{}
    if source in ('internet','live'):
        settings=assist.load_config()
        if source=='live' and not (paid and settings['live_enabled'] and settings['live_cost_authorized']):
            raise assist.AssistanceError('Explicit paid-service consent and entitlement required')
        from pcs_gnss_info import selected_network,save_assist_status
        kind='live' if source=='live' else 'predictive'
        with lock(assist.ROOT/'download.lock'):
            credentials=assist.load_credentials()
            if not credentials or credentials.get('identity')!=bound:
                raise assist.AssistanceError('Credentials do not match verified receiver')
            usage=assist.read_json(assist.ROOT/'usage.json',{})
            attempt=time.time()
            usage=assist.Downloader(assist.Cache()).refresh(settings,credentials,selected_network(),usage,kind,True)
            atomic_json(assist.ROOT/'usage.json',usage)
            save_assist_status(settings,credentials,usage)
            if usage.get(kind+'_success',0)<attempt:
                if fallback=='none':
                    raise assist.AssistanceError('Fresh assistance unavailable; selected no-fallback policy')
                selected='automatic' if fallback=='local' else 'retained'
    if selected=='automatic':
        # Local priority: qualified exact-receiver database, current-day cache,
        # then receiver-retained data. Live requests are never implicit here.
        if acq.qualified(acq.ROOT,bound,'database_restore'):
            try:
                frames,meta=acq.NavigationStore().read(bound,time.time(),config['database_max_age_hours'])
                selected='database'
            except (ValueError,OSError):
                pass
        if not frames:
            selected='cached'
    if selected=='database' and not hint.get('valid'):
        raise assist.AssistanceError('Independent date required for database age validation')
    if selected=='database' and not frames:
        frames,meta=acq.NavigationStore().read(bound,time.time(),config['database_max_age_hours'])
    if selected in ('cached','internet','live'):
        if not hint.get('valid'):
            if source=='automatic':
                selected='retained'
            else:
                raise assist.AssistanceError('Independent UTC date required for host orbit selection')
        else:
            kind,frames=assist.Cache().selected(bound,hint['utc'],live=selected=='live')
            if not frames or (selected=='live' and kind!='live'):
                if source=='automatic':
                    selected='retained'
                    frames=[]
                else:
                    raise assist.AssistanceError('Selected assistance cache unavailable')
    if selected in ('none','retained'):
        frames=[]
    return {'source':selected,'frames':frames,'metadata':meta,'download_seconds':time.monotonic()-started if source in ('internet','live') else 0}


def worker():
    os.umask(0o077)
    jobs=acq.Jobs()
    with lock(acq.ROOT/'worker.lock'):
        job=jobs.read()
        if job.get('state')!='Queued':
            return
        stopped_signal=False
        def stop(*_):
            nonlocal stopped_signal
            stopped_signal=True
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        def check():
            if stopped_signal or jobs.cancelled(job['id']):
                raise Cancelled('Cancelled')
        def phase(text):
            check()
            job.update(state='Running',phase=text)
            jobs.save(job)
        job.update(state='Running',phase='Preparing')
        jobs.save(job)
        try:
            facts=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True)
            bound=facts.get('identity')
            if not bound:
                raise assist.AssistanceError('Verified receiver identity missing')
            hint=time_hint()
            if job['kind'] in ('snapshot','restore'):
                if not hint['valid']:
                    raise assist.AssistanceError('Independent UTC date unavailable')
                phase('Receiver navigation maintenance')
                with Owner(job['id'],check).acquire() as (channel,actual):
                    if actual!=bound:
                        raise assist.AssistanceError('Receiver changed during preparation')
                    aop=channel.exchange(ubx(1,0x60),1,0x60)
                    if len(aop)!=16 or aop[5]:
                        raise assist.AssistanceError('Autonomous generation busy or unknown')
                    if job['kind']=='snapshot':
                        stopped(channel)
                        raw,ack=dump_database(channel,time.monotonic()+20,bound['binding'])
                        meta=acq.NavigationStore().publish(raw,ack,bound,hint,retention=acq.load_policy()['backup_retention'])
                        job['database_evidence']={'identity':bound,'sha256':meta['sha256'],'frame_count':meta['frame_count'],
                                                  'accepted':meta['frame_count'],'complete':True}
                    else:
                        frames,meta=acq.NavigationStore().read(bound,hint['utc'],acq.load_policy()['database_max_age_hours'])
                        stopped(channel)
                        result=restore_database(channel.exchange,frames,bound['binding'])
                        job['database_evidence']={'identity':bound,'sha256':meta['sha256'],'frame_count':len(frames),
                                                  'accepted':result['accepted'],'complete':True}
                phase('Waiting for fresh UTC/PPS after navigation maintenance')
                wait(paired_timing,120,check)
                job['timing_recovered']=True
                job['state']='Completed'
            else:
                prepared={}
                for trial in job['trials']:
                    phase('Preparing trial '+str(trial['trial'])+' assistance')
                    hint=time_hint()
                    # One fresh response per experiment, shared explicitly across
                    # repetitions. Never issue repeat billable requests in a loop.
                    key=(trial['source'],trial['fallback'])
                    if key not in prepared:
                        prepared[key]=prepare(trial['source'],bound,hint,trial['fallback'],trial['paid_consent'])
                    aid=prepared[key]
                    if key in prepared and aid['frames']:
                        if aid['source']=='live':
                            kind,frames=assist.Cache().selected(bound,hint['utc'],live=True)
                            if kind!='live':
                                raise assist.AssistanceError('Live response expired between trials')
                            aid=dict(aid,frames=frames)
                        else:
                            refreshed=prepare('cached' if aid['source']=='internet' else aid['source'],bound,hint,trial['fallback'])
                            aid=dict(aid,frames=refreshed['frames'],source=aid['source'] if refreshed['frames'] else refreshed['source'])
                        acq.time_frame(hint)  # Reject uncertain date before reset.
                    row={'trial':trial['trial'],'start':trial['start'],'requested_source':trial['source'],
                         'selected_source':aid['source'],'state':'Running','metrics':{},'reset_mask':acq.MASKS[trial['start']],
                         'reset_mode':2,'download_seconds':aid['download_seconds'],'accepted':0,'rejected':0,'offered':0,'unacknowledged':0,
                         'time_reference':hint['source'],'receiver_verified':False,
                         'firmware':bound.get('firmware'),'protocol':bound.get('protocol'),
                         'residual_data':'Autonomous/flash data may remain; not proven fully unassisted',
                         'comparability':'Warm/hot trials retain earlier navigation; RF and retained-data equivalence not established'}
                    job['results'].append(row)
                    phase('Trial '+str(trial['trial'])+': exclusive handover')
                    metrics=None
                    try:
                        with Owner(job['id'],check).acquire() as (channel,actual):
                            if actual!=bound:
                                raise assist.AssistanceError('Receiver changed')
                            row['receiver_verified']=True
                            # Identity, source validation and independent date all
                            # precede reset. No CFG/flash/constellation writes.
                            channel.decoder=UBXStream()
                            import termios
                            termios.tcflush(channel.fd,termios.TCIFLUSH)
                            started=time.monotonic()
                            metrics=acq.TrialMetrics(started,hint)
                            metrics.pps_baseline=pps_sequence()
                            channel.event=metrics.event
                            row['reset_at']=time.time()
                            channel.send(acq.reset_frame(trial['start']))
                            phase('Trial '+str(trial['trial'])+': receiver acquisition')
                            began=time.monotonic()
                            def counted_exchange(request,cls,msg,ack=None):
                                aiding=request[2]==0x13 and len(request)>8 and (cls,msg)==(0x13,0x60)
                                if aiding:
                                    row['offered']+=1
                                try:
                                    reply=channel.exchange(request,cls,msg,ack)
                                except BaseException:
                                    if aiding:
                                        row['unacknowledged']+=1
                                    raise
                                if aiding:
                                    valid=(len(reply)==8 and reply[:3]==b'\1\0\0' and reply[3]==request[3] and reply[4:8]==request[6:10])
                                    row['accepted' if valid else 'rejected']+=1
                                return reply
                            try:
                                if aid['frames']:
                                    if aid['source']=='database':
                                        stopped(channel)
                                    inject_time(counted_exchange,hint,bound['binding'])
                                    row['time_aiding']='Independent coarse UTC accepted; not a trusted timing sample'
                                    if aid['source']=='database':
                                        result=restore_database(counted_exchange,aid['frames'],bound['binding'])
                                        started(channel)
                                    else:
                                        result=manager.inject(counted_exchange,aid['frames'],deadline_seconds=15,binding=bound['binding'])
                                    if result['state']!='Accepted':
                                        raise assist.AssistanceError('Assistance delivery incomplete or rejected')
                            finally:
                                row['injection_seconds']=time.monotonic()-began
                                if aid['frames']:
                                    atomic_json(assist.ROOT/'injection.json',{'state':'Benchmark injection; acceptance recorded in acquisition report',
                                        'source':aid['source'],'timestamp':time.time(),
                                        **{k:row[k] for k in ('accepted','rejected','unacknowledged','offered')}})
                            while time.monotonic()-started<trial['timeout_seconds']:
                                channel.poll(.1)
                                metrics.timing({},pps_sequence())
                                row.update(metrics=dict(metrics.values),first_fix=metrics.first_fix)
                                if metrics.after_reset_seen and metrics.values['first_3d_seconds'] is not None and metrics.values['utc_seconds'] is not None:
                                    break
                                jobs.save(job)
                            if not metrics.after_reset_seen or metrics.values['first_3d_seconds'] is None:
                                raise assist.AssistanceError('Acquisition timeout or new session not verified')
                        phase('Trial '+str(trial['trial'])+': UTC/PPS and F10P recovery')
                        deadline=time.monotonic()+120
                        while time.monotonic()<deadline:
                            check()
                            metrics.timing(read_status(),pps_sequence())
                            row['metrics']=dict(metrics.values)
                            jobs.save(job)
                            if metrics.values['f10p_seconds'] is not None:
                                break
                            time.sleep(.5)
                        if metrics.values['f10p_seconds'] is None:
                            raise assist.AssistanceError('F10P recovery not observed within 120 seconds')
                        row.update(state='Completed',recovery='Fresh UTC, advancing PPS and F10P selection observed')
                    except BaseException:
                        if metrics:
                            row.update(metrics=dict(metrics.values),first_fix=metrics.first_fix)
                        row.update(state='Cancelled' if stopped_signal or jobs.cancelled(job['id']) else 'Failed',
                                   failure='Trial interrupted or unsuccessful; inspect recovery status')
                        raise
                job['state']='Completed'
        except (ValueError,OSError,subprocess.SubprocessError):
            job['state']='Cancelled' if stopped_signal or jobs.cancelled(job['id']) else 'Failed'
            job['failure']='Operation unsuccessful during '+job.get('phase','maintenance')
        finally:
            job['phase']='Restoring ordinary ownership and timing'
            try:
                recover()
            except (ValueError,OSError,subprocess.SubprocessError):
                job['state']='Recovery required'
                job['phase']='GNSS references remain withdrawn; operator recovery required'
            else:
                job['phase']='UART returned to GPSD; timing recovery is independently reported'
            job['completed_at']=time.time()
            jobs.finish(job)


def recover_job():
    recover()
    jobs=acq.Jobs()
    job=jobs.read()
    if job.get('state') in ('Queued','Running','Recovering','Recovery required'):
        job.update(state='Interrupted',phase='Recovered by service cleanup; inspect current UTC/PPS status',completed_at=time.time())
        jobs.finish(job)


def shutdown_backup():
    started=time.monotonic()
    config=acq.load_policy()
    armed_path=acq.ROOT/'shutdown-qualification.json'
    armed=acq.private_json(armed_path,{})
    qualification_trial=(armed.get('authorized') is True and 0<=time.time()-armed.get('at',0)<=900)
    if not config['save_on_shutdown'] and not qualification_trial:
        return
    jobs=acq.Jobs()
    bound=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True).get('identity')
    if qualification_trial:
        # Consume before I/O: never repeat an interrupted qualification backup.
        armed_path.unlink()
        if armed.get('identity')!=bound or armed.get('power_hold_seconds',0)<90:
            qualification_trial=False
    meta={}
    if not qualification_trial and not acq.qualified(acq.ROOT,bound,'shutdown'):
        state='Disabled: hardware qualification missing'
    else:
        try:
            with lock(acq.ROOT/'worker.lock'):
                hint=time_hint()
                if not hint['valid']:
                    raise assist.AssistanceError('Independent date unavailable')
                deadline=started+config['shutdown_budget_seconds']
                def check():
                    if time.monotonic()>=deadline:
                        raise assist.AssistanceError('Shutdown backup budget exceeded')
                with Owner('0'*32,check).acquire() as (channel,actual):
                    aop=channel.exchange(ubx(1,0x60),1,0x60)
                    if len(aop)!=16 or aop[5]:
                        raise assist.AssistanceError('Autonomous generation busy')
                    stopped(channel)
                    raw,ack=dump_database(channel,deadline,actual['binding'])
                    meta=acq.NavigationStore().publish(raw,ack,actual,hint,retention=config['backup_retention'])
                state='Completed'
        except (ValueError,OSError,subprocess.SubprocessError):
            state='Skipped or failed; last complete backup retained'
    atomic_json(acq.ROOT/'shutdown-result.json',{'state':state,'at':time.time(),'duration_seconds':time.monotonic()-started,
                'qualification_trial':qualification_trial,'identity':bound,'sha256':meta.get('sha256'),
                'boot_id':boot_id()})


def qualify(evidence_file,confirm,arm_shutdown=False):
    """Root-only evidence admission, never a web 'mark qualified' switch."""
    if confirm!='I authorize GNSS timing maintenance':
        raise assist.AssistanceError('Explicit hardware qualification authorization required')
    evidence=acq.private_json(evidence_file,{})
    bound=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True).get('identity')
    if not bound:
        raise assist.AssistanceError('Receiver not enrolled')
    receipts=[]
    for kind in ('snapshot','restore'):
        ident=evidence.get(kind,'')
        import re
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):
            raise assist.AssistanceError('Capture and restore reports required')
        job=acq.private_json(acq.ROOT/'history'/(ident+'.json'),{})
        proof=job.get('database_evidence',{})
        if (job.get('kind')!=kind or job.get('state')!='Completed' or not job.get('timing_recovered') or
                proof.get('identity')!=bound or proof.get('complete') is not True or
                not proof.get('frame_count') or proof.get('accepted')!=proof['frame_count']):
            raise assist.AssistanceError('Successful exact-receiver hardware evidence missing')
        receipts.append(proof)
    if receipts[0]['sha256']!=receipts[1]['sha256']:
        raise assist.AssistanceError('Restore must qualify the captured database')
    result={'identity':bound,'database_capture':True,'database_restore':True,'at':time.time(),
            'capture_report':evidence['snapshot'],'restore_report':evidence['restore'],'shutdown':False}
    shutdown=acq.private_json(acq.ROOT/'shutdown-result.json',{})
    if (shutdown.get('state')=='Completed' and shutdown.get('qualification_trial') is True and
            shutdown.get('identity')==bound and shutdown.get('sha256') and
            shutdown.get('boot_id')!=boot_id()):
        result['shutdown']=True
    atomic_json(acq.ROOT/'qualification.json',result)
    if arm_shutdown:
        if type(evidence.get('power_hold_seconds')) is not int or not 90<=evidence['power_hold_seconds']<=600:
            raise assist.AssistanceError('Verified power hold of at least 90 seconds required')
        atomic_json(acq.ROOT/'shutdown-qualification.json',{'authorized':True,'identity':bound,'at':time.time(),
                                                         'power_hold_seconds':evidence['power_hold_seconds']})
    return {'state':'Database hardware evidence admitted','shutdown_qualified':result['shutdown'],
            'shutdown_trial_armed':arm_shutdown}


def boot_assistance(exchange,version,unique,timing_healthy=False):
    """Inside GPSD admission's existing exclusive fd. Never performs network I/O.

    Host database restoration remains hardware-qualified and identity-bound.
    A pending GNSS-stop intent is recovered before any policy is evaluated.
    """
    bound=assist.receiver_identity(ubx(10,4,version),ubx(0x27,3,unique))
    journal=acq.ROOT/'boot-restore.json'
    pending=acq.private_json(journal,{})
    if pending.get('state')=='stopped':
        if pending.get('identity')!=bound:
            raise assist.AssistanceError('Interrupted boot restoration belongs to another receiver')
        # CFG-RST may not ACK; subsequent MON-VER is the responsiveness barrier.
        exchange(acq.control_frame(True)+ubx(10,4),10,4)
        atomic_json(journal,{'state':'recovered'})
    cfg=acq.load_policy()
    settings=assist.load_config()
    if timing_healthy or not settings['boot_injection']:
        return {'state':'Healthy timing / Deferred' if timing_healthy else 'Host injection disabled'}
    pvt=exchange(ubx(1,7),1,7)
    if len(pvt)!=92 or pvt[21]&1:
        return {'state':'Receiver fixed or unavailable / Deferred'}
    source='database' if cfg['restore_next_start'] else cfg['boot_policy']
    if source in ('none','retained'):
        return {'state':'No host assistance' if source=='none' else 'Receiver-retained only','accepted':0}
    # Network services run asynchronously; never hold this fd waiting for them.
    if source in ('internet','live'):
        fresh=acq.private_json(acq.ROOT/'boot-download.json',{})
        boot=boot_id()
        if fresh.get('boot_id')==boot and fresh.get('source')==source and fresh.get('success'):
            source='live' if source=='live' else 'cached'
        else:
            source={'local':'automatic','retained':'retained','none':'none'}[cfg['fallback']]
    if source in ('none','retained'):
        return {'state':'Online assistance not ready / '+source,'accepted':0}
    hint=time_hint()
    # prepare() must not download within admission, even for a cached Live reply.
    if source=='live':
        if not hint['valid']:
            return {'state':'Independent date unavailable / Ordinary acquisition','accepted':0}
        kind,frames=assist.Cache().selected(bound,hint['utc'],live=True)
        if kind!='live':
            return {'state':'Live cache unavailable / Ordinary acquisition','accepted':0}
        aid={'source':'live','frames':frames}
    else:
        aid=prepare(source,bound,hint,cfg['fallback'])
    if not aid['frames']:
        return {'state':'Receiver-retained / Ordinary acquisition','accepted':0}
    inject_time(exchange,hint,bound['binding'])
    if aid['source']=='database':
        if not acq.qualified(acq.ROOT,bound,'database_restore'):
            return {'state':'Database restore not hardware-qualified','accepted':0}
        atomic_json(journal,{'state':'stopped','identity':bound})
        try:
            exchange(acq.control_frame(False)+ubx(10,4),10,4)
            result=restore_database(exchange,aid['frames'],bound['binding'],deadline_seconds=3)
        finally:
            exchange(acq.control_frame(True)+ubx(10,4),10,4)
            atomic_json(journal,{'state':'recovered'})
        if cfg['restore_next_start']:
            # Admission already holds installation.lock; web settings take it too.
            atomic_json(acq.CONFIG,dict(cfg,restore_next_start=False))
    else:
        result=manager.inject(exchange,aid['frames'],binding=bound['binding'])
    return dict(result,source=aid['source'])


def recover_admission(exchange):
    """Recover a power-loss journal inside the already exclusive boot owner.

    Same-boot GPSD generation changes are not silently adopted. A genuine new
    boot has no old process/descriptor; exact identity still gates recovery.
    """
    pending=acq.private_json(JOURNAL,{})
    boot_restore=acq.private_json(acq.ROOT/'boot-restore.json',{})
    if pending.get('state')!='pending' and boot_restore.get('state')!='stopped':
        return
    bound=identity(exchange)
    if pending.get('state')=='pending':
        boot=boot_id()
        if pending.get('boot_id')==boot or pending.get('identity') not in (None,bound):
            raise assist.AssistanceError('Interrupted same-boot maintenance requires explicit recovery')
        manager.restore_pending(exchange,binding=bound['binding'])
        if pending.get('stopped'):
            exchange(acq.control_frame(True)+ubx(10,4),10,4)
        atomic_json(JOURNAL,dict(pending,state='recovered-after-boot',stopped=False))
        LEASE.unlink(missing_ok=True)
        jobs=acq.Jobs()
        job=jobs.read()
        if job.get('state') in ('Queued','Running','Recovering','Recovery required'):
            job.update(state='Interrupted',phase='Recovered after power loss; no completed TTFF claim',completed_at=time.time())
            jobs.finish(job)
    if boot_restore.get('state')=='stopped':
        if boot_restore.get('identity')!=bound:
            raise assist.AssistanceError('Boot database journal receiver mismatch')
        exchange(acq.control_frame(True)+ubx(10,4),10,4)
        atomic_json(acq.ROOT/'boot-restore.json',{'state':'recovered'})


def boot_refresh():
    """An optional asynchronous check; receiver startup never depends on it."""
    if not acq.CONFIG.exists():
        return
    cfg=acq.load_policy()
    if cfg['boot_policy'] not in ('internet','live'):
        return
    bound=assist.read_json(assist.ROOT/'receiver-facts.json',{},private=True).get('identity')
    if not bound:
        return
    source=cfg['boot_policy']
    started=time.time()
    try:
        prepare(source,bound,time_hint(),'none',paid=source=='live')
    except (ValueError,OSError,subprocess.SubprocessError):
        success=False
    else:
        usage=assist.read_json(assist.ROOT/'usage.json',{})
        success=usage.get(('live' if source=='live' else 'predictive')+'_success',0)>=started
    atomic_json(acq.ROOT/'boot-download.json',{'boot_id':boot_id(),
                'source':source,'success':success,'at':time.time()})
