# PCS Stats API

The PCS Stats API status/pairing canary was deployed on 2026-08-26. The complete
administrative action and password-management expansion described below was
deployed under a guarded rollback boundary on 2026-08-27. The service is
enabled on TCP 9443 behind explicit interface/source rules and uses the current
one-year PCS-local test certificate. Linux-native tests, exact installed-source
hashes, source-firewall and TLS inspection, trusted home-LAN smoke tests, all
three certificate identities, and the full PCS self-test passed. The legacy
desktop token remains intentionally limited to `stats:read`; it was not
silently elevated during deployment.

## Access model

Every version 1 endpoint supports two response views:

- A request without an `Authorization` header receives only the API's strict
  public allowlist. This is a subset of information approved for the public
  PCS homepage. Precise GNSS coordinates, private MQTT details, peer endpoints,
  modem identity, and credential-shaped fields are omitted.
- A request with a valid `Authorization: Bearer ...` token receives the same
  stable public data plus the related administrative status cards and, where
  applicable, authenticated client/network details.

An invalid supplied token returns `401`; it never silently downgrades to the
public view. Authenticated status still excludes passwords, passcodes, private
or preshared keys, credentials, API keys, access tokens, raw logs, and
arbitrary command output. "Authenticated" means all existing admin-visible
status information, not secrets.

The credential-exchange endpoint is `POST /api/v1/pair`. It accepts the
password over TLS during enrollment, verifies it using the existing PCS
password record, and returns one separately revocable `stats:read` +
`admin:actions` + `admin:password` bearer token. The password is sent to the
fixed helper over standard input, is never placed in a command line, and is
not stored by the API. The Android client must discard the password after
enrollment and store only the returned token in Android Keystore. The local
candidate also provides `POST /api/v1/admin/password` for an authenticated
password change; it requires the current password and the `admin:password`
scope. The replacement password must be 12-1024 characters, matching the PCS
control-panel helper.

## Version 1 endpoints

```text
GET /api/v1/status
GET /api/v1/network
GET /api/v1/cellular
GET /api/v1/time
GET /api/v1/gps
GET /api/v1/aprs
GET /api/v1/meshtastic
GET /api/v1/pistar
GET /api/v1/storage
GET /api/v1/services
POST /api/v1/pair
GET /api/v1/actions
POST /api/v1/actions/{action}/challenge
POST /api/v1/actions/{action}
POST /api/v1/admin/password
```

Responses use `application/vnd.pcs.v1+json`, UTC timestamps, explicit `null`
for known unavailable values, and separate machine-readable health severity.
Public responses set `access` to `public` and `details` to `null`.
Authenticated responses set `access` to `authenticated` and populate
`details` from the existing administrative dashboard model.

`GET /api/v1` returns a collector-free discovery document listing these
resources, pairing, action-catalog URL, supported methods, content type, and
public/authenticated access modes. The machine-readable OpenAPI 3.1 contract is
[`pcs-stats-api-v1.openapi.json`](pcs-stats-api-v1.openapi.json). Contract tests
compare its paths, resource identifiers, authentication declarations, content
types, and required response envelope directly with the implementation.

`POST /api/v1/pair` requires exactly this JSON shape:

```json
{"admin_password":"ADMIN_PASSWORD","device_id":"andre-phone"}
```

On success it returns `201` and the raw token exactly once. Device IDs are
1-64 safe letters, digits, dots, underscores, or hyphens and must be unique.
An incorrect password returns `401`, an existing ID returns `409`, and pairing
is limited to five attempts per source address per five minutes. Do not use
`curl -k` for real enrollment; a client must validate the PCS certificate.

## Administrative actions

Pairing grants `admin:actions` and `admin:password` in addition to `stats:read`. A paired device can
`GET /api/v1/actions` to receive the exact current control-panel action catalog.
Read-only buttons accept `POST {}`. Every state-changing button first requires
`POST /api/v1/actions/{action}/challenge` with `{}`, then one execution request
with `{"confirmation":"ACTION_NAME","challenge":"..."}`. The challenge is
bound to the device token, expires after 60 seconds, and is consumed before
execution so retries cannot repeat a dangerous action. Actions are serialized,
bounded, audited to the service journal, and return a structured result with a
bounded output field. A `stats:read`-only token cannot list or invoke actions.

`POST /api/v1/admin/password` is a dedicated exact-field route, separate from
the action dispatcher. It requires
`{"current_password":"...","new_password":"..."}` and calls only the
root-owned fixed password helper. Password values are never logged or
returned. The replacement password must be 12-1024 characters. A wrong
current password returns `401`; a rejected new password returns `409`;
malformed input returns `400`.

The action dispatcher is still a fixed allowlist matching the web panel: status,
self-test, Wi-Fi/cellular controls and tests, storage and backup operations,
Meshtastic restart, service/time/GPS restarts, restart logs, reboot, and
shutdown. There is no arbitrary action name, shell argument, or API-selected
command path. Query parameters are rejected in version 1. Other unsupported
`POST`, `PUT`, `PATCH`, and `DELETE` requests return `405`.

Errors use `application/problem+json` with a stable `code` and a generated
request identifier.

## Collection and limits

The service calls only the fixed `dashboard-public-json` and `dashboard-json`
collector actions or one of the named control-panel actions. It cannot pass
user-controlled shell arguments or command paths. Collection and actions have
bounded timeouts; successful snapshots are cached briefly; public,
failed-authentication, authenticated, pairing, and action requests have
separate fixed-window limits.

TLS handshakes run in daemon worker threads with a 10-second handshake timeout;
accepted HTTP clients receive a 30-second socket timeout and the listener keeps
a 32-connection queue. A client that opens TCP and never completes TLS therefore
cannot block the API accept loop or starve PCS-LAN and WireGuard callers.

## Token management

The helper issues a random token once and stores only its SHA-256 digest:

```bash
python3 scripts/pcs_api_token.py --file /tmp/pcs-api-tokens.json issue test-phone
python3 scripts/pcs_api_token.py --file /tmp/pcs-api-tokens.json list
python3 scripts/pcs_api_token.py --file /tmp/pcs-api-tokens.json revoke test-phone
```

Do not paste a real token into Git, shell history, issue text, or logs. The
deployed digest store is outside the repository as `root:pcs-api` mode `0640`.
The initial raw desktop token remains only in PCS's ignored, mode-`0600`
`private-config` directory. Revoke a lost or retired device without changing
the PCS administrator password:

```bash
sudo /usr/local/sbin/pcs-stats-api-setup --revoke-token andre-phone
```

Re-enrollment intentionally requires revocation first; silently replacing an
existing device ID could hide a lost credential that remains valid.

## Runtime packaging

The default-disabled setup workflow used for the supervised canary is:

```bash
./scripts/setup-pcs-stats-api.sh --prepare
./scripts/setup-pcs-stats-api.sh --validate-policy config/pcs-stats-api.example.conf
./scripts/setup-pcs-stats-api.sh --import-policy /path/to/deployment-policy.conf
./scripts/setup-pcs-stats-api.sh --validate-tls /path/to/server.crt /path/to/server.key /path/to/deployment-policy.conf
./scripts/setup-pcs-stats-api.sh --import-tls /path/to/server.crt /path/to/server.key
./scripts/setup-pcs-stats-api.sh --issue-token phone
./scripts/setup-pcs-stats-api.sh --revoke-token phone
./scripts/setup-pcs-stats-api.sh --activate
./scripts/setup-pcs-stats-api.sh --check
./scripts/setup-pcs-stats-api.sh --deactivate
./scripts/setup-pcs-stats-api.sh --rollback
./scripts/setup-pcs-stats-api.sh --help
```

`--prepare` installs a dedicated `pcs-api` runtime account, the application,
token helper, source firewall, hardened service units, and sudoers rules that
permit only `dashboard-public-json`, `dashboard-json`, the exact fixed
`pair-from-stdin` token operation, and the named control-panel action commands.
It requires the root-owned password helper already installed by
`setup-pcs-control-panel.sh` and never removes that shared helper on rollback.
They do not permit API-selected token commands, token paths, shell arguments, or
PCS actions outside that allowlist. Preparation
finishes with both services disabled and inactive. It does not create a
certificate, issue a token, open a port, or alter the base installer.

The deployment policy uses explicit `interface=source-network` mappings. It
must retain `eth0=10.42.0.0/24`; WireGuard sources must be approved `/32`s. A
trusted home Wi-Fi subnet may be added on `wlan0` only in the deployment-local
policy. `wwan0`, public networks, default routes, and broad WireGuard prefixes
are rejected. Activation applies the port-9443 firewall before starting the
API, checks the public contract over TLS, and deactivates both services on any
failure.

TLS is operator-supplied. Import validates certificate expiry, parses the
private key, proves that the key matches the certificate, and checks every
configured IP/DNS identity against the certificate SAN. The private key is
stored outside Git as `root:pcs-api` mode `0640`. The repository does not yet
choose a public CA, private CA, or Android trust-bootstrap policy on the
operator's behalf.

`--rollback` removes programs, service units, firewall integration, and the
sudoers rule while preserving the deployment-local policy, TLS material, and
hashed token records for deliberate recovery or removal.

Prepared deployments also install `/usr/local/sbin/pcs-stats-api-setup`, so
`--check`, `--deactivate`, and `--rollback` remain available after the
temporary staging directory or original checkout is unavailable.

## Client smoke test

[`scripts/pcs-api-smoke-test.py`](../scripts/pcs-api-smoke-test.py) provides a
safe external verification pass after activation. It requires an HTTPS origin
and an operator-supplied CA or self-signed certificate, then checks discovery,
public redaction, and that unauthenticated action/password writes return `403`.
It does not request a challenge or perform any state-changing operation. Add
authenticated status and action-catalog checks by placing a temporary admin
bearer token in an environment variable and passing `--token-env`; the token
is never printed or written by the utility.

```bash
python3 scripts/pcs-api-smoke-test.py \
  --base-url https://192.168.50.236:9443 \
  --ca-cert /path/to/pcs-api-certificate.pem
```

## Verified state and remaining product gates

The API process is disabled unless `PCS_API_ENABLED=yes` and its `main()`
requires a TLS certificate and private key. Source existence alone does not
enable a listener. The 2026-08-27 administrative deployment passed 318 local
tests with 15 platform skips, 50 focused Linux-native tests on PCS, exact
installed-source hash comparison, trusted external discovery/public-redaction
and unauthenticated-write checks, firewall/TLS checks, certificate-valid
requests through `192.168.50.236`, `10.42.0.1`, and `10.6.0.7`, and a full PCS
self-test with 153 passes, no warnings, failures, or skips. Remaining product
gates are:

- implement the Android certificate trust/pinning and renewal UX around the
  current deployment-local certificate;
- an independent client test of TCP 9443 through each intended WireGuard peer;
- keep the live PCS checkout synchronized to the exact v1.4 release commit.

The operator-approved live full-scope pairing acceptance passed on 2026-08-27.
It verified authenticated status and action discovery, the read-only
`wifi-status` action, challenge issuance without execution, invalid
password-change rejection, token revocation, and a subsequent `401` for the
revoked credential. The temporary raw token was then deleted.
