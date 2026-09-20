# PCS Power System

PCS supports a design intended for both emergency DC power and normal AC grid power.

The PCS hardware is operational, but this document remains a design/reference record until every value is reconciled with the physical as-built wiring. Check actual part ratings, enclosure layout, fuse ratings, wire gauge, heat, and safe AC wiring practices before using it for repair or replication.

This is not a certified electrical drawing.

## Documented Power Architecture

The documented architecture uses one regulated 12 V backbone regardless of whether PCS is powered from AC or external DC.

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

Documented design budget:

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

That is within the documented converter rating, but it leaves limited design
headroom and is not a measured as-built load. The COM-18732 should not be buried
or treated as a no-heat part; its mounting, airflow, current, and temperature
still require verification against the physical PCS.

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

Documented fuse plan:

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

The PCS hardware assembly is operational. The next documentation pass must record the physical as-built system rather than infer it from this design document.

Before this file is treated as an as-built electrical record, capture and verify:

- installed converter and power-supply part numbers
- source-selector pinout and break-before-make behavior
- actual fuse values and locations
- wire gauge, connector type, polarity, and grounding
- measured 12 V and 5 V rail voltage under idle and peak load
- peak current draw and converter temperature
- AC terminal guarding, strain relief, and protective-earth bonding
## INA226 Power Monitoring

PCS v1.8 introduced the input/5V monitors, since-boot Ah/Wh tracking, low-voltage
protection, coordinated PCS/Pi-Star shutdown, and GPIO13 audible status.
Supervised bench acceptance covered warning, recovery, shutdown, Pi-Star
handoff, and all audible patterns. Readings and health are integrated into
self-test, web/API/Android status, the LCD, and physical indicators.

PCS v1.8.1 added guarded full-appliance stress testing, persistent high-rate
rail/thermal recording, upload streaming and watchdog-reset fixes, and
exclusive INA226 ownership during diagnostics. A five-minute full non-RF run
completed with zero sample errors or throttling. This does not establish RF
immunity: SA818S/shared-I2C coupling remains unresolved. Both monitors were
restored and operationally checked after the September 9 Lite reinstall;
that check is not a new electrical calibration or shutdown bench test.

The upstream PCS input monitor was commissioned on September 7, 2026 at I2C
address `0x40` with an `R002` 2 milliohm shunt and advertised 20 A range. Its
TI manufacturer and INA226 die IDs matched, and live readings were stable near
23.96 V, 0.9 A, and 22 W. Those readings are plausible but still require a
trusted-meter comparison before being treated as calibrated physical evidence.
The 5V rail INA226 was commissioned on September 7, 2026 at address `0x4c`
with the same `R002` 2 milliohm shunt and advertised 20 A range. Its identity
registers matched the input monitor and its initial bus-voltage sample was
5.22 V. Both monitors still require comparison with a trusted meter before
their current and power readings are treated as calibrated physical evidence.

The intended roles are:

- `input` (`0x40`, commissioned): upstream of PCS conversion and authoritative
  for source voltage, total current, and total PCS input power.
- `rail_5v` (`0x4c`, commissioned): 5V voltage, current, and power.
- `rail_12v` (staged, disabled): dedicated 12V voltage, current, power, and
  since-boot charge/energy. Planned address is `0x4d`; solder selection and calibration await hardware confirmation.

The input monitor may also be commissioned by itself if the 5V monitor is
temporarily removed. In that state input measurements and low-voltage status
remain available, while the 5V and estimated non-5V fields are explicitly
reported as not commissioned rather than as a monitor fault.

The reported non-5V value is `input power - 5V power`. It is explicitly an
estimate that includes DC/DC conversion losses, not an exact 12V rail reading.
The dedicated `rail_12v` reading is separate from that estimate and is never
added to total input power (which would double-count downstream consumption).

Both configuration templates include `rail_12v` with `enabled: false` and the
planned address `0x4d` (A1 to SCL, A0 to VS/VCC). On the pictured module,
select the `L` (SCL) pad pair on A1 and the `U` pad pair on A0, removing any
previous selection on those rows. Change solder links only with power disconnected;
VS/VCC means the module logic supply, not the monitored 12V rail. Confirm the
address after wiring before enabling. Disabled monitors are not accessed or reported as offline.
After confirming the third module's address, shunt, and rated current, fill in
`address`, `shunt_ohms`, and `max_current_amps`, then set `enabled: true`.
Run `pcs-power-monitor check-config --config /etc/pcs/power-monitor.json`
before a separately authorized installation/restart. Enabled addresses must be
unique. Adding the monitor preserves existing same-boot energy totals; the new
monitor starts at zero. Hardware wiring and meter comparison remain pending.

The collector, public dashboard, control panel, stats API, self-test, and stress
logger support the third role. Its offline/current faults contribute to overall
health. No dedicated 12V voltage limits have been commissioned; source shutdown
continues to use the input monitor alone. The LCD retains its input/5V power
page and total-input usage page; APRS retains the total-input telemetry format.

Low-voltage protection defaults to 11.5V with 0.3V recovery hysteresis, three
consecutive low samples, and a 90-second countdown. In `auto` source mode the
first plausible reading classifies a source at or above 18V as nominal 24V and
does not apply the nominal-12V cutoff to it. Controlled shutdown is separately
gated by `allow_shutdown`; it was armed only after supervised hardware
validation demonstrated correct scaling, polarity, recovery cancellation, and
shutdown behavior. When the countdown expires, the guard invokes the standard
coordinated shutdown dispatcher so a paired Pi-Star receives its clean
poweroff request before PCS powers off. A direct PCS poweroff is retained only
as a fail-safe fallback if that dispatcher itself cannot run.

The normal 16x2 LCD rotation includes one compact power page. In Battery mode
its first row retains the `IN` label while showing source voltage and source-aware
total watts, including the separate Starlink branch; in Power Supply mode it
shows PCS input-only watts. Its second row shows 5V rail voltage and watts. A second
`Total PWR Usage` page shows the source-aware total
charge in Ah and energy in Wh since boot: PCS input plus the separate Starlink
branch in Battery mode, or PCS input alone in Power Supply mode. The standard
concise self-test reports separate Input Power,
5V Rail, and Power Protection rows with live measurements. The public and
authenticated web status views expose the full voltage, current, power,
since-boot mAh/Wh totals, per-monitor health, low-voltage state, and explicitly
labeled non-5V estimate.
The commissioned 5V policy treats readings through 5.30V as normal, readings
above 5.30V as WARN, and readings above 5.35V as BAD. The narrow warning band
preserves advance notice before the critical boundary.

Confirmed low voltage is a critical status everywhere: the LCD replaces its
normal rotation with input voltage and remaining shutdown time, the MAX7219
alternates its critical `X` with the power symbol, and the shared-services
WS2812 pixel turns red. The public/admin web card and app API expose BAD health,
alarm activity, whether automatic shutdown is armed, and the remaining
countdown. Voltage recovery clears all of these without hiding unrelated
warnings. The low-voltage buzzer uses a symmetric 50% drive waveform to reduce
audible distortion while retaining its loud one-second cadence. Pattern
priority is checked every 20ms; before the shutdown chime changes frequency,
the active-low buzzer input is held off for 25ms so the alarm cannot end on a
clipped PWM edge.

Charge and energy use trapezoidal integration of consecutive INA226 current and
power samples. Tracking resets on a real boot, survives service restarts during
that boot, and deliberately does not bridge intervals where a monitor is
offline. These are measured-load estimates and inherit the commissioned
INA226/shunt current-calibration accuracy; they are not billing-grade values.

Install or inspect locally on the Pi with:

```bash
./scripts/setup-power-audio.sh --install-power
./scripts/setup-power-audio.sh --check
```

The installer copies `config/power-monitor.example.json` only when no live
configuration exists, and does not overwrite an operator-calibrated file. Its
shunt/current values are deliberate non-runnable placeholders; the service is
not enabled until they are replaced and configuration validation passes.

## Planned fourth INA226: Starlink branch

Monitor roles are `input`, `rail_5v`, optional `rail_12v` (third), and optional
`starlink` (fourth). The third 12V monitor is still physically uninstalled and
staged disabled at `0x4d`. Both templates also stage `starlink.enabled: false`,
at planned address `0x4e`, with explicit placeholders for shunt resistance and
current range. Select A1 to SCL and A0 to SDA (the module's L and SDA/A pads).
Disconnect module power and remove conflicting solder bridges before changing
links. This follows TI's 7-bit address table: SCL/SDA = `1001110` = `0x4e`.
Wiring, calibration and voltage limits remain uncommissioned.

When installed and enabled, the Starlink branch exposes voltage, current, power,
charge (mAh) and energy (Wh) since boot. Existing same-boot totals survive a
collector restart; a newly observed branch begins accounting when first observed.
Input power remains the system total: neither branch measurement is added to it
or subtracted again from the existing input-minus-5V estimate. Branch placement
and whether it includes conversion losses depend on the eventual physical wiring.

An enabled missing sensor reports a monitor warning. Disabled sensors are never
accessed and do not cause missing-hardware warnings. Low-voltage shutdown still
uses only the input monitor. This support does not switch Starlink power, control
its WAN, or add LCD/APRS power fields.

Before enabling either planned monitor, install and verify it during a hardware
maintenance window, choose a unique 7-bit address in `0x40`–`0x4f`, confirm the
actual shunt/range, fill in the placeholders, and run `pcs-power-monitor
check-config --config /etc/pcs/power-monitor.json`. Compare measurements against
a meter before relying on them. Do not copy the illustrative test addresses as
hardware assignments. No live power-monitor restart is needed for local staging.

Staging validation (2026-09-14): the clean v1.9.1-based staging tree passed
583 Python tests on Debian 13/Python 3.13, 11 portal JavaScript tests, Python
compilation and shell syntax checks. Coverage includes four simultaneous
monitors, duplicate-address rejection, disabled sensors not being read, separate
branch energy without double-counting, missing-branch shutdown isolation and
public API field filtering. This is software validation; both planned modules
remain uninstalled and disabled. No live deployment or service restart occurred.


## DC source and Starlink accounting (v1.9.5)

The Power tab shows boot-scoped totals. Authenticated operators select Battery
or Power Supply and optionally enter nominal battery capacity in Wh under
Admin > DC source. The selection lives in `/run/pcs-power-monitor/source.json`,
validated against the kernel boot ID. Every boot defaults to Battery with no
capacity entered; service restarts preserve the same-boot selection and counters.

Battery mode uses the existing voltage detection, thresholds, confirmation,
hysteresis and coordinated shutdown. Power Supply cancels the low-voltage
countdown and disables automatic low-voltage shutdown; switching back to Battery
re-enables it and requires fresh confirming samples. Monitoring continues in both
modes. The configured 12V/24V source detection is unchanged: the existing guard
applies its 11.5V cutoff to a detected/configured 12V source, not a 24V input.

Battery totals sum the input and Starlink branches; 5V and 12V downstream rails
are not added again. Power Supply totals use only the PCS input. Every branch
keeps independent mAh and Wh counters regardless of source selection. Totals use
the counters accumulated this boot, including consumption before mode/capacity
entry. Remaining Wh = max(0, capacity - aggregate consumed Wh); an estimate at or
below 10% is flagged on the Power tab. Capacity never triggers shutdown. Missing
capacity leaves voltage protection and energy integration unchanged.

The commissioned PCS profile enables the Starlink INA226 at **0x48**, with the
live configured 0.002-ohm shunt and 20A maximum. The operator confirmed installation
and operation; software readback confirmed online measurements. This is not a new
bench calibration. The 12V monitor remains disabled at 0x4d; the live configuration
still contains uncommissioned calibration placeholders for that role.

Starlink voltage/current/power/mAh/Wh remain separate. Aggregate instantaneous
power is unavailable when a required branch is offline. Cumulative counters retain
measured energy across interruptions; unmeasured gaps are not reconstructed.
