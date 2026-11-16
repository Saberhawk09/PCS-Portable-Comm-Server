"""Real local GPSD/Chrony protocols, synthetic inputs, no hardware/clock writes."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_gnss as gnss
import pcs_gnss_runtime as runtime

LINUX = sys.platform.startswith('linux')


def wait_until(test, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = test()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError('Local protocol fixture did not become ready')


@unittest.skipUnless(LINUX and shutil.which('chronyd') and shutil.which('chronyc'), 'Linux Chrony integration tools required')
class ChronyProtocolTests(unittest.TestCase):
    def test_sock_pairing_rejection_and_reference_withdrawal(self):
        # -x prevents clock control; port 0 disables NTP serving. All control
        # and refclock sockets are private to this temporary directory.
        with tempfile.TemporaryDirectory(prefix='pcs-gnss-') as temp:
            root = Path(temp)
            clock, pulse, control = (str(root / name) for name in ('clock.sock', 'pulse.sock', 'control.sock'))
            config = root / 'chrony.conf'
            config.write_text(f'port 0\ncmdport 0\nbindcmdaddress {control}\npidfile {root}/pid\n'
                              f'user {"root" if os.geteuid() == 0 else __import__("pwd").getpwuid(os.geteuid()).pw_name}\n'
                              f'refclock SOCK {clock} refid F10N poll 0 filter 1 delay 0.2 precision 1e-1 prefer noselect\n'
                              f'refclock SOCK {pulse} refid F10P lock F10N maxlockage 2 poll 0 filter 1 prefer noselect\n')
            log = (root / 'log').open('w+')
            # Use the exact installed binary outside its distribution AppArmor
            # attachment path, which forbids fixture configs under /tmp.
            binary = root / 'chronyd'
            shutil.copyfile(shutil.which('chronyd'), binary)
            binary.chmod(0o755)
            process = subprocess.Popen([str(binary), '-U', '-n', '-x', '-f', str(config)], stdout=log, stderr=subprocess.STDOUT)
            def command(args):
                result = subprocess.run(['chronyc', '-h', control] + args[1:], capture_output=True, text=True, timeout=3)
                return result.returncode, result.stdout
            try:
                try:
                    wait_until(lambda: Path(clock).exists() and Path(pulse).exists() and Path(control).exists())
                except AssertionError:
                    log.flush()
                    self.fail((root / 'log').read_text())
                selection = runtime.Selection(command)
                selection.withdraw()
                _, sources = command(['chronyc', '-n', 'sources'])
                self.assertIn('F10P', sources)
                self.assertNotIn('#*', sources)
                selection.set('F10N', True)
                selection.set('F10P', True)
                for _ in range(9):
                    stamp = time.time()
                    runtime.send_sample((stamp, 0.0), clock)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                        sender.sendto(struct.pack('@lldiiii', int(stamp), 0, 0.0, 1, 0, 0, 0x534F434B), pulse)
                    time.sleep(1)
                _, sources = command(['chronyc', '-n', 'sources'])
                # Both have real measurements; the pulse reference must have
                # been completed by its named serial reference, not network time.
                rows = {line.split()[1]: line.split() for line in sources.splitlines()
                        if line.startswith('#') and len(line.split()) > 2}
                self.assertGreater(int(rows['F10P'][4], 8), 0)
                self.assertGreater(int(rows['F10N'][4], 8), 0)
                self.assertEqual(rows['F10P'][0], '#*', sources)
                selection.withdraw()
                _, selected = command(['chronyc', '-n', 'sources'])
                self.assertNotIn('#*', selected)
            finally:
                process.terminate()
                process.wait(timeout=5)
                log.close()


@unittest.skipUnless(LINUX and shutil.which('gpsd'), 'Linux GPSD integration tool required')
class GPSDProtocolTests(unittest.TestCase):
    def test_device_scoped_raw_ubx_and_original_toff(self):
        import pty
        master, slave = pty.openpty()
        device = os.ttyname(slave)
        with tempfile.TemporaryDirectory(prefix='gn-') as temp:
            os.chmod(temp, 0o755)
            alias = Path(temp) / 'receiver'
            alias.symlink_to(device)
            device = str(alias)
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1', 0))
                port = reserve.getsockname()[1]
            log = (Path(temp) / 'log').open('w+')
            binary = Path(temp) / 'gpsd'
            shutil.copyfile(shutil.which('gpsd'), binary)
            binary.chmod(0o755)
            process = subprocess.Popen([str(binary), '-N', '-n', '-b', '-S', str(port), '-F', str(Path(temp) / 'control'), device],
                                       stdout=log, stderr=subprocess.STDOUT)
            connections = []
            try:
                def open_connection():
                    try:
                        return socket.create_connection(('127.0.0.1', port), timeout=0.3)
                    except OSError:
                        return None
                for raw in (False, True):
                    connection = wait_until(open_connection)
                    connections.append(connection)
                    watch = {'enable': True, 'device': device}
                    watch.update({'raw': 2} if raw else {'json': True, 'pps': True})
                    connection.sendall(('?WATCH=' + json.dumps(watch, separators=(',', ':')) + ';\n').encode())
                    connection.settimeout(0.2)
                decoder, buffer, seen_raw, seen_toff = gnss.UBXStream(), b'', False, False
                activity = gnss.MessageActivity()
                deadline = time.monotonic() + 12
                sequence = 0
                while time.monotonic() < deadline and not (seen_raw and seen_toff and len(activity.seen) == 3):
                    epoch = int(time.time())
                    stamp = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
                    payload = bytearray(92)
                    struct.pack_into('<IHBBBBBBIi', payload, 0, 100000 + sequence * 1000, stamp.year, stamp.month,
                                     stamp.day, stamp.hour, stamp.minute, stamp.second, 7, 30, 0)
                    payload[20:24] = bytes((3, 1, 0, 8))
                    # Mixed NMEA + UBX as on the operator profile. Diagnostic
                    # contents are synthetic, never receiver acceptance evidence.
                    sig = bytes((0, 0, 0, 0, 0, 1, 0, 0)) + bytes(16)
                    os.write(master, b'$GPTXT,01,01,02,fixture*2C\r\n' + gnss.ubx(1, 7, payload) +
                             gnss.ubx(1, 0x43, sig) + gnss.ubx(1, 0x21, bytes(20)) + gnss.ubx(13, 1, bytes(16)))
                    sequence += 1
                    time.sleep(0.2)
                    try:
                        for cls, msg, value in decoder.feed(connections[1].recv(65536)):
                            activity.update(cls, msg, value)
                            seen_raw |= (cls, msg) == (1, 7) and len(value) == 92
                    except socket.timeout:
                        pass
                    try:
                        buffer += connections[0].recv(65536)
                    except socket.timeout:
                        pass
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        try:
                            report = json.loads(line)
                        except ValueError:
                            continue
                        if report.get('class') == 'TOFF':
                            self.assertEqual(report['device'], device)
                            self.assertLess(abs(report['clock_sec'] - time.time()), 3)
                            seen_toff = True
                self.assertTrue(seen_raw, 'GPSD must preserve receiver UBX via device-scoped WATCH raw=2')
                self.assertTrue(seen_toff, 'GPSD must publish original serial timing with WATCH pps=true')
                self.assertEqual(set(activity.seen), {'signal', 'timeutc', 'tim_tp'})
                self.assertEqual(activity.signals, 1)
            finally:
                for connection in connections:
                    connection.close()
                process.terminate()
                process.wait(timeout=5)
                os.close(master)
                os.close(slave)
                log.close()


if __name__ == '__main__':
    unittest.main()
