import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_fault as fault
    import pcs_qualify_safety as safety
    import pcs_qualify_state as state
    from pcs_qualify_wan import Identity, TABLE, OWNER
    REAL_APPLY = fault.apply


@unittest.skipIf(sys.platform == 'win32', 'Linux lease locks and subprocess bounds')
class FaultLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / 'runtime'
        state.private_dir(self.runtime)
        self.session = 'a' * 32
        self.target = Identity('eth1', 4, '02:00:00:00:00:01')
        self.document = None
        self.applied = []
        def apply(text, runtime):
            self.applied.append(text)
            self.document = (None if text.startswith('delete') else {'nftables': [{'table': {
                'family': 'inet', 'name': TABLE, 'comment': OWNER + self.session, 'handle': 17}}]})
        self.apply = apply
        for name, replacement in [('table', lambda: self.document), ('apply', apply)]:
            patcher = patch.object(fault, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(safety, 'command', return_value='active')
        patcher.start()
        self.addCleanup(patcher.stop)

    def arm(self):
        fault.arm_wan(self.session, self.target, 10, '10.42.0.0/24', '192.168.50.0/24', self.runtime)

    def test_lease_effect_cleanup_and_repeat(self):
        self.arm()
        self.assertTrue(safety.lease_valid(self.session, self.runtime))
        self.assertEqual(state.read_json(self.runtime / 'active.json')['effect'], 'nft-v4')
        safety.restore(self.runtime, expected_session='b' * 32)
        self.assertIsNotNone(self.document)
        safety.restore(self.runtime, expected_session=self.session)
        safety.restore(self.runtime)
        self.assertIsNone(self.document)
        self.assertFalse((self.runtime / 'wan.json').exists())
        self.assertFalse((self.runtime / 'active.json').exists())
        self.assertEqual(len(self.applied), 2)

    def test_uncertain_nft_commit_restores_before_returning_error(self):
        def uncertain(text, runtime):
            self.apply(text, runtime)
            if text.startswith('create'):
                raise state.HarnessError('collector_timeout')
        fault.apply.side_effect = uncertain
        with self.assertRaises(state.HarnessError): self.arm()
        self.assertIsNone(self.document)
        self.assertFalse((self.runtime / 'wan.json').exists())

    def test_preexisting_table_blocks_before_timer_or_record(self):
        self.document = {'unrelated': True}
        with self.assertRaises(state.HarnessError): self.arm()
        self.assertFalse((self.runtime / 'active.json').exists())
        safety.command.assert_not_called()

    def test_changed_owner_preserved_and_ledger_retained(self):
        self.arm()
        self.document['nftables'][0]['table']['comment'] = 'unrelated'
        with self.assertRaises(state.HarnessError): safety.restore(self.runtime)
        self.assertTrue((self.runtime / 'wan.json').exists())
        self.assertEqual(len(self.applied), 1)

    def test_corrupt_ledger_not_silently_discarded(self):
        self.arm()
        state.atomic_json(self.runtime / 'wan.json', {'session': self.session, 'command': 'CANARY'})
        with self.assertRaises(state.HarnessError): safety.restore(self.runtime)
        self.assertTrue((self.runtime / 'active.json').exists())

    def test_failed_timer_cannot_create_firewall(self):
        safety.command.side_effect = state.HarnessError('timer_unavailable')
        with self.assertRaises(state.HarnessError): self.arm()
        self.assertEqual(self.applied, [])
        self.assertFalse((self.runtime / 'wan.json').exists())

    def test_final_preflight_failure_leaves_no_fault_or_lease(self):
        def changed(): raise state.HarnessError('wan_preflight_changed')
        with self.assertRaises(state.HarnessError):
            fault.arm_wan(self.session, self.target, 10, '10.42.0.0/24', '192.168.50.0/24',
                          self.runtime, verify=changed)
        self.assertEqual(self.applied, [])
        self.assertFalse((self.runtime / 'active.json').exists())

    def test_batch_removed_after_command_error_and_symlink_refused(self):
        with patch.object(fault, 'command', side_effect=state.HarnessError('failed')):
            with self.assertRaises(state.HarnessError): REAL_APPLY('fixture', self.runtime)
        self.assertFalse((self.runtime / 'nft.batch').exists())
        target = Path(self.temp.name) / 'sentinel'
        target.write_text('unchanged')
        (self.runtime / 'nft.batch').symlink_to(target)
        with self.assertRaises(OSError): REAL_APPLY('fixture', self.runtime)
        self.assertEqual(target.read_text(), 'unchanged')


if __name__ == '__main__': unittest.main()
