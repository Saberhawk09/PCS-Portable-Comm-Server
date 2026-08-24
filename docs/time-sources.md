# PCS Time-Source Hierarchy

PCS serves NTP to trusted clients on `10.42.0.0/24` from the Raspberry Pi at
`10.42.0.1`. Chrony uses this order:

1. WWAN GPS NMEA through gpsd SHM 0, when Chrony considers it selectable.
2. Public Internet NTP, when GPS is unavailable or rejected.
3. The RTC-seeded system clock as degraded local holdover, when neither GPS nor
   Internet NTP is selectable.

The GPS refclock uses Chrony's `prefer` option. It does not use `trust`: a
reachable but inconsistent GPS sample must still pass Chrony's source-selection
safety checks. Internet NTP therefore remains both a fallback and an independent
sanity check.

The DS1307 is not configured as a continuously sampled Chrony refclock.
`pcs-rtc-seed.service` reads it once before Chrony starts, and rejects unreadable
or implausible dates. Chrony's `rtcsync` then copies authoritative GPS/Internet
time back to the RTC while synchronized. The local server advertises stratum 10
during holdover so clients can recognize that its time is degraded.

## Installed Configuration

The base installer runs both:

```bash
./scripts/setup-rtc.sh
./scripts/setup-chrony-lan-ntp.sh
```

When WWAN GPS is selected, it also runs:

```bash
./scripts/setup-wwan-gps-nmea.sh
```

The managed settings are:

```text
GPS:       refclock SHM 0 ... refid GPS ... prefer
Internet:  pool pool.ntp.org iburst maxsources 4
Holdover:  local stratum 10
RTC write: rtcsync
LAN:       allow 10.42.0.0/24
```

The Raspberry Pi OS Chrony defaults may provide additional Internet servers.
They remain secondary because they do not have the `prefer` option.

## Verification

```bash
systemctl status pcs-rtc-seed.service chrony.service gpsd.service --no-pager
sudo /usr/local/sbin/pcs-rtc-seed --check
chronyc tracking
chronyc sources -v
chronyc selectdata -a -v
./scripts/pcs-self-test.sh
```

In `chronyc sources -v`, `#* GPS` means GPS is selected. An Internet source with
`^*` means Internet NTP is selected. A tracking reference of `127.127.1.1`
(`7F7F0101`) at stratum 10 means local RTC/system-clock holdover is active.

Immediately after boot or source restoration, allow Chrony enough time to
collect multiple samples before judging selection.

## Controlled Failover Test

Run this only from a local terminal or a connection that does not depend on
Internet routing:

```bash
sudo ./scripts/test-time-source-failover.sh --run
```

The test temporarily excludes GPS and bursts the network sources to prove
Internet selection. It then marks only Chrony's network sources offline and
resets volatile source measurements to reproduce a cold start with neither
authoritative source available. This proves local holdover, verifies the
dashboard labels in each state, and checks the RTC without setting the system
clock. An EXIT trap restores network sources and GPS selection on every exit
path, and the test does not pass until GPS is selected again. These runtime
changes do not persist across a Chrony restart.

During an ordinary mid-session loss, Chrony can continue using its last
disciplined system-clock estimate until its uncertainty reaches the local-mode
distance threshold. That is more accurate than rereading the DS1307. The RTC is
the cold-boot seed; it is not used to pull an already disciplined running clock
backward.

Do not write the system clock to the RTC during the local fallback test. The PCS
admin action writes the RTC only after Chrony reports a normal, non-local
GPS/Internet synchronization.
