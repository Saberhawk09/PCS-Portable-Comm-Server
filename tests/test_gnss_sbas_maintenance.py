import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
spec=importlib.util.spec_from_file_location('profile',Path(__file__).resolve().parents[1]/'scripts/qualify-gnss-sbas.py')
profile=importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
from pcs_gnss import decode_values, key_size, ubx

class Receiver:
    def __init__(self):
        self.layers={0:{k:0 for k in profile.READ_KEYS},1:{},2:{}}
        self.layers[0][0x40520001]=115200
        self.layers[0][0x30230002]=100
        self.layers[1][next(iter(profile.DESIRED))]=0
        # Absent flash override exercises VALDEL on failed persistent writes.
        self.writes=[]
        self.bad_readback=False
    def exchange(self, frame, cls, msg, ack=None):
        p=frame[6:-2]
        if frame[3]==0x8b:
            layer=p[1]
            keys=[int.from_bytes(p[i:i+4],'little') for i in range(4,len(p),4)]
            data=bytes((1,layer,0,0))
            for k in keys:
                if k in self.layers[layer]:
                    data+=k.to_bytes(4,'little')+self.layers[layer][k].to_bytes(key_size(k),'little')
            if self.bad_readback:
                self.bad_readback=False
                return bytes((1,7,0,0))+data[4:]
            return data
        self.writes.append(frame)
        if frame[3]==0x8a:
            values=decode_values(bytes((1,0,0,0))+p[4:])
            for layer in (0,1,2):
                if p[1] & (1<<layer): self.layers[layer].update(values)
        elif frame[3]==0x8c:
            for layer in (1,2):
                if p[1] & (1<<layer):
                    for i in range(4,len(p),4): self.layers[layer].pop(int.from_bytes(p[i:i+4],'little'),None)
        else: raise AssertionError('Unexpected receiver operation')
        return frame[2:4]

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=profile.ROOT
        profile.ROOT=Path(self.temp.name)
        self.r=Receiver()
    def tearDown(self):
        profile.ROOT=self.old
        self.temp.cleanup()
    def test_only_documented_sbas_rate_is_writable(self):
        self.assertEqual(profile.DESIRED, {0x2091006b: 1})
        self.assertTrue({0x10230001, 0x1041000d, 0x30230002}.issubset(profile.READ_KEYS))
    def test_ram_preserves_flash_and_timing(self):
        before=profile.snapshot(self.r.exchange)
        profile.transaction(self.r.exchange,before,'a'*64,False)
        after=profile.snapshot(self.r.exchange)
        self.assertEqual(before['1'],after['1'])
        self.assertEqual(before['2'],after['2'])
        profile.verify_protected(before,after)
        self.assertEqual(self.r.layers[0][0x40520001],115200)
    def test_persistent_exact_keys_only(self):
        before=profile.snapshot(self.r.exchange)
        profile.transaction(self.r.exchange,before,'a'*64,True)
        for layer in (0,1,2):
            self.assertTrue(all(self.r.layers[layer][k]==1 for k in profile.DESIRED))
        self.assertEqual(len(self.r.writes),1)
        self.assertEqual(self.r.writes[0][7],7)
    def test_bad_readback_restores_sparse_layers_and_ram(self):
        before=profile.snapshot(self.r.exchange)
        real=self.r.exchange
        injected=[False]
        def exchange(*args):
            if args[0][3]==0x8a and not injected[0]:
                injected[0]=True
                result=real(*args)
                self.r.bad_readback=True
                return result
            return real(*args)
        with self.assertRaises(ValueError): profile.transaction(exchange,before,'a'*64,True)
        self.assertEqual(before,profile.snapshot(real))
        self.assertTrue(any(f[3]==0x8c for f in self.r.writes))
        self.assertEqual(json.loads((profile.ROOT/'profile-journal.json').read_text())['state'],'restored')
    def test_interrupted_journal_can_restore_exact_original_layers(self):
        before=profile.snapshot(self.r.exchange)
        journal={'keys':list(map(str,profile.DESIRED)),'before':before,'layers':[0,1,2]}
        self.r.exchange(ubx(6,0x8a,bytes((0,7,0,0))+b''.join(k.to_bytes(4,'little')+b'\x01' for k in profile.DESIRED)),5,1)
        profile.restore(self.r.exchange,journal)
        self.assertEqual(before,profile.snapshot(self.r.exchange))
    def test_restore_refuses_timing_key(self):
        with self.assertRaises(ValueError): profile.restore(self.r.exchange,{'keys':[str(0x40520001)],'layers':[0],'before':{'0':{}}})
        self.assertFalse(self.r.writes)
    def test_wrong_layer_rejected(self):
        self.r.bad_readback=True
        with self.assertRaises(ValueError): profile.read(self.r.exchange,profile.READ_KEYS)
    def test_missing_ram_rejected(self):
        self.r.layers[0].pop(profile.READ_KEYS[0])
        with self.assertRaises(ValueError): profile.snapshot(self.r.exchange)
    def test_no_duplicate_flash_writes(self):
        profile.transaction(self.r.exchange,profile.snapshot(self.r.exchange),'a'*64,True)
        self.r.writes.clear()
        result=profile.transaction(self.r.exchange,profile.snapshot(self.r.exchange),'a'*64,True)
        self.assertEqual(result['changed'],0)
        self.assertFalse(self.r.writes)
    def test_protected_timing_change_detected(self):
        before=profile.snapshot(self.r.exchange)
        self.r.layers[0][0x40520001]=38400
        with self.assertRaises(ValueError): profile.verify_protected(before,profile.snapshot(self.r.exchange))

if __name__=='__main__': unittest.main(verbosity=2)
