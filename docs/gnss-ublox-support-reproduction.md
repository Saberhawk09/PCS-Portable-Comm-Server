# Sanitized u-blox support draft — not sent

Prepared for possible operator-approved support escalation. This document
contains no receiver unique ID, location, account credential or binary database.
Raw failed entries and matching ACKs are retained privately and must not be
attached without separate operator authorization.

## Platform and authorities

NEO-F10N, firmware EXT SPG L1L5 6.00 (041e8a), hardware 000A0000, protocol 40.00;
CH340 UART1 at 115200 baud. Raspberry Pi 4 uses separate GPIO26 kernel PPS.
GPSD is the normal sole serial owner. Maintenance uses an exclusive bounded
handover; no competing UART writer or configuration-to-flash write is involved.

Reviewed [F10 SPG 6.00 interface description, R02](https://content.u-blox.com/sites/default/files/documents/u-blox-F10-SPG-6.00_InterfaceDescription_UBX-23002975.pdf)
and [NEO-F10N integration manual, R04](https://content.u-blox.com/sites/default/files/documents/NEO-F10N_IntegrationManual_UBXDOC-963802114-12193.pdf).
MGA-DBD is treated as opaque, same-receiver/firmware data. ACK status 255 is not
assigned an undocumented meaning.

## A. Database restore rejection

1. Verify identity, firmware, complete dump count, frame lengths/checksums,
   object digest and age. Temporarily enable ACKAIDING in RAM with journaled
   restoration. Stop GNSS using CFG-RST controlled mode 0x08.
2. Send the same captured DBD frames in order, waiting for the exact matching
   MGA-ACK for each frame. Stop on the first rejection/missing ACK. Start GNSS
   with controlled mode 0x09; restore ACKAIDING and serial ownership.
3. A pinned 159-entry backup over retained navigation repeatedly accepts 51
   entries and rejects entry 52, matched type 0/status 255. An added 50 ms pacing
   delay produces the same result. An earlier campaign failed at entry 53;
   the failure index is not invariant across captures/conditions.
4. The pinned 159-entry backup succeeds after explicit software cold start
   (navBbrMask FFFF, controlled mode 02), including three early-start trials with
   independently bounded coarse time supplied while the engine is running,
   before stop/restore/start. A 201-entry backup also succeeds through the worker.
5. A newer valid 199-entry backup rejects entry 62 after 61 accepts, even after
   software cold start. Repeating with the three-second startup wait gives the
   same matching type 0/status 255. There are no duplicate frames. Failed record
   bytes differ from the corresponding prefix record of the older 201-entry
   backup; no interpretation of the opaque contents is asserted.

Each failure terminates transfer, preserves the original backup and returns
ordinary acquisition/UTC/PPS/F10P without automatic reset. Configuration readback
matches baseline. Cold start is not sufficient to qualify arbitrary restoration.

Questions: Is replacement over retained navigation supported? What precise engine
state, clearing procedure and pacing are required? What does status 255 mean for
MGA-DBD? Are there content/state restrictions beyond exact receiver and firmware?

## B. Running-engine dump output

Two separately tested receiver-running dump candidates failed strict validation.
One produced a checksum-valid MGA-DBD frame with a 760-byte payload, versus the
documented 164-byte maximum. It was not saved as a usable backup. The last valid
backup survived and timing recovered. Production keeps stop/dump/start and does
not relax length/checksum validation.

Question: Is running-engine DBD collection supported on this firmware, and is any
larger format documented? Can a complete live dump be safely delimited without
stopping acquisition?

## C. Stop-associated PPS behavior

An earlier complete snapshot recovered valid UTC and USB timing, but kernel PPS
did not advance within 120 seconds. An authorized hot start recovered PPS.
Five subsequent completed baseline snapshots recovered F10P without intervention.
One stopped interval nevertheless produced approximately 244 kernel events/s
(about 738 extra edges), with GNSS references withdrawn throughout. Another
instrumented stop showed GPIO26 input/no bias/low and no edges, with unchanged
MON-HW3 masks. Completed readbacks preserved TIMEPULSE/ACKAIDING settings.

No scope trace was available during the anomalous interval. TIM-TP cannot prove
a physical edge, and receiver versus wiring/GPIO/kernel causation remains unknown.
GPIO pulls and drive were not altered because TIMEPULSE shares SAFEBOOT.

Questions: What pulse behavior is guaranteed across controlled stop/start? Are
there known SPG 6.00 database-operation/TIMEPULSE interactions or recommended
recovery sequences that preserve configuration without reset?

Automatic restore and shutdown capture remain independently held and disabled.
Full sanitized result tables are in [the maintenance report](gnss-maintenance-reliability.md).
