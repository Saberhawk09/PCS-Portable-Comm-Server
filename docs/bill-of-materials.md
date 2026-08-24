# Bill of Materials

Status reflects the latest known acquisition state. **Purchased / awaiting delivery** does not mean installed or validated. **Purchased / as-built record pending** means the part has been acquired, but its exact installation still needs to be reconciled with the physical PCS.

## Compute Hardware

| Item                 | Qty | Status      | Price | Notes                                |
| -------------------- | --: | ----------- | ----: | ------------------------------------ |
| Raspberry Pi 4 8GB   |   1 | Owned       |     - | Main server hardware                 |
| DS1307-compatible RTC module | 1 | Installed / tested | - | Boot-time reference clock |
| External flash drive |   1 | Installed / tested |     - | Removable storage for LAN file share |

## Networking / Cellular

| Item                        | Qty | Status    | Price | Notes                             |
| --------------------------- | --: | --------- | ----: | --------------------------------- |
| Sierra Wireless EM7565      |   1 | Installed / tested |     - | Cellular modem                    |
| M.2 WWAN to USB adapter     |   1 | Installed / tested |   $33 | USB adapter for modem             |
| External LTE antennas       |   2 | Installed / tested |   $13 | Cellular antennas                 |
| MHF4 to SMA female pigtails |   5 | Purchased |    $8 | Antenna adapter cables            |
| External GPS antenna        |   1 | Installed / tested |     - | Active GPS antenna for GNSS / NTP use |
| Thermal pad, 100x100x0.5mm  |   1 | Purchased |    $8 | Modem / adapter thermal interface |
| 9x9x5mm heatsinks           |  20 | Purchased |    $7 | Small component heatsinks         |

## Routing Hardware

| Item                              | Qty | Status | Price | Notes                            |
| --------------------------------- | --: | ------ | ----: | -------------------------------- |
| Linksys EA4500 running OpenWrt |   1 | Installed / tested |     - | Dedicated PCS AP / Ethernet switch |

## Radio / Expansion Hardware

| Item                               | Qty | Status                         | Price | Notes                                      |
| ---------------------------------- | --: | ------------------------------ | ----: | ------------------------------------------ |
| Pi-Star hotspot                    |   1 | Installed / tested             |     - | Fixed PCS-LAN node at `10.42.0.3`          |
| APRS USB sound adapter             | Sabrent USB audio / C-Media card ID `Device` | Installed / tested | 1 | RX 69% (+12 dB), AGC off; TX now -16 dB pending repeat decode validation (bidirectional AFSK previously tested at -18 dB) |
| APRS radio/PTT hardware            | SA818S V1.2 + stock Easy Digi | Installed / tested | 1 set | GPIO6 optoisolated PTT, 144.5500 MHz RF TX/RX, UART programming, messaging, IGate, and WIDE1-1 fill-in operation validated |
| RAK4631 Meshtastic expansion       | TBD | Purchased / as-built record pending | - | USB/MQTT gateway and GPSD position delivery live-validated; sensor model, mounting record, RF behavior, and sensor accuracy pending |

## Local Status Hardware

| Item | Qty | Status | Price | Notes |
| ---- | --: | ------ | ----: | ----- |
| HD44780-compatible 16x2 LCD | 1 | Installed / tested | - | GPIO-only rotating status and plain-language fault display |
| MAX7219 8x8 LED matrix | 1 | Installed / tested | - | SPI health annunciator through the 74AHCT125 |
| WS2812-compatible status pixels | 6 | Installed / tested | - | GPIO21 PCM health-indicator chain through the 74AHCT125 |
| 74AHCT125 level shifter | 1 | Installed / tested | - | 3.3 V to 5 V logic for the matrix and WS2812 paths |
| Armor Lite PWM fan | 1 | Installed / tested | - | GPIO18 hardware PWM duty validated; RPM unmeasured |

## Power System

| Item                          | Qty | Status    | Price | Notes                             |
| ----------------------------- | --: | --------- | ----: | --------------------------------- |
| Anderson Powerpole inlet      |   1 | Purchased / as-built record pending |   $15 | DC input                          |
| Automotive blade fuse holders |   2 | Purchased / as-built record pending |    $5 | Fuse holders                      |
| Bussmann 3A blade fuses       |   5 | Purchased / as-built record pending |    $5 | 12 V and converter branch fusing  |
| 7.5A slow-blow fuse           |   1 | Purchased / as-built record pending |     - | Main selected-DC input fuse       |
| SPST switches                 |   4 | Purchased / as-built record pending |    $6 | Auxiliary switching               |
| DPDT center-off switch        |   1 | Purchased / as-built record pending |   $12 | AC/DC source selection            |
| Switched fused C14 inlet      |   1 | Purchased / as-built record pending |    $8 | AC grid input                     |
| Mean Well LRS-100-24          |   1 | Purchased / as-built record pending |     - | 120 VAC to 24 VDC, 108 W          |
| SparkFun COM-18732            |   1 | Purchased / as-built record pending |     - | 8-36 V input to regulated 12 V    |
| Mean Well PSD-30A-5           |   1 | Purchased / as-built record pending |     - | Regulated 12 V to 5 V, 5 A        |
| 120 mm cooling fans           |   2 | Installed / operating                 |     - | Thermal performance not yet measured |

## Enclosure / Mechanical

| Item                                  | Qty   | Status                              | Price | Notes                                  |
| ------------------------------------- | ----: | ----------------------------------- | ----: | -------------------------------------- |
| Prototype enclosure and mounting hardware | 1 set | Installed / as-built record pending | - | Dimensions, fasteners, photos, and CAD references TBD |

## Estimated Listed Cost

Known listed cost total: **$120**

This total only includes items with prices currently listed in this document. Items marked with `-` were already owned, purchased separately, or still need their purchase price recorded, so they are not included.

No hardware remains marked **Planned**. Purchased expansion hardware remains uninstalled until delivery, and the power and enclosure rows still require reconciliation with the physical as-built unit before they can be called installed or electrically validated.
