import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_qualify_witness as witness


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'witness.jsonl'
        self.session = 'a' * 32

    def fixture(self):
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        def clock(i):
            return {'utc': (start + dt.timedelta(seconds=i)).isoformat(), 'monotonic': i + 100.0}
        return [dict(version=1, event='start', session=self.session, clock_id='b' * 32, boot_id=None,
                     duration=5, interval=1, **clock(0)),
                *[dict(event='sample', seq=i, ok=True, latency=0.01, **clock(i)) for i in range(5)],
                dict(event='end', samples=5, **clock(5))]

    def save(self, rows):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def test_complete_failure_missing_corruption_order_clock(self):
        rows = self.fixture()
        self.save(rows)
        self.assertEqual(witness.validate(self.path, self.session)['result'], 'PASS')
        rows[2]['ok'] = False
        self.save(rows)
        self.assertEqual(witness.validate(self.path, self.session)['result'], 'FAIL')
        for edit in ('missing_end', 'missing_sample', 'wrong_run', 'clock_step', 'raw_secret', 'nan'):
            rows = self.fixture()
            if edit == 'missing_end': rows.pop()
            if edit == 'missing_sample': rows.pop(2)
            if edit == 'wrong_run': rows[0]['session'] = 'c' * 32
            if edit == 'clock_step': rows[2]['monotonic'] += 10
            if edit == 'raw_secret': rows[2]['error'] = 'SECRET'
            if edit == 'nan': rows[2]['latency'] = float('nan')
            self.save(rows)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                witness.validate(self.path, self.session)

    def test_http_outage_and_bound_source(self):
        class Handler(BaseHTTPRequestHandler):
            status = 200
            def do_GET(self):
                self.send_response(self.status)
                self.end_headers()
                self.wfile.write(b'body-not-stored')
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            self.assertTrue(witness.probe('127.0.0.1', '127.0.0.1', port)[0])
            for status in (302, 503):
                Handler.status = status
                self.assertFalse(witness.probe('127.0.0.1', '127.0.0.1', port)[0])
            self.assertFalse(witness.probe('127.0.0.1', '192.0.2.90', port)[0])
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(witness.probe('127.0.0.1', '127.0.0.1', port)[0])

    def test_run_durable_complete_and_exclusive_output(self):
        from unittest.mock import patch
        with patch.object(witness, 'probe', return_value=(True, 0.01)):
            witness.run(self.session, '127.0.0.1', '127.0.0.1', 80, 5, 1, self.path)
        summary = witness.validate(self.path, self.session)
        self.assertEqual(summary['result'], 'PASS')
        self.assertNotIn('127.0.0.1', self.path.read_text())
        with self.assertRaises(FileExistsError):
            witness.run(self.session, '127.0.0.1', '127.0.0.1', 80, 5, 1, self.path)


if __name__ == '__main__':
    unittest.main()
