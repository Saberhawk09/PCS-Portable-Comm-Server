# Linksys EA4500 Access Point Setup

PCS currently uses a Linksys EA4500 v1 as a stock-firmware Wi-Fi access point and Ethernet switch.

The EA4500 is not acting as the router in the current PCS layout. The Raspberry Pi handles routing, DHCP/NAT sharing, DNS forwarding, dashboard, Samba, NTP, WWAN, and GPS services.

## Current PCS AP Layout

Client devices
    -> Wi-Fi / Ethernet
Linksys EA4500 v1
    -> LAN port, not WAN port
Raspberry Pi eth0
    -> PCS services and upstream internet

## Addressing

Raspberry Pi / PCS server: 10.42.0.1
Linksys EA4500 LAN IP:    10.42.0.2
Client DHCP range:         10.42.0.x
Gateway for clients:       10.42.0.1
DNS for clients:           10.42.0.1

## Linksys Settings

LAN IP:       10.42.0.2
DHCP server:  disabled
WAN port:     unused
Cable:        Pi eth0 to Linksys LAN port
Wi-Fi:        enabled

## Raspberry Pi Ethernet Profile

The Pi Ethernet port must use the PCS shared profile:

eth0 connection: pcs-router-wan-share
IPv4 method:     shared
IPv4 address:    10.42.0.1/24

Do not use netplan-eth0 for normal PCS AP operation. netplan-eth0 may provide the same static IP, but it does not provide the NetworkManager shared-mode DHCP/NAT behavior.

## Quick Checks

On the Pi:

nmcli device status
nmcli -f NAME,TYPE,AUTOCONNECT,DEVICE connection show
ip -br addr show eth0
./scripts/pcs-self-test.sh

Expected:

eth0 connected pcs-router-wan-share
pcs-router-wan-share autoconnect yes
netplan-eth0 autoconnect no
eth0 has 10.42.0.1/24

From a Windows client connected to the Linksys Wi-Fi:

ipconfig
ping 10.42.0.1
ping 8.8.8.8
ping google.com

Expected client settings:

IP address: 10.42.0.x
Gateway:    10.42.0.1
DNS:        10.42.0.1

If a client receives a 192.168.x.x address, the Linksys DHCP server is probably still enabled.

If a client receives no address, verify that eth0 is connected using pcs-router-wan-share.
