# Power System

The PCS power system is designed to support both grid power and field / emergency DC power.

The system has two possible power sources:

* 12–24 VDC input through an Anderson Powerpole inlet
* 120 VAC input through a switched and fused IEC C14 inlet

Only one source should be selected at a time.

## Power Source Selection

Source selection is handled by a 6-pin DPDT on-off-on toggle switch.

Planned switch orientation:

| Switch Position | Selected Source                 |
| --------------- | ------------------------------- |
| Up              | DC input via Anderson Powerpole |
| Center          | Off                             |
| Down            | AC input via internal 12V PSU   |

The AC input feeds an internal 12V 3A power supply. The DC input feeds the 12V system directly through the Anderson Powerpole inlet.

The selected source feeds the internal 12V DC rail.

## AC Input Path

```text
120 VAC inlet
    ↓
Switched / fused IEC C14 inlet
    ↓
Internal 12V 3A power supply
    ↓
DPDT source selector
    ↓
12V internal rail
```

## DC Input Path

```text
12–24 VDC Anderson Powerpole inlet
    ↓
DC input fuse
    ↓
DPDT source selector
    ↓
12V internal rail
```

## Internal 12V Rail

The internal DC bus is treated as a 12V rail and is fused at 3A.

This rail may power:

* 12V router hardware, if used
* 12V to 5V buck converter
* Future 12V accessories, if added

## 5V Rail

A 12V to 5V buck converter provides the 5V rail.

The buck converter is rated for up to 5A output.

The 5V rail is planned to power:

* Raspberry Pi 4
* USB WWAN modem adapter
* Other low-voltage USB/server hardware as needed

Estimated 5V draw:

| Device         | Estimated Current |
| -------------- | ----------------: |
| Raspberry Pi 4 |      1.5A typical |
| WWAN adapter   |         0.8A peak |
| Router         |               TBD |

Estimated total 5V draw is approximately 2.5–3.0A, not including any future accessories.

## Notes / Safety Considerations

The AC and DC inputs could technically be connected to live sources at the same time, but this should be avoided during normal use.

The source selector switch should ideally be break-before-make so the AC-derived 12V supply and external DC input are never briefly tied together during switching.

The DPDT switch allows both sides of the selected DC source to be switched, instead of only switching the positive rail.

Final fuse placement and wire sizing should be verified before enclosure assembly.
