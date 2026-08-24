import http.client
import importlib.util
import json
import re
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "web" / "pcs-control-panel" / "pcs_control_panel.py"
SPEC = importlib.util.spec_from_file_location("pcs_control_panel", MODULE_PATH)
pcs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pcs)


PUBLIC_DATA = {
    "generated_at": "2026-08-06T12:00:00-04:00",
    "overall": "ok",
    "system": {"status": "ok", "uptime": "2h", "local_time": "noon", "cpu_temperature": "42 C"},
    "network": {"status": "ok", "lan_gateway": "10.42.0.1", "openwrt_online": True, "internet_available": True, "uplink_type": "Wi-Fi", "connected_client_count": 3},
    "cellular": {"status": "ok", "modem_present": True, "connected": False, "carrier": "Field Carrier", "signal": "Good", "imei": "must-not-render"},
    "time": {"status": "ok", "chrony_active": True, "synchronized": True, "source": "GNSS"},
    "gnss": {"status": "ok", "receiver_active": True, "fix": "3D fix", "satellites": "8 used", "coordinates": "38.123456, -77.123456", "grid_square": "FM18kc"},
    "storage": {"status": "ok", "usb_mounted": True, "primary_share_available": True, "backup_share_available": True, "usb_free_gb": "40 GB", "backup_free_gb": "8 GB"},
    "services": {"status": "ok", "gpsd_lan_enabled": True},
    "pistar": {"configured": False},
    "aprs": {"configured": False},
    "meshtastic": {"configured": False},
}

ADMIN_DATA = {
    "generated_at": "2026-08-06T12:00:00-04:00",
    "overall": "ok",
    "cards": [{"id": "system", "title": "System", "status": "ok", "summary": "Healthy", "items": []}],
    "client_info": {"router_side_clients": []},
}


class PasswordTests(unittest.TestCase):
    def test_password_record_verifies_without_storing_plaintext(self):
        record = pcs.make_password_record("correct horse battery staple")
        self.assertTrue(pcs.verify_password_record("correct horse battery staple", record))
        self.assertFalse(pcs.verify_password_record("wrong password", record))
        self.assertNotIn("correct horse battery staple", str(record))

    def test_password_minimum_length(self):
        with self.assertRaises(ValueError):
            pcs.make_password_record("too-short")

    def test_password_helper_receives_secrets_only_through_stdin(self):
        completed = mock.Mock(returncode=0, stdout="updated", stderr="")
        with mock.patch.object(pcs.subprocess, "run", return_value=completed) as runner:
            updated, _ = pcs.change_admin_password(
                "correct horse battery staple",
                "new correct horse battery staple",
            )
        self.assertTrue(updated)
        command = runner.call_args.args[0]
        self.assertNotIn("correct horse battery staple", command)
        self.assertNotIn("new correct horse battery staple", command)
        payload = json.loads(runner.call_args.kwargs["input"])
        self.assertEqual(payload["current_password"], "correct horse battery staple")
        self.assertEqual(payload["new_password"], "new correct horse battery staple")


class SessionTests(unittest.TestCase):
    def test_logout_destroys_session(self):
        store = pcs.SessionStore(b"a" * 32, ttl=60)
        cookie, _ = store.create()
        session_id, session = store.validate(cookie)
        self.assertIsNotNone(session)
        store.destroy(session_id)
        self.assertEqual(store.validate(cookie), (None, None))

    def test_tampered_session_is_rejected(self):
        store = pcs.SessionStore(b"a" * 32, ttl=60)
        cookie, _ = store.create()
        self.assertEqual(store.validate(cookie + "x"), (None, None))

    def test_expired_session_is_rejected(self):
        store = pcs.SessionStore(b"a" * 32, ttl=0)
        cookie, _ = store.create()
        self.assertEqual(store.validate(cookie), (None, None))

    def test_password_rotation_destroys_all_sessions(self):
        store = pcs.SessionStore(b"a" * 32, ttl=60)
        first_cookie, _ = store.create()
        second_cookie, _ = store.create()
        store.destroy_all()
        self.assertEqual(store.validate(first_cookie), (None, None))
        self.assertEqual(store.validate(second_cookie), (None, None))


class PublicDataTests(unittest.TestCase):
    def test_public_contract_removes_unapproved_fields(self):
        sanitized = pcs.sanitize_public_dashboard(PUBLIC_DATA)
        self.assertNotIn("imei", sanitized["cellular"])
        self.assertEqual(sanitized["gnss"]["coordinates"], "38.123456, -77.123456")
        self.assertEqual(sanitized["gnss"]["grid_square"], "FM18kc")

    def test_offline_mode_is_warning_card_but_healthy_overall_header(self):
        offline = deepcopy(PUBLIC_DATA)
        offline["offline"] = True
        offline["overall"] = "ok"
        offline["network"].update({
            "status": "warn",
            "offline": True,
            "internet_available": False,
            "uplink_type": "Offline",
        })
        sanitized = pcs.sanitize_public_dashboard(offline)
        page = pcs.render_public_page(sanitized).decode("utf-8")
        self.assertTrue(sanitized["offline"])
        self.assertEqual(sanitized["overall"], "ok")
        self.assertEqual(sanitized["network"]["status"], "warn")
        self.assertIn('<span class="badge ok">OK - Offline</span>', page)
        self.assertIn('<span class="badge warn">WARN</span>', page)

    def test_openwrt_router_offline_is_a_hard_fault(self):
        router_offline = deepcopy(PUBLIC_DATA)
        router_offline["overall"] = "bad"
        router_offline["network"].update({
            "status": "bad",
            "openwrt_online": False,
        })
        sanitized = pcs.sanitize_public_dashboard(router_offline)
        page = pcs.render_public_page(sanitized).decode("utf-8")
        self.assertEqual(sanitized["overall"], "bad")
        self.assertEqual(sanitized["network"]["status"], "bad")
        self.assertIn('<span class="badge bad">BAD</span>', page)
        self.assertIn("OpenWrt AP online", page)
        self.assertIn(">No<", page)

        collector = (ROOT / "scripts" / "pcs-web-action.sh").read_text(encoding="utf-8")
        self.assertIn("router_offline = not openwrt_online", collector)
        self.assertIn("if not network_core_ok or router_offline", collector)
        self.assertIn("OpenWrt AP / switch offline at 10.42.0.2", collector)

    def test_admin_header_uses_offline_suffix_without_bad_status(self):
        offline = deepcopy(ADMIN_DATA)
        offline["offline"] = True
        page = pcs.render_admin_page(offline, "csrf-token").decode("utf-8")
        self.assertIn('<span class="badge ok">OK - Offline</span>', page)

    def test_aprs_card_is_hidden_until_hardware_profile_is_active(self):
        staged = deepcopy(PUBLIC_DATA)
        staged["aprs"] = {
            "configured": False,
            "status": "ok",
            "service": "staged / disabled",
            "radio": "waiting for hardware",
            "frequency": "not selected",
            "tx_state": "disabled during staging",
        }
        self.assertNotIn("APRS / Packet", pcs.render_public_page(staged).decode("utf-8"))

        active = deepcopy(PUBLIC_DATA)
        active["aprs"] = {
            "configured": True,
            "status": "ok",
            "service": "active",
            "radio": "USB audio / receive-only",
            "callsign": "W8IJC-10",
            "role": "digi-IGate / GPS tracker",
            "frequency": "144.550 MHz",
            "modem": "1200 baud AFSK",
            "aprs_is_profile": "two-way via noam.aprs2.net; all eligible RF to APRS-IS",
            "digipeater": "WIDE1-1 only (fill-in)",
            "beacon": "GPS every 10 minutes",
            "kiss": "AGW 10.42.0.1:8000 / KISS 10.42.0.1:8001",
            "fx25": "disabled",
            "packets": "3 last hour / 12 last 24h",
            "last_heard": "2026-08-17T20:15:00Z",
            "tx_state": "enabled",
            "aprs_is_passcode": "must-not-render",
            "ptt_gpio": "must-not-render",
            "audio_device": "must-not-render",
        }
        sanitized = pcs.sanitize_public_dashboard(active)
        page = pcs.render_public_page(sanitized).decode("utf-8")
        self.assertNotIn("radio", sanitized["aprs"])
        self.assertNotIn("aprs_is_passcode", sanitized["aprs"])
        self.assertNotIn("ptt_gpio", sanitized["aprs"])
        self.assertNotIn("audio_device", sanitized["aprs"])
        self.assertIn("APRS / Packet", page)
        self.assertIn("W8IJC-10", page)
        self.assertIn("144.550 MHz", page)
        self.assertIn("two-way via noam.aprs2.net; all eligible RF to APRS-IS", page)
        self.assertIn("WIDE1-1 only (fill-in)", page)
        self.assertIn("AGW 10.42.0.1:8000 / KISS 10.42.0.1:8001", page)
        self.assertIn("3 last hour / 12 last 24h", page)
        self.assertIn("2026-08-17T20:15:00Z", page)
        self.assertNotIn("USB audio", page)
        self.assertNotIn("must-not-render", page)

    def test_meshtastic_card_is_privacy_safe_and_hidden_until_active(self):
        staged = deepcopy(PUBLIC_DATA)
        staged["meshtastic"] = {
            "configured": False,
            "status": "ok",
            "service": "staged / disabled",
        }
        self.assertNotIn("Meshtastic / MQTT", pcs.render_public_page(staged).decode("utf-8"))

        active = deepcopy(PUBLIC_DATA)
        active["meshtastic"] = {
            "configured": True,
            "status": "ok",
            "service": "active",
            "node": "W8IJC PCS Portable Node (IJC1)",
            "hardware": "RAK4631",
            "firmware": "2.7.26.54e0d8d",
            "transport": "usb-serial",
            "radio_link": "connected",
            "mqtt": "connected",
            "broker": "mqtt.neomesh.org:1883 (plaintext)",
            "downlink_filters": 2,
            "mqtt_activity": "4 up / 2 down",
            "mesh_activity": "12 RX / 7 TX",
            "remote_nodes": "2 recent / 3 observed",
            "last_heard": "2026-08-24T03:30:00+00:00",
            "gpsd_position": "GPSD active; 4 sent; last 2m ago",
            "case_environment": "83.2 F / 60.5% RH",
            "utilization": "channel 1.5% / TX 0.1%",
            "power": "external; 4.242 V",
            "mqtt_password": "must-not-render",
            "subscription_topics": "must-not-render",
            "remote_identities": "must-not-render",
            "position_coordinates": "must-not-render",
        }
        sanitized = pcs.sanitize_public_dashboard(active)
        page = pcs.render_public_page(sanitized).decode("utf-8")
        self.assertIn("Meshtastic / MQTT", page)
        self.assertIn("W8IJC PCS Portable Node (IJC1)", page)
        self.assertIn("mqtt.neomesh.org:1883 (plaintext)", page)
        self.assertIn("GPSD active; 4 sent; last 2m ago", page)
        self.assertNotIn("mqtt_password", sanitized["meshtastic"])
        self.assertNotIn("subscription_topics", sanitized["meshtastic"])
        self.assertNotIn("remote_identities", sanitized["meshtastic"])
        self.assertNotIn("position_coordinates", sanitized["meshtastic"])
        self.assertNotIn("must-not-render", page)

    def test_meshtastic_admin_actions_are_fixed_and_confirm_restart(self):
        self.assertIn("meshtastic-status", pcs.ACTION_MAP)
        self.assertIn("restart-meshtastic", pcs.ACTION_MAP)
        self.assertIn("restart-meshtastic", pcs.DANGEROUS_ACTIONS)
        self.assertIn("restart-meshtastic", pcs.ACTION_CONFIRMS)
        dispatcher = (ROOT / "scripts" / "pcs-web-action.sh").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "setup-pcs-control-panel.sh").read_text(encoding="utf-8")
        self.assertIn("MESHTASTIC_STATUS_FILE", dispatcher)
        self.assertIn("meshtastic_status_action()", dispatcher)
        self.assertIn("restart_meshtastic_action()", dispatcher)
        self.assertIn("meshtastic-status)", dispatcher)
        self.assertIn("restart-meshtastic)", dispatcher)
        self.assertIn("${DISPATCHER_DST} meshtastic-status", installer)
        self.assertIn("${DISPATCHER_DST} restart-meshtastic", installer)


class RouteSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        credential = str(Path(cls.tempdir.name) / "admin.json")
        pcs.write_password_record(credential, "correct horse battery staple")
        pcs.AUTH = pcs.AuthManager(credential)
        pcs.SESSIONS = pcs.SessionStore(b"b" * 32, ttl=120)
        pcs.LOGIN_LIMITER = pcs.LoginLimiter()
        cls.action_mock = mock.Mock(return_value=(0, "action complete"))
        cls.password_change_mock = mock.Mock(return_value=(True, "Password updated."))
        cls.patchers = [
            mock.patch.object(pcs, "get_public_dashboard", return_value=pcs.sanitize_public_dashboard(PUBLIC_DATA)),
            mock.patch.object(pcs, "get_admin_dashboard", return_value=ADMIN_DATA),
            mock.patch.object(pcs, "run_action", cls.action_mock),
            mock.patch.object(pcs, "change_admin_password", cls.password_change_mock),
        ]
        for patcher in cls.patchers:
            patcher.start()
        cls.server = pcs.ReusableThreadingHTTPServer(("127.0.0.1", 0), pcs.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        for patcher in reversed(cls.patchers):
            patcher.stop()
        cls.tempdir.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read().decode("utf-8")
        response_headers = response.headers
        connection.close()
        return response.status, response_headers, content

    def login(self):
        status, headers, page = self.request("GET", "/admin/login")
        self.assertEqual(status, 200)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        login_cookie = headers.get("Set-Cookie").split(";", 1)[0]
        body = urlencode({"csrf": csrf, "password": "correct horse battery staple"})
        status, headers, _ = self.request(
            "POST",
            "/admin/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Cookie": login_cookie},
        )
        self.assertEqual(status, 303)
        session_cookie = next(
            value.split(";", 1)[0]
            for value in headers.get_all("Set-Cookie")
            if value.startswith("pcs_admin_session=")
        )
        return session_cookie

    def test_public_home_has_prominent_admin_login_and_allowed_coordinates(self):
        status, _, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(page.count("Admin Login"), 2)
        self.assertIn("38.123456, -77.123456", page)
        self.assertIn("FM18kc", page)
        self.assertNotIn("must-not-render", page)

        status, _, public_json = self.request("GET", "/api/public-status")
        self.assertEqual(status, 200)
        self.assertNotIn("imei", public_json.lower())
        self.assertIn("FM18kc", public_json)

    def test_unauthenticated_admin_get_redirects_to_login(self):
        status, headers, _ = self.request("GET", "/admin/")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login")

    def test_unauthenticated_direct_action_post_is_rejected(self):
        self.action_mock.reset_mock()
        body = urlencode({"csrf": "invalid", "action": "status"})
        status, headers, _ = self.request("POST", "/admin/run", body, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login")
        self.action_mock.assert_not_called()

    def test_unauthenticated_password_change_is_rejected(self):
        self.password_change_mock.reset_mock()
        body = urlencode({
            "csrf": "invalid",
            "current_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
            "confirm_password": "new correct horse battery staple",
        })
        status, headers, _ = self.request("POST", "/admin/password", body, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login")
        self.password_change_mock.assert_not_called()

    def test_incorrect_password_is_rejected(self):
        status, headers, page = self.request("GET", "/admin/login")
        self.assertEqual(status, 200)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        login_cookie = headers.get("Set-Cookie").split(";", 1)[0]
        body = urlencode({"csrf": csrf, "password": "definitely the wrong password"})
        status, _, page = self.request(
            "POST",
            "/admin/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Cookie": login_cookie},
        )
        self.assertEqual(status, 401)
        self.assertIn("Incorrect administrator password", page)

    def test_oversized_login_password_is_rejected(self):
        status, headers, page = self.request("GET", "/admin/login")
        self.assertEqual(status, 200)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        login_cookie = headers.get("Set-Cookie").split(";", 1)[0]
        body = urlencode({"csrf": csrf, "password": "x" * (pcs.MAX_PASSWORD_LENGTH + 1)})
        status, _, page = self.request(
            "POST",
            "/admin/login",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Cookie": login_cookie},
        )
        self.assertEqual(status, 401)
        self.assertIn("Incorrect administrator password", page)

    def test_login_csrf_action_and_logout_flow(self):
        session_cookie = self.login()
        status, _, admin_page = self.request("GET", "/admin/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('<main class="admin-main">', admin_page)
        self.assertIn("repeat(6,minmax(0,1fr))", admin_page)
        self.assertIn("Change Admin Password", admin_page)
        self.assertIn("forgotten password cannot be recovered", admin_page)
        csrf = re.search(r'name="csrf" value="([^"]+)"', admin_page).group(1)

        self.action_mock.reset_mock()
        bad_body = urlencode({"csrf": "wrong", "action": "status"})
        status, _, _ = self.request("POST", "/admin/run", bad_body, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 403)
        self.action_mock.assert_not_called()

        good_body = urlencode({"csrf": csrf, "action": "status"})
        status, _, page = self.request("POST", "/admin/run", good_body, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("action complete", page)
        self.action_mock.assert_called_once_with("status")

        logout_body = urlencode({"csrf": csrf})
        status, _, _ = self.request("POST", "/admin/logout", logout_body, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 303)
        status, headers, _ = self.request("GET", "/admin/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login")

    def test_authenticated_password_change_flow(self):
        session_cookie = self.login()
        status, _, page = self.request("GET", "/admin/password", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("Forgotten passwords cannot be changed here", page)
        self.assertIn("--reset-admin-password", page)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

        self.password_change_mock.reset_mock()
        mismatch = urlencode({
            "csrf": csrf,
            "current_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
            "confirm_password": "different correct horse battery staple",
        })
        status, _, page = self.request("POST", "/admin/password", mismatch, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 400)
        self.assertIn("did not match", page)
        self.password_change_mock.assert_not_called()

        wrong_current = urlencode({
            "csrf": csrf,
            "current_password": "incorrect current password",
            "new_password": "new correct horse battery staple",
            "confirm_password": "new correct horse battery staple",
        })
        status, _, page = self.request("POST", "/admin/password", wrong_current, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 401)
        self.assertIn("Current administrator password was incorrect", page)
        self.password_change_mock.assert_not_called()

        valid = urlencode({
            "csrf": csrf,
            "current_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
            "confirm_password": "new correct horse battery staple",
        })
        status, headers, _ = self.request("POST", "/admin/password", valid, {"Content-Type": "application/x-www-form-urlencoded", "Cookie": session_cookie})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login?changed=1")
        self.password_change_mock.assert_called_once_with(
            "correct horse battery staple",
            "new correct horse battery staple",
        )
        status, headers, _ = self.request("GET", "/admin/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/admin/login")


if __name__ == "__main__":
    unittest.main()
