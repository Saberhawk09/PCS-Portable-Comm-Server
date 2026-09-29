#!/usr/bin/env python3
"""Installed WAN lease crash tests, only in the marked disposable QEMU guest."""
import json
from pathlib import Path
import time
import unittest
import sys

import integration_qualification_wan as kernel
from integration_qualification_safety import guard, cmd, until, REPO


class InstalledWanLease(kernel.WanKernel):
    @classmethod
    def setUpClass(cls):
        guard()
        cmd('bash', str(REPO / 'scripts/setup-pcs-qualify.sh'), '--install')

    def setUp(self):
        super().setUp()
        self.addCleanup(self.stop_campaign)

    def stop_campaign(self):
        cmd('systemctl', 'stop', 'pcs-qualify-campaign.service', check=False)
        result = self.run_in(self.router, '/usr/local/sbin/pcs-qualify', 'cleanup', check=False)
        if result.returncode:
            # The inherited collision test deliberately leaves a foreign table.
            # Installed cleanup must refuse it; namespace teardown owns that fixture.
            tables = [row['table'] for row in self.table()['nftables'] if 'table' in row]
            self.assertEqual(tables[0]['comment'], 'unrelated-owner')
            self.assertFalse(Path('/run/pcs-qualification/wan.json').exists())
            self.assertEqual(result.returncode, 5)
        cmd('systemctl', 'stop', 'pcs-qualify-expiry.timer', 'pcs-qualify-expiry.service', check=False)

    def producer(self, boundary):
        ready = Path(self.temp.name) / 'ready'
        code = '''
import os,sys,time
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/pcs')
import pcs_qualify_safety as safety
import pcs_qualify_fault as fault
from pcs_qualify_state import *
from pcs_qualify_wan import Identity
namespace,boundary,index,ready=sys.argv[1:]
original_command=safety.command
def command(argv,*args,**kwargs):
    if argv[0]=='/usr/bin/systemd-run':
        argv=argv[:1]+['--property=NetworkNamespacePath=/run/netns/'+namespace]+argv[1:]
    return original_command(argv,*args,**kwargs)
safety.command=command
with lock(RUNTIME/'campaign.lock'):
    session=Session('FQ-301-v4')
    original_write=fault.atomic_json
    original_apply=fault.apply
    original_nft_command=fault.command
    def halt():
        Path(ready).write_text(session.id)
        time.sleep(300)
    def write(path,value):
        original_write(path,value)
        if boundary=='record' and path.name=='wan.json': halt()
        if boundary=='active' and path.name=='active.json' and value.get('state')=='active': halt()
    def apply(text,runtime):
        original_apply(text,runtime)
        if boundary=='commit' and text.startswith('create'): halt()
    def nft_command(argv,*args,**kwargs):
        value=original_nft_command(argv,*args,**kwargs)
        if boundary=='batch' and argv[:2]==['/usr/sbin/nft','-f']: halt()
        return value
    fault.atomic_json=write
    fault.apply=apply
    fault.command=nft_command
    fault.arm_wan(session.id,Identity('eth1',int(index),'02:00:00:00:00:01'),5,'10.42.0.0/24','192.168.50.0/24')
'''
        cmd('systemd-run', '--quiet', '--collect', '--unit=pcs-qualify-campaign',
            '--property=Type=exec', '--property=RuntimeMaxSec=60s',
            '--property=NetworkNamespacePath=/run/netns/' + self.router,
            'python3', '-c', code, self.router, boundary, str(self.target.index), str(ready))
        until(ready.exists, 10)
        return ready.read_text()

    def assert_expired(self, session):
        until(lambda: not Path('/run/pcs-qualification/wan.json').exists(), 25)
        self.assertFalse(Path('/run/pcs-qualification/active.json').exists())
        self.assertFalse(Path('/run/pcs-qualification/nft.batch').exists())
        self.assertNotEqual(self.run_in(self.router, 'nft', 'list', 'table', 'inet', 'pcs_qualification', check=False).returncode, 0)
        self.assertTrue(self.ping(self.router, '198.18.0.2'))
        self.run_in(self.router, '/usr/local/sbin/pcs-qualify', 'boot-cleanup')
        record = json.loads(Path('/var/lib/pcs-qualification/sessions', session, 'session.json').read_text())
        self.assertTrue(record['complete'])
        self.assertEqual(record['result'], 'ABORTED')
        self.assertEqual(record['scenario'], 'FQ-301-v4')

    def test_installed_expiry_after_kill_at_ownership_record(self):
        session = self.producer('record')
        cmd('systemctl', 'kill', '--signal=SIGKILL', 'pcs-qualify-campaign.service')
        self.assert_expired(session)

    def test_installed_expiry_after_kill_at_nft_commit(self):
        session = self.producer('commit')
        self.assertFalse(self.ping(self.router, '198.18.0.2'))
        cmd('systemctl', 'kill', '--signal=SIGKILL', 'pcs-qualify-campaign.service')
        self.assert_expired(session)

    def test_installed_expiry_kills_stopped_mutation_owner(self):
        session = self.producer('active')
        cmd('systemctl', 'kill', '--signal=SIGSTOP', 'pcs-qualify-campaign.service')
        self.assert_expired(session)

    def test_kill_with_batch_file_open_cleans_fixed_artifact(self):
        session = self.producer('batch')
        self.assertTrue(Path('/run/pcs-qualification/nft.batch').exists())
        cmd('systemctl', 'kill', '--signal=SIGKILL', 'pcs-qualify-campaign.service')
        self.assert_expired(session)

    def test_installer_refuses_new_unowned_module(self):
        manifest = Path('/var/lib/pcs-qualification/install.sha256')
        original = manifest.read_bytes()
        target = Path('/usr/local/lib/pcs/pcs_qualify_fault.py')
        original_target = target.read_bytes()
        try:
            manifest.write_bytes(b''.join(row for row in original.splitlines(keepends=True)
                                          if b'/pcs_qualify_fault.py' not in row))
            result = cmd('bash', str(REPO / 'scripts/setup-pcs-qualify.sh'), '--install', check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), original_target)
        finally:
            manifest.write_bytes(original)


if __name__ == '__main__':
    guard()
    if sys.argv[1:] == ['--prepare-boot']:
        sys.path.insert(0, str(REPO / 'scripts'))
        import pcs_qualify_state as state
        import pcs_qualify_fault as fault
        from pcs_qualify_wan import Identity, owned_handle
        record = Path('/root/qualification-wan-boot.json')
        assert not record.exists(), 'Preserve prior boot evidence before repeating'
        unused_index = 999999
        assert all(row['ifindex'] != unused_index for row in json.loads(cmd('ip', '-j', 'link').stdout))
        with state.lock(state.RUNTIME / 'campaign.lock'):
            session = state.Session('FQ-301-v4')
            fault.arm_wan(session.id, Identity('eth1', unused_index, '02:00:00:00:00:01'),
                          120, '10.42.0.0/24', '192.168.50.0/24')
            record.write_text(json.dumps({'boot_before': state.boot_id(), 'session': session.id,
                'table_handle_before': owned_handle(fault.table(), session.id)}, indent=2))
        print('Prepared harmless unused-ifindex fault; reboot the disposable guest now.')
    elif sys.argv[1:] == ['--verify-boot']:
        sys.path.insert(0, str(REPO / 'scripts'))
        import pcs_qualify_state as state
        import pcs_qualify_fault as fault
        record = Path('/root/qualification-wan-boot.json')
        evidence = json.loads(record.read_text())
        assert evidence['boot_before'] != state.boot_id(), 'Actual reboot required'
        assert fault.table() is None
        assert all(not (state.RUNTIME / name).exists() for name in ('active.json', 'marker', 'wan.json'))
        session = state.read_json(state.SESSIONS / evidence['session'] / 'session.json')
        assert session['scenario'] == 'FQ-301-v4' and session['result'] == 'ABORTED' and session['complete'] is True
        cmd('systemctl', 'is-active', 'pcs-qualify-cleanup.service')
        evidence.update(boot_after=state.boot_id(), passed=True)
        record.write_text(json.dumps(evidence, indent=2))
        print(record.read_text())
    else:
        unittest.main()
