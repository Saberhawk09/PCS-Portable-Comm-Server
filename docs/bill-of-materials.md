# PCS Bill of Materials

This document tracks planned and purchased hardware for PCS.

Prices are rough planning values and may change.

## Compute / Core Hardware

| Item | Purpose | Status |
|---|---|---|
| Raspberry Pi 4 8GB | Main PCS server/gateway | In use |
| USB flash drive | Primary Samba file share storage | In use |
| Actiontec T3200 | Temporary AP/switch for testing | In use |
| Sierra Wireless EM7565 | Future cellular modem upgrade | Pending validation |
| M.2 WWAN to USB adapter | USB adapter for cellular modem/GPS module | Tested with EM7455/DW5811e |
| External LTE antennas | Cellular signal improvement | Planned |
| MHF4 to SMA pigtails | Modem antenna connections | Planned |

## Power Hardware

| Item | Purpose | Notes |
|---|---|---|
| Anderson Powerpole inlet | DC power input | Planned |
| Switched/fused IEC C14 inlet | AC grid input | Planned |
| 12 V 3 A internal PSU | AC to internal 12 V bus | Planned |
| Automotive blade fuse holders | DC branch/input fusing | Planned |
| 3 A blade fuses | 12 V bus/input protection | Planned |
| DPDT center-off switch | AC/DC source selection | Prefer break-before-make |
| 12 V to 5 V buck converter | Pi/modem 5 V rail | Planned |

## Enclosure / Mechanical

| Item | Purpose | Notes |
|---|---|---|
| 3D printed PETG enclosure | Main case | Planned |
| TPU bumpers | Impact protection | Planned |
| External antenna bulkheads | LTE/GPS antenna mounting | Planned |
| Vents / airflow features | Cooling | Planned |
| Switch and indicator holes | Operator interface | Planned |

## Thermal / Electronics Support

| Item | Purpose | Notes |
|---|---|---|
| Thermal pad | Heat transfer for modem/adapter | Planned |
| Small heatsinks | Cooling electronics | Planned |
| USB hub or internal USB wiring | Device expansion if needed | TBD |

## Future / Optional Hardware

| Item | Purpose | Status |
|---|---|---|
| Dell DW5811e / Sierra Wireless EM7455 GPS | GPS-backed time/location source via NMEA | Tested |
| External GPS antenna | Better GPS reception | Tested |
| OLED/status display | Local status display | Optional |
| Battery voltage monitor | Power telemetry | Optional |
| OpenWRT router/AP | Final client Wi-Fi hardware | TBD |
| Final removable storage | Larger or more rugged file storage | TBD |

## Current Tested Hardware

Current working test hardware:

```text
Raspberry Pi 4
RTC module
USB flash drive
Actiontec T3200 as AP/switch
Home Wi-Fi as temporary uplink
```

Current pending major hardware:

```text
Cellular modem
WWAN USB adapter
GPS/GNSS time source
Final power system
Final enclosure
```

## Notes

This BOM is still evolving.

The current software baseline can run without final hardware, but the EM7455/DW5811e WWAN modem and GPS NMEA path have now been tested. Future EM7565 validation, final power hardware, and final enclosure work remain.
