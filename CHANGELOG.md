# Changelog

All notable user-facing PCS changes are recorded here.

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
