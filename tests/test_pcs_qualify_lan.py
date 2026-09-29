import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_qualify_lan import Receipts


class LanReceipts(unittest.TestCase):
    def setUp(self):
        self.session = 'a' * 32
        self.receiver = Receipts(self.session)

    def packet(self, seq, phase, ok=True):
        row = dict(version=1, session=self.session, seq=seq, phase=phase)
        if phase == 'after':
            row['ok'] = ok
        return row

    def sample(self, seq, at, ok=True):
        self.receiver.receive(self.packet(seq, 'before'), at)
        return self.receiver.receive(self.packet(seq, 'after', ok), at + .2)

    def test_coverage_uses_only_pcs_receipts(self):
        self.sample(0, 10)
        self.sample(1, 11)
        self.assertTrue(self.receiver.ready(11.3))
        self.assertEqual(self.receiver.coverage(10.5, 11), 'PASS')
        self.assertEqual(self.receiver.coverage(9, 11), 'INCONCLUSIVE')
        self.assertEqual(self.receiver.coverage(10, 12), 'INCONCLUSIVE')

    def test_failed_http_never_passes(self):
        self.sample(0, 10)
        self.sample(1, 11, False)
        self.assertFalse(self.receiver.ready(11.3))
        self.assertEqual(self.receiver.coverage(10, 11), 'FAIL')

    def test_retransmission_does_not_extend_coverage(self):
        first = self.sample(0, 10)
        again = self.receiver.receive(self.packet(0, 'after'), 20)
        self.assertEqual(first, again)
        self.assertEqual(len(self.receiver.samples), 1)
        self.receiver.receive(self.packet(0, 'after', False), 20)
        self.assertTrue(self.receiver.invalid)

    def test_missing_before_out_of_order_and_excess_latency(self):
        for case in ('after', 'order', 'slow', 'gap'):
            self.receiver = Receipts(self.session)
            if case == 'after': self.receiver.receive(self.packet(0, 'after'), 10)
            if case == 'order': self.receiver.receive(self.packet(1, 'before'), 10)
            if case == 'slow':
                self.receiver.receive(self.packet(0, 'before'), 10)
                self.receiver.receive(self.packet(0, 'after'), 14)
            if case == 'gap':
                self.sample(0, 10)
                self.sample(1, 16)
            with self.subTest(case=case):
                self.assertTrue(self.receiver.invalid)
                self.assertEqual(self.receiver.coverage(10, 11), 'INCONCLUSIVE')

    def test_wrong_session_and_noise_ignored(self):
        self.assertIsNone(self.receiver.receive({'session': 'b' * 32, 'secret': 'CANARY'}, 1))
        self.assertIsNone(self.receiver.receive([], 1))
        self.assertFalse(self.receiver.invalid)

    def test_authenticated_malformed_packet_invalidates(self):
        for field, value in [('seq', True), ('seq', 310), ('phase', 'execute'), ('version', 2), ('version', True)]:
            row = self.packet(0, 'before')
            row[field] = value
            receiver = Receipts(self.session)
            self.assertIsNone(receiver.receive(row, 1))
            self.assertTrue(receiver.invalid)
        row = self.packet(0, 'before')
        row['command'] = 'CANARY'
        self.assertIsNone(self.receiver.receive(row, 1))

    def test_finish_ack_only_after_required_window(self):
        self.receiver.finish_after = 11
        self.assertFalse(self.sample(0, 10)['done'])
        self.assertTrue(self.sample(1, 11)['done'])
        self.assertFalse(self.receiver.ready(16))


if __name__ == '__main__': unittest.main()
