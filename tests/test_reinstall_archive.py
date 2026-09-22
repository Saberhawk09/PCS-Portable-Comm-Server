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


if __name__ == "__main__":
    unittest.main()
