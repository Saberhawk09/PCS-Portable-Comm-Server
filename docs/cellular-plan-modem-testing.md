# Cellular Plan and Modem Comparison Notes

> **Operator snapshot from July 12, 2026:** plan names, prices, availability,
> prioritization, and carrier coverage change. This file preserves the original
> comparison context; it is not a current carrier-plan recommendation or a PCS
> software requirement. The maintained EM7565 hardware/software baseline is in
> [WWAN Card Setup Notes](wwan-card-setup.md).

This document records early testing of different cell carriers, plans, and
modems considered for PCS.

At the time of this snapshot, I was using the T-Mobile MI30TI, or Basic Mobile Internet - 30GB - Taxes Included plan. It allowed two lines of 4G/5G data with 30GB of high-speed data and then unlimited service throttled to 600 Kbps for $15/month. It was useful for experimentation despite having low priority on T-Mobile's network.

Unfortunately it seems this plan has been phased out and is no longer available for new users.

Some of my initial tests with the EM7455 and my Dell 7212 Toughbook when I was adding WWAN capability to it with some cheap internal antennas showed a large difference between T-Mobile and AT&T even at similar signal levels.

It's possible T-Mobile doesn't utilize the same bands as AT&T for my location, or the older Sierra Wireless cards are simply incompatible with them. I know my Dell specific DW5821e-eSIM which uses a Snapdragon X20 does NOT support an important band for T-Mobile specifically, so speeds are hurt there.

- The EM7565 uses a Snapdragon X16 (Cat 12) and offers 2x2 MIMO.
- The EM7455 uses an older Snapdragon X7 (Cat 6) and offers 2x2 MIMO.
- The DW5821e-eSIM uses a newer Snapdragon X20 LTE (Cat 16) and nominally
  offers 4x4 MIMO.*

The EM7455 and EM7565 have similar performance on T-Mobile, with the DW5821e-eSIM having somewhat better performance.


*Historical operator note: the DW5821e/eSIM variants expose four MHF4 antenna
connectors, but 4x4 MIMO may require a software change after a modem reset.
Preserve this as testing context rather than a supported PCS procedure. See the
[DW5821e OpenWrt notes](https://radenku.com/setting-modem-dell-dw5821e-openwrt/)
and [Sierra Wireless modem scripts](https://github.com/danielewood/sierra-wireless-modems).
