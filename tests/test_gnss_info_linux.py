"""Real loopback GPSD/UNIX control integration; no hardware or clock control."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_gnss import ubx
from test_gnss_intelligence import signal_rows, span, version

LINUX = sys.platform.startswith('linux')
REPO = Path(__file__).resolve().parents[1]


def until(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    raise AssertionError('Local diagnostic fixture timed out')


@unittest.skipUnless(LINUX, 'Linux UNIX-socket/service-signal integration')
class PassiveServiceTests(unittest.TestCase):
    def test_real_gpsd_feed_capture_snapshot_timeout_disconnect_and_sigterm_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='pcs-info-') as temporary:
            root = Path(temporary)
            runtime, state = root / 'run', root / 'state'
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(8)
            listener.settimeout(0.2)
            port = listener.getsockname()[1]
            stop, clients, watches = threading.Event(), [], []
            def connection(channel):
                with channel:
                    channel.settimeout(1)
                    command = channel.recv(4096)
                    watch = json.loads(command.split(b'=', 1)[1].split(b';', 1)[0])
                    watches.append(watch)
                    self.assertEqual(watch['device'], '/dev/pcs-gnss-neo')
                    while not stop.is_set():
                        try:
                            if watch.get('raw'):
                                # Two warning polls must produce one state event.
                                channel.sendall(version() + ubx(1, 0x43, signal_rows()) + ubx(10, 0x31, span()) +
                                                ubx(0x27, 9, bytes((1, 0, 0, 0, 5, 0, 0, 0, 3, 0, 0, 0))))
                            else:
                                channel.sendall(json.dumps({'class': 'SKY', 'device': '/dev/pcs-gnss-neo',
                                    'satellites': [{'gnssid': 0, 'svid': 4, 'used': True}, {'gnssid': 0, 'svid': 4, 'used': True}]}).encode() + b'\n')
                        except OSError:
                            break
                        stop.wait(0.15)
            def accept():
                while not stop.is_set():
                    try:
                        channel, _ = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    thread = threading.Thread(target=connection, args=(channel,), daemon=True)
                    clients.append(thread)
                    thread.start()
            accept_thread = threading.Thread(target=accept, daemon=True)
            accept_thread.start()
            script = root / 'service.py'
            script.write_text('''import sys, socket
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pcs_gnss_info as info
import pcs_assistnow as assist
root = Path(sys.argv[2])
info.RUNTIME = root / 'run'
info.SOCKET = info.RUNTIME / 'control.sock'
assist.ROOT = root / 'state'
info.gnss_config = lambda: {'mode': 'neo-f10n', 'baud': 115200}
native = socket.create_connection
socket.create_connection = lambda address, timeout: native(('127.0.0.1', int(sys.argv[3])), timeout)
def forbidden(*args, **kwargs):
    if args[0] == ['chronyc', '-h', '127.0.0.1', '-n', 'sources']:
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout='#* F10P 0 0 377 0 -479ns[ -621ns] +/- 134ns\\n')
    raise AssertionError('Passive diagnostics attempted a service or timing command')
info.subprocess.run = forbidden
info.serve()
''')
            log = (root / 'service.log').open('w+')
            process = subprocess.Popen([sys.executable, str(script), str(REPO / 'scripts'), str(root), str(port)], stdout=log, stderr=subprocess.STDOUT)
            def snapshot():
                return json.loads((runtime / 'status.json').read_text())
            def control(operation, seconds=5):
                with socket.socket(socket.AF_UNIX) as channel:
                    channel.settimeout(3)
                    channel.connect(str(runtime / 'control.sock'))
                    channel.sendall(json.dumps({'operation': operation, 'seconds': seconds}).encode() + b'\n')
                    return json.loads(channel.recv(4096))
            try:
                until(lambda: snapshot()['messages']['spectrum']['state'] == 'Fresh')
                until(lambda: snapshot()['counts']['visible'] == 1)
                self.assertNotIn('error', control('start-spectrum'))
                until(lambda: snapshot()['capture']['frame_count'] >= 1)
                self.assertNotIn('error', control('snapshot'))
                self.assertEqual(len(list(state.glob('spectrum-*.json'))), 1)
                until(lambda: not snapshot()['capture']['active'], seconds=9)
                self.assertEqual(len(json.loads((state / 'rf-events.json').read_text())), 1)
                # Capture cannot be indefinitely extended by repeated starts.
                self.assertNotIn('error', control('start-spectrum', 10))
                self.assertIn('error', control('start-spectrum', 10))
                self.assertNotIn('error', control('stop-spectrum'))
                for _ in range(12):
                    self.assertNotIn('error', control('snapshot'))
                self.assertEqual(len(list(state.glob('spectrum-*.json'))), 10)
                stop.set()
                listener.close()
                until(lambda: snapshot()['messages']['spectrum']['state'] == 'Stale', seconds=9)
                self.assertIn('error', control('start-spectrum'))
                self.assertEqual(process.poll(), None)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertFalse((runtime / 'control.sock').exists())
                self.assertFalse(snapshot()['capture']['active'])
                self.assertEqual({bool(w.get('raw')) for w in watches}, {False, True})
            finally:
                stop.set()
                listener.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                log.close()
                accept_thread.join(timeout=2)
                for thread in clients:
                    thread.join(timeout=2)

    def test_units_do_not_order_receiver_after_internet_or_downloads(self):
        unit = (REPO / 'systemd/pcs-assistnow-refresh.service').read_text()
        self.assertNotIn('network-online.target', unit)
        self.assertIn('PrivateDevices=yes', unit)
        for name in ('pcs-gnss.service',):
            source = (REPO / 'systemd' / name).read_text()
            self.assertNotIn('assistnow', source)
        source = (REPO / 'scripts/pcs-gnss.py').read_text()
        self.assertLess(source.index('operation(managed_exchange'), source.index("write_file(RUNTIME / 'gpsd.env'"))
        self.assertIn("timing_healthy=read_status().get('utc_valid') is True", source)


if __name__ == '__main__':
    unittest.main()
