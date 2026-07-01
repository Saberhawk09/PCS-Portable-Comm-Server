# Wiring Notes

> Draft wiring notes. This is not a finalized schematic.

## Source Selector

Planned switch: 6-pin DPDT on-off-on toggle.

| Switch Position | Function |
|---|---|
| Up | DC input from Anderson Powerpole |
| Center | Off |
| Down | AC-derived 12V from internal PSU |

## Power Flow

```text
AC Input:
C14 inlet → internal 12V PSU → DPDT source selector → 12V rail

DC Input:
Anderson Powerpole → fuse → DPDT source selector → 12V rail

12V Rail:
12V rail → 3A fuse → 12V loads / 5V buck converter

5V Rail:
12V-to-5V buck → Raspberry Pi / WWAN adapter