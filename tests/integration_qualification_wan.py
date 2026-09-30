#!/usr/bin/env python3
"""Real WAN primitive tests, confined to namespaces in a marked disposable VM.

Not a live FQ-301 runner. No appliance or host-network mutation is supported.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from integration_qualification_safety import guard, cmd, until, REPO
sys.path.insert(0, str(REPO / 'scripts'))
import pcs_qualify_wan as wan


class WanKernel(unittest.TestCase):
    def setUp(self):
        guard()
        self.token = uuid.uuid4().hex[:10]
        self.router, self.peer, self.client = ['fq-' + self.token + s for s in ('r', 'p', 'c')]
        self.session = uuid.uuid4().hex
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.unit = 'fq-wan-fixture-' + self.token
        self.addCleanup(lambda: cmd('systemctl', 'stop', self.unit + '.timer',
                                   self.unit + '.service', check=False))
        for ns in (self.router, self.peer, self.client):
            cmd('ip', 'netns', 'add', ns)
            self.addCleanup(lambda ns=ns: cmd('ip', 'netns', 'del', ns, check=False))
            cmd('ip', '-n', ns, 'link', 'set', 'lo', 'up')
        for local, ns, remote in [('eth1', self.peer, 'uplink'), ('eth0', self.client, 'lan')]:
            cmd('ip', '-n', self.router, 'link', 'add', local, 'type', 'veth',
                'peer', 'name', remote, 'netns', ns)
            for space, dev in ((self.router, local), (ns, remote)):
                cmd('ip', '-n', space, 'link', 'set', dev, 'up')
        for ns, dev, addr in [(self.router, 'eth1', '192.168.50.1/24'),
                              (self.peer, 'uplink', '192.168.50.2/24'),
                              (self.router, 'eth0', '10.42.0.1/24'),
                              (self.client, 'lan', '10.42.0.20/24'),
                              (self.peer, 'lo', '198.18.0.2/32'),
                              (self.peer, 'lo', '192.168.100.1/32')]:
            cmd('ip', '-n', ns, 'addr', 'add', addr, 'dev', dev)
        for ns, dev, addr in ((self.router, 'eth1', 'fd42::1/64'),
                              (self.peer, 'uplink', 'fd42::2/64')):
            cmd('ip', '-n', ns, '-6', 'addr', 'add', addr, 'dev', dev, 'nodad')
        cmd('ip', '-n', self.router, 'route', 'add', 'default', 'via', '192.168.50.2')
        cmd('ip', '-n', self.client, 'route', 'add', 'default', 'via', '10.42.0.1')
        cmd('ip', '-n', self.peer, 'route', 'add', '10.42.0.0/24', 'via', '192.168.50.1')
        self.run_in(self.router, 'sysctl', '-q', '-w', 'net.ipv4.ip_forward=1')
        self.target = wan.Identity('eth1', json.loads(
            cmd('ip', '-j', '-n', self.router, 'link', 'show', 'eth1').stdout)[0]['ifindex'],
            '02:00:00:00:00:01')
        self.assertTrue(self.ping(self.router, '198.18.0.2'))
        self.assertTrue(self.ping(self.client, '198.18.0.2'))

    def run_in(self, ns, *args, **kwargs):
        return cmd('ip', 'netns', 'exec', ns, *args, **kwargs)

    def nft(self, text, check=True):
        result = subprocess.run(['ip', 'netns', 'exec', self.router, 'nft', '-f', '-'],
                                input=text, text=True, capture_output=True, timeout=5)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def table(self):
        return json.loads(self.run_in(self.router, 'nft', '-j', 'list', 'table', 'inet', wan.TABLE).stdout)

    def plan(self, seconds=30):
        return wan.create_plan(self.session, self.target, seconds, '10.42.0.0/24', '192.168.50.0/24')

    def ping(self, ns, address):
        return self.run_in(ns, 'ping', '-n', '-c', '1', '-W', '1', address, check=False).returncode == 0

    def test_output_forward_ipv4_drop_preserves_local_ipv6_and_recovers(self):
        self.nft(self.plan())
        self.assertFalse(self.ping(self.router, '198.18.0.2'))
        self.assertFalse(self.ping(self.client, '198.18.0.2'))
        for ns, address in ((self.router, '192.168.50.2'), (self.router, '192.168.100.1'),
                            (self.client, '10.42.0.1'), (self.router, 'fd42::2')):
            self.assertTrue(self.ping(ns, address), address)
        self.nft(wan.delete_plan(self.table(), self.session))
        self.assertTrue(self.ping(self.router, '198.18.0.2'))
        self.assertTrue(self.ping(self.client, '198.18.0.2'))

    def test_kernel_expiry_without_runner_or_timer(self):
        self.nft(self.plan(5))  # nft exits; there is no userspace cleanup or refresh.
        self.assertFalse(self.ping(self.router, '198.18.0.2'))
        until(lambda: self.ping(self.client, '198.18.0.2'), 9)
        self.assertTrue(self.ping(self.router, '198.18.0.2'))
        self.nft(wan.delete_plan(self.table(), self.session))

    def test_collision_and_stale_handle_never_delete_replacement(self):
        self.nft(self.plan())
        deletion = wan.delete_plan(self.table(), self.session)
        self.assertNotEqual(self.nft(self.plan(), check=False).returncode, 0)
        self.nft(deletion)
        self.nft('create table inet pcs_qualification { comment "unrelated-owner"; }')
        before = self.table()
        self.assertNotEqual(self.nft(deletion, check=False).returncode, 0)
        with self.assertRaises(wan.HarnessError):
            wan.delete_plan(before, self.session)
        self.assertEqual(before, self.table())

    def test_independent_systemd_expiry_after_killed_producer(self):
        cleanup = Path(self.temp.name) / 'cleanup.py'
        cleanup.write_text(
            'import json,subprocess,sys\n'
            f'sys.path.insert(0,{str(REPO / "scripts")!r})\n'
            'import pcs_qualify_wan as w\n'
            'p=subprocess.run(["nft","-j","list","table","inet",w.TABLE],'
            'capture_output=True,text=True,check=True)\n'
            f'subprocess.run(["nft","-f","-"],input=w.delete_plan(json.loads(p.stdout),{self.session!r}),'
            'text=True,check=True)\n')
        cmd('systemd-run', '--quiet', '--collect', '--unit=' + self.unit,
            '--on-active=5s', '--timer-property=AccuracySec=100ms',
            '--property=TimeoutStartSec=10s', 'ip', 'netns', 'exec', self.router,
            'python3', str(cleanup))
        self.assertEqual(cmd('systemctl', 'is-active', self.unit + '.timer').stdout.strip(), 'active')
        producer = subprocess.Popen(['ip', 'netns', 'exec', self.router, 'python3', '-c',
            'import subprocess,time; subprocess.run(["nft","-f","-"],'
            f'input={self.plan(30)!r},text=True,check=True); time.sleep(60)'])
        try:
            until(lambda: self.run_in(self.router, 'nft', 'list', 'table', 'inet', wan.TABLE,
                                      check=False).returncode == 0, 3)
            producer.kill()
            producer.wait(timeout=5)
            self.assertFalse(self.ping(self.router, '198.18.0.2'))
            until(lambda: self.run_in(self.router, 'nft', 'list', 'table', 'inet', wan.TABLE,
                                      check=False).returncode != 0, 12)
            self.assertTrue(self.ping(self.router, '198.18.0.2'))
        finally:
            if producer.poll() is None:
                producer.kill()
                producer.wait(timeout=5)

    def test_production_firewalls_unchanged_and_prior_drop_not_bypassed(self):
        self.assertFalse(Path('/usr/local/sbin/pcs-uplink-management').exists())
        wg, api = (Path(self.temp.name) / name for name in ('wg.conf', 'api.conf'))
        wg.write_text('PCS_WG_ADDRESS=10.77.0.2/32\nPCS_WG_ALLOWED_IPS=10.77.0.1/32\nPCS_WG_ADMIN_SOURCES=10.77.0.1/32\n')
        api.write_text('PCS_API_PORT=9443\nPCS_API_ALLOWED_INTERFACE_SOURCES=eth0=10.42.0.0/24\n')
        for script in ('pcs-wireguard-firewall.sh', 'pcs-stats-api-firewall.sh'):
            self.run_in(self.router, 'env', 'PCS_WIREGUARD_CONFIG=' + str(wg),
                        'PCS_STATS_API_CONFIG=' + str(api), 'bash', str(REPO / 'scripts' / script), '--apply')
        self.nft('table inet fixture_security {\n chain output {\n type filter hook output priority -10; '
                 'policy accept;\n ip daddr 192.168.50.2 drop\n }\n}\n')
        names = ('pcs_wireguard', 'pcs_stats_api', 'fixture_security')
        before = [self.run_in(self.router, 'nft', '-j', 'list', 'table', 'inet', name).stdout for name in names]
        self.nft(self.plan())
        self.assertFalse(self.ping(self.router, '192.168.50.2'))
        self.nft(wan.delete_plan(self.table(), self.session))
        after = [self.run_in(self.router, 'nft', '-j', 'list', 'table', 'inet', name).stdout for name in names]
        self.assertEqual(before, after)


if __name__ == '__main__':
    guard()
    unittest.main()
