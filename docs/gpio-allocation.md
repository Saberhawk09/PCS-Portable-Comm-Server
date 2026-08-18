# PCS GPIO Allocation

This is the central Raspberry Pi 4 header allocation for PCS. It distinguishes
confirmed or selected assignments from future reservations so planned hardware
is not presented as already wired or validated.

Use BCM GPIO numbering in software. Physical pin numbers refer to the Pi 4
40-pin header.

| Function | BCM GPIO | Physical pin | Status | Notes |
| --- | ---: | ---: | --- | --- |
| RTC SDA | GPIO2 | 3 | Reserved / existing subsystem | I2C SDA; keep dedicated to the RTC bus. |
| RTC SCL | GPIO3 | 5 | Reserved / existing subsystem | I2C SCL; keep dedicated to the RTC bus. |
| EasyDigi APRS PTT | GPIO17 | 11 | Selected; not yet wired or bench-tested | Active high on the Pi side. EasyDigi optoisolation produces an active-low radio PTT closure to ground. Dire Wolf directive: `PTT GPIO 17`. |
| Fan PWM | GPIO18 | 12 | Future reservation | Keep available for the planned fan-control design; no measured implementation is documented yet. |
| SA818S UART TX | GPIO14 | 8 | Future reservation | Pi TX. Direct SA818S control requires an appropriate, validated logic-level interface and UART configuration. |
| SA818S UART RX | GPIO15 | 10 | Future reservation | Pi RX. Direct SA818S control requires an appropriate, validated logic-level interface and UART configuration. |

## Additional Reservations

- Keep SPI0 GPIO10, GPIO9, and GPIO11 available unless a later reviewed design
  assigns them.
- GPIO17 and GPIO18 also have SPI1 chip-select alternate functions. PCS reserves
  them for PTT and possible fan control, so a future SPI1 design must not assume
  those chip-select lines are free.
- Do not assign a WS2812 data GPIO until the indicator-light design and driver
  implementation are selected.
- Confirm the live pin function with the Raspberry Pi `pinout` and `pinctrl`
  tools before connecting hardware.

## PTT Logic

```text
GPIO17 LOW  -> EasyDigi optocoupler off -> radio PTT open -> receive
GPIO17 HIGH -> EasyDigi optocoupler on  -> radio PTT grounded -> transmit
```

The output must initialize inactive and remain inactive during boot, service
startup, shutdown, and software failure testing. Verify this with the radio
disconnected before permitting RF transmit.
