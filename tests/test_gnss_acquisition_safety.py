"""Qualification and failure-reporting fixtures; no hardware acceptance claims."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pcs_gnss_acquisition as acq
import pcs_gnss_maintenance as maintenance
import pcs_gnss_manager as manager
import pcs_assistnow as assist
from pcs_gnss import atomic_json
from test_gnss_acquisition import dbd
from test_gnss_intelligence import IDENTITY,NOW,version,unique
from test_gnss_corrections import pvt,fixture,panel


class AcquisitionSafetyTests(unittest.TestCase):
    @contextlib.contextmanager
    def boot_context(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);root.chmod(0o700)
            for target,name,value in ((acq,'ROOT',root),(assist,'ROOT',root),
                    (acq,'load_policy',acq.DEFAULT|{'boot_policy':'database'}),
                    (assist,'load_config',assist.DEFAULT|{'boot_injection':True}),
                    (maintenance,'time_hint',acq.independent_time(NOW,True,'network',uncertainty=1))):
                stack.enter_context(mock.patch.object(target,name,return_value=value) if callable(getattr(target,name)) else mock.patch.object(target,name,value))
            payload=bytearray(pvt(valid=False));payload[21]=0
            exchange=mock.Mock(return_value=bytes(payload))
            yield root,exchange

    def boot(self,exchange):
        return maintenance.boot_assistance(exchange,version()[6:-2],unique()[6:-2])

    def test_unqualified_database_sends_no_time_or_orbit_aid(self):
        with self.boot_context() as (_,exchange), \
             mock.patch.object(maintenance,'prepare',return_value={'source':'database','frames':acq.dbd_frames(dbd())}), \
             mock.patch.object(acq,'qualified',return_value=False),mock.patch.object(maintenance,'inject_time') as time_aid:
            result=self.boot(exchange)
            self.assertEqual(result['state'],'Database restore not hardware-qualified')
            time_aid.assert_not_called()
            self.assertTrue(all(c.args[0][2:4]==b'\1\7' for c in exchange.call_args_list))

    def test_unavailable_database_falls_back_without_receiver_writes(self):
        with self.boot_context() as (_,exchange), \
             mock.patch.object(maintenance,'prepare',side_effect=assist.AssistanceError('Private cache error must not be exported')), \
             mock.patch.object(maintenance,'inject_time') as time_aid:
            result=self.boot(exchange)
            self.assertEqual(result['accepted'],0)
            self.assertIn('Ordinary acquisition',result['state'])
            self.assertNotIn('Private',json.dumps(result))
            time_aid.assert_not_called()

    def test_interrupted_boot_identity_mismatch_still_fails_closed(self):
        with self.boot_context() as (root,exchange):
            atomic_json(root/'boot-restore.json',{'state':'stopped','identity':{'binding':'another-receiver'}})
            with self.assertRaisesRegex(assist.AssistanceError,'another receiver'):
                self.boot(exchange)
            exchange.assert_not_called()

    def test_export_ignores_malformed_database_evidence(self):
        for evidence in ('private-invalid',[],42):
            with self.subTest(evidence=type(evidence).__name__):
                value=json.loads(acq.result_export({'database_evidence':evidence}))
                self.assertEqual(value['database_evidence'],{})

    def restore(self,exchange):
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(assist,'ROOT',Path(tmp)), \
             mock.patch.object(manager,'read_values',return_value={manager.ACK_AIDING:0}), \
             mock.patch.object(manager,'write_values') as writer:
            with self.assertRaises(assist.AssistanceError) as error:
                maintenance.restore_database(exchange,acq.dbd_frames(dbd(2)),IDENTITY['binding'])
            self.assertEqual(writer.call_args.args[1],{manager.ACK_AIDING:0})
            return error.exception

    def test_unknown_database_ack_is_rejected_with_exact_partial_counts(self):
        frames=acq.dbd_frames(dbd(2))
        exchange=mock.Mock(side_effect=[b'\1\0\0\x80'+frames[0][6:10],b'\0\0\xff\x80'+frames[1][6:10]])
        error=self.restore(exchange)
        self.assertEqual(error.code,'database-rejected')
        self.assertEqual(error.evidence,{'frame_count':2,'offered':2,'accepted':1,'rejected':1,'unacknowledged':0,
                                         'failed_frame':2,'receiver_code':255,'complete':False})
        self.assertEqual(exchange.call_count,2)  # Unknown codes must not trigger retries.

    def test_database_timeout_never_serializes_exception_details(self):
        error=self.restore(mock.Mock(side_effect=assist.AssistanceError('https://private.invalid/?credential=sentinel')))
        self.assertEqual(error.code,'database-no-ack')
        self.assertEqual(error.evidence['unacknowledged'],1)
        self.assertEqual(error.evidence['accepted'],0)
        self.assertNotIn('sentinel',str(error))
        self.assertNotIn('private.invalid',str(error))

    def test_redacted_database_failure_keeps_numeric_evidence_only(self):
        job={'kind':'restore','failure_code':'database-rejected','failure_phase':'Receiver navigation maintenance',
             'ownership_recovered':True,'timing_recovered':False,
             'database_evidence':{'identity':IDENTITY,'sha256':'private','raw':'private','frame_count':159,
                                  'offered':53,'accepted':52,'rejected':1,'unacknowledged':0,'failed_frame':53,
                                  'receiver_code':255,'complete':False}}
        value=json.loads(acq.result_export(job))
        self.assertEqual(value['database_evidence']['accepted'],52)
        self.assertEqual(value['database_evidence']['receiver_code'],255)
        self.assertFalse(value['timing_recovered'])
        self.assertTrue(value['ownership_recovered'])
        self.assertNotIn('private',json.dumps(value))
        self.assertNotIn('identity',value['database_evidence'])

    def test_qualification_hold_is_visible_without_private_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            atomic_json(root/'qualification.json',{'identity':IDENTITY,'database_restore':False,'shutdown':False,
                       'requalification_required':True,'qualification_hold':'private-reason-sentinel'})
            value=acq.public_status(root=root,config=acq.DEFAULT,identity=IDENTITY)
            self.assertTrue(value['qualification_hold'])
            self.assertNotIn('private-reason-sentinel',json.dumps(value))
            data=fixture();data['acquisition']=value
            html=panel.render_gnss_page(data,'csrf-fixture',section='assistnow').decode()
            self.assertIn('on hold',html)

    def test_advanced_page_explains_partial_database_failure(self):
        data=fixture();data['acquisition']={'job':{'kind':'restore','state':'Failed',
            'failure':'Database entry rejected by receiver','failure_phase':'Receiver navigation maintenance',
            'ownership_recovered':True,'timing_recovered':False,
            'database_evidence':{'frame_count':159,'accepted':52,'rejected':1,'offered':53,
                                 'unacknowledged':0,'failed_frame':53,'receiver_code':255,'complete':False}}}
        html=panel.render_gnss_page(data,'csrf-fixture',section='advanced').decode()
        self.assertIn('Database entry rejected by receiver',html)
        self.assertIn('Receiver status code',html)
        self.assertIn('255',html)

    def test_database_maintenance_csv_and_text_include_failure_and_recovery(self):
        job={'kind':'restore','state':'Failed','failure_code':'database-rejected','failure':'Database entry rejected by receiver',
             'failure_phase':'Receiver navigation maintenance','ownership_recovered':True,'timing_recovered':False,
             'database_evidence':{'frame_count':159,'offered':53,'accepted':52,'rejected':1,'unacknowledged':0,'failed_frame':53,'receiver_code':255,'complete':False}}
        rows=list(csv.DictReader(io.StringIO(acq.result_export(job,'csv'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['database_accepted'],'52')
        self.assertEqual(rows[0]['receiver_code'],'255')
        self.assertEqual(rows[0]['ownership_recovered'],'True')
        self.assertEqual(rows[0]['timing_recovered'],'False')
        text=acq.result_export(job,'text')
        self.assertIn('Database entry rejected by receiver',text)
        self.assertIn('"receiver_code": 255',text)

    def test_cancelled_database_transfer_keeps_cancellation_and_restores_settings(self):
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(assist,'ROOT',Path(tmp)), \
             mock.patch.object(manager,'read_values',return_value={manager.ACK_AIDING:0}), \
             mock.patch.object(manager,'write_values') as writer:
            with self.assertRaises(maintenance.Cancelled):
                maintenance.restore_database(mock.Mock(side_effect=maintenance.Cancelled()),acq.dbd_frames(dbd()),IDENTITY['binding'])
            self.assertEqual(writer.call_args.args[1],{manager.ACK_AIDING:0})

    def test_worker_keeps_failure_phase_partial_ack_evidence_and_distinct_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o700)
            jobs=acq.Jobs(root);jobs.queue({'confirm':acq.CONFIRM},kind='restore')
            channel=mock.Mock();channel.exchange.return_value=bytes(16)
            owner=mock.Mock();owner.acquire.return_value=contextlib.nullcontext((channel,IDENTITY))
            evidence={'frame_count':2,'accepted':1,'rejected':1,'offered':2,'unacknowledged':0,'failed_frame':2,'receiver_code':255,'complete':False}
            error=maintenance.DatabaseTransferError('database-rejected',evidence)
            read=assist.read_json
            def read_facts(path,*args,**kwargs):
                return {'identity':IDENTITY} if Path(path).name=='receiver-facts.json' else read(path,*args,**kwargs)
            with mock.patch.object(acq,'Jobs',return_value=jobs),mock.patch.object(maintenance,'lock',return_value=contextlib.nullcontext()), \
                 mock.patch.object(assist,'ROOT',root),mock.patch.object(assist,'read_json',side_effect=read_facts), \
                 mock.patch.object(maintenance.signal,'signal'),mock.patch.object(maintenance,'Owner',return_value=owner), \
                 mock.patch.object(maintenance,'time_hint',return_value=acq.independent_time(NOW,True,'network',uncertainty=1)), \
                 mock.patch.object(acq.NavigationStore,'read',return_value=(acq.dbd_frames(dbd(2)),{})), \
                 mock.patch.object(maintenance,'stopped'),mock.patch.object(maintenance,'restore_database',side_effect=error), \
                 mock.patch.object(maintenance,'recover'),mock.patch.object(maintenance,'paired_timing',return_value=False):
                maintenance.worker()
            job=jobs.read()
            self.assertEqual(job['state'],'Failed')
            self.assertEqual(job['failure_phase'],'Receiver navigation maintenance')
            self.assertEqual(job['database_evidence'],evidence)
            self.assertTrue(job['ownership_recovered'])
            self.assertFalse(job['timing_recovered'])
