# PCS UI feature parity

Baseline: `ba80c8dc067cb1c038e425cbc283a0167efc4fb9` (`v2.2.2`). All original action IDs, descriptions, confirmation messages, POST handlers and CSRF fields remain intact.

| Existing action ID | Label | New section / category |
| --- | --- | --- |
| `starlink-status` | Starlink Telemetry | Network / Starlink |
| `starlink-reboot` | Reboot Starlink Mini | Network / Starlink |
| `starlink-shutdown` | Starlink Shutdown (staged) | Network / Starlink |
| `status` | View PCS Status | Overview / Overview tools |
| `self-test` | Run Self-Test | Actions / Frequently used |
| `storage-status` | View Storage | Overview / Overview tools |
| `wifi-status` | View Wi-Fi | Overview / Overview tools |
| `wifi-connect` | Connect Wi-Fi | Actions / Frequently used |
| `wifi-disconnect` | Disable Wi-Fi Radio | Network / Wi-Fi and cellular |
| `cellular-status` | View Cellular | Network / Wi-Fi and cellular |
| `cellular-connect` | Connect Cellular | Actions / Frequently used |
| `cellular-disconnect` | Disconnect Cellular | Network / Wi-Fi and cellular |
| `cellular-test` | Test Cellular | Network / Wi-Fi and cellular |
| `meshtastic-status` | View Meshtastic | Overview / Overview tools |
| `restart-meshtastic` | Restart Meshtastic | Radio / Radio operations |
| `aprs-mailbox-read` | Mark APRS Mailbox Read | Radio / Radio operations |
| `sync-backup` | Sync USB to SD Backup | Actions / Frequently used |
| `mount-usb` | Mount USB Storage | Storage / Storage operations |
| `mount-new-usb` | Mount New USB Storage | Storage / Storage operations |
| `safe-unmount-usb` | Unmount USB Safely | Storage / Storage operations |
| `restart-services` | Restart PCS Services | Diagnostics / Services and time |
| `restart-samba` | Restart Samba | Storage / Storage operations |
| `restart-modemmanager` | Restart ModemManager | Diagnostics / Services and time |
| `sync-time` | Sync Time Now | Diagnostics / Services and time |
| `restart-chrony` | Restart Chrony | Diagnostics / Services and time |
| `restart-gpsd` | Restart GPSD | Diagnostics / Services and time |
| `restart-logs` | View Restart Logs | Diagnostics / Services and time |
| `buzzer-mute` | Mute Audible Warnings | Diagnostics / Services and time |
| `buzzer-unmute` | Enable Audible Warnings | Diagnostics / Services and time |
| `reboot-system` | Reboot PCS | Diagnostics / Dangerous operations |
| `shutdown-system` | Shutdown PCS | Diagnostics / Dangerous operations |

All actions are also discoverable from the Actions catalog. Each has one actual form, avoiding duplicate controls. Unsupported Starlink shutdown retains its original diagnostic/status behavior and its explicit staged label.

| Existing status card IDs | New location |
| --- | --- |
| `cellular`, `client-lan`, `network`, `remote-management`, `starlink`, `uplink-details` | Network |
| `aprs`, `aprs-mailbox`, `meshtastic` | Radio |
| `backup-health`, `samba`, `storage` | Storage |
| `gps`, `power`, `services`, `system`, `system-stats`, `time`, `web-admin` | Diagnostics |
| Unknown or future IDs, including `dashboard` | Overview → Advanced / Other diagnostics; global alert/error remains above every section |
| Every card item and metric | Rendered by the original escaped card renderer; nothing filtered by title |

| Existing function / route | New location |
| --- | --- |
| Public overview and service launcher `/` | Overview, with Network, Radio, Power, Storage and Diagnostics anchors |
| Full public readings `/status/` | Overview service tile and Diagnostics → Detailed status |
| Power `/power/` and Starlink `/starlink/` | Contextual links from Power and Network; original permalinks retained |
| File sharing, docs, radio, Pi-Star help | Original paths and service links retained |
| Client addresses / MACs, WAN public IP | Authenticated Network → Detailed field access and clients |
| Graywolf access/settings detail, authenticated APRS mailbox and mark-read | Radio |
| Bandwidth ceilings, profiles, client overrides `/admin/bandwidth` | Network → Bandwidth settings; original protected page |
| Automatic backup enable, integer interval 1–43,200, keep_history | Storage → Backup settings, normal inline POST form |
| DC source for this boot, optional Wh capacity, four power branches and protection | System → PCS Power and DC source; original validation/defaults |
| Password change `/admin/password` and logout | Protected existing routes and header controls |
| `/health`, `/api/public-status`, `/api/v1`, `/cockpit/`, `/openwrt/`, legacy `:8080` | Routing and contracts unchanged |

Without JavaScript, every section remains visible and forms still POST normally. JavaScript only switches visible sections, focuses real returned output and preserves the preexisting confirmation prompts. Hash fragments never become command arguments.

Follow-up navigation: the six public destinations are shared by admin, with an extra Actions catalog. Wi-Fi, Meshtastic, storage and PCS status read actions now live as compact Overview panel forms. Power data/settings live in Power; advanced/service diagnostics live in Diagnostics.

All full authenticated status cards, mailbox bodies and client/management detail now live at `/admin/details`, grouped by subsystem. Clean cards, action forms and settings remain on `/admin/`.
