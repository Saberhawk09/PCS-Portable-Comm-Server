import hashlib
import base64
import io
import http.client
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "web" / "pcs-control-panel" / "pcs_stats_api.py"
CONTROL_PANEL_PATH = ROOT / "web" / "pcs-control-panel" / "pcs_control_panel.py"
TOKEN_HELPER_PATH = ROOT / "scripts" / "pcs_api_token.py"
OPENAPI_PATH = ROOT / "docs" / "pcs-stats-api-v1.openapi.json"
SPEC = importlib.util.spec_from_file_location("pcs_stats_api", API_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)
TOKEN_SPEC = importlib.util.spec_from_file_location("pcs_api_token", TOKEN_HELPER_PATH)
token_helper = importlib.util.module_from_spec(TOKEN_SPEC)
TOKEN_SPEC.loader.exec_module(token_helper)
CONTROL_PANEL_SPEC = importlib.util.spec_from_file_location("pcs_control_panel_for_api_test", CONTROL_PANEL_PATH)
control_panel = importlib.util.module_from_spec(CONTROL_PANEL_SPEC)
CONTROL_PANEL_SPEC.loader.exec_module(control_panel)


PUBLIC_DASHBOARD = {
    "generated_at": "2026-08-26T18:30:00-04:00",
    "overall": "warn",
    "offline": False,
    "system": {"status": "ok", "uptime": "2h", "cpu_temperature": "42 C", "local_time": "private-local-time"},
    "network": {"status": "ok", "internet_available": True, "uplink_type": "Cellular / WWAN", "openwrt_url": "must-not-render"},
    "remote_management": {"configured": True, "status": "ok", "connection": "connected", "management_address": "10.99.0.7/32", "endpoint": "must-not-render"},
    "cellular": {"status": "ok", "connected": True, "carrier": "Field Carrier", "imei": "must-not-render"},
    "time": {"status": "ok", "synchronized": True, "source": "GNSS"},
    "gnss": {"status": "ok", "fix": "3D fix", "grid_square": "FM18", "coordinates": "38.1,-77.1"},
    "storage": {"status": "ok", "usb_mounted": True},
    "services": {"status": "ok", "homepage_available": True},
    "pistar": {"configured": True, "online": True, "url": "must-not-render"},
    "aprs": {"configured": True, "status": "ok", "callsign": "N0CALL-10", "passcode": "must-not-render"},
    "meshtastic": {"configured": True, "status": "warn", "mqtt": "disconnected", "broker": "must-not-render", "node": "must-not-render"},
}

ADMIN_DASHBOARD = {
    "generated_at": "2026-08-26T18:30:01-04:00",
    "overall": "warn",
    "cards": [
        {
            "id": "network",
            "title": "Network",
            "status": "ok",
            "summary": "Network healthy",
            "items": [
                {"label": "Public IP", "value": "198.51.100.10"},
                {"label": "API key", "value": "must-not-render"},
            ],
        },
        {
            "id": "meshtastic",
            "title": "Meshtastic",
            "status": "warn",
            "summary": "Broker mismatch",
            "items": [{"label": "MQTT broker", "value": "mqtt.example.test:8883"}],
        },
    ],
    "client_info": {
        "router_ip": "10.42.0.1",
        "wan_public_ip": "198.51.100.10",
        "router_side_clients": [{"ip": "10.42.0.50", "mac": "00:11:22:33:44:55"}],
        "private_key": "must-not-render",
    },
}


class ContractTests(unittest.TestCase):
    def test_tls_handshakes_are_deferred_to_bounded_worker_threads(self):
        self.assertTrue(api.ReusableThreadingHTTPServer.daemon_threads)
        self.assertGreaterEqual(api.ReusableThreadingHTTPServer.request_queue_size, 16)

        server = object.__new__(api.ReusableThreadingHTTPServer)
        raw_socket = mock.Mock()
        wrapped_socket = mock.Mock(spec=api.ssl.SSLSocket)
        context = mock.Mock()
        context.wrap_socket.return_value = wrapped_socket
        server.tls_context = context
        with mock.patch.object(
            api.ThreadingHTTPServer,
            "get_request",
            return_value=(raw_socket, ("127.0.0.1", 12345)),
        ):
            request, address = server.get_request()

        self.assertIs(request, wrapped_socket)
        self.assertEqual(address, ("127.0.0.1", 12345))
        raw_socket.settimeout.assert_called_once_with(10)
        context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_side=True,
            do_handshake_on_connect=False,
        )
        wrapped_socket.do_handshake.assert_not_called()

    def test_tls_worker_completes_handshake_before_http_handler(self):
        server = object.__new__(api.ReusableThreadingHTTPServer)
        request = mock.Mock(spec=api.ssl.SSLSocket)
        client = ("127.0.0.1", 12345)
        with mock.patch.object(
            api.ThreadingHTTPServer,
            "process_request_thread",
        ) as parent:
            server.process_request_thread(request, client)

        request.do_handshake.assert_called_once_with()
        request.settimeout.assert_called_once_with(30)
        parent.assert_called_once_with(request, client)

    def test_stalled_tls_handshake_is_closed_without_entering_http_handler(self):
        server = object.__new__(api.ReusableThreadingHTTPServer)
        request = mock.Mock(spec=api.ssl.SSLSocket)
        request.do_handshake.side_effect = TimeoutError()
        client = ("127.0.0.1", 12345)
        with mock.patch.object(server, "shutdown_request") as shutdown, mock.patch.object(
            api.ThreadingHTTPServer,
            "process_request_thread",
        ) as parent:
            server.process_request_thread(request, client)

        shutdown.assert_called_once_with(request)
        parent.assert_not_called()

    def test_server_suppresses_expected_tls_disconnect_tracebacks(self):
        server = object.__new__(api.ReusableThreadingHTTPServer)
        with mock.patch.object(
            api.sys,
            "exc_info",
            return_value=(ConnectionResetError, ConnectionResetError(), None),
        ), mock.patch.object(api.ThreadingHTTPServer, "handle_error") as parent:
            server.handle_error(None, ("127.0.0.1", 12345))
        parent.assert_not_called()

    def test_server_reports_unexpected_handler_errors(self):
        server = object.__new__(api.ReusableThreadingHTTPServer)
        with mock.patch.object(
            api.sys,
            "exc_info",
            return_value=(RuntimeError, RuntimeError("test"), None),
        ), mock.patch.object(api.ThreadingHTTPServer, "handle_error") as parent:
            server.handle_error(None, ("127.0.0.1", 12345))
        parent.assert_called_once_with(None, ("127.0.0.1", 12345))

    def test_api_action_catalog_exactly_matches_current_control_panel_buttons(self):
        self.assertEqual(set(api.ACTION_MAP), set(control_panel.ACTION_MAP))
        for name, (label, description) in control_panel.ACTION_MAP.items():
            self.assertEqual(api.ACTION_MAP[name]["label"], label)
            self.assertEqual(api.ACTION_MAP[name]["description"], description)

    def test_discovery_contract_matches_implemented_resources(self):
        public = api.discovery_document()
        authenticated = api.discovery_document(authenticated=True)
        self.assertEqual(public["resources"], api.RESOURCE_PATHS)
        self.assertEqual(public["access"], "public")
        self.assertEqual(authenticated["access"], "authenticated")
        self.assertFalse(public["write_actions"])
        self.assertEqual(public["methods"], ["GET", "HEAD"])
        self.assertEqual(public["pairing"], "/api/v1/pair")
        self.assertEqual(public["actions"], "/api/v1/actions")
        self.assertEqual(public["password"], "/api/v1/admin/password")
        self.assertTrue(api.discovery_document(authenticated=True, action_authorized=True)["write_actions"])

    def test_public_contract_is_versioned_utc_nullable_and_redacted(self):
        document = api.api_document("status", PUBLIC_DASHBOARD)
        self.assertEqual(document["api_version"], "v1")
        self.assertEqual(document["schema_version"], "1.0")
        self.assertEqual(document["generated_at"], "2026-08-26T22:30:00Z")
        self.assertEqual(document["access"], "public")
        self.assertIsNone(document["details"])
        serialized = json.dumps(document).lower()
        self.assertNotIn("coordinates", serialized)
        self.assertNotIn("openwrt_url", serialized)
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("imei", serialized)
        self.assertNotIn("passcode", serialized)
        self.assertNotIn("broker", serialized)
        self.assertNotIn("must-not-render", serialized)

    def test_each_document_has_stable_resource_shape(self):
        for resource in api.RESOURCE_SECTIONS:
            document = api.api_document(resource, PUBLIC_DASHBOARD)
            self.assertEqual(document["resource"], resource)
            self.assertIn(document["health"]["severity"], {"ok", "warn", "bad"})
            self.assertIsInstance(document["data"], dict)
            self.assertNotIn("status", document["data"])

    def test_authenticated_contract_adds_admin_details_but_still_blocks_secrets(self):
        document = api.add_authenticated_details(
            api.api_document("status", PUBLIC_DASHBOARD),
            "status",
            ADMIN_DASHBOARD,
        )
        self.assertEqual(document["access"], "authenticated")
        self.assertEqual(len(document["details"]["cards"]), 2)
        self.assertEqual(document["details"]["client_info"]["wan_public_ip"], "198.51.100.10")
        serialized = json.dumps(document).lower()
        self.assertIn("mqtt.example.test", serialized)
        self.assertNotIn("api key", serialized)
        self.assertNotIn("private_key", serialized)
        self.assertNotIn("must-not-render", serialized)

    def test_authenticated_status_uses_admin_overall_warning(self):
        public = dict(PUBLIC_DASHBOARD)
        public["overall"] = "ok"
        admin = dict(ADMIN_DASHBOARD)
        admin["overall"] = "warn"

        document = api.add_authenticated_details(
            api.api_document("status", public),
            "status",
            admin,
        )

        self.assertEqual(document["health"]["severity"], "warn")
        self.assertFalse(document["health"]["offline"])

    def test_authenticated_resource_gets_only_related_admin_cards(self):
        network = api.add_authenticated_details(
            api.api_document("network", PUBLIC_DASHBOARD),
            "network",
            ADMIN_DASHBOARD,
        )
        self.assertEqual([card["id"] for card in network["details"]["cards"]], ["network"])
        self.assertIn("client_info", network["details"])
        meshtastic = api.add_authenticated_details(
            api.api_document("meshtastic", PUBLIC_DASHBOARD),
            "meshtastic",
            ADMIN_DASHBOARD,
        )
        self.assertEqual([card["id"] for card in meshtastic["details"]["cards"]], ["meshtastic"])
        self.assertNotIn("client_info", meshtastic["details"])


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "tokens.json"
        self.raw_token = "pcs_ro_" + "a" * 43
        self.path.write_text(json.dumps({
            "version": 1,
            "tokens": [{
                "id": "phone",
                "token_sha256": hashlib.sha256(self.raw_token.encode()).hexdigest(),
                "scopes": ["stats:read"],
                "enabled": True,
            }],
        }), encoding="utf-8")
        os.chmod(self.path, 0o640)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_store_authenticates_hash_without_storing_raw_token(self):
        store = api.ApiTokenStore(str(self.path))
        principal = store.authenticate(f"Bearer {self.raw_token}")
        self.assertEqual(principal["id"], "phone")
        self.assertNotIn(self.raw_token, self.path.read_text(encoding="utf-8"))
        self.assertIsNone(store.authenticate("Bearer wrong-token"))
        self.assertIsNone(store.authenticate(None))

    @unittest.skipIf(os.name == "nt", "POSIX token-file permissions are enforced on PCS/Linux")
    def test_world_readable_token_store_is_rejected(self):
        os.chmod(self.path, 0o644)
        self.assertFalse(api.ApiTokenStore(str(self.path)).configured())


class CollectorTests(unittest.TestCase):
    def test_collector_is_bounded_cached_and_action_allowlisted(self):
        collector = api.StatusCollector("/fixed-dispatcher", timeout=7, ttl=30)
        completed = subprocess.CompletedProcess([], 0, json.dumps(PUBLIC_DASHBOARD), "")
        with mock.patch.object(api.subprocess, "run", return_value=completed) as runner:
            first = collector.collect("dashboard-public-json")
            second = collector.collect("dashboard-public-json")
        self.assertEqual(first, second)
        runner.assert_called_once_with(
            ["sudo", "-n", "/fixed-dispatcher", "dashboard-public-json"],
            text=True,
            capture_output=True,
            timeout=7,
        )
        with self.assertRaises(ValueError):
            collector.collect("arbitrary-command")

    def test_collector_timeout_is_structured(self):
        collector = api.StatusCollector(timeout=1)
        with mock.patch.object(api.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 1)):
            with self.assertRaises(api.CollectionError) as raised:
                collector.collect()
        self.assertEqual(raised.exception.code, "collector_timeout")


class ActionRunnerTests(unittest.TestCase):
    def test_runner_is_serialized_bounded_and_passes_only_allowlisted_name(self):
        runner = api.ActionRunner("/fixed-dispatcher")
        completed = subprocess.CompletedProcess([], 0, "done", "warning")
        with mock.patch.object(api.subprocess, "run", return_value=completed) as process:
            result = runner.run("wifi-status")
        process.assert_called_once_with(
            ["sudo", "-n", "/fixed-dispatcher", "wifi-status"],
            text=True,
            capture_output=True,
            timeout=300,
        )
        self.assertEqual(result["output"], "done\nwarning")
        with self.assertRaises(ValueError):
            runner.run("arbitrary-shell")
        runner.lock.acquire()
        try:
            with self.assertRaises(api.ActionError) as raised:
                runner.run("wifi-status")
        finally:
            runner.lock.release()
        self.assertEqual(raised.exception.code, "action_busy")

    def test_runner_bounds_output_and_structures_timeout(self):
        runner = api.ActionRunner("/fixed-dispatcher")
        completed = subprocess.CompletedProcess([], 7, "x" * (api.MAX_ACTION_OUTPUT_BYTES + 100), b"stderr")
        with mock.patch.object(api.subprocess, "run", return_value=completed):
            result = runner.run("status")
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertTrue(result["output_truncated"])
        self.assertTrue(result["output"].endswith("[output truncated]"))

        with mock.patch.object(
            api.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["fixed"], 1, output=b"partial", stderr=b"late"),
        ):
            with self.assertRaises(api.ActionError) as raised:
                runner.run("status")
        self.assertEqual(raised.exception.code, "action_timeout")


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.raw_token = "pcs_ro_" + "b" * 43
        token_path = Path(cls.tempdir.name) / "tokens.json"
        token_path.write_text(json.dumps({
            "version": 1,
            "tokens": [{
                "id": "test-app",
                "token_sha256": hashlib.sha256(cls.raw_token.encode()).hexdigest(),
                "scopes": ["stats:read", "admin:actions", "admin:password"],
                "enabled": True,
            }, {
                "id": "read-only-app",
                "token_sha256": hashlib.sha256(("pcs_ro_" + "d" * 43).encode()).hexdigest(),
                "scopes": ["stats:read"],
                "enabled": True,
            }],
        }), encoding="utf-8")
        os.chmod(token_path, 0o640)
        cls.originals = (
            api.API_ENABLED,
            api.TOKENS,
            api.COLLECTOR,
            api.PUBLIC_LIMITER,
            api.UNAUTH_LIMITER,
            api.AUTH_LIMITER,
            api.PAIR_LIMITER,
            api.ACTION_LIMITER,
            api.ACTION_RUNNER,
            api.ACTION_CHALLENGES,
        )
        api.API_ENABLED = True
        api.TOKENS = api.ApiTokenStore(str(token_path))
        cls.collector = mock.Mock()
        cls.collector.collect.side_effect = lambda action: PUBLIC_DASHBOARD if action == "dashboard-public-json" else ADMIN_DASHBOARD
        api.COLLECTOR = cls.collector
        api.PUBLIC_LIMITER = api.FixedWindowLimiter(100, 60)
        api.UNAUTH_LIMITER = api.FixedWindowLimiter(100, 60)
        api.AUTH_LIMITER = api.FixedWindowLimiter(100, 60)
        api.PAIR_LIMITER = api.FixedWindowLimiter(100, 60)
        api.ACTION_LIMITER = api.FixedWindowLimiter(100, 60)
        cls.action_runner = mock.Mock()
        cls.action_runner.run.return_value = {
            "exit_code": 0, "ok": True, "output": "action complete",
            "output_truncated": False, "duration_ms": 12,
        }
        api.ACTION_RUNNER = cls.action_runner
        api.ACTION_CHALLENGES = api.ActionChallenges(ttl=60)
        cls.server = api.ReusableThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        (
            api.API_ENABLED,
            api.TOKENS,
            api.COLLECTOR,
            api.PUBLIC_LIMITER,
            api.UNAUTH_LIMITER,
            api.AUTH_LIMITER,
            api.PAIR_LIMITER,
            api.ACTION_LIMITER,
            api.ACTION_RUNNER,
            api.ACTION_CHALLENGES,
        ) = cls.originals
        cls.tempdir.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        result = response.status, response.headers, body
        connection.close()
        return result

    def test_public_request_gets_only_public_contract(self):
        status, headers, body = self.request("GET", "/api/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/vnd.pcs.v1+json")
        payload = json.loads(body)
        self.assertEqual(payload["access"], "public")
        self.assertIsNone(payload["details"])
        self.assertNotIn("198.51.100.10", body)

    def test_discovery_is_public_and_does_not_run_collector(self):
        self.collector.reset_mock()
        status, _, body = self.request("GET", "/api/v1")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["resource"], "discovery")
        self.assertEqual(payload["resources"], api.RESOURCE_PATHS)
        self.assertFalse(payload["write_actions"])
        self.collector.collect.assert_not_called()

    def test_valid_token_adds_authenticated_admin_details(self):
        status, _, body = self.request(
            "GET",
            "/api/v1/network",
            {"Authorization": f"Bearer {self.raw_token}"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["access"], "authenticated")
        self.assertEqual(payload["details"]["client_info"]["wan_public_ip"], "198.51.100.10")

    def test_invalid_supplied_token_is_rejected_not_downgraded_to_public(self):
        status, headers, body = self.request(
            "GET", "/api/v1/status", {"Authorization": "Bearer invalid-token"}
        )
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers.get("WWW-Authenticate"))
        self.assertEqual(json.loads(body)["code"], "unauthorized")

    def test_unknown_query_and_write_methods_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/v1/status?raw=1")[0], 400)
        status, headers, body = self.request("POST", "/api/v1/status")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "GET, HEAD")
        self.assertEqual(json.loads(body)["code"], "method_not_allowed")
        self.assertEqual(self.request("PUT", "/api/v1/pair")[0], 405)

    def test_pairing_returns_one_admin_scoped_device_token(self):
        paired = {
            "device_id": "andre-phone",
            "token_type": "Bearer",
            "token": "pcs_ro_" + "c" * 43,
            "scopes": ["stats:read", "admin:actions", "admin:password"],
        }
        request_body = json.dumps({"admin_password": "correct horse", "device_id": "andre-phone"})
        with mock.patch.object(api, "pair_device", return_value=paired) as pairer:
            status, headers, body = self.request(
                "POST", "/api/v1/pair",
                {"Content-Type": "application/json"},
                request_body,
            )
        self.assertEqual(status, 201)
        self.assertEqual(headers.get("Cache-Control"), "no-store, max-age=0")
        payload = json.loads(body)
        self.assertEqual(payload["resource"], "pairing")
        self.assertEqual(payload["token"], paired["token"])
        self.assertEqual(payload["scopes"], ["stats:read", "admin:actions", "admin:password"])
        pairer.assert_called_once_with("correct horse", "andre-phone")

    def test_pairing_rejects_bad_auth_duplicate_and_bad_schema(self):
        request_body = json.dumps({"admin_password": "wrong", "device_id": "phone"})
        with mock.patch.object(
            api, "pair_device",
            side_effect=api.PairingError("admin_authentication_failed", "Administrator authentication failed."),
        ):
            status, headers, body = self.request(
                "POST", "/api/v1/pair", {"Content-Type": "application/json"}, request_body
            )
        self.assertEqual(status, 401)
        self.assertIn("PCS-Admin", headers.get("WWW-Authenticate"))
        self.assertEqual(json.loads(body)["code"], "admin_authentication_failed")

        with mock.patch.object(
            api, "pair_device",
            side_effect=api.PairingError("device_already_paired", "That device ID is already paired."),
        ):
            status, _, body = self.request(
                "POST", "/api/v1/pair", {"Content-Type": "application/json"}, request_body
            )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["code"], "device_already_paired")
        self.assertEqual(self.request("POST", "/api/v1/pair", body=request_body)[0], 415)
        extra = json.dumps({"admin_password": "x", "device_id": "phone", "extra": True})
        self.assertEqual(self.request("POST", "/api/v1/pair", {"Content-Type": "application/json"}, extra)[0], 400)

    def test_pairing_has_a_separate_rate_limit(self):
        previous = api.PAIR_LIMITER
        api.PAIR_LIMITER = api.FixedWindowLimiter(1, 60)
        body = json.dumps({"admin_password": "wrong", "device_id": "phone"})
        try:
            with mock.patch.object(
                api, "pair_device",
                side_effect=api.PairingError("admin_authentication_failed", "Administrator authentication failed."),
            ):
                self.assertEqual(self.request("POST", "/api/v1/pair", {"Content-Type": "application/json"}, body)[0], 401)
                status, headers, response_body = self.request("POST", "/api/v1/pair", {"Content-Type": "application/json"}, body)
        finally:
            api.PAIR_LIMITER = previous
        self.assertEqual(status, 429)
        self.assertIsNotNone(headers.get("Retry-After"))
        self.assertEqual(json.loads(response_body)["code"], "rate_limited")

    def test_action_catalog_requires_admin_scope(self):
        status, _, body = self.request(
            "GET", "/api/v1/actions", {"Authorization": f"Bearer {self.raw_token}"}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["required_scope"], "admin:actions")
        self.assertEqual({item["name"] for item in payload["actions"]}, set(api.ACTION_MAP))
        readonly = "pcs_ro_" + "d" * 43
        status, _, body = self.request(
            "GET", "/api/v1/actions", {"Authorization": f"Bearer {readonly}"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "insufficient_scope")

    def test_head_action_catalog_preserves_head_semantics(self):
        status, _, body = self.request(
            "HEAD", "/api/v1/actions", {"Authorization": f"Bearer {self.raw_token}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, "")
        status, _, body = self.request("HEAD", "/api/v1/actions")
        self.assertEqual(status, 403)
        self.assertEqual(body, "")

    def test_safe_action_executes_only_the_fixed_dispatcher_name(self):
        self.action_runner.reset_mock()
        status, _, body = self.request(
            "POST", "/api/v1/actions/wifi-status",
            {"Authorization": f"Bearer {self.raw_token}", "Content-Type": "application/json"},
            "{}",
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["output"], "action complete")
        self.action_runner.run.assert_called_once_with("wifi-status")

    def test_dangerous_action_requires_and_consumes_a_challenge(self):
        self.action_runner.reset_mock()
        headers = {"Authorization": f"Bearer {self.raw_token}", "Content-Type": "application/json"}
        self.assertEqual(
            self.request("POST", "/api/v1/actions/restart-samba", headers, "{}")[0],
            400,
        )
        status, _, body = self.request(
            "POST", "/api/v1/actions/restart-samba/challenge", headers, "{}"
        )
        self.assertEqual(status, 201)
        challenge = json.loads(body)["challenge"]
        request_body = json.dumps({"confirmation": "restart-samba", "challenge": challenge})
        self.assertEqual(
            self.request("POST", "/api/v1/actions/restart-samba", headers, request_body)[0],
            200,
        )
        self.assertEqual(
            self.request("POST", "/api/v1/actions/restart-samba", headers, request_body)[0],
            409,
        )
        self.action_runner.run.assert_called_once_with("restart-samba")

    def test_arbitrary_action_and_read_only_action_token_are_rejected(self):
        self.action_runner.reset_mock()
        admin_headers = {"Authorization": f"Bearer {self.raw_token}", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/v1/actions/arbitrary-shell", admin_headers, "{}")[0], 404)
        readonly_headers = {"Authorization": "Bearer " + "pcs_ro_" + "d" * 43, "Content-Type": "application/json"}
        status, _, body = self.request("POST", "/api/v1/actions/wifi-status", readonly_headers, "{}")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "insufficient_scope")
        self.action_runner.run.assert_not_called()

    def test_password_change_requires_exact_admin_scope_and_schema(self):
        headers = {
            "Authorization": f"Bearer {self.raw_token}",
            "Content-Type": "application/json",
        }
        with mock.patch.object(api, "change_admin_password") as changer:
            status, _, body = self.request(
                "POST", "/api/v1/admin/password", headers,
                json.dumps({"current_password": "old secret", "new_password": "new secret password"}),
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["changed"], True)
        changer.assert_called_once_with("old secret", "new secret password")

        readonly_headers = {
            "Authorization": "Bearer " + "pcs_ro_" + "d" * 43,
            "Content-Type": "application/json",
        }
        status, _, body = self.request(
            "POST", "/api/v1/admin/password", readonly_headers,
            json.dumps({"current_password": "old secret", "new_password": "new secret password"}),
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "insufficient_scope")

        self.assertEqual(
            self.request(
                "POST", "/api/v1/admin/password", headers,
                json.dumps({"current_password": "old secret", "new_password": "new secret password", "extra": 1}),
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                "POST", "/api/v1/admin/password", headers,
                json.dumps({"current_password": "old secret", "new_password": "short"}),
            )[0],
            400,
        )
        self.assertEqual(self.request("POST", "/api/v1/admin/password", headers, "{}")[0], 400)

    def test_password_change_maps_helper_errors_without_exposing_passwords(self):
        headers = {
            "Authorization": f"Bearer {self.raw_token}",
            "Content-Type": "application/json",
        }
        with mock.patch.object(
            api, "change_admin_password",
            side_effect=api.PairingError("current_password_incorrect", "Current administrator password was incorrect."),
        ):
            status, response_headers, body = self.request(
                "POST", "/api/v1/admin/password", headers,
                json.dumps({"current_password": "wrong", "new_password": "new secret password"}),
            )
        self.assertEqual(status, 401)
        self.assertIn("PCS-Admin", response_headers.get("WWW-Authenticate"))
        self.assertNotIn("wrong", body)
        self.assertNotIn("new secret password", body)

    def test_password_change_invokes_configured_fixed_helper(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(api.subprocess, "run", return_value=completed) as process, \
             mock.patch.object(api, "PASSWORD_HELPER", "/fixed/password-helper"):
            api.change_admin_password("old", "new")
        process.assert_called_once_with(
            ["sudo", "-n", "/fixed/password-helper", "--change-from-stdin"],
            input='{"current_password":"old","new_password":"new"}',
            text=True,
            capture_output=True,
            timeout=20,
        )

    def test_password_change_maps_helper_exit_codes(self):
        for returncode, expected in ((3, "current_password_incorrect"), (4, "password_change_rejected"), (9, "password_change_failed")):
            with self.subTest(returncode=returncode), mock.patch.object(
                api.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], returncode, "", ""),
            ):
                with self.assertRaises(api.PairingError) as raised:
                    api.change_admin_password("old", "new")
            self.assertEqual(raised.exception.code, expected)

    def test_public_requests_are_rate_limited_independently(self):
        previous = api.PUBLIC_LIMITER
        api.PUBLIC_LIMITER = api.FixedWindowLimiter(1, 60)
        try:
            self.assertEqual(self.request("GET", "/api/v1/status")[0], 200)
            status, headers, body = self.request("GET", "/api/v1/status")
        finally:
            api.PUBLIC_LIMITER = previous
        self.assertEqual(status, 429)
        self.assertIsNotNone(headers.get("Retry-After"))
        self.assertEqual(json.loads(body)["code"], "rate_limited")

    def test_disabled_api_is_indistinguishable_from_missing_route(self):
        api.API_ENABLED = False
        try:
            status, _, body = self.request("GET", "/api/v1/status")
        finally:
            api.API_ENABLED = True
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["code"], "not_found")


class TokenHelperTests(unittest.TestCase):
    @staticmethod
    def admin_record(password):
        salt = b"0123456789abcdef"
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
        return {
            "version": 1,
            "algorithm": "pbkdf2-sha256",
            "iterations": 100_000,
            "salt": encode(salt),
            "password_hash": encode(digest),
        }

    def test_helper_issues_hashed_token_and_revokes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "tokens.json"
            issued = subprocess.run(
                ["python", str(TOKEN_HELPER_PATH), "--file", str(token_file), "issue", "phone"],
                text=True,
                capture_output=True,
                check=True,
            )
            raw_token = issued.stdout.strip().splitlines()[-1]
            stored = token_file.read_text(encoding="utf-8")
            self.assertTrue(raw_token.startswith("pcs_ro_"))
            self.assertNotIn(raw_token, stored)
            subprocess.run(
                ["python", str(TOKEN_HELPER_PATH), "--file", str(token_file), "revoke", "phone"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse(json.loads(token_file.read_text(encoding="utf-8"))["tokens"][0]["enabled"])

    def test_helper_rejects_malformed_store_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "tokens.json"
            token_file.write_text("[]\n", encoding="utf-8")
            result = subprocess.run(
                ["python", str(TOKEN_HELPER_PATH), "--file", str(token_file), "list"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsupported format", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_pairing_helper_verifies_admin_and_never_stores_raw_token(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "tokens.json"
            admin_file = Path(directory) / "admin.json"
            admin_file.write_text(json.dumps(self.admin_record("admin secret")), encoding="utf-8")
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
                "admin_password": "admin secret",
                "device_id": "android-phone",
            }).encode()), encoding="utf-8")
            with mock.patch.object(token_helper.sys, "stdin", stdin):
                result = token_helper.pair_from_stdin(str(token_file), str(admin_file))
            stored = token_file.read_text(encoding="utf-8")
            self.assertEqual(result["scopes"], ["stats:read", "admin:actions", "admin:password"])
            self.assertTrue(result["token"].startswith("pcs_ro_"))
            self.assertNotIn(result["token"], stored)
            self.assertNotIn("admin secret", stored)
            with self.assertRaises(token_helper.DuplicateTokenError):
                token_helper.issue(str(token_file), "android-phone")

    def test_pairing_helper_rejects_wrong_admin_without_creating_token(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "tokens.json"
            admin_file = Path(directory) / "admin.json"
            admin_file.write_text(json.dumps(self.admin_record("right")), encoding="utf-8")
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
                "admin_password": "wrong",
                "device_id": "android-phone",
            }).encode()), encoding="utf-8")
            with mock.patch.object(token_helper.sys, "stdin", stdin):
                with self.assertRaises(PermissionError):
                    token_helper.pair_from_stdin(str(token_file), str(admin_file))
            self.assertFalse(token_file.exists())

    def test_pairing_helper_serializes_linux_token_store_mutations(self):
        source = TOKEN_HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn("fcntl.flock(descriptor, fcntl.LOCK_EX)", source)
        self.assertIn("with token_store_lock(path):", source)


class OpenApiConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specification = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    def test_specification_is_openapi_31_and_covers_every_route(self):
        self.assertEqual(self.specification["openapi"], "3.1.0")
        expected_paths = {
            "/api/v1", "/api/v1/pair", "/api/v1/actions",
            "/api/v1/actions/{action}", "/api/v1/actions/{action}/challenge",
            "/api/v1/admin/password",
            *api.RESOURCE_PATHS.values(),
        }
        self.assertEqual(set(self.specification["paths"]), expected_paths)
        resources = {
            operation["x-pcs-resource"]: path
            for path, path_item in self.specification["paths"].items()
            for operation in [path_item.get("get", {})]
            if "x-pcs-resource" in operation
        }
        self.assertEqual(resources, api.RESOURCE_PATHS)

    def test_every_get_allows_public_or_bearer_and_uses_versioned_json(self):
        responses = self.specification["components"]["responses"]
        versioned_type = "application/vnd.pcs.v1+json"
        self.assertIn(versioned_type, responses["StatsResponse"]["content"])
        self.assertIn(versioned_type, responses["DiscoveryResponse"]["content"])
        for path, path_item in self.specification["paths"].items():
            if "get" not in path_item:
                continue
            with self.subTest(path=path):
                operation = path_item["get"]
                expected_security = [{"adminBearerAuth": []}] if path == "/api/v1/actions" else [{}, {"bearerAuth": []}]
                self.assertEqual(operation["security"], expected_security)
                self.assertIn("200", operation["responses"])
                self.assertIn("401", operation["responses"])
                self.assertIn("429", operation["responses"])

    def test_action_contract_has_catalog_execution_and_one_time_challenges(self):
        catalog = self.specification["paths"]["/api/v1/actions"]["get"]
        execute = self.specification["paths"]["/api/v1/actions/{action}"]["post"]
        challenge = self.specification["paths"]["/api/v1/actions/{action}/challenge"]["post"]
        self.assertEqual(catalog["security"], [{"adminBearerAuth": []}])
        self.assertIn("200", execute["responses"])
        self.assertIn("409", execute["responses"])
        self.assertIn("504", execute["responses"])
        self.assertIn("201", challenge["responses"])
        enum = self.specification["components"]["schemas"]["ActionName"]["enum"]
        self.assertEqual(set(enum), set(api.ACTION_MAP))
        metadata = self.specification["components"]["schemas"]["ActionMetadata"]
        self.assertEqual(set(metadata["required"]), {
            "name", "label", "description", "group", "dangerous",
            "challenge_required", "execute_path", "challenge_path",
        })

    def test_pairing_contract_is_tls_credential_exchange_with_exact_schema(self):
        operation = self.specification["paths"]["/api/v1/pair"]["post"]
        self.assertEqual(operation["security"], [])
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(set(schema["required"]), {"admin_password", "device_id"})
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("201", operation["responses"])
        self.assertIn("401", operation["responses"])
        self.assertIn("409", operation["responses"])
        self.assertIn("429", operation["responses"])
        scopes = self.specification["components"]["schemas"]["PairingEnvelope"]["properties"]["scopes"]
        self.assertEqual(scopes["minItems"], 3)
        self.assertEqual(scopes["maxItems"], 3)
        self.assertEqual([item["const"] for item in scopes["prefixItems"]], ["stats:read", "admin:actions", "admin:password"])

    def test_password_change_contract_is_admin_scoped_and_exact(self):
        operation = self.specification["paths"]["/api/v1/admin/password"]["post"]
        self.assertEqual(operation["security"], [{"adminBearerAuth": []}])
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["$ref"], "#/components/schemas/PasswordChangeRequest")
        password_schema = self.specification["components"]["schemas"]["PasswordChangeRequest"]
        self.assertEqual(set(password_schema["required"]), {"current_password", "new_password"})
        self.assertFalse(password_schema["additionalProperties"])
        self.assertEqual(password_schema["properties"]["new_password"]["minLength"], api.MIN_ADMIN_PASSWORD_LENGTH)
        self.assertIn("200", operation["responses"])
        self.assertIn("401", operation["responses"])
        self.assertIn("403", operation["responses"])
        self.assertIn("503", operation["responses"])

    def test_runtime_documents_match_envelope_required_fields(self):
        schema = self.specification["components"]["schemas"]["StatsEnvelope"]
        allowed = set(schema["properties"])
        required = set(schema["required"])
        for resource in api.RESOURCE_PATHS:
            with self.subTest(resource=resource):
                document = api.api_document(resource, PUBLIC_DASHBOARD)
                self.assertEqual(set(document), allowed)
                self.assertTrue(required.issubset(document))
                self.assertIn(document["resource"], schema["properties"]["resource"]["enum"])

    def test_openapi_file_contains_no_real_credentials_or_private_endpoint(self):
        serialized = json.dumps(self.specification).lower()
        self.assertNotIn("privatekey", serialized)
        self.assertNotIn("presharedkey", serialized)
        self.assertNotIn("asuscomm", serialized)
        self.assertNotIn("walbridge", serialized)


if __name__ == "__main__":
    unittest.main()
