# Changelog

All notable user-facing PCS changes are recorded here.

## [Unreleased]

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

### Changed

- moved optional Pi-Star coordinated-shutdown pairing to the first usable PCS-LAN
  point and reused the up-front installer selection without storing its password
- corrected executable permissions for the APRS helpers so a clean install does
  not modify the repository checkout
- aligned the README, project overview, documentation index, bill of materials,
  testing/release guidance, and reinstall record with the August 18 PCS state
- archived superseded bring-up notes while preserving their dated evidence and
  appending the current Pi/OpenWrt/Pi-Star architecture decisions
- recorded the remaining APRS radio/PTT and Meshtastic expansion hardware as
  purchased but not installed or validated; Meshtastic integration remains unimplemented

### Security

- Dire Wolf remains stopped and disabled during staging, with no live APRS-IS
  credential, PTT, beacon, or RF transmit path configured

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

[1.1]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/compare/v1.0...v1.1
[1.0]: https://github.com/Saberhawk09/PCS-Portable-Comm-Server/releases/tag/v1.0
