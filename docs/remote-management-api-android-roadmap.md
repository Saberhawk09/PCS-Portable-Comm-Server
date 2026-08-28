# Remote Management, PCS API, and Android Companion Roadmap

## Product boundary

The end state is one PCS management model reachable over either the trusted
local PCS LAN or the WireGuard management tunnel. The Android app should call
a versioned PCS API. It should not open SSH sessions, run arbitrary commands,
scrape HTML, or own WireGuard keys in its first release.

Nothing in this roadmap is deployed merely because its design or source exists
in the repository. Each phase needs its own local tests, security review,
supervised deployment, and live verification before the next phase depends on
it.

```text
Android companion
       |
       +-- local PCS Wi-Fi ---------+
       |                            |
       +-- Android WireGuard app ---+--> HTTPS /api/v1 on PCS
                                            |
                                shared PCS service layer
                                  /         |          \
                              health     telemetry    actions
```

## Phase 1: WireGuard management plane

The local repository implementation is described in
[WireGuard Remote Management](wireguard-remote-management.md). Its deployment
gate is a proven split tunnel, authenticated handshake, no default/home-LAN
route, explicit PCS-LAN isolation, and authorized remote access across Wi-Fi
and manually controlled cellular.

The first Android release will rely on the established Android WireGuard app.
PCS Companion can detect that the remote address is reachable and may offer a
shortcut to Android VPN settings later, but it will not generate, import, or
store tunnel keys.

## Phase 2: read-only PCS API

Extract status collection from the current web control panel into a shared
service layer, then expose versioned JSON without duplicating shell-command
logic. Initial endpoints:

Development and a supervised canary deployment began on 2026-08-26. The
current source implements the versioned status contract as a separate,
default-disabled, TLS-only process. It also provides a rate-limited one-time
pairing exchange that verifies the existing administrator password and returns
a per-device `stats:read` + `admin:actions` + `admin:password` token without storing the password. Android trust,
discovery, and enrollment behavior is fixed in the
[Android Client Bootstrap Contract](pcs-android-client-bootstrap.md).
Unauthenticated requests receive only an explicit public allowlist; valid
revocable `stats:read` tokens add administrative status details without ever
returning credentials or private keys. The previously validated status/pairing
canary is installed and enabled on PCS behind explicit PCS-LAN, trusted-home-
Wi-Fi, and WireGuard source rules. The complete action/password expansion was
deployed under a guarded rollback boundary on 2026-08-27 and passed its public,
TLS/firewall, installed-hash, Linux-native, and full-appliance gates. Live
operator-approved full-scope pairing and non-mutating administrative acceptance
also passed on 2026-08-27, including verified revocation. The implementation is
released in PCS v1.4. The first local Android client now provides explicit
certificate import, fingerprint/SAN review, exact-certificate trust, and normal
hostname verification; its real-device gates remain open.
See [PCS Stats API](pcs-stats-api.md).

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

API requirements:

- a documented schema and content type on every response
- stable field names, explicit `null`/unknown semantics, and UTC timestamps
- bounded command execution and response time
- no precise GNSS position in an unauthenticated remote response
- no passwords, private keys, APRS-IS credentials, private MQTT details, raw
  modem identity, or unrestricted logs
- structured health severity distinct from human display text
- scoped authentication and replay-resistant confirmation before administrative endpoints
- HTTPS appropriate for Android clients; WireGuard is defense in depth, not a
  reason to normalize cleartext credentials
- contract, authorization, timeout, and redaction tests

## Phase 3: Android read-only companion

Build a native Kotlin application using Jetpack Compose, coroutines/Flow,
OkHttp, and Material 3. The app tries the home-LAN address, PCS local LAN, then
the WireGuard address, and clearly shows `LOCAL`, `REMOTE`, or `OFFLINE`.

The initial implementation exists locally under `android/pcs-companion`. It
builds public and authenticated status views, visibly timestamped offline
cache, strict TLS onboarding, Android-Keystore token storage, endpoint editing,
and dynamic rendering of the server action catalog. Local JVM tests, Android
lint, debug assembly, and minified release assembly pass. Operator
acceptance on 2026-08-28 confirmed the debug APK on a real Android device over
the trusted home LAN, direct PCS LAN, and cellular/WireGuard routes, including
successful backup synchronization and challenge-protected shutdown. PCS v1.4.1
bundles the first production-signed Companion APK (app v0.1.5). Credential
persistence/revocation testing and broader device-version coverage remain open.

The first overview should answer whether PCS is healthy without pretending
that planned sensors exist. It can show only currently measured data:

- overall health and uptime
- local/WireGuard reachability
- WAN type and cellular state/signal when available
- GPS fix/time-source state with privacy-aware location handling
- APRS, Meshtastic, Pi-Star, storage, service, and temperature health
- the exact self-test warning/failure summary

Room or another local store is optional for bounded history/cache. Offline data
must be visibly timestamped rather than presented as current.

## Phase 4: enrolled administration

Revocable per-device credentials, one-time authenticated pairing, and the
fixed control-panel action API are deployed in PCS v1.4.
The Android app must store the credential in
hardware-backed Keystore when available and must not store the web
administrator password.

The deployed backend exposes the complete existing control-panel action allowlist:
network and cellular controls, storage/backup operations, Meshtastic and
service/time/GPS restarts, self-test/status tools, restart logs, and carefully
gated reboot/shutdown. Future actions such as diagnostic bundles remain
separate additions. There will be no arbitrary shell endpoint.

Every action needs:

- explicit authorization scope
- CSRF/replay protection appropriate to the token design
- request identifier and audit record
- bounded execution and a structured result
- confirmation proportional to impact
- Android biometric or device-credential confirmation for every state-changing action
- tests proving that read-only tokens cannot invoke it

## Phase 5: telemetry, power, and alerts

Add history and thresholds only for sensors that are physically installed and
calibrated. INA226 rail values remain planned until the hardware, shunts,
addressing, calibration, and measurements are verified.

Local/VPN polling comes first. True push alerts while the phone and tunnel are
asleep require an outbound notification relay such as an operator-selected
MQTT/webhook/notification service. That dependency must be opt-in, documented,
privacy-reviewed, and nonessential to local PCS operation.

## Phase 6: radio-specific capabilities

Add concise APRS and Meshtastic views using stable service APIs. Prefer linking
to the full specialist interface rather than cloning it. Any message transmit,
RF enable/disable, map publication, or position-sharing feature needs an
independent authorization and supervised RF/privacy review.

## Cross-phase completion gates

A phase is complete only when source, tests, security documentation, recovery,
and the actual deployed behavior agree. Repository-only work is labeled local;
configured-but-inactive work is labeled configured; live claims require direct
evidence from the commissioned system.
