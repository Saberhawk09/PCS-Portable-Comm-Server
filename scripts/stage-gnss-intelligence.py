#!/usr/bin/env python3
"""Stage/install additive GNSS tools; never enable/restart appliance services.

Install/rollback require separate deployment authorization. Receiver settings,
enrollment, Chrony, modem and networking configuration are outside this manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcs_assistnow import atomic_bytes, DEFAULT
from pcs_gnss import atomic_json

REPO = Path(__file__).resolve().parents[1]
MODULES = ('pcs_gnss.py', 'pcs_assistnow.py', 'pcs_gnss_diagnostics.py', 'pcs_gnss_manager.py', 'pcs_gnss_info.py')
UNITS = ('pcs-gnss-info.service', 'pcs-assistnow-refresh.service', 'pcs-assistnow-manual.service',
         'pcs-assistnow-live.service', 'pcs-assistnow-refresh.timer')
CONFIRM = 'I authorize PCS GNSS software installation'
ASSIST_JOURNAL = Path('/var/lib/pcs-assistnow/receiver-transaction.json')


def plan(repo=REPO):
    files = {}
    for name in MODULES:
        files['/usr/local/lib/pcs/' + name] = (repo / 'scripts' / name, 0o644)
    files['/usr/local/sbin/pcs-gnss'] = (repo / 'scripts/pcs-gnss.py', 0o755)
    for name in UNITS:
        files['/etc/systemd/system/' + name] = (repo / 'systemd' / name, 0o644)
    files['/usr/local/lib/pcs/pcs_control_panel.py'] = (repo / 'web/pcs-control-panel/pcs_control_panel.py', 0o644)
    return files


def stage(target, repo=REPO):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    entries = {}
    for index, (destination, (source, mode)) in enumerate(plan(repo).items()):
        raw = source.read_bytes().replace(b'\r\n', b'\n')
        name = str(index)
        (target / name).write_bytes(raw)
        entries[destination] = {'source': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'mode': mode}
    generated = {
        '/usr/local/sbin/pcs-gnss-info': ('#!/usr/bin/python3\nimport sys, subprocess\nsys.path.insert(0, "/usr/local/lib/pcs")\nfrom pcs_gnss_info import main\ntry:\n    main()\nexcept (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):\n    print("GNSS action unavailable; inspect operator guide.", file=sys.stderr)\n    sys.exit(1)\n', 0o755),
        '/etc/sudoers.d/pcs-gnss-info': ('pi ALL=(root) NOPASSWD: /usr/local/sbin/pcs-gnss-info web-status, /usr/local/sbin/pcs-gnss-info web-update\n', 0o440),
        '/etc/systemd/system/pcs-control-panel.service.d/gnss-info.conf': ('[Service]\nExecStart=\nExecStart=/usr/bin/python3 /usr/local/lib/pcs/pcs_control_panel.py\n', 0o644),
    }
    for destination, (raw, mode) in generated.items():
        name = str(len(entries))
        (target / name).write_text(raw, encoding='utf-8', newline='\n')
        entries[destination] = {'source': name, 'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'mode': mode}
    atomic_json(target / 'manifest.json', {'version': 1, 'files': entries})
    return target


def verify_stage(target):
    manifest = json.loads((target / 'manifest.json').read_text())
    expected = set(plan()) | {'/usr/local/sbin/pcs-gnss-info', '/etc/sudoers.d/pcs-gnss-info',
                             '/etc/systemd/system/pcs-control-panel.service.d/gnss-info.conf'}
    if manifest.get('version') != 1 or set(manifest['files']) != expected:
        raise ValueError('Stage target manifest mismatch')
    for entry in manifest['files'].values():
        if not str(entry['source']).isdigit() or entry['mode'] not in (0o644, 0o755, 0o440):
            raise ValueError('Invalid staged source')
        path = target / entry['source']
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Staged content changed')
    return manifest


def install(target, backups=Path('/var/backups/pcs-gnss-info')):
    manifest = verify_stage(target)
    # Refuse links and unknown file types rather than following external paths.
    # Complete every preflight before writing the first managed destination.
    states = {}
    for destination, entry in manifest['files'].items():
        path = Path(destination)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Managed destination contains a symlink')
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Managed destination is not regular')
            states[destination] = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
                                   'before': entry['source']}
        else:
            states[destination] = None
    sudoers = next(e for p, e in manifest['files'].items() if p.startswith('/etc/sudoers'))
    subprocess.run(['visudo', '-cf', str(target / sudoers['source'])], check=True, capture_output=True)
    backup = backups / (time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    for destination, state in states.items():
        if state is not None:
            shutil.copy2(destination, backup / state['before'])
    receipt = {'version': 1, 'before': states, 'installed': manifest['files'], 'complete': False}
    atomic_json(backup / 'receipt.json', receipt)
    try:
        for destination, entry in manifest['files'].items():
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(path, (target / entry['source']).read_bytes())
            os.chmod(path, entry['mode'])
            os.chown(path, 0, 0)
        for path in (Path('/etc/pcs/assistnow'), Path('/var/lib/pcs-assistnow')):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chown(path, 0, 0)
            os.chmod(path, 0o700)
        if not Path('/etc/pcs/assistnow.json').exists():
            atomic_json('/etc/pcs/assistnow.json', DEFAULT)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        receipt['complete'] = True
        atomic_json(backup / 'receipt.json', receipt)
    except BaseException:
        rollback(backup, interrupted=True)
        raise
    return backup


def rollback(backup, interrupted=False):
    if not interrupted and ASSIST_JOURNAL.exists():
        journal = json.loads(ASSIST_JOURNAL.read_text())
        if not isinstance(journal, dict) or journal.get('state') == 'pending':
            raise ValueError('Resolve the receiver RAM recovery journal before removing its management software')
    receipt = json.loads((backup / 'receipt.json').read_text())
    if set(receipt['before']) != set(plan()) | {'/usr/local/sbin/pcs-gnss-info', '/etc/sudoers.d/pcs-gnss-info',
                      '/etc/systemd/system/pcs-control-panel.service.d/gnss-info.conf'}:
        raise ValueError('Unexpected rollback targets')
    if not interrupted:
        for destination, entry in receipt['installed'].items():
            path = Path(destination)
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError('Installed file changed; manual rollback review required')
    for destination, previous in receipt['before'].items():
        path = Path(destination)
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Rollback target is a link')
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            atomic_bytes(path, (backup / previous['before']).read_bytes())
            os.chmod(path, previous['mode'])
            os.chown(path, previous['uid'], previous['gid'])
    subprocess.run(['systemctl', 'daemon-reload'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage', 'install', 'rollback'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--confirm')
    args = parser.parse_args()
    if args.action == 'stage':
        print(stage(args.directory))
    else:
        if os.geteuid() != 0 or args.confirm != CONFIRM:
            parser.error('Separate deployment authorization and root are required')
        if args.action == 'install':
            print(install(args.directory))
            print('Installed inactive. No services started, stopped, enabled or restarted.')
        else:
            rollback(args.directory)
            print('Files restored. Review service state with the operator guide.')


if __name__ == '__main__':
    main()
