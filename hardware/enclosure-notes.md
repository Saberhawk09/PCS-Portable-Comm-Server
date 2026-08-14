# Enclosure Notes

> The PCS hardware is assembled and usable. Exact as-built dimensions, fasteners, mounting locations, photos, and CAD/export references still need to be recorded here.

## Design Goal

The PCS enclosure should organize all major components into a compact, rugged, field-usable package.

The current concept is closer to a luggable computer than a generic electronics box. Inspiration includes older portable computers where the front face is protected when the cover is closed.

The enclosure design uses or anticipates these printable materials:

- PETG for the main enclosure
- TPU for bumpers, feet, or impact protection

## Case Style

Current design style:

- luggable PC form factor
- protected front IO when the cover is closed
- front-accessible service ports when the cover is open
- rugged enough for portable radio/event use
- modular enough to revise individual IO shields

The front panel is not expected to be one fully custom integrated PCB-style panel. Instead, the current concept uses individual 3D printed IO shields around the discrete components.

Front IO design includes access for:

- Raspberry Pi 4 ports
- Linksys EA4500 Ethernet ports
- WWAN modem enclosure ports and antenna connectors
- AC input
- DC input
- source selector
- status indicators

## External Features

Documented external access:

- AC input through switched/fused IEC C14 inlet
- DC input through Anderson Powerpole inlet
- source selector switch
- Ethernet ports
- USB/service access, if needed
- LTE antenna connectors
- GPS antenna connector
- possible power/status indicator lights

## Internal Mounting

Internal components to mount:

- Raspberry Pi 4
- WWAN M.2 to USB adapter / modem enclosure
- Linksys EA4500 running OpenWrt
- Mean Well LRS-100-24 internal AC/DC power supply
- SparkFun COM-18732 main 12 V buck/boost converter
- Mean Well PSD-30A-5 5 V converter
- fuse holders
- power/source switches
- removable storage

## Power System Layout

The documented power system uses:

```text
AC or external DC input
        |
        v
DPDT source selector
        |
        v
7.5 A slow-blow main selected-DC fuse
        |
        v
SparkFun COM-18732 regulated 12 V rail
        |
        +-- Linksys EA4500 / 12 V loads
        +-- Mean Well PSD-30A-5 -> 5 V Pi stack
```

The Mean Well PSD-30A-5 must be downstream of the regulated 12 V rail. It should not be fed from raw 24 V input.

## Cooling

The enclosure should include airflow near heat-producing components.

Likely heat sources:

- SparkFun COM-18732
- Mean Well PSD-30A-5
- Mean Well LRS-100-24
- Raspberry Pi 4
- WWAN modem / USB adapter
- Linksys EA4500

The COM-18732 is the main power-path bottleneck and should get deliberate airflow and a sensible mounting location.

## AC Safety

The internal AC/DC supply introduces real AC wiring inside the enclosure.

Safety planning:

- keep AC wiring physically separated from low-voltage DC wiring
- use strain relief at the AC inlet
- bond AC safety earth to the PSU frame/FG and any exposed metal chassis parts
- cover AC terminals with a proper terminal cover, guard, or insulating shield
- do not rely on PETG alone as the only AC safety barrier
- keep service access from casually exposing AC terminals

## Serviceability Notes

The design should allow access to:

- SD card
- external flash drive
- fuses
- antenna pigtails
- power wiring
- Pi USB/Ethernet ports
- EA4500 Ethernet ports
- modem enclosure / antenna connections

## Labeling

External labels should be simple and obvious:

- AC IN
- DC IN
- SOURCE SELECT
- OFF
- LTE
- GPS
- LAN
- WAN / UPLINK if used
- POWER
- 12 V
- 5 V
