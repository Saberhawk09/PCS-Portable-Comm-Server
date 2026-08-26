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
```

API requirements:

- a documented schema and content type on every response
- stable field names, explicit `null`/unknown semantics, and UTC timestamps
- bounded command execution and response time
- no precise GNSS position in an unauthenticated remote response
- no passwords, private keys, APRS-IS credentials, private MQTT details, raw
  modem identity, or unrestricted logs
- structured health severity distinct from human display text
- read-only authentication designed before administrative endpoints
- HTTPS appropriate for Android clients; WireGuard is defense in depth, not a
  reason to normalize cleartext credentials
- contract, authorization, timeout, and redaction tests

## Phase 3: Android read-only companion

Build a native Kotlin application using Jetpack Compose, coroutines/Flow,
OkHttp/Retrofit, and Material 3. The app tries the local PCS API first, then the
WireGuard address, and clearly shows `LOCAL`, `REMOTE`, or `OFFLINE`.

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

Add revocable per-device credentials issued through a one-time authenticated
pairing flow. Store the Android credential in hardware-backed Keystore when
available. Do not store the web administrator password.

Allowlisted API actions may include cellular connect/disconnect, APRS or
Meshtastic restart, self-test, diagnostic bundle creation, and carefully gated
system/Pi-Star power actions. There will be no arbitrary shell endpoint.

Every action needs:

- explicit authorization scope
- CSRF/replay protection appropriate to the token design
- request identifier and audit record
- bounded execution and a structured result
- confirmation proportional to impact
- Android biometric confirmation for reboot/shutdown and comparable actions
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
