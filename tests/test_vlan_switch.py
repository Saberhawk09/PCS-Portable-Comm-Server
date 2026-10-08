"""Portable offline tests: all host/network operations are mocked."""
import configparser
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_vlan_switch as v
import pcs_uplink_manager as m
import pcs_uplink_management as management

UUID = v.IDS['pcs-starlink-vlan']
BRICK = '02:00:00:00:00:02'
SWITCH = '02:00:00:00:00:04'


def uplink(**kwargs):
    return replace(m.Uplink('starlink', 'Starlink', 'vlan', 1, interface='eth0.20',
                           parent='eth0', vlan_id=20, profile=UUID), **kwargs)


def settings():
    return {'connection': {'type': 'vlan', 'uuid': UUID, 'interface-name': 'eth0.20'},
            'vlan': {'parent': 'eth0', 'id': 20}, 'ipv4': {'method': 'auto', 'route-metric': 100}}


class IdentityTests(unittest.TestCase):
    def test_exact_profile_and_no_lan_aliases(self):
        m.validate_vlan_settings(uplink(), settings())
        for group, key, value in [('connection', 'type', '802-3-ethernet'),
                                  ('connection', 'uuid', v.IDS['pcs-lan-vlan']),
                                  ('connection', 'interface-name', 'eth0.10'),
                                  ('vlan', 'id', 10), ('vlan', 'parent', 'eth1'),
                                  ('ipv4', 'method', 'shared')]:
            with self.subTest(group=group, key=key):
                changed = settings()
                changed[group][key] = value
                with self.assertRaises(ValueError):
                    m.validate_vlan_settings(uplink(), changed)
        for interface in ('eth0', 'eth0.10', 'lo'):
            self.assertFalse(m.interface_name(interface))

    def test_config_requires_full_identity_and_rejects_mac_shortcuts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'uplinks.json'
            for changes in ({}, {'vlan_id': 10}, {'parent': 'eth1'}, {'interface': 'eth0.10'},
                            {'profile': ''}, {'mac': BRICK}, {'type': 'ethernet'}):
                row = vars(uplink(**changes))
                path.write_text(json.dumps(dict(version=1, uplinks=[row])))
                if changes:
                    with self.assertRaises(ValueError):
                        m.load_config(path)
                else:
                    self.assertEqual(m.load_config(path).uplinks[0].vlan_id, 20)

    def adapter(self):
        nm = m.NetworkManager.__new__(m.NetworkManager)
        nm.dbus = Mock(Int64=int, Int32=int)
        nm.iface = Mock()
        props = {('/wan', 'DeviceType'): 11, ('/wan', 'Interface'): 'eth0.20',
                 ('/wan', 'Parent'): '/parent', ('/wan', 'VlanId'): 20,
                 ('/parent', 'Interface'): 'eth0', ('/parent', 'DeviceType'): 1,
                 ('/wan', 'ActiveConnection'): '/session'}
        nm.prop = Mock(side_effect=lambda path, iface, name: props[(path, name)])
        nm.applied = Mock(return_value=(settings(), 7))
        return nm, props

    def test_runtime_parent_tag_and_session_revalidated_before_reapply(self):
        o = m.Observation(interface='eth0.20', device='/wan', profile=UUID,
                          session='/session', vlan_identity=uplink())
        nm, props = self.adapter()
        nm.reapply(o, {'ipv4': {'route-metric': 200}})
        nm.iface.return_value.Reapply.assert_called_once()
        for key, value in [(('/wan', 'VlanId'), 10), (('/parent', 'Interface'), 'eth1'),
                           (('/wan', 'ActiveConnection'), '/replacement')]:
            nm, props = self.adapter()
            props[key] = value
            with self.assertRaises((ValueError, RuntimeError)):
                nm.reapply(o, {'ipv4': {'route-metric': 200}})
            nm.iface.return_value.Reapply.assert_not_called()

    def test_unverified_vlan_and_lan_never_reapplied(self):
        for interface in ('eth0', 'eth0.10', 'eth0.20'):
            nm, _ = self.adapter()
            o = m.Observation(interface=interface, device='/wan', profile=UUID, session='/session')
            with self.assertRaises(ValueError):
                nm.reapply(o, {'ipv4': {'route-metric': 200}})
            nm.iface.return_value.Reapply.assert_not_called()

    def test_disconnected_vlan_is_observed_as_failed_wan_not_unknown_or_lan(self):
        nm, props = self.adapter()
        nm.manager = Mock()
        nm.manager.GetDevices.return_value = ['/parent', '/lan', '/wan']
        props.update({(m.ROOT, 'ActiveConnections'): [],
                      ('/parent', 'PermHwAddress'): BRICK,
                      ('/lan', 'Interface'): 'eth0.10', ('/lan', 'DeviceType'): 11,
                      ('/wan', 'IpInterface'): 'eth0.20', ('/wan', 'State'): 30,
                      ('/wan', 'Carrier'): False, ('/wan', 'Ip4Config'): '/', ('/wan', 'Ip6Config'): '/'})
        nm.prepare_probes = Mock()
        config = m.Config((uplink(), m.Uplink('wifi', 'Wi-Fi', 'wifi', 2, interface='wlan0')))
        observation = nm.observe(m.Config((uplink(),)))['starlink']
        self.assertEqual(observation.interface, 'eth0.20')
        self.assertEqual(observation.device, '/wan')
        self.assertEqual(observation.error, '')
        self.assertEqual(observation.state, 'link-down')
        policy = m.Policy(config)
        observations = {'starlink': observation, 'wifi': m.Observation(internet=True)}
        policy.choose(observations, 0)
        self.assertEqual(policy.choose(observations, 30)[0], 'wifi')

    def test_vlan_dhcp_renewal_retains_session(self):
        nm, _ = self.adapter()
        o = m.Observation(interface='eth0.20', device='/wan', profile=UUID,
                          session='/session', vlan_identity=uplink())
        nm.renew_ipv4(uplink(), o)
        args = nm.iface.return_value.Reapply.call_args.args
        self.assertEqual(args[0]['ipv4']['route-metric'], 101)
        self.assertEqual(args[1], 7)

    def test_management_requires_identity_and_current_trusted_subnet(self):
        cfg = m.Config((uplink(),))
        entries = management.validate_policy({'version': 1, 'trusted': [
            {'uplink': 'starlink', 'sources': ['192.168.50.0/24']}]}, cfg)
        devices = [{'ifname': 'eth0.20', 'link_type': 'ether', 'ifindex': 20,
                    'addr_info': [{'family': 'inet', 'local': '192.168.50.2'}]},
                   {'ifname': 'eth0.10', 'link_type': 'ether', 'ifindex': 10}]
        resolver = Mock(return_value='eth0.20')
        self.assertEqual(management.resolve(entries, devices, Mock(), resolver),
                         [('starlink', 20, '192.168.50.0/24')])
        resolver.side_effect = ValueError('wrong parent')
        with self.assertRaises(ValueError):
            management.resolve(entries, devices, Mock(), resolver)


class StagingTests(unittest.TestCase):
    def test_stage_and_repeat_have_no_process_calls(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(v, 'run') as run, patch.object(v.subprocess, 'run') as child:
            first = v.stage(folder, BRICK, SWITCH, 'auto')
            second = v.stage(folder, BRICK, SWITCH, 'auto')
            self.assertEqual(first, second)
            self.assertEqual(v.verify_stage(folder), first)
            run.assert_not_called()
            child.assert_not_called()
            for name in v.IDS:
                config = configparser.ConfigParser()
                config.read(Path(folder) / (name + '.nmconnection'))
                self.assertEqual(config['connection']['autoconnect'], 'false')
            lan = configparser.ConfigParser()
            lan.read(Path(folder) / 'pcs-lan-vlan.nmconnection')
            self.assertEqual(lan['ipv4']['shared-dhcp-range'], '10.42.0.100,10.42.0.200')
            self.assertEqual(lan['ipv4']['never-default'], 'true')
            parent = configparser.ConfigParser()
            parent.read(Path(folder) / 'pcs-vlan-parent.nmconnection')
            self.assertEqual(parent['ipv4']['method'], 'disabled')
            self.assertEqual(parent['ipv6']['method'], 'disabled')

    def test_tampered_stage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            v.stage(folder, BRICK, SWITCH, 'auto')
            path = Path(folder) / 'pcs-starlink-vlan.nmconnection'
            path.write_text(path.read_text().replace('id=20', 'id=10'))
            with self.assertRaises(ValueError):
                v.verify_stage(folder)

    def test_invalid_identity_and_injection_rejected(self):
        for value in ('', 'ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00', 'a; flush ruleset'):
            with self.assertRaises(ValueError):
                v.guard(value, SWITCH)
        with self.assertRaises(ValueError):
            v.guard(BRICK, BRICK)

    def test_guard_applies_to_both_ip_families_and_preserves_bridged_clients(self):
        text = v.guard(BRICK, SWITCH)
        self.assertIn('table inet', text)
        self.assertIn('ether saddr { ' + BRICK + ', ' + SWITCH + ' } counter drop', text)
        self.assertNotIn('iifname "eth0.10" counter drop', text)
        self.assertLess(text.index('pcs-infrastructure-no-internet'), text.index('pcs-wan-lan-deny'))
        self.assertIn('udp sport 67 udp dport 68 accept', text)
        self.assertIn('pcs-parent-untrusted', text)


class TransactionTests(unittest.TestCase):
    def test_unauthorized_apply_is_side_effect_free(self):
        with patch.object(v, 'run') as run, patch.object(v, 'backup') as backup:
            with self.assertRaises(ValueError):
                v.apply(Path('unused'), 900, False, False)
            run.assert_not_called()
            backup.assert_not_called()

    def test_apply_arms_before_changes_and_rolls_back_failed_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            v.stage(base / 'stage', BRICK, SWITCH, 'auto')
            old = m.Uplink('starlink', 'Starlink', 'ethernet', 1, profile='old-wan', mac=BRICK)
            config = m.Config((old,))
            events = []
            def command(*args, **kwargs):
                events.append(args)
                if args[:3] == ('nmcli', '-g', 'ipv6.method'):
                    return 'auto'
                if args[:3] == ('nmcli', '-g', 'UUID'):
                    return 'old-wan'
                if args[:3] == ('nmcli', '-g', 'connection.type'):
                    return '802-3-ethernet'
                if args[:3] == ('nmcli', '--wait', '20'):
                    raise RuntimeError('simulated LAN failure')
                return ''
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                if str(path).replace('\\', '/') == '/etc/pcs/uplinks.json':
                    return json.dumps({'version': 1, 'uplinks': [vars(old)]})
                return original_read(path, *args, **kwargs)
            manifest = {'active': ['old-wan'], 'services': {s: False for s in v.SERVICES}}
            with patch.object(v, 'STATE', base / 'state'), patch.object(v, 'MODE', base / 'mode'), \
                 patch.object(v, 'preflight'), patch.object(m, 'load_config', return_value=config), \
                 patch.object(Path, 'read_text', read), patch.object(v, 'write'), \
                 patch.object(v, 'json_write'), patch.object(v, 'backup', return_value=(base / 'backup', manifest)), \
                 patch.object(v, 'arm', side_effect=lambda *a: events.append(('ARM',))), \
                 patch.object(v, 'run', side_effect=command), patch.object(v, 'rollback') as rollback:
                with self.assertRaisesRegex(RuntimeError, 'simulated'):
                    v.apply(base / 'stage', 900, True, True)
                rollback.assert_called_once()
            self.assertLess(events.index(('ARM',)), events.index(('systemctl', 'stop', 'pcs-uplink-manager.service')))
            self.assertFalse(any('ModemManager' in ' '.join(e) for e in events))

    def test_rollback_restores_exact_file_and_does_not_activate_cellular(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            saved = base / 'backup-123'
            saved.mkdir()
            target = base / 'uplinks.json'
            target.write_text('changed')
            (saved / '0').write_text('original private policy')
            (saved / 'pcs-firewall.nft').write_text('')
            manifest = {'files': [{'path': str(target), 'saved': '0', 'exists': True}],
                        'active': ['legacy', 'cellular'], 'services': {s: False for s in v.SERVICES}}
            (saved / 'manifest.json').write_text(json.dumps(manifest))
            (base / 'pending.json').write_text(json.dumps({'backup': str(saved)}))
            def command(*args, **kwargs):
                if args[:4] == ('nft', '-j', 'list', 'tables'):
                    return '{"nftables": []}'
                if args[:3] == ('nmcli', '-g', 'connection.uuid'):
                    return 'legacy'
                if args[:3] == ('nmcli', '-g', 'connection.type'):
                    return 'gsm' if args[-1] == 'cellular' else '802-3-ethernet'
                return ''
            with patch.object(v, 'STATE', base), patch.object(v, 'targets', return_value=[target]), \
                 patch.object(v, 'run', side_effect=command) as run, patch.object(v.subprocess, 'run'):
                v.rollback()
                self.assertEqual(target.read_text(), 'original private policy')
                self.assertFalse((base / 'pending.json').exists())
                self.assertFalse(any('up' in c.args and 'cellular' in c.args for c in run.call_args_list))
                self.assertFalse(any('flush ruleset' in str(c) for c in run.call_args_list))

    def test_commit_requires_external_evidence_and_unexpired_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            pending = dict(backup='test', created=time.time(), timeout=900)
            (base / 'pending.json').write_text(json.dumps(pending))
            receipt = base / 'receipt.json'
            receipt.write_text(json.dumps(dict(backup='test', operator='tester')))
            with patch.object(v, 'STATE', base), patch.object(v, 'check') as check, patch.object(v, 'run'):
                with self.assertRaises(ValueError):
                    v.commit(receipt)
                check.assert_not_called()
                evidence = dict(backup='test', operator='tester', **{g: 'witness evidence' for g in v.GATES})
                receipt.write_text(json.dumps(evidence))
                pending['created'] = time.time() - 1000
                (base / 'pending.json').write_text(json.dumps(pending))
                with self.assertRaisesRegex(ValueError, 'expired'):
                    v.commit(receipt)
                check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
