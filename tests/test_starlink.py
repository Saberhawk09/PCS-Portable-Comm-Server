import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


s = module('pcs_starlink')
life = module('pcs_starlink_lifecycle')


def config(**values):
    return s.load_config(ROOT / 'config/starlink.example.json') | values


class TelemetryTests(unittest.TestCase):
    def test_mac_resolution_follows_usb_rename_and_excludes_lan(self):
        uplink = SimpleNamespace(id='starlink', type='ethernet', mac='00:11:22:33:44:55', interface='')
        loader = SimpleNamespace(load_config=lambda: SimpleNamespace(uplinks=[uplink]))
        devices = [Path('/sys/class/net') / name for name in ('lo', 'eth0', 'enx001122334455')]
        with patch.dict(sys.modules, pcs_uplink_manager=loader), patch.object(Path, 'iterdir', return_value=devices), patch.object(Path, 'exists', return_value=True), patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Permanent address: 00:11:22:33:44:55')) as run:
            self.assertEqual(s.resolve_interface(config()), 'enx001122334455')
            self.assertEqual(run.call_count, 1)
            uplink.interface = 'old-usb-name'
            with self.assertRaisesRegex(ValueError, 'absent or ambiguous'):
                s.resolve_interface(config())

    def test_ambiguous_mac_and_non_ethernet_are_rejected(self):
        uplink = SimpleNamespace(id='starlink', type='ethernet', mac='00:11:22:33:44:55', interface='')
        loader = SimpleNamespace(load_config=lambda: SimpleNamespace(uplinks=[uplink]))
        with patch.dict(sys.modules, pcs_uplink_manager=loader), patch.object(Path, 'iterdir', return_value=[Path('/sys/class/net/enx1'), Path('/sys/class/net/enx2')]), patch.object(Path, 'exists', return_value=True), patch.object(s.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Permanent address: 00:11:22:33:44:55')):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                s.resolve_interface(config())
            uplink.type = 'wifi'
            with self.assertRaisesRegex(ValueError, 'Ethernet'):
                s.resolve_interface(config())

    def test_default_disabled_never_contacts_dish(self):
        call = Mock()
        result = s.sample(config(), call)
        call.assert_not_called()
        self.assertFalse(result['available'])
        self.assertEqual(result['state'], 'DISABLED')

    def test_only_status_polled_and_private_fields_removed(self):
        call = Mock(return_value={'state': 'CONNECTED', 'status': 'ok', 'latency_ms': 0,
                                  'device_id': 'secret', 'location': 'secret', 'allow_reboot': True})
        result = s.sample(config(enabled=True), call)
        call.assert_called_once_with('status')
        self.assertEqual(result['latency_ms'], 0)
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('allow_reboot', result)

    def test_protocol_status_metrics_and_outages(self):
        result = s.normalize({'device_state': {'uptime_s': '120'}, 'pop_ping_latency_ms': 30,
            'pop_ping_drop_rate': .2, 'downlink_throughput_bps': 0,
            'obstruction_stats': {'fraction_obstructed': .1, 'currently_obstructed': True},
            'outage': {'cause': 'OBSTRUCTED'}, 'alerts': {'thermal_throttle': True, 'secret': True},
            'device_info': {'id': 'secret'}, 'location': {'latitude': 123}})
        self.assertEqual(result['uptime_seconds'], 120)
        self.assertEqual(result['packet_loss_percent'], 20)
        self.assertEqual(result['obstruction_percent'], 10)
        self.assertEqual(result['state'], 'OBSTRUCTED')
        self.assertEqual(result['downlink_bps'], 0)
        self.assertEqual(result['alerts_summary'], 'thermal_throttle')
        self.assertNotIn('secret', json.dumps(result))
        for value in (None, True, -1, float('nan'), float('inf'), 'bad'):
            self.assertIsNone(s.number(value))

    def test_failed_poll_and_expired_cache_cannot_look_live(self):
        result = s.sample(config(enabled=True), Mock(side_effect=subprocess.TimeoutExpired('fake', 10)))
        self.assertFalse(result['available'])
        self.assertNotIn('latency_ms', result)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'status.json'
            p.write_text(json.dumps(dict(result, collected_at=100, available=True, latency_ms=12)))
            self.assertTrue(s.cached_status(p, now=110)['available'])
            for now in (99, 146):
                stale = s.cached_status(p, now=now)
                self.assertFalse(stale['available'])
                self.assertNotIn('latency_ms', stale)

    def test_outer_child_deadline_is_enforced(self):
        with patch.object(s.subprocess, 'run', side_effect=subprocess.TimeoutExpired('child', 10)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                s.invoke('status')
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_configuration_rejects_unpaired_controls_and_unknown_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'config.json'
            for change in ({'allow_reboot': True}, {'follow_pcs_reboot': True},
                           {'enabled': 'yes'}, {'target': 'outside.invalid'}, {'poll_seconds': 0}):
                p.write_text(json.dumps(config(**change)))
                with self.assertRaises(ValueError):
                    s.load_config(p)


class ControlTests(unittest.TestCase):
    def test_disabled_and_shutdown_never_submit_rpc(self):
        call = Mock()
        self.assertEqual(s.control('reboot', config(), call)['result'], 'disabled')
        armed = config(enabled=True, paired_device_id='ut-test', allow_reboot=True)
        self.assertEqual(s.control('shutdown', armed, call)['result'], 'unsupported')
        call.assert_not_called()

    def test_identity_mismatch_refuses_reboot_after_fresh_status(self):
        reply = SimpleNamespace(HasField=lambda key: True,
                                dish_get_status=SimpleNamespace(device_info=SimpleNamespace(id='wrong')))
        stub = SimpleNamespace(Handle=Mock(return_value=reply))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            s.rpc_request(stub, lambda **kw: kw, 'reboot', 'ut-paired')
        self.assertEqual(stub.Handle.call_args_list[0].args[0], {'get_status': {}})
        self.assertEqual(stub.Handle.call_count, 1)

    def test_paired_reboot_is_one_explicit_rpc_after_status(self):
        reply = SimpleNamespace(HasField=lambda key: True,
                                dish_get_status=SimpleNamespace(device_info=SimpleNamespace(id='ut-paired')))
        stub = SimpleNamespace(Handle=Mock(side_effect=[reply, object()]))
        self.assertEqual(s.rpc_request(stub, lambda **kw: kw, 'reboot', 'ut-paired')['result'], 'request_accepted')
        self.assertEqual([c.args[0] for c in stub.Handle.call_args_list], [{'get_status': {}}, {'reboot': {}}])

    def test_timeout_is_not_retried(self):
        call = Mock(side_effect=subprocess.TimeoutExpired('fake', 10))
        result = s.control('reboot', config(enabled=True, allow_reboot=True, paired_device_id='ut-test'), call)
        self.assertEqual(result['result'], 'unconfirmed')
        self.assertEqual(call.call_count, 1)

    def test_follow_requires_separate_explicit_flag(self):
        call = Mock(return_value={'result': 'request_accepted'})
        cfg = config(enabled=True, allow_reboot=True, paired_device_id='ut-test')
        self.assertEqual(s.follow('reboot', cfg, call)['result'], 'disabled')
        call.assert_not_called()
        self.assertEqual(s.follow('reboot', cfg | {'follow_pcs_reboot': True}, call)['result'], 'request_accepted')

    def test_pcs_completes_after_mini_timeout_or_failure(self):
        for action, verb in [('reboot', 'reboot'), ('shutdown', 'poweroff')]:
            for failure in (subprocess.TimeoutExpired('fake', 12), SimpleNamespace(returncode=1)):
                run = Mock(side_effect=[failure, SimpleNamespace(returncode=0)])
                life.perform(action, run)
                self.assertEqual(run.call_args_list[-1].args[0], ['systemctl', '--no-block', verb])
                self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
