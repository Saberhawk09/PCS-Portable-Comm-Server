"""Actual spawned worker/UDP/IPC, loopback substituted only for physical LAN binding."""
import copy
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_qualify_lan as lan


def fixture_worker(channel, session, server, client):
    original = lan._SocketReceiver.__init__
    def loopback(self, session, server, client):
        self.receipts = lan.Receipts(session)
        self.finished = False
        self.client = client
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((server, lan.PORT))
        self.socket.settimeout(.1)
    lan._SocketReceiver.__init__ = loopback
    try:
        lan._receiver_worker(channel, session, server, client)
    finally:
        lan._SocketReceiver.__init__ = original


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux receipt worker')
class ReceiverProcess(unittest.TestCase):
    def setUp(self):
        self.session = 'a' * 32
        with patch.object(lan, '_receiver_worker', fixture_worker):
            self.receiver = lan.Receiver(self.session, '127.0.0.1', '127.0.0.1')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.connect(('127.0.0.1', lan.PORT))
        self.sock.settimeout(2)  # Same unchanged client deadline.

    def tearDown(self):
        self.sock.close()
        try:
            self.receiver.close()
        except ValueError:
            pass

    def exchange(self, seq, phase, ok=True):
        packet = dict(version=1, session=self.session, seq=seq, phase=phase)
        if phase == 'after': packet['ok'] = ok
        started = time.monotonic()
        self.sock.send(json.dumps(packet).encode())
        reply = json.loads(self.sock.recv(1025))
        self.assertLess(time.monotonic() - started, 2)
        return reply

    def test_slow_parent_does_not_delay_ack_and_close_preserves_unpolled_receipts(self):
        # No parent Receiver.poll while collectors would be running.
        for seq in range(4):
            self.exchange(seq, 'before')
            time.sleep(.8)
            self.exchange(seq, 'after')
        self.assertEqual(self.receiver.receipts.samples, [])
        pid = self.receiver.process.pid
        self.receiver.close()
        self.assertEqual(len(self.receiver.receipts.samples), 4)
        self.assertFalse(Path('/proc', str(pid)).exists())
        self.receiver.close()  # Idempotent.

    def test_finish_waits_for_actual_done_ack(self):
        self.exchange(0, 'before')
        self.exchange(0, 'after')
        self.receiver.finish(time.monotonic())
        self.assertFalse(self.receiver.finished)
        self.assertFalse(self.exchange(1, 'before')['done'])
        self.assertTrue(self.exchange(1, 'after')['done'])
        self.receiver.poll()
        self.assertTrue(self.receiver.finished)
        self.assertEqual(len(self.receiver.receipts.samples), 2)

    def test_wrong_source_wrong_session_and_oversize_never_make_samples(self):
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            other.bind(('127.0.0.2', 0))
            other.sendto(json.dumps(dict(version=1, session=self.session, seq=0, phase='before')).encode(),
                         ('127.0.0.1', lan.PORT))
        finally:
            other.close()
        self.sock.send(b'x' * 1025)
        self.sock.send(json.dumps(dict(version=1, session='b'*32, seq=0, phase='before')).encode())
        self.receiver.poll()
        self.assertEqual(self.receiver.receipts.samples, [])
        self.assertFalse(self.receiver.receipts.invalid)
        self.exchange(0, 'before')
        self.exchange(0, 'after')
        self.receiver.poll()
        self.assertEqual(len(self.receiver.receipts.samples), 1)

    def test_malformed_session_packet_is_sticky_invalid(self):
        self.sock.send(json.dumps(dict(version=1, session=self.session, seq=True, phase='before')).encode())
        self.receiver.poll()
        self.assertTrue(self.receiver.receipts.invalid)
        self.exchange(0, 'before')
        self.exchange(0, 'after')
        self.receiver.poll()
        self.assertTrue(self.receiver.receipts.invalid)

    def test_worker_death_fails_closed_and_releases_port(self):
        self.receiver.process.kill()
        self.receiver.process.join(2)
        with self.assertRaises(ValueError): self.receiver.poll()
        with self.assertRaises(ValueError): self.receiver.close()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', lan.PORT))

    def test_parent_ipc_disappearance_releases_worker_and_port(self):
        self.receiver.channel.close()  # Equivalent to kernel closing a dead parent's IPC.
        self.receiver.process.join(3)
        self.assertFalse(self.receiver.process.is_alive())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', lan.PORT))

    def test_stopped_worker_times_out_without_hanging_ipc(self):
        os.kill(self.receiver.process.pid, signal.SIGSTOP)
        started = time.monotonic()
        with self.assertRaises(ValueError): self.receiver.poll()
        self.assertLess(time.monotonic() - started, 3)
        os.kill(self.receiver.process.pid, signal.SIGCONT)

    def test_private_snapshot_cannot_rewrite_history_or_forge_finish(self):
        self.exchange(0, 'before')
        self.exchange(0, 'after')
        self.receiver.poll()
        value = dict(session=self.session, samples=copy.deepcopy(self.receiver.receipts.samples),
                     invalid=False, finish_after=None, finished=False)
        for key, bad in (('samples', []), ('session', 'b'*32), ('finished', True), ('invalid', 1)):
            changed = copy.deepcopy(value)
            changed[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): self.receiver._update(changed)


if __name__ == '__main__': unittest.main()
