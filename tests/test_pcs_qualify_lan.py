import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_qualify_lan import Receipts, validate_file


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


class LanFileImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'client.jsonl'
        self.events_path = Path(self.temp.name) / 'events.jsonl'
        self.session = 'a' * 32
        self.boot = '12345678-1234-1234-1234-123456789abc'
        self.rows = [dict(version=1, event='start', session=self.session, clock_id='b' * 32,
                          boot_id=None, scope='lan_http_with_pcs_receipts', platform='windows')]
        self.rows += [dict(event='sample', seq=i, ok=True, pcs_before=100 + i, pcs_after=100.2 + i)
                      for i in range(4)]
        self.rows += [dict(event='end', samples=4, complete=True)]
        for i, row in enumerate(self.rows):
            row.update(monotonic=1000 + i, utc=(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) +
                                               dt.timedelta(seconds=i)).isoformat())
        self.events = []
        for i in range(4):
            self.event('lan_witness', 100.3 + i, dict(seq=i, before=100 + i, after=100.2 + i, ok=True))
            if i == 0:
                self.event('wan_fault_started', 100.4)
            if i == 2:
                self.event('wan_recovery_observed', 102.4)

    def event(self, kind, mono, evidence=None):
        self.events.append(dict(event=kind, monotonic=mono, evidence=evidence or {},
                                session=self.session, boot_id=self.boot, seq=len(self.events)))

    def validate(self):
        for path, rows in ((self.path, self.rows), (self.events_path, self.events)):
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        return validate_file(self.path, self.session, self.events_path)

    def test_windows_and_linux_receipts_match_without_cross_host_clock_alignment(self):
        for platform in ('windows', 'linux'):
            self.rows[0].update(platform=platform, boot_id=None if platform == 'windows' else self.boot)
            summary = self.validate()
            self.assertEqual(summary['result'], 'PASS')
            self.assertTrue(summary['matched_receipts'])
            self.assertEqual(summary['samples'], 4)
            self.assertNotIn(self.session, json.dumps(summary))
            self.assertNotIn(self.boot, json.dumps(summary))

    def test_observed_http_failure_is_fail(self):
        self.rows[2]['ok'] = False
        self.events[2]['evidence']['ok'] = False
        self.assertEqual(self.validate()['result'], 'FAIL')

    def test_tampered_or_wrong_session_file_rejected(self):
        original = copy.deepcopy(self.rows)
        for row, key, value in ((0, 'session', 'c' * 32), (1, 'ok', False), (1, 'pcs_after', 100.1),
                                (1, 'secret', 'CANARY'), (0, 'clock_id', 'CANARY')):
            self.rows = copy.deepcopy(original)
            self.rows[row][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()

    def test_incomplete_or_malformed_client_rejected(self):
        original = copy.deepcopy(self.rows)
        for row, key, value in ((-1, 'complete', False), (-1, 'samples', True), (0, 'version', True),
                                (1, 'seq', True), (1, 'pcs_before', float('nan')), (1, 'ok', 1),
                                (1, 'monotonic', False), (1, 'utc', 'not-a-clock'),
                                (0, 'boot_id', 'bad'), (1, 'pcs_after', 110)):
            self.rows = copy.deepcopy(original)
            self.rows[row][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
        self.rows = original[:-1]
        with self.assertRaises(ValueError):
            self.validate()

    def test_client_clock_jump_rejected(self):
        self.rows[2]['monotonic'] += 10
        with self.assertRaises(ValueError):
            self.validate()

    def test_server_mismatch_cross_boot_and_missing_recovery_rejected(self):
        original = copy.deepcopy(self.events)
        for key, value in (('session', 'c' * 32), ('seq', True), ('boot_id', '23456789-1234-1234-1234-123456789abc'),
                           ('monotonic', float('inf')), ('evidence', {})):
            self.events = copy.deepcopy(original)
            self.events[2][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
        self.events = copy.deepcopy(original)
        self.events[4]['event'] = 'not_recovery'
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_window_coverage_never_passes(self):
        self.events[1]['monotonic'] = 99
        with self.assertRaises(ValueError):
            self.validate()
        self.events[1]['monotonic'] = 100.4
        self.events[4]['monotonic'] = 104
        self.events[5]['monotonic'] = 105
        self.assertEqual(self.validate()['result'], 'INCONCLUSIVE')

    def test_truncated_oversized_duplicate_key_and_invalid_utf8_rejected(self):
        self.validate()
        original = self.path.read_bytes()
        for raw in (original[:-1], b'x' * (2 * 1024 * 1024 + 1), b'\xff\n',
                    original.replace(b'"version": 1', b'"version": 1, "version": 1')):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                validate_file(self.path, self.session, self.events_path)


if __name__ == '__main__': unittest.main()
