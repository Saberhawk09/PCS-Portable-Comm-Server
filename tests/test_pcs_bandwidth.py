import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pcs_bandwidth_config import PolicyError, clients, load, rate, validate
from pcs_bandwidth_engine import Engine, detect, plan
from pcs_bandwidth import revision, update


def policy():
    return validate({'profile': 'starlink', 'profiles': {
        'starlink': {'enabled': True, 'default_down': '5M', 'default_up': '2M', 'total_down': '20M'},
        'unrestricted': {'enabled': False}}, 'clients': []})


class PolicyTests(unittest.TestCase):
    def test_rates_and_invalid_values(self):
        for value in ('5M', '5mbit', '5mbps'):
            self.assertEqual(rate(value), 5000000)
        for value in ('0', '-1M', 'nan', '5M;reboot', '20gbit', True, '1e4'):
            with self.assertRaises(PolicyError):
                rate(value)

    def test_default_is_per_ip_and_router_excluded(self):
        entries = list(clients(policy(), ['10.42.0.1']))
        self.assertEqual(len(entries), 253)
        self.assertEqual(len({e['minor'] for e in entries}), 253)
        self.assertTrue(all(e['down'] == '5M' for e in entries))

    def test_bad_config_is_rejected(self):
        for change in ({'lan_network': '10.42.0.0/16'}, {'lan_interface': 'eth0;foo'},
                       {'profile': 'typo'}, {'profiles': {'x': {'enabled': 'false'}}},
                       {'clients': [{'ip': '192.168.0.5'}]},
                       {'clients': [{'ip': '10.42.0.2'}, {'ip': '10.42.0.2'}]},
                       {'clients': [{'ip': '10.42.0.2', 'unlimited': True, 'upload': '1M'}]},
                       {'clietns': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate({**policy(), **change})

    def test_yaml_duplicate_keys_and_unsafe_objects(self):
        for text in ('profile: starlink\nprofile: unrestricted\n', '!!python/object/apply:os.system [echo nope]'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'config.yaml'
                path.write_text(text)
                with self.assertRaises(Exception):
                    load(path)

    def test_ui_changes_are_revision_checked_and_bounded(self):
        config = policy()
        edited = update(config, {'operation': 'client', 'ip': '10.42.0.2', 'download': '8M', 'revision': revision(config)})
        self.assertEqual(edited['clients'][0]['download'], '8M')
        self.assertFalse(config['clients'])
        with self.assertRaises(PolicyError):
            update(edited, {'operation': 'profile', 'profile': 'unrestricted', 'revision': revision(config)})
        with self.assertRaises(PolicyError):
            update(config, {'operation': 'client', 'ip': '10.42.0.2', 'command': 'reboot'})
        trusted = update(config, {'operation': 'client', 'ip': '10.42.0.2', 'unlimited': True})
        self.assertTrue(trusted['clients'][0]['unlimited'])
        self.assertFalse(update(trusted, {'operation': 'delete-client', 'ip': '10.42.0.2'})['clients'])


class TopologyTests(unittest.TestCase):
    addresses = [{'ifname': 'lan', 'addr_info': [{'family': 'inet', 'local': '10.42.0.1'}]}]
    rules = [{'table': 'local'}, {'table': 'main'}, {'table': 'default'}]

    def test_lowest_metric_and_missing_wan(self):
        routes = [{'dst': 'default', 'dev': 'cell', 'metric': 600}, {'dst': 'default', 'dev': 'wifi', 'metric': 50}]
        self.assertEqual(detect(policy(), self.addresses, routes, self.rules)['wan'], 'wifi')
        self.assertIsNone(detect(policy(), self.addresses, [], self.rules)['wan'])

    def test_ambiguity_requires_override(self):
        routes = [{'dst': 'default', 'dev': dev} for dev in ('cell', 'wifi')]
        with self.assertRaises(PolicyError):
            detect(policy(), self.addresses, routes, self.rules)
        with self.assertRaises(PolicyError):
            detect(policy(), self.addresses, routes[:1], [{'table': 42}])
        config = {**policy(), 'wan_interface': 'cell'}
        self.assertEqual(detect(config, self.addresses, routes, [{'table': 42}])['wan'], 'cell')


class PlanTests(unittest.TestCase):
    network = dict(lan='lan', wan='wan', router_addresses=['10.42.0.1'])

    def test_nat_safe_forward_classification_and_ifb_only_queues(self):
        result = plan(policy(), self.network)
        self.assertIn('hook forward', result['nft'])
        self.assertIn('iifname "lan" oifname "wan" ip saddr 10.42.0.2', result['nft'])
        self.assertIn('iifname "wan" oifname "lan" ip daddr 10.42.0.2', result['nft'])
        self.assertNotIn('flush ruleset', result['nft'])
        self.assertNotIn('meta mark', result['nft'])
        self.assertTrue(all('dev pcsbw-' in c for c in result['tc']))
        self.assertEqual(sum(c.endswith('fq_codel') for c in result['tc']), 506)

    def test_disabled_or_no_wan_creates_no_rules(self):
        for config, network in (({**policy(), 'profile': 'unrestricted'}, self.network),
                                (policy(), {**self.network, 'wan': None})):
            result = plan(config, network)
            self.assertFalse(result['enabled'])
            self.assertEqual(result['tc'], [])
            self.assertEqual(result['nft'], '')

    def test_unlimited_still_obeys_aggregate_ceiling(self):
        config = update(policy(), {'operation': 'client', 'ip': '10.42.0.2', 'unlimited': True})
        result = plan(config, self.network)
        line = next(c for c in result['tc'] if 'classid b702:101 ' in c)
        self.assertIn('ceil 20000000bit', line)


class OwnershipTests(unittest.TestCase):
    def test_iproute2_raw_ematch_json_is_parsed_without_losing_ownership(self):
        raw = '[{"pref":47801,"kind":"basic","options":{"handle":"1"\n  meta(priority mask 0xffff0000 eq -1224671232)\n,"actions":[{"kind":"mirred","to_dev":"pcsbw-up"}]}}]'
        with mock.patch('pcs_bandwidth_engine.run', return_value=mock.Mock(stdout=raw)):
            engine = Engine('/unused')
            self.assertTrue(engine.own_filter(engine.filters('wan')[0], 'up'))

    def test_disabled_profile_can_cleanup_without_lan(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory)
            with mock.patch('pcs_bandwidth_engine.topology', side_effect=PolicyError('No LAN')), mock.patch.object(engine, 'cleanup'):
                result = engine.apply({**policy(), 'profile': 'unrestricted'})
                self.assertEqual(result['status'], 'DISABLED')

    def test_foreign_table_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory)
            with mock.patch.object(engine, 'table', return_value={'nftables': [{'table': {'name': 'pcs_bandwidth'}}]}), mock.patch('pcs_bandwidth_engine.run') as runner:
                with self.assertRaises(RuntimeError):
                    engine.cleanup()
                runner.assert_not_called()

    def test_foreign_ifb_name_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(directory)
            with mock.patch.object(engine, 'table', return_value=None), mock.patch.object(engine, 'links', return_value={'pcsbw-up': {'ifalias': 'someone-else'}}), mock.patch('pcs_bandwidth_engine.run') as runner:
                with self.assertRaises(RuntimeError):
                    engine.cleanup()
                runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
