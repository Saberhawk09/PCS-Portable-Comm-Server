# Dire Wolf / APRS Integration

PCS treats APRS as an optional subsystem with a deliberate two-stage rollout:

`PCS_APRS_ENGINE` selects `direwolf` (the commissioned default described here)
or the separately staged `graywolf` alternative. See
[Graywolf APRS Staging](graywolf-aprs.md). Only Dire Wolf currently has a
supported PCS activation and rollback workflow.

1. **Software staging** installs Dire Wolf and PCS monitoring support while the
   service remains stopped and disabled.
2. **Managed activation** installs the commissioned SA818S, ALSA, firewall, and
   Dire Wolf profile after an operator reviews the explicit RF confirmation.

The hardware was commissioned on August 23, 2026. RF TX/RX, UART control,
Easy Digi audio/PTT, GNSS, two-way APRS-IS messaging, message acknowledgements,
WIDE1-1 fill-in digipeating, AGW, and KISS were demonstrated. The repository
still retains its evidence gates and typed RF confirmation so a clean rebuild
cannot infer physical validation from mere device presence.

## What Software Staging Does

Run from the repository root on the PCS Pi:

```bash
./scripts/setup-direwolf-aprs.sh --prepare-uart
./scripts/setup-direwolf-aprs.sh --prepare
./scripts/setup-direwolf-aprs.sh --import-commissioned-profile
./scripts/setup-direwolf-aprs.sh --check
```

The base installer runs `--prepare-uart` automatically when APRS staging is
selected. It enables the hardware UART, removes only serial-console kernel
arguments, disables serial getty units, and leaves Bluetooth unchanged. If a
boot file changes, reboot before attempting activation.

The staging command:

- installs the Raspberry Pi OS / Debian `direwolf` package and supporting tools
- verifies Dire Wolf 1.8 or newer with the PCS tracker clock-jump guard; when
  either requirement is missing, builds the pinned stable 1.8.1 tag at commit
  `a231971a652bfb574a4bae9a5d875fbce53d2267` from the
  [official Dire Wolf repository](https://github.com/wb2osz/direwolf) and
  applies the maintained tracker guard
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
managed ordering override that selects the validated executable path and starts
only after radio programming, ALSA restoration, GPSD, networking, and LAN
firewalling. The Debian package remains
installed because it supplies the service account and unit; a newer pinned
source build is installed under `/usr/local` when the packaged version is too
old or lacks the tracker clock-jump guard. Re-running `--prepare` skips the
build only when both the 1.8+ and guard requirements are already satisfied.

## Command Reference

Every supported `setup-direwolf-aprs.sh` command is listed here. Commands that
change system state require the normal Pi user and request `sudo` only for the
specific install or service operation.

| Command | Changes state | Purpose |
| --- | --- | --- |
| `--prepare` | Yes | Install Dire Wolf, nftables, libgpiod tools, and build dependencies; build guarded pinned stable 1.8.1 if the installed binary is older than 1.8 or lacks the tracker clock-jump fix; copy the safe template; keep Dire Wolf stopped and disabled. |
| `--configure-options` | Local config only | Interactively record non-secret desired-profile values. It never collects the APRS-IS passcode. |
| `--import-commissioned-profile` | Local config only | Replace the complete managed APRS block with the versioned PCS profile, preserve non-APRS and active-mode state, back up the prior local config, and reset every hardware-evidence gate. |
| `--prepare-uart` | Boot files and services | Idempotently set `enable_uart=1`, remove the Pi serial login console, and disable serial getty units without changing Bluetooth. Existing boot files receive one-time `.pcs-pre-uart.bak` backups. |
| `--record-validation` | Local config only | Record receive audio, radio channel, PTT, transmit audio/deviation, and timing checks after they have actually passed. |
| `--check` | No | Show desired profile, installed package, service, GPSD, audio, and activation state. |
| `--capabilities` | No | Report Dire Wolf version, gpsd, GPIO, nftables, `gen_packets`, `atest`, FX.25, and variable-speed fixture support. |
| `--list-audio` | No | List USB and ALSA devices, stable card-ID candidates, and CM108/CM119 information. |
| `--detect-audio` | Local config only | Record a stable ALSA endpoint only when exactly one USB card has both capture and playback; refuse ambiguous or missing hardware and reset audio validation gates. |
| `--set-rx-level PERCENT` | Mixer and local config only | Persist and apply a validated USB capture percentage without regenerating `/etc/direwolf.conf`, restarting Dire Wolf, or touching TX playback. |
| `--set-tx-timing DELAY TAIL` | Local config only | Record validated 10 ms `TXDELAY`/`TXTAIL` units. Dire Wolf must be active and the requested values must exactly match `/etc/direwolf.conf`; the command never edits or restarts the live service. |
| `--software-test` | Temporary files only | Run AX.25, FX.25, and variable-speed audio-file encode/decode fixtures without a radio. |
| `--render-config rx` | No | Print the proposed receive/IGate configuration with a visible passcode placeholder. |
| `--render-config tx` | No | Print the proposed complete transmit configuration with unresolved choices marked `BLOCKED`. |
| `--validate-config rx` | No | Lint the receive configuration and report every receive-activation blocker. |
| `--validate-config tx` | No | Lint the transmit configuration and report every hardware, policy, and capability blocker. |
| `--activate-rx` | Yes | Transactionally install receive audio, SA818S/ALSA preparation, RF-to-IS IGating, GPSD, logging, and LAN-only AGW/KISS. Output is `null` and all RF transmit directives are absent. |
| `--activate-tx` | Yes, RF-capable | Transactionally install the complete commissioned profile. Requires every evidence gate plus the typed `ENABLE-RF-W8IJC-10` confirmation. |
| `--rollback` | Yes | Restore the newest root-owned PCS Dire Wolf configuration backup and its recorded active mode. |
| `--help` / `-h` | No | Print the same concise command summary in the terminal. |

Examples:

```bash
./scripts/setup-direwolf-aprs.sh --capabilities
./scripts/setup-direwolf-aprs.sh --software-test
./scripts/setup-direwolf-aprs.sh --prepare-uart
./scripts/setup-direwolf-aprs.sh --import-commissioned-profile
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
`W8IJC-10` is the selected station identity and `Device` is the stable ALSA
card ID. The template still uses a null output and no PTT, so it cannot transmit.

## Selected PCS Deployment Profile

The commissioned profile supplied and physically validated by the operator is:

| Setting | Selected value | Activation state |
| --- | --- | --- |
| Role | GPS-backed two-way digi-IGate | Selected |
| Callsign / SSID | `W8IJC-10` | RF and APRS-IS tested |
| RF channel | `144.5500 MHz` simplex, 25 kHz, no tones | SA818S readback and RF TX/RX tested |
| USB audio | Sabrent/C-Media; ALSA card ID `Device` | Current -16 dB playback, 69% capture, AGC off; repeat TX decode validation at -16 dB |
| APRS-IS | Conventional two-way IGate through `noam.aprs2.net` | RF-to-IS and eligible APRS-IS message return tested |
| GPS | EM7565 NMEA through local gpsd at `localhost:2947` | 3D fix and APRS.fi position/altitude/course demonstrated |
| Network TNC | AGW 8000/tcp and KISS 8001/tcp | EasyTerm and `kissutil` tested; both restricted to the PCS LAN |
| RF transmit | Enabled only by guarded TX profile | 3/3 PCS-to-FT3D and 10/10 acknowledged long messages demonstrated |
| PTT | Active-high BCM GPIO6 / pin 31 through Easy Digi | Bench and RF tested; Dire Wolf uses `PTT GPIOD gpiochip0 6` |
| Beacon | GPS tracker to RF and APRS-IS every 10 minutes | independent `SENDTO=0` and `SENDTO=IG` beacons selected; post-change RF/IS observation required |
| Digipeater | WIDE1-1 one-hop fill-in only | 4/4 FT3D-to-PCS packets and `[0H]` repeat behavior demonstrated |
| FX.25 transmit | Disabled | Not part of the commissioned on-air profile; receive support remains available |
| Packet logging | Enabled | Managed directory, retention, telemetry parser, and dashboard fields implemented |

The 144.550 MHz local channel is an alternate APRS RF carrier chosen to reduce
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
Activation installs `pcs-aprs-kiss-firewall.service`, which permits both ports
only from loopback and `10.42.0.0/24` on `eth0`, then drops those ports
on every other interface. The Pi-side self-test verifies the persistent rule;
the hardware activation checklist still requires a client-side reachability test.

## Managed Startup and Recovery

Activation installs shared support services and a Dire Wolf override:

1. `pcs-sa818.service` opens `/dev/serial0` at 9600 8N1, sends the commissioned
   SA818S commands, and requires an exact group readback:

   ```text
   AT+DMOSETGROUP=1,144.5500,144.5500,0000,1,0000
   AT+DMOSETVOLUME=8
   AT+SETFILTER=1,1,1
   AT+SETTAIL=0
   AT+DMOREADGROUP
   ```

   The SA818S convention is inverted for filters: `1` means off. The expected
   readback is `+DMOREADGROUP:1,144.5500,144.5500,0000,1,0000`.
2. `pcs-aprs-audio.service` waits for ALSA card `Device`, applies Speaker
   `-16dB`, Mic `69%`, and Auto Gain Control `off`, then verifies the controls.
   A C-Media-specific udev hook runs a delayed, retriggerable refresh after each
   USB enumeration so the distribution ALSA restore rule cannot leave stale
   mixer values behind.
3. `pcs-aprs-kiss-firewall.service` admits AGW 8000/tcp and KISS 8001/tcp only
   on loopback or `eth0` from `10.42.0.0/24` and drops both ports elsewhere.
4. `pcs-aprs-ptt-safe.service` owns GPIO6 as an output low with a pull-down
   whenever neither APRS engine is running. Both engine overrides conflict with
   the guard and start it again after an engine stops.
5. `direwolf.service` starts after the radio/audio/firewall services, gpsd,
   sound, and
   `network-online.target`. Its pre-start hooks reapply both the radio and audio
   profiles on every restart. `Restart=always` with a five-second delay lets it
    recover when the UART or USB sound card disappears and later re-enumerates.
6. `91-pcs-direwolf-uplink-recovery` asks a hardened oneshot helper to evaluate
   confirmed NetworkManager changes. It compares the selected IPv4 default
   interface with a runtime baseline, gives Dire Wolf 45 seconds to reconnect
   by itself, verifies APRS-IS DNS, and restarts Dire Wolf only when its
   APRS-IS TCP session is still absent. A five-minute cooldown prevents repeated
   restarts and their associated 30-second startup beacon schedule.

For an already-active installation, install only this recovery integration with:

```bash
./scripts/setup-direwolf-aprs.sh --install-uplink-recovery
```

This command does not regenerate `/etc/direwolf.conf`, restart Dire Wolf, key
PTT, or transmit. The route-change recovery itself may restart an active TX
profile only after a real uplink transition and failed native APRS-IS recovery.
If APRS-IS DNS is unavailable, it leaves Dire Wolf running so its normal retry
loop can continue without causing an unnecessary RF startup beacon.

The active APRS engine alone owns GPIO6 PTT; the PTT guard owns it low between
engine runs. The UART initializer never keys the radio. The
bench FTDI adapter must be unplugged during normal RF operation: it previously
caused stuck-TX behavior and defeats the intended USB/radio-side isolation.

Commissioning also established two physical test cautions. The Pi GPIO extender
ribbon must be installed for both UART/PTT continuity, and a handheld receiver
at extremely close range can overload and mimic bad modulation. The FT3D test
decoded normally at close range after its antenna was removed; do not tune the
audio path around a receiver-overload artifact.

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
| `PCS_APRS_RADIO` | Hardware label | Dashboard-only description of the commissioned SA818S/Easy Digi/audio path. |
| `PCS_APRS_CONFIG_VERSION` | Managed integer | Selects the versioned APRS defaults and triggers safe legacy-profile migration when outdated. |
| `PCS_APRS_ACTIVE_MODE` | `staged`, `rx`, `tx` | Written by activation/rollback so status reflects what is actually live rather than desired TX intent. |

`monitor` is a local receive-only software TNC. `rx-igate` forwards received RF
packets to APRS-IS without Internet-to-RF gating. The remaining roles can
transmit and therefore require completed PTT, level, deviation, policy, and
on-air validation.

### Audio and modem

| PCS option | Safe default | Dire Wolf directive |
| --- | --- | --- |
| `PCS_APRS_AUDIO_INPUT` | `plughw:CARD=Device,DEV=0` | First `ADEVICE` parameter |
| `PCS_APRS_AUDIO_OUTPUT` | `plughw:CARD=Device,DEV=0` | Second `ADEVICE` parameter in TX mode; RX forces `null` |
| `PCS_APRS_AUDIO_CARD` | `Device` | Stable ALSA card ID used by the mixer service |
| `PCS_APRS_PLAYBACK_CONTROL` / `PCS_APRS_PLAYBACK_LEVEL` | `Speaker` / `-16dB` | Commissioned transmit playback control and level |
| `PCS_APRS_CAPTURE_CONTROL` / `PCS_APRS_CAPTURE_LEVEL` | `Mic` / `69%` | Commissioned receive capture control and level |
| `PCS_APRS_AGC_CONTROL` / `PCS_APRS_AGC_STATE` | `Auto Gain Control` / `off` | Keeps AGC disabled before every Dire Wolf start |
| `PCS_APRS_SAMPLE_RATE` | `48000` | `ARATE` |
| `PCS_APRS_AUDIO_CHANNELS` | `1` | `ACHANNELS` |
| `PCS_APRS_MODEM` | `1200` | `MODEM` |

The guided choices cover 300, 1200, and 9600 baud. A 1200-baud AFSK profile is
the common VHF APRS starting point. A 9600-baud profile needs a suitable direct
radio data/discriminator path; a normal speaker/microphone interface is not
enough. Advanced 2400/4800 PSK variants remain manual because their exact modem
syntax and compatibility must match the deployed Dire Wolf version and peer
network.

### SA818S radio programming

| PCS option | Commissioned value | Effect |
| --- | --- | --- |
| `PCS_APRS_RADIO_INIT` | `yes` | Requires boot-time programming/readback before Dire Wolf. |
| `PCS_APRS_RADIO_DEVICE` / `PCS_APRS_RADIO_BAUD` | `/dev/serial0` / `9600` | Pi UART control path, 8N1. |
| `PCS_APRS_RADIO_BANDWIDTH_KHZ` | `25` | SA818S group bandwidth code `1`. |
| `PCS_APRS_RADIO_TX_FREQUENCY_MHZ` / `PCS_APRS_RADIO_RX_FREQUENCY_MHZ` | `144.5500` / `144.5500` | Simplex TX/RX frequency. |
| `PCS_APRS_RADIO_TX_TONE` / `PCS_APRS_RADIO_RX_TONE` | `0000` / `0000` | No CTCSS/CDCSS. |
| `PCS_APRS_RADIO_SQUELCH` / `PCS_APRS_RADIO_VOLUME` | `1` / `8` | Validated receiver settings. |
| `PCS_APRS_RADIO_PRE_DE_EMPHASIS` | `off` | Produces SA818S filter code `1`. |
| `PCS_APRS_RADIO_HIGH_PASS` | `off` | Produces SA818S filter code `1`. |
| `PCS_APRS_RADIO_LOW_PASS` | `off` | Produces SA818S filter code `1`. |
| `PCS_APRS_RADIO_TX_TAIL` | `off` | Disables the radio's transmit tail tone. |

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
| `PCS_APRS_BEACON_PATH` | RF path or empty/direct | Commissioned as direct and applied only to the RF copy. |
| `PCS_APRS_BEACON_SENDTO` | `BOTH`, `IG`, or channel | Commissioned as `BOTH`, which renders one channel-0 RF beacon and one direct APRS-IS beacon. |
| `PCS_APRS_BEACON_SYMBOL` | APRS symbol description | Commissioned as `igate`. |
| `PCS_APRS_BEACON_OVERLAY` | Single overlay character | Commissioned as `T`. |
| `PCS_APRS_BEACON_ALTITUDE` | `yes` / `no` | Adds `ALT=1` when enabled. |
| `PCS_APRS_BEACON_COMMENT` | Short public comment | `PCS Portable Communication Server - W8IJC`. |
| `PCS_APRS_DIGIPEAT` | `yes` / `no` | Enables the operator-approved `DIGIPEAT` rule only in the TX profile. |
| `PCS_APRS_DIGIPEAT_MODE` | `fill-in` / `wide-area` / `custom` | Selected as `fill-in`. |
| `PCS_APRS_DIGIPEAT_ALIAS` | Human-readable repeated alias | Selected as `WIDE1-1`. |
| `PCS_APRS_DIGIPEAT_ALIAS_PATTERN` | One-hop alias regex | `^WIDE1-1$`; `MYCALL` is also implied by Dire Wolf. |
| `PCS_APRS_DIGIPEAT_WIDE_PATTERN` | WIDEn-N regex | `^WIDE1-1$`; deliberately excludes WIDE2-N and larger paths. |
| `PCS_APRS_DIGIPEAT_PREEMPTIVE` | `OFF`, `DROP`, `MARK`, or `TRACE` | Selected as `OFF`. |
| `PCS_APRS_DIGIPEAT_FILTER` | `all-eligible` or Dire Wolf filter | Selected as `all-eligible`; no additional `FILTER 0 0` restriction. |
| `PCS_APRS_DIGIPEAT_DEDUPE_SECONDS` | Seconds | Selected as `30`. |
| `PCS_APRS_FX25_TX` | `yes` / `no` | Commissioned as `no`; normal FX.25 receive support does not require this. |

Dire Wolf timing directives use 10 ms units. Supervised RF testing found that
600 ms worked after warm-up but missed the first packet after a cold start.
The observed initial packet decoded after increasing the SA818S PTT-to-audio
delay to 750 ms. The operator has now selected 700 ms; 750 ms remains the last
receiver-confirmed cold-start baseline until 700 ms passes repeated checks.

| PCS option | Selected starting value | Activation treatment |
| --- | --- | --- |
| `PCS_APRS_DWAIT` | `0` | Rendered only in the TX profile. |
| `PCS_APRS_SLOTTIME` | `10` | 100 ms channel-access slot. |
| `PCS_APRS_PERSIST` | `63` | Conventional half-duplex persistence. |
| `PCS_APRS_TXDELAY` | `70` | Operator-selected 700 ms SA818S pre-key delay; repeat cold-start verification. |
| `PCS_APRS_TXTAIL` | `20` | Commissioned 200 ms tail. |
| `PCS_APRS_FULLDUP` | `OFF` | Selected for the simplex tactical channel. |

After supervised RF timing validation, make the currently active values part
of the repeatable managed profile without regenerating the live configuration:

```bash
./scripts/setup-direwolf-aprs.sh --set-tx-timing 75 20
```

The command requires an active installation and refuses any values that do not
already match `/etc/direwolf.conf`. It records the match in
`config/pcs-install.conf` without restarting Dire Wolf or changing existing
hardware-evidence flags.

### TNC clients, APRS-IS, GPS, and logging

| PCS option | Safe default | Effect |
| --- | --- | --- |
| `PCS_APRS_AGW_PORT` | `8000` | `AGWPORT`; EasyTerm and connected-mode-capable clients. |
| `PCS_APRS_KISS_PORT` | `8001` | `KISSPORT`; `kissutil` and KISS clients. |
| `PCS_APRS_KISS_LAN_INTERFACE` | `eth0` | Only this PCS client interface may accept AGW/KISS traffic. |
| `PCS_APRS_KISS_LAN_NETWORK` | `10.42.0.0/24` | Source network admitted by the dedicated nftables rule. |
| `PCS_APRS_AGENT_ENABLED` | `no` | When selected, generates a virtual Internet channel for the separately managed APRS status/mailbox agent. |
| `PCS_APRS_AGENT_ICHANNEL` | `8` | Unused KISS channel mapped to APRS-IS; channel 0 remains RF. |
| `PCS_APRS_AGENT_TOCALL` | `APZPCS` | Experimental APRS software destination used on agent ACKs and replies. |
| `PCS_APRS_AGENT_DEDUPE_TTL_SECONDS` | `86400` | Persistent sender/message-ID duplicate window. |
| `PCS_APRS_AGENT_MAILBOX_LIMIT` | `100` | Maximum retained `MSG` mailbox entries; oldest entries are removed first. |
| `PCS_APRS_AGENT_SENDER_RATE_PER_MINUTE` | `12` | Per-sender response ceiling for the unauthenticated public APRS interface. |
| `PCS_APRS_AGENT_GLOBAL_RATE_PER_MINUTE` | `60` | Whole-agent response ceiling. |
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

The optional [PCS APRS Agent](aprs-agent.md) connects only to Dire Wolf on
loopback. It does not use APRS-IS credentials or channel 0 and therefore cannot
request RF transmission. `ICHANNEL` carries every APRS-IS packet delivered to
Dire Wolf, so exact addressee filtering remains the agent's responsibility.

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

For this PCS, import the complete version-controlled profile instead of
copying individual settings:

```bash
./scripts/setup-direwolf-aprs.sh --import-commissioned-profile
```

This removes obsolete `PCS_APRS_*` keys, replaces every desired APRS value as
one managed block, preserves unrelated installer choices and the current live
mode, and resets all validation evidence to `no`. The prior local config is
saved as `config/pcs-install.conf.bak`. This prevents an old hidden alias or RF
setting from surviving an upgrade.

Run this at an interactive Pi terminal:

```bash
./scripts/setup-direwolf-aprs.sh --configure-options
```

It validates and records the role, callsign/SSID, frequency label, modem,
sample rate, audio-channel count, desired ALSA endpoints, TNC ports, APRS-IS,
GPSD, transmit intent, PTT family, beaconing, digipeating, FX.25 transmit, and
logging. It does not ask for an APRS-IS passcode and does not create
`/etc/direwolf.conf`.

When the local APRS schema is old or missing, `--configure-options` first loads
the current commissioned defaults and resets evidence before presenting the
prompts. Unsupported legacy APRS keys are removed when the reviewed settings
are saved.

This separation lets the intended operating profile be reviewed in GitHub
issues or documentation without committing the APRS-IS passcode or generating
a hardware-specific live configuration.

## Activation Blockers for This Profile

The implemented generator refuses activation until the required values and
evidence gates are complete. `--validate-config rx` and `--validate-config tx`
print the complete current blocker list without changing the system.

- confirmation that the radio is programmed for the selected 144.550 MHz
  local channel and `/dev/serial0` is present
- `enable_uart=1`, no `serial0`/`ttyAMA*`/`ttyS*` kernel console, and a reboot
  after any boot-file change made by `--prepare-uart`
- a Dire Wolf build that reports compiled-in gpsd support, plus a successful
  local `localhost:2947` protocol/fix check
- detected ALSA capture/playback identifier
- GPIO6 / physical pin 31 wiring to the EasyDigi; active-high polarity is
  selected and must be confirmed with a disconnected-radio bench test
- APRS-IS passcode entered interactively on the Pi
- tracker beacon destination, interval, symbol, overlay, altitude, and comment
- AGW and KISS ports restricted to the PCS client LAN
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

## Recorded Hardware Commissioning

The operator completed these discovery steps before the current profile was
recorded:

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

After supervised packet-level measurement, apply a corrected capture value
without changing the active Dire Wolf TX configuration:

```bash
./scripts/setup-direwolf-aprs.sh --set-rx-level 69
```

The command updates `config/pcs-install.conf`, backs up and replaces the
root-owned `/etc/pcs/aprs/audio.conf`, and restarts only the audio restoration
oneshot. It rolls both configuration files back if mixer verification fails.

The resulting as-built choices are:

- `W8IJC-10` on 144.5500 MHz simplex, 25 kHz, no tones
- SA818S V1.2, stock Easy Digi, and ALSA card `Device`
- active-high GPIO6 through the Easy Digi optocoupler
- -16 dB playback, 69% capture (+12 dB), AGC off; receive validation remains the four W8IJC-7 packets decoded at 26-56, with three at 38-56 around Dire Wolf's recommended level of 50
- 700 ms `TXDELAY`, 200 ms `TXTAIL`; repeat cold-start decode verification at 700 ms
- two-way IGate, GNSS-to-APRS-IS beacon, and WIDE1-1 fill-in service

Do not store an APRS-IS passcode or other credentials in Git. Supply secrets
interactively on the Pi and keep the resulting live configuration root-owned.

## Safe Activation Order

1. Keep the transmitter disconnected or use a suitable dummy load.
2. Run `--prepare-uart`; reboot if it reports a boot-file change.
3. Run `--import-commissioned-profile`, then confirm the FTDI bench adapter is
   unplugged and run `--list-audio`.
4. Record only the hardware evidence actually reconfirmed on this installation.
5. Run `--render-config rx` and `--validate-config rx`.
6. Run `--activate-rx`. This installs no playback, PTT, beacon, digipeater,
   FX.25 TX, or Internet-to-RF directives.
7. Verify AGW and KISS from a PCS LAN client and verify rejection from uplink interfaces.
8. Confirm the recorded hardware evidence still matches the installed wiring.
9. Run `--render-config tx` and `--validate-config tx` and review the diff.
10. Perform the operator-supervised RF test, then run `--activate-tx` only when
   the complete activation is intended.

Each successful activation saves the previous `/etc/direwolf.conf` under
`/etc/pcs/aprs/backups/`. If the new service fails to start, the installer
automatically restores the previous configuration; `--rollback` provides the
same recovery path later.

After successful activation, the installer refreshes an already-commissioned
PCS control panel and preserves its credentials. A full base installation also
installs the current panel at its normal later step.

## Tracker Clock-Jump Protection

Dire Wolf 1.8.1 already suppresses replay bursts for ordinary scheduled
beacons after a forward system-clock correction, but its fixed-interval
`TBEACON` path does not apply the same check. On a clockless cold boot, a later
GPS or NTP correction can therefore enqueue every missed tracker interval.

PCS applies
[`patches/direwolf-1.8.1-tracker-clock-jump.patch`](../patches/direwolf-1.8.1-tracker-clock-jump.patch)
to both fixed-interval tracker paths. After one due tracker event, an overdue
schedule is moved to one full interval after the corrected current time. This
keeps receive, IGate, and offline digipeating operation independent of Internet
availability while preventing historical position packets from flooding RF or
APRS-IS. `--prepare` verifies the protection in the installed executable and
rebuilds the pinned source if it is absent.

## Software-Only Validation Boundary

Without hardware, PCS can validate supported Dire Wolf installation, service
ownership, disabled staging state, configuration-file permissions, dashboard
integration, rendered RX/TX policy, capability availability, and Dire Wolf's
synthetic AX.25/FX.25 encode/decode tooling. `--software-test` uses temporary
WAV files and never opens a radio or keys PTT. It cannot validate
USB enumeration, ALSA routing, audio levels, PTT polarity/timing, radio
deviation, receiver performance, RF coverage, digipeating behavior, or on-air
regulatory correctness.

The current hardware/RF results are operator-supplied commissioning evidence;
repository tests do not reproduce RF measurements. A rebuild or hardware change
must not silently reuse those evidence flags without reconfirmation.

The system-wide header allocation, including RTC, fan, PTT, and the installed
SA818S UART, is maintained in [PCS GPIO Allocation](gpio-allocation.md).

## Upstream References

- [Dire Wolf project and release notes](https://github.com/wb2osz/direwolf)
- [Version-specific Dire Wolf User Guide](https://github.com/wb2osz/direwolf/blob/master/doc/User-Guide.pdf)
- [Dire Wolf application notes for IGates, digipeaters, packet routing, and radio interfaces](https://github.com/wb2osz/direwolf-doc)
- [Debian `gen_packets` manual](https://manpages.debian.org/testing/direwolf/gen_packets.1.en.html)
