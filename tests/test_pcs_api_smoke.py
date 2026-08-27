import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "pcs-api-smoke-test.py"
SPEC = importlib.util.spec_from_file_location("pcs_api_smoke_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def response(status, payload, content_type="application/vnd.pcs.v1+json"):
    return MODULE.Response(
        status=status,
        headers={"Content-Type": content_type},
        body=json.dumps(payload).encode("utf-8"),
    )


def public_discovery():
    return {
        "api_version": "v1",
        "resource": "discovery",
        "resources": {
            name: f"/api/v1/{name}"
            for name in ("status", "network", "cellular", "time", "gps", "aprs", "meshtastic", "pistar", "storage", "services")
        },
        "actions": "/api/v1/actions",
        "password": "/api/v1/admin/password",
        "write_actions": False,
    }


class SmokeTest(unittest.TestCase):
    def make_request(self, authenticated=False, public_status=None):
        calls = []
        public_status = public_status or {
            "api_version": "v1",
            "resource": "status",
            "access": "public",
            "details": None,
            "health": {"severity": "ok"},
        }

        def request(method, path, headers, body):
            calls.append((method, path, headers, body))
            has_token = "Authorization" in headers
            if path == "/api/v1":
                payload = dict(public_discovery())
                if has_token:
                    payload["write_actions"] = True
                return response(200, payload)
            if path == "/api/v1/status":
                payload = dict(public_status)
                if has_token:
                    payload["access"] = "authenticated"
                    payload["details"] = {"network": {"status": "ok"}}
                return response(200, payload)
            if path == "/api/v1/actions":
                if not has_token:
                    return response(403, {"code": "insufficient_scope"}, "application/problem+json")
                return response(200, {"resource": "actions", "actions": [{"name": "status"}]})
            if path == "/api/v1/admin/password":
                return response(403, {"code": "insufficient_scope"}, "application/problem+json")
            raise AssertionError(path)

        return request, calls

    def test_public_checks_pass_without_token(self):
        request, calls = self.make_request()
        checks = MODULE.run_checks(request)
        self.assertEqual(
            checks,
            [
                "public discovery",
                "public redaction",
                "unauthenticated actions blocked",
                "unauthenticated password change blocked",
            ],
        )
        self.assertEqual([call[0:2] for call in calls], [
            ("GET", "/api/v1"),
            ("GET", "/api/v1/status"),
            ("GET", "/api/v1/actions"),
            ("POST", "/api/v1/admin/password"),
        ])

    def test_authenticated_checks_use_token_without_printing_or_mutation(self):
        request, calls = self.make_request(authenticated=True)
        checks = MODULE.run_checks(request, token="pcs_ro_test-token")
        self.assertIn("authenticated status and action catalog", checks)
        self.assertEqual(calls[-3][2]["Authorization"], "Bearer pcs_ro_test-token")
        self.assertEqual(calls[-3][0:2], ("GET", "/api/v1"))
        self.assertTrue(all(call[0] in {"GET", "POST"} for call in calls))
        self.assertNotIn("challenge", " ".join(call[1] for call in calls))

    def test_public_status_sensitive_field_fails(self):
        request, _ = self.make_request(public_status={
            "api_version": "v1",
            "resource": "status",
            "access": "public",
            "details": None,
            "private_key": "redacted",
        })
        with self.assertRaisesRegex(MODULE.SmokeFailure, "sensitive fields present"):
            MODULE.run_checks(request)

    def test_missing_resource_fails(self):
        request, _ = self.make_request()

        def incomplete(method, path, headers, body):
            if path == "/api/v1":
                payload = public_discovery()
                payload["resources"].pop("gps")
                return response(200, payload)
            return request(method, path, headers, body)

        with self.assertRaisesRegex(MODULE.SmokeFailure, "required resources"):
            MODULE.run_checks(incomplete)

    def test_request_factory_rejects_non_https_and_paths(self):
        with self.assertRaisesRegex(MODULE.SmokeFailure, "HTTPS origin"):
            MODULE._request_factory("http://127.0.0.1:9443", None, 1)
        with self.assertRaisesRegex(MODULE.SmokeFailure, "HTTPS origin"):
            MODULE._request_factory("https://127.0.0.1:9443/api/v1", None, 1)
        with self.assertRaisesRegex(MODULE.SmokeFailure, "credentials"):
            MODULE._request_factory("https://user:pass@127.0.0.1:9443", None, 1)


if __name__ == "__main__":
    unittest.main()
