# Manual position fallback

Available in PCS v2.3.0. Installing or activating it on an existing appliance is a
separate deployment operation. No coordinates are enabled by default.

In Administration → Diagnostics → Location settings, enter WGS84 decimal-degree
latitude/longitude, optional elevation above sea level in metres, and enable
manual fallback. Confirm that the existing APRS/Meshtastic sharing configuration
may publish the supplied position. The page requires the existing administrator
session and CSRF token. Root validates inputs again and writes
`/etc/pcs/location.json` atomically with mode 0600. Invalid values preserve the
previous file. Coordinates are never command-line arguments or service logs.

## Selection and time

- With manual fallback disabled, use a fresh receiver fix or report unavailable.
- With fallback enabled, use **Manual** when GPS is absent, invalid or stale.
- After a fresh receiver fix remains valid for five seconds, automatically return
  to GPS. Repeated cached GPS epochs do not extend freshness. A fix expires after
  15 seconds without a new epoch; invalid/no-fix reports invalidate it immediately.
- **Persist manual position** keeps Manual selected even with a usable receiver.
  It survives restart and remains in effect until an administrator changes it.
- The receiver stays monitored continuously, including persistent mode. A failed
  receiver connection is retried every five seconds without disconnecting position
  clients. This changes no modem, antenna, receiver or RF settings.
- Clock discipline is independent of position selection, including persist mode.
  Existing Chrony configuration prefers usable receiver GPS SHM time, falls back
  to Internet NTP, and reports RTC/local holdover when neither is usable. A manual
  position supplies no GPS timestamp, speed, heading, satellite count or accuracy.

The dashboard, status API, APRS agent GPS/status replies, LCD, LED and matrix health
use the selected source. Manual is displayed on location/fix fields; LCD uses
**Man** and the matrix uses an M glyph. Actual observed satellites remain receiver
diagnostics. Manual coordinates stay out of the public JSON; it shows Manual and
the grid locator. Exact coordinates remain available to authenticated location
settings, privileged diagnostics and the existing trusted-LAN position clients.

A fresh valid manual selection resolves the no-GPS-lock warning, including its
matrix/buzzer alert. It does not suppress service failures, invalid configuration,
stale telemetry, unsynchronized time, low voltage or other faults. Self-test
accepts Manual instead of receiver lock; GPS-time unavailability is accepted only
when Chrony has actually selected a network NTP source.

## Lightweight position feed

`pcs-location.service` uses only Python's standard library. One read-only WATCH
connection reads real GPSD on loopback port **2947**. A separate read-only GPSD JSON
endpoint at **127.0.0.1:2948** exposes the selected position. This separation keeps
manual data out of Chrony's receiver SHM/refclock path. Service readiness is
announced after binding the socket and publishing its initial state.

The endpoint supports VERSION, DEVICES, WATCH JSON and POLL for position clients,
and rejects device mutations and raw/NMEA requests. Manual TPVs contain a usable
2D position (3D if elevation is provided) and `pcs_source: Manual`, without an
invented GNSS time or motion. SKY reports retain observed receiver data. Foreign
GPSD applications may display 2D/3D because they do not recognize PCS source labels;
PCS interfaces explicitly display Manual. Raw receiver tools continue using 2947.

Exact effective position is root-only `/run/pcs-location/position.json`. The
coordinate-free status file is `/run/pcs-location/status.json`; it expires for
readers after ten seconds, so a stopped/stuck service cannot indefinitely clear
no-fix alarms. Dashboard requests still read existing cached audience projections.

New-install defaults route Dire Wolf, Graywolf and the Meshtastic GPSD option to
2948. The trusted LAN proxy keeps its existing **10.42.0.1:2947** address, forwarding
to the selected-position feed. No existing beacon or position-sharing toggle is
enabled by configuring a manual position.

## Existing-appliance upgrade and rollback

Do not run the full base installer for this feature-only upgrade. Before activation,
record the exact source/static version, backup existing root-owned configurations,
units/drop-ins, helper files, permissions and service enablement, and arrange an
explicit rollback. Stage and review the concrete appliance-specific changes first.

1. `./scripts/setup-pcs-location.sh --stage` installs the module, helper, unit and
   fixed sudoers entries without enabling/starting the service. The standalone
   installer does not rewrite existing radio configurations or restart radios.
2. Update the existing managed local GPSD routes, preserving all unrelated values:
   Dire Wolf `/etc/direwolf.conf` GPSD line and desired `PCS_APRS_GPSD_PORT` → 2948;
   APRS agent `/etc/pcs/aprs-agent.conf` `gpsd_port` → 2948; Meshtastic environment
   `PCS_MESHTASTIC_GPSD_PORT` → 2948 if explicitly set. Keep local hosts loopback.
   Custom external receivers require individual review; do not silently redirect.
3. For an already provisioned Graywolf, change only its GPS API configuration to
   loopback GPSD port 2948 using its authenticated API and read it back. Do not
   restage or activate its complete profile just to change the GPS port.
4. Install updated root-owned dispatcher, location module, APRS agent, GPIO driver,
   Meshtastic gateway and web backend/static presentation. Preserve their existing
   installation paths, ownership and runtime dependencies. Install the revised
   radio service ordering and LAN-proxy unit; keep LAN socket binding unchanged.
5. After authorization, start/enable the location service and restart only affected
   consumers as required. Radio consumers may publish the position under their
   existing settings after reconnection; commissioning and RF testing stay separate.
   For an existing commissioned Dire Wolf/APRS Agent installation, stop the agent
   and Dire Wolf first, start/confirm `pcs-aprs-ptt-safe.service`, start Dire Wolf,
   then start the agent. Do not put Dire Wolf in a combined restart transaction:
   its existing ExecStopPost safety-holder activation can cancel the pending start.
   Restore only the services that were active before the change; preserve all radio
   settings and the safety holder's normal conflict-based ownership handoff.
6. Before enabling fallback, verify every configured consumer is using 2948 (or
   the existing LAN proxy), the receiver/time path is still raw 2947/SHM, and actual
   Chrony selection is correct. Check protected settings, source labels, public
   redaction, no-fix alert behavior, automatic recovery and persist behavior.

For rollback, first disable manual fallback, restore the saved consumer routes,
units/helpers/source/static pointer and enablement states, then restart only the
affected consumers. Restore the LAN proxy's raw 2947 target before stopping the
position service. Preserve `/etc/pcs/location.json` privately for recovery or remove
it deliberately; never place actual coordinates in Git, test fixtures or reports.

## Development validation

`tests/test_pcs_location.py` uses synthetic coordinates, an injected clock and real
loopback sockets. It covers GPS loss/recovery, persistence, replay/staleness, boundary
and invalid inputs, private atomic writes, expired status, read-only protocol flows,
Manual LCD/indicator behavior and preservation of unrelated faults. HTTP tests cover
authentication, CSRF, sharing confirmation and rejected invalid settings.

Live GPS loss, actual Chrony source transitions, Pi-Star behavior, physical LCD/LED
output, and over-air/APRS-IS/Meshtastic position publication remain appliance
acceptance checks. Unit success does not establish those physical results.
