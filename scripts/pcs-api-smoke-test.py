#!/usr/bin/env python3

"""Run safe, read-only smoke checks against a PCS Stats API endpoint.

The script deliberately never invokes an administrative action.  It is meant
for a supervised deployment or client-integration check and accepts a bearer
token only through an environment variable so it cannot land in command
history or normal process arguments.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import ssl
import sys
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit


JSON_CONTENT_TYPES = {
    "application/json",
    "application/problem+json",
    "application/vnd.pcs.v1+json",
}
SENSITIVE_KEYS = {
    "access_token",
    "admin_password",
    "api_key",
    "apikey",
    "coordinates",
    "credential",
    "credentials",
    "endpoint",
    "latitude",
    "longitude",
    "password",
    "private_key",
    "privatekey",
    "preshared_key",
    "presharedkey",
    "secret",
    "token",
    "token_sha256",
}


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class SmokeFailure(RuntimeError):
    """A stable, user-safe smoke-test failure."""


Request = Callable[[str, str, Mapping[str, str], bytes | None], Response]


def _header(response: Response, name: str) -> str:
    wanted = name.lower()
    return next((value for key, value in response.headers.items() if key.lower() == wanted), "")


def _json(response: Response, label: str) -> dict:
    content_type = _header(response, "Content-Type").split(";", 1)[0].strip().lower()
    if content_type not in JSON_CONTENT_TYPES and not content_type.endswith("+json"):
        raise SmokeFailure(f"{label}: unexpected content type")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"{label}: invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{label}: response is not a JSON object")
    return payload


def _assert_status(response: Response, expected: int, label: str) -> None:
    if response.status != expected:
        problem = ""
        try:
            payload = _json(response, label)
            code = payload.get("code")
            if isinstance(code, str):
                problem = f" ({code})"
        except SmokeFailure:
            pass
        raise SmokeFailure(f"{label}: expected HTTP {expected}, got {response.status}{problem}")


def _find_sensitive_keys(value: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in SENSITIVE_KEYS:
                found.append(path)
            found.extend(_find_sensitive_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_sensitive_keys(child, f"{prefix}[{index}]"))
    return found


def run_checks(request: Request, token: str | None = None) -> list[str]:
    """Run the non-mutating API checks and return human-readable check names."""

    checks: list[str] = []
    empty_headers = {"Accept": "application/vnd.pcs.v1+json, application/problem+json"}

    discovery_response = request("GET", "/api/v1", empty_headers, None)
    _assert_status(discovery_response, 200, "public discovery")
    discovery = _json(discovery_response, "public discovery")
    if discovery.get("api_version") != "v1" or discovery.get("resource") != "discovery":
        raise SmokeFailure("public discovery: unsupported API contract")
    resources = discovery.get("resources")
    required_resources = {
        "status",
        "network",
        "cellular",
        "time",
        "gps",
        "aprs",
        "meshtastic",
        "pistar",
        "storage",
        "services",
    }
    if not isinstance(resources, dict) or not required_resources.issubset(resources):
        raise SmokeFailure("public discovery: required resources are missing")
    if discovery.get("actions") != "/api/v1/actions" or discovery.get("password") != "/api/v1/admin/password":
        raise SmokeFailure("public discovery: administrative routes are missing")
    if discovery.get("write_actions") is not False:
        raise SmokeFailure("public discovery: unauthenticated writes are enabled")
    checks.append("public discovery")

    public_status_response = request("GET", "/api/v1/status", empty_headers, None)
    _assert_status(public_status_response, 200, "public status")
    public_status = _json(public_status_response, "public status")
    if public_status.get("access") != "public" or public_status.get("details") is not None:
        raise SmokeFailure("public status: response is not redacted")
    sensitive = _find_sensitive_keys(public_status)
    if sensitive:
        raise SmokeFailure("public status: sensitive fields present")
    checks.append("public redaction")

    action_response = request("GET", "/api/v1/actions", empty_headers, None)
    _assert_status(action_response, 403, "unauthenticated actions")
    checks.append("unauthenticated actions blocked")

    password_response = request(
        "POST",
        "/api/v1/admin/password",
        {**empty_headers, "Content-Type": "application/json"},
        b"{}",
    )
    _assert_status(password_response, 403, "unauthenticated password change")
    checks.append("unauthenticated password change blocked")

    if token is not None:
        auth_headers = {**empty_headers, "Authorization": f"Bearer {token}"}
        auth_discovery_response = request("GET", "/api/v1", auth_headers, None)
        _assert_status(auth_discovery_response, 200, "authenticated discovery")
        auth_discovery = _json(auth_discovery_response, "authenticated discovery")
        if auth_discovery.get("write_actions") is not True:
            raise SmokeFailure("authenticated discovery: admin action scope is missing")

        auth_status_response = request("GET", "/api/v1/status", auth_headers, None)
        _assert_status(auth_status_response, 200, "authenticated status")
        auth_status = _json(auth_status_response, "authenticated status")
        if auth_status.get("access") != "authenticated":
            raise SmokeFailure("authenticated status: token was not accepted")
        if not isinstance(auth_status.get("details"), dict):
            raise SmokeFailure("authenticated status: details are missing")

        auth_actions_response = request("GET", "/api/v1/actions", auth_headers, None)
        _assert_status(auth_actions_response, 200, "authenticated actions")
        auth_actions = _json(auth_actions_response, "authenticated actions")
        if auth_actions.get("resource") != "actions" or not isinstance(auth_actions.get("actions"), list):
            raise SmokeFailure("authenticated actions: invalid action catalog")
        checks.append("authenticated status and action catalog")

    return checks


def _request_factory(base_url: str, ca_cert: str | None, timeout: float) -> Request:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SmokeFailure("base URL must be an HTTPS origin without a path, query, or fragment")
    if parsed.username or parsed.password:
        raise SmokeFailure("base URL must not contain credentials")

    context = ssl.create_default_context()
    if ca_cert:
        try:
            context.load_verify_locations(cafile=ca_cert)
        except (OSError, ssl.SSLError) as exc:
            raise SmokeFailure("unable to load the supplied CA certificate") from exc
    connection_class = http.client.HTTPSConnection

    def request(method: str, path: str, headers: Mapping[str, str], body: bytes | None) -> Response:
        connection = connection_class(parsed.hostname, parsed.port or 443, context=context, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            return Response(
                response.status,
                {key: value for key, value in response.getheaders()},
                response.read(256 * 1024),
            )
        except (OSError, http.client.HTTPException) as exc:
            raise SmokeFailure(f"{method} {path}: HTTPS connection failed") from exc
        finally:
            connection.close()

    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run safe PCS Stats API smoke checks")
    parser.add_argument("--base-url", required=True, help="HTTPS origin, for example https://192.168.50.236:9443")
    parser.add_argument("--ca-cert", help="operator-supplied CA or self-signed certificate file")
    parser.add_argument("--token-env", help="environment variable containing an admin bearer token")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds (default: 10)")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 120:
        parser.error("--timeout must be greater than 0 and no more than 120 seconds")
    token = None
    if args.token_env:
        token = os.environ.get(args.token_env)
        if not token:
            parser.error(f"token environment variable is empty or unset: {args.token_env}")

    try:
        checks = run_checks(_request_factory(args.base_url, args.ca_cert, args.timeout), token=token)
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PCS Stats API smoke checks passed:")
    for check in checks:
        print(f"- {check}")
    if token is None:
        print("- authenticated checks not requested (no token supplied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
