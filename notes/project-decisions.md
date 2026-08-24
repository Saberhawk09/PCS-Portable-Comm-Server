# Project Decisions

This file records major PCS design decisions and the reasoning behind them.

> Decisions are append-only historical records. Later dated entries explicitly supersede earlier choices. Use the newest applicable entry and the [current project overview](../docs/project-overview.md) when they differ.

## 2026-07-01

### Repository structure created

Initial project folders were created for documentation, hardware notes, scripts, and development notes.

Reasoning: keeping design notes, setup instructions, and hardware planning separated should make the project easier to maintain as it grows.

### Dedicated router remains part of the design — superseded in part

PCS will currently keep a dedicated router in the design instead of making the Raspberry Pi handle all routing directly.

Reasoning: the router can handle Wi-Fi, DHCP, LAN switching, and normal client networking while the Pi focuses on server-side services.

Superseded in August 2026: the dedicated router remains, but only as a bridged AP/Ethernet switch. The Raspberry Pi now owns DHCP, DNS, NAT/routing, and PCS services.

### Raspberry Pi server role — implemented and expanded

The Raspberry Pi is planned to handle cellular modem access, LAN file sharing, GPSD, and Chrony/NTP services.

Reasoning: this keeps field services centralized while still allowing the router to handle ordinary client networking.

Implemented and expanded in August 2026: the Pi also became the PCS gateway, DHCP/DNS provider, public homepage host, and authenticated administration host.

## 2026-07-01 Pending Decisions — historical

- Final router hardware/configuration — resolved as the Linksys EA4500 running OpenWrt in bridged AP/switch mode
- Whether WWAN is handled by the Pi or directly by an OpenWRT router — resolved as the Pi-hosted EM7565 path
- Final source selector switch choice — DPDT center-off design selected; exact as-built pinout still requires recording
- Final fuse placement — design documented; installed values and locations still require as-built verification
- Final enclosure layout — operational v1 prototype exists; exact dimensions, fasteners, photos, and CAD references remain pending

## 2026-08-18

### Current network roles

The Raspberry Pi at `10.42.0.1` is the gateway and service host. It owns DHCP, DNS, NAT/routing, Samba, Chrony/NTP, RTC, GPSD, the public homepage, authenticated administration, and optional cellular integration.

The Linksys EA4500 at `10.42.0.2` runs OpenWrt as a bridged Wi-Fi access point and Ethernet switch. Pi-Star is the optional fixed PCS-LAN node at `10.42.0.3`.

Reasoning: centralizing routing and field services on the Pi makes the software baseline reproducible while keeping Wi-Fi and switching on dedicated hardware.

### Optional expansion boundaries

Dire Wolf / APRS is software-staged with its service and RF path disabled. The C-Media/Unitek Y-247A USB sound adapter is installed and detected for capture/playback, but the radio/PTT hardware, audio levels, and RF path remain unvalidated and require bench and operator-supervised RF testing. Meshtastic hardware is purchased and awaiting delivery; PCS integration is not yet implemented.

Reasoning: purchased or staged hardware must not be described as installed, RF-ready, or validated before observed commissioning evidence exists.

### As-built hardware records remain separate

The AC/DC power system and prototype enclosure are operational, but exact fuse values, wiring, grounding, rail measurements, thermal results, dimensions, mounting details, photographs, and CAD references remain pending.

Reasoning: an operational prototype does not by itself provide a safe, reproducible as-built electrical or mechanical record.

## 2026-08-23

### APRS subsystem commissioned and managed

The prior software-staged APRS boundary is superseded for the installed PCS.
The operator validated the SA818S UART and RF channel, Easy Digi audio/PTT,
Sabrent USB audio levels, Dire Wolf timing, GNSS beacons, two-way APRS-IS
messages and acknowledgements, WIDE1-1 fill-in behavior, AGW, and KISS.

The repository now records the commissioned `W8IJC-10` profile on 144.5500 MHz
and manages SA818S programming/readback, ALSA restoration, LAN-only AGW/KISS,
Dire Wolf ordering/restarts, self-test, status, and dashboard health. Fresh
installs remain staged until the explicit evidence gates and RF confirmation are
satisfied; repository tests do not reproduce the operator's RF measurements.

Reasoning: physical commissioning can now support a reproducible service profile
without weakening the safeguards that prevent an unverified rebuild from
transmitting or exposing network TNC listeners.

## 2026-08-24

### Pi-Star uses guarded wired-only networking

The installed Pi-Star hotspot uses its RTL8152 USB Ethernet adapter as `eth0`
at `10.42.0.3`. A two-stage handoff verified continuous carrier and PCS
reachability before the managed `disable-wifi` overlay disabled onboard Wi-Fi.
Pi-Star retains its native credentials and a recoverable SD-card rollback path.

Reasoning: wired Ethernet removes an unnecessary radio dependency inside the
PCS enclosure while the guarded handoff prevents a cable, adapter, or switch
fault from silently removing the last management path.

### PCS time is GPS first with two fallbacks

Chrony prefers usable GPS from the EM7565/gpsd SHM source, selects public
Internet NTP when GPS is unavailable or rejected, and advertises the
RTC-seeded system clock as degraded stratum-10 holdover only when neither
authoritative source is selectable. The DS1307 seeds boot and is updated by
`rtcsync`; it is not continuously sampled as a Chrony refclock.

Reasoning: this preserves GPS independence in the field, retains an independent
Internet sanity check/fallback, and still provides recognizable degraded local
time after a cold boot without either source.

### Meshtastic gateway commissioned over scoped USB

The installed RAK4631 uses a persistent scoped USB serial session with its
Bluetooth radio disabled. PCS proxies the radio's encrypted NeoMesh MQTT
traffic, sends a GPSD-backed position every 30 minutes, retains the firmware's
separate hourly opted-in MapReport, and mirrors public LongFast/map-report
uplinks to the map broker without subscribing to it. Primary-channel and map
position precision are enforced at 15 bits.

IJC1 public-map appearance and an opted-in IJC2 position received over LoRa and
forwarded to that map demonstrate the commissioned public gateway path. The
service stores aggregate health only; it does not retain messages, remote
identities, coordinates, or channel keys. Broader RF coverage and environment
sensor accuracy remain field-characterization work.

Reasoning: the scoped USB transport is reliable under the hardened systemd
sandbox, the publish-only public mirror avoids an Internet-to-RF flood path,
and explicit position opt-in/precision keeps public location sharing deliberate.
