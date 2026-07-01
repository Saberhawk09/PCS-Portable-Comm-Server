# Enclosure Notes

> Draft enclosure planning notes. Final dimensions and mounting details are TBD.

## Design Goal

The PCS enclosure should organize all major components into a compact, rugged, field-usable package.

The case is planned to be 3D printed, primarily using:

- PETG for the main enclosure
- TPU for bumpers, feet, or impact protection

## External Features

Planned external access:

- AC input through switched/fused IEC C14 inlet
- DC input through Anderson Powerpole inlet
- Source selector switch
- Ethernet ports
- USB/service access, if needed
- LTE antenna connectors
- GPS antenna connector
- Possible power/status indicator lights

## Internal Mounting

Internal components to mount:

- Raspberry Pi 4
- WWAN M.2 to USB adapter
- Router or OpenWRT router
- 12V 3A power supply
- 12V to 5V buck converter
- Fuse holders
- Power switches
- Removable storage

## Cooling

The enclosure should include vents near heat-producing components.

Likely heat sources:

- Raspberry Pi 4
- WWAN modem / USB adapter
- Router
- 12V PSU
- 5V buck converter

## Serviceability Notes

The design should allow access to:

- SD card
- External flash drive
- Fuses
- Antenna pigtails
- Power wiring
- Pi USB/Ethernet ports

## Labeling

External labels should be simple and obvious:

- AC IN
- DC IN
- SOURCE SELECT
- OFF
- LTE
- GPS
- LAN
- WAN
- POWER