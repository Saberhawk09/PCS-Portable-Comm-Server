# README housekeeping and documentation audit

This audit preserves the release and operating details removed from the root
README during the September 2026 housekeeping. The root remains the short
front page; specialist guides own the details.

| Removed material | Destination |
| --- | --- |
| WireGuard commissioning, default-off setup, /32 peers, trusted home access | [WireGuard](wireguard-remote-management.md) |
| v1.4 HTTPS API, guarded administration, pairing/revocation acceptance | [Stats API](pcs-stats-api.md#verified-state-and-remaining-product-gates) |
| Real-phone acceptance and v1.5 signed Companion v0.2.0 | [Android bootstrap](pcs-android-client-bootstrap.md), [roadmap](remote-management-api-android-roadmap.md) |
| v1.6 Graywolf history, 8070 portal, GPIO6 watchdog, capture-buffer patch, 925/100 ms | [Graywolf](graywolf-aprs.md) |
| v1.7 agent, RF/Internet channel routing, mailbox surfaces, FT3DR exchange | [APRS agent](aprs-agent.md) |
| v1.8 INA226/buzzer and v1.8.1 stress acceptance and unresolved RF/I2C issue | [Power system](power-system.md#ina226-power-monitoring) |
| Safe rendering, synthetic tests, guarded RX/TX, SA818S/audio and rollback | [Dire Wolf](direwolf-aprs.md) |
| Windows ipconfig, subnet/gateway, LAN/Internet/DNS ping tests | [Testing checklist](testing-checklist.md#windows-client-network-test) |
| USB/SD share mapping, discovery, credentials, additive backups, intervals, manual sync | [Samba](samba-file-share.md) |
| Electrical components and physical record | [Bill of materials](bill-of-materials.md), [power system](power-system.md) |

The repository-wide documentation pass checked Markdown links and scanned
status, release, installation-path, and commissioning claims against the
current scripts and observed recovery. It corrected obsolete Graywolf
activation restrictions, the exact-path WireGuard instructions, an outdated
release-history gate, and the last-rebuild summary. Existing Windows and Samba
sections already contained the supplied procedures and were retained.

Historical evidence is dated. The repaired Lite appliance passed operational
checks and the operator confirmed two-way APRS. Agent RF channel 0 is now
explicitly enabled; a new agent-specific over-air request/ACK/PONG test remains
unobserved. Final clean-install acceptance, full three-device reinstall,
electrical as-built measurements, and unresolved RF/I2C coupling remain open.
See the [recovery record](full-stack-reinstall.md#september-2026-recovery-evidence).

## September 26, 2026 v2.1.1 reconciliation

The current-state summaries were reconciled after the v2.0.0 main-12V monitor
commissioning and for the v2.1.1 documentation release. The root README, documentation
index, project overview, reinstall runbook, script reference, and remote-client
roadmap now describe the commissioned four-monitor input/12V/5V/Starlink
profile, raw-versus-12V-only accounting, and the current public/LCD power
surfaces.

The same pass replaced the obsolete repaired-v1.8.2 status summary with the
accepted September 22 commissioned reinstall boundary. Historical September 9
recovery evidence remains dated in the reinstall runbook; it is context for the
repairs, not the current acceptance result. Electrical as-built details,
three-appliance recovery, RF/I2C hardening, extended RF validation, and physical
commissioning checks remain explicitly open.
