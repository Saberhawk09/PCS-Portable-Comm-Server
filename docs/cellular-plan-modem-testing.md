This document will eventually be for testing different cell carriers and plans with PCS.

As of now, I am currently using the T-Mobile MI30TI, or Basic Mobile Internet - 30GB - Taxes Included plan. This plan allows me to have 2 lines of 4G/5G data with 30GB of highspeed then unlimited throttled to 600Kbps for $15/mo. Perfect for experimentation, despite being the lowest priority on T-Mobile's network.

Unfortunately it seems this plan has been phased out and is no longer available for new users.

Some of my initial tests with the EM7455 and my Dell 7212 Toughbook when I was adding WWAN capability to it with some cheap internal antennas showed a large difference between T-Mobile and AT&T even at similar signal levels.

It's possible T-Mobile doesn't utilize the same bands as AT&T for my location, or the older Sierra Wireless cards are simply incompatible with them. I know my Dell specific DW5821e-eSIM which uses a Snapdragon X20 does NOT support an important band for T-Mobile specifically, so speeds are hurt there.

The EM7565 uses a Snapdragon X16 - CAT12 and offers 2x2 MIMO
The EM7455 uses an older Snapdragon X7 - CAT6 and offers 2x2 MIMO
The DW5821e-eSIM uses a newer Snapdragon X20 LTE - CAT16 and offers 4x4 MIMO*

The EM7455 and EM7565 have similar performance on T-Mobile, with the DW5821e-eSIM having somewhat better performance.


*Note: The DW5821e / eSIM variants TECHNICALLY support 4x4 MIMO thanks to the quad MHF4 antenna connectors, however 4x4 MIMO is disabled in software and must be re-enabled every time the modem is reset (not rebooted/power cycled). For more info see 
https://radenku.com/setting-modem-dell-dw5821e-openwrt/
https://github.com/danielewood/sierra-wireless-modems