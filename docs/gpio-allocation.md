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
| EasyDigi APRS PTT | GPIO6 | 31 | Selected; not bench-tested | Active high on the Pi side. EasyDigi optoisolation produces an active-low radio PTT closure to ground. Dire Wolf directive: `PTT GPIO 6`. |
| MAX7219 CS/LOAD | GPIO8 | 24 | Installed and bench-tested | SPI0 CE0 through one channel of the 74AHCT125. |
| MAX7219 DIN | GPIO10 | 19 | Installed and bench-tested | SPI0 MOSI through one channel of the 74AHCT125. |
| MAX7219 CLK | GPIO11 | 23 | Installed and bench-tested | SPI0 SCLK through one channel of the 74AHCT125. |
| SA818S UART TX | GPIO14 | 8 | Reserved; logic level unverified | Pi TX to radio RXD. Do not connect until the radio UART level is measured. |
| SA818S UART RX | GPIO15 | 10 | Reserved; logic level unverified | Pi RX from radio TXD. Do not connect until the radio UART level is measured. |
| LCD E | GPIO17 | 11 | Installed and bench-tested | HD44780 enable. |
| Fan PWM | GPIO18 | 12 | Selected; behavior not measured | PWM-capable line; no automatic fan curve is selected yet. |
| WS2812 data | GPIO21 | 40 | Selected; not bench-tested | PCM-capable output through the 74AHCT125; six LEDs are planned. |
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
- The SA818 UART is separate from EasyDigi audio/PTT isolation. Keep it disabled
  until the module logic voltage has been measured and an appropriate level
  interface has been confirmed.
- GPIO21 uses the WS2812 driver's PCM path so GPIO18 remains available for
  hardware PWM fan control.
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
```

Real LCD, MAX7219, or WS2812 writes require the two-part
`--hardware --apply` confirmation. `demo all` intentionally excludes the fan,
PTT, UART, and RTC. The fan has a separate command that also requires an
explicit duty value because the Armor Lite control behavior and a safe thermal
curve have not been measured:

```bash
python3 scripts/pcs_gpio.py demo lcd --hardware --apply
python3 scripts/pcs_gpio.py lcd --line1 "PCS ONLINE" --line2 "LCD DRIVER READY" --hardware --apply
python3 scripts/pcs_gpio.py demo matrix --hardware --apply
sudo python3 scripts/pcs_gpio.py demo leds --hardware --apply
python3 scripts/pcs_gpio.py demo fan --fan-duty 100 --hardware --apply
```

The real backends expect the Raspberry Pi OS `gpiozero` and `spidev` Python
modules plus `rpi_ws281x` for the LEDs. Run `check` before commissioning.

## HD44780 Live Status

The installed 16x2 LCD rotates six pages every three seconds: PCS state and
uptime; CPU temperature in Celsius and Fahrenheit; active Cellular/WiFi/Offline
uplink; ModemManager cellular state and signal quality; gpsd fix state with
paired satellites in view/used; then active AP client count and the current
six-character Maidenhead grid square. Raw coordinates are never retained or
logged by the display daemon.

Install or inspect its GPIO-only service with:

```bash
bash scripts/setup-gpio-lcd.sh --install
bash scripts/setup-gpio-lcd.sh --check
```

The service owns only the six documented LCD GPIO lines. It does not request or
drive SPI, PTT, UART, fan, or WS2812 lines.

## MAX7219 Live Statistics

The installed matrix rotates three privacy-preserving PCS health indicators:

1. a `°C` unit symbol followed by CPU temperature in degrees Celsius
2. ModemManager cellular signal quality percentage
3. gpsd satellites in view, without retaining or logging coordinates

The first two metrics show an identifying icon followed by two large digits.
GPS shows a satellite/dish icon followed by the two-digit satellites-in-view
count. The count is replaced by `X` when there is no fix or no satellites and
`?` when gpsd data is unavailable. The service owns only `/dev/spidev0.0`; it
does not request or drive PTT, UART, fan, LCD, or WS2812 GPIO lines.

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

## PTT Logic

```text
GPIO6 LOW  -> EasyDigi optocoupler off -> radio PTT open -> receive
GPIO6 HIGH -> EasyDigi optocoupler on  -> radio PTT grounded -> transmit
```

The output must initialize inactive and remain inactive during boot, service
startup, shutdown, and software failure testing. Verify this with the radio
disconnected before permitting RF transmit.
