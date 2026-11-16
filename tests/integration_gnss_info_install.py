#!/usr/bin/env python3
"""Explicit inactive-install/rollback proof in a disposable Linux VM only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
spec = importlib.util.spec_from_file_location('stage_gnss_info', REPO / 'scripts/stage-gnss-intelligence.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def file_state(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def services():
    return subprocess.check_output(['systemctl', 'show', '-p', 'ActiveState', '-p', 'ExecMainPID',
        'gpsd.service', 'chrony.service', 'pcs-gnss.service', 'ModemManager.service', 'NetworkManager.service'], text=True)


def main():
    if (sys.argv[1:] != ['--run'] or os.geteuid() != 0 or
            Path('/etc/pcs-qualification-disposable').read_text().strip() != 'local-qemu-acceptance-only'):
        raise SystemExit('Requires --run as root in the named disposable QEMU environment')
    protected = ('/etc/pcs/gnss.json', '/etc/default/gpsd', '/etc/chrony/chrony.conf',
                 '/etc/chrony/conf.d/pcs-wwan-gps.conf', '/boot/firmware/config.txt')
    before = {p: file_state(p) for p in protected}
    active = services()
    with tempfile.TemporaryDirectory(prefix='gnss-info-install-') as temp:
        stage = installer.stage(Path(temp) / 'stage')
        entries = installer.verify_stage(stage)['files']
        original = {p: file_state(p) for p in entries}
        native_write, writes = installer.atomic_bytes, [0]
        def interrupted(path, data):
            writes[0] += 1
            if writes[0] == 3:
                raise OSError('Disposable installation interruption fixture')
            return native_write(path, data)
        with mock.patch.object(installer, 'atomic_bytes', side_effect=interrupted):
            try:
                installer.install(stage, Path(temp) / 'interrupted-backups')
            except OSError:
                pass
            else:
                raise AssertionError('Expected an interrupted installation')
        assert {p: file_state(p) for p in entries} == original
        assert services() == active
        print('PASS: interrupted installation restores all managed files before retry')
        backup = installer.install(stage, Path(temp) / 'backups')
        try:
            assert services() == active, 'Inactive installation changed protected services'
            assert {p: file_state(p) for p in protected} == before
            subprocess.run(['systemd-analyze', 'verify'] + [str(REPO / 'systemd' / u) for u in installer.UNITS], check=True)
            status = json.loads(subprocess.check_output(['/usr/local/sbin/pcs-gnss-info', 'web-status'], text=True))
            assert 'timing' in status and 'assistance' in status
            assert 'chipcode' not in json.dumps(status)
            # Unknown operation cannot escape into arbitrary root arguments.
            denied = subprocess.run(['/usr/local/sbin/pcs-gnss-info', 'web-update'], input='{"operation":"reboot"}',
                                    text=True, capture_output=True)
            assert denied.returncode != 0
            modified = Path('/usr/local/lib/pcs/pcs_gnss_info.py')
            installed_bytes = modified.read_bytes()
            modified.write_bytes(installed_bytes + b'\n# subsequent operator edit\n')
            try:
                installer.rollback(backup)
            except ValueError:
                pass
            else:
                raise AssertionError('Rollback overwrote a subsequent operator edit')
            modified.write_bytes(installed_bytes)
            print('PASS: exact inactive installation, units, private status and fixed action boundary')
        finally:
            installer.rollback(backup)
        assert {p: file_state(p) for p in entries} == original, 'Rollback failed to restore exact file content/absence'
        assert services() == active
        assert {p: file_state(p) for p in protected} == before
        print('PASS: exact rollback and GPSD/Chrony/modem/network service preservation')


if __name__ == '__main__':
    main()
