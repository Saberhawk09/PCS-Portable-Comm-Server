# Backup versus restore coverage — October 12, 2026

Recovery development follow-up: the separate [inactive staging tool](recovery-staging.md)
now manifests new exports and validates/stages explicit legacy/VLAN profiles,
uplink/management policy, bandwidth, local SNMP, power configuration and existing
Wi-Fi/WireGuard selections. The table below still describes the **automatic/live
restore helper**, whose allowlist is unchanged. Staged support does not mean those
gaps are closed for an appliance reinstall or that activation is implemented.

This is a source audit of the v2.2.0 baseline plus candidate `86b072b`, not a
restore trial. No private archive or running appliance was changed. The accepted
September v1.9.7 fresh rebuild remains valid for its recorded baseline, not for
all later topology and policy additions.

The [export helper](../scripts/pcs-reinstall-state.sh) captures broad state roots.
The [archive validator](../scripts/pcs_reinstall_archive.py) permits those roots,
but the [restore planner](../scripts/pcs_reinstall_restore.py) selects a narrower
set. `exact` validates required identity families and recovers installer settings;
it does not restore every archive member. Reinstall source, packages, generated
units/firewalls and boot-local state follow separate installation paths.

| State | Export coverage | Restore / regeneration | Classification and remaining gap |
| --- | --- | --- | --- |
| Saved `config/pcs-install.conf` | Explicit path | Exact recovery parses permitted settings into a new settings file used by the base installer | Restored settings, not arbitrary shell execution; this is not a general configuration replay. |
| Wi-Fi NetworkManager profiles | Entire system-connections directory | `network` selects `wifi` / `802-11-wireless` profiles | Backed up and restored, with conflicts rejected unless explicit replacement is selected. |
| WireGuard management policy and keys | `/etc/pcs`, `/etc/wireguard` | Strict policy/key restoration; private path also selects WireGuard files; installer regenerates service/firewall support | Backed up and restored within existing identity validation. |
| API policy, certificate/key, token records | `/etc/pcs-stats-api` | `api` selects the identity family; runtime environment regenerated | Backed up and restored; no new certificate/key should be inferred from backup presence. |
| Web admin, SSH host/client state, Pi password, Samba credentials | Explicit private roots | Selected private identities and Pi-only password hash restored | Backed up and restored; base service configuration regenerated. |
| Meshtastic PCS-side environment/MQTT, Pi-Star pairing, Dire Wolf config, ALSA, Bluetooth, Graywolf database | Explicit roots or `/etc/pcs` | Selected private paths restored | Backed up and restored where present. External radio/switch/AP/hotspot device state is not restored by these file copies. |
| Starlink private collector/pairing config | `/etc/pcs/starlink.json` via broad root | Selected by private restore; component installation reinstalls code | Backed up and restored; does not provide physical DC switching or external terminal recovery. |
| VLAN mode marker `/etc/pcs/network-mode` | Captured under `/etc/pcs` | Not selected by restore; base installer reads the existing marker at process startup and defaults to legacy when absent | Backed up, not restored. An archive with VLAN profiles is not enough to select VLAN mode safely. |
| VLAN LAN/WAN and legacy Ethernet NM profiles | Captured under system-connections | Network restore selects Wi-Fi only; base/component installers generate their own profiles; guarded VLAN migration owns its staged profiles | Backed up, not directly restored. Current VLAN identities and activation order need explicit recovery design. |
| Uplink policy `/etc/pcs/uplinks.json` | Captured under `/etc/pcs` | Not selected by restore; uplink setup preserves an existing valid config or derives one from installer/legacy settings and observed profiles | Backed up, not restored as exact policy. Timings, priorities, identity references and switch assistance need reconciliation. |
| Trusted uplink management `/etc/pcs/uplink-management.json` | Captured under `/etc/pcs` | Not selected by restore; installer creates/preserves policy through its own validated path | Backed up, not restored. Do not infer identical management grants after a fresh rebuild. |
| Bandwidth `/etc/pcs/bandwidth.yaml` | Captured under `/etc/pcs` | Not selected by restore; explicit component installer preserves an existing file or installs unrestricted defaults | Backed up, not restored. Do not silently enable shaping or recover a policy against unverified interfaces. |
| Bandwidth install manifest/service state | `/var/lib/pcs-bandwidth-install` and systemd enablement not export roots | Explicit installer regenerates manifest/units; service activation is separate | Not captured by this export; configuration alone is not installed ownership evidence. |
| SNMP JSON and community | Captured under `/etc/pcs` | Neither selected by restore; documented manual setup installs `snmp acl`, protected credential and disabled configuration | Backed up, not restored; intentionally manual commissioning/activation. A new switch or firmware/mapping change requires re-verification. |
| Switch commissioning evidence and VLAN rollback state | `/var/lib/pcs-switch-commissioning`, `/var/lib/pcs/vlan-switch`, `/var/backups/...` are outside export roots | No general replay | Not covered. Preserve these separately and privately; do not restore stale rollback timers or fabricate acceptance receipts. |
| Power calibration `/etc/pcs/power-monitor.json` | Captured under `/etc/pcs` | Not selected by private restore; power installer preserves an existing file or selects commissioned/generic template | Backed up, not restored exactly. Factory/profile settings do not recover operator-specific calibration or thresholds. |
| APRS agent persistent mailbox | `/var/lib/pcs-aprs-agent` captured | Not selected by private restore | Backed up, not automatically restored; separate decision needed for old queued messages and retention. |
| Snapshot JSON, uplink ownership/provenance, power totals/source selection and live counters | `/run` excluded | Regenerated in the new boot by owning services | Intentionally ephemeral; never replay old boot ownership or counters as current. |
| Runtime source, units, firewall rules, DHCP/DNS and service enablement | Not general backup roots | Installed/generated from reviewed source and settings | Regenerated; unit checks alone do not prove current-topology restore or correct activation order. |
| D-Link flash config, AP/Pi-Star OS, modem/RAK firmware-side state, physical wiring | Not covered by PCS file export | Operator/external-device procedures | Intentionally manual and independently accepted. |

## Minimum follow-up design

The first maintenance pass made no restore implementation change. Its follow-up
implements manifest/plan/inactive-stage validation, with tests described in the
new staging guide. Activation remains deferred. Blindly
adding the mode marker or VLAN profiles to the current copy list could change
the control network mid-install. Restoring source policy without matching profile
identity could select the wrong WAN. Those are operational decisions, not a safe
allowlist expansion.

1. Add a non-mutating recovery manifest/plan that reports captured, selected,
   regenerated and deferred components, with schemas and file permissions checked.
2. Stage VLAN topology, profiles and uplink policy together before activation.
   Verify parent/interface/VLAN ID/profile UUID and LAN/WAN separation. Do not
   source imported shell or start NetworkManager actions during planning.
3. Validate optional bandwidth and SNMP configuration in isolation. Preserve
   credentials privately; keep enforcement/monitoring activation explicit and
   require fresh physical mapping evidence when hardware identity has changed.
4. Decide explicitly whether calibrated power policy and queued APRS messages
   are restored, transformed or deliberately left for operator reconciliation.
5. Exercise missing/partial/corrupt archives, foreign identities, legacy mode,
   repeat installation, rollback and interruption in a disposable system with
   independent recovery access. Only then plan supervised appliance acceptance.

The focused synthetic coverage tests document that broad archival inclusion does
not silently enable these deferred network components. They validate boundaries
using temporary files; they do not certify a complete recovery implementation.
