"""Offline position selection and real loopback GPSD-protocol integration."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_location as location
import pcs_gpio as gpio
import pcs_aprs_agent as aprs
import pcs_meshtastic_gateway as mesh


class PositionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.epoch = 2_000_000_000.0
        self.state = location.PositionState(lambda: self.now, lambda: self.epoch)
        self.state.config = location.validate_config({'enabled': True, 'latitude': 0, 'longitude': 0})

    def fix(self, **extra):
        report = {'class': 'TPV', 'mode': 3, 'lat': 41.1, 'lon': -81.2,
                  'time': datetime.fromtimestamp(self.epoch, timezone.utc).isoformat()}
        self.state.update(dict(report, **extra))

    def advance(self, seconds):
        self.now += seconds
        self.epoch += seconds

    def source(self):
        return self.state.selection()[2]['source']

    def test_no_lock_uses_manual_without_fabricating_gps_or_time(self):
        tpv, _, status = self.state.selection()
        self.assertEqual(self.source(), 'Manual')
        self.assertEqual((tpv['lat'], tpv['lon']), (0, 0))
        self.assertTrue(status['available'])
        self.assertFalse(status['receiver_fix'])
        for name in ['time', 'speed', 'track', 'eph', 'satellites']:
            self.assertNotIn(name, tpv)
        self.assertNotIn('latitude', status)
        self.assertNotIn('lat', status)

    def test_recovery_requires_stable_fresh_fix_then_reverts_on_loss(self):
        self.fix()
        self.assertEqual(self.source(), 'Manual')
        self.advance(4)
        self.fix()
        self.assertEqual(self.source(), 'Manual')
        self.advance(1)
        self.fix()
        self.assertEqual(self.source(), 'GPS')
        self.state.update({'class': 'TPV', 'mode': 1})
        self.assertEqual(self.source(), 'Manual')

    def test_persist_keeps_manual_while_receiver_recovers(self):
        self.state.config['persist'] = True
        self.fix()
        self.advance(6)
        self.fix()
        tpv, _, status = self.state.selection()
        self.assertEqual(status['source'], 'Manual')
        self.assertTrue(status['receiver_fix'])
        self.state.config['persist'] = False
        self.assertEqual(self.source(), 'GPS')

    def test_partial_receiver_epochs_do_not_prevent_recovery(self):
        for _ in range(7):
            stamp = datetime.fromtimestamp(self.epoch, timezone.utc).isoformat()
            self.state.update({'class': 'TPV', 'mode': 3, 'time': stamp})
            self.state.update({'class': 'TPV', 'mode': 3, 'time': stamp, 'lat': 41.1})
            self.state.update({'class': 'TPV', 'mode': 3, 'time': stamp, 'lon': -81.2})
            self.advance(1)
        self.assertEqual(self.source(), 'GPS')

    def test_disable_restores_receiver_or_unavailable(self):
        self.state.config['enabled'] = False
        self.assertEqual(self.source(), 'Unavailable')
        self.fix()
        self.assertEqual(self.source(), 'GPS')

    def test_stale_future_and_replayed_fixes_never_mask_loss(self):
        self.fix()
        self.advance(6)
        self.fix()
        self.assertEqual(self.source(), 'GPS')
        self.advance(16)
        # Replaying the exact last GPS epoch does not refresh receipt time.
        self.state.update(dict(self.state.tpv))
        self.assertEqual(self.source(), 'Manual')
        self.fix(time=datetime.fromtimestamp(self.epoch + 60, timezone.utc).isoformat())
        self.assertEqual(self.source(), 'Manual')
        self.fix(time=datetime.fromtimestamp(self.epoch - 60, timezone.utc).isoformat())
        self.assertEqual(self.source(), 'Manual')

    def test_coordinates_and_altitude_must_be_finite_and_in_range(self):
        for key, value in [('latitude', math.nan), ('longitude', math.inf), ('latitude', True),
                           ('latitude', 91), ('longitude', -181), ('altitude_m', 10001)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                location.validate_config(dict(self.state.config, **{key: value}))
        for value in [None, [], {'enabled': 'yes'}, {'persist': 1}, {'latitude': 0}, {'password': 'x'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                location.validate_config(value)

    def test_boundary_grid_and_zero_coordinates(self):
        self.assertEqual(location.grid(0, 0), 'JJ00aa')
        self.assertEqual(len(location.grid(90, 180)), 6)
        self.fix(lat=math.nan)
        self.assertEqual(self.source(), 'Manual')

    def test_private_atomic_config_preserves_previous_on_rejected_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'location.json'
            location.save_config(self.state.config, path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                location.save_config(dict(self.state.config, latitude=100), path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(location.load_config(path)['latitude'], 0)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_expired_status_does_not_suppress_faults(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'status.json'
            location.atomic_write(path, self.state.selection()[2], 0o644)
            self.assertEqual(location.read_status(path, self.now)['source'], 'Manual')
            self.assertEqual(location.read_status(path, self.now + 11), {})
            self.assertEqual(location.read_status(path, self.now - 1), {})

    def test_dashboard_manual_labels_and_private_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            report, _, status = self.state.selection()
            status['updated_monotonic'] = location.time.monotonic()
            location.atomic_write(path / 'status.json', status, 0o644)
            location.atomic_write(path / 'position.json', report)
            info, _ = location.apply_dashboard({'satellites': '0 used'}, path)
            self.assertEqual(info['fix_quality'], 'Manual')
            self.assertTrue(info['lat_lon'].startswith('Manual'))
            self.assertTrue(info['grid_square'].startswith('Manual'))

    def test_manual_only_removes_no_fix_alert_and_lcd_fits(self):
        stats = gpio.StatsSnapshot(40, 50, 0, False, position_source='Manual', grid_square='JJ00aa')
        health = gpio.MatrixHealthSnapshot(stats=stats, root_used_percent=20, primary_usb_mounted=True,
                                          failed_services=1, router_online=True, pistar_online=True)
        names = [a.name for a in gpio.matrix_alerts(health)]
        self.assertNotIn('gps_fix', names)
        self.assertIn('failed_services', names)
        pages = gpio.lcd_status_pages(stats, 100)
        self.assertTrue(any('Man' in a for a, b in pages))
        self.assertTrue(all(len(line) <= 16 for pair in pages for line in pair))
        self.assertEqual(len(gpio.stats_frames(stats)), 6)


class PositionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.state = location.PositionState()
        self.state.config = location.validate_config({'enabled': True, 'latitude': 12.5, 'longitude': -34.5})
        self.stop = threading.Event()
        self.server = location.PositionServer(('127.0.0.1', 0), self.state, self.stop)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = socket.create_connection(self.server.server_address, timeout=3)
        self.connection.settimeout(3)
        self.stream = self.connection.makefile('rb')
        self.assertEqual(self.read()['class'], 'VERSION')

    def tearDown(self):
        self.stop.set()
        self.stream.close()
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def read(self):
        return json.loads(self.stream.readline())

    def test_real_socket_compound_watch_and_poll(self):
        self.connection.sendall(b'?VERSION;?DEVICES;?WATCH={"enable":true,"json":true};\n')
        classes = [self.read()['class'] for _ in range(5)]
        self.assertEqual(classes, ['VERSION', 'DEVICES', 'WATCH', 'TPV', 'SKY'])
        self.connection.sendall(b'?POLL;\n')
        for _ in range(5):
            reply = self.read()
            if reply['class'] == 'POLL':
                break
        self.assertEqual(reply['class'], 'POLL')
        self.assertEqual(reply['tpv'][0]['pcs_source'], 'Manual')
        self.assertNotIn('time', reply['tpv'][0])

    def test_device_mutation_is_rejected(self):
        self.connection.sendall(b'?DEVICE={"path":"/dev/test","bps":9600};\n')
        self.assertEqual(self.read()['class'], 'ERROR')

    def test_fragmented_watch_and_disabled_watch(self):
        self.connection.sendall(b'?WATCH={"enable":')
        self.connection.sendall(b'false,"json":true};\n')
        self.assertFalse(self.read()['enable'])
        self.connection.sendall(b'?POLL;\n')
        self.assertEqual(self.read()['class'], 'POLL')

    def test_raw_stream_not_misrepresented_as_receiver(self):
        self.connection.sendall(b'?WATCH={"enable":true,"raw":1};\n')
        self.assertEqual(self.read()['class'], 'ERROR')

    def test_aprs_status_consumes_manual_without_a_gps_timestamp(self):
        provider = aprs.StatusProvider(gpsd_host='127.0.0.1', gpsd_port=self.server.server_address[1])
        self.assertEqual(provider.gps_value(), 'Manual')
        with mock.patch.object(provider, 'gps_value', return_value='Manual'):
            self.assertIn('GPS Manual', provider.status())

    def test_meshtastic_consumes_manual_coordinates_and_msl_elevation(self):
        self.state.config['altitude_m'] = 123
        position = mesh.read_gpsd_position(host='127.0.0.1', port=self.server.server_address[1])
        self.assertEqual(position, (12.5, -34.5, 123))

    @unittest.skipUnless(shutil.which('cc') and Path('/usr/include/gps.h').exists(), 'Linux native libgps development files required')
    def test_native_libgps_client_used_by_direwolf_accepts_manual_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'location-client'
            subprocess.run(['cc', str(Path(__file__).with_name('location_libgps_client.c')),
                            '-lgps', '-lm', '-o', str(executable)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(executable), str(self.server.server_address[1])],
                                    check=True, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.stdout.strip(), 'manual-position-without-gps-time')


if __name__ == '__main__':
    unittest.main()
