# PCS recovery development report — October 12, 2026

## Final status

**Partially implemented with safe, tested functionality.** Inspect, plan, inactive staging, validation and staging rollback are implemented and disposable-tested. Automatic activation and a complete current-topology reinstall are deliberately not implemented. Simulated recovery is not accepted physical recovery.

No PCS appliance, router, switch, modem or radio was contacted. No production configuration, recovery archive or credentials were used. No push, PR, main merge, release publication or hardware acceptance was performed. The separate disposable QEMU guest was shut down after validation; its overlay and logs remain available.

## Implemented

Worktree: `pcs-recovery-v221`; branch: `codex/v2.2.1-recovery`. It starts at preserved maintenance HEAD `dc6ee47ed3e0259c79d017f7d2648e5e471359eb`.

Implementation HEAD: `f87d7ff34aab114d3ea2730e4cea9f7debb2e18f`. Tested source tree: `be01690a65d97dab24f6e8f7cd98091015716534`. The subsequent report-only commit does not change tested implementation. Its ID is available in the branch log.

| Local commit | Changes |
| --- | --- |
| `94efdc0529410cd083f54429ef6e18dabad6b23b` | Manifest creation/inspection, private export integration, archive boundary hardening. Files: `scripts/pcs_recovery_manifest.py`, `scripts/pcs-reinstall-state.sh`, `scripts/pcs_reinstall_archive.py`, `tests/recovery_fixtures.py`, `tests/test_recovery_manifest.py`, `tests/test_reinstall_archive.py`. |
| `1ee11c2e851d13bf9eb38b91cb407cd229f8978c` | Explicit topology planning, private staging, validation, conflict checks and retained staging rollback. Files: `scripts/pcs_recovery_stage.py`, `tests/test_recovery_stage.py`. |
| `0d13f3798c08067765ee1ed983568129383cb7b1` | Synthetic CLI/encryption/libnm campaign and narrow existing-CI extension. Files: `tests/integration_recovery.py`, `.github/workflows/ci.yml`. |
| `3f37dcc5ad21a1f06356d34f34c11e7c7e24b6b1` | Candidate workflow and acceptance boundaries. Files: `docs/recovery-staging.md`, `docs/recovery-coverage.md`, `docs/full-stack-reinstall.md`, `docs/v2.2.1-release-readiness.md`, `docs/README.md`, `scripts/README.md`, `CHANGELOG.md`. |
| `2703268ccbe8bcf72529fe6b9b483c5b25d91ac8` | Explicit VLAN guard regeneration requirements and cellular ModemManager dependency in manifest/plan, corresponding tests and `docs/recovery-staging.md`. |
| `f87d7ff34aab114d3ea2730e4cea9f7debb2e18f` | Correct eventual installation modes/owners in `scripts/pcs_recovery_stage.py` and assertions in `tests/test_recovery_stage.py`; actual staging remains private. |

The schema-1 manifest records export time, checkout commit/version/dirty status, hashed machine identity, file hashes/metadata, non-secret profile identities and relationships, captured policy components, dependencies and manual boundaries. It resides inside the encrypted archive. Creation failure fails the export pipeline; inspection recomputes inventory and rejects invalid manifests. Legacy manifest-free archives remain readable with unknown source metadata, without invented settings.

The staging CLI provides `init`, `plan`, `stage --dry-run`, `stage`, `validate --libnm` and `rollback`. It has no activation command. It requires explicit LAN/WAN/parent UUIDs and interfaces; validates the supported native VLAN 10/20 or legacy topology; reuses current component configuration validators; and lists every selected file, dependency, exclusion and detected conflict. Optional offline target inspection detects conflicting files/profiles and later drift. An omitted target means conflicts have not been assessed.

Staging writes only a newly initialized private workspace. Identical repetition validates and returns unchanged. A changed plan/state is rejected. Ordinary failures clean only that invocation's pending generation. Rollback retires staged artifacts without deleting evidence. Symlinks, staged hardlinks, unsafe archive paths, excessive payloads and duplicate/colliding archive paths are rejected.

Existing `scripts/pcs_reinstall_restore.py` behavior and the base installer's recovery invocation are unchanged. The installer does not automatically call the new staging tool. See `docs/recovery-staging.md` for the supported procedure and limitations.

## Recovery coverage: before and after

“Staged” means validated bytes in an inactive private directory, not installed or accepted on hardware.

| Configuration | Before this session | Now supported | Still manual/deferred |
| --- | --- | --- | --- |
| Backup identity/integrity | Encrypted archive and checksum; no structured capture contract | Versioned manifest, per-file integrity, archive digest pinning, explicit legacy absence | Trusted archive provenance; authenticated envelope migration |
| Native Ethernet VLAN 10 LAN / 20 WAN | Captured files; no coordinated VLAN recovery | Explicit parent/child UUID/interface/ID checks and profile/mode staging | Physical identity, guard regeneration, atomic activation and rollback |
| Legacy Ethernet | Existing installer/recovery behavior | Explicit legacy LAN and distinct WAN staging; no adapter-based inference | Replacement-interface remapping is not supported |
| Wi-Fi and WireGuard | Existing recovery selection/identity contracts | Existing selection reused; private profile/key/policy staging; unit coverage retained | Live connectivity, peer reachability and operator activation |
| Unified uplinks | Persistent JSON backed up, omitted from automatic modern restore | Exact preferred/fallback/startup/readiness/recovery policy and referenced cellular profile staged; dependencies identified | Modem/APN suitability and paid-session acceptance; unsupported MAC-only mapping fails closed |
| Management permissions | Persistent configuration captured | Validated persistent policy staged with intended mode/owner | Firewall/service installation and physical management isolation |
| Bandwidth | Persistent policy captured | Exact default/client/aggregate policy staged and validated | Runtime counters are excluded; services and generated installation state must be recreated |
| SNMP | Local JSON/credential captured; manual switch setup | Validated local configuration and private community credential staged | Physical switch configuration and mapping acceptance remain manual |
| Four INA226 monitors | Power configuration captured | Exact custom roles/calibration/scaling configuration staged; no invented values | Wiring, calibration accuracy and physical measurements remain unverified |
| LCD/GPIO | Software layout and installer settings; generated markers | Saved installer settings safely parsed and staged | Layout/pins come from reviewed software; units/GPIO shutdown markers regenerated |
| Other legacy identity state | Existing commissioned identity restore | Existing helper unchanged; new plan explicitly excludes files it does not stage | APRS/admin/SSH/other component activation still follows existing controlled procedures |
| Old standalone cellular fallback | Historical configuration may be captured | Explicitly excluded from new staged selection; current unified uplinks supported | Legacy fallback migration needs a separate contract |
| Runtime/service/firewall state | Mixed captured and regenerated state | Exclusions and required generated VLAN guard files/services made explicit | No replay of runtime counters, stale service state or external switch setup |

Schema 1 intentionally supports the repository's implemented native VLAN layout and explicit legacy `eth0` LAN contract. It does not guess that an arbitrary `eth0` is LAN: the request and captured profile must agree. Extra Ethernet WANs, incompatible mode markers, unresolved MAC-only WAN policy and unsupported VLAN layouts are rejected.

## Validation

The final implementation was exported with Git file modes into a new disposable Debian 13 QEMU overlay, backed by the retained previous maintenance guest. Networking used restricted QEMU user networking with loopback SSH only, no bridge. Guest identity and its disposable marker were checked. Full Python discovery ran as unprivileged `qualtest`; namespace checks ran only inside that guest. No package download was needed during this session.

Environment: Python 3.13.5, PyYAML 6.0.2, libnm 1.52.1. The CI PyYAML pin is 6.0.3, so this is local validation, not an exact hosted CI run. Windows checks used Python 3.14.2; Android used the available JDK 21/SDK/Gradle cache offline.

| Executed check | Result and evidence |
| --- | --- |
| `timeout 900 python3 -m unittest discover -s tests -v` | Final tree: **941 discovered; 937 passed, 4 skipped; 0 failures/errors**, 97.492 seconds. Four skips are native Windows Winsock tests. `tmp/recovery-v221/linux-unit-final.log`. |
| Recovery-focused tests: `python -m unittest discover -s tests -p 'test_recovery*.py' -v` | Linux: **28 passed**, also included in final full discovery. Windows: **27 passed, 1 POSIX skip**, before the final metadata-only mode assertion update; final Linux includes that update. `recovery-windows-final.log`, final full log. |
| Legacy recovery/archive tests: `python -m unittest discover -s tests -p 'test_reinstall*.py' -v` | Linux: **24 passed**, included in final discovery. Windows: **20 passed, 4 POSIX skips**. `legacy-windows-final.log`. |
| Documentation: `python -m unittest discover -s tests -p test_documentation.py -v` | **6 passed** on Windows and included in final Linux discovery. `docs-windows-final.log`. |
| `timeout 120 node --test tests/test_pcs_home.js` | Final tree: **17 passed**, no failures/skips. `linux-js-final.log`. |
| `python3 -m compileall -q scripts tests web`; `bash -n` for every `scripts/*.sh`; executable mode assertions | All passed on final tree. Commands retained in `validate-linux.sh`; result in `linux-validation-final.log`. |
| `timeout 300 python3 tests/integration_recovery.py --run` | Final tree: **8 scenarios passed**, **17 profiles verified by libnm**; five successful scenarios each exercised repeat staging/rollback. `linux-validation-final.log`. |
| ACL dependency/inheritance probe | Passed with `/usr/bin/setfacl`; inherited ACL checked. `recovery-acl.log`. |
| `python3 tests/integration_switch_snmp.py` | Passed real isolated GET/WALK, optional objects, private credential handling and agent-loss checks. `recovery-snmp.log`. |
| `systemd-analyze verify systemd/pcs-switch-monitor.service` | Exit 0. `recovery-switch-unit.log`. |
| `python3 tests/integration_vlan_namespaces.py` | Passed VLAN separation, routed traffic, ingress and infrastructure egress isolation checks. `recovery-vlan.log`. |
| `python3 tests/integration_vlan_keyfiles.py` | Passed all staged parent/LAN/WAN libnm verification. `recovery-keyfiles.log`. |
| `python3 tests/integration_bandwidth.py --run`, then `--run --vlan` | Both passed NAT, policy/caps, accounting, idempotence, cleanup and collision checks. `recovery-bandwidth-legacy.log`, `recovery-bandwidth-vlan.log`. |
| Windows `test_pcs_qualify_lan.py` / `test_pcs_qualify_windows.py` discovery | **15 + 18 passed**; native Windows checks cover the four Linux platform skips. `windows-lan.log`, `windows-native.log`. |
| Android preview: `gradlew.bat --offline --no-daemon testDebugUnitTest lintDebug` | **15 JVM tests passed**, no errors/skips; lint **No issues found**. Build successful, 35 tasks (13 executed, 22 cached). `android-preview.log`, preview checkout XML/lint reports. |
| Final Git review | `git diff --check dc6ee47e HEAD` clean; intended paths/modes reviewed; candidate, maintenance, recovery and preview worktrees clean before report commit. |

All log paths above are under workspace `tmp/recovery-v221/`, unless stated otherwise. Focused suites overlap full discovery and must not be added to its count. The seven dependency/namespace checks ran at `3f37dcc5`; their tested implementations are unchanged by the two later recovery metadata commits. Android source is unchanged by those commits as well. Final full Python/JavaScript/static/recovery campaign checks ran at `f87d7ff`.

Campaign scenarios: legacy Ethernet; VLAN; VLAN with Wi-Fi; VLAN with Wi-Fi and cellular settings; old manifest-free legacy archive; corrupt archive rejection; incomplete VLAN rejection; conflicting target rejection. Successful cases include inspection, planning, dry-run, private staging, libnm validation, unrelated target preservation, repeat staging, rollback and restaging. New archive cases also exercise synthetic encrypted transport. Unit tests separately cover secret omission, permissions, archive boundaries, plan tampering, target drift and ordinary failure cleanup.

Early failures were fixed rather than hidden: the first Windows focused run found a real target UUID collision-check gap for additional Wi-Fi/GSM profiles; the implementation was corrected. The first Linux campaign found a synthetic legacy keyfile missing its required `[ethernet]` section; the fixture was corrected without weakening libnm validation. Retained logs: `tmp/recovery-focused-windows.log` and `tmp/recovery-v221/recovery-first-linux.log`.

Not run: hosted GitHub Actions, complete CI Docker wrappers, a full Pi reinstall, physical recovery, live WAN fault injection, cellular data, RF, hardware stress, real-phone acceptance or release signing. Existing component installers can activate services and assume Pi hardware; bypassing those guards would not provide trustworthy isolated full-reinstall evidence. Android emitted its existing Gradle deprecation warning despite successful lint/tests.

## Safety assessment

- New manifests and plans omit raw secret values, but filenames, UUIDs, fingerprints and hashes remain private metadata. Staged payloads contain secrets: retain private storage. Linux verifies invoking-user ownership, directories 0700 and files 0600. Intended eventual installation modes/owners are separate plan metadata.
- Per-file hashes and checksum sidecars detect corruption, not malicious replacement of both archive and metadata. Existing AES-CBC encryption is retained for backwards compatibility and is not authenticated encryption. A versioned authenticated envelope remains future work; independently trust the archive/digest source now.
- Source version identifies the export checkout, not proof of every installed binary. Missing manifest/source fields never certify old archive identity.
- Profiles retain their original autoconnect flags and policies retain enabled settings. They are inactive because they remain outside live paths. Do not copy the staged root wholesale into `/` or load its profiles into NetworkManager.
- Offline target conflict checks are optional and explicitly reported as unassessed when omitted. Staging never writes the target. Required package/service/generated-file lists are requirements, not assertions that dependencies are installed.
- VLAN guard rules, service and NetworkManager ordering must be regenerated from reviewed physical management identities. No switch setup, firewall activation or network connection is applied by these tools.
- Staging rollback preserves retired generations; it is not live network rollback. An interrupted process may leave a private pending directory/lock: preserve evidence and initialize another workspace. This is not a power-loss-qualified transaction.
- Old archives remain readable within strict safety boundaries. Previously tolerated unsafe paths/links are rejected, and extraction now requires an empty protected destination. There is no safety bypass for historical malformed archives.

## Release preparation

Local `v2.2.0` resolves to `22965383cf1500201d82544901401aa8799b8f27`. Original Linux candidate `86b072b5f753216104b695649e69d9acca6aa446` and maintenance `dc6ee47ed3e0259c79d017f7d2648e5e471359eb` remain preserved. No remote release status was changed or reverified.

Diff review confirms dashboard snapshot, switch polling, uplink startup/readiness, self-test and web-action implementations remain unchanged from `86b072b`. The explicit CI `acl` installation/inheritance check remains present. `scripts/deploy-dashboard-snapshot-stage.sh` retains executable mode 100755 and was not executed. Existing historical qualification records were not edited. Changelog/readiness documents label v2.2.1 and recovery work as unreleased candidates and keep current VLAN, FQ-303, cold-boot and other physical acceptance gates open.

Android recommendation: source-compatible for explicit later inclusion, subject to real-phone acceptance. The exact five-file `86b072b` → `c3046fdce79d78340d7aaea01c2d8ec9271b58e7` patch applied cleanly in separate branch `codex/v2.2.1-integration-preview`, worktree `pcs-v221-integration-preview`. Preview patch commit: `3be7ea2c1c3c7adfc7b7ee9f42aa2b52be25881d`; final preview HEAD: `1f89f3c4d8eed8c8b3693d1f25a7a818faba64aa`, including the two later related recovery metadata commits. Its only difference from the recovery implementation is those five Android files. Android subtree `a90c2ff6854c35720c715abf3e14b9575add8663` exactly matches `c3046fd`.

Review found optional snapshot freshness parsing and nested card rendering compatible with the Linux API; authentication/enrollment/transport behavior was not changed. The preview was not merged into the recovery, maintenance or published branch. No APK or signing material was deleted or altered. Companion v0.3.0 remains a local candidate.

## Operator actions

1. Review the local commits and `docs/recovery-staging.md`; decide whether the recovery phase and Android candidate belong in v2.2.1. Review the synthetic example plan at workspace `tmp/recovery-v221/synthetic-review-example/plan.json`; it contains fabricated fixture data, not appliance measurements.
2. Before physical recovery, select a separately trusted backup and explicit source topology; verify replacement hardware/interface/UUID identities, independent console access, management isolation and a retained original disk/backup. Resolve all plan conflicts. Do not use current staging as a live restore command.
3. Complete and disposable-test an activation transaction and independent rollback/watchdog before authorizing actual recovery. Existing component installers are not yet an inactive orchestration layer.
4. In a separately authorized physical session, validate VLAN LAN/WAN separation, management reachability, reboot persistence, Wi-Fi/WireGuard, switch mapping, power calibration/roles, and controlled uplink recovery. Preserve original storage until acceptance. Paid cellular/RF/fault tests require their own deliberate gates.
5. Test Companion v0.3.0 on a real phone: enrollment/TLS, older/missing freshness responses, stale/unavailable states, nested cards and private/admin projections.
6. Complete outstanding current-topology qualification, including FQ-303 where applicable, then explicitly authorize release integration/publication. This session supplies no such authorization.

## Next Codex assignment

Implement an **offline activation transaction planner and validator** for native VLAN recovery: required physical management identities, generated VLAN guard artifacts, mode/profile/service ordering and a rollback journal. Keep activation disabled; add disposable failure-injection tests for incomplete dependencies and interrupted steps. This is the smallest useful step toward repeatable complete recovery without prematurely enabling live restoration.

Highest-value next step toward v2.2.1: review this tested recovery phase and select the intended release scope before commissioning the remaining physical acceptance session.
