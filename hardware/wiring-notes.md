# Wiring Notes

> Draft wiring notes. This is not a finalized schematic.

## Current Power Flow

The current proposed PCS power path uses a regulated 12 V backbone.

```text
AC Input:
C14 inlet -> Mean Well LRS-100-24 -> 24 VDC -> DPDT source selector

DC Input:
Anderson Powerpole 10-24 VDC -> DPDT source selector

Selected DC:
DPDT source selector -> 7.5 A slow-blow fuse -> SparkFun COM-18732

12 V Rail:
COM-18732 -> regulated 12 V bus
regulated 12 V bus -> branch fuses -> Linksys EA4500 / fans / accessories
regulated 12 V bus -> Mean Well PSD-30A-5

5 V Rail:
PSD-30A-5 -> regulated 5 V bus -> Raspberry Pi / WWAN adapter / USB storage
```

## Source Selector

Planned switch: 6-pin DPDT center-off toggle.

| Switch Position | Function |
| --- | --- |
| Up | Internal 24 V supply from Mean Well LRS-100-24 |
| Center | Off |
| Down | External DC input from Anderson Powerpole |

Preferred wiring switches both positive and negative/source return.

```text
Internal PSU +24 V  --\
                       +-- selected DC positive -> main fuse -> COM-18732 VIN+
External DC positive --/

Internal PSU 0 V     --\
                       +-- selected DC negative -> COM-18732 VIN-
External DC negative --/
```

## Main Fuse

Current planned main fuse:

```text
7.5 A slow-blow
```

Preferred placement:

```text
source selector common positive output -> 7.5 A slow-blow fuse -> COM-18732 VIN+
```

This protects the common selected-DC feed into the main 12 V regulator. Keep unfused source-to-selector runs short, insulated, strain-relieved, and properly sized.

## Branch Fuses

Current branch fuse planning:

```text
Router / AP 12 V branch:     3 A
PSD-30A-5 input branch:      3 A
Optional 5 V output branch:  3 A to 5 A
```

Final fuse sizing should be based on actual measured current draw and wire gauge.

## Converter Notes

The SparkFun COM-18732 is the main 12 V buck/boost converter.

```text
Input:   8-36 VDC
Output:  regulated 12 VDC, up to 6 A / 72 W
```

The Mean Well PSD-30A-5 creates the 5 V rail.

```text
Input:   9-18 VDC
Output:  regulated 5 VDC, up to 5 A
```

The PSD-30A-5 must be fed from the regulated 12 V bus, not directly from raw 24 V input.

## AC Safety Notes

- Keep AC wiring physically separated from low-voltage DC wiring.
- Use the C14 inlet fuse/switch as intended.
- Bond AC safety earth to the internal PSU frame/FG and any exposed metal chassis parts.
- Cover the AC terminals so they cannot be touched with fingers or loose tools.
- Do not rely on a printed PETG wall alone as the only AC safety barrier.
