import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_rf as rf
    import pcs_qualify_scenario as scenario
    import pcs_qualify_state as state
    import pcs_qualify_safety as safety
    import pcs_qualify as cli
    from pcs_qualify_observe import command as bounded_command

PTT = ('gpiochip0 6\t"GPIO6"\toutput bias=pull-down consumer="pcs-ptt-safe"\n'
       'PCS APRS PTT guard holds gpiochip0 line 6 low.\n')
LOW = ' 6: op dl pd | lo // GPIO6 = output\n'


@unittest.skipIf(sys.platform == 'win32', 'Linux collectors')
class RFSafety(unittest.TestCase):
    def setUp(self):
        idle = dict(LoadState='loaded', ActiveState='inactive', SubState='dead',
                    MainPID='0', ControlPID='0', Job='')
        self.rows = [dict(idle) for _ in rf.UNITS]
        self.rows[2].update(ActiveState='active', SubState='running', MainPID='123')
        self.ptt, self.level = PTT, LOW
        self.calls = []

    def command(self, argv):
        self.calls.append(argv)
        if argv[0] == '/usr/bin/systemctl':
            self.assertEqual(argv[1:3], ['show', '--property=' + ','.join(rf.FIELDS)])
            return ''.join(f'{k}={v}\n' for k, v in self.rows[rf.UNITS.index(argv[-1])].items())
        if argv == ['/usr/local/sbin/pcs-aprs-ptt-safe', '--check']:
            if isinstance(self.ptt, Exception):
                raise self.ptt
            return self.ptt
        self.assertEqual(argv, ['/usr/bin/pinctrl', 'get', '6'])
        return self.level

    def observe(self):
        with patch.object(rf, 'command', side_effect=self.command):
            return rf.observe()

    def test_installed_recovery_inactive_engines_verified_ptt_pass(self):
        self.assertEqual(self.observe()['gate'], 'PASS')

    def test_bcm2711_write_only_drive_still_requires_measured_low_and_guard(self):
        self.level = LOW.replace('dl', '--')
        self.assertEqual(self.observe()['gate'], 'PASS')
        for level in (self.level.replace('| lo', '| hi'), self.level.replace('| lo', '| --'),
                      self.level.replace('op', 'ip'), self.level.replace('pd', 'pu'),
                      LOW.replace('dl', 'dh'), LOW.replace('dl', '??')):
            with self.subTest(level=level):
                self.level = level
                self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.level = LOW.replace('dl', '--')
        self.ptt = PTT.replace('pcs-ptt-safe', 'DIREWOLF')
        self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.ptt = PTT
        self.rows[0].update(ActiveState='active', SubState='running', MainPID='50')
        self.assertEqual(self.observe()['gate'], 'BLOCKED')

    def test_active_direwolf_blocks_without_ptt_action(self):
        self.rows[0].update(ActiveState='active', SubState='running', MainPID='50')
        self.assertEqual(self.observe()['reason'], 'rf_engine_active')
        self.assertTrue(all(c[1] == 'show' for c in self.calls))

    def test_unknown_error_transitional_pending_and_residual_engine_block(self):
        original = copy.deepcopy(self.rows)
        for field, value in [('ActiveState', 'unknown'), ('ActiveState', 'failed'),
                             ('ActiveState', 'activating'), ('LoadState', 'not-found'),
                             ('MainPID', '2'), ('ControlPID', '2'), ('Job', '1/start')]:
            with self.subTest(field=field, value=value):
                self.rows = copy.deepcopy(original)
                self.rows[0][field] = value
                self.assertEqual(self.observe()['gate'], 'BLOCKED')
        with patch.object(rf, 'command', side_effect=rf.HarnessError('collector_failed')):
            self.assertEqual(rf.observe()['gate'], 'BLOCKED')

    def test_graywolf_active_or_ambiguous_blocks_absent_allowed(self):
        self.rows[1]['ActiveState'] = 'active'
        self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.rows[1]['ActiveState'] = 'unknown'
        self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.rows[1].update(ActiveState='inactive', LoadState='not-found')
        self.assertEqual(self.observe()['gate'], 'PASS')

    def test_inflight_or_queued_recovery_blocks(self):
        self.rows[3].update(ActiveState='activating', SubState='start', MainPID='8')
        self.assertEqual(self.observe()['reason'], 'rf_recovery_not_idle')
        self.rows[3].update(ActiveState='inactive', SubState='dead', MainPID='0', Job='8/start')
        self.assertEqual(self.observe()['gate'], 'BLOCKED')

    def test_ptt_failure_timeout_malformed_high_wrong_gpio_or_owner_blocks(self):
        for value in (rf.HarnessError('collector_failed'), rf.HarnessError('collector_unavailable'),
                      '', PTT + 'secret\n', PTT.replace('gpiochip0', 'gpiochip1'),
                      PTT.replace('GPIO6', 'GPIO7'), PTT.replace('pcs-ptt-safe', 'DIREWOLF'),
                      PTT.replace('output', 'input'), PTT.replace('output', 'output active-low'),
                      PTT.replace('6', '7')):
            with self.subTest(value=str(value)):
                self.ptt = value
                self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.ptt = PTT
        for value in ('', LOW.replace('lo', 'hi'), LOW + 'garbage\n',
                      LOW.replace('op', 'ip'), LOW.replace('pd', 'pu'),
                      '6: malformed | lo // malformed'):
            self.level = value
            self.assertEqual(self.observe()['gate'], 'BLOCKED')

    def test_guard_must_be_running_and_stable(self):
        self.rows[2]['MainPID'] = '0'
        self.assertEqual(self.observe()['gate'], 'BLOCKED')
        self.rows[2]['MainPID'] = '123'
        def changed(argv):
            value = self.command(argv)
            if argv[0].endswith('pinctrl'):
                self.rows[2]['MainPID'] = '124'
            return value
        with patch.object(rf, 'command', side_effect=changed):
            self.assertEqual(rf.observe()['reason'], 'rf_state_changed')

    def test_actual_bounded_collector_timeout_fails_closed(self):
        def timeout(argv):
            if argv[0].endswith('pcs-aprs-ptt-safe'):
                return bounded_command([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=.02)
            return self.command(argv)
        with patch.object(rf, 'command', side_effect=timeout):
            self.assertEqual(rf.observe()['gate'], 'BLOCKED')

    def test_existing_helper_check_is_read_only_and_configuration_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            config = path / 'ptt-safe.conf'
            config.write_text('PCS_APRS_PTT_LINE=6\n')
            gpio = path / 'gpioinfo'
            gpio.write_text('#!/bin/sh\nprintf \'%s\\n\' \'gpiochip0 6 "GPIO6" output bias=pull-down consumer="pcs-ptt-safe"\'\n')
            gpio.chmod(0o755)
            # Any control action is fatal in this real helper execution.
            for name in ('gpioset', 'systemctl'):
                forbidden = path / name
                forbidden.write_text('#!/bin/sh\nexit 99\n')
                forbidden.chmod(0o755)
            before = config.read_bytes()
            result = subprocess.run(['bash', str(ROOT / 'scripts/pcs-aprs-ptt-safe.sh'), '--check'],
                                    env={**os.environ, 'PATH': tmp + ':/usr/bin:/bin',
                                         'PCS_APRS_PTT_SAFE_CONFIG': str(config)},
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.ptt = result.stdout
            self.assertEqual(self.observe()['gate'], 'PASS')
            self.assertEqual(config.read_bytes(), before)

    def test_gate_and_cleanup_never_control_rf_or_write_aprs_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'runtime'
            state.private_dir(runtime)
            before = copy.deepcopy(self.rows)
            with patch.object(rf, 'command', side_effect=self.command), \
                    patch.object(safety, 'command', side_effect=self.command), \
                    patch('builtins.open', side_effect=AssertionError('unexpected config file access')):
                rf.require_safe()
                safety.restore(runtime)
                rf.require_safe()
            self.assertEqual(self.rows, before)
            self.assertFalse(any(any(word in c for word in ('stop', 'start', 'restart', 'mask', 'disable'))
                                 for c in self.calls))

    def test_blocked_scenario_records_rf_evidence_without_injection(self):
        self.rows[0]['ActiveState'] = 'active'
        with tempfile.TemporaryDirectory() as tmp:
            session = state.Session('FQ-301-v4', Path(tmp) / 'sessions')
            with patch.object(rf, 'command', side_effect=self.command), \
                    patch.object(scenario, 'arm_wan') as arm:
                result, reason = scenario.run(session, 60)
            self.assertEqual((result, reason), ('BLOCKED', 'rf_engine_active'))
            arm.assert_not_called()
            session.finish(result, reason)
            report = (session.path / 'report.md').read_text()
            events = (session.path / 'events.jsonl').read_text()
            self.assertIn('gate: BLOCKED', report)
            self.assertIn('rf_engine_active', events)
            self.assertNotIn('gpiochip', events)

    def test_pass_evidence_report_and_post_cleanup_failure_cannot_pass_campaign(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = state.Session('FQ-301-v4', Path(tmp) / 'sessions')
            with patch.object(rf, 'command', side_effect=self.command):
                rf.require_safe(session)
            session.finish('PASS', 'fixture')
            self.assertIn('ptt_safe: PASS', (session.path / 'report.md').read_text())
            rows = [json.loads(s) for s in (session.path / 'events.jsonl').read_text().splitlines()]
            self.assertEqual(rows[1]['evidence']['recovery'], 'installed')
            self.rows[0]['ActiveState'] = 'active'
            with patch.object(cli, 'RUNTIME', Path(tmp) / 'runtime'), \
                    patch.object(cli, 'Session', return_value=session), \
                    patch.object(cli, 'boot_cleanup'), patch.object(cli, 'preflight'), \
                    patch.object(cli, 'restore'), patch.object(scenario, 'run', return_value=('PASS', 'fixture')), \
                    patch.object(rf, 'command', side_effect=self.command):
                self.assertEqual(cli.campaign('FQ-301-v4', 60), cli.EXIT['ABORTED'])
            self.assertEqual(session.manifest['reason'], 'rf_engine_active')


if __name__ == '__main__':
    unittest.main()
