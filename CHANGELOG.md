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
- selected BCM GPIO17 / physical pin 11 for EasyDigi PTT and documented the
  tentative PCS-wide GPIO allocation
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
- recorded APRS and Meshtastic expansion hardware as purchased but not installed
  or validated; Meshtastic integration remains unimplemented

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
