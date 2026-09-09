import contextlib
import io
from pathlib import Path
import signal
import socket
import unittest
from unittest.mock import patch


class WireGuardDnsRetryTests(unittest.TestCase):
    def run_resolver(self, responses):
        source = (Path(__file__).parents[1] / "scripts/pcs-wireguard-endpoint-refresh.sh").read_text()
        code = source.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
        output = io.StringIO()
        with patch("sys.argv", ["resolver", "example.invalid"]), \
             patch("signal.signal"), patch("signal.alarm", create=True), \
             patch("signal.SIGALRM", 14, create=True), \
             patch("time.sleep"), \
             patch("socket.getaddrinfo", side_effect=responses) as lookup, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            try:
                exec(compile(code, "resolver", "exec"), {})
                status = 0
            except SystemExit as exc:
                status = exc.code
        return status, output.getvalue(), lookup.call_count

    def test_temporary_failure_recovers_on_retry(self):
        result = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.1", 0))]
        status, output, calls = self.run_resolver([socket.gaierror(socket.EAI_AGAIN, "temporary"), result])
        self.assertEqual((status, output, calls), (0, "192.0.2.1\n", 2))

    def test_repeated_temporary_failure_defers_without_an_endpoint(self):
        status, output, calls = self.run_resolver([socket.gaierror(socket.EAI_AGAIN, "temporary")] * 3)
        self.assertEqual((status, output, calls), (75, "", 3))

    def test_permanent_failure_is_not_suppressed(self):
        status, output, calls = self.run_resolver([socket.gaierror(socket.EAI_NONAME, "missing")])
        self.assertNotEqual(status, 75)
        self.assertNotEqual(status, 0)
        self.assertEqual((output, calls), ("", 1))
