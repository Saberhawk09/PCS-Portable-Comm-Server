# PCS Power System

PCS is planned to support both emergency DC power and normal AC grid power.

This document is a planning/reference document. Final wiring should be checked against actual part ratings, enclosure layout, fuse ratings, wire gauge, heat, and safe AC wiring practices.

This is not a certified electrical drawing.

## Current Proposed Architecture

The current planned architecture uses one regulated 12 V backbone regardless of whether PCS is powered from AC or external DC.

```text
120 VAC input
        |
        v
Mean Well LRS-100-24
24 VDC, 4.5 A, 108 W
        |
        v
DPDT center-off source selector
        |
        v
7.5 A slow-blow selected-DC fuse
        |
        v
SparkFun COM-18732 buck/boost converter
8-36 V input -> regulated 12 VDC, 6 A, 72 W
        |
        v
Regulated 12 V bus
        |
        +-- Linksys EA4500 / 12 V loads
        +-- 12 V fan or accessories
        +-- Mean Well PSD-30A-5
                9-18 V input -> regulated 5 VDC, 5 A
                |
                v
            Raspberry Pi 4 / fan / USB flash / WWAN adapter
```

External DC power feeds the same source selector:

```text
External Anderson Powerpole input
10-24 VDC nominal
        |
        v
DPDT center-off source selector
```

The source selector chooses either the internal 24 V supply or the external DC input. The selected DC output then feeds the COM-18732, which creates the regulated 12 V rail.

## Main Parts

| Part | Role | Notes |
| --- | --- | --- |
| Switched/fused IEC C14 inlet | AC input | Keep AC wiring guarded and separated |
| Mean Well LRS-100-24 | Internal AC/DC supply | 120 VAC input, 24 VDC 4.5 A / 108 W output |
| Anderson Powerpole inlet | External DC input | 10-24 VDC nominal field input |
| DPDT center-off switch | Source selector | Prefer switching both positive and negative |
| 7.5 A slow-blow fuse | Main selected-DC fuse | Goes after source selector and before COM-18732 |
| SparkFun COM-18732 | Main 12 V regulator | 8-36 V input, regulated 12 V 6 A / 72 W output |
| Mean Well PSD-30A-5 | 5 V converter | Must be downstream of regulated 12 V bus |

## Important Converter Rule

The Mean Well PSD-30A-5 must be downstream of the regulated 12 V rail.

Do not feed the PSD-30A-5 directly from the raw 24 V supply or the external 10-24 V input.

```text
Correct:
COM-18732 regulated 12 V -> PSD-30A-5 -> 5 V rail

Incorrect:
Raw 24 V input -> PSD-30A-5
```

## Power Budget

Current planning budget:

```text
Router / AP budget:     12 V x 3 A = 36 W
Pi stack budget:         5 V x 3 A = 15 W
```

With converter losses, the 12 V regulator sees roughly:

```text
Router branch:           36 W
5 V converter input:     about 19.5 W
Total 12 V bus load:     about 55.5 W
```

COM-18732 rating:

```text
12 V x 6 A = 72 W
```

Expected use:

```text
55.5 W / 72 W = about 77 percent
```

That is acceptable for the planned build, but the COM-18732 should not be buried or treated as a no-heat part. Design the enclosure around airflow and mounting for this converter.

Design target:

```text
Normal max:      about 40-55 W
Surge/headroom:  up to 72 W
```

## Source Selector

Use a DPDT center-off selector if possible.

Preferred switch behavior:

```text
Up:      internal 24 V PSU
Center:  off
Down:    external DC input
```

Preferred switching:

```text
Internal PSU +24 V  --\
                       +-- selected DC positive -> 7.5 A fuse -> COM-18732 VIN+
External DC positive --/

Internal PSU 0 V     --\
                       +-- selected DC negative -> COM-18732 VIN-
External DC negative --/
```

Switching both positive and negative/source return keeps the internal AC/DC supply and external field input isolated from each other when not selected.

## Fuse Planning

Current planned fuses:

```text
Main selected-DC input fuse:  7.5 A slow-blow
Router 12 V branch:          3 A
PSD-30A-5 input branch:      3 A
Optional 5 V output fuse:    3 A to 5 A
```

Preferred single main fuse placement:

```text
Source selector common positive output
        |
        v
7.5 A slow-blow fuse
        |
        v
COM-18732 VIN+
```

A single fuse after source selection does not protect the short positive runs from the Anderson inlet or LRS-100-24 output to the selector. Keep those runs short, insulated, strain-relieved, and sized appropriately.

More protective wiring would fuse each source before the selector:

```text
LRS-100-24 +24 V -> fuse -> source selector
Anderson +DC     -> fuse -> source selector
```

## 12 V Rail

The COM-18732 creates the main regulated 12 V bus.

The 12 V bus may power:

- Linksys EA4500 / access point hardware
- 12 V fan or case accessories
- Mean Well PSD-30A-5 input
- future low-current 12 V accessories

## 5 V Rail

The Mean Well PSD-30A-5 creates the 5 V rail from the regulated 12 V bus.

Planned 5 V loads:

- Raspberry Pi 4
- Pi fan
- USB flash drive
- WWAN modem USB adapter
- future 5 V accessories if budget allows

Current planning budget:

```text
5 V rail:      5 V at 5 A available
Pi stack use:  about 5 V at 3 A budget
```

## Cooling Notes

Likely heat sources:

- SparkFun COM-18732
- Mean Well PSD-30A-5
- Mean Well LRS-100-24
- Raspberry Pi 4
- WWAN modem / USB adapter
- Linksys EA4500

The COM-18732 is the main power-path bottleneck and should get deliberate airflow.

## Safety Notes

- Keep AC wiring physically separated from low-voltage DC wiring.
- Use strain relief for AC and DC inlets.
- Use appropriate wire gauge for each fused branch.
- Fuse close to power entry points where practical.
- Avoid exposed AC terminals inside the enclosure.
- Cover AC terminals with a proper guard, terminal cover, or insulating shield.
- Do not rely on PETG alone as the only AC safety barrier.
- Confirm switch ratings for DC use.
- Prefer break-before-make source switching.
- Do not connect AC-derived DC and external DC together directly.
- Bond AC safety earth to the C14 earth terminal, PSU frame/FG, and exposed metal chassis parts if present.

## Current Status

The current proposed power architecture is defined, but final wiring and enclosure implementation are still pending.

Software testing is currently being done from normal Raspberry Pi power while the final case and power system are developed.
