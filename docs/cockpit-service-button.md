# Cockpit Service Button

PCS includes a systemd one-shot service that can be started from Cockpit to restart core PCS services.

## Purpose

This provides a simple "restart PCS services" button from the Cockpit Services page.

The service is:

    pcs-restart-services.service

In Cockpit, open:

    Services -> pcs-restart-services.service -> Start

Because this is a one-shot service, it normally appears as inactive after it finishes. That is expected.

## What It Restarts

The restart script handles:

- Samba / SMB
- Chrony
- ModemManager
- Avahi
- PCS LAN/AP handoff profile

It intentionally avoids restarting NetworkManager and Cockpit itself to reduce the chance of breaking remote access.

## Installing the Service

Copy the included service file into systemd:

    sudo cp systemd/pcs-restart-services.service /etc/systemd/system/pcs-restart-services.service
    sudo systemctl daemon-reload
    sudo systemctl disable pcs-restart-services.service

The service should remain disabled because it is used manually as a button, not automatically at boot.

## Testing

Start it manually:

    sudo systemctl start pcs-restart-services.service
    systemctl status pcs-restart-services.service --no-pager -l

Then run:

    ./scripts/pcs-self-test.sh

