# Graywolf APRS Engine

PCS can select `PCS_APRS_ENGINE="graywolf"` instead of the default
`PCS_APRS_ENGINE="direwolf"`. Installation remains hardware-safe by default;
production activation is a separate confirmed transaction.

The base installer asks for the APRS engine when APRS staging is selected and
then runs the matching setup script. For a direct Graywolf staging run:

```bash
./scripts/setup-direwolf-aprs.sh --prepare-uart
./scripts/setup-graywolf-aprs.sh --prepare
./scripts/setup-graywolf-aprs.sh --check
./scripts/setup-graywolf-aprs.sh --capabilities
```

After staging, commissioning, and supervised RX/TX acceptance:

```bash
./scripts/setup-graywolf-aprs.sh --activate
./scripts/setup-graywolf-aprs.sh --rollback-direwolf
```

Activation requires typing `ENABLE-RF-<callsign>`, verifies the SA818S, audio,
GPSD, and LAN-only firewall prerequisites, backs up the disabled Graywolf
database, enables the equivalent channel/tracker/fill-in digi/AGW/KISS profile
and requested iGate mode, selects Graywolf in `pcs-install.conf`, and enables it
at boot.
The iGate portion follows `PCS_APRS_IGATE`: `yes` activates both directions
and lets the tracker use RF plus APRS-IS; `no` keeps the iGate and both gate
directions off and restricts the tracker to RF only.
An always-on watchdog observes GPIO6 with `pinctrl`; if PTT remains high for
eight seconds it force-stops Graywolf and restores the low-holding guard.
Rollback restores the pre-activation Graywolf database and re-enables the
preserved Dire Wolf service and commissioned configuration.

`--prepare` installs the architecture-matched Graywolf 0.14.13 Debian package
from the upstream GitHub release and verifies the pinned upstream SHA-256 before
installation. Supported PCS package architectures are `amd64`, `arm64`, and
`armhf`. Any other architecture is refused rather than guessed.

The command then:

- verifies the upstream package behavior (enable but do not start), then leaves
  `graywolf.service` stopped and disabled after installation
- preserves an active Dire Wolf service and its active-engine selection
- refuses to proceed when Graywolf itself is already active
- installs a systemd override with `Conflicts=direwolf.service`
- installs and starts `pcs-aprs-ptt-safe.service` when neither APRS engine is
  active; it owns GPIO6 low with a pull-down until an APRS engine starts
- records `PCS_APRS_ENGINE_STAGED="graywolf"`; on a fresh install with no
  active Dire Wolf service it also records Graywolf as the selected staged
  engine
- creates `/etc/pcs/aprs/graywolf-staged` as a non-secret staging marker
- leaves the Graywolf configuration and history databases unconfigured

Staging does not create a Graywolf administrator, configure a callsign, derive
an APRS-IS login, select audio/GPS/PTT, enable a beacon or digipeater, start a
service, or transmit. Existing Dire Wolf configuration is not removed.

`scripts/pcs-graywolf-profile.py` is a migration helper for an authenticated,
loopback-only Graywolf staging instance. It creates the matching PCS profile
with the radio channel, beacon, digipeater rule, iGate, AGW, and KISS interface
disabled, then reads every safety-critical value back. It also performs
full-resource updates after creating beacons and digipeater rules because
Graywolf 0.14.13's SQLite create defaults otherwise turn those boolean fields
on and add a `WIDE1-1` beacon path. The helper is intentionally not dispatched
by `--prepare` and does not activate an APRS engine.

## Why Dire Wolf Remains Installed

Removing Dire Wolf now could break PCS behavior even if Graywolf can handle the
radio. The commissioned activation and rollback workflow, APRS-IS uplink
recovery, CSV telemetry, configuration validation, and portions of the
SA818S/audio prerequisite services still use Dire Wolf-specific paths or the
`direwolf` service account. Existing AGW/KISS clients also need protocol-level
acceptance against Graywolf rather than an assumption based only on advertised
port support.

The safe migration sequence is therefore additive: stage Graywolf, complete a
dependency audit and refactor, perform supervised RX and TX acceptance, prove
rollback, and only then consider removing the Dire Wolf package. The current
installer deliberately stops at the first step.

## PCS Port and Storage Policy

The upstream package binds its web UI to `0.0.0.0:8080`. PCS already uses TCP
8080 for its legacy administrator redirect, so the PCS override uses
`PCS_GRAYWOLF_HTTP_ADDRESS="10.42.0.1"` and
`PCS_GRAYWOLF_HTTP_PORT="8070"`. Port 8080 is rejected by the staging script.
The shared PCS APRS firewall restricts TCP 8070, 8000, and 8001 to loopback and
the `10.42.0.0/24` PCS LAN on the configured LAN interface. The service remains
disabled until the rest of the activation workflow is accepted.

The PCS public homepage and authenticated administration page include a
Graywolf access card when Graywolf is selected. From a device on the PCS LAN,
open `http://10.42.0.1:8070/`; Graywolf keeps its own authentication boundary.

The override places Graywolf's packet-history database under
`/run/graywolf/history.db`. This avoids continuous packet-history writes to the
Pi SD card. History is consequently cleared on reboot; the configuration
database remains under `/var/lib/graywolf/graywolf.db`.

## Activation Boundary

Graywolf supports the required PCS primitives: 1200-baud AFSK, ALSA audio,
Linux gpiochip PTT, gpsd, fill-in digipeating, bidirectional APRS-IS, RF and
APRS-IS beacons, KISS TCP, AGWPE, a REST API, and Prometheus metrics. Those
capabilities do not prove the commissioned SA818S/Easy Digi path works with
Graywolf.

PCS production activation requires all of the following to remain true:

1. PCS SA818S and ALSA prerequisite service ownership independent of Dire Wolf
2. dashboard telemetry from Graywolf's API or metrics
3. clock-correction and reboot behavior for scheduled GPS beacons
4. exact fill-in, two-way iGate, KISS, and AGW client acceptance
5. separately confirmed supervised RF TX after receive-only acceptance
6. transactional activation and rollback between engine configurations

Use `--activate`, not a manual service start, on the commissioned radio. Never
run Graywolf and Dire Wolf together: they would contend for the
same USB audio device, GPIO6 PTT line, APRS-IS identity, and network TNC ports.

Both engine overrides conflict with the PTT guard and start it after the engine
stops. This is a software fail-safe for the active-high EasyDigi input; it does
not replace a supervised physical key/unkey test. A failed or interrupted test
must leave both engines stopped and the guard verified as the GPIO6 consumer.

Do not store Graywolf administrator credentials or other protected deployment
state in Git. Its SQLite databases are local appliance state, not repository
configuration.

## PCS TX Timing Diagnostic

On the commissioned USB audio/GPIO6 path, use separate measurements for GPIO
PTT duration, USB audio onset, and over-the-air decode. A long configured HDLC
preamble is not evidence of a software stall. The September 2026 acceptance
run observed six consistent GPIO pulses at approximately 1.30 seconds with a
500 ms preamble and 50 ms tail. USB audio began within the 5–10 ms observation
resolution of GPIO assertion, including the first transmission after a modem
sink rebuild. No ALSA, drain, or watchdog fault occurred. Therefore any
remaining variable 700–1200 ms carrier-to-decode behavior is downstream of the
PCS GPIO-to-USB-audio path and must be verified at the SA818S RF output and
receiving decoder before reducing the commissioned preamble.

The live output gain is an RF measurement, not an installer default. Preserve
the operator-calibrated Graywolf gain when reapplying or verifying the profile;
do not overwrite it from PCS automation.

## Upstream References

- [Graywolf project](https://github.com/chrissnell/graywolf)
- [Graywolf handbook](https://chrissnell.com/software/graywolf/)
- [Graywolf API reference](https://chrissnell.com/software/graywolf/api.html)
