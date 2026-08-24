# PCS Hardware Documentation

The PCS hardware is assembled and operational. This directory separates working-system facts from electrical and mechanical details that still need an exact as-built record.

## Current Operational Hardware

- Raspberry Pi 4 8GB
- Linksys EA4500 running OpenWrt
- Sierra Wireless EM7565 with USB adapter
- LTE and active GNSS antennas
- DS1307 RTC
- removable USB primary storage
- Pi-Star hotspot on guarded RTL8152 USB Ethernet with onboard Wi-Fi disabled
- HD44780 LCD, MAX7219 matrix, six WS2812 status pixels, and GPIO18 PWM fan
- SA818S/Easy Digi APRS subsystem with C-Media USB audio and GPIO6 PTT
- RAK4631 USB Meshtastic/NeoMesh gateway

## Hardware Records

- [Wiring Notes](wiring-notes.md)
- [Enclosure Notes](enclosure-notes.md)
- [Bill of Materials](../docs/bill-of-materials.md)
- [Power System](../docs/power-system.md)

## As-Built Documentation Still Needed

- enclosure dimensions, fasteners, mounting locations, and photos
- CAD document/export references and revision identifiers
- connector pinouts and cable routing
- actual fuse values, wire gauge, and protective-earth bonding
- measured rail voltage, current draw, and thermal results
- antenna connector labels and final external mounting details
- RAK4631 sensor model/mounting record and referenced temperature/humidity baseline

Do not treat design values in the wiring or power documents as measured facts until their as-built tables are completed.
