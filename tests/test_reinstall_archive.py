import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pcs_reinstall_archive", ROOT / "scripts/pcs_reinstall_archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ReinstallArchiveTests(unittest.TestCase):
    def make_archive(self, name, content=b"secret", kind="file"):
        temporary = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        temporary.close()
        path = Path(temporary.name)
        with tarfile.open(path, "w:gz") as stream:
            info = tarfile.TarInfo(name)
            if kind == "file":
                import io
                info.size = len(content)
                stream.addfile(info, io.BytesIO(content))
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                stream.addfile(info)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_accepts_allowlisted_private_state(self):
        path = self.make_archive("etc/pcs/meshtastic-mqtt.env")
        self.assertEqual(len(archive.members(path)), 1)

    def test_rejects_traversal_and_unrelated_paths(self):
        for name in ("../../etc/shadow", "/etc/pcs/secret", "etc/passwd"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    archive.members(self.make_archive(name))

    def test_rejects_links_even_under_allowlist(self):
        with self.assertRaisesRegex(ValueError, "links/devices"):
            archive.members(self.make_archive("etc/pcs/linked", kind="link"))

    def test_rejects_regular_file_used_as_allowlisted_ancestor(self):
        with self.assertRaisesRegex(ValueError, "ancestor"):
            archive.members(self.make_archive("etc"))

    def test_rejects_file_directory_collision_before_extraction(self):
        import io
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'collision.tar.gz'
            with tarfile.open(path, 'w:gz') as stream:
                for name in ('etc/pcs/parent', 'etc/pcs/parent/child'):
                    info = tarfile.TarInfo(name)
                    info.size = 1
                    stream.addfile(info, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'parent directory'):
                archive.members(path)

    def test_rejects_cross_platform_separator_and_control_characters(self):
        for name in ('etc/pcs/a\\b', 'etc/pcs/a\nb', 'etc/pcs/a:stream', 'etc/pcs/.. /escape', 'etc/pcs/CON'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                archive.members(self.make_archive(name))

    def test_extract_refuses_existing_content_before_writes(self):
        from unittest.mock import patch
        import types
        import stat
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            (destination / 'keep').write_bytes(b'keep')
            # Ownership is synthetic here; actual POSIX modes are tested in the
            # disposable recovery campaign. The content preflight is portable.
            real_stat = Path.stat
            def info(path, *args, **kwargs):
                if path == destination:
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
                return real_stat(path, *args, **kwargs)
            source = self.make_archive('etc/pcs/test')
            with patch.object(Path, 'stat', info), self.assertRaisesRegex(ValueError, 'empty'):
                archive.extract(source, destination)
            self.assertEqual((destination / 'keep').read_bytes(), b'keep')


if __name__ == "__main__":
    unittest.main()
