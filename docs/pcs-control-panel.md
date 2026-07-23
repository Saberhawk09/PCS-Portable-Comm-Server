# PCS Control Panel

PCS includes a local web control panel for common operator tasks.

Primary dashboard URL:

```text
http://10.42.0.1
```

Direct control panel URL:

```text
http://10.42.0.1:8080
```

The port 80 dashboard URL redirects to the control panel on port 8080.

## Purpose

The control panel provides a simple local interface for checking system health and running common PCS actions without remembering terminal commands.

It is intended for trusted local PCS clients only.

Keep this interface on the trusted PCS LAN.

## Services

The control panel uses two systemd services:

```text
pcs-control-panel.service
pcs-dashboard-redirect.service
```

`pcs-control-panel.service` runs the main web interface on port 8080.

`pcs-dashboard-redirect.service` runs a small port 80 redirect service.

## Setup Scripts

Install the main control panel:

```bash
./scripts/setup-pcs-control-panel.sh
```

Install the port 80 dashboard redirect:

```bash
./scripts/setup-dashboard-redirect.sh
```

## Web Actions

The control panel uses an allowlisted dispatcher script:

```text
/usr/local/sbin/pcs-web-action
```

Repository source:

```text
scripts/pcs-web-action.sh
```

The web app does not run arbitrary shell commands. It only calls known PCS actions.

Available actions include:

```text
View PCS Status
Run Self-Test
View Storage
View Wi-Fi
Connect Wi-Fi
Disable Wi-Fi Radio
View Cellular
Connect Cellular
Disconnect Cellular
Test Cellular
Sync USB to SD Backup
Mount USB Storage
Unmount USB Safely
Restart PCS Services
Restart Samba
Sync Time Now
Restart Chrony
Restart GPSD
View Restart Logs
```

## Dashboard Cards

The dashboard shows quick status cards for:

```text
System stats
Core services
Network
Storage
Web/admin interfaces
Time / NTP
Samba
Cellular / WWAN
GPS / GNSS
```

These cards are intended to give a quick field-health overview before using the detailed action buttons.

## Field Access

The dashboard includes local client information:

```text
PCS dashboard:      http://10.42.0.1
PCS control panel:  http://10.42.0.1:8080
Cockpit:            https://10.42.0.1:9090
Primary share:      \\10.42.0.1\PCS-Share
Backup share:       \\10.42.0.1\PCS-Backup
LAN NTP server:     10.42.0.1
```

Windows NTP test:

```cmd
w32tm /stripchart /computer:10.42.0.1 /samples:5 /dataonly
```

## WAN and Client Display

The dashboard attempts to show:

- WAN/public IP
- Uplink interface
- Uplink source IP
- Clients visible on the Pi `eth0` network

Client visibility works best when the access point is in AP/switch mode and clients receive addresses directly from the Pi.

## Friendly Client Names

Friendly local client names can be configured with:

```text
config/local-client-names.tsv
```

This file is ignored by Git so private local device names and MAC addresses are not published.

A safe example file is tracked:

```text
config/local-client-names.example.tsv
```

Example format:

```text
10.42.0.2	Linksys EA4500 OpenWrt AP
10.42.0.123	Field Laptop
aa:bb:cc:dd:ee:ff	Field Laptop
```

## Health Checks

Check service status:

```bash
systemctl status pcs-control-panel.service --no-pager -l
systemctl status pcs-dashboard-redirect.service --no-pager -l
```

Test the main control panel locally:

```bash
curl -I http://127.0.0.1:8080
```

Test the port 80 redirect:

```bash
curl -I http://127.0.0.1
```

Test the redirect health endpoint:

```bash
curl http://127.0.0.1/health
```

## Notes

The control panel is an operator convenience layer.

The terminal scripts remain the source of truth for setup and troubleshooting.

If the control panel hangs or behaves strangely, restart it:

```bash
sudo systemctl restart pcs-control-panel.service
```
