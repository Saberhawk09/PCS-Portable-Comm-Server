For a wipe/reinstall of this exact commissioned PCS, choose `COMMISSIONED`.
That mode recreates the non-secret Pi-side configuration for the fitted USB
storage, WWAN/GNSS and LAN GPSD, automatic Wi-Fi/Starlink/cellular uplinks,
read-only Starlink telemetry, LCD, WS2812 indicators, MAX7219 matrix, PWM fan,
four-monitor INA226 commissioned power profile, buzzer, and staged Dire Wolf and
Meshtastic software. A single attached non-LAN Ethernet adapter is detected and
MAC-bound as the Starlink uplink; multiple candidates fail closed so the
operator can set `PCS_STARLINK_MAC` explicitly.

The commissioned rebuild can accept the single encrypted archive created by
`pcs-reinstall-state.sh --export`. The installer first rebuilds every managed
software component from the checked-out source, then restores the allowlisted
Wi-Fi/WireGuard, API/TLS, SSH, Samba, Bluetooth, Pi-Star pairing, Meshtastic,
Starlink pairing, APRS-IS, and other private identity state. The archive and
passphrase stay outside Git, extraction is limited to a temporary root-only
directory, and generated units/helpers are never restored from the archive.
Without an archive, setup asks for a new Samba password and creates fresh
identities. It does not log into or modify Pi-Star, the Meshtastic radio,
OpenWrt, or Starlink.
An exact archive also restores the generated non-secret commissioning settings.
If its complete recorded APRS audio, channel, PTT, transmit-audio, and timing
validation set is present, the installer restores the managed Dire Wolf 1.8.1,
audio/radio helpers, APRS Agent, and recorded RX/TX service state. Otherwise it
fails safe with APRS stopped. This does not replace the post-reboot network, RF,
and physical acceptance test.

Create the private archive on the working PCS before wiping it:

```bash
./scripts/pcs-reinstall-state.sh --export /mnt/pcs-usb/PCS-Share/pcs-private-reinstall.tar.gz.enc
```

During `COMMISSIONED` setup, enter that archive's absolute path at the encrypted
recovery prompt and enter its passphrase once. Supplying the archive restores
credentials after their owning software has been freshly installed; it is not
a system-image restore. Keep the archive, passphrase, and optional checksum in
separate trusted locations.

When boot configuration must expose I2C, the installer stages the power monitor
for first boot and exits at an explicit reboot boundary. After reboot, run the
self-test below; no component installer rerun is required.