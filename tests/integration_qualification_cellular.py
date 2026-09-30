#!/usr/bin/env python3
"""FQ-302 dual-fault kernel/lease tests, marked disposable VM only."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import integration_qualification_wan as kernel
from integration_qualification_safety import guard,cmd,until,REPO
sys.path.insert(0,str(REPO/'scripts'))
import pcs_qualify_wan as wan

class DualKernel(kernel.WanKernel):
    def setUp(self):
        super().setUp()
        cmd('ip','-n',self.router,'link','add','wlan0','type','veth','peer','name','wifi-peer','netns',self.peer)
        for ns,dev,addr in ((self.router,'wlan0','192.168.1.1/24'),(self.peer,'wifi-peer','192.168.1.2/24')):
            cmd('ip','-n',ns,'addr','add',addr,'dev',dev);cmd('ip','-n',ns,'link','set',dev,'up')
        cmd('ip','-n',self.peer,'addr','add','198.18.1.2/32','dev','lo')
        cmd('ip','-n',self.router,'route','add','198.18.1.2/32','via','192.168.1.2','dev','wlan0')
        self.wifi=wan.Identity('wlan0',json.loads(cmd('ip','-j','-n',self.router,'link','show','wlan0').stdout)[0]['ifindex'],'02:00:00:00:00:02')
        self.assertTrue(self.ping(self.router,'198.18.1.2'))
    def dual(self):
        return wan.create_cellular_plan(self.session,self.target,self.wifi,60,'10.42.0.0/24','192.168.50.0/24','192.168.1.0/24')
    def test_both_paths_drop_lan_local_ipv6_preserved_and_restore(self):
        self.nft(self.dual())
        self.assertFalse(self.ping(self.router,'198.18.0.2'))
        self.assertFalse(self.ping(self.router,'198.18.1.2'))
        for ip in ('10.42.0.20','192.168.50.2','192.168.1.2','fd42::2'):
            self.assertTrue(self.ping(self.router,ip))
        self.nft(wan.delete_plan(self.table(),self.session,wan.CELLULAR_OWNER))
        self.assertTrue(self.ping(self.router,'198.18.0.2'));self.assertTrue(self.ping(self.router,'198.18.1.2'))
    def test_second_target_transaction_error_cannot_leave_first_fault(self):
        self.assertNotEqual(self.nft(self.dual()+'add element inet pcs_qualification blocked { invalid_index }\n',check=False).returncode,0)
        self.assertNotEqual(self.run_in(self.router,'nft','list','table','inet',wan.TABLE,check=False).returncode,0)
        self.assertTrue(self.ping(self.router,'198.18.0.2'));self.assertTrue(self.ping(self.router,'198.18.1.2'))
    def test_installed_lease_cleans_both_after_killed_owner(self):
        cmd('bash',str(REPO/'scripts/setup-pcs-qualify.sh'),'--install')
        ready=Path(self.temp.name)/'ready'
        script=Path(self.temp.name)/'producer.py'
        script.write_text('''
import sys,time
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/pcs')
import pcs_qualify_safety as safety
from pcs_qualify_state import Session,RUNTIME,lock
from pcs_qualify_wan import Identity
from pcs_qualify_fault import arm_cellular
ns,eth,wifi,ready=sys.argv[1:]
original=safety.command
def command(argv,*args,**kwargs):
    if argv[0]=='/usr/bin/systemd-run':argv=argv[:1]+['--property=NetworkNamespacePath=/run/netns/'+ns]+argv[1:]
    return original(argv,*args,**kwargs)
safety.command=command
with lock(RUNTIME/'campaign.lock'):
    session=Session('FQ-302')
    arm_cellular(session.id,Identity('eth1',int(eth),'02:00:00:00:00:01'),Identity('wlan0',int(wifi),'02:00:00:00:00:02'),60,'10.42.0.0/24','192.168.50.0/24','192.168.1.0/24')
    Path(ready).write_text(session.id)
    time.sleep(300)
''')
        cmd('systemd-run','--quiet','--collect','--unit=pcs-qualify-campaign',
            '--property=NetworkNamespacePath=/run/netns/'+self.router,
            'python3',str(script),self.router,str(self.target.index),str(self.wifi.index),str(ready))
        try:
            until(ready.exists,12)
            self.assertFalse(self.ping(self.router,'198.18.0.2'));self.assertFalse(self.ping(self.router,'198.18.1.2'))
            cmd('systemctl','kill','--signal=SIGKILL','pcs-qualify-campaign.service')
            until(lambda:not Path('/run/pcs-qualification/wan.json').exists(),85)
            self.assertTrue(self.ping(self.router,'198.18.0.2'));self.assertTrue(self.ping(self.router,'198.18.1.2'))
            until(lambda:cmd('systemctl','show','--value','--property=ActiveState','pcs-qualify-expiry.service').stdout.strip()=='inactive')
            self.run_in(self.router,'pcs-qualify','boot-cleanup')
            record=json.loads(Path('/var/lib/pcs-qualification/sessions',ready.read_text(),'session.json').read_text())
            self.assertEqual(record['result'],'ABORTED');self.assertTrue(record['complete'])
        finally:
            cmd('systemctl','stop','pcs-qualify-campaign.service',check=False)
            self.run_in(self.router,'pcs-qualify','cleanup',check=False)
            cmd('systemctl','stop','pcs-qualify-expiry.timer','pcs-qualify-expiry.service',check=False)

if __name__=='__main__':
    guard();unittest.main()
