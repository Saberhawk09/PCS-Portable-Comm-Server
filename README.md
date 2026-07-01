# PCS-Portable-Comm-Server

A portable communications server built around a Raspberry Pi 4 with dedicated routing, integrated cellular internet, GPS disciplined NTP, LAN file sharing, and web monitoring.

What started as an annoyance caused by Windows networking has evolved into my first real hardware and software project.

# WARNING, AI GENERATED CODE

I'm a highschool dropout working at a convenience store, of course I'm using AI to help me code. With that said, all code will be reviewed by me (not just hey it works, let's keep going) and all ideas / designs will be 100% human generated.

# Premise

As usual, when multi-billion dollar companies fail to understand how to code their software properly, open source comes to the rescue yet again.

# The Issue That Started This Project

During ARRL Field Day 2026 me and my amateur radio club ran into some networking issues. We had 3 Windows laptops running radio contact logging software for the event, and wanted all 3 to use the same log file for accurate tracking of stats.

The problem was... Windows update.

During the Winter Field Day prior, we had used my Dell Latitude 7212 Toughbook for networking. All logging machines connected to my machine via the WiFi hotspot function and it worked well enough. The only problem was w
Windows not allowing me to turn the hotspot on without an internet connection. My toughbook had a DW5821e cellular modem, but the signal was very marginal so I had to make sure once the hotspot was on it never turned off.

Nerve wracking and annoying, but we made it work.

Windows being Windows, this suboptimal but functional solution never worked again.

It was an hour before start time, while everyone was setting up antennas I was configuring the network share. Only trouble was, Windows had other ideas. Devices couldn't connect to my hotspot, once connected my Toughbook never showed they were, they didn't have internet access, and I could never see my Toughbook share over the local network. It was a massive headache that was thankfully solved by a club member who let us use his portable cellular router while we sourced a replacement dedicated club router.

Once we had everything hooked up via Ethernet, all the file sharing worked and we never had a single issue with networking or the rest of the event.

Needless to say I was annoyed. Not just at Windows, but at myself for assuming it would work properly and not planning ahead. Well the lessons from that mistake have evolved into this project.