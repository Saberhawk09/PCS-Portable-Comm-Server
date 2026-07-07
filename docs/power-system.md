# PCS Power System

PCS is planned to support both emergency DC power and normal AC grid power.

This document is a planning/reference document. Final wiring should be checked against actual part ratings, enclosure layout, fuse ratings, wire gauge, and safe AC wiring practices.

## Planned Inputs

PCS is planned to support two power sources:

```text
12-24 VDC input via Anderson Powerpole
120 VAC input via fused/switched IEC C14 inlet
```

## Planned Internal Power Layout

```text
DC input / AC-derived 12 V
        |
Source select switch
        |
12 V internal bus
        |
Branch fusing
        |
Router / 12 V loads
        |
12 V -> 5 V buck converter
        |
Raspberry Pi / USB modem hardware
```

## DC Input

Planned DC input:

```text
Connector: Anderson Powerpole
Input range: 12-24 VDC
```

The DC input should be fused close to the inlet.

Current planning value:

```text
DC input fuse: 3 A
```

## AC Input

Planned AC input:

```text
Connector: switched/fused IEC C14 inlet
Internal PSU: 120 VAC -> 12 VDC
```

The AC input should remain physically separated from low-voltage DC wiring inside the enclosure.

## Source Switching

The current planned source switch is a DPDT center-off toggle switch.

Planned switch behavior:

```text
Up:     DC input
Middle: Off
Down:   AC-derived 12 V
```

Preferred switch type:

```text
DPDT
Center-off
Break-before-make
Rated for expected DC current and voltage
```

A 6-terminal DPDT switch allows both positive and negative/source return conductors to be switched if desired.

## Internal 12 V Bus

Planned internal bus:

```text
Voltage: 12 VDC nominal
Fuse:    3 A
```

The 12 V bus may power:

- Router / access point hardware
- 12 V accessories
- 12 V to 5 V buck converter

## 5 V Rail

The 5 V rail is created from the 12 V bus with a buck converter.

Planned use:

- Raspberry Pi 4
- WWAN modem USB adapter
- Other 5 V accessories if needed

Current planning estimate:

```text
Buck converter output: 5 V
Maximum output:        5 A
```

## Estimated Loads

Rough current estimates:

```text
Raspberry Pi 4:      ~1.5 A typical at 5 V
WWAN USB adapter:    ~0.8 A peak at 5 V
Router/AP:           TBD
Estimated 5 V total: ~2.5-3.0 A
```

These are planning estimates. Final fuse sizing should be based on actual measured current draw and device ratings.

## Fuse Planning

Planned fuse locations:

```text
DC input fuse
AC inlet fuse
12 V internal bus fuse
Optional branch fuses for major loads
```

Current known/planned value:

```text
12 V bus / DC input: 3 A
```

Final fuse sizing should protect the wiring and device branches, not just the load.

## Safety Notes

- Keep AC wiring physically separated from low-voltage DC wiring.
- Use strain relief for AC and DC inlets.
- Use appropriate wire gauge for each fused branch.
- Fuse close to power entry points.
- Avoid exposing AC terminals inside the enclosure.
- Confirm switch ratings for DC use.
- Prefer break-before-make source switching.
- Do not connect AC-derived 12 V and external DC input together directly.

## Current Status

The power system is still in the planning/build stage.

Software testing is currently being done from normal Raspberry Pi power, with the final enclosure power system still pending.
