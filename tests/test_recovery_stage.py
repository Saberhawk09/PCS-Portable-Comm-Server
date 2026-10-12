import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from recovery_fixtures import archive, bundle, state, manifest, ROOT, SECRET, CELL
import pcs_recovery_stage as recovery


class RecoveryStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        recovery.initialize(self.workspace)

    def prepare(self, mode='vlan', wifi=True, cellular=True, sealed=True):
        data, request = state(mode, wifi, cellular)
        path = bundle(self.root, data, sealed)
        plan, payload = recovery.build_plan(path, request)
        return path, plan, payload

    def test_campaign_legacy_vlan_wifi_cellular_and_old_backup(self):
        for index, (mode, wifi, cellular, sealed) in enumerate([
                ('legacy', False, False, True), ('vlan', False, False, True),
                ('vlan', True, False, True), ('vlan', True, True, True),
                ('legacy', True, False, False)]):
            with self.subTest(mode=mode, wifi=wifi, cellular=cellular, sealed=sealed):
                folder = self.root / str(index)
                folder.mkdir()
                workspace = folder / 'workspace'
                recovery.initialize(workspace)
                data, request = state(mode, wifi, cellular)
                if not sealed: del data[manifest.MODE]
                path = bundle(folder, data, sealed)
                before = path.read_bytes()
                plan, payload = recovery.build_plan(path, request)
                self.assertFalse(plan['activation'])
                self.assertIn('acl', plan['dependencies'])
                self.assertIn('pcs-uplink-manager.service', plan['required_services'])
                self.assertNotIn(SECRET, json.dumps(plan))
                self.assertEqual(recovery.stage(path, plan, workspace), 'staged-inactive')
                recovery.validate_generation(workspace / 'staged', plan, payload)
                self.assertEqual(recovery.stage(path, plan, workspace), 'unchanged')
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(len(list(workspace.glob('staged'))), 1)
                if not sealed:
                    mode_row = next(row for row in plan['files'] if row['path'] == manifest.MODE)
                    self.assertEqual(mode_row['origin'], 'explicit-request')

    def test_calibration_policy_and_credentials_preserved_exactly(self):
        path, plan, payload = self.prepare()
        recovery.stage(path, plan, self.workspace)
        staged = self.workspace / 'staged/rootfs'
        power = json.loads((staged / 'etc/pcs/power-monitor.json').read_bytes())
        self.assertEqual(power['monitors']['input']['shunt_ohms'], 0.003)
        self.assertEqual((staged / 'etc/pcs/switch-monitor.community').read_bytes(), (SECRET + '\n').encode())
        for name, content in payload.items():
            self.assertEqual((staged / name).read_bytes(), content)
        self.assertFalse((staged / 'etc/pcs/operator-unrelated.json').exists())
        self.assertTrue(any(row['path'] == 'etc/pcs/operator-unrelated.json' for row in plan['exclusions']))

    def test_unrelated_workspace_and_target_files_preserved(self):
        path, original, _ = self.prepare()
        target = self.root / 'offline-root'
        target.mkdir()
        (target / 'unrelated').write_bytes(b'keep')
        (self.workspace / 'operator-note').write_bytes(b'keep-too')
        plan, _ = recovery.build_plan(path, original['request'], target)
        recovery.stage(path, plan, self.workspace)
        self.assertEqual((target / 'unrelated').read_bytes(), b'keep')
        self.assertEqual(list(target.iterdir()), [target / 'unrelated'])
        self.assertEqual((self.workspace / 'operator-note').read_bytes(), b'keep-too')

    def test_conflicting_target_topology_rejected_before_stage(self):
        path, original, _ = self.prepare()
        target = self.root / 'offline-root'
        (target / 'etc/pcs').mkdir(parents=True)
        (target / manifest.MODE).write_text('legacy\n')
        plan, _ = recovery.build_plan(path, original['request'], target)
        self.assertIn(manifest.MODE, plan['conflicts'])
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            recovery.stage(path, plan, self.workspace)
        self.assertFalse((self.workspace / 'staged').exists())

    def test_duplicate_target_uuid_under_other_name_conflicts(self):
        path, original, payload = self.prepare()
        target = self.root / 'offline-root'
        (target / manifest.NM).mkdir(parents=True)
        name = next(name for name in payload if name.startswith(manifest.NM))
        (target / manifest.NM / 'other-name').write_bytes(payload[name])
        plan, _ = recovery.build_plan(path, original['request'], target)
        self.assertIn(manifest.NM + 'other-name', plan['conflicts'])

    def test_target_drift_and_plan_tampering_rejected(self):
        path, original, _ = self.prepare()
        target = self.root / 'offline-root'
        target.mkdir()
        plan, _ = recovery.build_plan(path, original['request'], target)
        (target / 'etc/pcs').mkdir(parents=True)
        (target / manifest.MODE).write_text('legacy\n')
        with self.assertRaises(ValueError): recovery.stage(path, plan, self.workspace)
        edited = copy.deepcopy(original)
        edited['files'][0]['install_mode'] = '0777'
        with self.assertRaises(ValueError): recovery.stage(path, edited, self.workspace)

    def test_unknown_or_incomplete_topology_never_guessed(self):
        data, request = state()
        path = archive(self.root / 'raw.tar.gz', data)
        for field, value in [('mode', 'unknown'), ('lan_uuid', None), ('parent_uuid', 'missing')]:
            invalid = copy.deepcopy(request)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                recovery.build_plan(path, invalid)
        del data[next(name for name in data if 'pcs-vlan-parent' in name)]
        with self.assertRaises(ValueError):
            recovery.build_plan(archive(self.root / 'missing.tar.gz', data), request)

    def test_bad_vlan_parent_id_interface_and_ip_rejected(self):
        for old, new in [(b'parent=eth0', b'parent=eth9'), (b'id=20\n', b'id=10\n'),
                         (b'interface-name=eth0.20', b'interface-name=eth9.20')]:
            data, request = state()
            name = manifest.NM + 'pcs-starlink-vlan.nmconnection'
            data[name] = data[name].replace(old, new)
            with self.subTest(change=new), self.assertRaises(ValueError):
                recovery.build_plan(archive(self.root / 'bad.tar.gz', data), request)
        data, request = state()
        name = manifest.NM + 'pcs-vlan-parent.nmconnection'
        data[name] = data[name].replace(b'method=disabled', b'method=auto')
        with self.assertRaises(ValueError):
            recovery.build_plan(archive(self.root / 'ip.tar.gz', data), request)

    def test_corrupt_policies_and_missing_dependencies_rejected(self):
        cases = [('etc/pcs/uplinks.json', b'{}'), ('etc/pcs/bandwidth.yaml', b'bad: ['),
                 ('etc/pcs/power-monitor.json', b'{"version":1,"monitors":{}}'),
                 ('etc/pcs/switch-monitor.community', None),
                 ('etc/pcs/uplink-management.json', b'{"version":1,"trusted":[{"uplink":"absent","sources":["0.0.0.0/0"]}]}'),
                 ('etc/pcs/switch-monitor.json', b'{"version":1,"version":1}'),
                 ('etc/pcs/power-monitor.json', b'{"version":1,"threshold":NaN}')]
        for name, content in cases:
            data, request = state()
            if content is None: del data[name]
            else: data[name] = content
            with self.subTest(path=name), self.assertRaises(ValueError):
                recovery.build_plan(archive(self.root / 'bad.tar.gz', data), request)

    def test_missing_cellular_profile_rejected(self):
        data, request = state(wifi=True, cellular=True)
        del data[manifest.NM + 'cellular.nmconnection']
        with self.assertRaises(ValueError):
            recovery.build_plan(archive(self.root / 'missing.tar.gz', data), request)

    def test_wireguard_and_saved_display_settings_staged_without_execution(self):
        data, request = state(wifi=True)
        data['etc/pcs/wireguard-management.conf'] = b'PCS_WG_ALLOWED_IPS="10.6.0.1/32"\nPCS_WG_ADMIN_SOURCES="10.6.0.1/32"\nPCS_WG_USE_PRESHARED_KEY=yes\n'
        data['etc/pcs/wireguard/private.key'] = b'synthetic-private-key\n'
        data['etc/pcs/wireguard/preshared.key'] = b'synthetic-preshared-key\n'
        data[recovery.INSTALL_CONFIG] = b'PCS_SETUP_GPIO_LCD=yes\nPCS_SETUP_POWER_MONITOR=yes\n'
        path = bundle(self.root, data)
        plan, payload = recovery.build_plan(path, request)
        self.assertIn('wireguard-tools', plan['dependencies'])
        self.assertIn(recovery.INSTALL_CONFIG, payload)
        self.assertEqual(payload['etc/pcs/wireguard/private.key'], b'synthetic-private-key\n')
        recovery.stage(path, plan, self.workspace)
        del data['etc/pcs/wireguard/preshared.key']
        with self.assertRaises(ValueError):
            recovery.build_plan(archive(self.root / 'partial-wg.tar.gz', data), request)

    def test_staging_write_failure_cleans_only_its_pending_artifacts(self):
        path, plan, _ = self.prepare()
        (self.workspace / 'keep').write_text('keep')
        original = recovery.write_private
        def fail(target, data):
            if target.name == 'plan.json': raise OSError('synthetic disk full')
            original(target, data)
        with patch.object(recovery, 'write_private', side_effect=fail), self.assertRaises(OSError):
            recovery.stage(path, plan, self.workspace)
        self.assertFalse((self.workspace / 'staged').exists())
        self.assertFalse(list(self.workspace.glob('.pending-*')))
        self.assertFalse((self.workspace / '.lock').exists())
        self.assertEqual((self.workspace / 'keep').read_text(), 'keep')
        self.assertEqual(recovery.stage(path, plan, self.workspace), 'staged-inactive')

    def test_rollback_retains_evidence_and_allows_clean_restage(self):
        path, plan, payload = self.prepare()
        recovery.stage(path, plan, self.workspace)
        name = recovery.rollback(self.workspace)
        self.assertFalse((self.workspace / 'staged').exists())
        recovery.validate_generation(self.workspace / name, plan, payload)
        self.assertEqual(recovery.stage(path, plan, self.workspace), 'staged-inactive')

    def test_stage_tampering_and_unexpected_files_rejected(self):
        path, plan, payload = self.prepare()
        recovery.stage(path, plan, self.workspace)
        extra = self.workspace / 'staged/rootfs/extra'
        recovery.write_private(extra, b'extra')
        with self.assertRaises(ValueError): recovery.stage(path, plan, self.workspace)
        extra.unlink()
        file = self.workspace / 'staged/rootfs' / manifest.MODE
        file.write_bytes(b'legacy\n')
        with self.assertRaises(ValueError): recovery.validate_generation(self.workspace / 'staged', plan, payload)

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership/mode and symlink tests')
    def test_permissions_hardlinks_and_symlinks_fail_closed(self):
        path, plan, payload = self.prepare()
        recovery.stage(path, plan, self.workspace)
        file = self.workspace / 'staged/rootfs' / manifest.MODE
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        file.chmod(0o644)
        with self.assertRaises(ValueError): recovery.validate_generation(self.workspace / 'staged', plan, payload)
        file.chmod(0o600)
        os.link(file, self.root / 'hardlink')
        with self.assertRaises(ValueError): recovery.validate_generation(self.workspace / 'staged', plan, payload)
        (self.root / 'hardlink').unlink()
        file.unlink()
        file.symlink_to(self.root / 'outside')
        with self.assertRaises(ValueError): recovery.validate_generation(self.workspace / 'staged', plan, payload)
        self.workspace.chmod(0o755)
        with self.assertRaises(ValueError): recovery.check_workspace(self.workspace)

    def test_cli_plan_dry_run_stage_validate_and_safe_error(self):
        path, plan, _ = self.prepare()
        request = self.root / 'request.json'
        request.write_bytes(manifest.json_bytes(plan['request']))
        output = self.root / 'plan.json'
        cli = [sys.executable, str(ROOT / 'scripts/pcs_recovery_stage.py')]
        def call(*args):
            return subprocess.run(cli + list(map(str, args)), capture_output=True, text=True, timeout=30)
        self.assertEqual(call('plan', path, '--request', request, '--output', output).returncode, 0)
        self.assertEqual(call('stage', path, output, self.workspace, '--dry-run').returncode, 0)
        self.assertFalse((self.workspace / 'staged').exists())
        self.assertEqual(call('stage', path, output, self.workspace).returncode, 0)
        self.assertEqual(call('validate', path, output, self.workspace).returncode, 0)
        output.write_text('{"bad":true}')
        result = call('stage', path, output, self.workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
