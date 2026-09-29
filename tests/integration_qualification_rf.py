#!/usr/bin/env python3
"""Real systemd RF gate states in disposable QEMU; GPIO observations are synthetic.

Fixture engines are /usr/bin/sleep, never radio software. No appliance execution.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from integration_qualification_safety import guard, cmd, REPO

sys.path.insert(0, str(REPO / 'scripts'))
import pcs_qualify_rf as rf
import pcs_qualify_safety as safety
import pcs_qualify_state as state


class RealRF(unittest.TestCase):
    def test_real_service_states_jobs_and_read_only_gate(self):
        guard()
        paths = [Path('/run/systemd/system', name) for name in rf.UNITS]
        # Refuse to replace anything, even in the disposable guest.
        for unit, path in zip(rf.UNITS, paths):
            self.assertFalse(path.exists() or path.is_symlink())
            self.assertEqual(cmd('systemctl', 'show', '--value', '-p', 'LoadState', unit).stdout.strip(), 'not-found')
        created = []
        original = rf.command
        calls = []
        def observation(argv):
            calls.append(argv)
            if argv == ['/usr/local/sbin/pcs-aprs-ptt-safe', '--check']:
                return ('gpiochip0 6 "GPIO6" output bias=pull-down consumer="pcs-ptt-safe"\n'
                        'PCS APRS PTT guard holds gpiochip0 line 6 low.\n')
            if argv == ['/usr/bin/pinctrl', 'get', '6']:
                return '6: op -- pd | lo // GPIO6 = output\n'
            self.assertEqual(argv[1], 'show')
            return original(argv)
        try:
            for path in paths:
                with path.open('x') as stream:
                    stream.write('[Unit]\nDescription=Disposable qualification RF state fixture\n'
                                 '[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\n')
                created.append(path)
            cmd('systemctl', 'daemon-reload')
            cmd('systemctl', 'start', rf.UNITS[2])  # Fixture setup, never harness control.
            with patch.object(rf, 'command', side_effect=observation):
                self.assertEqual(rf.observe()['gate'], 'PASS')
                cmd('systemctl', 'start', rf.UNITS[0])
                self.assertEqual(rf.observe()['reason'], 'rf_engine_active')
                cmd('systemctl', 'stop', rf.UNITS[0])
                cmd('systemctl', 'start', rf.UNITS[1])
                self.assertEqual(rf.observe()['reason'], 'rf_engine_active')
                cmd('systemctl', 'stop', rf.UNITS[1])
                cmd('systemctl', 'start', rf.UNITS[3])
                self.assertEqual(rf.observe()['reason'], 'rf_recovery_not_idle')
                cmd('systemctl', 'stop', rf.UNITS[3])
                before = rf.states()
                with tempfile.TemporaryDirectory() as tmp:
                    runtime = state.private_dir(Path(tmp) / 'runtime')
                    rf.require_safe()
                    safety.restore(runtime)
                    rf.require_safe()
                self.assertEqual(rf.states(), before)
                cmd('systemctl', 'stop', rf.UNITS[2])
                self.assertEqual(rf.observe()['reason'], 'rf_ptt_unverified')
            self.assertTrue(all(c[1] in ('show', '--check', 'get') for c in calls))
        finally:
            for path in created:
                cmd('systemctl', 'stop', path.name, check=False)
                path.unlink()
            cmd('systemctl', 'daemon-reload')


if __name__ == '__main__':
    guard()
    unittest.main(verbosity=2)
