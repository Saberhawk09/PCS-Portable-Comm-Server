"""Synthetic receiver/time states; never opens an appliance or a real GNSS port."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest
import contextlib
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_gnss as gnss
import pcs_gnss_runtime as runtime
import pcs_gpio as gpio

SPEC = importlib.util.spec_from_file_location('gnss_cli', ROOT / 'scripts/pcs-gnss.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def config():
    return {'version': 1, 'mode': gnss.NEO, 'receiver': {'vid': '1a86', 'pid': '7523',
            'serial': '', 'id_path': 'platform-test-usb-0:1.2:1.0', 'model': 'NEO-F10N', 'protocol': '40.00'},
            'serial_offset': 0.1, 'serial_delay': 0.2, 'ram_profile': True}


BASE = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc).timestamp()


def pvt(second=0, flags=7, fix=3, accuracy=30):
    stamp = dt.datetime.fromtimestamp(BASE + second, dt.timezone.utc)
    value = bytearray(92)
    struct.pack_into('<IHBBBBBBIi', value, 0, 100000 + int(second * 1000), stamp.year, stamp.month,
                     stamp.day, stamp.hour, stamp.minute, stamp.second, flags, accuracy, 0)
    value[20:22] = bytes((fix, int(fix > 0)))
    return bytes(value)


class ReceiverTests(unittest.TestCase):
    def test_missing_configuration_is_legacy_and_no_hardware_access(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(gnss.os, 'open') as opened:
            self.assertEqual(gnss.load_config(Path(temp) / 'missing'), gnss.DEFAULT)
            self.assertEqual(gnss.read_status(config=Path(temp) / 'missing')['state'], 'legacy')
            opened.assert_not_called()

    def test_config_requires_enrollment_and_calibration(self):
        self.assertEqual(gnss.validate_config(config())['mode'], gnss.NEO)
        for change in ({'receiver': {}}, {'serial_offset': float('nan')}, {'serial_delay': 0},
                       {'ram_profile': False}, {'mode': 'auto'}, {'extra': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                gnss.validate_config(config() | change)

    def test_usb_topology_identifies_only_enrolled_ch340(self):
        binding = config()['receiver']
        correct = binding | {'device': '/dev/ttyUSB9'}
        other = binding | {'id_path': 'other', 'device': '/dev/ttyUSB0'}
        self.assertEqual(gnss.match_receiver([other, correct], binding)['device'], '/dev/ttyUSB9')
        correct['device'] = '/dev/ttyUSB4'
        self.assertEqual(gnss.match_receiver([correct], binding)['device'], '/dev/ttyUSB4')

    def test_absent_and_ambiguous_usb_rejected(self):
        binding = config()['receiver']
        for inventory in ([], [binding, binding], [binding | {'id_path': 'wrong'}]):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                gnss.match_receiver(inventory, binding)

    def test_unique_serial_can_survive_port_move(self):
        binding = config()['receiver'] | {'serial': 'unique'}
        self.assertEqual(gnss.match_receiver([binding | {'id_path': 'new', 'device': 'moved'}], binding)['device'], 'moved')

    def test_no_usb_metadata_claims_positive_model(self):
        def response(*fields):
            return bytes(40) + b''.join(field.encode().ljust(30, b'\0') for field in fields)
        self.assertEqual(gnss.mon_ver(response('MOD=NEO-F10N', 'PROTVER=40.00'))['model'], 'NEO-F10N')
        for fields in (('PROTVER=40.00',), ('MOD=NEO-M9N', 'PROTVER=40.00'), ('MOD=NEO-F10N', 'PROTVER=99.00')):
            with self.assertRaises(ValueError):
                gnss.mon_ver(response(*fields))

    def test_udev_rule_is_enrolled_only(self):
        rule = cli.udev_rule(config()['receiver'])
        self.assertIn('ID_PATH', rule)
        self.assertIn('ID_MM_PORT_IGNORE', rule)
        self.assertNotIn('ID_MM_DEVICE_IGNORE', rule)
        self.assertIn(config()['receiver']['id_path'], rule)

    def test_udev_injection_is_rejected(self):
        value = config()
        value['receiver']['id_path'] = 'bad", RUN+="bad'
        with self.assertRaises(ValueError):
            gnss.validate_config(value)

    def test_fragmented_mixed_ubx_and_checksum_failure(self):
        message = gnss.ubx(1, 7, pvt())
        stream = gnss.UBXStream()
        self.assertEqual(stream.feed(b'$GPRMC,test\r\n' + message[:17]), [])
        self.assertEqual(stream.feed(message[17:]), [(1, 7, pvt())])
        self.assertEqual(stream.feed(message[:-1] + b'\0' + message), [(1, 7, pvt())])
        self.assertEqual(stream.feed(b'\xb5\x62\x01\x07\xff\xff' + message), [(1, 7, pvt())])

    def test_ubx_readback_and_volatile_write_layers(self):
        values = dict(gnss.KEYS.values())
        frame = gnss.valset(values)
        self.assertEqual(frame[7], 1)
        readback = bytes((1, 0, 0, 0)) + frame[10:-2]
        self.assertEqual(gnss.decode_values(readback), values)
        self.assertEqual(gnss.valset(values, persistent=True)[7], 7)
        with self.assertRaises(ValueError):
            gnss.decode_values(readback[:-1])

    def test_receiver_configuration_is_idempotent(self):
        values = dict(gnss.KEYS.values())
        with patch.object(cli, 'receiver_values', return_value=values), patch.object(cli, 'exchange') as send:
            self.assertEqual(cli.configure_receiver(1, values), values)
            send.assert_not_called()

    def test_receiver_configuration_readback_failure_restores_ram(self):
        before = dict(gnss.KEYS.values())
        desired = before | {gnss.KEYS['tp_grid'][0]: 0}
        before[gnss.KEYS['tp_grid'][0]] = 1
        with patch.object(cli, 'receiver_values', side_effect=[before, before]), patch.object(cli, 'exchange') as send:
            with self.assertRaises(ValueError):
                cli.configure_receiver(1, desired)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args.args[1], gnss.valset({gnss.KEYS['tp_grid'][0]: 1}))


class DeliveredProfileTests(unittest.TestCase):
    fixtures = ROOT / 'tests/fixtures/gnss'

    def test_exact_operator_exports_and_no_required_receiver_writes(self):
        report = gnss.export_report(self.fixtures / 'stock-f10-neo.ucf', self.fixtures / 'f10-neo-pcs-finished.ucf')
        self.assertEqual(report['stock_layers'], {'0': 380, '1': 0, '2': 0})
        self.assertEqual(report['finished_layers'], {'0': 380, '1': 11, '2': 11})
        self.assertEqual(len(report['ram_changes']), 17)
        self.assertEqual(report['unchanged_ram'], 363)
        self.assertEqual(report['required_ram_changes'], {})
        self.assertFalse(report['applied'])
        ram = gnss.ucf_layers(self.fixtures / 'f10-neo-pcs-finished.ucf')[0]
        with patch.object(cli, 'receiver_values', return_value=ram), patch.object(cli, 'exchange') as exchange:
            cli.configure_receiver(1, gnss.required_values())
            exchange.assert_not_called()

    def test_only_eleven_keys_have_persistence_evidence(self):
        layers = gnss.ucf_layers(self.fixtures / 'f10-neo-pcs-finished.ucf')
        self.assertEqual(layers[1], layers[2])
        self.assertEqual(layers[2][gnss.KEYS['baud'][0]], 115200)
        self.assertNotIn(gnss.OUTPUT_KEYS['sig_uart1'], layers[2])
        self.assertEqual(layers[0][gnss.OUTPUT_KEYS['sig_uart1']], 1)
        self.assertEqual(layers[0][gnss.OUTPUT_KEYS['gll_uart1']], 0)
        self.assertEqual(layers[0][gnss.OUTPUT_KEYS['vtg_uart1']], 0)
        self.assertEqual(layers[0][gnss.KEYS['timels_uart1'][0]], 0)

    def test_delivered_firmware_identity_keeps_evidence_separate(self):
        payload = b'EXT SPG L1L5 6.00 (041e8a)'.ljust(30, b'\0') + b'000A0000'.ljust(10, b'\0')
        payload += b'MOD=NEO-F10N'.ljust(30, b'\0') + b'PROTVER=40.00'.ljust(30, b'\0')
        value = gnss.mon_ver(payload)
        self.assertEqual(value['hardware'], '000A0000')
        self.assertEqual(value['firmware'], 'EXT SPG L1L5 6.00 (041e8a)')

    def test_baud_is_host_configuration_and_old_config_remains_valid(self):
        self.assertNotIn('baud', gnss.validate_config(config()))
        for baud in gnss.BAUDS:
            self.assertEqual(gnss.validate_config(config() | {'baud': baud})['baud'], baud)
            self.assertIn('-s ' + str(baud), cli.gpsd_environment(baud))
        for baud in (True, 9600, '115200'):
            with self.assertRaises(ValueError):
                gnss.validate_config(config() | {'baud': baud})

    def test_no_automatic_baud_or_diagnostic_message_reconfiguration(self):
        before = dict(gnss.KEYS.values()) | {gnss.OUTPUT_KEYS['sig_uart1']: 1}
        for change in ({gnss.KEYS['baud'][0]: 38400}, {gnss.OUTPUT_KEYS['sig_uart1']: 5}):
            with patch.object(cli, 'receiver_values', return_value=before), patch.object(cli, 'exchange') as send:
                with self.assertRaises(ValueError):
                    cli.configure_receiver(1, before | change)
                send.assert_not_called()

    def test_missing_required_report_is_only_a_differential_ram_write(self):
        before = dict(gnss.KEYS.values())
        before[gnss.KEYS['pvt_uart1'][0]] = 0
        after = before | {gnss.KEYS['pvt_uart1'][0]: 1}
        with patch.object(cli, 'receiver_values', side_effect=[before, after]), patch.object(cli, 'exchange') as send:
            cli.configure_receiver(1, gnss.required_values())
            self.assertEqual(send.call_args.args[1], gnss.valset({gnss.KEYS['pvt_uart1'][0]: 1}))
            self.assertEqual(send.call_args.args[1][7], 1)  # RAM only

    def identify(self, baud, responses, fallback=True):
        calls = []
        @contextlib.contextmanager
        def port(path, speed):
            calls.append((path, speed))
            yield 3
        values = dict(gnss.KEYS.values()) | {gnss.KEYS['baud'][0]: baud}
        with patch.object(cli, 'serial_port', side_effect=port), patch.object(cli, 'exchange', side_effect=responses), \
                patch.object(cli, 'mon_ver', return_value={'model': 'NEO-F10N', 'protocol': '40.00'}), \
                patch.object(cli, 'receiver_values', return_value=values):
            with cli.identified_receiver('/dev/ttyUSB9', 115200, fallback) as result:
                self.assertEqual(result[3], baud)
        return calls

    def test_115200_probe_and_38400_power_loss_recovery(self):
        self.assertEqual(self.identify(115200, [b'mon']), [('/dev/ttyUSB9', 115200)])
        self.assertEqual(self.identify(38400, [cli.ReceiverTimeout(), b'mon']),
                         [('/dev/ttyUSB9', 115200), ('/dev/ttyUSB9', 38400)])

    def test_no_baud_retry_for_wrong_model_or_owned_port(self):
        @contextlib.contextmanager
        def port(*_):
            yield 3
        with patch.object(cli, 'serial_port', side_effect=port) as opened, \
                patch.object(cli, 'exchange', return_value=b'bad'), patch.object(cli, 'mon_ver', side_effect=ValueError('wrong model')):
            with self.assertRaises(ValueError):
                with cli.identified_receiver('/dev/ttyUSB9'):
                    self.fail('Admitted wrong receiver')
            self.assertEqual(opened.call_count, 1)

    def test_explicit_baud_does_not_silently_scan_other_speeds(self):
        with self.assertRaises(ValueError):
            self.identify(38400, [cli.ReceiverTimeout()], fallback=False)

    def test_export_rejects_duplicate_layers_and_does_not_open_hardware(self):
        row = {'key': '0x40520001', 'value': 115200, 'valueType': 'U4', 'layer': 0, 'action': 'Set'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli, 'command') as command:
            path = Path(tmp) / 'bad.ucf'
            path.write_text(json.dumps({'type': 'CFG-ITEM', 'items': [row, row]}))
            with self.assertRaises(ValueError):
                gnss.ucf_layers(path)
            command.assert_not_called()

    def test_signal_and_timing_metadata_is_not_a_clock_authorization(self):
        now = [10]
        activity = gnss.MessageActivity(lambda: now[0])
        sig = bytes((0, 0, 0, 0, 0, 2, 0, 0)) + bytes(32)
        activity.update(1, 0x43, sig)
        activity.update(1, 0x21, bytes(20))
        activity.update(13, 1, bytes(16))
        self.assertEqual(activity.status()['signals_reported'], 2)
        self.assertNotIn('utc_valid', activity.status())
        now[0] = 13
        self.assertIsNone(activity.status()['signals_reported'])
        activity.update(1, 0x43, sig[:-1])
        self.assertEqual(activity.status()['signal_age_seconds'], 3)

    @unittest.skipIf(os.name == 'nt', 'POSIX termios/PTYS')
    def test_real_termios_probe_recovers_factory_baud_without_receiver_writes(self):
        import pty
        import termios
        import threading
        import time
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        os.close(slave)
        stop, requests = threading.Event(), []
        os.set_blocking(master, False)
        def receiver():
            decoder = gnss.UBXStream()
            while not stop.is_set():
                try:
                    data = os.read(master, 65536)
                except (BlockingIOError, OSError):
                    time.sleep(0.01)
                    continue
                for cls, msg, payload in decoder.feed(data):
                    requests.append((cls, msg))
                    if termios.tcgetattr(master)[4] != termios.B38400:
                        continue  # simulated baud reversion on power loss
                    if (cls, msg) == (10, 4):
                        response = b'EXT SPG L1L5 6.00 (041e8a)'.ljust(30, b'\0') + b'000A0000'.ljust(10, b'\0')
                        response += b'MOD=NEO-F10N'.ljust(30, b'\0') + b'PROTVER=40.00'.ljust(30, b'\0')
                    elif (cls, msg) == (6, 0x8B):
                        keys = struct.unpack('<' + 'I' * ((len(payload) - 4) // 4), payload[4:])
                        values = dict(gnss.KEYS.values()) | {gnss.KEYS['baud'][0]: 38400}
                        response = bytes((1, 0, 0, 0)) + b''.join(struct.pack('<I', key) +
                            int(values.get(key, 1)).to_bytes(gnss.key_size(key), 'little') for key in keys)
                    else:
                        continue
                    os.write(master, gnss.ubx(cls, msg, response))
        worker = threading.Thread(target=receiver)
        worker.start()
        try:
            with patch.object(cli, 'command', return_value=Mock(returncode=1, stdout='')):
                with cli.identified_receiver(path, 115200) as (_, identity, values, baud):
                    self.assertEqual(baud, 38400)
                    self.assertEqual(identity['hardware'], '000A0000')
            self.assertEqual(requests, [(10, 4), (10, 4), (6, 0x8B)])
        finally:
            stop.set()
            worker.join(timeout=2)
            os.close(master)


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.gate = gnss.TimeGate(lambda: self.now)

    def ready(self):
        for second in range(7):
            self.now = second
            self.gate.update(pvt(second))
        self.assertTrue(self.gate.ready())

    def toff(self, second=6, **changes):
        return {'device': runtime.SERIAL_ALIAS, 'real_sec': int(BASE + second), 'real_nsec': 0,
                'clock_sec': int(BASE + second), 'clock_nsec': 100000000} | changes

    def test_valid_time_and_device_specific_toff_pair(self):
        self.ready()
        sample = self.gate.sample(self.toff(), runtime.SERIAL_ALIAS)
        self.assertAlmostEqual(sample[1], -0.1, places=5)

    def test_no_duplicate_toff_samples(self):
        self.ready()
        self.assertIsNotNone(self.gate.sample(self.toff(), runtime.SERIAL_ALIAS))
        self.assertIsNone(self.gate.sample(self.toff(), runtime.SERIAL_ALIAS))

    def test_wrong_receiver_and_wrong_second_rejected(self):
        self.ready()
        self.assertIsNone(self.gate.sample(self.toff(device='other'), runtime.SERIAL_ALIAS))
        self.assertIsNone(self.gate.sample(self.toff(second=5), runtime.SERIAL_ALIAS))
        self.assertIsNone(self.gate.sample(self.toff(real_nsec=-1), runtime.SERIAL_ALIAS))

    def test_no_fix_and_unresolved_time_rejected(self):
        for payload in (pvt(fix=0), pvt(flags=3), pvt(flags=0), pvt(accuracy=100000001), b'bad'):
            self.gate.update(payload)
            self.assertFalse(self.gate.fresh())

    def test_valid_time_is_separate_from_position(self):
        self.gate.update(pvt(fix=5))  # time-only solution; no coordinates consulted
        self.assertTrue(self.gate.fresh())

    def test_stale_time_expires_without_receiver_messages(self):
        self.ready()
        self.now = 9
        self.assertFalse(self.gate.ready())

    def test_replayed_epoch_and_discontinuity_rejected(self):
        self.ready()
        self.gate.update(pvt(6))
        self.assertFalse(self.gate.ready())
        self.now = 7
        self.gate.update(pvt(30))
        self.assertFalse(self.gate.ready())

    def test_reconnect_requires_new_recovery_interval(self):
        self.ready()
        self.gate.reset()
        self.gate.update(pvt(6))
        self.assertFalse(self.gate.ready())
        self.assertTrue(self.gate.fresh())

    def test_utc_nano_rollover_and_leap_suppression(self):
        value = bytearray(pvt())
        struct.pack_into('<i', value, 16, -5000000)
        self.assertAlmostEqual(gnss.pvt_time(value)[1], BASE - 0.005, places=5)
        value[10] = 60
        with self.assertRaises(ValueError):
            gnss.pvt_time(value)

    @unittest.skipIf(os.name == 'nt', 'Unix socket ABI checked on Linux')
    def test_sock_uses_original_gpsd_timestamp_not_bridge_receipt(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            address = str(Path(temp) / 'clock.sock')
            receiver.bind(address)
            receiver.settimeout(1)
            runtime.send_sample((BASE + 0.125, -0.125), address)
            seconds, micros, offset, pps, leap, pad, magic = struct.unpack('@lldiiii', receiver.recv(128))
            self.assertEqual((seconds, micros, offset, pps, leap, pad, magic), (int(BASE), 125000, -0.125, 0, 0, 0, 0x534F434B))

    def test_chrony_withdraws_both_sources_and_retries_failed_commands(self):
        calls = []
        def command(args):
            calls.append(args)
            return 0, ''
        selection = runtime.Selection(command)
        selection.withdraw()
        self.assertEqual([call[2:] for call in calls if call[1] == 'selectopts'], [['F10P', '+noselect'], ['F10N', '+noselect']])
        selection.set('F10N', True)
        selection.set('F10N', True)
        self.assertEqual(len([call for call in calls if call[1] == 'selectopts']), 3)
        selection.command = lambda args: (1, 'error')
        with self.assertRaises(RuntimeError):
            selection.set('F10P', True)
        self.assertIs(selection.current['F10P'], False)

    def test_no_pps_refclock_is_safe_to_withdraw(self):
        selection = runtime.Selection(lambda args: (1, '503 No such source'))
        selection.withdraw()


class PPSTests(unittest.TestCase):
    def tree(self, root, line=26, falling=False):
        controller = root / 'firmware/devicetree/base/gpio'
        controller.mkdir(parents=True)
        (controller / 'phandle').write_bytes(struct.pack('>I', 3))
        (controller / 'compatible').write_bytes(b'brcm,bcm2835-gpio\0')
        node = root / 'class/pps/pps7/device/of_node'
        node.mkdir(parents=True)
        (node / 'compatible').write_bytes(b'pps-gpio\0')
        (node / 'gpios').write_bytes(struct.pack('>III', 3, line, 0))
        if falling:
            (node / 'assert-falling-edge').touch()

    def test_pps_number_does_not_define_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.tree(root)
            self.assertEqual(gnss.pps_devices(root), ['/dev/pps7'])

    def test_other_gpio_and_falling_edge_not_accepted(self):
        for line, falling in ((18, False), (26, True)):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.tree(root, line, falling)
                self.assertEqual(gnss.pps_devices(root), [])

    def virtual_tree(self, root, line=26, falling=False, name='pps@1a.-1'):
        self.tree(root, line, falling)
        node = root / 'class/pps/pps7/device/of_node'
        destination = root / 'bus/platform/drivers/pps-gpio/pps@1a/of_node'
        destination.parent.mkdir(parents=True)
        node.rename(destination)
        node.parent.rmdir()
        (root / 'class/pps/pps7/name').write_text(name)

    def test_virtual_pps_node_requires_bound_platform_and_gpio_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.virtual_tree(root)
            self.assertEqual(gnss.pps_devices(root), ['/dev/pps7'])

    def test_virtual_serial_other_gpio_wrong_name_and_falling_edge_rejected(self):
        for line, falling, name in ((18, False, 'pps@1a.-1'), (26, True, 'pps@1a.-1'),
                                    (26, False, 'usbserial2'), (26, False, 'pps@1b.-1')):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.virtual_tree(root, line, falling, name)
                self.assertEqual(gnss.pps_devices(root), [])

    def test_virtual_name_alone_without_bound_driver_is_not_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pps = root / 'class/pps/pps4'
            pps.mkdir(parents=True)
            (pps / 'name').write_text('pps@1a.-1')
            self.assertEqual(gnss.pps_devices(root), [])

    def test_pi4_bcm2711_controller_is_supported_for_virtual_and_parented_pps(self):
        for virtual in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (self.virtual_tree if virtual else self.tree)(root)
                (root / 'firmware/devicetree/base/gpio/compatible').write_bytes(b'brcm,bcm2711-gpio\0')
                self.assertEqual(gnss.pps_devices(root), ['/dev/pps7'])

    def test_unqualified_controller_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.virtual_tree(root)
            (root / 'firmware/devicetree/base/gpio/compatible').write_bytes(b'brcm,bcm2712-gpio\0')
            self.assertEqual(gnss.pps_devices(root), [])

    def test_kernel_event_activity_uses_sequence_and_monotonic_age(self):
        now = [10]
        pulse = runtime.PPSActivity(lambda: now[0])
        pulse.observe('/dev/pps7', '1234567890.100000000#1')
        self.assertEqual(pulse.changed, float('-inf'))
        pulse.observe('/dev/pps7', '1234567891.100000000#2')
        self.assertEqual(pulse.changed, 10)
        now[0] = 20
        pulse.observe('/dev/pps7', '1234567891.100000000#2')
        self.assertEqual(pulse.changed, 10)
        pulse.observe('/dev/pps8', '1234567891.100000000#2')
        self.assertEqual(pulse.changed, float('-inf'))


class StatusTests(unittest.TestCase):
    def test_web_public_projection_drops_raw_and_private_receiver_data(self):
        spec = importlib.util.spec_from_file_location('gnss_web_fixture', ROOT / 'web/pcs-control-panel/pcs_control_panel.py')
        web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web)
        value = web.sanitize_public_dashboard({'gnss': {'coordinates': 'PRIVATE', 'receiver': {
            'mode': gnss.NEO, 'diagnostic': 'PPS present; UTC invalid', 'raw': 'PRIVATE', 'lat': 20,
            'device': 'PRIVATE', 'source': {'lat': 20}}}})
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertNotIn('source', value['gnss']['receiver'])
        self.assertEqual(web.dashboard_values(value)['gnssTiming'], 'PPS present; UTC invalid')

    def tracking(self, ref='F10P', leap='Normal'):
        return f'Reference ID : 46313050 ({ref})\nLeap status : {leap}\nSystem time : 0.000001 seconds slow of NTP time\nRoot dispersion : 0.000003 seconds\n'

    def test_chrony_source_presence_is_not_selection(self):
        value = gnss.parse_chrony(self.tracking('time.example'), '#? F10P 0 0 0\n^* 1.2.3.4 0 0 0')
        self.assertEqual(value['source'], 'Internet NTP')
        self.assertEqual(value['pps_selection'], '?')

    def test_pps_usb_legacy_network_and_holdover(self):
        for ref, marker, expected in [('F10P', '#*', 'NEO-F10N PPS'), ('F10N', '#*', 'NEO-F10N USB'),
                                      ('GPS', '#*', 'EM7565 GNSS'), ('1.2.3.4', '^*', 'Internet NTP')]:
            self.assertEqual(gnss.parse_chrony(self.tracking(ref), marker + ' ' + ref)['source'], expected)
        self.assertEqual(gnss.parse_chrony('Reference ID : 7F7F0101\nLeap status : Normal', '')['source'], 'Local holdover')
        self.assertFalse(gnss.parse_chrony(self.tracking(leap='Not synchronised'), '#* F10P')['synchronized'])

    def test_eight_diagnostic_states_and_usb_fallback(self):
        base = {'mode': gnss.NEO, 'connected': True, 'utc_valid': True, 'pps_present': True, 'pps_active': True}
        for state, expected in [({'mode': gnss.LEGACY}, 'Legacy'), ({'connected': False}, 'not connected'),
                                ({'utc_valid': False, 'pps_active': False}, 'UTC unavailable'),
                                ({'pps_present': False}, 'PPS unavailable'), ({'utc_valid': False}, 'UTC invalid'),
                                ({'pps_selection': 'x'}, 'not selected'),
                                ({'source': 'NEO-F10N PPS', 'synchronized': True}, 'validated PPS')]:
            self.assertIn(expected, gnss.diagnostic(base | state))

    def test_public_snapshot_filters_raw_data_and_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gnss.atomic_json(root / 'config', config())
            gnss.atomic_json(root / 'status.json', {'mode': gnss.NEO, 'state': 'fresh', 'updated_monotonic': 100,
                                                 'utc_valid': True, 'lat': 10, 'raw': 'PRIVATE', 'device': 'PRIVATE'})
            result = gnss.read_status(root, root / 'config', 101)
            self.assertTrue(result['utc_valid'])
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertNotIn('lat', result)
            self.assertFalse(gnss.read_status(root, root / 'config', 200)['utc_valid'])

    def test_manual_position_does_not_hide_timing_failure(self):
        stats = gpio.StatsSnapshot(30, 60, 0, False, position_source='Manual', gnss_timing='Internet NTP', gnss_degraded=True)
        health = gpio.MatrixHealthSnapshot(stats, 10, True, 0)
        self.assertIn('gnss_timing', [item.name for item in gpio.matrix_alerts(health)])
        self.assertNotIn('gps_fix', [item.name for item in gpio.matrix_alerts(health)])
        self.assertEqual(gpio.led_status_indicators(health)[5].state, 'timing_degraded')
        self.assertTrue(any(page[0] == 'GNSS Time / PPS' for page in gpio.lcd_status_pages(stats, 10)))


class InstallationTests(unittest.TestCase):
    def test_serial_only_activation_retains_calibration_and_explicit_pps_gate(self):
        value = config() | {'pps_enabled': False}
        self.assertFalse(gnss.validate_config(value)['pps_enabled'])
        for change in ({'pps_enabled': 0}, {'pps_enabled': 'false'}, {'serial_offset': None}):
            with self.assertRaises(ValueError):
                gnss.validate_config(value | change)

    def test_uncommissioned_pps_never_selected_even_with_valid_utc_and_noise(self):
        selection = Mock()
        runtime.select_time_sources(selection, True, True, False)
        self.assertEqual(selection.set.call_args_list, [unittest.mock.call('F10P', False), unittest.mock.call('F10N', True)])
        selection.reset_mock()
        runtime.select_time_sources(selection, False, True, True)
        self.assertEqual(selection.set.call_args_list, [unittest.mock.call('F10P', False), unittest.mock.call('F10N', False)])

    def test_serial_only_prepare_pps_has_no_kernel_refclock_or_alias(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'RUNTIME', Path(temp)), \
                patch.object(cli, 'load_config', return_value=config() | {'pps_enabled': False}), \
                patch.object(cli, 'pps_devices', return_value=['/dev/pps7']) as devices:
            (Path(temp) / 'pps').write_text('stale')
            cli.prepare_pps()
            self.assertFalse((Path(temp) / 'pps').exists())
            self.assertNotIn('refclock', (Path(temp) / 'pps.conf').read_text())
            devices.assert_not_called()

    def test_serial_only_preflight_requires_no_wiring_claim_or_pps_device(self):
        def command(args, **kwargs):
            values = {'gpsd': 'gpsd: 3.25', 'chronyd': 'chronyd version 4.6.1', 'pinctrl': '26: ip pn | hi'}
            return Mock(stdout=('pool example\nlocal stratum 10\nrtcsync\n' if '-p' in args
                                else values[args[0]]))
        def read(path, *args, **kwargs):
            return ('confdir /etc/chrony/conf.d\n' if str(path).endswith('chrony.conf')
                    else 'fixture-boot-id')
        with patch.object(cli, 'usb_devices', return_value=[config()['receiver'] | {'device': '/dev/ttyUSB9'}]), \
                patch.object(cli, 'pps_devices', return_value=[]), patch.object(cli, 'file_state', return_value=None), \
                patch.object(cli, 'command', side_effect=command) as commands, \
                patch.object(Path, 'read_text', read), patch.object(Path, 'exists', return_value=False):
            result = cli.preflight(config() | {'pps_enabled': False})
            self.assertEqual(result['blockers'], [])
            self.assertFalse(result['gpio_confirmed'])
            self.assertFalse(any(call.args[0][0] == 'pinctrl' for call in commands.call_args_list))
            self.assertIn('Unique GPIO26', ';'.join(cli.preflight(config())['blockers']))

    def test_pairing_enabled_only_after_serial_reference(self):
        selection = Mock()
        runtime.select_time_sources(selection, True, True, True)
        self.assertEqual(selection.set.call_args_list, [unittest.mock.call('F10N', True), unittest.mock.call('F10P', True)])

    @unittest.skipIf(os.name == 'nt', 'POSIX ownership and symlink restoration')
    def test_rollback_restores_bytes_permissions_and_absence_in_sorted_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            z, a, absent = root / 'z', root / 'a', root / 'absent'
            z.write_text('z original')
            a.write_text('a original')
            z.chmod(0o640)
            managed = (str(z), str(a), str(absent))
            result = Mock(stdout='disabled', returncode=0)
            with patch.object(cli, 'BACKUPS', root / 'backups'), patch.object(cli, 'MANAGED', managed), \
                    patch.object(cli, 'command', return_value=result) as command, patch.object(cli, 'apparmor'):
                target, receipt = cli.backup()
                z.write_text('changed')
                a.write_text('changed')
                absent.write_text('new')
                cli.restore(target)
                self.assertEqual(z.read_text(), 'z original')
                self.assertEqual(a.read_text(), 'a original')
                self.assertFalse(absent.exists())
                self.assertEqual(z.stat().st_mode & 0o777, 0o640)
                self.assertFalse(any('ModemManager' in str(call) for call in command.call_args_list))
                self.assertFalse(any('start' in call.args[0] and 'pcs-wwan-gps-nmea.service' in call.args[0] for call in command.call_args_list))

    def test_repeat_activation_is_a_noop(self):
        marker = {'complete': True, 'config_digest': cli.config_digest(config())}
        with patch.object(cli, 'load_config', return_value=config()), patch.object(cli, 'command') as command, \
                patch.object(Path, 'read_text', return_value=json.dumps(marker)):
            self.assertEqual(cli.activate(config(), Path('/missing-receipt'), True)['changes'], False)
            command.assert_not_called()

    def test_rollback_never_opens_a_renumbered_or_foreign_modem_port(self):
        binding = {'vid': '1199', 'pid': '9091', 'serial': 'modem', 'id_path': 'usb-path',
                   'device': '/dev/ttyUSB1', 'interface': '02'}
        for current in (binding | {'device': '/dev/ttyUSB8'}, binding | {'serial': 'foreign'},
                        binding | {'interface': '03'}):
            with patch.object(cli, 'usb_devices', return_value=[current]):
                self.assertFalse(cli.legacy_matches(binding))
        with patch.object(cli, 'usb_devices', return_value=[binding]):
            self.assertTrue(cli.legacy_matches(binding))
        with patch.object(cli, 'usb_devices', return_value=[binding,
                binding | {'device': '/dev/ttyUSB0', 'interface': '00'},
                binding | {'device': '/dev/ttyUSB3', 'interface': '03'}]):
            self.assertTrue(cli.legacy_matches(binding))

    def test_activation_failure_triggers_recorded_software_rollback(self):
        import contextlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = {'created_epoch': __import__('time').time(), 'blockers': [], 'config_digest': 'fixture',
                       'gpio_confirmed': True, 'device': '/dev/fixture', 'pps': ['/dev/pps7'],
                       'files': {}, 'versions': {}, 'boot_id': 'fixture'}
            path = root / 'preflight'
            path.write_text(json.dumps(receipt))
            with patch.object(cli, 'load_config', return_value=gnss.DEFAULT), \
                    patch.object(cli, 'preflight', return_value=receipt), patch.object(cli, 'stage', return_value=root), \
                    patch.object(cli, 'backup', return_value=(root, {})), patch.object(cli, 'command', side_effect=RuntimeError('stop failed')), \
                    patch.object(cli, 'restore') as restore:
                with self.assertRaises(RuntimeError):
                    cli.activate(config(), path, True)
                restore.assert_called_once_with(root)

    def test_chrony_pairing_and_serial_fallback_are_distinct(self):
        text = cli.chrony_config(config())
        self.assertIn('refid F10N', text)
        self.assertIn('prefer noselect', text)
        self.assertNotIn('trust', text)
        self.assertNotIn('SHM', text)

    def test_staging_only_writes_versioned_inactive_artifacts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'STAGES', Path(temp)), patch.object(cli, 'command') as command:
            first = cli.stage()
            before = {path.name: path.stat().st_mtime_ns for path in first.iterdir()}
            self.assertEqual(first, cli.stage())
            self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in first.iterdir()})
            self.assertEqual(len(list(first.iterdir())), 4)
            command.assert_not_called()

    def test_config_and_service_state_snapshot_preserves_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file = root / 'existing'
            file.write_text('original')
            with patch.object(cli, 'BACKUPS', root / 'backups'), patch.object(cli, 'MANAGED', (str(file), str(root / 'absent'))), patch.object(cli, 'command', return_value=Mock(stdout='active')):
                target, receipt = cli.backup()
            self.assertEqual((target / '0').read_text(), 'original')
            self.assertIsNone(receipt['files'][str(root / 'absent')])

    def test_activation_refuses_stale_or_changed_receipts_before_commands(self):
        value = {'created_epoch': 0, 'blockers': [], 'config_digest': 'test'}
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load_config', return_value=gnss.DEFAULT), \
                patch.object(cli, 'preflight', return_value=value), patch.object(cli, 'command') as command:
            path = Path(temp) / 'receipt'
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                cli.activate(config(), path, True)
            command.assert_not_called()

    def test_failed_time_samples_never_have_lte_side_effects(self):
        # The complete runtime command vocabulary is limited to Chrony and GPSD.
        import ast
        tree = ast.parse((ROOT / 'scripts/pcs_gnss_runtime.py').read_text())
        commands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value in ('chronyc', 'systemctl'):
                commands.append([item.value for item in node.elts if isinstance(item, ast.Constant)])
        self.assertTrue(commands)
        self.assertTrue(all(call[0] == 'chronyc' or call in (['systemctl', 'restart', '--no-block', 'gpsd.service'],
                                                          ['systemctl', 'restart', 'chrony.service']) for call in commands))


if __name__ == '__main__':
    unittest.main()
