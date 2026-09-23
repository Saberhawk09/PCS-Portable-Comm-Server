import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UsbPrimaryStorageTests(unittest.TestCase):
    def test_usb_detection_preserves_empty_lsblk_columns(self):
        for relative in ("scripts/pcs-web-action.sh", "scripts/setup-pcs-base.sh"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("while read -r name; do", source)
            self.assertIn('fstype="$(lsblk -dnro FSTYPE "${name}"', source)
            self.assertIn('tran="$(lsblk -dnro TRAN "${name}"', source)
            self.assertIn("done < <(lsblk -rpno NAME", source)
            self.assertNotIn("while read -r name type fstype pkname", source)

    def test_replacement_removes_legacy_fstab_mount_entry(self):
        source = (ROOT / "scripts/setup-usb-primary-share.sh").read_text(encoding="utf-8")
        self.assertIn(
            '${SUDO} sed -i "\\|[[:space:]]${USB_MOUNT}[[:space:]]|d" /etc/fstab',
            source,
        )


if __name__ == "__main__":
    unittest.main()
