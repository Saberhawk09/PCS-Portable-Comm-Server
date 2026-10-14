#!/usr/bin/env python3
"""Disposable recovery CLI campaign: synthetic data, libnm parsing, no services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from recovery_fixtures import state, archive, ROOT, manifest, SECRET
import pcs_recovery_stage as recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    args = parser.parse_args()
    if os.name != 'posix':
        raise SystemExit('POSIX permissions and libnm are required')
    import gi
    gi.require_version('NM', '1.0')
    from gi.repository import GLib, NM
    results = []
    cli = [sys.executable, str(ROOT / 'scripts/pcs_recovery_stage.py')]
    manifest_cli = [sys.executable, str(ROOT / 'scripts/pcs_recovery_manifest.py')]
    with tempfile.TemporaryDirectory(prefix='pcs-recovery-campaign-') as temp:
        root = Path(temp)
        def call(*argv, input=None, succeeds=True):
            result = subprocess.run(list(map(str, argv)), input=input, capture_output=True, timeout=60)
            assert (result.returncode == 0) == succeeds, 'unexpected CLI result: ' + str(argv[0])
            assert SECRET.encode() not in result.stdout + result.stderr, 'credential in CLI output'
            return result
        for mode, wifi, cell, old in [('legacy', False, False, False), ('vlan', False, False, False),
                                     ('vlan', True, False, False), ('vlan', True, True, False),
                                     ('legacy', True, False, True)]:
            name = f'{mode}-wifi{int(wifi)}-cell{int(cell)}-old{int(old)}'
            folder = root / name
            folder.mkdir(mode=0o700)
            data, request = state(mode, wifi, cell)
            if old: del data[manifest.MODE]
            raw = archive(folder / 'raw.tar.gz', data)
            source = raw
            if not old:
                # Exercise the exact manifest export filter used by the shell helper.
                sealed = subprocess.run(manifest_cli + ['seal-stream', '--commit', 'a' * 40, '--release', 'v2.2.0'],
                                        input=raw.read_bytes(), capture_output=True, timeout=60)
                assert sealed.returncode == 0 and not sealed.stderr
                source = folder / 'sealed.tar.gz'
                source.write_bytes(sealed.stdout)
                # Synthetic encrypted transport round-trip, never operator credentials.
                encrypted = folder / 'state.enc'
                call('openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-pass', 'stdin',
                     '-in', source, '-out', encrypted, input=b'Synthetic-Test-Passphrase\n')
                encrypted.chmod(0o600)
                ciphertext_hash = manifest.file_digest(encrypted)
                restored = folder / 'decrypted.tar.gz'
                call('openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-pass', 'stdin',
                     '-in', encrypted, '-out', restored, input=b'Synthetic-Test-Passphrase\n')
                restored.chmod(0o600)
                assert source.read_bytes() == restored.read_bytes()
                assert ciphertext_hash == manifest.file_digest(encrypted)
                source = restored
            call(*manifest_cli, 'inspect', source, '--sha256', manifest.file_digest(source))
            request_path = folder / 'request.json'
            request_path.write_bytes(manifest.json_bytes(request))
            plan_path = folder / 'plan.json'
            workspace = folder / 'workspace'
            target = folder / 'offline-target'
            target.mkdir(mode=0o700)
            (target / 'unrelated').write_bytes(b'preserve')
            call(*cli, 'init', workspace)
            call(*cli, 'plan', source, '--request', request_path, '--target-root', target,
                 '--sha256', manifest.file_digest(source), '--output', plan_path)
            call(*cli, 'stage', source, plan_path, workspace, '--dry-run')
            assert not (workspace / 'staged').exists()
            call(*cli, 'stage', source, plan_path, workspace)
            call(*cli, 'validate', source, plan_path, workspace, '--libnm')
            parsed = 0
            for path in (workspace / 'staged/rootfs' / manifest.NM).iterdir():
                text = path.read_text()
                keyfile = GLib.KeyFile.new()
                keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
                connection = NM.keyfile_read(keyfile, '/', NM.KeyfileHandlerFlags.NONE, None, None)
                assert connection.verify()
                parsed += 1
            assert list(target.iterdir()) == [target / 'unrelated']
            call(*cli, 'stage', source, plan_path, workspace)
            assert len(list(workspace.iterdir())) == 2  # marker and single generation
            call(*cli, 'rollback', workspace)
            assert not (workspace / 'staged').exists() and len(list(workspace.glob('retired-*'))) == 1
            call(*cli, 'stage', source, plan_path, workspace)
            results.append(dict(scenario=name, result='PASS', libnm_profiles=parsed))
        # Negative CLI cases: corrupt archive, missing profile, conflicting target.
        data, request = state()
        folder = root / 'negative'
        folder.mkdir(mode=0o700)
        request_path = folder / 'request.json'
        request_path.write_bytes(manifest.json_bytes(request))
        corrupt = folder / 'corrupt.tar.gz'
        corrupt.write_bytes(b'not-a-tar')
        result = call(*manifest_cli, 'inspect', corrupt, succeeds=False)
        assert b'Traceback' not in result.stderr
        results.append(dict(scenario='corrupt-archive', result='PASS-rejected'))
        del data[manifest.NM + 'pcs-vlan-parent.nmconnection']
        missing = archive(folder / 'missing.tar.gz', data)
        call(*cli, 'plan', missing, '--request', request_path, '--output', folder / 'missing-plan.json', succeeds=False)
        assert not (folder / 'missing-plan.json').exists()
        results.append(dict(scenario='incomplete-vlan', result='PASS-rejected'))
        data, _ = state()
        source = archive(folder / 'full.tar.gz', data)
        target = folder / 'target'
        (target / 'etc/pcs').mkdir(parents=True)
        (target / manifest.MODE).write_text('legacy\n')
        conflict = folder / 'conflict.json'
        workspace = folder / 'workspace'
        call(*cli, 'init', workspace)
        call(*cli, 'plan', source, '--request', request_path, '--target-root', target, '--output', conflict, succeeds=False)
        call(*cli, 'stage', source, conflict, workspace, succeeds=False)
        assert not (workspace / 'staged').exists()
        results.append(dict(scenario='conflicting-target', result='PASS-rejected'))
    print(json.dumps(dict(environment='disposable synthetic state; no activation', scenarios=results), indent=2))
    print('PASS: 8 scenarios; repeated staging/rollback in each successful scenario; no physical recovery qualification')


if __name__ == '__main__':
    main()
