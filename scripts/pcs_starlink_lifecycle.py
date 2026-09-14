#!/usr/bin/env python3
"""Queued PCS power action: bounded optional Mini request, then PCS action.

Called only by the existing authenticated/root dispatcher. Pi-Star preparation
remains there. No boot/service restart hook, retries or automatic power cycling.
"""
import argparse
import os
import subprocess


def perform(action, run=subprocess.run):
    if action not in {'reboot', 'shutdown'}:
        raise ValueError('Invalid PCS lifecycle action')
    try:
        result = run(['/usr/local/sbin/pcs-starlink', 'follow', action], timeout=12, check=False)
        if result.returncode:
            print('Mini follow request was disabled, unsupported or unconfirmed; continuing PCS action.', flush=True)
    except (OSError, subprocess.SubprocessError):
        print('Mini follow request timed out or failed; continuing PCS action.', flush=True)
    return run(['systemctl', '--no-block', 'reboot' if action == 'reboot' else 'poweroff'],
               timeout=10, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['reboot', 'shutdown'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root dispatcher required')
    perform(args.action)
