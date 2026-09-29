import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_scenario as scenario
    from pcs_qualify_lan import Receipts


@unittest.skipIf(sys.platform == 'win32', 'Linux runner imports')
class WanScenario(unittest.TestCase):
    def simulate(self, fault=None):
        context = dict(now=100., injected=None, removed=None, snapshots=0)
        session = type('Session', (), {'id': 'a' * 32, 'events': [], 'manifest': {}})()
        session.event = lambda kind, evidence: session.events.append((kind, evidence))
        class Receiver:
            def __init__(self, *_args):
                self.receipts = Receipts(session.id)
            def poll(self):
                context['now'] += 1
                if fault == 'missing_witness': return
                seq = len(self.receipts.samples)
                base = dict(version=1, session=session.id, seq=seq)
                self.receipts.receive(dict(base, phase='before'), context['now'] - .1)
                self.receipts.receive(dict(base, phase='after', ok=not (fault == 'http' and seq > 3)), context['now'])
            def close(self): pass
        def snapshot():
            context['snapshots'] += 1
            if fault == 'stale' and context['injected'] is not None:
                raise scenario.HarnessError('cache_stale_or_wrong_boot')
            active_fault = context['injected'] is not None and context['removed'] is None
            fallback = active_fault and fault != 'no_fallback'
            rows = [dict(type='ethernet', internet=not active_fault, selected=not fallback,
                         active=not fallback, link=True, address=True),
                    dict(type='wifi', internet=True, selected=fallback, active=fallback, link=True, address=True)]
            fixed = 'changed' if fault == 'identity' and context['injected'] else 'fixed'
            return dict(fixed=fixed, slots=[0, 1], target=None, lan='10.42.0.0/24', wan='192.168.50.0/24',
                        server='10.42.0.1', client='10.42.0.20', uplink=dict(internet=True, uplinks=rows),
                        failure_seconds=5, recovery_seconds=5, poll_seconds=1)
        def arm(*args, **kwargs): context['injected'] = context['now']
        def rf_check(*args):
            if fault == 'rf_changed' and context['injected'] is not None:
                raise scenario.RFBlocked('rf_engine_active')
        def restore(**kwargs):
            if context['injected'] is not None and context['removed'] is None:
                context['removed'] = context['now']
        with patch.object(scenario, 'snapshot', side_effect=snapshot), \
                patch.object(scenario, 'require_safe', side_effect=rf_check), \
                patch.object(scenario, 'checkpoint', return_value={}), \
                patch.object(scenario, 'assess', return_value='PASS'), \
                patch.object(scenario, 'Receiver', Receiver), \
                patch.object(scenario, 'boot_id', return_value='same-boot'), \
                patch.object(scenario.time, 'monotonic', side_effect=lambda: context['now']), \
                patch.object(scenario, 'arm_wan', side_effect=arm), \
                patch.object(scenario, 'lease_valid', return_value=True), \
                patch.object(scenario, 'table', return_value={}), \
                patch.object(scenario, 'owned_handle', return_value=1), \
                patch.object(scenario, 'restore', side_effect=restore):
            result = scenario.run(session, 60)
        return result, context, session.events

    def test_complete_transition_requires_detection_recovery_and_witness(self):
        result, context, events = self.simulate()
        self.assertEqual(result, ('PASS', 'sampled_lan_and_ipv4_transition'))
        names = [name for name, _ in events]
        for name in ('wan_failure_observed', 'wan_fallback_observed', 'wan_fault_removed', 'wan_recovery_observed'):
            self.assertIn(name, names)
        self.assertGreaterEqual(context['removed'] - context['injected'], 60)

    def test_missing_witness_never_injects(self):
        result, context, _ = self.simulate('missing_witness')
        self.assertEqual(result[0], 'BLOCKED')
        self.assertIsNone(context['injected'])

    def test_changed_identity_aborts_and_restores(self):
        result, context, _ = self.simulate('identity')
        self.assertEqual(result[0], 'ABORTED')
        self.assertIsNotNone(context['removed'])

    def test_http_failure_fails_and_restores(self):
        result, context, _ = self.simulate('http')
        self.assertEqual(result[0], 'FAIL')
        self.assertIsNotNone(context['removed'])

    def test_rf_state_change_aborts_and_cleans_up(self):
        result, context, _ = self.simulate('rf_changed')
        self.assertEqual(result, ('ABORTED', 'rf_engine_active'))
        self.assertIsNotNone(context['removed'])

    def test_stale_observation_is_inconclusive_and_restores(self):
        result, context, _ = self.simulate('stale')
        self.assertEqual(result[0], 'INCONCLUSIVE')
        self.assertIsNotNone(context['removed'])

    def test_recovery_without_fallback_does_not_pass(self):
        result, _, _ = self.simulate('no_fallback')
        self.assertEqual(result, ('FAIL', 'failure_or_fallback_not_observed'))

    def test_snapshot_collects_source_route_and_blocks_wrong_identity(self):
        mac = '02:00:00:00:00:01'
        rows = []
        for kind, iface, priority in [('ethernet', 'eth1', 1), ('wifi', 'wlan0', 2)]:
            row = {k: False for k in ('link', 'address', 'address6', 'active', 'selected', 'selected6', 'owned', 'suppressed')}
            row.update(type=kind, interface=iface, id=kind, priority=priority,
                       profile='fixture-profile', internet=True, internet6=True, link=True, address=True)
            rows.append(row)
        cache = dict(version=1, generated_at=100., boot='boot', mode='auto', internet=True, uplinks=rows)
        config = dict(version=1, mode='auto', uplinks=[dict(id=r['id'], type=r['type'],
                      interface=r['interface'], profile=r['profile'], priority=r['priority']) for r in rows])
        environment = dict(dev='eth0', mac=mac)
        calls = []
        def command(argv):
            calls.append(argv)
            if argv[0].endswith('ethtool'): return 'Permanent address: ' + mac
            if 'address' in argv:
                address = '10.42.0.1' if argv[-1] == 'eth0' else '192.168.50.237'
                return json.dumps([dict(addr_info=[dict(family='inet', scope='global', local=address, prefixlen=24)])])
            if 'link' in argv:
                return json.dumps([dict(ifname='eth1', ifindex=4, link_type='ether', operstate='UP',
                                        flags=['UP', 'LOWER_UP'], address=environment['mac'])])
            if 'rule' in argv:
                return json.dumps([dict(priority=p, src='all', table=t) for p, t in
                                   ((0, 'local'), (32766, 'main'), (32767, 'default'))])
            return json.dumps([dict(dst='10.42.0.20', dev=environment['dev'], **{'from': '10.42.0.1'})])
        with patch.dict(scenario.os.environ, {'SSH_CONNECTION': '10.42.0.20 50000 10.42.0.1 22'}), \
                patch.object(scenario, 'command', side_effect=command), \
                patch.object(scenario, 'read_json', side_effect=lambda p, *_: cache if p == scenario.UPLINK else config), \
                patch.object(scenario.time, 'time', return_value=100.), \
                patch.object(scenario, 'boot_id', return_value='boot'):
            result = scenario.snapshot()
            self.assertEqual(result['target'].index, 4)
            self.assertIn(['/usr/sbin/ip', '-j', '-4', 'route', 'get', '10.42.0.20', 'from', '10.42.0.1'], calls)
            for key, wrong, expected in [('dev', 'eth1', 'independent_direct_lan_control_required'),
                                         ('mac', '02:00:00:00:00:02', 'wan_permanent_identity_unverified')]:
                old = environment[key]
                environment[key] = wrong
                with self.subTest(key=key), self.assertRaisesRegex(scenario.HarnessError, expected):
                    scenario.snapshot()
                environment[key] = old


if __name__ == '__main__': unittest.main()
