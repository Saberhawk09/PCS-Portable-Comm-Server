#!/usr/bin/env python3
"""Destructive ONLY to an explicitly marked disposable QEMU guest.

Run --run, then --prepare-boot, reboot the guest, then --verify-boot.
Never run on PCS. No production WAN injection code is exercised or installed.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import pcs_qualify_state as state
import pcs_qualify_safety as safety

MARKER = Path('/etc/pcs-qualification-disposable')
RUNTIME = state.RUNTIME
SESSIONS = state.SESSIONS
EVIDENCE = Path('/root/qualification-acceptance.json')


def guard():
    if (os.geteuid() != 0 or not MARKER.is_file() or
            MARKER.read_text().strip() != 'local-qemu-acceptance-only' or
            subprocess.run(['systemd-detect-virt'], capture_output=True, text=True).stdout.strip() not in ('qemu', 'kvm')):
        raise SystemExit('REFUSED: explicitly marked disposable QEMU guest required')


def cmd(*args, check=True, timeout=30):
    return subprocess.run(args, text=True, capture_output=True, check=check, timeout=timeout)


def until(predicate, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(0.1)
    raise AssertionError('deadline exceeded')


class RealSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard()
        cmd('bash', str(REPO / 'scripts/setup-pcs-qualify.sh'), '--install')

    def tearDown(self):
        cmd('pcs-qualify', 'cleanup')
        cmd('systemctl', 'stop', 'pcs-qualify-expiry.timer', check=False)
        cmd('systemctl', 'stop', 'pcs-qualify-expiry.service', check=False)
        cmd('systemctl', 'stop', 'pcs-qualify-campaign.service', check=False)

    def test_01_real_timer_and_firewall_unchanged(self):
        before = cmd('nft', '-j', 'list', 'ruleset').stdout
        result = cmd('pcs-qualify', 'run', 'FQ-002')
        self.assertIn('"result": "PASS"', result.stdout)
        self.assertFalse((RUNTIME / 'marker').exists())
        self.assertEqual(before, cmd('nft', '-j', 'list', 'ruleset').stdout)

    def test_02_kill9_at_each_lease_commit_boundary(self):
        for phase in ('prepared', 'marker', 'active'):
            with self.subTest(phase=phase):
                rendezvous = Path('/run/qualification-test-ready')
                rendezvous.unlink(missing_ok=True)
                # Test-only instrumentation around actual disk commits, not a production hook.
                code = '''
import sys,time
sys.path.insert(0, '/usr/local/lib/pcs')
import pcs_qualify_safety as s
from pcs_qualify_state import *
from pathlib import Path
phase=sys.argv[1]
with lock(RUNTIME/'campaign.lock'):
    session=Session('FQ-002')
    original=s.atomic_json
    def write(path,data):
        original(path,data)
        point='marker' if path.name=='marker' else data.get('state')
        if point==phase:
            Path('/run/qualification-test-ready').write_text(session.id)
            time.sleep(60)
    s.atomic_json=write
    s.arm(session.id,5)
'''
                runner = subprocess.Popen(['/usr/bin/python3', '-c', code, phase])
                try:
                    until(rendezvous.exists)
                    session = rendezvous.read_text()
                    runner.kill()
                    runner.wait(timeout=5)
                    if phase == 'prepared':
                        self.assertFalse((RUNTIME / 'marker').exists())
                        # No effect before timer arming. Startup recovers the prepared record.
                        cmd('pcs-qualify', 'boot-cleanup')
                    else:
                        until(lambda: not (RUNTIME / 'active.json').exists())
                        until(lambda: state.read_json(SESSIONS / session / 'session.json').get('complete') is True)
                    self.assertFalse((RUNTIME / 'marker').exists())
                    self.assertEqual(state.read_json(SESSIONS / session / 'session.json')['result'], 'ABORTED')
                finally:
                    if runner.poll() is None: runner.kill(); runner.wait()
                    rendezvous.unlink(missing_ok=True)
                    cmd('systemctl', 'stop', 'pcs-qualify-expiry.timer', check=False)
                    cmd('systemctl', 'stop', 'pcs-qualify-expiry.service', check=False)

    def test_03_stopped_runner_concurrency_and_independent_cleanup(self):
        runner = subprocess.Popen(['pcs-qualify', 'run', 'FQ-001', '--duration', '30'], stdout=subprocess.PIPE, text=True)
        try:
            session = json.loads(runner.stdout.readline())['session']
            until(lambda: (RUNTIME / 'marker').exists())
            runner.send_signal(signal.SIGSTOP)
            other = cmd('pcs-qualify', 'run', 'FQ-002', check=False)
            self.assertEqual(other.returncode, 5)
            cmd('pcs-qualify', 'cleanup')
            self.assertFalse((RUNTIME / 'marker').exists())
            runner.send_signal(signal.SIGCONT)
            self.assertEqual(runner.wait(timeout=15), 3)
            self.assertEqual(state.read_json(SESSIONS / session / 'session.json')['result'], 'ABORTED')
        finally:
            if runner.poll() is None:
                runner.kill(); runner.wait()
            runner.stdout.close()

    def test_04_corrupt_state_restores_only_owned_artifacts(self):
        sentinel = Path('/run/qualification-unrelated-sentinel')
        sentinel.write_text('preserve')
        try:
            (RUNTIME / 'active.json').write_text('{unparseable')
            (RUNTIME / 'marker').symlink_to(sentinel)
            for _ in range(2): cmd('pcs-qualify', 'cleanup')
            self.assertEqual(sentinel.read_text(), 'preserve')
        finally:
            sentinel.unlink()

    def test_04b_stopped_mutation_owner_cannot_block_expiry(self):
        ready = Path('/run/qualification-test-ready')
        ready.unlink(missing_ok=True)
        code = '''
import sys,time
sys.path.insert(0, '/usr/local/lib/pcs')
import pcs_qualify_safety as s
from pcs_qualify_state import *
from pathlib import Path
with lock(RUNTIME/'campaign.lock'):
    session=Session('FQ-002')
    original=s.atomic_json
    def write(path,data):
        original(path,data)
        if path.name=='marker':
            Path('/run/qualification-test-ready').write_text(session.id)
            time.sleep(60)
    s.atomic_json=write
    s.arm(session.id,5)
'''
        runner = subprocess.Popen(['systemd-run', '--quiet', '--collect', '--wait', '--pipe',
                                   '--unit=pcs-qualify-campaign.service', '/usr/bin/python3', '-c', code],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            until(ready.exists)
            session = ready.read_text()
            cmd('systemctl', 'kill', '--signal=SIGSTOP', '--kill-whom=all', 'pcs-qualify-campaign.service')
            until(lambda: not (RUNTIME / 'marker').exists())
            self.assertNotEqual(runner.wait(timeout=10), 0)
            until(lambda: state.read_json(SESSIONS / session / 'session.json')['complete'])
            self.assertEqual(state.read_json(SESSIONS / session / 'session.json')['result'], 'ABORTED')
        finally:
            cmd('systemctl', 'kill', '--signal=SIGKILL', 'pcs-qualify-campaign.service', check=False)
            if runner.poll() is None: runner.kill(); runner.wait()
            ready.unlink(missing_ok=True)

    def test_05_real_nft_coexistence_and_kernel_timeout(self):
        ns = 'qualification-proof'
        cmd('ip', 'netns', 'add', ns)
        config = tempfile.TemporaryDirectory()
        try:
            self.assertFalse(Path('/usr/local/sbin/pcs-uplink-management').exists(),
                             'Refuse an installed management reconciler in this fixture')
            cmd('ip', '-n', ns, 'link', 'set', 'lo', 'up')
            def nft(script):
                subprocess.run(['ip', 'netns', 'exec', ns, 'nft', '-f', '-'], input=script,
                               text=True, check=True, capture_output=True)
            wg = Path(config.name) / 'wireguard.conf'
            api = Path(config.name) / 'api.conf'
            wg.write_text('PCS_WG_ADDRESS=10.77.0.2/32\nPCS_WG_ALLOWED_IPS=10.77.0.1/32\nPCS_WG_ADMIN_SOURCES=10.77.0.1/32\n')
            api.write_text('PCS_API_PORT=9443\nPCS_API_ALLOWED_INTERFACE_SOURCES=eth0=10.42.0.0/24\n')
            for script in ('pcs-wireguard-firewall.sh', 'pcs-stats-api-firewall.sh'):
                cmd('ip', 'netns', 'exec', ns, 'env', 'PCS_WIREGUARD_CONFIG=' + str(wg),
                    'PCS_STATS_API_CONFIG=' + str(api), 'bash', str(REPO / 'scripts' / script), '--apply')
            baseline = cmd('ip', 'netns', 'exec', ns, 'nft', '-j', 'list', 'table', 'inet', 'pcs_wireguard').stdout
            # All names and addresses below are test fixtures confined to this namespace.
            nft('table inet pcs_qualification {\n set blocked {\n type ipv4_addr; flags timeout; timeout 3s;\n }\n'
                'chain output {\n type filter hook output priority -5; policy accept;\n ip daddr @blocked drop\n }\n}\n'
                'add element inet pcs_qualification blocked { 127.0.0.1 timeout 3s }\n')
            self.assertNotEqual(cmd('ip', 'netns', 'exec', ns, 'ping', '-c', '1', '-W', '1', '127.0.0.1', check=False).returncode, 0)
            # nft process has exited; kernel must expire the element without any runner.
            until(lambda: cmd('ip', 'netns', 'exec', ns, 'ping', '-c', '1', '-W', '1', '127.0.0.1', check=False).returncode == 0, 8)
            cmd('ip', 'netns', 'exec', ns, 'nft', 'delete', 'table', 'inet', 'pcs_qualification')
            self.assertEqual(baseline, cmd('ip', 'netns', 'exec', ns, 'nft', '-j', 'list', 'table', 'inet', 'pcs_wireguard').stdout)
            cmd('ip', 'netns', 'exec', ns, 'nft', 'list', 'table', 'inet', 'pcs_stats_api')
        finally:
            cmd('ip', 'netns', 'del', ns)
            config.cleanup()

    def test_06_repeat_install_check_rollback_remove(self):
        installer = str(REPO / 'scripts/setup-pcs-qualify.sh')
        cmd('bash', installer, '--install')
        cmd('bash', installer, '--check')
        manifest = Path('/var/lib/pcs-qualification/install.sha256').read_bytes()
        source = REPO / 'scripts/pcs_qualify_witness.py'
        hidden = source.with_suffix('.temporarily-absent')
        source.rename(hidden)
        try:
            self.assertNotEqual(cmd('bash', installer, '--install', check=False).returncode, 0)
            self.assertEqual(manifest, Path('/var/lib/pcs-qualification/install.sha256').read_bytes())
            cmd('bash', installer, '--check')
        finally:
            hidden.rename(source)
        preserved = sorted(p.name for p in SESSIONS.iterdir())
        cmd('bash', installer, '--remove')
        cmd('bash', installer, '--remove')
        self.assertEqual(preserved, sorted(p.name for p in SESSIONS.iterdir()))
        self.assertFalse(Path('/usr/local/sbin/pcs-qualify').exists())
        cmd('bash', installer, '--install')
        self.assertEqual(RUNTIME.stat().st_mode & 0o777, 0o700)
        self.assertEqual(SESSIONS.stat().st_mode & 0o777, 0o700)

    def test_07_observation_actual_cli_with_sanitized_cache_fixtures(self):
        from pcs_qualify_observe import ROLES
        directories = [Path('/run/pcs-power-monitor'), Path('/run/pcs-uplink-manager')]
        units = ['pcs-power-monitor.service', 'pcs-uplink-manager.service']
        for directory, unit in zip(directories, units):
            self.assertFalse(directory.exists(), 'Fixture collision: refuse to touch existing PCS state')
            self.assertFalse(Path('/etc/systemd/system', unit).exists())
            self.assertFalse(Path('/usr/lib/systemd/system', unit).exists())
        baseline = cmd('nft', '-j', 'list', 'ruleset').stdout
        stop = threading.Event()
        mode = ['ok']
        def update():
            while not stop.is_set():
                now = time.time() - (100 if mode[0] == 'stale' else 0)
                power = {'version': 1, 'collected_at_epoch': now, 'status': 'bad' if mode[0] == 'bad' else 'ok',
                         'energy_tracking': {'boot_id': state.boot_id()}, 'password': 'SECRET-CANARY',
                         'monitors': {r: {'status': 'ok', 'voltage': 12, 'current': 1, 'power': 12,
                                          'error': 'RAW-CANARY'} for r in ROLES}}
                row = {k: False for k in ('link', 'address', 'address6', 'active', 'selected', 'selected6', 'owned', 'suppressed', 'internet', 'internet6')}
                row.update(type='ethernet', addresses=['ADDRESS-CANARY'], name='SSID-CANARY')
                uplink = {'version': 1, 'generated_at': now, 'boot': state.boot_id(), 'mode': 'auto',
                          'internet': True, 'uplinks': [row], 'coordinates': 'COORD-CANARY'}
                for directory, value in zip(directories, (power, uplink)):
                    state.atomic_json(directory / 'status.json', value)
                stop.wait(0.5)
        thread = threading.Thread(target=update, daemon=True)
        try:
            for directory, unit in zip(directories, units):
                directory.mkdir()
                cmd('systemd-run', '--quiet', '--collect', '--unit=' + unit, '/usr/bin/sleep', '300')
            thread.start()
            until(lambda: all((p / 'status.json').exists() for p in directories))
            for condition, expected, code in [('ok', 'PASS', 0), ('stale', 'INCONCLUSIVE', 2), ('bad', 'FAIL', 1)]:
                mode[0] = condition
                time.sleep(0.7)
                run = cmd('pcs-qualify', 'run', 'FQ-001', '--duration', '5', check=False)
                self.assertEqual(run.returncode, code, run.stdout + run.stderr)
                session = json.loads(run.stdout.splitlines()[0])['session']
                record = state.read_json(SESSIONS / session / 'session.json')
                self.assertEqual(record['result'], expected)
                self.assertTrue(record['complete'])
                for path in (SESSIONS / session).iterdir():
                    self.assertNotIn('CANARY', path.read_text())
                for unit in units: cmd('systemctl', 'is-active', unit)
                self.assertEqual(baseline, cmd('nft', '-j', 'list', 'ruleset').stdout)
            missing = cmd('pcs-qualify', 'witness', session, '/no-such-witness', check=False)
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(state.read_json(SESSIONS / session / 'witness.json')['result'], 'INCONCLUSIVE')
        finally:
            stop.set()
            if thread.is_alive(): thread.join(timeout=3)
            for unit in units: cmd('systemctl', 'stop', unit, check=False)
            for directory in directories:
                (directory / 'status.json').unlink(missing_ok=True)
                if directory.exists(): directory.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['--run', '--prepare-boot', '--verify-boot'])
    # Accept literal action after '--' to avoid ambiguous option parsing.
    args = parser.parse_args()
    guard()
    if args.action == '--run':
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RealSafety))
        EVIDENCE.write_text(json.dumps({'tests': result.testsRun, 'passed': result.wasSuccessful(),
                                       'boot': state.boot_id(), 'systemd': cmd('systemctl', '--version').stdout.splitlines()[0],
                                       'nft': cmd('nft', '--version').stdout.strip()}, indent=2))
        return not result.wasSuccessful()
    if args.action == '--prepare-boot':
        session = state.Session('FQ-002')
        safety.arm(session.id, 300)
        evidence = json.loads(EVIDENCE.read_text())
        evidence['interrupted_session'] = session.id
        EVIDENCE.write_text(json.dumps(evidence, indent=2))
        print('Prepared. Reboot this disposable guest externally, then --verify-boot.')
    else:
        evidence = json.loads(EVIDENCE.read_text())
        assert evidence['boot'] != state.boot_id(), 'Actual guest reboot required'
        assert not (RUNTIME / 'marker').exists()
        assert not (RUNTIME / 'active.json').exists()
        session = state.read_json(SESSIONS / evidence['interrupted_session'] / 'session.json')
        assert session['result'] == 'ABORTED' and session['complete'] is True
        cmd('systemctl', 'is-active', 'pcs-qualify-cleanup.service')
        evidence['boot_cleanup_passed'] = True
        evidence['new_boot'] = state.boot_id()
        EVIDENCE.write_text(json.dumps(evidence, indent=2))
        print(EVIDENCE.read_text())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
