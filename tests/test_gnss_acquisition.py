"""Protocol 40.00 fixtures; these are not hardware TTFF measurements."""
import contextlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pcs_gnss_acquisition as acq
import pcs_gnss_maintenance as maintenance
import pcs_gnss_info as info
import pcs_gnss_manager as manager
import pcs_assistnow as assist
from pcs_gnss import atomic_json,ubx
from test_gnss_intelligence import IDENTITY,NOW,prediction
from test_gnss_corrections import pvt,fixture,panel


def dbd(n=1):
    return b''.join(ubx(0x13,0x80,bytes((i,))+bytes(11)) for i in range(n))


def complete(n=1):
    return b'\1\0\0\x80'+struct.pack('<I',n)


class PolicyTests(unittest.TestCase):
    def test_documented_resets_preserve_configuration(self):
        for mode,mask in [('hot',0),('warm',1),('cold',65535)]:
            self.assertEqual(acq.reset_frame(mode),ubx(6,4,struct.pack('<HBB',mask,2,0)))
        self.assertEqual(acq.control_frame(False),ubx(6,4,b'\0\0\x08\0'))
        self.assertEqual(acq.control_frame(True),ubx(6,4,b'\0\0\x09\0'))
        with self.assertRaises(ValueError):
            acq.reset_frame('power-cycle')

    def test_all_policies_and_disabled_shutdown_default(self):
        self.assertFalse(acq.policy({})['save_on_shutdown'])
        for name in acq.POLICIES:
            for fallback in acq.FALLBACKS:
                self.assertEqual(acq.policy({'boot_policy':name,'fallback':fallback})['boot_policy'],name)
        for bad in ({'boot_policy':'invented'},{'save_on_shutdown':'yes'},{'backup_retention':11},
                    {'save_on_shutdown':True},{'secret':'do-not-render'}):
            with self.assertRaises(ValueError):
                acq.policy(bad)

    def test_trial_modes_sources_bounds_and_alternating_order(self):
        for mode in acq.MASKS:
            for source in acq.SOURCES:
                rows=acq.trials({'confirm':acq.CONFIRM,'start':mode,'source':source})
                self.assertEqual((rows[0]['start'],rows[0]['source']),(mode,source))
        for comparison in acq.COMPARISONS:
            rows=acq.trials({'confirm':acq.CONFIRM,'comparison':comparison,'repetitions':2})
            self.assertEqual([r['source'] for r in rows[:2]],list(reversed([r['source'] for r in rows[2:]])))
        for bad in ({},{'confirm':'yes'},{'confirm':acq.CONFIRM,'repetitions':6},
                    {'confirm':acq.CONFIRM,'timeout_seconds':301},{'confirm':acq.CONFIRM,'device':'/dev/ttyUSB1'}):
            with self.assertRaises(ValueError):
                acq.trials(bad)

    def test_time_cannot_be_downloaded_or_gnss_trusted(self):
        for ref in ('F10P','F10N',''):
            self.assertFalse(acq.independent_time(NOW,True,ref)['valid'])
        self.assertTrue(acq.independent_time(NOW,True,'network')['valid'])
        self.assertFalse(acq.independent_time(0,True,'network')['valid'])
        rtc={'validated':True,'device':'ds1307','age_seconds':1,'utc':NOW}
        self.assertTrue(acq.independent_time(NOW,rtc=rtc)['valid'])
        self.assertFalse(acq.independent_time(NOW,rtc=rtc|{'age_seconds':61})['valid'])

    def test_coarse_time_layout_no_spoofing_trust_and_stale_hint_rejected(self):
        hint=acq.independent_time(NOW,True,'network',uncertainty=1)
        frame=acq.time_frame(hint,hint['observed_monotonic']+1.25)
        p=frame[6:-2]
        self.assertEqual((len(p),p[0],p[1],p[2],p[3],p[11]),(24,16,0,0,128,0))
        self.assertEqual(struct.unpack_from('<H',p,4)[0],2026)
        self.assertEqual(p[6:11],bytes((11,16,12,0,1)))
        self.assertEqual(struct.unpack_from('<I',p,12)[0],250000000)
        self.assertGreaterEqual(struct.unpack_from('<H',p,16)[0],3)
        for altered,stamp in ((hint|{'valid':False},hint['observed_monotonic']),
                              (hint,hint['observed_monotonic']+601),(hint|{'uncertainty_seconds':None},hint['observed_monotonic'])):
            with self.assertRaises(ValueError):
                acq.time_frame(altered,stamp)

    def test_independent_cached_network_crosscheck_while_pps_selected(self):
        sources='#* F10P 0 0 377 0 +2ns[+2ns] +/- 8ns\n'
        sources+='^- 172.234.44.141 2 10 377 40 -2ms[-2ms] +/- 77ms\n'
        sources+='^- 108.61.215.221 3 10 377 45 +1ms[+1ms] +/- 35ms\n'
        with mock.patch.object(maintenance,'command',side_effect=['Leap status : Normal',sources]),mock.patch.object(maintenance.time,'time',return_value=NOW):
            self.assertTrue(maintenance.time_hint()['valid'])
        with mock.patch.object(maintenance,'command',side_effect=['Leap status : Normal',sources.replace('377 45','377 999')]),mock.patch.object(maintenance.time,'time',return_value=NOW):
            hint=maintenance.time_hint()
            self.assertTrue(hint['valid'])
            self.assertGreater(hint['uncertainty_seconds'],1)
        for changed in (sources.replace('108.61.215.221','10.42.0.1'),sources.replace('377 45','377 3601'),sources.replace('+1ms','+9s')):
            with mock.patch.object(maintenance,'command',side_effect=['Leap status : Normal',changed]),mock.patch.object(maintenance.time,'time',return_value=NOW):
                self.assertFalse(maintenance.time_hint()['valid'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.store=acq.NavigationStore(self.root)
        self.hint=acq.independent_time(NOW,True,'network')

    def tearDown(self):
        self.temp.cleanup()

    def save(self,n=1):
        return self.store.publish(dbd(n),complete(n),IDENTITY,self.hint)

    def test_complete_same_receiver_database_and_private_objects(self):
        self.save(3)
        frames,meta=self.store.read(IDENTITY,NOW+1)
        self.assertEqual(len(frames),3)
        self.assertEqual(meta['bytes'],len(dbd(3)))
        if os.name=='posix':
            self.assertEqual((self.store.root/(meta['id']+'.ubx')).stat().st_mode&0o777,0o600)

    def test_invalid_frames_completion_and_no_independent_date(self):
        bad=(b'',dbd()[:-1],dbd()+b'junk',ubx(0x13,0x80,bytes(11)),ubx(0x13,0x80,bytes(165)),prediction())
        for data in bad:
            with self.subTest(size=len(data)),self.assertRaises(ValueError):
                self.store.publish(data,complete(),IDENTITY,self.hint)
        for ack in (complete(2),b'\3'+complete()[1:],complete()[:-1],bytes(8)):
            with self.assertRaises(ValueError):
                self.store.publish(dbd(),ack,IDENTITY,self.hint)
        with self.assertRaises(ValueError):
            self.store.publish(dbd(),complete(),IDENTITY,{'valid':False})

    def test_expired_future_identity_firmware_checksum_and_permissions(self):
        meta=self.save()
        for identity,stamp in ((IDENTITY|{'binding':'other'},NOW),(IDENTITY|{'firmware':'different'},NOW),
                               (IDENTITY,NOW-1),(IDENTITY,NOW+25*3600)):
            with self.assertRaises(ValueError):
                self.store.read(identity,stamp)
        path=self.store.root/(meta['id']+'.ubx')
        path.write_bytes(dbd(2))
        with self.assertRaises(ValueError):
            self.store.read(IDENTITY,NOW)
        path.write_bytes(dbd())
        if os.name=='posix':
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                self.store.read(IDENTITY,NOW)

    def test_interrupted_publication_never_replaces_last_good(self):
        first=self.save()
        original=acq.atomic_json
        def interrupted(path,value,*args):
            if Path(path).name=='latest.json':
                raise OSError('simulated power loss')
            return original(path,value,*args)
        with mock.patch.object(acq,'atomic_json',side_effect=interrupted),self.assertRaises(OSError):
            self.save(2)
        self.assertEqual(self.store.read(IDENTITY,NOW)[1]['id'],first['id'])
        for _ in range(7):
            self.store.publish(dbd(),complete(),IDENTITY,self.hint,retention=3)
        self.assertEqual(len(list(self.store.root.glob('*.ubx'))),3)

    def test_failed_publication_objects_cannot_grow_storage_without_bound(self):
        first=self.save()
        orphan=self.store.root/('f'*32+'.ubx')
        with orphan.open('wb') as stream:
            stream.truncate(12*1024*1024)
        with self.assertRaisesRegex(ValueError,'storage limit'):
            self.save(2)
        self.assertEqual(self.store.read(IDENTITY,NOW)[1]['id'],first['id'])

    @unittest.skipUnless(os.name=='posix','POSIX symlink controls')
    def test_symlink_directory_and_metadata_are_rejected(self):
        elsewhere=self.root/'elsewhere'
        elsewhere.mkdir(mode=0o700)
        (self.root/'navigation').symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaises(ValueError):
            self.save()
        with self.assertRaises(ValueError):
            self.store.read(IDENTITY,NOW)

    def test_jobs_exclusion_cancellation_bounded_history_and_export_redaction(self):
        jobs=acq.Jobs(self.root)
        value=jobs.queue({'confirm':acq.CONFIRM})
        with self.assertRaises(ValueError):
            jobs.queue({'confirm':acq.CONFIRM})
        jobs.cancel()
        self.assertTrue(jobs.cancelled(value['id']))
        value.update(state='Cancelled',identity='private-id',antenna='private-position')
        value['results']=[{'trial':1,'metrics':{'first_3d_seconds':10,'latitude':123},
                          'first_fix':{'latitude':'private-coordinate'},'identity':'private-id'}]
        jobs.finish(value)
        for kind in ('json','csv','text'):
            result=acq.result_export(value,kind)
            for private in ('private-id','private-position','latitude','private-coordinate'):
                self.assertNotIn(private,result)
        self.assertIn('10',acq.result_export(value,'csv'))


class MeasurementTests(unittest.TestCase):
    def test_in_progress_is_not_reported_as_failed_or_in_success_statistics(self):
        value=acq.summary([{'requested_source':'none','selected_source':'none','state':'Running',
                            'metrics':{'receiver_ttff_seconds':2}}])['sources']['none']
        self.assertEqual((value['failures'],value['in_progress']),(0,1))
        self.assertEqual(value['metrics']['receiver_ttff_seconds']['samples'],0)

    def test_actual_events_new_session_and_separate_recovery_metrics(self):
        clock=[100.0]
        metrics=acq.TrialMetrics(100,{'valid':False},clock=lambda:clock[0])
        metrics.pps_baseline=20
        metrics.event(1,7,pvt())
        self.assertIsNone(metrics.values['first_3d_seconds'])
        metrics.event(1,3,struct.pack('<IBBBBII',123000,3,1,0,0,8000,900000))
        self.assertFalse(metrics.after_reset_seen)
        clock[0]=110
        metrics.event(1,3,struct.pack('<IBBBBII',123000,3,1,0,0,8000,10000))
        metrics.event(1,7,pvt())
        self.assertEqual(metrics.values['receiver_ttff_seconds'],8)
        self.assertEqual(metrics.values['first_3d_seconds'],10)
        metrics.timing({},21)
        self.assertEqual(metrics.values['pps_seconds'],10)
        clock[0]=120
        state={'state':'fresh','synchronized':True,'selected_ref':'NTP','utc_valid':False}
        metrics.timing(state,22)
        self.assertEqual(metrics.values['chrony_sync_seconds'],20)
        self.assertIsNone(metrics.values['f10p_seconds'])
        clock[0]=125
        metrics.timing(state|{'selected_ref':'F10P','utc_valid':True,'pps_active':True,'pps_validation':'paired','pps_selection':'*'},23)
        self.assertEqual(metrics.values['f10p_seconds'],25)
        metrics.event(1,7,b'bad')

    def test_fallback_is_not_pooled_with_requested_source(self):
        rows=[{'requested_source':'internet','selected_source':'cached','state':'Completed','metrics':{'receiver_ttff_seconds':1}},
              {'requested_source':'internet','selected_source':'internet','state':'Completed','metrics':{'receiver_ttff_seconds':9}}]
        self.assertEqual(acq.summary(rows)['sources']['internet']['metrics']['receiver_ttff_seconds']['median'],9)


class TransferTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','POSIX ownership receipt')
    def test_restricted_gate_accepts_only_root_bound_nonreused_owner_receipts(self):
        import pcs_gnss_runtime as runtime
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'123').mkdir()
            ticks=10000
            (root/'123'/'stat').write_text('123 (gpsd) '+' '.join(['S']+['0']*18+[str(ticks)]))
            proof=root/'proof.json'
            original_lstat=Path.lstat
            metadata=SimpleNamespace(st_uid=0,st_mode=0o100644,st_size=256)
            def lstat(path,*args,**kwargs):
                return metadata if path==proof else original_lstat(path,*args,**kwargs)
            value={'kind':'owner','pid':'123','start_ticks':ticks,'generation':[99,100],'read_only':True}
            with mock.patch.object(runtime.os,'stat',return_value=SimpleNamespace(st_rdev=99,st_ino=100)),mock.patch.object(Path,'lstat',lstat):
                def owned(data):
                    proof.write_text(json.dumps(data))
                    return runtime.owned_generation('/fake/device',root,mock.Mock(side_effect=[(0,'123'),(1,'')]),proof)
                self.assertEqual(owned(value),(99,100))
                for changed in (value|{'start_ticks':ticks+1},value|{'generation':[99,101]},value|{'read_only':False}):
                    self.assertIsNone(owned(changed))
                metadata.st_uid=65534
                self.assertIsNone(owned(value))
                metadata.st_uid=0;metadata.st_mode=0o100666
                self.assertIsNone(owned(value))
                metadata.st_mode=0o100644
                admission={'kind':'admission','generation':[99,100],'read_only':True,
                           'admitted_monotonic':ticks/os.sysconf('SC_CLK_TCK')-1}
                self.assertEqual(owned(admission),(99,100))
                self.assertIsNone(owned(admission|{'admitted_monotonic':-1000}))

    def test_gate_restart_recognizes_existing_readonly_owner_without_serial_open(self):
        import pcs_gnss_runtime as runtime
        with tempfile.TemporaryDirectory() as tmp:
            proc=Path(tmp);(proc/'123').mkdir();(proc/'123'/'cmdline').write_bytes(b'GPSD\0-b\0')
            device=proc/'device';device.touch()
            with mock.patch.object(runtime.os,'stat',return_value=mock.Mock(st_rdev=99,st_ino=100)):
                self.assertEqual(runtime.owned_generation(str(device),proc,mock.Mock(side_effect=[(0,'123'),(0,'123')])),(99,100))
                self.assertIsNone(runtime.owned_generation(str(device),proc,mock.Mock(side_effect=[(0,'123'),(0,'999')])))
                (proc/'123'/'cmdline').write_bytes(b'GPSD\0')
                self.assertIsNone(runtime.owned_generation(str(device),proc,mock.Mock(side_effect=[(0,'123'),(0,'123')])))

    def test_timing_gate_acknowledges_only_after_withdrawal_and_never_opens_uart(self):
        import pcs_gnss_runtime as runtime
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            atomic_json(root/'maintenance.json',{'id':'a'*32})
            handlers={}
            selection=mock.Mock()
            def notified(value):
                if value=='WATCHDOG=1':
                    ack=json.loads((root/'maintenance-ack.json').read_text())
                    self.assertTrue(ack['withdrawn'])
                    self.assertEqual(ack['id'],'a'*32)
                    self.assertGreaterEqual(selection.withdraw.call_count,2)
                    handlers[runtime.signal.SIGTERM]()
            with mock.patch.object(runtime,'RUNTIME',root),mock.patch.object(runtime,'load_config',return_value={'mode':'neo-f10n'}), \
                 mock.patch.object(runtime,'Selection',return_value=selection),mock.patch.object(runtime,'connect') as connect, \
                 mock.patch.object(runtime.signal,'signal',side_effect=lambda sign,fn:handlers.update({sign:fn})), \
                 mock.patch.object(runtime,'notify',side_effect=notified),mock.patch.object(runtime,'run',return_value=(0,'')) as run:
                runtime.serve()
                connect.assert_not_called()
                self.assertTrue(all(call.args[0][0]=='chronyc' for call in run.call_args_list))
                selection.set.assert_not_called()

    def test_owner_refuses_competing_uart_and_recovers_cancelled_handover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            lease=root/'maintenance.json';journal=root/'journal.json'
            module=mock.Mock()
            with mock.patch.object(maintenance,'RUNTIME',root),mock.patch.object(maintenance,'LEASE',lease), \
                 mock.patch.object(maintenance,'JOURNAL',journal),mock.patch.object(maintenance,'lock',return_value=contextlib.nullcontext()), \
                 mock.patch.object(maintenance,'admission',return_value=module),mock.patch.object(maintenance,'enrolled'), \
                 mock.patch.object(maintenance,'command',return_value='123'),mock.patch.object(maintenance,'owners',return_value=['999']):
                with self.assertRaises(ValueError):
                    with maintenance.Owner('a'*32).acquire():
                        self.fail('Must refuse before serial open')
                module.identified_receiver.assert_not_called()
                self.assertFalse(lease.exists())
            atomic_json(journal,{'state':'pending','id':'a'*32,'gpsd_pid':'123','stopped':False})
            atomic_json(lease,{'id':'a'*32})
            with mock.patch.object(maintenance,'LEASE',lease),mock.patch.object(maintenance,'JOURNAL',journal), \
                 mock.patch.object(maintenance,'command',return_value='123'),mock.patch.object(maintenance,'owners',side_effect=[[],['123']]), \
                 mock.patch.object(maintenance,'control') as control,mock.patch.object(maintenance,'wait'), \
                 mock.patch.object(assist,'read_json',return_value={}):
                # private_json delegates to assist.read_json, so preserve journal.
                with mock.patch.object(acq,'private_json',return_value=json.loads(journal.read_text())):
                    maintenance.recover(locked=True)
                control.assert_called_once_with('+')
                self.assertFalse(lease.exists())

    def test_changed_gpsd_generation_keeps_timing_withdrawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            lease=root/'maintenance.json';atomic_json(lease,{'id':'a'*32})
            with mock.patch.object(maintenance,'LEASE',lease),mock.patch.object(acq,'private_json',return_value={'state':'pending','gpsd_pid':'123'}), \
                 mock.patch.object(maintenance,'command',return_value='456'),mock.patch.object(maintenance,'admission') as admission:
                with self.assertRaises(ValueError):
                    maintenance.recover(locked=True)
                self.assertTrue(lease.exists())
                admission.assert_not_called()

    def test_database_dump_completion_and_bad_counts(self):
        channel=mock.Mock()
        channel.poll.return_value=[(0x13,0x80,bytes(12)),(0x13,0x60,complete())]
        raw,ack=maintenance.collect_database(channel,maintenance.time.monotonic()+1)
        self.assertEqual(raw,dbd())
        self.assertEqual(ack,complete())
        channel.poll.return_value=[(0x13,0x80,bytes(12)),(0x13,0x60,complete(2))]
        with self.assertRaises(ValueError):
            maintenance.collect_database(channel,maintenance.time.monotonic()+1)

    def test_dump_enables_ram_ack_and_restores_after_incomplete_transfer(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(assist,'ROOT',Path(tmp)), \
             mock.patch.object(manager,'read_values',return_value={manager.ACK_AIDING:0}), \
             mock.patch.object(manager,'write_values') as writer, \
             mock.patch.object(maintenance,'collect_database',side_effect=ValueError('Incomplete')):
            with self.assertRaises(ValueError):
                maintenance.dump_database(mock.Mock(),maintenance.time.monotonic()+1,IDENTITY['binding'])
            self.assertEqual([c.args[1] for c in writer.call_args_list],[{manager.ACK_AIDING:1},{manager.ACK_AIDING:0}])
            self.assertEqual(assist.read_json(Path(tmp)/'receiver-transaction.json',{})['state'],'restored')

    def test_restore_ack_exact_match_and_cleanup_on_every_failure(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(assist,'ROOT',Path(tmp)), \
             mock.patch.object(manager,'read_values',return_value={manager.ACK_AIDING:0}), \
             mock.patch.object(manager,'write_values') as writer:
            frame=acq.dbd_frames(dbd())[0]
            for ack in (b'',complete(5),b'\1\0\x04\x80'+frame[6:10],b'\1\0\0\x20'+frame[6:10]):
                with self.assertRaises(ValueError):
                    maintenance.restore_database(mock.Mock(return_value=ack),[frame],IDENTITY['binding'])
                self.assertEqual(writer.call_args.args[1],{manager.ACK_AIDING:0})
            ack=b'\1\0\0\x80'+frame[6:10]
            self.assertEqual(maintenance.restore_database(mock.Mock(return_value=ack),[frame],IDENTITY['binding'])['accepted'],1)

    def test_no_network_during_unassisted_and_no_implicit_live(self):
        with mock.patch.object(acq,'load_policy',return_value=acq.DEFAULT),mock.patch.object(assist,'Downloader') as downloader:
            self.assertEqual(maintenance.prepare('none',IDENTITY,{'valid':False})['frames'],[])
            with self.assertRaises(ValueError):
                maintenance.prepare('live',IDENTITY,{'valid':True},paid=False)
            downloader.assert_not_called()

    def test_fresh_download_revalidates_independent_date_before_cache_selection(self):
        from test_gnss_intelligence import CREDENTIALS
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            cache=assist.Cache(root)
            downloader=assist.Downloader(cache,request=lambda _:prediction(),clock=lambda:NOW+2)
            hint=acq.independent_time(NOW,True,'network',uncertainty=1)
            later=acq.independent_time(NOW+3,True,'network',uncertainty=1)
            with mock.patch.object(acq,'load_policy',return_value=acq.DEFAULT), \
                 mock.patch.object(assist,'ROOT',root),mock.patch.object(assist,'Cache',return_value=cache), \
                 mock.patch.object(assist,'load_config',return_value=assist.DEFAULT|{'allow_metered':True}), \
                 mock.patch.object(assist,'load_credentials',return_value=CREDENTIALS), \
                 mock.patch.object(assist,'Downloader',return_value=downloader), \
                 mock.patch.object(info,'selected_network',return_value={'online':True,'uplink':'existing','metered':False}), \
                 mock.patch.object(info,'save_assist_status'), \
                 mock.patch.object(maintenance,'lock',return_value=contextlib.nullcontext()), \
                 mock.patch.object(maintenance.time,'time',return_value=NOW+1), \
                 mock.patch.object(maintenance,'time_hint',return_value=later):
                result=maintenance.prepare('internet',IDENTITY,hint,'none')
            self.assertEqual(result['source'],'internet')
            self.assertEqual(result['frames'],[prediction()])
            self.assertEqual(assist.read_json(root/'usage.json',{})['predictive_requests'],1)
            # A genuinely pre-download date must remain rejected by the cache.
            self.assertEqual(cache.selected(IDENTITY,NOW)[1],[])

    def test_boot_no_host_and_healthy_timing_do_not_reset(self):
        from test_gnss_intelligence import version,unique
        exchange=mock.Mock()
        raw=bytearray(pvt(valid=False));raw[21]=0
        exchange.return_value=bytes(raw)
        with mock.patch.object(acq,'private_json',return_value={}),mock.patch.object(acq,'load_policy',return_value=acq.DEFAULT|{'boot_policy':'none'}), \
             mock.patch.object(assist,'load_config',return_value=assist.DEFAULT|{'boot_injection':True}):
            result=maintenance.boot_assistance(exchange,version()[6:-2],unique()[6:-2])
            self.assertEqual(result['accepted'],0)
            self.assertTrue(all(call.args[0][2:4]!=b'\6\4' for call in exchange.call_args_list))
            exchange.reset_mock()
            maintenance.boot_assistance(exchange,version()[6:-2],unique()[6:-2],timing_healthy=True)
            exchange.assert_not_called()

    def test_shutdown_disabled_and_failure_never_publishes_partial_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            with mock.patch.object(acq,'ROOT',root),mock.patch.object(acq,'load_policy',return_value=acq.DEFAULT), \
                 mock.patch.object(maintenance,'Owner') as owner:
                maintenance.shutdown_backup()
                owner.assert_not_called()
            with mock.patch.object(acq,'ROOT',root),mock.patch.object(acq,'load_policy',return_value=acq.DEFAULT|{'save_on_shutdown':True}), \
                 mock.patch.object(maintenance,'boot_id',return_value='fixture-boot'), \
                 mock.patch.object(acq,'qualified',return_value=True),mock.patch.object(assist,'read_json',return_value={'identity':IDENTITY}), \
                 mock.patch.object(maintenance,'lock',side_effect=BlockingIOError),mock.patch.object(acq.NavigationStore,'publish') as publish:
                maintenance.shutdown_backup()
                self.assertIn('failed',json.loads((root/'shutdown-result.json').read_text())['state'])
                publish.assert_not_called()


class InterfaceTests(unittest.TestCase):
    def test_five_sections_and_confirmation_screen(self):
        data=fixture()
        data['acquisition']={'policy':acq.DEFAULT,'job':{'state':'Running','phase':'Receiver acquisition','results':[]}}
        for section in panel.GNSS_SECTIONS:
            html=panel.render_gnss_page(data,'csrf-fixture',section=section).decode()
            if section=='advanced':
                self.assertIn('Acquisition Testing',html)
                self.assertIn('http-equiv="refresh"',html)
                self.assertIn('acquisition-review',html)
            else:
                self.assertNotIn('http-equiv="refresh"',html)
            if section=='assistnow':
                self.assertIn('Boot assistance policy',html)
                self.assertIn('navigation-snapshot-review',html)
        html=panel.render_gnss_confirmation({'operation':'acquisition-review','start':'cold','source':'cached','antenna':'<script>unsafe</script>'},'csrf-fixture').decode()
        self.assertIn(acq.CONFIRM,html)
        self.assertIn('value="acquisition-start"',html)
        self.assertNotIn('<script>unsafe',html)
        self.assertIn('timing interruption',html)

    def test_root_boundary_rejects_missing_confirmation_unknown_fields_and_unqualified_automation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            with mock.patch.object(acq,'ROOT',root),mock.patch.object(acq,'CONFIG',root/'policy.json'), \
                 mock.patch.object(acq,'Jobs',side_effect=lambda:acq_jobs(root)), \
                 mock.patch.object(info,'lock',return_value=contextlib.nullcontext()), \
                 mock.patch.object(assist,'read_json',return_value={}),mock.patch.object(info.subprocess,'run') as run:
                for request in ({'operation':'acquisition-start','confirm':'yes'},
                                {'operation':'navigation-snapshot','confirm':acq.CONFIRM,'device':'bad'},
                                {'operation':'boot-policy','confirm':'yes','save_on_shutdown':'yes','power_hold_seconds':'60'},
                                {'operation':'acquisition-export','id':'../../etc/passwd'}):
                    with self.assertRaises(ValueError):
                        info.acquisition_update(request)
                run.assert_not_called()


class WorkerTests(unittest.TestCase):
    def run_worker(self,mode='hot',cancel_phase=None,source='none',injection_failure=False,time_hints=None):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);root.chmod(0o700)
        jobs=acq.Jobs(root)
        job=jobs.queue({'confirm':acq.CONFIRM,'start':mode,'source':source})
        original_save=jobs.save
        def saved(value):
            original_save(value)
            if cancel_phase and cancel_phase in value.get('phase',''):
                jobs.cancel()
        jobs.save=saved
        sent=[]
        class FakeChannel:
            fd=123
            decoder=None
            event=lambda *args:None
            def send(self,frame):
                sent.append(frame)  # CFG-RST deliberately returns no ACK.
            def exchange(self,*args):
                if injection_failure:
                    raise ValueError('Fixture interruption')
                if args[0]==ubx(1,3):
                    self.event(1,3,struct.pack('<IBBBBII',123000,3,1,0,0,1234,1000))
                return b''
            def poll(self,*args):
                self.event(1,3,struct.pack('<IBBBBII',123000,0,0,0,0,0,0))
                payload=pvt();payload[23]=25
                self.event(1,7,payload)
                return []
        channel=FakeChannel()
        @contextlib.contextmanager
        def acquired():
            if jobs.cancelled(job['id']):
                raise maintenance.Cancelled()
            yield channel,IDENTITY
        owner=mock.Mock();owner.acquire=acquired
        paired={'state':'fresh','synchronized':True,'selected_ref':'F10P','utc_valid':True,'pps_active':True,'pps_validation':'paired','pps_selection':'*'}
        aid={'source':source,'frames':[prediction()] if source!='none' else [],'download_seconds':0}
        self.prepared_dates=[]
        def prepared(source,bound,hint,*args):
            self.prepared_dates.append(hint['utc'])
            return aid
        read_json=assist.read_json
        def read(path,*args,**kwargs):
            return {'identity':IDENTITY} if Path(path).name=='receiver-facts.json' else read_json(path,*args,**kwargs)
        with mock.patch.object(acq,'Jobs',return_value=jobs),mock.patch.object(maintenance,'lock',return_value=contextlib.nullcontext()), \
             mock.patch.object(assist,'ROOT',root), \
             mock.patch.object(assist,'read_json',side_effect=read),mock.patch.object(maintenance.signal,'signal'), \
             mock.patch.object(maintenance,'time_hint',side_effect=time_hints or (lambda:acq.independent_time(NOW,True,'network',uncertainty=1))), \
             mock.patch.object(maintenance,'prepare',side_effect=prepared),mock.patch.object(maintenance,'Owner',return_value=owner), \
             mock.patch.object(maintenance,'read_status',return_value=paired),mock.patch.object(maintenance,'pps_sequence',return_value=20), \
             mock.patch.object(maintenance,'recover') as recover, mock.patch.dict(sys.modules,{'termios':mock.Mock()}), \
             mock.patch.object(maintenance,'stopped'),mock.patch.object(maintenance,'started') as restart, \
             mock.patch.object(maintenance,'restore_database',return_value={'state':'Accepted','accepted':1}), \
             mock.patch.object(maintenance,'inject_time',side_effect=ValueError('Fixture injection failure') if injection_failure else None):
            maintenance.worker()
            recover.assert_called()
            if source=='database' and not injection_failure:
                restart.assert_called_once_with(channel)
        return jobs.read(),sent

    def test_worker_rechecks_independent_date_after_download_before_reset(self):
        hints=[acq.independent_time(NOW+offset,True,'network',uncertainty=1) for offset in (0,0,3)]
        with mock.patch.object(manager,'inject',return_value={'state':'Accepted','accepted':1}):
            job,sent=self.run_worker(source='internet',time_hints=hints)
        self.assertEqual(job['state'],'Completed')
        self.assertEqual(self.prepared_dates,[NOW,NOW+3])
        self.assertEqual(sent,[acq.reset_frame('hot')])

    def test_hot_warm_cold_actual_fixture_events_without_reset_ack(self):
        for mode in acq.MASKS:
            with self.subTest(mode=mode):
                job,sent=self.run_worker(mode)
                self.assertEqual(job['state'],'Completed')
                self.assertEqual(sent,[acq.reset_frame(mode)])
                self.assertEqual(job['results'][0]['metrics']['receiver_ttff_seconds'],1.234)
                self.assertEqual(job['results'][0]['first_fix']['satellites_used'],25)
                self.assertIn('not proven fully unassisted',job['results'][0]['residual_data'])

    def test_cancellation_in_each_externally_visible_phase_recovers(self):
        for phase in ('Preparing trial','exclusive handover','receiver acquisition','UTC/PPS and F10P recovery'):
            with self.subTest(phase=phase):
                job,_=self.run_worker(cancel_phase=phase)
                self.assertEqual(job['state'],'Cancelled')
                self.assertTrue(all(r['state']!='Completed' for r in job['results']))

    def test_injection_failure_preserves_failure_and_runs_cleanup(self):
        job,sent=self.run_worker(source='cached',injection_failure=True)
        self.assertEqual(job['state'],'Failed')
        self.assertEqual(job['results'][0]['accepted'],0)
        self.assertEqual(sent,[acq.reset_frame('hot')])
        self.assertIsNotNone(job['results'][0]['injection_seconds'])

    def test_database_assisted_trial_restarts_navigation_before_measuring(self):
        job,sent=self.run_worker(source='database')
        self.assertEqual(job['state'],'Completed')
        self.assertEqual(sent,[acq.reset_frame('hot')])


acq_jobs=acq.Jobs

if __name__=='__main__':
    unittest.main()
