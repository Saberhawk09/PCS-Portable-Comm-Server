import importlib.util
import ast
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('snapshot', ROOT / 'scripts/pcs_dashboard_snapshot.py')
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)

class SnapshotTests(unittest.TestCase):
    def test_first_read_is_immediate_and_does_not_collect(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(snapshot.subprocess, 'run') as run:
            started = time.monotonic()
            value = snapshot.read_snapshot(True, Path(temp))
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertEqual(value['snapshot']['state'], 'unavailable')
            self.assertIn('loading', value['error'])
            run.assert_not_called()

    def test_audiences_and_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            for name, data in [('public', {'overall': 'ok', 'generated_at': 'old', 'alerts': []}),
                               ('admin', {'overall': 'ok', 'generated_at': 'old', 'secret': 'private-test'})]:
                snapshot.atomic_write(runtime / (name + '.json'), {'collected_monotonic': 100, 'data': data})
            fresh = snapshot.read_snapshot(True, runtime, 110)
            stale = snapshot.read_snapshot(True, runtime, 200)
            self.assertEqual(fresh['snapshot']['state'], 'fresh')
            self.assertEqual(stale['snapshot']['state'], 'stale')
            self.assertEqual(stale['overall'], 'warn')
            self.assertEqual(stale['generated_at'], 'old')
            self.assertNotIn('private-test', json.dumps(stale))
            self.assertIn('private-test', json.dumps(snapshot.read_snapshot(False, runtime, 110)))

    def test_corrupt_snapshot_returns_loading_without_collecting(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            (runtime / 'public.json').write_text('{')
            self.assertEqual(snapshot.read_snapshot(True, runtime)['snapshot']['state'], 'unavailable')

    def test_failed_collection_preserves_previous_data(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            snapshot.atomic_write(runtime / 'public.json', {'collected_monotonic': 10, 'data': {'generated_at': 'old'}})
            before = (runtime / 'public.json').read_bytes()
            with mock.patch.object(snapshot, 'collect_output', side_effect=subprocess.TimeoutExpired('collector', 60)):
                snapshot.collect_once(runtime)
            self.assertEqual((runtime / 'public.json').read_bytes(), before)

    def test_successful_collection_keeps_distinct_projections(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            outputs = [json.dumps({'generated_at': 'now', 'public': True}),
                       json.dumps({'generated_at': 'now', 'private': True})]
            with mock.patch.object(snapshot, 'collect_output', side_effect=outputs):
                snapshot.collect_once(runtime)
            self.assertNotIn('private', json.dumps(snapshot.read_snapshot(True, runtime)))
            self.assertTrue(snapshot.read_snapshot(False, runtime)['private'])

    @unittest.skipIf(os.name == 'nt', 'Linux flock integration')
    def test_second_collector_exits_without_collecting(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'entered'
            code = (
                "import importlib.util,time; from pathlib import Path; "
                f"s=importlib.util.spec_from_file_location('snapshot',{str(ROOT / 'scripts/pcs_dashboard_snapshot.py')!r}); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                f"m.RUNTIME=Path({temp!r}); "
                f"m.collect_once=lambda: (Path({str(marker)!r}).open('a').write('entered'), time.sleep(20)); "
                "m.main()"
            )
            first = subprocess.Popen([sys.executable, '-c', code])
            try:
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                subprocess.run([sys.executable, '-c', code], timeout=2, check=True)
                self.assertEqual(marker.read_text(), 'entered')
            finally:
                first.terminate()
                first.wait(timeout=3)

    def test_healthy_uplink_is_not_overridden_by_failed_hostname_ping(self):
        source = (ROOT / 'scripts/pcs-web-action.sh').read_text().split("    python3 - <<'PY'\n", 1)[1].split('\nPY\n}', 1)[0]
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'dashboard_internet_available')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<collector>', 'exec'), namespace)
        available = namespace['dashboard_internet_available']
        self.assertTrue(available({'available': True, 'internet': True}, 'wlan0', False))
        self.assertFalse(available({'available': True, 'internet': False}, 'wlan0', True))
        self.assertTrue(available({'available': False}, 'wlan0', True))
        self.assertFalse(available({'available': True, 'internet': True}, '', True))

    def test_fast_actions_do_not_enter_host_service(self):
        source = (ROOT / 'scripts/pcs-web-action.sh').read_text()
        host_dispatch = source.split('dispatch_host_namespace_action() {', 1)[1].split('queue_pcs_power_action()', 1)[0]
        self.assertNotIn('dashboard-public-json|', host_dispatch)
        self.assertIn('python3 /usr/local/lib/pcs/pcs_dashboard_snapshot.py --read public', source)

if __name__ == '__main__':
    unittest.main()
