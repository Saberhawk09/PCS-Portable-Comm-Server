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

Credentials, firmware flashing, radio identity, RF behavior, and on-air testing remain operator-supervised. Do not place passwords, callsign credentials, SIM details, router backups, or private deployment data in Git.
