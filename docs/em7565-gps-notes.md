# EM7565 GPS / GNSS Notes

## Current PCS commissioning state — November 15, 2026

NEO-F10N supplies all PCS GNSS positioning and timing, with GPIO26/pin37 PPS.
The operator separately authorized GNSS-only AT maintenance and a brief
ModemManager stop/start. EM7565 sessions were ended, GPSENABLE changed from 1
to 0, and GPSAUTOSTART changed from function 2 to 0. Readback verified both
disable settings and no active fix session. LTE settings and active network
connections were preserved; the final PCS self-test passed. No modem reset or
PCS reboot was performed during that initial shutdown transaction.

Subsequent release validation included two PCS software reboots and one
separately authorized actual modem reset. Query-only readback after that reset
confirmed GPSENABLE=0, GPSAUTOSTART=0 and fix session NONE. The repeated PCS
reboot automatically recovered MBIM/LTE with GNSS still disabled. The first
reboot's MBIM failure and its recovery are retained in the
[v2.4.0 validation record](v2.4.0-validation.md). Physical cold-power acceptance
remains separate.

Root-only evidence and original settings are retained on the appliance at
`/var/backups/pcs-em7565-gnss-otmawogp/`. A supervised modem rollback restores
GPSENABLE=1 and GPSAUTOSTART=2,1,180,50,1 using protected authentication, exclusive
AT access and readback verification. It does not switch PCS's selected source.
The earlier PCS software rollback alone no longer restores modem GNSS operation.
See [the separate hardware-validation addendum](gnss-pps-hardware-validation-2026-11-15.md#subsequent-authorized-em7565-gnss-shutdown).

The baseline and antenna instructions below describe the historical modem path.
They are not instructions to re-enable it on the commissioned F10 installation.

Optional shared AUX/GNSS reception is **unproven**, and is not a qualified backup.
See [the guarded manufacturer-based procedure](gnss-pps-commissioning.md#phase-3--optional-em7565-shared-auxgnss).
The former dedicated GNSS bulkhead is now used by NEO-F10N; MAIN and LTE diversity
remain untouched. Do not run the historical installer as a receiver migration.

The remaining sections capture the historical known-good EM7565 modem path.

The PCS build uses a generic WWAN NMEA/gpsd/Chrony software path for modem GPS.

## Known-Good Baseline

Historical known-good state before F10 commissioning:

```text
Modem:       Sierra Wireless / Semtech EM7565
USB mode:    diag,nmea,modem,mbim
GPS port:    /dev/ttyUSB1
GPS RF path: dedicated GNSS connector / GPS SMA path
Bias power:  about 3.1-3.3 V at GPS SMA center pin
NMEA:        present
gpsd:        receiving NMEA
cgps:        shows satellites when antenna has sky view
Dashboard:   shows GPS data when gpsd has usable data
Chrony:      can see GPS source for LAN NTP
```

## Important Hardware Lesson

If NMEA is present but the modem cannot get a valid fix, do not assume gpsd or Chrony is the problem.

The known PCS bench issue was a bad, open, poorly seated, or wrong-path MHF4-to-SMA GPS pigtail. Once the active GPS antenna received bias power through the GPS SMA path, satellites appeared in `cgps` and the dashboard showed good GPS data.

## Active GPS Antenna Bias

An active GPS antenna needs DC bias power from the modem/adapter path.

Expected GPS SMA bias:

```text
about 3.1-3.3 VDC
```

Test with no antenna attached:

```text
Meter:       DC volts
Red probe:   GPS SMA center pin
Black probe: GPS SMA shell / ground
Expected:    about 3.1-3.3 VDC
```

Be careful not to bridge the SMA center pin to the shell.

If the GPS SMA shows 0 V:

- confirm the MHF4 pigtail is on the correct modem connector
- confirm the pigtail is seated correctly
- try another known-good pigtail
- check whether the adapter board passes DC bias to the SMA
- confirm GNSS antenna power is enabled
- confirm the selected GNSS RF path matches the SMA being measured

## Relevant AT Settings

Current notes from the validated path:

```text
AT+WANT=1
```

`AT+WANT=1` enables GNSS antenna power on supported Sierra/Semtech modems and is expected to persist across power cycles.

If the modem is configured intentionally from an AT command session, the GPS RF path may also need to be selected:

```text
AT!ENTERCND="<operator-provided key; never log>"
AT!CUSTOM="GPSSEL",0
AT+WANT=1
# Apply only the activation action documented for the installed firmware.
```

Use these only when intentionally configuring the modem. Do not change AT settings blindly on a working modem.

Do not query ENTERCND? or place credentials in shell arguments, terminal logs or
source. GPSSEL activation/reset requirements must be established for the exact
firmware before writing it; a reset requires separate approval.

## GPS Fix Test

After connecting the active GPS antenna with sky view:

```bash
sudo systemctl restart gpsd.socket gpsd
sleep 5
gpspipe -r -n 40
```

Bad or no fix examples:

```text
$GPRMC,,V,...
$GPGGA,,,,,,0,...
```

Good / valid fix examples:

```text
$GPRMC,...,A,...
$GPGGA,...,1,...
```

Then check:

```bash
cgps
chronyc sources -v
```

Give the modem a few minutes, especially after a cold start or when testing indoors.

## Known-Good Capture

After GPS is working, capture a baseline:

```bash
mkdir -p ~/em7565-baseline
TS="$(date +%Y%m%d-%H%M%S)"

{
  echo "===== PCS EM7565 GPS KNOWN GOOD $TS ====="
  echo
  mmcli -m 0
  echo
  echo "===== gpspipe raw ====="
  gpspipe -r -n 30
  echo
  echo "===== gpspipe json ====="
  gpspipe -w -n 10
  echo
  echo "===== chrony ====="
  chronyc sources -v
  chronyc tracking
} | tee ~/em7565-baseline/pcs-em7565-gps-known-good-$TS.txt
```

## Troubleshooting Summary

If `/dev/ttyUSB1` has NMEA output but GPS never gets a fix:

1. Check GPS SMA bias voltage.
2. Check MHF4 pigtail continuity, seating, and connector choice.
3. Check active GPS antenna connection and sky view.
4. Confirm `AT+WANT=1`.
5. Confirm the selected GNSS RF path matches the adapter/SMA path.
6. Restart `gpsd` and retest with `gpspipe`, `cgps`, and `chronyc sources -v`.
