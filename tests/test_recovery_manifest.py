import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from recovery_fixtures import archive, bundle, state, manifest, SECRET


class RecoveryManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_sealed_inventory_is_verified_private_and_complete(self):
        data, _ = state(wifi=True, cellular=True)
        path = bundle(self.root, data)
        result, payload = manifest.inspect(path, manifest.file_digest(path))
        self.assertEqual(result['manifest_status'], 'verified')
        self.assertEqual(set(data), {row['path'] for row in result['inventory']['files']})
        self.assertEqual(len(result['inventory']['profiles']), 5)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('synthetic-machine', json.dumps(result))
        self.assertEqual(payload['etc/pcs/switch-monitor.community'], data['etc/pcs/switch-monitor.community'])
        self.assertIn('acl', result['inventory']['dependencies'])
        self.assertEqual(result['manifest']['source']['commit'], 'a' * 40)

    def test_old_archive_is_readable_without_invented_identity(self):
        data, _ = state('legacy')
        del data[manifest.MODE]
        result, _ = manifest.inspect(bundle(self.root, data, sealed=False))
        self.assertIsNone(result['manifest'])
        self.assertEqual(result['manifest_status'], 'absent-legacy')
        self.assertEqual(result['inventory']['topology']['mode'], 'unknown')

    def test_inspection_does_not_modify_archive(self):
        data, _ = state()
        path = bundle(self.root, data)
        before = path.read_bytes()
        manifest.inspect(path)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual({p.name for p in self.root.iterdir()}, {'old.tar.gz', 'new.tar.gz'})

    def test_payload_corruption_or_omission_rejected(self):
        data, _ = state()
        _, payload = manifest.inspect(bundle(self.root, data))
        for action in ('change', 'omit', 'extra'):
            changed = dict(payload)
            if action == 'change': changed['etc/pcs/power-monitor.json'] = b'{}'
            elif action == 'omit': del changed['etc/pcs/uplinks.json']
            else: changed['etc/pcs/extra'] = b'extra'
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'match archive'):
                manifest.inspect(archive(self.root / (action + '.tar.gz'), changed))

    def test_bad_manifest_versions_metadata_and_duplicate_keys_rejected(self):
        data, _ = state()
        _, payload = manifest.inspect(bundle(self.root, data))
        original = json.loads(payload[manifest.MANIFEST])
        for key, value in [('schema', 2), ('schema', True), ('created_utc', 'invalid'), ('source', {})]:
            invalid = dict(original)
            invalid[key] = value
            payload[manifest.MANIFEST] = manifest.json_bytes(invalid)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                manifest.inspect(archive(self.root / 'invalid.tar.gz', payload))
        with self.assertRaises(ValueError):
            manifest.parse_json(b'{"schema":1,"schema":1}')

    def test_expected_external_digest_is_independent(self):
        data, _ = state()
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            manifest.inspect(bundle(self.root, data), '0' * 64)

    def test_invalid_profile_captured_but_not_claimed_valid(self):
        data, _ = state()
        data[manifest.NM + 'bad'] = b'password=DO-NOT-ECHO'
        result, _ = manifest.inspect(bundle(self.root, data))
        self.assertEqual(result['inventory']['invalid_profiles'], [manifest.NM + 'bad'])
        self.assertNotIn('DO-NOT-ECHO', json.dumps(result))

    def test_failed_seal_removes_incomplete_new_output(self):
        data, _ = state()
        source = archive(self.root / 'raw.tar.gz', data)
        output = self.root / 'bad.tar.gz'
        with self.assertRaises(ValueError):
            manifest.seal(source, output, 'bad-commit', 'v2.2.0')
        self.assertFalse(output.exists())
        output.write_bytes(b'keep-existing')
        with self.assertRaises(FileExistsError):
            manifest.seal(source, output, 'a' * 40, 'v2.2.0')
        self.assertEqual(output.read_bytes(), b'keep-existing')

    def test_preserves_original_tar_metadata(self):
        source = self.root / 'source.tar.gz'
        with tarfile.open(source, 'w:gz', format=tarfile.PAX_FORMAT) as stream:
            directory = tarfile.TarInfo('etc/pcs')
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o750
            stream.addfile(directory)
            info = tarfile.TarInfo('etc/pcs/test')
            info.size, info.uid, info.gid, info.mode = 4, 123, 456, 0o640
            info.pax_headers = {'SCHILY.xattr.user.synthetic': 'retained'}
            stream.addfile(info, io.BytesIO(b'test'))
        output = self.root / 'output.tar.gz'
        manifest.seal(source, output, 'a' * 40, 'v2.2.0')
        with tarfile.open(output) as stream:
            info = stream.getmember('etc/pcs/test')
            self.assertEqual((info.uid, info.gid, info.mode), (123, 456, 0o640))
            self.assertEqual(info.pax_headers['SCHILY.xattr.user.synthetic'], 'retained')
            self.assertEqual(stream.getmember('etc/pcs').mode, 0o750)


if __name__ == '__main__':
    unittest.main()
