"""Linux unit tests for qualification boundaries, not appliance acceptance."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_state as state
    import pcs_qualify_observe as observe
    import pcs_qualify_safety as safety
    import pcs_qualify as cli


@unittest.skipIf(sys.platform == 'win32', 'qualification requires Linux')
class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = state.boot_id()
        self.power = {'version': 1, 'collected_at_epoch': 100, 'status': 'ok',
                      'energy_tracking': {'boot_id': self.boot},
                      'monitors': {r: {'status': 'ok', 'voltage': 12, 'current': 1, 'power': 12}
                                   for r in observe.ROLES}}
        row = {k: False for k in ('link', 'address', 'address6', 'active', 'selected', 'selected6', 'owned', 'suppressed', 'internet', 'internet6')}
        row['type'] = 'ethernet'
        self.uplink = {'version': 1, 'boot': self.boot, 'generated_at': 100, 'mode': 'auto', 'internet': True, 'uplinks': [row]}

    def test_canaries_and_exact_allowlist(self):
        for source in (self.power, self.uplink):
            source.update(password='SECRET-CANARY', coordinates='COORD-CANARY', error='ERROR-CANARY')
        self.power['monitors']['input']['error'] = 'SENSOR-CANARY'
        self.uplink['uplinks'][0].update(name='SSID-CANARY', addresses=['IP-CANARY'], profile='PROFILE-CANARY')
        raw = json.dumps([observe.sanitize_power(self.power, 101, self.boot),
                          observe.sanitize_uplink(self.uplink, 101, self.boot)])
        self.assertNotIn('CANARY', raw)
        # A secret substituted into an allowed typed field is rejected, not copied.
        self.power['status'] = 'SECRET-CANARY'
        with self.assertRaises(state.HarnessError):
            observe.sanitize_power(self.power, 101, self.boot)

    def test_freshness_schema_wrong_boot_and_nonfinite(self):
        for now, boot in ((99, self.boot), (111, self.boot), (101, 'other')):
            with self.subTest(now=now, boot=boot), self.assertRaises(state.HarnessError):
                observe.sanitize_power(self.power, now, boot)
        for value in (True, '12', float('nan'), float('inf'), 10001):
            self.power['monitors']['input']['voltage'] = value
            with self.subTest(value=value), self.assertRaises(state.HarnessError):
                observe.sanitize_power(self.power, 101, self.boot)

    def test_verdicts_do_not_pass_missing_evidence(self):
        evidence = {'issues': [], 'services': {u: 'active' for u in observe.SERVICES},
                    'power': observe.sanitize_power(self.power, 101, self.boot),
                    'uplink': observe.sanitize_uplink(self.uplink, 101, self.boot)}
        self.assertEqual(observe.assess(evidence), 'PASS')
        evidence['power']['status'] = 'warn'
        self.assertEqual(observe.assess(evidence), 'PASS WITH OBSERVATION')
        evidence['power']['status'] = 'bad'
        self.assertEqual(observe.assess(evidence), 'FAIL')
        evidence['issues'] = ['power_unavailable']
        self.assertEqual(observe.assess(evidence), 'INCONCLUSIVE')

    def test_collector_missing_malformed_timeout(self):
        p = self.root / 'bad.json'
        p.write_text('{secret garbage')
        with patch.object(observe, 'command', side_effect=state.HarnessError('timeout')):
            evidence = observe.checkpoint(p, self.root / 'missing')
        self.assertEqual(observe.assess(evidence), 'INCONCLUSIVE')
        self.assertNotIn('secret', json.dumps(evidence))
        with self.assertRaises(state.HarnessError):
            observe.command(['/usr/bin/python3', '-c', 'import time;time.sleep(5)'], timeout=0.05)
        with self.assertRaises(state.HarnessError):
            observe.command(['/usr/bin/python3', '-c', 'print("x"*100000)'])

    def test_private_paths_no_links_traversal_or_fifo(self):
        for value in ('../bad', '/', 'a' * 33, 'not-a-session'):
            with self.assertRaises(state.HarnessError):
                state.identifier(value)
        target = self.root / 'target'
        target.write_text('{}')
        link = self.root / 'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            state.read_json(link)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(state.HarnessError):
            state.read_json(fifo)
        target.write_text('x' * 100)
        with self.assertRaises(state.HarnessError):
            state.read_json(target, 10)

    def test_sessions_permissions_results_and_storage_bounds(self):
        session = state.Session('FQ-001', self.root / 'sessions')
        for result in state.RESULTS:
            session.finish(result, 'test_result')
            self.assertEqual(state.read_json(session.path / 'session.json')['result'], result)
            self.assertIn(result, (session.path / 'report.md').read_text())
        self.assertEqual(session.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((session.path / 'events.jsonl').stat().st_mode & 0o777, 0o600)
        rows = [json.loads(v) for v in (session.path / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([r['seq'] for r in rows], list(range(len(rows))))
        self.assertTrue(all({'utc', 'monotonic', 'boot_id'} <= r.keys() for r in rows))
        with patch.object(state, 'MAX_SESSIONS', 1), self.assertRaises(state.HarnessError):
            state.Session('FQ-001', self.root / 'sessions')
        with patch.object(state, 'MIN_FREE', 10**20), self.assertRaises(state.HarnessError):
            state.storage_ready(self.root / 'sessions')
        with patch.object(state, 'MAX_FILE', 8200), self.assertRaises(state.HarnessError):
            session.event('checkpoint', {'too_big': 'x' * 100})

    def test_campaign_lock_does_not_block_restore(self):
        runtime = state.private_dir(self.root / 'run')
        (runtime / 'marker').write_text('test')
        with state.lock(runtime / 'campaign.lock'):
            with self.assertRaises(state.HarnessError):
                with state.lock(runtime / 'campaign.lock'):
                    pass
            safety.restore(runtime)
        self.assertFalse((runtime / 'marker').exists())

    def test_brief_cleanup_overlap_never_kills_campaign(self):
        runtime = state.private_dir(self.root / 'run')
        state.atomic_json(runtime / 'active.json', {'invocation':'a'*32})
        (runtime / 'marker').write_text('marker')
        ready = threading.Event()
        def other_cleanup():
            with state.lock(runtime / 'mutation.lock'):
                ready.set()
                time.sleep(.2)
        worker = threading.Thread(target=other_cleanup)
        worker.start();self.assertTrue(ready.wait(2))
        def systemctl(args):
            return ('/run/systemd/transient/'+safety.CAMPAIGN if '--property=FragmentPath' in args else 'a'*32)
        try:
            with patch.object(safety,'command',side_effect=systemctl) as command:
                safety.restore(runtime)
                command.assert_not_called()
        finally:
            worker.join(2)
        self.assertFalse((runtime/'marker').exists())

    def test_campaign_cannot_kill_itself_on_prolonged_cleanup_contention(self):
        runtime = state.private_dir(self.root / 'run')
        state.atomic_json(runtime / 'active.json', {'invocation':'a'*32})
        with state.lock(runtime/'mutation.lock'), patch.dict(os.environ,INVOCATION_ID='a'*32), \
                patch.object(safety,'command') as command:
            with self.assertRaisesRegex(state.HarnessError,'cleanup_lock_contended'):
                safety.restore(runtime)
            command.assert_not_called()

    def test_restore_partial_manifest_and_symlink_target_survives(self):
        runtime = state.private_dir(self.root / 'run')
        outside = self.root / 'unrelated'
        outside.write_text('preserve')
        (runtime / 'marker').symlink_to(outside)
        (runtime / 'active.json').write_text('{malformed')
        safety.restore(runtime)
        safety.restore(runtime)
        self.assertEqual(outside.read_text(), 'preserve')

    def test_arm_failure_never_leaves_effect(self):
        runtime = state.private_dir(self.root / 'run')
        with patch.object(safety, 'command', side_effect=state.HarnessError('unavailable')):
            with self.assertRaises(state.HarnessError):
                safety.arm('a' * 32, 5, runtime)
        self.assertFalse((runtime / 'marker').exists())
        self.assertFalse((runtime / 'active.json').exists())

    def test_boot_cleanup_preserves_complete_and_aborts_unfinished(self):
        sessions = self.root / 'sessions'
        good = state.Session('FQ-001', sessions)
        good.finish('PASS', 'test_result')
        unfinished = state.Session('FQ-001', sessions)
        safety.boot_cleanup(sessions, self.root / 'run')
        self.assertEqual(state.read_json(good.path / 'session.json')['result'], 'PASS')
        self.assertEqual(state.read_json(unfinished.path / 'session.json')['result'], 'ABORTED')

    def test_unknown_and_ambiguous_control_fail_closed(self):
        self.assertFalse(safety.control_path({})['verified'])
        with patch.object(safety, 'command', return_value='[{"dev":"eth1"},{"dev":"eth2"}]'):
            self.assertFalse(safety.control_path({'SSH_CONNECTION': '1.2.3.4 22 5.6.7.8 22'})['verified'])
        self.assertFalse(safety.control_path({'SSH_CONNECTION': 'garbage'})['network_mutation_allowed'])

    def test_wireguard_control_resolves_outer_endpoint(self):
        with patch.object(safety, 'route', side_effect=['wg-pcs', 'eth1']) as route, \
                patch.object(Path, 'read_text', return_value='2'), \
                patch.object(safety, 'command', return_value='PUBLICKEY\t[2001:db8::1]:51820\n'):
            result = safety.control_path({'SSH_CONNECTION': '10.77.0.2 1234 10.77.0.3 22'})
        self.assertEqual(route.call_args_list[1].args, ('2001:db8::1',))
        self.assertEqual(result['transport'], 'wireguard')
        self.assertTrue(result['verified'])
        self.assertFalse(result['network_mutation_allowed'])

    def test_fixed_scenario_registry(self):
        self.assertEqual(set(cli.REGISTRY), {'FQ-001', 'FQ-002', 'FQ-301-v4', 'FQ-302', 'FQ-303'})

    def test_failed_report_cannot_be_claimed_as_complete_pass(self):
        session = state.Session('FQ-001', self.root / 'sessions')
        with patch.object(state, 'report', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                session.finish('PASS', 'test_result')
        self.assertFalse(state.read_json(session.path / 'session.json')['complete'])
        with self.assertRaises(state.HarnessError):
            state.report(session.path)

    def test_changed_invocation_is_not_killed(self):
        runtime = state.private_dir(self.root / 'run')
        state.atomic_json(runtime / 'active.json', {'invocation': 'a' * 32})
        with state.lock(runtime / 'mutation.lock'), patch.object(safety, 'command', return_value='changed') as command:
            with self.assertRaises(state.HarnessError):
                safety.restore(runtime)
        self.assertFalse(any('kill' in call.args[0] for call in command.call_args_list))

    def test_corrupt_persistent_manifest_does_not_prevent_other_recovery(self):
        sessions = self.root / 'sessions'
        corrupt = state.Session('FQ-001', sessions)
        other = state.Session('FQ-001', sessions)
        (corrupt.path / 'session.json').write_text('{SECRET-CANARY')
        safety.boot_cleanup(sessions, self.root / 'run')
        safety.boot_cleanup(sessions, self.root / 'run')
        self.assertEqual(state.read_json(corrupt.path / 'session.json')['result'], 'HARNESS ERROR')
        self.assertEqual(state.read_json(other.path / 'session.json')['result'], 'ABORTED')
        self.assertNotIn('CANARY', ''.join(p.read_text() for p in corrupt.path.iterdir()))

    def test_delayed_expiry_cannot_revoke_new_campaign(self):
        runtime = state.private_dir(self.root / 'run')
        state.atomic_json(runtime / 'active.json', {'session': 'b' * 32})
        (runtime / 'marker').write_text('new lease')
        safety.restore(runtime, expected_session='a' * 32)
        self.assertTrue((runtime / 'marker').exists())
        safety.restore(runtime, expected_session='b' * 32)
        self.assertFalse((runtime / 'marker').exists())


if __name__ == '__main__':
    unittest.main()
