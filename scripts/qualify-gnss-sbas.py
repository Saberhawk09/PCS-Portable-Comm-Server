"""Explicit one-time commissioning; no daemon, no reset, no timing-key writes.

Only NAV-SBAS message output can be changed; no reset, assistance or ANA writes.
Only run under the deployment's bounded systemd maintenance unit. GPSD releases
the enrolled alias before the existing exclusive admission helper opens it.
The StopPost recovery command restores an interrupted transaction, then gives
the alias back to the same GPSD process. Network work is never performed here.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
sys.path.insert(0, '/usr/local/lib/pcs')
from pcs_gnss import (KEYS, OUTPUT_KEYS, atomic_json, decode_values, load_config,
                      match_receiver, ubx, valget, valset)
from pcs_assistnow import receiver_identity

ROOT = Path('/var/tmp/pcs-gnss-refinement-20261117/receiver-maintenance')
ALIAS = '/dev/pcs-gnss-neo'
# UBX-23002975 R02: CFG-MSGOUT-UBX_NAV_SBAS_UART1, U1, 1/navigation epoch.
RATES = {'NAV-SBAS': 0x2091006b}
DESIRED = {RATES['NAV-SBAS']: 1}
# Read and preserve timing, constellation, diagnostic, ANA and ITFM settings.
PROTECTED_DIAGNOSTICS = (0x2091035a, 0x2091038c, 0x20910635, 0x2091068a,
    0x20910346, 0x20910016, 0x20910039, 0x2091001b, 0x2091007a,
    0x20910011, 0x10230001, 0x1041000d, 0x30230002)
READ_KEYS = sorted(set(DESIRED) | set(PROTECTED_DIAGNOSTICS) |
                   {key for key, _ in KEYS.values()} | set(OUTPUT_KEYS.values()))

def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()

def owners():
    p = subprocess.run(['fuser', ALIAS], capture_output=True, text=True, timeout=3)
    if p.returncode not in (0, 1):
        raise ValueError('Cannot establish receiver ownership')
    return p.stdout.split()

def control(sign):
    assert sign in ('+', '-')
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect('/run/gpsd.sock')
        s.sendall((sign + ALIAS + '\n').encode())
        raw = s.recv(4096)
        if not (raw.startswith(b'OK') or json.loads(raw).get('class') == 'ACK'):
            raise ValueError('GPSD ownership control refused')

def admission():
    loader = importlib.machinery.SourceFileLoader('pcs_admission_profile', '/usr/local/sbin/pcs-gnss')
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

def enrolled(module):
    cfg = load_config()
    actual = match_receiver(module.usb_devices(), cfg['receiver'])['device']
    if cfg['mode'] != 'neo-f10n' or cfg.get('baud') != 115200 or os.stat(actual).st_rdev != os.stat(ALIAS).st_rdev:
        raise ValueError('Enrolled receiver identity changed')

def read(exchange, keys, layer=0):
    payload = exchange(valget(keys, layer), 6, 0x8b)
    if len(payload) < 4 or payload[1] != layer:
        raise ValueError('Wrong receiver configuration layer')
    result = decode_values(payload)
    if set(result) - set(keys) or (layer == 0 and set(result) != set(keys)):
        raise ValueError('Incomplete or unrelated receiver configuration readback')
    return result

def snapshot(exchange):
    return {str(layer): {str(k): v for k, v in read(exchange, READ_KEYS, layer).items()} for layer in (0, 1, 2)}

def identity(exchange):
    version = exchange(ubx(10, 4), 10, 4)
    unique = exchange(ubx(0x27, 3), 0x27, 3)
    atomic_json(ROOT/'identity-readback.json', {'version_frame':ubx(10,4,version).hex(),'unique_frame':ubx(0x27,3,unique).hex()})
    print(json.dumps({'identity_format':{'length':len(unique),'version':unique[0] if unique else None,'reserved_zero':unique[1:4]==b'\0\0\0','nonzero_id':any(unique[4:])}}),file=sys.stderr)
    return receiver_identity(ubx(10, 4, version), ubx(0x27, 3, unique)), version, unique

def verify_protected(before, after):
    for layer in ('0', '1', '2'):
        a = {k:v for k,v in before[layer].items() if int(k) not in DESIRED}
        b = {k:v for k,v in after[layer].items() if int(k) not in DESIRED}
        if a != b:
            raise ValueError('Unrelated configuration changed')

def restore(exchange, journal):
    if set(journal['keys']) - set(map(str, DESIRED)) or not journal['keys']:
        raise ValueError('Unexpected rollback keys')
    for layer in journal['layers']:
        wanted = journal['before'][str(layer)]
        present = {int(k): wanted[k] for k in journal['keys'] if k in wanted}
        missing = [int(k) for k in journal['keys'] if k not in wanted]
        mask = 1 << layer
        if layer == 0 and missing:
            raise ValueError('Missing RAM rollback value')
        if present:
            frame = bytearray(valset(present))
            # Build a fresh checksum after choosing only this layer.
            payload = bytes((0, mask, 0, 0)) + bytes(frame[10:-2])
            exchange(ubx(6, 0x8a, payload), 5, 1, b'\x06\x8a')
        if missing:
            exchange(ubx(6, 0x8c, bytes((0, mask, 0, 0)) + b''.join(struct.pack('<I', k) for k in missing)), 5, 1, b'\x06\x8c')
        actual = read(exchange, [int(k) for k in journal['keys']], layer)
        if actual != present:
            raise ValueError('Rollback layer readback mismatch')

def transaction(exchange, before, binding, persist):
    layers = [0, 1, 2] if persist else [0]
    changes = {k:v for k,v in DESIRED.items() if any(before[str(layer)].get(str(k)) != v for layer in layers)}
    if not changes:
        return {'state': 'Already configured', 'changed': 0}
    journal = {'state': 'pending', 'binding': binding, 'before': before,
               'keys': list(map(str, changes)), 'layers': layers, 'persistent': persist}
    atomic_json(ROOT/'profile-journal.json', journal)
    try:
        exchange(valset(changes, persistent=persist), 5, 1, b'\x06\x8a')
        after = snapshot(exchange)
        for layer in layers:
            if any(after[str(layer)].get(str(k)) != v for k,v in changes.items()):
                raise ValueError('Profile layer readback mismatch')
        verify_protected(before, after)
    except BaseException:
        restore(exchange, journal)
        atomic_json(ROOT/'profile-journal.json', dict(journal, state='restored'))
        raise
    atomic_json(ROOT/'profile-journal.json', dict(journal, state='committed'))
    atomic_json(ROOT/('after-persistent.json' if persist else 'after-ram.json'), after)
    return {'state': 'Layer readback verified', 'changed': len(changes), 'persistent': persist}

def resume():
    state = json.loads((ROOT/'session.json').read_text())
    if command('systemctl','show','gpsd.service','-p','MainPID','--value') != state['pid']:
        raise ValueError('GPSD generation changed; manual recovery required')
    current = owners()
    journal = json.loads((ROOT/'profile-journal.json').read_text()) if (ROOT/'profile-journal.json').exists() else {}
    try:
        if journal.get('state') == 'pending':
            if current:
                raise ValueError('Interrupted transaction still owned; refuse competing open')
            module=admission()
            enrolled(module)
            with module.identified_receiver(ALIAS,115200,False) as (fd,_,_,_):
                exchange=lambda request,cls,msg,ack=None: module.exchange(fd,request,cls,msg,ack,timeout=2)
                bound,_,_=identity(exchange)
                if bound['binding'] != journal['binding']:
                    raise ValueError('Rollback receiver binding mismatch')
                restore(exchange,journal)
                atomic_json(ROOT/'profile-journal.json',dict(journal,state='restored-after-interruption'))
    finally:
        current=owners()
        if not current:
            control('+')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not owners(): time.sleep(.1)
        if owners() != [state['pid']]:
            raise ValueError('GPSD did not regain sole receiver ownership')
    print(json.dumps({'state':'GPSD ownership restored','pid':state['pid']}))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('inspect','ram','persist','resume','restore-original'))
    parser.add_argument('--restore-confirm')
    parser.add_argument('--confirm')
    args=parser.parse_args()
    if args.action=='restore-original' and args.restore_confirm!='I authorize restoring the pre-refinement NAV-SBAS output':
        parser.error('Explicit receiver rollback authorization required')
    if args.action in ('ram', 'persist') and args.confirm != 'I authorize NAV-SBAS output maintenance':
        parser.error('Explicit NAV-SBAS maintenance authorization required')
    assert os.geteuid()==0 and command('hostname')=='pcs-pi'
    os.umask(0o077)
    ROOT.mkdir(mode=0o700,exist_ok=True)
    if args.action=='resume':
        resume()
        return
    if args.action == 'inspect' and (ROOT/'original.json').exists():
        raise ValueError('Existing baseline must not be overwritten')
    import fcntl
    with Path('/run/pcs-gnss/installation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        module=admission()
        enrolled(module)
        pid=command('systemctl','show','gpsd.service','-p','MainPID','--value')
        assert pid!='0' and owners()==[pid]
        assert b'-b\0' in Path('/proc/'+pid+'/cmdline').read_bytes()
        atomic_json(ROOT/'session.json',{'pid':pid,'action':args.action,'started':time.time()})
        control('-')
        deadline=time.monotonic()+3
        while owners() and time.monotonic()<deadline: time.sleep(.1)
        if owners(): raise ValueError('GPSD did not release UART')
        try:
            with module.identified_receiver(ALIAS,115200,False) as (fd,_,_,baud):
                exchange=lambda request,cls,msg,ack=None: module.exchange(fd,request,cls,msg,ack,timeout=2)
                bound,version,unique=identity(exchange)
                before=snapshot(exchange)
                if before['0'][str(KEYS['measurement_ms'][0])]!=1000 or before['0'][str(KEYS['navigation_ratio'][0])]!=1:
                    raise ValueError('Profile requires existing one-Hz navigation')
                if args.action=='inspect':
                    atomic_json(ROOT/'original.json',{'identity':bound,'values':before,'timestamp':time.time()})
                    result={'state':'Preserved current configuration','identity':{k:v for k,v in bound.items() if k!='binding'},'settings':{name:before['0'].get(str(key)) for name,key in RATES.items()},'orbit_error_limit_m':before['0'][str(0x30230002)],'stored_layer_keys':{layer:len(values) for layer,values in before.items()}}
                else:
                    original=json.loads((ROOT/'original.json').read_text())
                    if original['identity']!=bound: raise ValueError('Receiver identity changed')
                    verify_protected(original['values'],before)
                    if args.action=='persist' and any(before['0'].get(str(k))!=v for k,v in DESIRED.items()):
                        raise ValueError('RAM profile not qualified')
                    if args.action=='restore-original':
                        journal={'state':'pending','binding':bound['binding'],'keys':list(map(str,DESIRED)),
                                 'layers':[0,1,2],'before':original['values'],'persistent':True}
                        atomic_json(ROOT/'profile-journal.json',journal)
                        restore(exchange,journal)
                        atomic_json(ROOT/'profile-journal.json',dict(journal,state='original-profile-restored'))
                        result={'state':'Original NAV-SBAS output restored in RAM, BBR and flash'}
                    else:
                        result=transaction(exchange,before,bound['binding'],args.action=='persist')
                atomic_json('/run/pcs-gnss/receiver-info.json',dict(bound,baud=baud,verified_at=time.time()))
                print(json.dumps(result))
        finally:
            resume()

if __name__=='__main__':
    main()
