"""Synthetic coverage boundaries, not a claim of current-topology recovery."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


archive = module('pcs_reinstall_archive')
restore = module('pcs_reinstall_restore')

# Deliberately synthetic. These settings must not activate network components
# simply because a broad /etc/pcs backup contains them. A later reviewed recovery
# implementation should replace this boundary with its explicit staged contract.
DEFERRED = {
    'etc/pcs/network-mode': 'vlan\n',
    'etc/pcs/uplinks.json': '{"version":1,"uplinks":[]}\n',
    'etc/pcs/uplink-management.json': '{"version":1,"trusted":[]}\n',
    'etc/pcs/bandwidth.yaml': 'version: 1\n',
    'etc/pcs/switch-monitor.json': '{"enabled":false,"mapping":null}\n',
    'etc/pcs/switch-monitor.community': 'synthetic-test-community\n',
    'etc/pcs/power-monitor.json': '{"allow_shutdown":false}\n',
    'etc/NetworkManager/system-connections/test-lan.nmconnection': '[connection]\ntype=vlan\n',
    'etc/NetworkManager/system-connections/test-wan.nmconnection': '[connection]\ntype=802-3-ethernet\n',
}


class RecoveryCoverageTests(unittest.TestCase):
    def test_modern_settings_are_archivable_without_implying_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.tar.gz'
            with tarfile.open(path, 'w:gz') as stream:
                for name, value in DEFERRED.items():
                    data = value.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    stream.addfile(info, io.BytesIO(data))
            self.assertEqual({name for _, name in archive.members(path)}, set(DEFERRED))

    def test_planning_does_not_select_deferred_state_or_modify_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, value in DEFERRED.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding='utf-8')
            wifi = 'etc/NetworkManager/system-connections/test-wifi.nmconnection'
            (root / wifi).write_text('[connection]\ntype=wifi\n', encoding='utf-8')
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            selected = set()
            for component in ('network', 'private', 'api'):
                selected.update(row[0] for row in restore.plan_restore(root, component))
            self.assertEqual(selected, {wifi})
            self.assertTrue(set(DEFERRED).isdisjoint(selected))
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_network_only_archive_is_not_an_exact_commissioned_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, value in DEFERRED.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding='utf-8')
            with self.assertRaises(ValueError):
                restore.plan_restore(root, 'exact')


if __name__ == '__main__':
    unittest.main()
