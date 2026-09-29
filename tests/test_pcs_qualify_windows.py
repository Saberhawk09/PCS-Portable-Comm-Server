"""Windows ABI/policy tests run on both OSes; real Winsock tests run on Windows."""
import ctypes
import http.server
import ipaddress
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pcs_qualify_windows as windows
import pcs_qualify_lan as lan


class WindowsPolicy(unittest.TestCase):
    def setUp(self):
        nic, address, policy, route, best = (windows.Interface(), windows.Unicast(),
                                            windows.IPInterface(), windows.Route(), windows.Address.ipv4('10.42.0.20'))
        for row in (nic, address, policy, route):
            row.index, row.luid = 7, 123
        nic.type, nic.flags, nic.oper, nic.admin, nic.connected = 6, 5, 1, 1, 1
        nic.mac_length, nic.mac[0], nic.guid[0] = 6, 2, 1
        policy.family, policy.connected = socket.AF_INET, 1
        address.address = windows.Address.ipv4('10.42.0.20')
        address.dad, address.prefix_length, address.valid, address.preferred = 4, 24, 500, 500
        route.prefix.address, route.prefix.length = windows.Address.ipv4('10.42.0.0'), 24
        route.next_hop, route.valid = windows.Address.ipv4('0.0.0.0'), 500
        self.rows = (nic, address, policy, route, best)

    def check(self):
        with patch.object(windows, 'inspect', return_value=self.rows):
            return windows.client_route('10.42.0.1', '10.42.0.20', '7')

    def test_sdk_abi_sizes_and_offsets(self):
        for row, size in ((windows.Address, 28), (windows.Prefix, 32), (windows.Route, 104),
                          (windows.Unicast, 80), (windows.Interface, 1352), (windows.IPInterface, 168)):
            self.assertEqual(ctypes.sizeof(row), size)
        self.assertEqual(windows.Interface.flags.offset, 1152)
        self.assertEqual(windows.IPInterface.weak_receive.offset, 43)
        self.assertEqual(windows.Route.next_hop.offset, 44)

    def test_physical_ethernet_and_wifi_pass(self):
        self.assertEqual(self.check()[0], 7)
        self.rows[0].type = 71
        self.assertEqual(self.check()[0], 7)

    def test_virtual_tunnel_down_or_ambiguous_adapter_blocks(self):
        for field, value in [('type', 131), ('tunnel', 1), ('flags', 0), ('flags', 7),
                             ('oper', 2), ('admin', 2), ('connected', 2), ('index', 8),
                             ('luid', 0), ('mac_length', 0)]:
            with self.subTest(field=field):
                old = getattr(self.rows[0], field)
                setattr(self.rows[0], field, value)
                with self.assertRaises(ValueError): self.check()
                setattr(self.rows[0], field, old)

    def test_unassigned_tentative_deprecated_wrong_source_blocks(self):
        for field, value in [('dad', 1), ('skip_source', 1), ('valid', 0), ('preferred', 0),
                             ('prefix_length', 0), ('prefix_length', 32), ('index', 9), ('luid', 456)]:
            with self.subTest(field=field):
                old = getattr(self.rows[1], field)
                setattr(self.rows[1], field, value)
                with self.assertRaises(ValueError): self.check()
                setattr(self.rows[1], field, old)
        self.rows[1].address = windows.Address.ipv4('10.42.0.21')
        with self.assertRaises(ValueError): self.check()

    def test_weak_host_forwarding_or_disconnected_policy_blocks(self):
        for field, value in [('weak_send', 1), ('weak_receive', 1), ('forwarding', 1), ('connected', 0)]:
            with self.subTest(field=field):
                old = getattr(self.rows[2], field)
                setattr(self.rows[2], field, value)
                with self.assertRaises(ValueError): self.check()
                setattr(self.rows[2], field, old)

    def test_gateway_wrong_interface_default_host_and_loopback_routes_block(self):
        for field, value in [('index', 8), ('luid', 5), ('loopback', 1), ('valid', 0)]:
            old = getattr(self.rows[3], field)
            setattr(self.rows[3], field, value)
            with self.assertRaises(ValueError): self.check()
            setattr(self.rows[3], field, old)
        for prefix in (0, 32):
            self.rows[3].prefix.length = prefix
            with self.assertRaises(ValueError): self.check()
        self.rows[3].prefix.length = 24
        self.rows[3].next_hop = windows.Address.ipv4('10.42.0.254')
        with self.assertRaises(ValueError): self.check()

    def test_best_source_ipv6_or_off_subnet_blocks(self):
        self.rows[4].family = 23
        with self.assertRaises(ValueError): self.check()
        self.rows[4].family = socket.AF_INET
        self.rows[4].ip[:] = ipaddress.IPv4Address('10.42.0.99').packed
        with self.assertRaises(ValueError): self.check()

    def test_identity_detects_interface_replacement(self):
        previous = self.check()
        self.rows[0].guid[0] = 2
        self.assertNotEqual(self.check(), previous)

    def test_numeric_index_only_no_names_or_commands(self):
        for value in ('0', '-1', 'Ethernet', '7; command', '07', '16777216', '', 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows.interface_index(value)

    def test_local_multicast_invalid_and_linklocal_addresses_block(self):
        for value in ('127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.1', '10.42.0.20', '::1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows.client_route(value, '10.42.0.20', '7')

    def test_api_errors_fail_closed(self):
        with patch.object(windows, 'inspect', side_effect=OSError('private diagnostic')):
            with self.assertRaises(OSError): windows.client_route('10.42.0.1', '10.42.0.20', '7')

    def test_pin_uses_network_order_outbound_host_order_receive_list(self):
        sock = Mock()
        sock.getsockopt.side_effect = [7, 1, struct.pack('=I', 7)]
        windows.pin_socket(sock, '7', socket.SOCK_DGRAM)
        self.assertEqual(sock.setsockopt.call_args_list[0].args, (socket.IPPROTO_IP, 31, b'\0\0\0\7'))
        self.assertEqual(sock.setsockopt.call_args_list[-1].args, (socket.IPPROTO_IP, 29, 7))

    def test_failed_pin_readback_never_falls_back(self):
        for values in ([0], [7, 0], [7, 1, struct.pack('=I', 8)]):
            sock = Mock()
            sock.getsockopt.side_effect = values
            with self.assertRaises(ValueError): windows.pin_socket(sock, '7', socket.SOCK_DGRAM)

    def test_socket_closed_if_platform_pin_fails(self):
        sock = Mock()
        with patch.object(lan.sys, 'platform', 'win32'), patch.object(lan.socket, 'socket', return_value=sock), \
                patch.object(windows, 'pin_socket', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError): lan.bound_socket('10.42.0.20', '7', socket.SOCK_DGRAM)
        sock.close.assert_called_once()
        sock.bind.assert_not_called()


@unittest.skipUnless(sys.platform == 'win32', 'native Windows Winsock integration')
class NativeWindowsTransport(unittest.TestCase):
    def exercise(self, failure=None):
        # Local-only transport fixture; admission deliberately mocked because the
        # production gate correctly refuses loopback/self targets. No PCS contact.
        index = windows.inspect('127.0.0.1', '127.0.0.1', 1)[3].index
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503 if failure == 'http' else 200)
                self.end_headers()
                self.wfile.write(b'PRIVATE_HTTP_BODY')
            def log_message(self, *_args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        http_thread = threading.Thread(target=server.serve_forever, daemon=True)
        http_thread.start()
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.1)
        receipts = lan.Receipts('a' * 32)
        receipts.finish_after = time.monotonic() + .3
        stopped = threading.Event()
        def serve():
            while not stopped.is_set():
                try: packet, peer = receiver.recvfrom(1024)
                except socket.timeout: continue
                reply = receipts.receive(json.loads(packet), time.monotonic())
                if reply:
                    if failure == 'receipt': reply['receipt'] = 'PRIVATE_SECRET'
                    receiver.sendto(json.dumps(reply).encode(), peer)
        udp_thread = threading.Thread(target=serve, daemon=True)
        udp_thread.start()
        original_bound = lan.bound_socket
        class LocalHTTP:
            def __init__(self, sock): self.sock = sock
            def __getattr__(self, name): return getattr(self.sock, name)
            def connect(self, peer):
                # Only redirect the fixed HTTP port to this local fixture's port.
                assert peer == ('127.0.0.1', 80)
                return self.sock.connect(('127.0.0.1', server.server_port))
        def bound(source, interface, kind):
            sock = original_bound(source, interface, kind)
            return LocalHTTP(sock) if kind == socket.SOCK_STREAM else sock
        calls = 0
        def route(*_args):
            nonlocal calls
            calls += 1
            return 'changed' if failure == 'route' and calls >= 3 else 'fixture'
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'witness.jsonl'
                with patch.object(lan, 'client_route', side_effect=route), \
                        patch.object(lan, 'PORT', receiver.getsockname()[1]), \
                        patch.object(lan, 'bound_socket', side_effect=bound):
                    if failure in ('receipt', 'route'):
                        with self.assertRaises(ValueError):
                            lan.run('a' * 32, '127.0.0.1', '127.0.0.1', str(index), 10, output)
                    else:
                        lan.run('a' * 32, '127.0.0.1', '127.0.0.1', str(index), 10, output)
                    before = output.read_bytes()
                    with self.assertRaises(FileExistsError):
                        lan.run('a' * 32, '127.0.0.1', '127.0.0.1', str(index), 10, output)
                    self.assertEqual(output.read_bytes(), before)
                text = output.read_text()
                rows = [json.loads(line) for line in text.splitlines()]
                self.assertIsNone(rows[0]['boot_id'])
                self.assertEqual(rows[0]['platform'], 'windows')
                self.assertNotIn('PRIVATE', text)
                self.assertNotIn('127.0.0.1', text)
                self.assertEqual(rows[-1]['complete'], failure not in ('receipt', 'route'))
                if failure not in ('receipt', 'route'):
                    self.assertEqual(receipts.coverage(receipts.samples[0]['before'], receipts.samples[-1]['after']),
                                     'FAIL' if failure == 'http' else 'PASS')
        finally:
            stopped.set()
            udp_thread.join(2)
            receiver.close()
            server.shutdown()
            server.server_close()
            http_thread.join(2)

    def test_real_native_sockets_and_protocol(self): self.exercise()
    def test_http_failure_recorded(self): self.exercise('http')
    def test_malformed_receipt_incomplete(self): self.exercise('receipt')
    def test_adapter_change_incomplete(self): self.exercise('route')


if __name__ == '__main__': unittest.main()
