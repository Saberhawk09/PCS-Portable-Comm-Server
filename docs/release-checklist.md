# Release Checklist

Use this checklist for every public PCS release.

## Local Validation

- [ ] working tree contains only intended release changes
- [ ] `python -m unittest discover -s tests -v` passes
- [ ] all Python files compile with `python -m py_compile`
- [ ] all shell scripts pass `bash -n`
- [ ] `git diff --check` reports no whitespace errors
- [ ] README, changelog, and documentation describe the current behavior

## PCS Validation

- [ ] deploy the candidate commit to `/home/pi/Projects/PCS-Portable-Comm-Server`
- [ ] run `./scripts/setup-pcs-control-panel.sh` when web files or permissions changed
- [ ] run `./scripts/pcs-self-test.sh`
- [ ] confirm zero failures and investigate every warning
- [ ] verify the public homepage and admin login from a PCS client
- [ ] verify an authenticated admin action
- [ ] verify Samba, NTP, GNSS, OpenWrt, and Pi-Star as applicable
- [ ] when APRS is selected, run the selected engine's setup script with `--check` and `--capabilities`
- [ ] for Dire Wolf, also run `--software-test` and review `--validate-config rx` and `--validate-config tx`; investigate every blocker instead of bypassing it
- [ ] confirm the selected APRS service is disabled/inactive when staged; an intentionally active Dire Wolf or Graywolf engine requires its documented guarded activation and RF validation, and Graywolf additionally requires the runtime GPIO6 watchdog
- [ ] reboot and repeat the self-test for changes affecting startup or systemd

## GitHub Validation

- [ ] push a branch and open a pull request against `main`
- [ ] wait for GitHub Actions CI to pass
- [ ] review the final PR diff and release notes
- [ ] merge the PR
- [ ] create an annotated version tag on the merged commit
- [ ] publish a GitHub Release from that tag using the changelog entry
- [ ] verify the release page, tag, and default branch
- [ ] close or update issues completed by the release

## Manual Field Checkpoints

Credentials, firmware flashing, radio identity, RF behavior, and on-air testing
remain operator-supervised. Do not place passwords, APRS-IS passcodes, callsign
credentials, MQTT credentials, SIM details, router backups, or private
deployment data in Git. Meshtastic software staging and service/status checks
are part of the Pi-side test baseline. For the commissioned USB node, repeat
broker-policy, 15-bit map-precision, GPSD position, and controlled opt-in
RF-to-map checks after relevant radio, channel, MQTT, or map changes. BLE pairing
is required only for a build that deliberately selects BLE instead of the
deployed scoped USB transport. Sensor accuracy remains an operator-supervised
checkpoint.
