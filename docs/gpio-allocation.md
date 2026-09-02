# PCS GPIO Allocation

This is the central Raspberry Pi 4 header allocation for PCS. It distinguishes
confirmed or selected assignments from future reservations so planned hardware
is not presented as already wired or validated.

Use BCM GPIO numbering in software. Physical pin numbers refer to the Pi 4
40-pin header.

| Function | BCM GPIO | Physical pin | Status | Notes |
| --- | ---: | ---: | --- | --- |
| RTC SDA | GPIO2 | 3 | Existing subsystem | I2C SDA; kernel-managed. |
| RTC SCL | GPIO3 | 5 | Existing subsystem | I2C SCL; kernel-managed. |
| LCD RS | GPIO4 | 7 | Installed and bench-tested | HD44780-compatible 16x2 LCD in 4-bit mode. |
| EasyDigi APRS PTT | GPIO6 | 31 | Installed / tested | Active high on the Pi side. EasyDigi optoisolation produces an active-low SA818S PTT closure to ground. Dire Wolf 1.8.1 directive: `PTT GPIOD gpiochip0 6`. |
| MAX7219 CS/LOAD | GPIO8 | 24 | Installed and bench-tested | SPI0 CE0 through one channel of the 74AHCT125. |
| MAX7219 DIN | GPIO10 | 19 | Installed and bench-tested | SPI0 MOSI through one channel of the 74AHCT125. |
| MAX7219 CLK | GPIO11 | 23 | Installed and bench-tested | SPI0 SCLK through one channel of the 74AHCT125. |
| SA818S UART TX | GPIO14 | 8 | Installed / tested | `/dev/serial0` Pi TX to SA818S RXD at 9600 8N1; managed by `pcs-sa818.service`. |
| SA818S UART RX | GPIO15 | 10 | Installed / tested | `/dev/serial0` Pi RX from SA818S TXD at 9600 8N1; managed by `pcs-sa818.service`. |
| LCD E | GPIO17 | 11 | Installed and bench-tested | HD44780 enable. |
| Fan PWM | GPIO18 | 12 | Hardware PWM control implemented; RPM unmeasured | PWM0/ALT5, 100 Hz, fail-safe full duty. |
| WS2812 data | GPIO21 | 40 | Installed and live-tested | PCM output through the 74AHCT125 to six WS2812-compatible status pixels. |
| LCD D4 | GPIO27 | 13 | Installed and bench-tested | HD44780 4-bit data; as-built wiring. |
| LCD D5 | GPIO22 | 15 | Installed and bench-tested | HD44780 4-bit data; as-built wiring. |
| LCD D6 | GPIO23 | 16 | Installed and bench-tested | HD44780 4-bit data; as-built wiring. |
| LCD D7 | GPIO24 | 18 | Installed and bench-tested | HD44780 4-bit data; as-built wiring. |

## Bus and Ownership Boundaries

- The RTC remains owned by the Linux I2C stack and `setup-rtc.sh`.
- The MAX7219 uses SPI0 CE0, MOSI, and SCLK. GPIO9/MISO is not connected by this
  write-only display path. The installed PCS matrix was successfully exercised
  at 500 kHz with global intensity register value `0x03` on August 19, 2026.
- Dire Wolf owns GPIO6 when an RF transmit profile is deliberately activated.
  The general GPIO commissioning utility never toggles PTT.
  Guarded TX validation rejects a stale local GPIO17 setting left by an older
  `config/pcs-install.conf`; update it to GPIO6 and repeat the disconnected-radio
  polarity test before activation.
- The SA818S UART is separate from EasyDigi audio/PTT isolation. The serial
  console is disabled, `enable_uart=1` is active, and Bluetooth remains enabled.
  `pcs-sa818.service` owns `/dev/serial0` only long enough to apply and verify
  the commissioned radio profile before Dire Wolf starts.
- GPIO21 uses the WS2812 driver's PCM path so GPIO18 remains available for
  hardware PWM fan control. PCM cannot simultaneously serve an I2S audio
  device; the separate USB Dire Wolf sound adapter does not use PCM.
- GPIO18 uses PWM0/ALT5 at the cooler vendor's documented 100 Hz frequency.
  PCS disables only the unused onboard analogue audio function that otherwise
  shares the PWM block; the separate USB Dire Wolf sound adapter is unaffected.
- Confirm the live pin function with the Raspberry Pi `pinout` and `pinctrl`
  tools before connecting hardware.

## Offline-Safe Commissioning Code

`scripts/pcs_gpio.py` is the first device-code foundation. Its default demo is
a simulator and does not touch GPIO:

```bash
python3 scripts/pcs_gpio.py pins
python3 scripts/pcs_gpio.py check
python3 scripts/pcs_gpio.py demo all --duration 0
python3 scripts/pcs_gpio.py lcd --line1 "PCS ONLINE" --line2 "LCD DRIVER READY"
python3 scripts/pcs_gpio.py lcd-status
python3 scripts/pcs_gpio.py led-status
```

Real LCD, MAX7219, fan, or WS2812 writes require the two-part
`--hardware --apply` confirmation. `demo all` intentionally excludes the fan,
PTT, UART, and RTC. The guarded fan demo requires an explicit duty value:

```bash
python3 scripts/pcs_gpio.py demo lcd --hardware --apply
python3 scripts/pcs_gpio.py lcd --line1 "PCS ONLINE" --line2 "LCD DRIVER READY" --hardware --apply
python3 scripts/pcs_gpio.py demo matrix --hardware --apply
sudo python3 scripts/pcs_gpio.py demo leds --hardware --apply
sudo python3 scripts/pcs_gpio.py led-status --once --hold-seconds 10 --hardware --apply
sudo python3 scripts/pcs_gpio.py demo fan --fan-duty 100 --hardware --apply
```

The real backends expect the Raspberry Pi OS `gpiozero` and `spidev` Python
modules plus `rpi_ws281x` for the LEDs. Run `check` before commissioning.

The persistent fan service uses a conservative 40/55/70/85/100% curve at
0/45/55/65/75 C with 3 C downshift hysteresis. Missing temperature data and
service startup/shutdown force 100% duty. Install it with
`scripts/setup-gpio-fan.sh --install`; one reboot is normally required for the
PWM0 overlay. The fan provides no tachometer feedback, so duty is verified in
software while physical RPM remains unmeasured. See the
[52Pi Armor Lite Pi 4 documentation](https://wiki.52pi.com/index.php?title=ZP-0110)
for the vendor's PWM capability and 100 Hz example.

## HD44780 Live Status

The installed 16x2 LCD rotates seven pages every three seconds: PCS state and
uptime; CPU temperature in Celsius and Fahrenheit; active Cellular/WiFi/Offline
uplink; NetworkManager cellular data state and ModemManager signal quality; gpsd fix state with
paired satellites in view/used; then active AP client count and the current
six-character Maidenhead grid square; then APRS agent state and session counters.
The APRS page shows `APRS Stats: Ok` when connected, `APRS Stats: MSG` when the
mailbox has unread entries, or `APRS Stats: Err` when status is unavailable.
Its second line begins as `Pkt RX:0 Msgs:0` and compacts larger counts to remain
within 16 characters. Raw coordinates and mailbox contents are never retained
or logged by the display daemon.
The displayed cellular `On`/`Off` state follows the NetworkManager data session,
not the modem's separate registered/available state.
Warnings append a centered plain-language condition page after the six normal
pages. An unreachable OpenWrt AP is a hard fault that suppresses the normal
rotation and shows `HARD FAULT` / `ROUTER OFFLINE`. When
Pi-Star monitoring is configured, an unreachable hotspot appends
`WARNING` / `PI-STAR OFFLINE`; builds without Pi-Star do not generate that
warning. Critical CPU, root-disk, or local-service faults suppress the normal
rotation and show only centered `HARD FAULT` pages until the critical condition
clears.

Install or inspect its GPIO-only service with:

```bash
bash scripts/setup-gpio-lcd.sh --install
bash scripts/setup-gpio-lcd.sh --check
```

The service owns only the six documented LCD GPIO lines. It does not request or
drive SPI, PTT, UART, fan, or WS2812 lines.

## Six-Pixel WS2812 Status Indicators

The GPIO21 chain assigns one stable responsibility to each pixel, starting at
the Pi-side `DATA IN` end:

| Pixel | Condition | Normal / informational color | Fault color |
| ---: | --- | --- | --- |
| 0 | Pi CPU temperature | Green | Amber at 75 C; red at 85 C |
| 1 | Root filesystem use | Green | Amber at 85%; red at 95% |
| 2 | Primary USB storage | Green when mounted | Amber when missing |
| 3 | Local services / configured Pi-Star dependency | Green when local services are healthy and Pi-Star is reachable or not configured | Amber when configured Pi-Star is unreachable; red when one or more local systemd units fail |
| 4 | Active network uplink / OpenWrt AP | Green for Cellular or WiFi when the AP is reachable | Amber when the uplink is offline; red when the OpenWrt AP is offline |
| 5 | GPS fix | Green when locked | Amber for no fix |

A dim blue-violet pixel means the corresponding collector returned no usable
data. The chain is configured for 800 kHz GRB ordering and a global brightness
of 32/255, so the status display remains subdued. The daemon rewrites the chain
only when a status color changes and turns all six pixels off during a clean
service stop.
While APRS mail is unread, pixel 3 briefly pulses white on each poll and then
returns to its normal local-services color; the underlying six pixel meanings
do not change.

Install or inspect the persistent service explicitly:

```bash
bash scripts/setup-gpio-leds.sh --install
bash scripts/setup-gpio-leds.sh --check
```

The installer keeps the pinned `rpi-ws281x` dependency in an isolated virtual
environment and enables `pcs-gpio-leds.service`. The base installer exposes it
as `PCS_SETUP_GPIO_LEDS=yes|no`. Stop the service before running a manual LED
demo so only one process owns the PCM/DMA output.

## MAX7219 Health Annunciator

The installed matrix is an across-the-room health annunciator. A dim checkmark
indicates normal operation. Every warning shows an `!` followed by the
affected subsystem icon; every critical fault shows an `X` followed by the
subsystem icon. Alert sources are CPU temperature (75/85 C warning/critical),
root-disk use (85/95 percent), missing primary USB storage, failed systemd units,
an unreachable OpenWrt AP/switch, a configured but unreachable Pi-Star hotspot,
no active uplink, and unavailable GPS fix. The OpenWrt fault uses the Wi-Fi
symbol and critical severity; Pi-Star uses a dedicated raspberry symbol and
warning severity. Local systemd failures remain critical. Detailed live values
remain on the LCD.
Unread APRS mail prepends a letter/envelope icon to this rotation. It is an
informational frame and does not create a warning or fault by itself.
The healthy checkmark uses intensity 1; warning and critical frames use
intensity 10 out of the MAX7219's 0-15 range. The matrix service owns only
`/dev/spidev0.0`; it does not request or drive PTT, UART, fan, LCD, or WS2812
GPIO lines.

Install or inspect the persistent service explicitly:

```bash
bash scripts/setup-gpio-stats.sh --install
bash scripts/setup-gpio-stats.sh --check
```

The installer enables SPI0 when necessary, installs `python3-spidev` if it is
missing, installs `pcs_gpio.py` as `/usr/local/sbin/pcs-gpio`, and enables
`pcs-gpio-stats.service`. The base installer offers this as the explicit
`PCS_SETUP_GPIO_STATS=yes|no` optional choice, so PCS builds without the matrix
leave it disabled.

## Boot and Shutdown Indicator States

Each LCD, WS2812, or matrix installer also registers that device with the
shared `pcs-gpio-startup.service`. At boot the LCD shows `PCS Booting Up` and
`Stand by...`, the six pixels cycle through the color spectrum, and the matrix
lights every pixel before checkerboard frames. Boot states remain for at most
90 seconds while the ordinary health inputs settle. The service hands off
early when no alerts remain and always hands off on timeout so persistent
faults stay visible.

The installers also register the device with `pcs-gpio-shutdown.service`. The
shutdown service is ordered before the startup and normal display daemons,
which makes systemd stop those daemons first and run the final display writes
afterward during a normal halt, reboot, or poweroff:

- LCD: `PCS Offline` and `Shutting Down`
- WS2812: all six pixels blue at the configured 32/255 global brightness
- MAX7219: a bed with three compact Z glyphs at intensity 1

The drivers release GPIO, PCM/DMA, and SPI ownership without clearing those
final frames, so the controllers retain them while PCS remains electrically
powered. Removing power naturally blanks all three displays. Device markers
under `/etc/pcs/gpio-shutdown` are shared by startup and shutdown and ensure a
build probes only the visual hardware installed by its corresponding PCS setup
script. The shutdown state is informational;
it does not replace confirmation that Linux has completed shutdown before
disconnecting power.

## PTT Logic

```text
GPIO6 LOW  -> EasyDigi optocoupler off -> radio PTT open -> receive
GPIO6 HIGH -> EasyDigi optocoupler on  -> radio PTT grounded -> transmit
```

This polarity, release-to-RX behavior, and the EasyDigi optocoupler path have
been bench- and RF-tested. Dire Wolf is the only service permitted to request
GPIO6; the radio initializer never touches PTT.
