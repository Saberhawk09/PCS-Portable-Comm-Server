# PCS as-built completion checklist

This is a physical-record checklist, not a new electrical acceptance result.
The commissioned profile and dated operating evidence establish four INA226 roles
and an active-low GPIO13 buzzer. They do not establish every wire, fuse, terminal,
measurement or thermal margin. [Issue #1](https://github.com/Saberhawk09/PCS-Portable-Comm-Server/issues/1)
remains open. Preserve existing measured records and identify their dates/conditions.

- [ ] Record installed PSU/converter part numbers and photographs of labels.
- [ ] Record selector pinout and verify its break-before-make behavior.
- [ ] Record actual inlet, selected-DC and branch fuse values and locations.
- [ ] Record wire gauges, connector types/polarity, routing and strain relief.
- [ ] Record protective-earth bonding and AC terminal guarding.
- [ ] Photograph each INA226's shunt, polarity, position and I2C address wiring;
      distinguish configured 0.002-ohm/20A values from independent verification.
- [ ] Compare each monitor with a trusted reference at idle and a representative
      authorized load; record instruments, conditions and uncertainty.
- [ ] Record idle/peak rail voltages and peak current with source/load identified.
- [ ] Record sustained converter/enclosure temperatures and sensor placement;
      compare the case environment sensor against a reference.
- [ ] Record GPIO13 buzzer polarity, installed pull-up and routing.
- [ ] Record RF/I2C mitigation and a separately authorized bounded acceptance result.
- [ ] Capture enclosure dimensions, mounting, final photographs and CAD references.
- [ ] Reconcile [wiring notes](wiring-notes.md), [power system](../docs/power-system.md)
      and [BOM](../docs/bill-of-materials.md) with the completed physical record.

Do not fill unknown values from design assumptions. This checklist does not
authorize power interruption, load stress, RF transmission or appliance changes.
