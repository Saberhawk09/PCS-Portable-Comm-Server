import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_manual as cellular

@unittest.skipIf(sys.platform=='win32','Linux-only scenario')
class ManualCampaign(unittest.TestCase):
    def simulate(self,case='pass'):
        from pcs_qualify_lan import Receipts
        clock={'now':100.,'fault':None,'removed':None,'restore':0}
        events=[]
        session=type('Session',(),{'id':'a'*32,'manifest':{},'event':lambda _,k,v:events.append((k,v))})()
        token=dict(session='/active/7',profile='profile')
        class Receiver:
            def __init__(self,*args):self.receipts=Receipts(session.id);self.finished=False
            def poll(self):
                clock['now']+=1
                if case=='witness_loss' and clock['fault'] is not None and clock['now']-clock['fault']>5:return
                n=len(self.receipts.samples);p=dict(version=1,session=session.id,seq=n)
                self.receipts.receive(dict(p,phase='before'),clock['now']-.1)
                reply=self.receipts.receive(dict(p,phase='after',ok=not (case=='http_failure' and clock['fault'] is not None)),clock['now'])
                self.finished=reply['done']
            def finish(self,at):self.receipts.finish_after=at
            def close(self):pass
        def snapshot():
            if case in ('route_ambiguous','private_error') and clock['fault'] is not None:
                raise cellular.Ambiguous('effective_route_unverified' if case=='route_ambiguous' else 'private identity detail')
            if case=='service_failure' and clock['fault'] is not None:raise cellular.HarnessError('unexpected_failed_service')
            if case=='identity_failure' and clock['fault'] is not None:raise cellular.HarnessError('wifi_profile_changed')
            fault=clock['fault'] is not None and clock['removed'] is None
            elapsed=clock['now']-(clock['fault'] or clock['now'])
            recovery=clock['removed'] is not None and clock['now']-clock['removed']>=30
            active=clock['fault'] is not None and elapsed>=40 and not recovery
            if case=='no_activation':active=False
            if case=='stuck_owned' and clock['removed'] is not None:active=True
            facts=dict(boot='boot',daemon='daemon',owned={},suppressed=[],
                active=[dict(token,state=2,interface='wwan0')],
                cellular_id='cell',cellular_profile='profile',bearer_connected=True,
                modem_state=11,registration=1,modem_identity=['modem'],
                operator_sessions={'cell':dict(token,boot='boot',daemon='daemon',
                    origin='operator_connect',monotonic=1,utc=1)})
            if case=='initial_owned':facts['owned']={'cell':token}
            if case=='no_audit':facts['operator_sessions']={}
            if case=='claim' and clock['fault'] is not None:facts['owned']={'cell':token}
            if case=='disappear' and recovery:facts['active']=[]
            if case=='replacement' and clock['fault'] is not None:facts['active'][0]['session']='/replacement'
            if case=='cleanup_disconnect' and clock['restore']>=2:facts['active']=[]
            def row(kind,healthy,chosen,owned=False):
                return dict(type=kind,internet=healthy,internet6=True,selected=chosen,active=chosen,
                    owned=owned,link=True,address=True)
            ethernet=not fault
            chosen=not active
            rows=[row('ethernet',ethernet,chosen),row('wifi',ethernet,False),row('cellular',True,active,False)]
            effective='cellular' if active else 'ethernet'
            if case=='route_disagrees' and active:effective='wifi'
            return dict(fixed=('fixed',),facts=facts,uplink=dict(uplinks=rows,internet=True),
                slots=[0,1],cell_slot=2,effective=effective,server='10.42.0.1',client='10.42.0.20',
                target=None,wifi_target=None,lan='10.42.0.0/24',wan='192.168.50.0/24',wifi_net='192.168.1.0/24',
                failure_seconds=30,recovery_seconds=30,poll_seconds=10)
        def arm(*args,**kwargs):
            kwargs['verify']()
            if case=='slow_prepare':raise cellular.HarnessError('lease_setup_too_slow')
            if case=='partial_error':raise OSError('uncertain_commit')
            clock['fault']=clock['now']
        def restore(**kwargs):
            clock['restore']+=1
            if clock['fault'] is not None and clock['removed'] is None:clock['removed']=clock['now']
        from contextlib import ExitStack
        def command(*args,**kwargs):
            if case=='dns_failure':raise cellular.HarnessError('collector_failed')
            return 'resolved'
        with ExitStack() as stack:
            values={'RUNTIME':Path('/nonexistent-fq302-fixture'),'command':command,'baseline_checks':lambda:'f'*40,'require_safe':lambda *a:None,'snapshot':snapshot,
                'checkpoint':lambda:dict(power=dict(status='ok')),'assess':lambda _: 'PASS',
                'Receiver':Receiver,'arm_manual':arm,'restore':restore,'lease_valid':lambda _:True,
                'table':lambda:None if case!='cleanup_failure' and (clock['removed'] is not None or clock['fault'] is None) else {},'owned_handle':lambda *a:1,'boot_id':lambda:'boot'}
            for name,value in values.items():stack.enter_context(patch.object(cellular,name,value))
            stack.enter_context(patch.object(cellular.time,'monotonic',lambda:clock['now']))
            if case=='cleanup_failure':
                with self.assertRaisesRegex(cellular.HarnessError,'cellular_fault_cleanup_unverified'):cellular.run(session,150)
                result=None
            else:
                result=cellular.run(session,150)
        return result,clock,events
    def test_preserves_audited_session(self):
        result,clock,events=self.simulate()
        self.assertEqual(result,('PASS','manual_cellular_session_preserved'))
        self.assertIn(('operator_session_after_cleanup',dict(preserved=True,manager_owned=False)),events)
        self.assertGreater(clock['now']-clock['removed'],50)
        self.assertNotIn('/active/7',json.dumps(events))
        self.assertNotIn('"profile"',json.dumps(events))
    def test_manager_owned_and_missing_audit_block_without_fault(self):
        for case in ('initial_owned','no_audit'):
            result,clock,_=self.simulate(case)
            self.assertEqual(result[0],'BLOCKED');self.assertIsNone(clock['fault'])
    def test_claim_loss_replacement_and_cleanup_loss_fail(self):
        for case,reason in [('claim','manager_claimed_operator_session'),('disappear','operator_session_disappeared'),
                            ('replacement','operator_session_identity_changed'),('cleanup_disconnect','operator_session_disappeared')]:
            with self.subTest(case=case):
                result,clock,_=self.simulate(case)
                self.assertEqual(result,('FAIL',reason));self.assertGreater(clock['restore'],0)
    def test_witness_and_route_ambiguity_remain_inconclusive(self):
        for case in ('witness_loss','route_ambiguous'):
            self.assertEqual(self.simulate(case)[0][0],'INCONCLUSIVE')
    def test_partial_fault_error_and_cleanup_failure(self):
        result,clock,_=self.simulate('partial_error')
        self.assertEqual(result[0],'HARNESS ERROR');self.assertGreater(clock['restore'],0)
        self.simulate('cleanup_failure')
    def test_http_failure_is_fail(self):
        self.assertEqual(self.simulate('http_failure')[0][0],'FAIL')
    def test_dns_is_separate_observation(self):
        self.assertEqual(self.simulate('dns_failure')[0][0],'PASS WITH OBSERVATION')

@unittest.skipIf(sys.platform=='win32','Linux-only ownership observation')
class Provenance(unittest.TestCase):
    def facts(self):
        token=dict(session='/active/operator',profile='private-profile')
        return dict(boot='boot',daemon='daemon',owned={},suppressed=[],active=[dict(token,state=2,interface='wwan0')],
            cellular_id='cell',cellular_profile='private-profile',modem_identity=['private-modem'],
            modem_state=11,registration=1,bearer_connected=True,operator_sessions={'cell':dict(token,
                boot='boot',daemon='daemon',origin='operator_connect',monotonic=1,utc=1)})
    @patch('pcs_qualify_manual.boot_id',return_value='boot')
    def test_audit_is_exact_and_cannot_be_forged_from_active_unowned(self,_):
        import copy
        facts=self.facts()
        identity=cellular.manual_identity(facts)
        for field,value in [('boot','other'),('daemon','other'),('profile','other'),('session','other'),
                            ('origin','automatic'),('monotonic',float('nan')),('utc',None)]:
            with self.subTest(field=field):
                bad=copy.deepcopy(facts);bad['operator_sessions']['cell'][field]=value
                with self.assertRaises(cellular.Ambiguous):cellular.manual_identity(bad)
        facts['operator_sessions']['cell']['utc']=2
        with self.assertRaises(cellular.ManualFailure):cellular.manual_identity(facts,identity)
    @patch('pcs_qualify_manual.boot_id',return_value='boot')
    def test_absent_audit_and_unknown_owner_block(self,_):
        for key,value in [('operator_sessions',{}),('operator_sessions',None),('owned',None)]:
            facts=self.facts();facts[key]=value
            with self.assertRaises(cellular.Ambiguous):cellular.manual_identity(facts)

if sys.platform != 'win32':
    import test_pcs_qualify_fault as lifecycle
    import pcs_qualify_wan as wan

@unittest.skipIf(sys.platform=='win32','Linux-only manual dual-fault lease')
class ManualLifecycle(unittest.TestCase if sys.platform=='win32' else lifecycle.FaultLifecycle):
    def setUp(self):
        owner=patch.object(lifecycle,'OWNER',wan.MANUAL_OWNER)
        owner.start();self.addCleanup(owner.stop)
        super().setUp()
    def arm(self):
        lifecycle.fault.arm_manual(self.session,self.target,wan.Identity('wlan0',5,'02:00:00:00:00:02'),
            60,'10.42.0.0/24','192.168.50.0/24','192.168.1.0/24',self.runtime)
