"""Run real nginx integration tests when PCS_TEST_NGINX (or nginx on PATH) exists."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock
from urllib.parse import urlencode

import test_pcs_control_panel as control


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frontend_install", ROOT / "scripts/pcs_frontend_install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
NGINX = os.environ.get("PCS_TEST_NGINX") or shutil.which("nginx") or (
    "/usr/sbin/nginx" if Path("/usr/sbin/nginx").is_file() else None
)


class ProxyIdentityTests(unittest.TestCase):
    def test_only_opted_in_loopback_peer_can_supply_one_valid_address(self):
        for trusted, peer, header, expected in (
            (False, "127.0.0.1", "10.42.0.5", "127.0.0.1"),
            (True, "10.42.0.6", "10.42.0.5", "10.42.0.6"),
            (True, "127.0.0.1", "10.42.0.5", "10.42.0.5"),
            (True, "127.0.0.1", "10.42.0.5, 10.42.0.6", "127.0.0.1"),
            (True, "127.0.0.1", "invalid", "127.0.0.1"),
            (True, "127.0.0.1", "", "127.0.0.1"),
            (True, "::1", "2001:db8::1", "2001:db8::1"),
        ):
            with self.subTest(trusted=trusted, peer=peer, header=header), mock.patch.object(control.pcs, "TRUST_PROXY", trusted):
                handler = types.SimpleNamespace(client_address=(peer, 123), headers={"X-Real-IP": header})
                self.assertEqual(control.pcs.Handler.client_ip(handler), expected)

    def test_backend_defaults_and_installed_service_are_loopback_unprivileged(self):
        service = (ROOT / "systemd/pcs-control-panel.service").read_text()
        self.assertIn("PCS_CONTROL_HOST=127.0.0.1", service)
        self.assertIn("PCS_CONTROL_PORT=8081", service)
        self.assertIn("PCS_CONTROL_TRUST_PROXY=1", service)
        self.assertNotIn("CAP_NET_BIND_SERVICE", service)
        self.assertEqual((control.pcs.HOST, control.pcs.PORT), ("127.0.0.1", 8081))


class InstallerPreflightTests(unittest.TestCase):
    def test_missing_unit_stderr_is_handled_as_clean_install(self):
        missing = types.SimpleNamespace(stdout="", stderr="No such file", returncode=1)
        loaded = types.SimpleNamespace(stdout="not-found\n", stderr="", returncode=0)
        with mock.patch.object(installer.subprocess, "run", side_effect=[missing, loaded]):
            self.assertEqual(installer.state("pcs-control-panel.service", "is-enabled"), "not-found")

    def test_nginx_package_failure_restores_temporary_mask(self):
        def command(*args):
            if args[0] == "apt-get":
                raise RuntimeError("package unavailable")
        with mock.patch.object(installer.shutil, "which", return_value=None), mock.patch.object(installer, "state", return_value="not-found"), mock.patch.object(installer, "run", side_effect=command) as run:
            with self.assertRaisesRegex(RuntimeError, "package unavailable"):
                installer.install_nginx()
        self.assertEqual(run.call_args_list[-1], mock.call("systemctl", "unmask", "--runtime", "nginx.service"))

    def test_existing_nginx_mask_is_preserved(self):
        with mock.patch.object(installer.shutil, "which", return_value=None), mock.patch.object(installer, "state", return_value="masked"), mock.patch.object(installer, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "masked"):
                installer.install_nginx()
        run.assert_not_called()


@unittest.skipUnless(NGINX, "Real nginx required: install nginx or set PCS_TEST_NGINX")
class NginxRouteSecurityTests(control.RouteSecurityTests):
    """Repeat every existing HTTP security flow through the actual reverse proxy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(super().tearDownClass)
        cls.proxy_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.proxy_temp.cleanup)
        cls.proxy_root = Path(cls.proxy_temp.name)
        (cls.proxy_root / "logs").mkdir()
        (cls.proxy_root / "temp").mkdir()
        cls.nginx = str(Path(NGINX).resolve())
        cls.nginx_args = [cls.nginx, "-e", "stderr", "-p", cls.proxy_root.as_posix() + "/", "-c", "nginx.conf"]
        config = (ROOT / "config/nginx/pcs.conf").read_text()
        cls.config_path = cls.proxy_root / "nginx.conf"
        wrapper = (
            "worker_processes 1;\nerror_log logs/error.log;\npid logs/nginx.pid;\nevents {}\nhttp {\n"
            "access_log off;\nclient_body_temp_path temp/body;\nproxy_temp_path temp/proxy;\n"
            "fastcgi_temp_path temp/fastcgi;\nuwsgi_temp_path temp/uwsgi;\nscgi_temp_path temp/scgi;\n%s\n}\n"
        )
        # Debian nginx -t checks listener privileges. Keep unit tests unprivileged
        # by changing only port/root locations; the installer validates :80 as root.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = config.replace("listen 80 default_server;", f"listen 127.0.0.1:{port} default_server;")
        config = config.replace("127.0.0.1:8081", f"127.0.0.1:{cls.port}")
        with mock.patch.object(installer, "WEB_RELEASES", cls.proxy_root / "releases"):
            static = installer.stage_site()
        config = config.replace("root /var/www/pcs;", f'root "{static.as_posix()}";')
        cls.config_path.write_text(wrapper % config)
        result = subprocess.run(cls.nginx_args + ["-t"], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.process = subprocess.Popen(
            cls.nginx_args + ["-g", "daemon off;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        cls.addClassCleanup(cls.stop_proxy)
        cls.port = port
        cls.proxy_trust = mock.patch.object(control.pcs, "TRUST_PROXY", True)
        cls.proxy_trust.start()
        cls.addClassCleanup(cls.proxy_trust.stop)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError((cls.proxy_root / "logs/error.log").read_text())

    @classmethod
    def tearDownClass(cls):
        # Registered cleanups also run if nginx startup fails.
        pass

    @classmethod
    def stop_proxy(cls):
        subprocess.run(cls.nginx_args + ["-s", "quit"], capture_output=True, timeout=10)
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.terminate()
            cls.process.wait(timeout=5)

    def test_unknown_routes_are_not_forwarded(self):
        with mock.patch.object(control.pcs.Handler, "do_GET") as backend:
            for path in ("/api/private", "/.git/config", "/pcs_control_panel.py", "/api/public-status/", "/proxy/8081"):
                self.assertEqual(self.request("GET", path)[0], 404)
            backend.assert_not_called()

    def test_public_home_has_prominent_admin_login_and_allowed_coordinates(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Admin Login", body)
        self.assertIn("LOCAL OPERATIONS", body)
        self.assertIn("script-src 'self'", headers.get("Content-Security-Policy"))
        status, _, body = self.request("GET", "/status/")
        self.assertEqual(status, 200)
        self.assertIn("FM18kc", body)
        status, _, body = self.request("GET", "/api/public-status")
        self.assertEqual(status, 200)
        self.assertNotIn("imei", body.lower())

    def test_static_pages_assets_and_clean_service_redirects(self):
        status, _, body = self.request("GET", "/starlink/")
        self.assertEqual(status, 200)
        self.assertIn("Dish diagnostics", body)
        self.assertNotIn("<form", body)
        self.assertNotIn("starlink-reboot", body)
        for path, mime in (("/files/", "text/html"), ("/docs/", "text/html"), ("/radio/", "text/html"), ("/pistar/", "text/html"), ("/assets/css/pcs.css", "text/css"), ("/assets/js/pcs.js", "application/javascript")):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(mime, headers.get("Content-Type"))
            self.assertTrue(body)
        status, headers, _ = self.request("GET", "/cockpit/", headers={"Host": "pcs.local"})
        self.assertEqual((status, headers.get("Location")), (302, "https://pcs.local:9090/"))

    def test_admin_path_query_cookie_and_security_headers_survive_proxy(self):
        status, headers, page = self.request("GET", "/admin/login?changed=1")
        self.assertEqual(status, 200)
        self.assertIn("Password updated. Sign in again", page)
        self.assertIn("Path=/admin", headers.get("Set-Cookie"))
        self.assertIn("HttpOnly", headers.get("Set-Cookie"))
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie"))
        self.assertIn("no-store", headers.get("Cache-Control"))
        self.assertIn("frame-ancestors", headers.get("Content-Security-Policy"))
        status, headers, _ = self.request("HEAD", "/admin")
        self.assertEqual((status, headers.get("Location")), (303, "/admin/"))

    def test_nginx_overwrites_spoofed_proxy_identity(self):
        _, headers, page = self.request("GET", "/admin/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        with mock.patch.object(control.pcs.LOGIN_LIMITER, "allowed", return_value=False) as allowed:
            status, _, _ = self.request("POST", "/admin/login", urlencode({"csrf": csrf, "password": "wrong"}), {
                "Cookie": headers.get("Set-Cookie").split(";", 1)[0],
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Real-IP": "10.42.0.99", "X-Forwarded-For": "10.42.0.99",
            })
        self.assertEqual(status, 429)
        allowed.assert_called_once_with("127.0.0.1")

    def test_request_size_limit_rejects_before_backend(self):
        with mock.patch.object(control.pcs.Handler, "do_POST") as backend:
            self.assertEqual(self.request("POST", "/admin/login", "x" * 65537)[0], 413)
            backend.assert_not_called()

    def test_zz_backend_unavailable_returns_bounded_gateway_error(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual(self.request("GET", "/api/public-status")[0], 502)
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/assets/js/pcs.js")[0], 200)
        self.assertEqual(self.request("GET", "/docs/")[0], 200)
        self.assertEqual(self.request("GET", "/status/")[0], 502)
        self.assertEqual(self.request("GET", "/admin/")[0], 502)


class LegacyRedirectTests(unittest.TestCase):
    def test_legacy_get_head_and_health_preserve_frontend_destination(self):
        spec = importlib.util.spec_from_file_location("legacy_redirect", ROOT / "web/pcs-control-panel/pcs_dashboard_redirect.py")
        redirect = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(redirect)
        server = redirect.ThreadingHTTPServer(("127.0.0.1", 0), redirect.RedirectHandler)
        thread = control.threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method, path, expected in (("GET", "/", 308), ("HEAD", "/", 308), ("GET", "/health", 200)):
                connection = control.http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                connection.request(method, path, headers={"Host": "pcs.local:8080"})
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                if expected == 308:
                    self.assertEqual(response.headers.get("Location"), "http://pcs.local/admin/")
                response.read()
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@unittest.skipIf(os.name == "nt", "Installer transactions require Linux symlink semantics")
class InstallerTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.nginx = self.base / "nginx"
        self.units = self.base / "units"
        self.backup_root = self.base / "backups"
        self.backup_root.mkdir()
        (self.nginx / "sites-available").mkdir(parents=True)
        (self.nginx / "sites-enabled").mkdir()
        self.units.mkdir()
        self.files = [self.nginx / "sites-available/pcs", self.nginx / "sites-enabled/pcs",
                      self.nginx / "sites-enabled/default"] + [self.units / name for name in installer.SERVICES[:2]]
        for index, path in enumerate(self.files):
            path.write_text(f"original-{index}")
        self.original = [path.read_bytes() for path in self.files]
        for name, value in (("NGINX", self.nginx), ("UNITS", self.units), ("BACKUP_ROOT", self.backup_root), ("WEB_ROOT", self.base / "www"), ("WEB_RELEASES", self.base / "releases")):
            patcher = mock.patch.object(installer, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.package = mock.patch.object(installer, "install_nginx")
        self.package.start()
        self.addCleanup(self.package.stop)
        self.states = mock.patch.object(installer, "state", side_effect=lambda name, operation: "active" if operation == "is-active" else "enabled")
        self.states.start()
        self.addCleanup(self.states.stop)

    def test_staged_validation_failure_does_not_touch_installed_files_or_services(self):
        with mock.patch.object(installer, "run", side_effect=RuntimeError("invalid config")) as run:
            with self.assertRaisesRegex(RuntimeError, "invalid config"):
                installer.migrate()
        self.assertEqual([p.read_bytes() for p in self.files], self.original)
        self.assertEqual(run.call_args.args[:2], ("nginx", "-t"))
        self.assertEqual(run.call_count, 1)

    def test_full_configuration_failure_restores_files_without_service_downtime(self):
        def command(*args):
            if args == ("nginx", "-t"):
                raise RuntimeError("conflicting installed site")
        with mock.patch.object(installer, "run", side_effect=command) as run:
            with self.assertRaisesRegex(RuntimeError, "conflicting installed site"):
                installer.migrate()
        self.assertEqual([p.read_bytes() for p in self.files], self.original)
        self.assertFalse(any(call.args[0] == "systemctl" for call in run.call_args_list))

    def test_failed_endpoint_restores_files_and_previous_service_states(self):
        with mock.patch.object(installer, "run") as run, mock.patch.object(installer, "check_endpoints", side_effect=RuntimeError("bad endpoint")):
            with self.assertRaisesRegex(RuntimeError, "bad endpoint"):
                installer.migrate()
        self.assertEqual([p.read_bytes() for p in self.files], self.original)
        for name in installer.SERVICES:
            self.assertIn(mock.call("systemctl", "start", name), run.call_args_list)
        calls = [call.args for call in run.call_args_list]
        nginx_start = calls.index(("systemctl", "start", "nginx.service"))
        self.assertEqual(calls[nginx_start - 1], ("nginx", "-t"))

    def test_success_validates_before_switch_and_retains_backup(self):
        with mock.patch.object(installer, "run") as run, mock.patch.object(installer, "check_endpoints") as endpoints:
            installer.migrate()
        self.assertEqual(self.files[0].read_text(), (ROOT / "config/nginx/pcs.conf").read_text())
        self.assertFalse(self.files[2].exists())
        endpoints.assert_called_once()
        calls = [call.args for call in run.call_args_list]
        self.assertLess(calls.index(("nginx", "-t")), calls.index(("systemctl", "stop", *installer.SERVICES[:2])))
        activation = calls.index(("systemctl", "reload-or-restart", "nginx.service"))
        self.assertEqual(calls[activation - 1], ("nginx", "-t"))
        self.assertEqual(len(list(self.backup_root.glob("migration-*/files.json"))), 1)
