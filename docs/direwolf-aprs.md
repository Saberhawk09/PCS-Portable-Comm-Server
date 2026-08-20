# Dire Wolf / APRS Integration

PCS treats APRS as an optional subsystem with a deliberate two-stage rollout:

1. **Software staging** installs Dire Wolf and PCS monitoring support while the
   service remains stopped and disabled.
2. **Hardware activation** selects the real USB audio interface, station
   identity, radio/PTT method, levels, frequency, and optional APRS-IS settings.

The repository implements software staging plus a guarded activation workflow.
Before hardware arrives, operators can record the desired profile, render and
lint both receive and transmit configurations, inspect capabilities, and run
synthetic packet tests. Activation remains blocked by explicit hardware evidence
gates, so placeholders cannot accidentally reach RF or APRS-IS.

## What Software Staging Does

Run from the repository root on the PCS Pi:

```bash
./scripts/setup-direwolf-aprs.sh --prepare
./scripts/setup-direwolf-aprs.sh --configure-options
./scripts/setup-direwolf-aprs.sh --check
```

The staging command:

- installs the Raspberry Pi OS / Debian `direwolf` package and supporting tools
- verifies Dire Wolf 1.8 or newer; when the distribution package is older,
  builds the pinned stable 1.8.1 tag at commit
  `a231971a652bfb574a4bae9a5d875fbce53d2267` from the
  [official Dire Wolf repository](https://github.com/wb2osz/direwolf)
- installs a receive-only example under `/etc/pcs/aprs/`
- records `PCS_SETUP_APRS="staged"` in the ignored local install config
- stops and disables `direwolf.service`
- leaves `/etc/direwolf.conf` absent unless an active operator configuration
  already exists
- adds a software-staged APRS card to the authenticated PCS dashboard

It does **not** configure a callsign, APRS-IS login/passcode, audio device, PTT,
beacon, digipeater, IGate, or RF transmit path. The public APRS card remains
hidden while the subsystem is only staged.

The packaged Dire Wolf service runs as the unprivileged `direwolf` user. PCS
ensures membership in the available `audio`, `dialout`, and Raspberry Pi `gpio`
groups, verifies that the packaged unit loads `/etc/direwolf.conf`, and uses a
small restart/firewall ordering override that selects the validated executable
path rather than inventing a parallel daemon. The Debian package remains
installed because it supplies the service account and unit; a newer pinned
source build is installed under `/usr/local` only when the packaged version is
too old. Re-running `--prepare` skips the build when an installed 1.8+ version
already satisfies the requirement.

## Command Reference

Every supported `setup-direwolf-aprs.sh` command is listed here. Commands that
change system state require the normal Pi user and request `sudo` only for the
specific install or service operation.

| Command | Changes state | Purpose |
| --- | --- | --- |
| `--prepare` | Yes | Install Dire Wolf, nftables, libgpiod tools, and build dependencies; build pinned stable 1.8.1 if the packaged version is older than 1.8; copy the safe template; keep Dire Wolf stopped and disabled. |
| `--configure-options` | Local config only | Interactively record non-secret desired-profile values. It never collects the APRS-IS passcode. |
| `--record-validation` | Local config only | Record receive audio, radio channel, PTT, transmit audio/deviation, and timing checks after they have actually passed. |
| `--check` | No | Show desired profile, installed package, service, GPSD, audio, and activation state. |
| `--capabilities` | No | Report Dire Wolf version, gpsd, GPIO, nftables, `gen_packets`, `atest`, FX.25, and variable-speed fixture support. |
| `--list-audio` | No | List USB and ALSA devices, stable card-ID candidates, and CM108/CM119 information. |
| `--detect-audio` | Local config only | Record a stable ALSA endpoint only when exactly one USB card has both capture and playback; refuse ambiguous or missing hardware and reset audio validation gates. |
| `--software-test` | Temporary files only | Run AX.25, FX.25, and variable-speed audio-file encode/decode fixtures without a radio. |
| `--render-config rx` | No | Print the proposed receive/IGate configuration with a visible passcode placeholder. |
| `--render-config tx` | No | Print the proposed complete transmit configuration with unresolved choices marked `BLOCKED`. |
| `--validate-config rx` | No | Lint the receive configuration and report every receive-activation blocker. |
| `--validate-config tx` | No | Lint the transmit configuration and report every hardware, policy, and capability blocker. |
| `--activate-rx` | Yes | Transactionally install receive audio, RF-to-IS IGating, GPSD, logging, and LAN-only KISS. Output is `null` and all RF transmit directives are absent. |
| `--activate-tx` | Yes, RF-capable | Transactionally install the complete operator-approved profile. Requires every evidence gate plus the typed `ENABLE-RF-W8IJC-2` confirmation. |
| `--rollback` | Yes | Restore the newest root-owned PCS Dire Wolf configuration backup and its recorded active mode. |
| `--help` / `-h` | No | Print the same concise command summary in the terminal. |

Examples:

```bash
./scripts/setup-direwolf-aprs.sh --capabilities
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-direwolf-aprs.sh --detect-audio
./scripts/setup-direwolf-aprs.sh --render-config rx
./scripts/setup-direwolf-aprs.sh --validate-config tx
```

Rendering never prints a real APRS-IS passcode. The activation commands read it
twice from an interactive, non-echoing prompt and place it only in the restricted
live configuration.

## Safe Default Template

[`config/direwolf.example.conf`](../config/direwolf.example.conf) documents the
relevant Dire Wolf settings for PCS. Its active defaults are receive-only: the
output device is `null`, AGW/KISS listeners are disabled with port `0`, and
APRS-IS, PTT, beaconing, digipeating, and FEC transmit remain commented out.
`W8IJC-2` is the selected station identity. `PCS_AUDIO` remains a placeholder;
do not enable the service until it is replaced with the detected stable ALSA
identifier and the remaining activation blockers are resolved.

## Selected PCS Deployment Profile

The intended profile supplied by the operator is recorded in the repository:

| Setting | Selected value | Activation state |
| --- | --- | --- |
| Role | GPS-backed two-way digi-IGate | Selected |
| Callsign / SSID | `W8IJC-2` | Selected |
| RF channel | `144.555 MHz` local tactical APRS channel | Selected; radio programming and on-air confirmation pending |
| USB audio | Unitek Y-247A / C-Media `0d8c:0014`; ALSA card ID `Device` | Installed and detected for capture/playback; stable physical-port naming, levels, and radio path pending |
| APRS-IS | Conventional two-way IGate through `noam.aprs2.net` | All eligible RF to APRS-IS; normal APRS message gating back to RF; passcode pending |
| GPS | EM7565 NMEA through local gpsd at `localhost:2947` | Selected and configured in the template |
| Network TNC | KISS on 8001/tcp | Persistent nftables PCS-LAN-only rule implemented; deployment validation pending |
| RF transmit | Intended | Hardware validation pending |
| PTT | Active-high BCM GPIO6 / physical pin 31 through EasyDigi; radio side closes PTT to ground | Selected; wiring and bench validation pending |
| Beacon | GPS tracker every 10 minutes | RF path, symbol, and final comment pending |
| Digipeater | WIDE1-1 fill-in on the local tactical channel | Rule selected; hardware/on-air validation pending |
| FX.25 transmit | Enabled in intended profile | On-air compatibility validation pending |
| Packet logging | Enabled | Managed directory, retention, telemetry parser, and dashboard fields implemented |

The 144.555 MHz tactical channel is an alternate APRS RF carrier chosen to reduce
interference and packet collisions on 144.390 MHz; it is not intended to be
private. The selected policy gates every received packet that is eligible under
Dire Wolf's normal IGate rules. In the generated Dire Wolf configuration this
means no restrictive `FILTER 0 IG` directive. APRS-IS is public, so operators on
the tactical channel must understand that their eligible packets will be
uploaded. The return direction uses Dire Wolf's conventional two-way IGate
behavior: eligible APRS messages are transmitted directly on channel 0, not the
entire APRS-IS stream.

The AGW and KISS ports in the template are Dire Wolf's conventional TCP ports:

```text
AGW:  8000/tcp
KISS: 8001/tcp
```

They are disabled in the safe template and closed while Dire Wolf is staged.
Activation installs `pcs-aprs-kiss-firewall.service`, which permits the selected
KISS port only from loopback and `10.42.0.0/24` on `eth0`, then drops that port
on every other interface. The Pi-side self-test verifies the persistent rule;
the hardware activation checklist still requires a client-side reachability test.

## Relevant Configuration Options

The desired values live in the ignored `config/pcs-install.conf`. The public
example records the selected PCS callsign and non-secret profile, but never an
APRS-IS passcode.

### Station role and identity

| PCS option | Values | Dire Wolf effect |
| --- | --- | --- |
| `PCS_APRS_ROLE` | `monitor`, `rx-igate`, `digipeater`, `tracker`, `digi-igate` | Selects which configuration blocks are generated. |
| `PCS_APRS_CALLSIGN` | Licensed callsign with optional SSID | Becomes `MYCALL` and normally the APRS-IS login identity. |
| `PCS_APRS_FREQUENCY` | Operator label such as a frequency and band | Dashboard/documentation metadata only; Dire Wolf does not tune the radio. |
| `PCS_APRS_ACTIVE_MODE` | `staged`, `rx`, `tx` | Written by activation/rollback so status reflects what is actually live rather than desired TX intent. |

`monitor` is a local receive-only software TNC. `rx-igate` forwards received RF
packets to APRS-IS without Internet-to-RF gating. The remaining roles can
transmit and therefore require completed PTT, level, deviation, policy, and
on-air validation.

### Audio and modem

| PCS option | Safe default | Dire Wolf directive |
| --- | --- | --- |
| `PCS_APRS_AUDIO_INPUT` | `auto` until detection | First `ADEVICE` parameter |
| `PCS_APRS_AUDIO_OUTPUT` | `null` | Optional second `ADEVICE` parameter |
| `PCS_APRS_SAMPLE_RATE` | `48000` | `ARATE` |
| `PCS_APRS_AUDIO_CHANNELS` | `1` | `ACHANNELS` |
| `PCS_APRS_MODEM` | `1200` | `MODEM` |

The guided choices cover 300, 1200, and 9600 baud. A 1200-baud AFSK profile is
the common VHF APRS starting point. A 9600-baud profile needs a suitable direct
radio data/discriminator path; a normal speaker/microphone interface is not
enough. Advanced 2400/4800 PSK variants remain manual because their exact modem
syntax and compatibility must match the deployed Dire Wolf version and peer
network.

### Transmit and PTT

| PCS option | Values | Notes |
| --- | --- | --- |
| `PCS_APRS_TX_ENABLED` | `yes` / `no` | Desired state only; never activates RF by itself. |
| `PCS_APRS_PTT_METHOD` | `none`, `cm108`, `serial-rts`, `serial-dtr`, `gpio`, `hamlib`, `vox` | Exact device, line, polarity, or Hamlib model is collected after hardware detection. |
| `PCS_APRS_PTT_INTERFACE` | Hardware label | Selected as `EasyDigi`. |
| `PCS_APRS_PTT_GPIO_LINE` | BCM GPIO line | Selected as GPIO6, physical header pin 31. |
| `PCS_APRS_PTT_ACTIVE_LEVEL` | `high` / `low` | Selected as `high`: GPIO high energizes the EasyDigi optocoupler and its isolated output pulls radio PTT to ground. |
| `PCS_APRS_BEACON` | `yes` / `no` | Enables `TBEACON` in the guarded TX profile after interval/path/symbol review. |
| `PCS_APRS_BEACON_TYPE` | `gps-tracker` / `fixed` | Selected as `gps-tracker`. |
| `PCS_APRS_BEACON_INTERVAL` | Dire Wolf interval | Selected as `10:00` (every 10 minutes). |
| `PCS_APRS_BEACON_PATH` | RF path or empty/direct | Pending tactical-channel plan. |
| `PCS_APRS_BEACON_SYMBOL` | APRS symbol description | Pending operator selection. |
| `PCS_APRS_BEACON_COMMENT` | Short public comment | Initially `PCS`; review before activation. |
| `PCS_APRS_DIGIPEAT` | `yes` / `no` | Enables the operator-approved `DIGIPEAT` rule only in the TX profile. |
| `PCS_APRS_DIGIPEAT_MODE` | `fill-in` / `wide-area` / `custom` | Selected as `fill-in`. |
| `PCS_APRS_DIGIPEAT_ALIAS` | Human-readable repeated alias | Selected as `WIDE1-1`. |
| `PCS_APRS_DIGIPEAT_ALIAS_PATTERN` | One-hop alias regex | `^W8IJC-2$`; `MYCALL` is also implied by Dire Wolf. |
| `PCS_APRS_DIGIPEAT_WIDE_PATTERN` | WIDEn-N regex | `^WIDE1-1$`; deliberately excludes WIDE2-N and larger paths. |
| `PCS_APRS_DIGIPEAT_PREEMPTIVE` | `OFF`, `DROP`, `MARK`, or `TRACE` | Selected as `OFF`. |
| `PCS_APRS_DIGIPEAT_FILTER` | `all-eligible` or Dire Wolf filter | Selected as `all-eligible`; no additional `FILTER 0 0` restriction. |
| `PCS_APRS_DIGIPEAT_DEDUPE_SECONDS` | Seconds | Selected as `30`. |
| `PCS_APRS_FX25_TX` | `yes` / `no` | Enables `FX25TX` only in the TX profile; normal receive support does not require this. |

Dire Wolf timing directives `DWAIT`, `SLOTTIME`, `PERSIST`, `TXDELAY`,
`TXTAIL`, and `FULLDUP` are shown in the template. Their example values are not
measured PCS defaults and must be calibrated with the actual radio.

| PCS option | Selected starting value | Activation treatment |
| --- | --- | --- |
| `PCS_APRS_DWAIT` | `0` | Rendered only in the TX profile. |
| `PCS_APRS_SLOTTIME` | `10` | Units are 10 ms; hardware validation required. |
| `PCS_APRS_PERSIST` | `63` | Hardware/channel validation required. |
| `PCS_APRS_TXDELAY` | `30` | Starting value only; PTT/audio measurement required. |
| `PCS_APRS_TXTAIL` | `10` | Starting value only; PTT/audio measurement required. |
| `PCS_APRS_FULLDUP` | `OFF` | Selected for the simplex tactical channel. |

### TNC clients, APRS-IS, GPS, and logging

| PCS option | Safe default | Effect |
| --- | --- | --- |
| `PCS_APRS_AGW_PORT` | `0` | `AGWPORT`; `0` disables, conventional enabled port is 8000/tcp. |
| `PCS_APRS_KISS_PORT` | `0` | `KISSPORT`; `0` disables, conventional enabled port is 8001/tcp. |
| `PCS_APRS_KISS_LAN_INTERFACE` | `eth0` | Only this PCS client interface may accept KISS traffic. |
| `PCS_APRS_KISS_LAN_NETWORK` | `10.42.0.0/24` | Source network admitted by the dedicated nftables rule. |
| `PCS_APRS_IGATE` | `no` | Adds the `IGSERVER` and `IGLOGIN` APRS-IS connection; mode controls the return RF path. |
| `PCS_APRS_IGATE_SERVER` | `noam.aprs2.net` | Regional APRS-IS rotate address; change when appropriate. |
| `PCS_APRS_IGATE_MODE` | `rx-only` / `two-way` | Selected as `two-way`; `IGTXVIA` is generated only for the guarded TX profile. |
| `PCS_APRS_IGATE_RF_TO_IS_FILTER` | `all-eligible` or a Dire Wolf `FILTER 0 IG` expression | `all-eligible` omits the filter directive and gates every packet allowed by Dire Wolf's normal IGate rules. |
| `PCS_APRS_IGATE_IS_TO_RF_FILTER` | `normal-messages` or a Dire Wolf `FILTER IG 0` expression | Selected as normal Dire Wolf message gating, not a full APRS-IS rebroadcast. |
| `PCS_APRS_IGATE_TX_PATH` | `direct` or an RF path | Selected as direct; generates `IGTXVIA 0` with no digipeater hops in TX mode. |
| `PCS_APRS_IGATE_TX_LIMIT_1M` | Packet count | Selected as Dire Wolf's example limit of 6 per minute. |
| `PCS_APRS_IGATE_TX_LIMIT_5M` | Packet count | Selected as Dire Wolf's example limit of 10 per five minutes. |
| `PCS_APRS_GPSD` | `no` | Adds `GPSD` for tracker/beacon profiles. |
| `PCS_APRS_GPSD_HOST` | `localhost` | Uses the private gpsd listener on the PCS Pi. |
| `PCS_APRS_GPSD_PORT` | `2947` | Standard gpsd TCP port; the selected directive is `GPSD localhost 2947`. |
| `PCS_APRS_LOGGING` | `no` | Adds `LOGDIR /var/log/direwolf` when enabled. |
| `PCS_APRS_LOG_RETENTION_DAYS` | `14` | Controls the root-owned logrotate policy installed during activation. |

Dire Wolf daily CSV logs remain under `/var/log/direwolf`, writable by the
unprivileged service account. `pcs-aprs-telemetry` counts RF packets from the
`utime`, `source`, and `chan` columns, excludes synthetic channel 999 tracker
transmissions, and reads both current and rotated gzip logs. Public status gets
aggregate counts and the last RF timestamp; the authenticated dashboard may
also display the most recently heard station.

The selected GPS flow is:

```text
EM7565 GNSS
    -> NMEA /dev/ttyUSB1
    -> gpsd 127.0.0.1:2947
    -> Dire Wolf: GPSD localhost 2947
    -> 10-minute GPS tracker beacon
```

Dire Wolf must use the local listener. The LAN-only proxy at
`10.42.0.1:2947` exists for Pi-Star and other trusted PCS clients and is not
part of Dire Wolf's local path. This allows gpsd, Chrony, the PCS dashboard,
and Dire Wolf to share the receiver without opening the serial device multiple
times.

The APRS-IS passcode is deliberately not a PCS install-config option. It must
be entered interactively when the root-owned live configuration is generated.
`IGTXVIA` remains commented in the safe template because it enables RF
transmit. The intended activated profile uses `IGTXVIA 0`, which transmits
eligible messages directly without a digipeater path, plus `IGTXLIMIT 6 10`.

## Configure the Desired Profile

Run this at an interactive Pi terminal:

```bash
./scripts/setup-direwolf-aprs.sh --configure-options
```

It validates and records the role, callsign/SSID, frequency label, modem,
sample rate, audio-channel count, desired ALSA endpoints, TNC ports, APRS-IS,
GPSD, transmit intent, PTT family, beaconing, digipeating, FX.25 transmit, and
logging. It does not ask for an APRS-IS passcode and does not create
`/etc/direwolf.conf`.

This separation lets the intended operating profile be reviewed in GitHub
issues or documentation without committing the APRS-IS passcode or generating
a hardware-specific live configuration.

## Activation Blockers for This Profile

The implemented generator refuses activation until the required values and
evidence gates are complete. `--validate-config rx` and `--validate-config tx`
print the complete current blocker list without changing the system.

- confirmation that the radio is programmed for the selected 144.555 MHz
  tactical channel
- a Dire Wolf build that reports compiled-in gpsd support, plus a successful
  local `localhost:2947` protocol/fix check
- detected ALSA capture/playback identifier
- GPIO6 / physical pin 31 wiring to the EasyDigi; active-high polarity is
  selected and must be confirmed with a disconnected-radio bench test
- APRS-IS passcode entered interactively on the Pi
- tracker beacon interval, RF path, symbol, and final comment
- KISS port restricted to the PCS client LAN
- receive level, transmit audio/deviation, PTT timing, and supervised RF test

The evidence options default to `no` and are never inferred from device
presence:

| Evidence option | Required for |
| --- | --- |
| `PCS_APRS_RX_AUDIO_VALIDATED` | RX and TX activation |
| `PCS_APRS_RADIO_CHANNEL_VALIDATED` | RX and TX activation |
| `PCS_APRS_PTT_VALIDATED` | TX activation |
| `PCS_APRS_TX_AUDIO_VALIDATED` | TX activation |
| `PCS_APRS_TX_TIMING_VALIDATED` | TX activation |

Use `--record-validation` only after completing the matching bench step. Merely
detecting a USB sound card does not satisfy any evidence gate.

## Hardware Arrival Checklist

With the detected USB sound card attached, collect evidence before editing a live
configuration:

```bash
lsusb
arecord -l
aplay -l
arecord -L
aplay -L
for control in /dev/snd/controlC*; do
    echo "=== ${control} ==="
    sudo udevadm info --query=property --name="${control}"
done
./scripts/setup-direwolf-aprs.sh --list-audio
```

The card number (`card 1`, `card 2`, and so on) can change between boots. Prefer
a stable ALSA name such as `plughw:CARD=<card-id>,DEV=0` after confirming the
actual identifier.

`--list-audio` is the read-only foundation for setup-script detection:
it reports USB devices, ALSA capture/playback hardware, stable card-ID
candidates from `/proc/asound/cards`, and Dire Wolf's `cm108` mapping when that
tool is available. `--detect-audio` selects only when exactly one USB ALSA card
has both capture and playback nodes. Zero or multiple candidates are refused;
ambiguous devices are never guessed.

Record and validate these operator choices:

- licensed station callsign and APRS SSID
- local APRS frequency/band plan
- radio and interface models
- receive and transmit audio device names
- PTT mechanism supported by the actual interface (for example CM108/CM119
  GPIO, serial RTS, radio CAT, GPIO, or VOX)
- receive level and decode quality
- transmit deviation/level, only if RF TX will be enabled
- whether the node is receive-only, an RX/two-way IGate, a digipeater, a
  tracker, or a combined digi-IGate
- APRS-IS server/login/passcode if an IGate is intentionally enabled

Do not store an APRS-IS passcode or other credentials in Git. Supply secrets
interactively on the Pi and keep the resulting live configuration root-owned.

## Safe Activation Order

1. Keep the transmitter disconnected or use a suitable dummy load.
2. Run `--list-audio`, then `--detect-audio` when exactly one USB sound card is
   attached. Save explicit endpoints manually if discovery is ambiguous, and run
   `--record-validation` after receive decoding succeeds.
3. Run `--render-config rx` and `--validate-config rx`.
4. Run `--activate-rx`. This installs no playback, PTT, beacon, digipeater,
   FX.25 TX, or Internet-to-RF directives.
5. Verify KISS from a PCS LAN client and verify rejection from uplink interfaces.
6. Bench-test PTT, calibrate transmit audio/deviation and timing, finalize the
   beacon symbol/path/comment, then record only the completed validation gates.
7. Run `--render-config tx` and `--validate-config tx` and review the diff.
8. Perform the operator-supervised RF test, then run `--activate-tx` only when
   the complete activation is intended.

Each successful activation saves the previous `/etc/direwolf.conf` under
`/etc/pcs/aprs/backups/`. If the new service fails to start, the installer
automatically restores the previous configuration; `--rollback` provides the
same recovery path later.

## Software-Only Validation Boundary

Before hardware arrives, PCS can validate supported Dire Wolf installation, service
ownership, disabled staging state, configuration-file permissions, dashboard
integration, rendered RX/TX policy, capability availability, and Dire Wolf's
synthetic AX.25/FX.25 encode/decode tooling. `--software-test` uses temporary
WAV files and never opens a radio or keys PTT. It cannot validate
USB enumeration, ALSA routing, audio levels, PTT polarity/timing, radio
deviation, receiver performance, RF coverage, digipeating behavior, or on-air
regulatory correctness.

Those hardware/RF results must remain marked pending until measured.

The system-wide header reservation, including RTC, fan, PTT, and future SA818S
UART assignments, is maintained in [PCS GPIO Allocation](gpio-allocation.md).

## Upstream References

- [Dire Wolf project and release notes](https://github.com/wb2osz/direwolf)
- [Version-specific Dire Wolf User Guide](https://github.com/wb2osz/direwolf/blob/master/doc/User-Guide.pdf)
- [Dire Wolf application notes for IGates, digipeaters, packet routing, and radio interfaces](https://github.com/wb2osz/direwolf-doc)
- [Debian `gen_packets` manual](https://manpages.debian.org/testing/direwolf/gen_packets.1.en.html)
