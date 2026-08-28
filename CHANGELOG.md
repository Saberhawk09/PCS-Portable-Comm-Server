# Changelog

All notable user-facing PCS changes are recorded here.

## [Unreleased]

### Added

- local native PCS Companion Android foundation with ordered home-LAN,
  PCS-LAN, and WireGuard discovery; explicit certificate enrollment and exact
  pinning; public/authenticated status with timestamped offline cache;
  Keystore-encrypted device tokens; dynamic administrative actions with
  device authentication and one-time challenges; password rotation; and local
  trust/token recovery controls
- corrected strict discovery decoding for the live v1 `authentication` object
  and bound server-provided action paths/challenges to the fixed API namespace
- moved TLS handshakes off the API accept loop with bounded client timeouts so
  one stalled cellular connection cannot block PCS-LAN or WireGuard clients
- fixed the PCS WireGuard MTU at a cellular-safe 1280 bytes and added live and
  self-test verification to prevent TLS/SSH black holes on constrained uplinks
- replaced the Android app's generic HTTPS connection failure with safe,
  endpoint-specific permission, routing, timeout, hostname, certificate, and
  TLS diagnostics for every configured route without weakening
  exact-certificate trust
- disabled Android HTTP connection reuse for the PCS API, whose HTTP/1.0
  responses close each TLS session, so discovery cannot leave a stale socket
  for the immediately following status request
- allow the sandboxed PCS API collector read-only Netlink route inspection so
  a working internet uplink is not falsely reported as `Offline`/`bad`; normal
  backup-sync attention remains a `warn` condition, and authenticated status
  uses that administrative overall severity in the Companion headline
- grant the sandboxed API action runner write access only to the fixed
  `/srv/pcs-share-backup` destination so the allowlisted `sync-backup` action
  can update the SD mirror without weakening the rest of `ProtectSystem=strict`
- record operator acceptance of PCS Companion over home LAN, direct PCS LAN,
  and cellular/WireGuard, plus successful backup synchronization and
  challenge-protected shutdown on 2026-08-28
- added Android 17 `ACCESS_LOCAL_NETWORK` declaration, runtime prompt, denial
  handling, and request gating so API 37 devices can reach private PCS addresses
- Android JVM coverage for endpoint policy, API parsing, TLS certificate and
  hostname rejection, and bounded responses, plus clean Android lint, debug
  APK assembly, and minified unsigned release assembly

## [1.4] - 2026-08-27

### Added

- default-disabled PCS Stats API packaging with versioned
  read-only resources, separate public/authenticated response views, hashed
  revocable tokens, HTTPS-only startup, fixed command allowlists, request
  limits, structured errors, collector-free discovery, an OpenAPI 3.1 client
  contract, contract/security tests, guarded activation/rollback, and a
  supervised HTTPS canary deployment on PCS
- administrator-approved API device pairing through a fixed, rate-limited TLS
  route, one-time revocable `stats:read` + `admin:actions` + `admin:password` bearer tokens, exact sudo isolation,
  OpenAPI coverage, an Android bootstrap/trust contract, and supervised PCS
  deployment/reboot validation
- expansion of the API contract to the complete current control-panel
  action catalog, one-time challenge-gated execution, bounded output/audit
  records, and a dedicated authenticated administrator-password route,
  deployed under a guarded rollback boundary on PCS on 2026-08-27
- safe external `pcs-api-smoke-test.py` checks for HTTPS trust, discovery,
  public redaction, unauthenticated write protection, and optional
  environment-supplied authenticated status/action-catalog validation
- quiet handling of expected TLS client disconnects so certificate rejection
  does not leave misleading API traceback noise in the service journal
- narrow systemd write access for the API's root-isolated pairing and password
  helpers, permitting only `/etc/pcs-stats-api` and `/etc/pcs-control-panel`,
  with live full-scope pairing, non-mutating administration, revocation, and
  revoked-token denial acceptance on PCS

- explicit trusted-home-Wi-Fi management access, allowing the protected PCS
  host services directly from one configured private `wlan0` subnet while
  retaining the cellular/unknown-interface deny policy and WireGuard `/32`
  restrictions

- sanitized WireGuard remote-management cards for the public homepage and
  authenticated admin dashboard, including boot, firewall, handshake, route,
  and aggregate transfer status without key or endpoint disclosure
- guarded Dire Wolf APRS-IS recovery after a confirmed default-uplink change,
  with a native reconnect grace period, DNS gate, restart cooldown, and a
  no-restart installation path

- WireGuard management-plane implementation with strict `/32`
  split-tunnel validation, PCS-LAN-to-home isolation, protected administrative
  listeners, NetworkManager shared-LAN compatibility, handshake-gated
  activation, deactivation/rollback, tests, and an operator runbook
- default-off base-installer integration using the ignored
  `private-config/wg-pcs.conf` client profile, with strict safe-subset import
  and all-or-nothing activation
- ASUS profile compatibility that validates but discards the exported DNS
  override and stores an optional WireGuard PSK in a separate root-only file
- phased design for a versioned PCS API and native Android companion, with
  explicit authentication, privacy, HTTPS, and deployment gates

### Security

- prohibited default routes, home-LAN prefixes, broad WireGuard management
  sources, arbitrary remote commands, and committed VPN private keys
- kept remote management default-off for fresh installs; the commissioned PCS
  activation was separately handshake-gated and supervised

## [1.3.2] - 2026-08-25

### Fixed

- prevented Dire Wolf fixed-interval GPS tracker beacons from replaying every
  missed interval after a large forward clock correction, while preserving
  APRS receive, IGate, and offline digipeating behavior
- made the DS1307 cold-boot seed wait for its device unit and retry transient
  early-boot read failures before Chrony starts

## [1.3.1] - 2026-08-24

### Added

- installer-selectable automatic Wi-Fi-to-cellular fallback with 30-second
  loss/recovery stability windows, service-owned session tracking, dashboard
  visibility, status/self-test coverage, and a conservative manual default

### Changed

- kept the NetworkManager cellular profile non-autoconnecting in both policies
  so manually started cellular sessions remain under operator control

## [1.3] - 2026-08-24

### Added

- commissioned NeoMesh forwarding health for the live RAK4631, including
  encrypted primary-channel uplink/downlink, serial Client Proxy, broker-match
  validation, and opted-in hourly map-report status
- optional uplink-only mirroring to the separate MQTT broker used by the
  coverage map embedded at `neome.sh/meshtastic/`, including default-key
  LongFast traffic and explicit opt-in firmware map reports, with connection
  and publish health exposed without public-broker downlink
- exposed the privacy-safe radio forwarding and public-map policy on the public
  and authenticated Meshtastic dashboard cards
- GPSD-backed synchronization of the RAK4631's local map position, with a
  500-meter movement threshold to avoid unnecessary fixed-position flash writes
- repeatable 15-bit primary-channel position precision enforcement and runtime
  policy validation whenever the public NeoMesh map mirror is enabled
- managed SA818S initialization over `/dev/serial0` with exact group readback
- explicit APRS ALSA restoration and verification before every Dire Wolf start
- delayed APRS mixer restoration after each commissioned USB sound-card
  enumeration, preventing a later distribution ALSA restore from reapplying
  stale levels
- LAN-only nftables enforcement for both AGW 8000/tcp and KISS 8001/tcp
- restart-always Dire Wolf recovery for USB sound-card re-enumeration
- atomic commissioned-profile import with stale APRS-key removal and hardware
  evidence reset
- idempotent Raspberry Pi UART/serial-console preparation for APRS rebuilds
- guarded two-stage Pi-Star handoff to its RTL8152 USB Ethernet adapter with a
  recoverable onboard Wi-Fi disable policy
- latched visual shutdown state with `PCS Offline` / `Shutting Down` on the
  LCD, six blue status pixels, and a subdued bed/ZZZ matrix icon
- active-only public and authenticated Meshtastic dashboard integration for
  node, USB/BLE, MQTT, aggregate mesh/proxy, GPSD, case environment,
  utilization, and power status
- fixed privacy-safe Meshtastic status and confirmed gateway-restart actions
- opt-in bounded GPSD position delivery to the attached Meshtastic node

### Changed

- reconciled the maintained README, hardware inventory, setup/recovery guides,
  operator status output, and project decisions with the commissioned August 24
  system, including wired-only Pi-Star, GPS-first timekeeping, active APRS, and
  the validated IJC1/IJC2 Meshtastic map paths
- expanded CI to compile every maintained Python source and verify executable
  mode on every shell installer/helper instead of relying on stale file lists
- corrected the local client-name example and operator output for the current
  Linksys EA4500 OpenWrt AP/switch topology while preserving legacy profile
  names required for in-place upgrades
- made I2C status discovery robust when an unprivileged shell omits
  `/usr/sbin` from `PATH`
- reduced the stationary PCS Meshtastic GPSD position-packet cadence from five
  minutes to 30 minutes while retaining the separate hourly public MapReport
- made active Meshtastic health fail closed when the radio MQTT module, Client
  Proxy, or radio/PCS broker mapping is not correctly configured
- made the Meshtastic status command read the running USB/BLE gateway snapshot
  by default so dashboard checks never compete for the radio transport
- reduced the commissioned APRS USB capture gain from 100%/+23 dB to
  69%/+12 dB after four live W8IJC-7 packets decoded at 26-56, with three at
  38-56 near Dire Wolf's recommended receive level of 50
- changed the commissioned GNSS position beacon from APRS-IS-only to independent
  channel-0 RF and direct APRS-IS packets every 10 minutes
- reconciled the production profile to the commissioned `W8IJC-10` station on
  144.5500 MHz with GPIO6 GPIOD PTT, operator-selected 700 ms TX delay, 200 ms
  tail, -16 dB TX playback, and FX.25 TX off
- recorded the validated SA818S, Easy Digi, UART, audio, RF, APRS-IS messaging,
  GNSS beacon, and WIDE1-1 fill-in hardware state
- made the base installer preserve APRS active-mode and AGW/KISS LAN firewall
  fields, and refresh an existing control panel after guarded activation
- made every Pi-Star network apply require a 30-second carrier and PCS
  reachability stability window before any filesystem or Wi-Fi mutation
- guarded Pi-Star's native Wi-Fi boot and AP-service paths so wired-only boots
  still complete their read-only remount without false Wi-Fi service failures
- cleaned Pi-Star native boot health by removing the cgroup-v2 fstab conflict,
  guarding legacy D-Star startup with native mode markers, and allowing ARM
  `uname` in the haveged service sandbox

## [1.2] - 2026-08-21

### Added

- hardware-safe Dire Wolf / APRS software staging and validation workflow
- authenticated dashboard state for staged or active APRS installations
- receive-only configuration example and hardware-arrival activation checklist
- interactive non-secret APRS profile questionnaire and read-only audio/PTT discovery
- versioned W8IJC-2 digi-IGate profile for an alternate tactical APRS channel,
  including all-eligible RF-to-APRS-IS publication and conventional two-way
  APRS message gating back to RF
- active-high Raspberry Pi GPIO PTT intent for an optoisolated EasyDigi
  active-low radio closure-to-ground output
- selected BCM GPIO6 / physical pin 31 for EasyDigi PTT and documented the
  finalized schematic's PCS-wide GPIO allocation
- added an offline-safe GPIO commissioning utility with simulated-by-default
  LCD, MAX7219, WS2812, and explicit-duty fan tests
- added a persistent six-pixel GPIO21 WS2812 health-indicator daemon with
  stable CPU, disk, USB, service/Pi-Star, uplink/router, and GPS assignments
- added repeatable installers, status reporting, and self-test coverage for
  the HD44780 LCD, WS2812 indicators, MAX7219 matrix, and GPIO18 PWM fan
- added optional GPIO18 PWM0 thermal fan control at the vendor-documented
  100 Hz frequency, with a conservative five-step curve, hysteresis, runtime
  status, and full-duty startup/shutdown/missing-temperature fail-safe behavior
- recorded the installed MAX7219/AHCT125 path as bench-tested at 500 kHz and
  selected the proven `0x03` indoor intensity for its driver
- added a hardened SPI-only MAX7219 daemon that rotates CPU temperature,
  cellular quality and a coordinate-free GPS
  satellite count using compact icons and digits; no-fix and unavailable states
  replace the count with explicit status symbols
- changed the matrix temperature identifier from a thermometer to a clear `°C`
  unit symbol before the numeric Celsius reading
- added `PCS_SETUP_GPIO_STATS=yes|no` as an optional base-installer choice,
  including SPI enablement, the `python3-spidev` dependency, and conditional
  PCS status/self-test coverage
- matched the matrix satellite count to the web admin by keeping gpsd's fullest
  recent multi-constellation `SKY` report instead of the first partial report
- added a guarded two-line HD44780 LCD command and aligned its data pins with
  the installed GPIO27/GPIO22/GPIO23/GPIO24 wiring
- bench-tested the installed HD44780 path and added a hardened GPIO-only daemon
  with rotating PCS/uptime, CPU/LTE, and GPS/fix pages
- revised the LCD rotation to four operator-defined pages with dual-unit CPU
  temperature, cellular on/off state, and paired GPS view/used satellite counts
- expanded the LCD rotation with active network-uplink and AP-client/grid-square
  pages using the PCS route, neighbor, and GPS definitions
- centered all HD44780 text fields within their 16-character rows
- added plain-language LCD warning pages and a critical-only hard-fault mode
  using the same health conditions as the MAX7219 annunciator
- corrected the LCD cellular state to follow the actual NetworkManager data
  session instead of treating a registered idle modem as connected
- repurposed the MAX7219 from duplicate telemetry into a priority health
  annunciator with a dim healthy checkmark, `!` plus subsystem warning, and
  `X` plus subsystem critical patterns at a subdued alert intensity
- aligned the web panel, LCD, matrix, and WS2812 indicators so internet-only
  loss remains a warning while an unreachable OpenWrt AP is a hard fault
- recorded the installed C-Media/Unitek Y-247A APRS USB sound adapter as
  capture/playback-detected while keeping PTT, audio levels, and RF validation pending
- selected 144.555 MHz as the operator-defined tactical APRS channel
- selected a 10-minute GPS beacon interval and conventional WIDE1-1-only
  fill-in digipeater policy with no preemptive handling
- connected the Dire Wolf tracker profile to the documented private PCS gpsd
  path at `localhost:2947` and exposed that source in admin status
- added an active-only public APRS operating-profile card without exposing
  APRS-IS credentials, GPIO/PTT wiring, audio device paths, or staged settings
- added guarded Dire Wolf RX/TX render, validation, activation, and rollback
- pinned an official Dire Wolf 1.8.1 source-build fallback for Raspberry Pi OS
  releases whose package is too old for the PCS libgpiod/FX.25 profile
- fixed non-interactive nftables discovery and Dire Wolf help parsing under
  shell `pipefail`
- bound the service override to the validated binary path and guarded render,
  activation, and rollback commands with explicit hardware evidence gates and
  interactive RF confirmation
- added synthetic AX.25/FX.25 packet tests, capability reporting, persistent
  LAN-only KISS filtering, managed log retention, and APRS dashboard telemetry
- added a privacy-preserving persistent Meshtastic BLE/MQTT client-proxy
  gateway with reconnect handling, explicit downlink filters, echo suppression,
  local environment telemetry, guarded staging, and recovery documentation

### Changed

- moved optional Pi-Star coordinated-shutdown pairing to the first usable PCS-LAN
  point and reused the up-front installer selection without storing its password
- corrected executable permissions for the APRS helpers so a clean install does
  not modify the repository checkout
- aligned the README, project overview, documentation index, bill of materials,
  testing/release guidance, and reinstall record with the August 18 PCS state
- archived superseded bring-up notes while preserving their dated evidence and
  appending the current Pi/OpenWrt/Pi-Star architecture decisions
- recorded the installed and live-tested LCD, matrix, WS2812 indicators, and
  PWM fan while preserving the unmeasured RPM, power, and thermal boundaries
- deployed the Meshtastic gateway software to PCS while leaving the unproven
  persistent BLE/MQTT link, RF behavior, and sensor baseline explicitly pending

### Security

- Dire Wolf remains stopped and disabled during staging, with no live APRS-IS
  credential, PTT, beacon, or RF transmit path configured
- Meshtastic credentials remain outside Git, runtime status excludes message,
  position, channel-key, and remote-identity storage, and downlink begins denied

## [1.1] - 2026-08-13

### Added

- public field-status homepage on port 80
- password-protected PCS administration with CSRF protection and rate limiting
- secure in-browser administrator password rotation with session invalidation
- LAN GPSD access and optional Pi-Star monitoring, time, GPS, and coordinated shutdown integration
- repeatable setup and validation improvements for PCS and Pi-Star
- automated GitHub CI for Python tests, Python compilation, and shell syntax
- release checklist and consolidated as-built hardware documentation status

### Changed

- moved the legacy dashboard path to a compatibility redirect on port 8080
- expanded the self-test to cover homepage authentication, public-data boundaries, Pi-Star, and password-helper installation
- aligned the README and technical documents with the operational PCS hardware and software stack

### Security

- placed administrative actions behind authenticated sessions
- stored password hashes and session keys outside the repository with restricted permissions
- limited privileged web operations through explicit sudoers allowlists
- passed password-change secrets to the root helper only through local stdin

## [1.0] - 2026-07-09

- initial rebuild-tested PCS software baseline
- Raspberry Pi gateway, DHCP/DNS, Samba, Chrony, RTC, WWAN/GNSS, Cockpit, and control-panel setup
- hardware-first installation documentation

[Unreleased]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.4...HEAD
[1.4]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.3.2...v1.4
[1.3.2]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.3...v1.3.1
[1.3]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.2...v1.3
[1.2]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.1...v1.2
[1.1]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.0...v1.1
[1.0]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/releases/tag/v1.0
