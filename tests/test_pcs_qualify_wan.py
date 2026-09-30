"""WAN development contracts. These do not authorize a commissioned-PCS fault."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_qualify_wan as wan

SESSION = 'a' * 32
MAC = '02:00:00:00:00:01'


class WanPrimitives(unittest.TestCase):
    def setUp(self):
        self.link = dict(ifname='eth1', ifindex=4, link_type='ether', operstate='UP',
                         flags=['BROADCAST', 'UP', 'LOWER_UP'], address=MAC)
        self.target = wan.ethernet_identity(self.link, MAC)
        self.connection = '10.42.0.20 50000 10.42.0.1 22'
        self.routes = [dict(dst='10.42.0.20', **{'from': '10.42.0.1'}, dev='eth0')]
        self.rules = [dict(priority=p, src='all', table=t)
                      for p, t in ((0, 'local'), (32766, 'main'), (32767, 'default'))]
        self.table = {'nftables': [{'table': dict(family='inet', name=wan.TABLE,
                         comment=wan.OWNER + SESSION, handle=12)}]}

    def test_control_query_includes_actual_ssh_server_source(self):
        self.assertEqual(wan.control_query(self.connection)[-3:],
                         ['10.42.0.20', 'from', '10.42.0.1'])
        wan.direct_lan_control(self.connection, self.routes, self.rules, '10.42.0.0/24')

    def test_control_rejects_target_wan_wireguard_and_unknown_routes(self):
        for dev in ('eth1', 'wlan0', 'wg-pcs', 'wg-direct', '', None):
            with self.subTest(dev=dev), self.assertRaises(wan.HarnessError):
                wan.direct_lan_control(self.connection, [dict(self.routes[0], dev=dev)],
                                       self.rules, '10.42.0.0/24')

    def test_control_rejects_ambiguous_or_indirect_or_different_source(self):
        cases = [[], self.routes * 2, [None]]
        for key, value in [('gateway', '10.42.0.2'), ('nexthops', []), ('table', 100),
                           ('from', '10.42.0.99'), ('type', 'local'), ('nhid', 1)]:
            cases.append([dict(self.routes[0], **{key: value})])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(wan.HarnessError):
                wan.direct_lan_control(self.connection, rows, self.rules, '10.42.0.0/24')

    def test_control_rejects_non_lan_or_malformed_connection(self):
        for value in ('', None, '::1 50 ::1 22', '10.42.0.20 0 10.42.0.1 22',
                      '10.42.0.20 50 10.42.0.1 99999', 'bad 50 10.42.0.1 22',
                      '192.168.50.230 50 192.168.50.236 22',
                      '10.42.0.255 50 10.42.0.1 22', '10.42.0.1 50 10.42.0.1 22'):
            with self.subTest(value=value), self.assertRaises(wan.HarnessError):
                wan.direct_lan_control(value, self.routes, self.rules, '10.42.0.0/24')

    def test_policy_selectors_and_extra_rules_block(self):
        for key in ('fwmark', 'iif', 'oif', 'uidrange', 'dst', 'suppress_prefixlength', 'goto'):
            rows = copy.deepcopy(self.rules)
            rows[1][key] = 'ANY'
            with self.subTest(key=key), self.assertRaises(wan.HarnessError):
                wan.standard_policy(rows)
        for rows in (None, [], self.rules * 2, self.rules[:-1]):
            with self.assertRaises(wan.HarnessError):
                wan.standard_policy(rows)
        malformed = copy.deepcopy(self.rules)
        malformed[0]['table'] = {}
        with self.assertRaises(wan.HarnessError):
            wan.standard_policy(malformed)

    def test_identity_rejects_lan_virtual_down_and_random_mac(self):
        for key, value in [('ifname', 'eth0'), ('ifindex', True), ('ifindex', 0),
                           ('operstate', 'DOWN'), ('master', 'br0'), ('linkinfo', {}),
                           ('flags', ['UP']), ('flags', [{}]),
                           ('address', '02:00:00:00:00:02')]:
            with self.subTest(key=key), self.assertRaises(wan.HarnessError):
                wan.ethernet_identity(dict(self.link, **{key: value}), MAC)
        for mac in (None, '', '00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', '";flush ruleset'):
            with self.assertRaises(wan.HarnessError):
                wan.ethernet_identity(dict(self.link, address=mac), mac)

    def test_identity_replacement_blocks(self):
        wan.revalidate(self.target, self.link, MAC)
        with self.assertRaises(wan.HarnessError):
            wan.revalidate(self.target, dict(self.link, ifindex=5), MAC)

    def test_plan_is_bounded_exclusive_and_ipv4_only(self):
        plan = wan.create_plan(SESSION, self.target, 30, '10.42.0.0/24', '192.168.50.0/24')
        self.assertTrue(plan.startswith('create table inet pcs_qualification'))
        self.assertIn('type iface_index; flags timeout; timeout 30s', plan)
        self.assertEqual(plan.count('meta nfproto ipv4 meta oif @blocked'), 2)
        self.assertIn('192.168.100.1/32', plan)
        self.assertNotIn('hook input', plan)
        self.assertNotIn('flush', plan)
        self.assertNotIn('ip6', plan)

    def test_plan_rejects_duration_target_and_network_bypass(self):
        for seconds in (True, 0, 4, 121, 1.5, '30'):
            with self.assertRaises(wan.HarnessError):
                wan.create_plan(SESSION, self.target, seconds, '10.42.0.0/24', '192.168.50.0/24')
        for cidr in ('0.0.0.0/0', '10.42.0.0/24', '192.168.50.1/24',
                     '::/0', '224.0.0.0/24', '127.0.0.0/24', 'x;flush ruleset'):
            with self.assertRaises(wan.HarnessError):
                wan.create_plan(SESSION, self.target, 30, '10.42.0.0/24', cidr)
        with self.assertRaises(wan.HarnessError):
            wan.create_plan('";flush ruleset', self.target, 30, '10.42.0.0/24', '192.168.50.0/24')

    def test_cleanup_uses_handle_and_exact_session_ownership(self):
        self.assertEqual(wan.delete_plan(self.table, SESSION), 'delete table inet handle 12\n')
        for key, value in [('comment', wan.OWNER + 'b' * 32), ('name', 'pcs_wireguard'),
                           ('family', 'ip'), ('handle', True), ('handle', 0)]:
            value_doc = copy.deepcopy(self.table)
            value_doc['nftables'][0]['table'][key] = value
            with self.subTest(key=key), self.assertRaises(wan.HarnessError):
                wan.delete_plan(value_doc, SESSION)
        for document in ({}, {'nftables': []}, {'nftables': self.table['nftables'] * 2}):
            with self.assertRaises(wan.HarnessError):
                wan.delete_plan(document, SESSION)


if __name__ == '__main__':
    unittest.main()
