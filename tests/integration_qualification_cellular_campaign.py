#!/usr/bin/env python3
"""FQ-302 real runner/HTTP/witness/dual nft/systemd; synthetic cellular ownership facts.

Physical identity, RF/PTT hardware, power sensors and NetworkManager behavior are NOT certified by
this fixture. Its snapshot adapter supplies two deterministic healthy WAN roles.
The fault, clocks, receipts, processes, expiry, persistence and reports are real.
"""
import json
from pathlib import Path
import subprocess
import time
import unittest

import integration_qualification_wan as kernel
from integration_qualification_safety import guard, cmd, until, REPO


from integration_qualification_cellular import DualKernel


class CellularCampaign(DualKernel):
    @classmethod
    def setUpClass(cls):
        guard()
        cmd('bash', str(REPO / 'scripts/setup-pcs-qualify.sh'), '--install')

    def campaign(self, break_http=False):
        ready = Path(self.temp.name) / 'session'
        output = Path(self.temp.name) / 'witness.jsonl'
        script = Path(self.temp.name) / 'runner.py'
        script.write_text('''
import sys,time,json
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/pcs')
import pcs_qualify as cli
import pcs_qualify_cellular as scenario
import pcs_qualify_safety as safety
import pcs_qualify_fault as fault
import pcs_qualify_rf as rf
# Explicit synthetic RF observation: x86 has no commissioned Raspberry Pi GPIO.
rf.observe=lambda: dict(gate="PASS", reason="rf_quiescent", direwolf="inactive",
    graywolf="inactive", recovery="installed", ptt_safe="PASS",
    consequence="bounded_inactive_engines")
from pcs_qualify_wan import Identity
namespace,index,ready=sys.argv[1:]
original=safety.command
def command(argv,*args,**kwargs):
    if argv[0]=='/usr/bin/systemd-run':
        argv=argv[:1]+['--property=NetworkNamespacePath=/run/netns/'+namespace]+argv[1:]
    return original(argv,*args,**kwargs)
safety.command=command
original_session=cli.Session
class Session(original_session):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        Path(ready).write_text(self.id)
cli.Session=Session
target=Identity('eth1',int(index),'02:00:00:00:00:01')
import subprocess
wifi_index=json.loads(subprocess.check_output(['ip','-j','link','show','wlan0']))[0]['ifindex']
wifi_target=Identity('wlan0',wifi_index,'02:00:00:00:00:02')
clock={'fault':None,'removed':None}
token=dict(session='/synthetic/active/cellular',profile='PRIVATE-PROFILE-CANARY')
def snapshot():
    present=fault.table() is not None
    now=time.monotonic()
    if present and clock['fault'] is None:clock['fault']=now
    if not present and clock['fault'] is not None and clock['removed'] is None:clock['removed']=now
    recovered=clock['removed'] is not None and now-clock['removed']>=20
    active=clock['fault'] is not None and now-clock['fault']>=35 and not recovered
    def row(kind,healthy,chosen,owned=False):
        return dict(type=kind,internet=healthy,internet6=True,selected=chosen,active=chosen,
                    owned=owned,link=True,address=True)
    rows=[row('ethernet',not present,not active),row('wifi',not present,False),row('cellular',active,active,active)]
    facts=dict(boot=scenario.boot_id(),owned={'cell':token} if active else {},suppressed=[],
        active=[dict(token,state=2,interface='wwan0')] if active else [],cellular_id='cell',
        cellular_profile='PRIVATE-PROFILE-CANARY',bearer_connected=active,modem_state=8,registration=1)
    return dict(fixed=('namespace-fixture',target,wifi_target),target=target,wifi_target=wifi_target,
        slots=[0,1],cell_slot=2,lan='10.42.0.0/24',wan='192.168.50.0/24',wifi_net='192.168.1.0/24',
        server='10.42.0.1',client='10.42.0.20',uplink=dict(internet=True,uplinks=rows),facts=facts,
        effective='cellular' if active else 'ethernet',failure_seconds=5,recovery_seconds=15,poll_seconds=1)
scenario.snapshot=snapshot
scenario.baseline_checks=lambda:'f'*40
scenario.checkpoint=lambda:{'fixture':True,'power':{'status':'ok'}}
scenario.assess=lambda evidence:'PASS'
original_collect=scenario.command
def collect(argv,*args,**kwargs):
    if argv[:2]==['/usr/bin/getent','ahostsv4']:return 'DNS-FIXTURE'
    return original_collect(argv,*args,**kwargs)
scenario.command=collect
if __name__ == '__main__':
    raise SystemExit(cli.campaign('FQ-302',90))
''')
        http = subprocess.Popen(['ip', 'netns', 'exec', self.router, 'python3', '-m', 'http.server',
                                 '80', '--bind', '10.42.0.1', '--directory', self.temp.name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        witness = None
        try:
            until(lambda: self.run_in(self.client, 'python3', '-c',
                  'import urllib.request; assert urllib.request.urlopen("http://10.42.0.1",timeout=1).status==200',
                  check=False).returncode == 0)
            cmd('systemd-run', '--quiet', '--collect', '--unit=pcs-qualify-campaign',
                '--property=Type=exec', '--property=RuntimeMaxSec=180s',
                '--property=MemoryMax=128M', '--property=CPUQuota=20%',
                '--property=NetworkNamespacePath=/run/netns/' + self.router,
                '--setenv=SSH_CONNECTION=10.42.0.20 50000 10.42.0.1 22',
                'python3', str(script), self.router, str(self.target.index), str(ready))
            until(ready.exists)
            session = ready.read_text()
            events = Path('/var/lib/pcs-qualification/sessions', session, 'events.jsonl')
            until(lambda: 'wan_waiting_for_witness' in events.read_text())
            witness = subprocess.Popen(['ip', 'netns', 'exec', self.client, 'python3',
                str(REPO / 'scripts/pcs_qualify_lan.py'), '--session', session,
                '--target', '10.42.0.1', '--source', '10.42.0.20', '--interface', 'lan',
                '--output', str(output), '--duration', '120'], stdout=subprocess.DEVNULL)
            if break_http:
                until(lambda: self.run_in(self.router, 'nft', 'list', 'table', 'inet', 'pcs_qualification',
                                         check=False).returncode == 0, 30)
                http.terminate()
                http.wait(timeout=5)
            manifest = events.with_name('session.json')
            until(lambda: json.loads(manifest.read_text()).get('complete') is True, 115)
            value = json.loads(manifest.read_text())
            self.assertEqual(value['result'], 'FAIL' if break_http else 'PASS', events.read_text())
            if not break_http:
                self.assertEqual(witness.wait(timeout=5), 0)
                rows = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertTrue(rows[-1]['complete'])
                self.assertGreater(len(rows), 50)
                imported = json.loads(cmd('pcs-qualify', 'witness', session, str(output)).stdout)
                self.assertEqual(imported['result'], 'PASS')
                self.assertTrue(imported['matched_receipts'])
                self.assertEqual(json.loads(manifest.read_text()), value)
                self.assertIn('Independent client HTTP sampling: **PASS**', events.with_name('report.md').read_text())
                # A damaged import cannot upgrade or rewrite the campaign verdict.
                damaged = output.with_name('incomplete.jsonl')
                damaged.write_bytes(output.read_bytes().rstrip(b'\n'))
                rejected = cmd('pcs-qualify', 'witness', session, str(damaged), check=False)
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(json.loads(manifest.read_text()), value)
                cmd('pcs-qualify', 'witness', session, str(output))
            self.assertNotEqual(self.run_in(self.router, 'nft', 'list', 'table', 'inet', 'pcs_qualification',
                                            check=False).returncode, 0)
            self.assertFalse(Path('/run/pcs-qualification/wan.json').exists())
            self.assertFalse(Path('/run/pcs-qualification/active.json').exists())
            for address in ('10.42.0.20', '10.42.0.1', '192.168.50.0', 'PRIVATE-PROFILE-CANARY', '/synthetic/active'):
                self.assertNotIn(address, events.read_text())
            # Preserve this run's independent client file alongside the session.
            import shutil
            shutil.copyfile(output, events.with_name('lan-witness-original.jsonl'))
            events.with_name('lan-witness-original.jsonl').chmod(0o600)
        finally:
            if witness is not None and witness.poll() is None:
                witness.terminate()
                witness.wait(timeout=5)
            if http.poll() is None:
                http.terminate()
                http.wait(timeout=5)
            cmd('systemctl', 'stop', 'pcs-qualify-campaign.service', check=False)
            self.run_in(self.router, 'pcs-qualify', 'cleanup')
            cmd('systemctl', 'stop', 'pcs-qualify-expiry.timer', 'pcs-qualify-expiry.service', check=False)

    def test_campaign_with_real_independent_lan_witness(self):
        self.campaign()

    def test_lan_http_outage_fails_and_restores(self):
        self.campaign(break_http=True)


if __name__ == '__main__':
    guard()
    # Kernel primitives are covered separately; run only the two new campaign cases.
    suite = unittest.TestSuite(CellularCampaign(name) for name in (
        'test_campaign_with_real_independent_lan_witness', 'test_lan_http_outage_fails_and_restores'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
