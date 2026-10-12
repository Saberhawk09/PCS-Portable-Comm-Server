# Qualification evidence index

Reviewed October 12, 2026. This index summarizes existing evidence; it does not
record new campaigns. The [field qualification guide](field-qualification.md)
retains the original dated results, exact source hashes and accepted wording.

Workstation locations below are relative to the operator's PCS workspace, outside
the Git repository. They identify retained reports, not distributable release
attachments. Do not upload their witness logs, private snapshots, addresses,
credentials or raw operator artifacts. The paths are intentionally plain text
because these private directories do not exist in a public clone.

| Test / stage | Date and source | Topology / environment | Recorded result | Evidence location and limitations |
| --- | --- | --- | --- | --- |
| FQ-001 cached observation | September 30, normal PCS v2.1.1 `236dc2067ee3972cb5f66036dba716d029652909`, harness `6869c70d3af41c9a8d735f4c1c5b0ea624f3c0fb`; also a prerequisite October 2 | Commissioned PCS, legacy untagged LAN and separate Ethernet WAN | PASS before accepted WAN campaigns | `tmp/qualification-live-6869c70/LIVE-RESULT.md`; October 2 record in the field guide. Cached observation only, not WAN/RF/boot acceptance. |
| FQ-002 harmless-marker expiry | Same September 30 baseline/harness; also passed before October 2 FQ-302 | Commissioned PCS, legacy topology | PASS prerequisite | Same report and October 2 record. Proves the bounded marker scenario, not arbitrary fault recovery. |
| Phase 1 safety/cleanup development | September 28, Phase 1 development sequence; see original guide for checkpoint provenance | Disposable Debian 13 amd64 QEMU, not Pi hardware | Eight privileged tests and actual guest reboot cleanup recorded passing | `tmp/qualification-vm/qualification-acceptance.json`, `qualification-integration.log`; original field-guide history. Not a new claim about a later exact head or appliance. |
| FQ-301-v4 | September 30; normal v2.1.1 `236dc2067ee3972cb5f66036dba716d029652909`; harness `6869c70d3af41c9a8d735f4c1c5b0ea624f3c0fb` | Commissioned PCS; legacy LAN `eth0`, WAN `eth1`, Wi-Fi standby; independent Windows LAN witness; RF engines inactive | PASS: sampled LAN HTTP and IPv4 transition/recovery; 124 receipts, zero failed samples, maximum gap ~1.085 seconds | `tmp/qualification-live-6869c70/LIVE-RESULT.md`. Not IPv6 failover, physical Starlink loss, RF recovery or gap-free connectivity. Earlier incomplete attempts remain INCONCLUSIVE. |
| FQ-302 | October 2; normal source `2d2c2ef6308991efcf4daaa26ceba42a942bbd56`; harness `0638862537fdda2e6d76b84fb2719fb3eb0318f2` | Commissioned PCS, legacy Ethernet/Wi-Fi plus cellular; independent Windows witness; RF engines inactive | PASS: automatic manager-owned cellular IPv4 fallback and owned-session release; 140 receipts, zero failed samples, max gap ~1.092 seconds | `tmp/qualification-live-0638862/report.md`, retained `LIVE-RESULT.md` and private archives. Not operator-session preservation, IPv6/RF/physical failure or gap-free continuity. |
| FQ-303 development | October 2 final source `03dc0774cff58b53595fd3248eb110688a5316ff`; included in v2.1.2 October 4 | Disposable Linux guest, synthetic sessions and witness campaigns; Windows witness CI | Software validation: focused 17 tests and both final-head disposable campaigns passed; development package distinguishes earlier unchanged regression suites | `tmp/qualification-fq303/ACCEPTANCE.md`, `fq303-focused-final.log`, `fq303-campaign-final.log`, `ci-final.log`. No accepted commissioned live FQ-303 result found. A failed/interrupted request to run it is not a pass. |
| VLAN software validation | October 10 v2.2.0 `22965383cf1500201d82544901401aa8799b8f27` | CI disposable packet/SNMP/bandwidth namespaces and libnm keyfiles | Published main CI passed; see release-linked results | [v2.2.0 validation](v2.2.0-validation.md). Does not certify physical switch, modem, Mini, boot or RF behavior. |
| Current SNMP and startup commissioning | October 10–11; local source `86b072b5f753216104b695649e69d9acca6aa446` records deployed work | Commissioned VLAN 10 LAN / VLAN 20 WAN with a local router, not Mini | Physical port 2 mapping, five-second polling, link speed, limited IPv4 management-service isolation and steady-state health recorded passing | [Commissioning record](dashboard-snapshot-commissioning.md); protected appliance evidence remains at its recorded location. Startup was enabled after grace expired, so this is not cold-boot acceptance. |
| Current VLAN FQ-301/FQ-302/FQ-303 | No accepted run | Current LAN `eth0.10`, WAN `eth0.20` | NOT QUALIFIED by the legacy harness | Admission still pins `eth0`/physical `eth1` and rejects VLAN identity. Requires reviewed topology adaptation, disposable validation, then separate supervised admission and authorization. |

For FQ-303, the operator must create a fresh session through the supported audited
Connect path. Qualification does not connect/disconnect cellular or manufacture
provenance. Session identity, manager ownership, effective routing, witness
completeness and cleanup are separate assertions.

Future evidence entries should include the exact source, boot/topology, scenario
and duration, artifact checksum, independent witness result, cleanup outcome and
narrow accepted claim. Store raw material privately and publish sanitized summaries
only after review. Repeated flap/hysteresis, current IPv6 field qualification and
RF/APRS recovery remain separate scenarios.
