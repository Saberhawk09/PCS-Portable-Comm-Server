import copy
import importlib.util
import json
import os
import stat
from types import SimpleNamespace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_switch_monitor as s
from test_uplink_manager import m, config, observations, FakeNM
from dataclasses import replace


def cfg():
    return s.DEFAULTS | {'enabled': True, 'mapping': {'physical_port': 2, 'ifindex': 17, 'identity': 'a' * 64, 'verified_at': 1}}


def observed(oper=1, admin=1, uptime=1000):
    return dict(identity='a' * 64, uptime=uptime, ports={'17': {'descr': 'port', 'admin': admin, 'oper': oper}})


class SwitchTests(unittest.TestCase):
    def test_defaults_and_rejected_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'config'
            self.assertFalse(s.load_config(p)['enabled'])
            for field, value in [('traps_enabled', True), ('snmp_version', '3'), ('address', '8.8.8.8'), ('retries', 99), ('poll_seconds', True), ('community_file', '/tmp/leak')]:
                p.write_text(json.dumps(cfg() | {field: value}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    s.load_config(p)
            p.write_text(json.dumps(cfg()))
            self.assertEqual(s.load_config(p)['mapping']['ifindex'], 17)

    def test_parser_numeric_enums_ticks_and_unsupported(self):
        result = s.parse_output('.1.3.6.1 2\n.1.3.6.2 "Port 2"\n.1.3.6.3 No Such Instance currently exists\n.1.3.6.4 "123"')
        self.assertEqual(result, {'1.3.6.1': 2, '1.3.6.2': 'Port 2', '1.3.6.4': '123'})
        for text in ['garbage', '.1.3 1\n.1.3 2', 'x' * 65537]:
            with self.assertRaises(ValueError): s.parse_output(text)

    def test_readonly_transport_private_credential_and_no_logging(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'community'; p.write_text('SyntheticTest123'); p.chmod(0o600)
            with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': d}):
                transport = s.NetSnmp(cfg())
            def run(command, **kwargs):
                self.assertEqual(command[0], '/usr/bin/snmpget')
                self.assertNotIn('SyntheticTest123', ' '.join(command))
                self.assertNotIn('SyntheticTest123', str(kwargs['env']))
                conf = Path(kwargs['env']['SNMPCONFPATH']) / 'snmp.conf'
                self.assertEqual(conf.read_text(), 'defCommunity SyntheticTest123\n')
                self.assertLessEqual(kwargs['timeout'], 5)
                kwargs['stdout'].write(b'.1.3 "SyntheticTest123"\n')
                return type('Done', (), {'returncode': 0})()
            with patch.object(s.subprocess, 'run', side_effect=run):
                self.assertEqual(transport.request(['1.3'], s.time.monotonic() + 5), {'1.3': '<redacted>'})
            with patch.object(s.subprocess, 'run', side_effect=OSError('SyntheticTest123')):
                with self.assertRaisesRegex(TimeoutError, '^SNMP request unavailable$'):
                    transport.request(['1.3'], s.time.monotonic() + 1)

    @unittest.skipUnless(os.name == 'posix', 'systemd credential mount permissions')
    def test_systemd_private_readonly_mount_and_ordinary_file_permissions(self):
        path = Path('/run/credentials/pcs-switch-monitor.service/community')
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_uid=0, st_size=16)
        parent = SimpleNamespace(st_mode=stat.S_IFDIR | 0o550, st_uid=0)
        with patch.object(Path, 'lstat', return_value=parent), patch.object(s.os, 'statvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            self.assertTrue(s.systemd_readonly_credential(path, info, str(path.parent)))
            self.assertFalse(s.systemd_readonly_credential(Path('/tmp/community'), info, '/tmp'))
            self.assertFalse(s.systemd_readonly_credential(path, info, None))
            for mode in (0o444, 0o460, 0o660):
                self.assertFalse(s.systemd_readonly_credential(path, SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=0), str(path.parent)))
            with patch.object(s.os, 'statvfs', return_value=SimpleNamespace(f_flag=0)):
                self.assertFalse(s.systemd_readonly_credential(path, info, str(path.parent)))
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'community'; file.write_text('SyntheticTest123'); file.chmod(0o440)
            with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': directory}), self.assertRaises(ValueError):
                s.credential(cfg())

    def test_credential_generation_exclusive_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'secret'
            s.main(['--generate-community', '--output', str(p)])
            self.assertRegex(p.read_text().strip(), '^[A-Za-z0-9]{16}$')
            with self.assertRaises(FileExistsError): s.main(['--generate-community', '--output', str(p)])

    def test_link_states_timeout_reboot_and_mapping(self):
        monitor = s.Monitor(cfg(), 'boot')
        self.assertEqual(monitor.update(observed(), 10, 100)['state'], 'up')
        self.assertEqual(monitor.update(observed(2), 15, 105)['state'], 'down')
        self.assertEqual(monitor.update(observed(2, 2), 20, 110)['state'], 'administratively_down')
        self.assertEqual(monitor.update(None, 25, 115, 'poll_failed')['state'], 'unknown')
        reboot = monitor.update(observed(2, uptime=1), 30, 120)
        self.assertEqual(reboot['state'], 'unknown')
        self.assertEqual(monitor.update(observed(2, uptime=2), 35, 125)['state'], 'down')
        self.assertEqual(monitor.update(observed() | {'identity': 'b' * 64}, 40, 130)['state'], 'unknown')
        self.assertEqual(s.Monitor(cfg() | {'enabled': False}, 'boot').update(None, 1, 1)['state'], 'monitor_unavailable')

    def test_restart_does_not_invent_transition_or_trust_old_confirmation(self):
        first = s.Monitor(cfg(), 'boot').update(observed(), 10, 100)
        monitor = s.Monitor(cfg(), 'boot', first)
        second = monitor.update(observed(), 20, 110)
        self.assertEqual(first['last_transition'], second['last_transition'])
        self.assertNotEqual(first['generation'], second['generation'])
        self.assertEqual(second['state_since_mono'], 20)
        monitor.update(None, 25, 115, 'poll_failed')
        self.assertEqual(monitor.update(observed(), 30, 120)['last_transition'], 100)

    def test_cache_stale_boot_configuration_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            conf, cache = Path(d) / 'config', Path(d) / 'cache'
            conf.write_text(json.dumps(cfg()))
            good = s.Monitor(cfg(), 'boot').update(observed(2), 10, 100)
            cache.write_text(json.dumps(good))
            def read(now=11, boot='boot'): return s.cached_status(cache, conf, now, boot)
            self.assertEqual(read()['state'], 'down')
            self.assertEqual(read(31)['state'], 'stale')
            self.assertEqual(read(9)['state'], 'stale')
            self.assertEqual(read(11, 'new')['state'], 'monitor_unavailable')
            for broken in [[], {}, good | {'state_since_mono': 'bad'}, good | {'config_digest': 'wrong'}, good | {'sequence': False}]:
                cache.write_text(json.dumps(broken)); self.assertEqual(read()['state'], 'monitor_unavailable')
            cache.write_text(json.dumps(good | {'ifindex': 2})); self.assertEqual(read()['state'], 'unknown')

    def test_explicit_cable_cycle_nonmatching_index_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [observed(oper) | {'boot': 'boot', 'address': '10.42.0.4', 'sampled_mono': i + 1} for i, oper in enumerate([1, 2, 1])]
            for r in rows: r['ports']['7'] = {'descr': 'other', 'admin': 1, 'oper': 1}
            paths = [Path(d) / str(i) for i in range(3)]
            def save():
                for p, r in zip(paths, rows): p.write_text(json.dumps(r))
            save()
            self.assertEqual(s.verify_mapping(*paths, 2, 17)['ifindex'], 17)
            with self.assertRaises(ValueError): s.verify_mapping(*paths, 2, 2)
            rows[1]['ports']['7']['oper'] = 2; save()
            with self.assertRaisesRegex(ValueError, 'ambiguous'): s.verify_mapping(*paths, 2, 17)

    def test_optional_oids_do_not_break_core_poll(self):
        class Transport:
            def request(self, oids, deadline, walk=False):
                if walk: return {s.OIDS['descr'] + '.17': 'port'}
                if s.OIDS['sys_descr'] in oids: return {s.OIDS['sys_descr']: 'firmware', s.OIDS['sys_object_id']: '.1.3.6', s.OIDS['uptime']: 100}
                if s.OIDS['admin'] + '.17' in oids: return {s.OIDS['admin'] + '.17': 1, s.OIDS['oper'] + '.17': 2}
                raise TimeoutError('unsupported')
        with patch.object(s, 'boot_id', return_value='boot'):
            result = s.sample(cfg(), Transport())
        self.assertEqual(result['ports']['17']['oper'], 2)
        self.assertIsNone(result['speed'])


class IntegrationPolicyTests(unittest.TestCase):
    def config(self, **kwargs):
        c = config()
        return replace(c, uplinks=(replace(c.uplinks[0], type='vlan', interface='eth0.20', parent='eth0', vlan_id=20), *c.uplinks[1:]), startup_grace_enabled=True, switch_assist=True, **kwargs)

    def test_grace_absolute_deadline_restart_and_irreversible_close(self):
        c = self.config(); state = {}; g = m.StartupGrace(c, state)
        self.assertTrue(g.update({}, False, 10)['held'])
        self.assertEqual(m.StartupGrace(c, state).update({}, False, 200)['remaining_seconds'], 40)
        self.assertFalse(g.update({}, False, 240)['held'])
        self.assertFalse(m.StartupGrace(replace(c, startup_grace_seconds=900), state).update({}, False, 241)['held'])
        state = {}; g = m.StartupGrace(c, state)
        self.assertFalse(g.update({}, True, 100)['held'])
        self.assertFalse(m.StartupGrace(c, state).update({}, False, 120)['held'])

    def test_grace_optin_eligibility_manual_and_missing_uptime(self):
        for c, elapsed, suppressed in [(config(), 5, []), (self.config(mode='manual'), 5, []), (self.config(), None, []), (self.config(), 5, ['starlink']), (replace(self.config(), uplinks=config(False).uplinks), 5, [])]:
            self.assertFalse(m.StartupGrace(c, {}).update({}, False, elapsed, suppressed)['held'])

    def test_controller_boot_grace_and_nm_restart_without_monitor(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'switch_status', return_value={'state': 'unknown'}), patch.object(m, 'boot_elapsed', return_value=10):
            nm = FakeNM(observations()); c = m.Controller(self.config(), nm, d, boot='boot')
            c.step(0); result = c.step(40)
            self.assertTrue(result['startup_grace']['held']); self.assertEqual(nm.activated, [])
            with patch.object(nm, 'daemon', return_value='new'), patch.object(m, 'boot_elapsed', return_value=200):
                c = m.Controller(self.config(), nm, d, boot='boot')
                self.assertEqual(c.step(200)['startup_grace']['remaining_seconds'], 40)
            with patch.object(nm, 'daemon', return_value='new'), patch.object(m, 'boot_elapsed', return_value=241):
                self.assertFalse(c.step(241)['startup_grace']['held'])
                self.assertEqual(nm.activated, ['cellular'])

    def test_connected_operator_cellular_not_delayed_or_adopted(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'switch_status', return_value={}), patch.object(m, 'boot_elapsed', return_value=10):
            nm = FakeNM(observations(cellular=True)); c = m.Controller(self.config(), nm, d, boot='boot')
            c.step(0); result = c.step(40)
            self.assertEqual(result['selected_id'], 'cellular')
            self.assertFalse(c.state['owned']); self.assertFalse(nm.disconnected)

    def test_healthy_recovery_closes_grace_and_manual_connect_bypasses_it(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'switch_status', return_value={}), patch.object(m, 'boot_elapsed', return_value=50):
            nm = FakeNM(observations(starlink=True)); c = m.Controller(self.config(), nm, d, boot='boot')
            self.assertTrue(c.step(0)['startup_grace']['held'])
            result = c.step(30)
            self.assertEqual(result['selected_id'], 'starlink')
            self.assertFalse(result['startup_grace']['held'])
            nm.obs['starlink'].internet = False
            self.assertFalse(c.step(35)['startup_grace']['held'])
            c.operator('connect', c.config.uplinks[-1])
            self.assertEqual(nm.activated, ['cellular']); self.assertFalse(c.state['owned'])
            restarted = m.Controller(self.config(), nm, d, boot='newboot')
            self.assertTrue(restarted.startup_grace.update({}, False, 5)['held'])

    def test_early_grace_completion_survives_runtime_state_reload(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'switch_status', return_value={}), patch.object(m, 'boot_elapsed', return_value=60):
            nm = FakeNM(observations(starlink=True)); c = m.Controller(self.config(), nm, d, boot='boot')
            c.step(0)
            c.state = m.read_json(Path(d) / 'state.json')
            self.assertFalse(c.step(30)['startup_grace']['held'])
            nm.obs['starlink'].internet = False
            restarted = m.Controller(self.config(), nm, d, boot='boot')
            self.assertFalse(restarted.step(40)['startup_grace']['held'])

    def test_verified_down_releases_grace_after_existing_failure_timers(self):
        cache = dict(state='down', reachable=True, error='', physical_port=2, generation='g', sequence=1, sampled_mono=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'switch_status', side_effect=lambda: cache.copy()), patch.object(m, 'boot_elapsed', return_value=40):
            nm = FakeNM(observations()); controller = m.Controller(self.config(), nm, directory, boot='boot')
            self.assertTrue(controller.step(0)['startup_grace']['held'])
            cache.update(sequence=2, sampled_mono=10)
            self.assertFalse(controller.step(10)['startup_grace']['held'])
            self.assertEqual(nm.activated, [])
            cache.update(sequence=3, sampled_mono=30)
            result = controller.step(30)
            self.assertEqual(nm.activated, ['cellular'])
            self.assertEqual(result['startup_grace']['reason'], 'physical_wan_disconnected')
            self.assertTrue(result['startup_grace']['cellular_ready'])

    def test_link_reconnection_restores_remaining_startup_grace(self):
        grace = m.StartupGrace(self.config(), {})
        self.assertFalse(grace.update(observations(), False, 40, physical_down_confirmed=True)['held'])
        result = grace.update(observations(), False, 50, physical_down_confirmed=False)
        self.assertTrue(result['held']); self.assertEqual(result['remaining_seconds'], 190)

    def test_healthy_or_unknown_preferred_health_cannot_release_grace(self):
        for source, field, value in [('wifi', 'internet', True), ('wifi', 'internet6', True), ('wifi', 'internet', None), ('wifi', 'internet6', None), ('starlink', 'internet', True)]:
            obs = observations(); setattr(obs[source], field, value)
            self.assertTrue(m.StartupGrace(self.config(), {}).update(obs, False, 40, physical_down_confirmed=True)['held'])
        c = replace(self.config(), switch_assist=False)
        self.assertTrue(m.StartupGrace(c, {}).update(observations(), False, 40, physical_down_confirmed=True)['held'])

    def test_unknown_physical_states_retain_finite_deadline(self):
        for state in ['up', 'unknown', 'stale', 'monitor_unavailable', 'administratively_down']:
            cache = dict(state=state, reachable=True, error='', physical_port=2, generation='g', sequence=1, sampled_mono=0)
            with tempfile.TemporaryDirectory() as directory, patch.object(m, 'switch_status', side_effect=lambda: cache.copy()), patch.object(m, 'boot_elapsed', return_value=40):
                nm = FakeNM(observations()); controller = m.Controller(self.config(), nm, directory, boot='boot')
                controller.step(0); cache.update(sequence=2, sampled_mono=40)
                self.assertTrue(controller.step(40)['startup_grace']['held'])
                self.assertEqual(nm.activated, [])
                with patch.object(m, 'boot_elapsed', return_value=241):
                    controller.step(50)
                    self.assertEqual(nm.activated, ['cellular'])

    def test_unready_modem_waits_without_consuming_retry_backoff(self):
        cache = dict(state='down', reachable=True, error='', physical_port=2, generation='g', sequence=1, sampled_mono=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'switch_status', side_effect=lambda: cache.copy()), patch.object(m, 'boot_elapsed', return_value=40):
            nm = FakeNM(observations()); controller = m.Controller(self.config(), nm, directory, boot='boot')
            controller.step(0); cache.update(sequence=2, sampled_mono=30)
            with patch.object(nm, 'cellular_ready', return_value=False):
                result = controller.step(30)
                self.assertEqual(result['startup_grace']['reason'], 'waiting_for_modem')
                self.assertEqual(nm.activated, []); self.assertNotIn('cellular', controller.policy.retry_at)
            cache.update(sequence=3, sampled_mono=40); controller.step(40)
            self.assertEqual(nm.activated, ['cellular'])

    def test_ipv6_wifi_prevents_paid_activation_after_grace_expires(self):
        obs = observations(); obs['wifi'].internet6 = True
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'switch_status', return_value={}), patch.object(m, 'boot_elapsed', return_value=250):
            nm = FakeNM(obs); controller = m.Controller(self.config(), nm, directory, boot='boot')
            controller.step(0); result = controller.step(40)
            self.assertEqual(nm.activated, [])
            self.assertEqual(result['startup_grace']['reason'], 'preferred_wan_healthy')

    def test_api_nested_allowlists_discard_topology_credentials_and_arbitrary_text(self):
        spec = importlib.util.spec_from_file_location('switch_test_api', ROOT / 'web/pcs-control-panel/pcs_stats_api.py')
        api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
        dashboard = {'network': {
            'switch_monitor': {'state': 'down', 'ifindex': 17, 'address': '10.42.0.4', 'community': 'secret', 'error': 'secret', 'reachable': True},
            'wan_startup': {'state': 'WAN Starting', 'held': True, 'remaining_seconds': 120, 'uplink': 'private'},
        }}
        self.assertEqual(api.sanitize_sections(dashboard)['network'], {})
        result = api.add_authenticated_details({}, 'network', dashboard)['details']['network_diagnostics']
        self.assertEqual(result['switch_monitor'], {'state': 'down', 'reachable': True})
        self.assertNotIn('uplink', result['wan_startup'])
        result = api.sanitize_switch_diagnostics({'switch_monitor': {'state': 'secret'}})
        self.assertEqual(result['switch_monitor'], {})

    def test_physical_assist_distinct_polls_and_fallback_guards(self):
        c = self.config(); obs = observations(); assist = m.PhysicalAssist()
        cache = dict(state='down', reachable=True, error='', physical_port=2, sequence=1, generation='g', sampled_mono=1)
        self.assertEqual(assist.overrides(c, cache, obs, 1), {})
        self.assertEqual(assist.overrides(c, cache, obs, 20), {})
        cache.update(sequence=2, sampled_mono=21)
        self.assertEqual(assist.overrides(c, cache, obs, 21), {'starlink': 0})
        for state in ['unknown', 'stale', 'monitor_unavailable', 'up', 'administratively_down']:
            self.assertEqual(assist.overrides(c, cache | {'state': state}, obs, 22), {})
        obs['starlink'].link = False
        self.assertEqual(assist.overrides(c, cache, obs, 23), {})
        obs['starlink'].link = True
        self.assertEqual(assist.overrides(c, cache | {'generation': 'restart'}, obs, 24), {})

    def test_acceleration_requires_failed_health_and_other_preferred_wans(self):
        c = self.config(); p = m.Policy(c); p.selected = 'starlink'
        p.choose(observations(starlink=True), 0)
        self.assertEqual(p.choose(observations(starlink=True), 40, failure_overrides={'starlink': 0})[0], 'starlink')
        self.assertEqual(p.choose(observations(), 41, failure_overrides={'starlink': 0})[1], 'cellular')
        p = m.Policy(c); p.choose(observations(), 0)
        self.assertIsNone(p.choose(observations(), 10, failure_overrides={'starlink': 0})[1])

    def test_public_cache_omits_all_monitor_and_boot_details(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'status.json'
            p.write_text(json.dumps(dict(generated_at=m.time.time(), switch_monitor={'ifindex': 17}, startup_grace={'held': True}, uplinks=[], usage={})))
            value = m.cached_status(p, public=True)
            self.assertNotIn('switch_monitor', value); self.assertNotIn('startup_grace', value)


if __name__ == '__main__': unittest.main()
