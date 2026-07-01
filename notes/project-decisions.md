# Project Decisions

This file records major PCS design decisions and the reasoning behind them.

## 2026-07-01

### Repository structure created

Initial project folders were created for documentation, hardware notes, scripts, and development notes.

Reasoning: keeping design notes, setup instructions, and hardware planning separated should make the project easier to maintain as it grows.

### Dedicated router remains part of the design

PCS will currently keep a dedicated router in the design instead of making the Raspberry Pi handle all routing directly.

Reasoning: the router can handle Wi-Fi, DHCP, LAN switching, and normal client networking while the Pi focuses on server-side services.

### Raspberry Pi server role

The Raspberry Pi is planned to handle cellular modem access, LAN file sharing, GPSD, and Chrony/NTP services.

Reasoning: this keeps field services centralized while still allowing the router to handle ordinary client networking.

## Pending Decisions

- Final router hardware/configuration
- Whether WWAN is handled by the Pi or directly by an OpenWRT router
- Final source selector switch choice
- Final fuse placement
- Final enclosure layout