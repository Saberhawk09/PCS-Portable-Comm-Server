import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_uplink_manager as uplinks
import pcs_uplink_management as management


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.config = uplinks.Config((uplinks.Uplink('starlink', 'Starlink', 'ethernet', 1, mac='00:11:22:33:44:55'),))
        self.policy = {'version': 1, 'trusted': [{'uplink': 'starlink', 'sources': ['192.168.50.0/24']}]}
        self.device = {'ifname': 'enx001122334455', 'ifindex': 7, 'link_type': 'ether', 'addr_info': [{'family': 'inet', 'local': '192.168.50.222'}]}

    def test_mac_binding_survives_name_change_and_uses_current_index(self):
        entries = management.validate_policy(self.policy, self.config)
        for name, index in [('eth1', 7), ('enx001122334455', 14)]:
            self.device.update(ifname=name, ifindex=index)
            self.assertEqual(management.resolve(entries, [self.device], lambda _: '00:11:22:33:44:55'), [('starlink', index, '192.168.50.0/24')])

    def test_absent_wrong_mac_or_different_upstream_gets_no_grant(self):
        entries = management.validate_policy(self.policy, self.config)
        self.assertEqual(management.resolve(entries, [], lambda _: ''), [])
        self.assertEqual(management.resolve(entries, [self.device], lambda _: '00:11:22:33:44:66'), [])
        self.device['addr_info'][0]['local'] = '192.168.1.100'
        self.assertEqual(management.resolve(entries, [self.device], lambda _: '00:11:22:33:44:55'), [])

    def test_lan_device_is_never_selected(self):
        self.device['ifname'] = 'eth0'
        self.assertEqual(management.resolve(management.validate_policy(self.policy, self.config), [self.device], lambda _: '00:11:22:33:44:55'), [])

    def test_public_broad_lan_and_ipv6_sources_are_rejected(self):
        for network in ['0.0.0.0/0', '8.8.8.0/24', '10.0.0.0/8', '10.42.0.0/24', '::/0', '127.0.0.0/24', '192.168.50.1/24']:
            with self.subTest(network=network):
                self.policy['trusted'][0]['sources'] = [network]
                with self.assertRaises(ValueError):
                    management.validate_policy(self.policy, self.config)

    def test_cellular_or_name_only_ethernet_is_not_trusted(self):
        for kind, mac in [('cellular', ''), ('ethernet', '')]:
            config = uplinks.Config((uplinks.Uplink('starlink', 'WAN', kind, 1, interface='eth1', mac=mac),))
            with self.assertRaises(ValueError):
                management.validate_policy(self.policy, config)

    def test_refresh_changes_only_owned_rules_and_preserves_default_denies(self):
        chains = {'pcs_wireguard': [{'handle': 3, 'comment': 'pcs-wg-home-management'}, {'handle': 4, 'comment': 'pcs-uplink-management:starlink'}], 'pcs_stats_api': [{'handle': 9, 'comment': 'pcs-api-default-deny'}]}
        script = management.transaction(chains, [('starlink', 7, '192.168.50.0/24')])
        self.assertIn('delete rule inet pcs_wireguard input handle 4', script)
        self.assertNotIn('handle 3', script)
        self.assertNotIn('handle 9', script)
        self.assertIn('meta iif 7 ip saddr 192.168.50.0/24', script)
        self.assertIn('tcp dport { 9443 }', script)
        self.assertNotIn('flush', script)
        self.assertNotIn('forward', script)
        self.assertEqual(management.transaction(chains, []), 'delete rule inet pcs_wireguard input handle 4\n')


if __name__ == '__main__':
    unittest.main()
