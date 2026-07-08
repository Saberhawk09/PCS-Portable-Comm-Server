# EM7565 GPS / GNSS Notes

This document captures the current known-good PCS GPS/GNSS behavior for the EM7565 modem path.

The PCS build uses a generic WWAN NMEA/gpsd/Chrony software path for modem GPS.

## Known-Good Baseline

Current known-good state:

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
AT!ENTERCND="A710"
AT!CUSTOM="GPSSEL",0
AT+WANT=1
AT!RESET
```

Use these only when intentionally configuring the modem. Do not change AT settings blindly on a working modem.

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
