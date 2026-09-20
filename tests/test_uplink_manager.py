import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pcs_uplink_manager', ROOT / 'scripts/pcs_uplink_manager.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
CELL_UUID = '11111111-1111-1111-1111-111111111111'


def config(starlink=True, mode='auto'):
    rows = [m.Uplink('wifi', 'Wi-Fi', 'wifi', 2, interface='wlan0'), m.Uplink('cellular', 'Cellular', 'cellular', 3, profile=CELL_UUID, activation='fallback')]
    if starlink:
        rows.insert(0, m.Uplink('starlink', 'Starlink', 'ethernet', 1, interface='enx001122334455', profile='starlink'))
    return m.Config(tuple(rows), mode=mode)


def observations(starlink=False, wifi=False, cellular=False, manual=False):
    rows = {}
    for uid, healthy, iface in [('starlink', starlink, 'enx001122334455'), ('wifi', wifi, 'wlan0'), ('cellular', cellular, 'wwan0')]:
        connected = uid != 'cellular' or cellular or manual
        rows[uid] = m.Observation(interface=iface if connected else '', device='/device/' + uid, profile=CELL_UUID if uid == 'cellular' else uid, session='/active/' + uid if connected else '', link=connected, address=connected, internet=healthy)
    return rows


class PolicyTests(unittest.TestCase):
    def test_starlink_preferred_without_cellular_activation(self):
        p = m.Policy(config())
        for now in (0, 10, 20, 30):
            desired, activation = p.choose(observations(starlink=True, wifi=True), now)
            self.assertIsNone(activation)
        self.assertEqual(desired, 'starlink')

    def test_carrier_and_dhcp_without_internet_falls_to_wifi(self):
        p = m.Policy(config())
        p.selected = 'starlink'
        o = observations(wifi=True)
        self.assertTrue(o['starlink'].link and o['starlink'].address)
        self.assertEqual(p.choose(o, 0), ('starlink', None))
        self.assertEqual(p.choose(o, 29), ('starlink', None))
        self.assertEqual(p.choose(o, 30), ('wifi', None))

    def test_cellular_waits_for_sustained_failure(self):
        p = m.Policy(config())
        self.assertIsNone(p.choose(observations(), 0)[1])
        self.assertIsNone(p.choose(observations(), 29)[1])
        self.assertEqual(p.choose(observations(), 30)[1], 'cellular')

    def test_transient_failure_does_not_switch(self):
        p = m.Policy(config())
        p.selected = 'starlink'
        p.choose(observations(starlink=True, wifi=True), 0)
        self.assertEqual(p.choose(observations(wifi=True), 40)[0], 'starlink')
        self.assertEqual(p.choose(observations(starlink=True, wifi=True), 50)[0], 'starlink')

    def test_transient_recovery_does_not_failback(self):
        p = m.Policy(config())
        p.selected = 'cellular'
        p.choose(observations(starlink=True, cellular=True), 0)
        self.assertEqual(p.choose(observations(cellular=True), 10)[0], 'cellular')
        p.choose(observations(starlink=True, cellular=True), 20)
        self.assertEqual(p.choose(observations(starlink=True, cellular=True), 49)[0], 'cellular')
        self.assertEqual(p.choose(observations(starlink=True, cellular=True), 50)[0], 'starlink')

    def test_unconfigured_starlink_legacy_behavior(self):
        p = m.Policy(config(False))
        p.choose(observations(), 0)
        self.assertEqual(p.choose(observations(), 30)[1], 'cellular')

    def test_unknown_does_not_trigger_expensive_activation(self):
        p = m.Policy(config())
        o = observations()
        o['starlink'].internet = None
        p.choose(o, 0)
        self.assertIsNone(p.choose(o, 90)[1])

    def test_suppression_and_retry_backoff(self):
        p = m.Policy(config())
        p.choose(observations(), 0)
        self.assertIsNone(p.choose(observations(), 30, ['cellular'])[1])
        p.retry_at['cellular'] = 100
        self.assertIsNone(p.choose(observations(), 90)[1])


class FakeNM:
    def __init__(self, obs):
        self.obs = obs
        self.changes = []
        self.disconnected = []
        self.activated = []
        self.renewed = []
        self.settings = {}

    def daemon(self):
        return ':1.7'

    def observe(self, cfg):
        return {u.id: self.obs[u.id] for u in cfg.uplinks}

    def applied(self, o):
        return self.settings.setdefault(o.device, {'ipv4': {'route-metric': 900, 'dns-priority': 0}, 'ipv6': {'route-metric': 900, 'dns-priority': 0}}), 1

    def reapply(self, o, values):
        if o.interface == 'wwan0':
            raise AssertionError('modem reapply would discard bearer IP configuration')
        self.changes.append((o.interface, values))
        settings, _ = self.applied(o)
        for family, fields in values.items():
            settings[family].update(fields)

    def fixed_metrics(self, o):
        return {family: values['route-metric'] for family, values in self.applied(o)[0].items()}

    def effective(self, target='1.1.1.1'):
        family = 'ipv6' if ':' in target else 'ipv4'
        address = 'address6' if family == 'ipv6' else 'address'
        active = [o for o in self.obs.values() if getattr(o, address) and o.session]
        if not active:
            return ''
        return min(active, key=lambda o: self.applied(o)[0][family]['route-metric']).interface

    def activate(self, u):
        self.activated.append(u.id)
        self.obs[u.id] = m.Observation(interface='wwan0', device='/device/cellular', profile=u.profile, session='/active/new', link=True, address=True, internet=True)
        return '/active/new'

    def deactivate(self, session):
        self.disconnected.append(session)

    def renew_ipv4(self, u, o):
        self.renewed.append(u.id)


class ControllerTests(unittest.TestCase):
    def make(self, folder, obs):
        nm = FakeNM(obs)
        return m.Controller(config(), nm, folder, boot='boot'), nm

    @patch.object(m.subprocess, 'run')
    def test_manual_cellular_survives_standby_failover_and_recovery(self, run):
        for metric in (20, 900, 25000):
            with self.subTest(metric=metric), tempfile.TemporaryDirectory() as folder:
                c, nm = self.make(folder, observations(starlink=True, cellular=True, manual=True))
                nm.settings['/device/cellular'] = {f: {'route-metric': metric, 'dns-priority': 0} for f in ('ipv4', 'ipv6')}
                c.step(0)
                c.step(30)
                self.assertEqual(nm.effective(), 'enx001122334455')
                nm.obs['starlink'].internet = False
                c.step(40)
                self.assertEqual(c.step(70)['active_id'], 'cellular')
                c = m.Controller(config(), nm, folder, boot='boot')
                nm.obs['starlink'].internet = True
                self.assertEqual(c.step(80)['active_id'], 'cellular')
                self.assertEqual(c.step(110)['active_id'], 'starlink')
                self.assertEqual(nm.disconnected, [])
                self.assertEqual(c.state['owned'], {})
                self.assertTrue(nm.obs['cellular'].address)
                self.assertFalse(any(iface == 'wwan0' for iface, _ in nm.changes))

    @patch.object(m.subprocess, 'run')
    def test_manual_mode_does_not_reapply_legacy_modem_journal(self, run):
        with tempfile.TemporaryDirectory() as folder:
            nm = FakeNM(observations(cellular=True, manual=True))
            c = m.Controller(config(mode='manual'), nm, folder, boot='boot')
            c.state['original']['cellular'] = {'session': '/active/cellular', 'values': {'ipv4': {'route-metric': 900}}}
            c.step(0)
            self.assertNotIn('cellular', c.state['original'])
            self.assertEqual(nm.disconnected, [])

    @patch.object(m.subprocess, 'run')
    def test_manual_cellular_never_claimed_or_disconnected_after_restart(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations(starlink=True, cellular=True, manual=True))
            c.step(0)
            c.step(30)
            c = m.Controller(config(), nm, folder, boot='boot')
            c.step(40)
            c.step(70)
            self.assertEqual(nm.disconnected, [])
            self.assertEqual(c.state['owned'], {})

    @patch.object(m.subprocess, 'run')
    def test_owned_cellular_released_after_stable_recovery_and_restart(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations())
            c.step(0)
            c.step(30)
            self.assertEqual(nm.activated, ['cellular'])
            c = m.Controller(config(), nm, folder, boot='boot')
            nm.obs['starlink'].internet = True
            c.step(40)
            self.assertEqual(nm.disconnected, [])
            c.step(70)
            self.assertEqual(nm.disconnected, ['/active/new'])

    @patch.object(m.subprocess, 'run')
    def test_replacement_session_cannot_inherit_ownership(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations())
            c.step(0)
            c.step(30)
            nm.obs['cellular'].session = '/active/operator'
            nm.obs['starlink'].internet = True
            c.step(40)
            c.step(70)
            self.assertEqual(nm.disconnected, [])

    @patch.object(m.subprocess, 'run')
    def test_transient_probe_failure_keeps_effective_route(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations(starlink=True, wifi=True))
            c.step(0)
            c.step(30)
            nm.obs['starlink'].internet = False
            c.step(40)
            self.assertEqual(nm.effective(), 'enx001122334455')
            c.step(70)
            self.assertEqual(nm.effective(), 'wlan0')

    def test_offline_never_mutates_lan(self):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations())
            status = c.step(0)
            self.assertFalse(status['internet'])
            self.assertFalse(any(iface == 'eth0' for iface, _ in nm.changes))

    def test_failed_activation_does_not_claim_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations())
            c.step(0)
            with patch.object(nm, 'activate', side_effect=RuntimeError('modem unavailable')):
                status = c.step(30)
            self.assertIn('modem unavailable', status['error'])
            self.assertEqual(c.state['owned'], {})

    def test_initial_default_failure_observes_failure_window(self):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations(wifi=True))
            c.step(0)
            self.assertEqual(nm.effective(), 'enx001122334455')
            c.step(29)
            self.assertEqual(nm.effective(), 'enx001122334455')
            c.step(30)
            self.assertEqual(nm.effective(), 'wlan0')

    @patch.object(m.subprocess, 'run')
    def test_stalled_bound_ethernet_ipv4_is_reactivated_without_cable_cycle(self, run):
        with tempfile.TemporaryDirectory() as folder:
            obs = observations(wifi=True)
            obs['starlink'] = m.Observation(
                interface='enx001122334455', device='/device/starlink',
                profile='starlink', session='/active/starlink', link=True,
                address=False, address6=True, internet=False, internet6=True,
            )
            c, nm = self.make(folder, obs)
            c.step(0)
            c.step(m.ETHERNET_IPV4_RECOVERY_SECONDS - 1)
            self.assertNotIn('starlink', nm.renewed)
            c.step(m.ETHERNET_IPV4_RECOVERY_SECONDS)
            self.assertEqual(nm.disconnected, [])
            self.assertIn('starlink', nm.renewed)
            self.assertEqual(c.state['owned'], {})

    @patch.object(m.subprocess, 'run')
    def test_stalled_operator_ethernet_profile_is_never_reactivated(self, run):
        with tempfile.TemporaryDirectory() as folder:
            obs = observations(wifi=True)
            obs['starlink'] = m.Observation(
                interface='enx001122334455', device='/device/starlink',
                profile='operator-profile', session='/active/operator', link=True,
                address=False, address6=True, internet=False, internet6=True,
            )
            c, nm = self.make(folder, obs)
            c.step(0)
            c.step(m.ETHERNET_IPV4_RECOVERY_SECONDS + 1)
            self.assertEqual(nm.disconnected, [])
            self.assertNotIn('starlink', nm.renewed)

    @patch.object(m.subprocess, 'run')
    def test_manual_mode_restores_only_unchanged_manager_fields(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations(starlink=True, wifi=True))
            c.step(0)
            c.step(30)
            nm.settings['/device/wifi']['ipv4']['dns-priority'] = -80
            c = m.Controller(config(mode='manual'), nm, folder, boot='boot')
            c.step(50)
            self.assertEqual(nm.settings['/device/starlink']['ipv4']['route-metric'], 900)
            self.assertEqual(nm.settings['/device/wifi']['ipv4']['dns-priority'], -80)
            self.assertEqual(nm.disconnected, [])

    @patch.object(m.subprocess, 'run')
    def test_restart_retains_selected_route_and_resets_stability(self, run):
        with tempfile.TemporaryDirectory() as folder:
            c, nm = self.make(folder, observations(starlink=True, wifi=True))
            c.step(0)
            c.step(30)
            c = m.Controller(config(), nm, folder, boot='boot')
            nm.obs['starlink'].internet = False
            self.assertEqual(c.step(40)['selected_id'], 'starlink')
            self.assertEqual(c.step(70)['selected_id'], 'wifi')


class AccountingTests(unittest.TestCase):
    def test_changed_config_id_does_not_duplicate_interface_totals(self):
        a = m.Accounting(None, 'boot')
        o = m.Observation(counters={'rx_bytes': 100, 'tx_bytes': 20}, counter_identity='nic:1')
        a.update({'wan': o})
        self.assertEqual(a.update({'renamed': o})['total_bytes'], 120)
        o.counters['rx_bytes'] = 200
        self.assertEqual(a.update({'renamed': o})['total_bytes'], 220)
        self.assertEqual(a.update({'wan': o})['total_bytes'], 220)
    def test_restart_reset_hotplug_and_no_double_counting(self):
        a = m.Accounting(None, 'boot')
        o = m.Observation(counters={'rx_bytes': 100, 'tx_bytes': 20}, counter_identity='nic:1')
        self.assertEqual(a.update({'wan': o})['total_bytes'], 120)
        a = m.Accounting(a.data, 'boot')
        self.assertEqual(a.update({'wan': o})['total_bytes'], 120)
        o.counters['rx_bytes'] = 150
        self.assertEqual(a.update({'wan': o})['total_bytes'], 170)
        a.update({'wan': m.Observation()})
        o.counter_identity = 'nic:2'
        o.counters = {'rx_bytes': 5, 'tx_bytes': 2}
        result = a.update({'wan': o})
        self.assertEqual(result['total_bytes'], 177)
        self.assertTrue(result['partial'])
        self.assertEqual(a.update({})['total_bytes'], 177)
        self.assertIsNone(m.Accounting(a.data, 'next-boot').summary()['total_bytes'])


class ConfigAndPrivacyTests(unittest.TestCase):
    def test_reject_lan_and_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            for rows in [[{'id': 'wan', 'name': 'WAN', 'type': 'ethernet', 'priority': 1, 'interface': 'eth0'}], [{'id': 'wan', 'name': 'WAN', 'type': 'ethernet', 'priority': 1, 'interface': 'enx001122334455'}] * 2]:
                path.write_text(json.dumps({'version': 1, 'uplinks': rows}))
                with self.assertRaises(ValueError):
                    m.load_config(path)

    def test_public_cache_hides_identity_and_stale_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'status.json'
            m.atomic_json(path, {'generated_at': 100, 'uplinks': [{'id': 'wan', 'interface': 'secret', 'profile': 'secret', 'addresses': ['secret'], 'owned': True}], 'usage': {'total_bytes': 12}})
            public = m.cached_status(path, True, 110)
            self.assertNotIn('secret', json.dumps(public))
            self.assertEqual(public['usage']['total_bytes'], 12)
            self.assertFalse(m.cached_status(path, True, 200)['available'])

    @patch.object(m.subprocess, 'run')
    def test_probe_bound_either_target_success(self, run):
        run.return_value.returncode = 0
        self.assertTrue(m.probe('enx001122334455', ['1.1.1.1', '8.8.8.8'], 2))
        self.assertEqual(run.call_args.args[0][3:5], ['-I', 'enx001122334455'])
        self.assertEqual(run.call_count, 1)


class SetupMigrationTests(unittest.TestCase):
    def test_nic_discovery_without_sbin_in_operator_path(self):
        setup_spec = importlib.util.spec_from_file_location('pcs_uplink_setup', ROOT / 'scripts/pcs_uplink_setup.py')
        setup = importlib.util.module_from_spec(setup_spec)
        setup_spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('eth0', 'enx001122334455'):
                (root / name / 'device').mkdir(parents=True)
            with patch.object(setup, 'Path', return_value=root), patch.object(setup.shutil, 'which', return_value=None), patch.object(setup, 'run', side_effect=['ethernet', 'Permanent address: 00:11:22:33:44:55']) as run:
                self.assertEqual(setup.candidates(), [{'interface': 'enx001122334455', 'mac': '00:11:22:33:44:55'}])
                self.assertEqual(run.call_args.args, ('/usr/sbin/ethtool', '-P', 'enx001122334455'))

    def test_explicit_manual_policy_blocks_legacy_auto_inheritance(self):
        setup_spec = importlib.util.spec_from_file_location('pcs_uplink_setup', ROOT / 'scripts/pcs_uplink_setup.py')
        setup = importlib.util.module_from_spec(setup_spec)
        setup_spec.loader.exec_module(setup)
        self.assertTrue(setup.inherit_legacy_mode(None, {}, None))
        for existing, env, requested in [
            (None, {}, 'manual'),
            (None, {'PCS_UPLINK_MODE': 'manual'}, None),
            (None, {'PCS_CELLULAR_FALLBACK_MODE': 'manual'}, None),
            ({'mode': 'manual'}, {}, None),
        ]:
            with self.subTest(existing=existing, env=env, requested=requested):
                self.assertFalse(setup.inherit_legacy_mode(existing, env, requested))


if __name__ == '__main__':
    unittest.main()
