"""FQ-302 ownership proof and atomic dual-fault compilation."""
import copy
import os
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
if sys.platform != 'win32':
    import pcs_qualify_cellular as cellular
    import pcs_qualify_wan as wan

@unittest.skipIf(sys.platform=='win32','Linux-only checkout collector')
class Checkout(unittest.TestCase):
    def test_real_sanitized_collector_preserves_clean_and_dirty_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            def git(*args):
                return subprocess.check_output(['git','-C',directory,*args],stderr=subprocess.DEVNULL).decode().strip()
            git('init');(path/'tracked').write_text('original')
            git('add','tracked')
            git('-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
            sha=git('rev-parse','HEAD')
            config=(path/'.git/config').read_bytes()
            index=(path/'.git/index').read_bytes()
            self.assertEqual(cellular.checkout_commit(directory),sha)
            self.assertEqual((path/'.git/config').read_bytes(),config)
            self.assertEqual((path/'.git/index').read_bytes(),index)
            for name in ('tracked','untracked'):
                (path/name).write_text('changed')
                with self.assertRaisesRegex(cellular.HarnessError,'normal_checkout_dirty'):
                    cellular.checkout_commit(directory)
                if name=='tracked':(path/name).write_text('original')
            (path/'untracked').unlink()
            # Real cross-owner reproduction when this suite runs in the root VM.
            if os.geteuid()==0:
                os.chown(path,65534,65534);os.chown(path/'.git',65534,65534)
                with self.assertRaises(cellular.HarnessError):
                    cellular.command(['/usr/bin/git','-C',directory,'status','--porcelain'])
                self.assertEqual(cellular.checkout_commit(directory),sha)
                (path/'untracked').write_text('still blocked')
                with self.assertRaisesRegex(cellular.HarnessError,'normal_checkout_dirty'):
                    cellular.checkout_commit(directory)

@unittest.skipIf(sys.platform=='win32','Linux-only scenario')
class Ownership(unittest.TestCase):
    def setUp(self):
        self.f=dict(boot='boot',owned={},suppressed=[],active=[],cellular_id='cell',
            cellular_profile='profile',bearer_connected=False,modem_state=8,registration=1)
        self.token=dict(session='/active/7',profile='profile')
        self.p=patch.object(cellular,'boot_id',return_value='boot');self.p.start();self.addCleanup(self.p.stop)
    def active(self):
        self.f.update(active=[dict(self.token,state=2,interface='wwan0')],bearer_connected=True)
        self.f['owned']['cell']=self.token.copy()
    def test_inactive_is_not_owned(self):
        self.assertEqual(cellular.ownership(self.f),('inactive',None))
    def test_exact_ledger_session_and_profile_required(self):
        self.active();self.assertEqual(cellular.ownership(self.f),('owned_active',self.token))
        for field in ('session','profile'):
            bad=copy.deepcopy(self.f);bad['owned']['cell'][field]='other'
            with self.subTest(field=field),self.assertRaises(cellular.Ambiguous):cellular.ownership(bad)
    def test_operator_session_is_never_adopted(self):
        self.active();self.f['owned']={}
        with self.assertRaises(cellular.Ambiguous):cellular.ownership(self.f)
    def test_replaced_session_aborts(self):
        self.active()
        with self.assertRaises(cellular.Ambiguous):cellular.ownership(self.f,dict(session='/old',profile='profile'))
    def test_pending_bearer_cannot_prove_active_internet(self):
        self.active();self.f['bearer_connected']=False
        self.assertEqual(cellular.ownership(self.f)[0],'owned_pending')
    def test_release_requires_prior_exact_session_and_recovery(self):
        self.active();self.f['owned']={};self.f['active'][0]['state']=3
        with self.assertRaises(cellular.Ambiguous):cellular.ownership(self.f,self.token)
        self.assertEqual(cellular.ownership(self.f,self.token,True)[0],'releasing')
    def test_no_session_with_bearer_or_stale_ownership_is_ambiguous(self):
        for field,value in [('bearer_connected',True),('owned',{'cell':self.token}),('suppressed',['cell']),('boot','old'),('modem_state',-1),('registration',0)]:
            bad=copy.deepcopy(self.f);bad[field]=value
            with self.subTest(field=field),self.assertRaises(cellular.Ambiguous):cellular.ownership(bad)
    def test_collector_contains_no_mutation_methods(self):
        import ast
        source=(Path(__file__).resolve().parents[1]/'scripts/pcs_qualify_cellular_read.py').read_text()
        calls=[n.func.attr for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        self.assertFalse(set(calls)&{'observe','activate','deactivate','ActivateConnection','DeactivateConnection','Connect','CreateBearer','Enable','Reapply','Update','save'})

@unittest.skipIf(sys.platform=='win32','Linux-only route snapshot')
class RouteSnapshot(unittest.TestCase):
    def setUp(self):
        self.token=dict(session='/active/7',profile='profile')
        self.pending=dict(boot='boot',daemon='daemon',modem_identity=['modem'],
            cellular_id='cell',cellular_profile='profile',activation='fallback',
            owned={'cell':self.token.copy()},suppressed=[],modem_state=10,registration=1,
            bearer_connected=False,active=[dict(self.token,state=1,interface='')])
        self.ready=copy.deepcopy(self.pending)
        self.ready.update(modem_state=11,bearer_connected=True,
                          active=[dict(self.token,state=2,interface='wwan0')])
        p=patch.object(cellular,'boot_id',return_value='boot');p.start();self.addCleanup(p.stop)
    def test_activation_between_reads_refreshes_once_for_same_owned_session(self):
        with patch.object(cellular,'one',return_value={'dev':'wwan0'}) as route, \
                patch.object(cellular,'cellular_facts',return_value=self.ready) as facts:
            self.assertEqual(cellular.effective_route(self.pending,'1.1.1.1'),(self.ready,'cellular',True))
            self.assertEqual(route.call_count,2);facts.assert_called_once_with()
    def test_stable_route_needs_no_refresh(self):
        with patch.object(cellular,'one',return_value={'dev':'eth1'}), \
                patch.object(cellular,'cellular_facts') as facts:
            self.assertEqual(cellular.effective_route(self.pending,'1.1.1.1')[1:],('ethernet',False))
            facts.assert_not_called()
    def test_refresh_rejects_identity_ownership_and_session_changes(self):
        edits=[('boot','other'),('daemon','other'),('modem_identity',['other']),
               ('cellular_profile','other'),('owned',{}),('suppressed',['cell']),
               ('active',[dict(self.token,session='/replacement',state=2,interface='wwan0')])]
        for key,value in edits:
            fresh=copy.deepcopy(self.ready);fresh[key]=value
            with self.subTest(key=key),patch.object(cellular,'one',return_value={'dev':'wwan0'}), \
                    patch.object(cellular,'cellular_facts',return_value=fresh),self.assertRaises(cellular.Ambiguous):
                cellular.effective_route(self.pending,'1.1.1.1')
    def test_refresh_never_accepts_unknown_or_changing_route_or_pending_bearer(self):
        for device,second,fresh in [('wg-pcs','wg-pcs',self.ready),('wwan0','eth1',self.ready),
                                     ('wwan0','wwan0',self.pending)]:
            with self.subTest(device=device,second=second,fresh=fresh), \
                    patch.object(cellular,'one',side_effect=[{'dev':device},{'dev':second}]), \
                    patch.object(cellular,'cellular_facts',return_value=fresh) as facts,self.assertRaises(cellular.Ambiguous):
                cellular.effective_route(self.pending,'1.1.1.1')
            facts.assert_called_once_with()
    def test_unowned_activation_and_policy_route_are_not_refreshed(self):
        unowned=copy.deepcopy(self.pending);unowned['owned']={}
        for initial,route in [(unowned,{'dev':'wwan0'}),(self.pending,{'dev':'wwan0','table':100})]:
            with patch.object(cellular,'one',return_value=route),patch.object(cellular,'cellular_facts') as facts, \
                    self.assertRaises(cellular.Ambiguous):cellular.effective_route(initial,'1.1.1.1')
            facts.assert_not_called()
    def test_collector_failure_is_not_retried(self):
        with patch.object(cellular,'one',return_value={'dev':'wwan0'}), \
                patch.object(cellular,'cellular_facts',side_effect=cellular.HarnessError('collector_unavailable')) as facts, \
                self.assertRaises(cellular.HarnessError):cellular.effective_route(self.pending,'1.1.1.1')
        facts.assert_called_once_with()

@unittest.skipIf(sys.platform=='win32','Linux-only compiler')
class DualFault(unittest.TestCase):
    def test_one_atomic_table_with_two_time_limited_indices(self):
        text=wan.create_cellular_plan('a'*32,wan.Identity('eth1',4,'02:00:00:00:00:01'),
            wan.Identity('wlan0',5,'02:00:00:00:00:02'),180,'10.42.0.0/24','192.168.50.0/24','192.168.1.0/24')
        self.assertEqual(text.count('create table'),1)
        self.assertIn('4 timeout 180s, 5 timeout 180s',text)
        self.assertIn('192.168.50.0/24',text);self.assertIn('192.168.1.0/24',text)
        self.assertIn(wan.CELLULAR_OWNER+'a'*32,text)
        self.assertNotIn('hook input',text)
    def test_301_owner_does_not_accept_302_table(self):
        table={'nftables':[{'table':dict(name=wan.TABLE,family='inet',handle=8,comment=wan.CELLULAR_OWNER+'a'*32)}]}
        with self.assertRaises(ValueError):wan.owned_handle(table,'a'*32)
        self.assertEqual(wan.owned_handle(table,'a'*32,wan.CELLULAR_OWNER),8)
    def test_invalid_target_and_overlap_refused(self):
        eth=wan.Identity('eth1',4,'02:00:00:00:00:01')
        for wifi,net in [(wan.Identity('eth0',5,''),'192.168.1.0/24'),(wan.Identity('wlan0',4,''),'192.168.1.0/24'),(wan.Identity('wlan0',5,''),'10.42.0.0/24')]:
            with self.assertRaises(ValueError):wan.create_cellular_plan('a'*32,eth,wifi,180,'10.42.0.0/24','192.168.50.0/24',net)



@unittest.skipIf(sys.platform=='win32','Linux-only scenario')
class Campaign(unittest.TestCase):
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
                reply=self.receipts.receive(dict(p,phase='after',ok=case!='http_failure'),clock['now'])
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
            facts=dict(boot='boot',owned={'cell':token} if active else {},suppressed=[],
                active=[dict(token,state=2,interface='wwan0')] if active else [],
                cellular_id='cell',cellular_profile='profile',bearer_connected=active,
                modem_state=8,registration=1)
            if case=='manual' and active:facts['owned']={}
            if case=='initial_active' and clock['fault'] is None:
                facts.update(active=[dict(token,state=2,interface='wwan0')],bearer_connected=True)
            def row(kind,healthy,chosen,owned=False):
                return dict(type=kind,internet=healthy,internet6=True,selected=chosen,active=chosen,
                    owned=owned,link=True,address=True)
            ethernet=not fault
            chosen=not active
            rows=[row('ethernet',ethernet,chosen),row('wifi',ethernet,False),row('cellular',active,active,active)]
            effective='cellular' if active else 'ethernet'
            if case=='route_disagrees' and active:effective='wifi'
            return dict(fixed=('fixed',),facts=facts,uplink=dict(uplinks=rows,internet=True),
                slots=[0,1],cell_slot=2,effective=effective,server='10.42.0.1',client='10.42.0.20',
                target=None,wifi_target=None,lan='10.42.0.0/24',wan='192.168.50.0/24',wifi_net='192.168.1.0/24',
                failure_seconds=30,recovery_seconds=30,poll_seconds=10)
        def arm(*args,**kwargs):
            kwargs['verify']()
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
                'Receiver':Receiver,'arm_cellular':arm,'restore':restore,'lease_valid':lambda _:True,
                'table':lambda:None if case!='cleanup_failure' and (clock['removed'] is not None or clock['fault'] is None) else {},'owned_handle':lambda *a:1,'boot_id':lambda:'boot'}
            for name,value in values.items():stack.enter_context(patch.object(cellular,name,value))
            stack.enter_context(patch.object(cellular.time,'monotonic',lambda:clock['now']))
            if case=='cleanup_failure':
                with self.assertRaisesRegex(cellular.HarnessError,'cellular_fault_cleanup_unverified'):cellular.run(session,180)
                result=None
            else:
                result=cellular.run(session,180)
        return result,clock,events
    def test_complete_owned_lifecycle(self):
        result,clock,events=self.simulate()
        self.assertEqual(result,('PASS','automatic_cellular_ownership_and_release'))
        for name in ('preferred_paths_unhealthy','cellular_owned','cellular_bearer_active','cellular_internet_proven',
                     'wan_fault_removed','preferred_route_restored','cellular_disconnected_and_ownership_cleared'):
            self.assertIn(name,[n for n,_ in events])
        self.assertGreater(clock['restore'],0)
        self.assertIn(('cellular_fault_cleanup',dict(verified=True,cellular_manipulated=False)),events)
    def test_cleanup_failure_is_independent_and_cannot_complete_as_pass(self):
        result,clock,events=self.simulate('cleanup_failure')
        self.assertIsNone(result);self.assertGreater(clock['restore'],0)
        self.assertIn(('cellular_fault_cleanup',dict(verified=False,cellular_manipulated=False)),events)
    def test_active_initial_session_blocks_without_fault(self):
        result,clock,_=self.simulate('initial_active');self.assertEqual(result[0],'BLOCKED');self.assertIsNone(clock['fault'])
    def test_manual_or_unproven_ownership_is_inconclusive_and_restored(self):
        result,clock,_=self.simulate('manual');self.assertEqual(result[0],'INCONCLUSIVE');self.assertGreater(clock['restore'],0)
    def test_witness_loss_inconclusive(self):
        result,clock,_=self.simulate('witness_loss');self.assertEqual(result[0],'INCONCLUSIVE');self.assertGreater(clock['restore'],0)
    def test_ambiguity_records_only_allowlisted_reason_and_still_cleans_up(self):
        for case,reason in [('route_ambiguous','effective_route_unverified'),
                            ('private_error','cellular_ownership_or_route_unproven')]:
            result,clock,events=self.simulate(case)
            self.assertEqual(result,('INCONCLUSIVE',reason))
            self.assertIn(('cellular_admission_lost',dict(reason=reason)),events)
            self.assertGreater(clock['restore'],0)
            self.assertNotIn('private identity detail',str(events))
    def test_no_activation_route_disagreement_and_failed_release_never_pass(self):
        for case in ('no_activation','route_disagrees','stuck_owned'):
            with self.subTest(case=case):
                result,clock,_=self.simulate(case);self.assertEqual(result[0],'FAIL');self.assertGreater(clock['restore'],0)
    def test_service_failure_fails_identity_change_aborts_and_both_restore(self):
        for case,expected in [('service_failure','FAIL'),('identity_failure','ABORTED')]:
            with self.subTest(case=case):
                result,clock,_=self.simulate(case);self.assertEqual(result[0],expected);self.assertGreater(clock['restore'],0)
    def test_dns_failure_is_separate_observation(self):
        result,clock,events=self.simulate('dns_failure')
        self.assertEqual(result[0],'PASS WITH OBSERVATION')
        self.assertIn(('cellular_dns_observation',dict(result='unavailable',scope='system_resolver_separate_from_interface_probe')),events)
    def test_uncertain_activation_cleans_up(self):
        result,clock,_=self.simulate('partial_error');self.assertEqual(result[0],'HARNESS ERROR');self.assertGreater(clock['restore'],0)



@unittest.skipIf(sys.platform=='win32','Linux production controller fixture')
class ProductionOwnership(unittest.TestCase):
    def test_real_controller_creates_exact_owned_activation_and_releases_it(self):
        import tempfile
        import test_uplink_manager as fixture
        class NM(fixture.FakeNM):
            def deactivate(self,session):
                super().deactivate(session)
                self.obs['cellular']=fixture.m.Observation()
        with tempfile.TemporaryDirectory() as folder,patch.object(fixture.m.subprocess,'run'),patch.object(cellular,'boot_id',return_value='boot'):
            nm=NM(fixture.observations(starlink=True,wifi=True))
            c=fixture.m.Controller(fixture.config(),nm,folder,boot='boot')
            c.step(0);c.step(30)
            self.assertEqual(nm.activated,[])
            nm.obs['starlink'].internet=nm.obs['wifi'].internet=False
            c.step(40);c.step(69)
            self.assertEqual(nm.activated,[])
            c.step(70)
            self.assertEqual(nm.activated,['cellular'])
            observation=nm.obs['cellular']
            facts=dict(boot='boot',owned=c.state['owned'],suppressed=[],cellular_id='cellular',
                cellular_profile=fixture.CELL_UUID,bearer_connected=True,modem_state=11,registration=1,
                active=[dict(session=observation.session,profile=observation.profile,state=2,interface='wwan0')])
            state,token=cellular.ownership(facts)
            self.assertEqual(state,'owned_active')
            c.step(80);c.step(110)
            self.assertEqual(nm.effective(),'wwan0')
            nm.obs['starlink'].internet=nm.obs['wifi'].internet=True
            c.step(120);c.step(149)
            self.assertEqual(nm.disconnected,[])
            c.step(150)
            self.assertEqual(nm.disconnected,[token['session']])
            self.assertEqual(c.state['owned'],{})
            facts.update(owned=c.state['owned'],active=[],bearer_connected=False)
            self.assertEqual(cellular.ownership(facts,token,True)[0],'inactive')



if sys.platform != 'win32':
    import test_pcs_qualify_fault as lifecycle

@unittest.skipIf(sys.platform=='win32','Linux-only dual lease lifecycle')
class DualLifecycle(unittest.TestCase if sys.platform=='win32' else lifecycle.FaultLifecycle):
    def setUp(self):
        owner=patch.object(lifecycle,'OWNER',wan.CELLULAR_OWNER)
        owner.start();self.addCleanup(owner.stop)
        super().setUp()
    def arm(self):
        lifecycle.fault.arm_cellular(self.session,self.target,wan.Identity('wlan0',5,'02:00:00:00:00:02'),
            60,'10.42.0.0/24','192.168.50.0/24','192.168.1.0/24',self.runtime)

if __name__=='__main__':unittest.main()
