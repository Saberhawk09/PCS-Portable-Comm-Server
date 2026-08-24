import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class DocumentationTests(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        markdown_files = [
            path
            for path in ROOT.rglob("*.md")
            if ".git" not in path.parts
        ]
        for source in markdown_files:
            content = source.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(content):
                target = match.group(1).strip().strip("<>")
                if not target or target.startswith("#") or "://" in target or target.startswith("mailto:"):
                    continue
                relative_path = unquote(target.split("#", 1)[0])
                if not relative_path:
                    continue
                resolved = (source.parent / relative_path).resolve()
                with self.subTest(source=source.relative_to(ROOT), target=target):
                    self.assertTrue(resolved.exists(), f"Broken link in {source.relative_to(ROOT)}: {target}")

    def test_current_status_docs_match_commissioned_meshtastic_path(self):
        current_docs = [
            ROOT / "README.md",
            ROOT / "docs" / "README.md",
            ROOT / "docs" / "project-overview.md",
            ROOT / "docs" / "meshtastic-bluetooth-gateway.md",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in current_docs)

        self.assertIn("30 minutes", combined)
        self.assertIn("IJC2", combined)
        self.assertIn("RF-to-map", combined)
        self.assertNotIn("Public-map frontend appearance", combined)
        self.assertNotIn("complete and validate the RAK4631 BLE pairing", combined)

    def test_current_network_names_do_not_restore_superseded_router(self):
        names = (ROOT / "config" / "local-client-names.example.tsv").read_text(encoding="utf-8")
        status = (ROOT / "scripts" / "pcs-status.sh").read_text(encoding="utf-8")

        self.assertIn("Linksys EA4500 OpenWrt AP", names)
        self.assertNotIn("Actiontec", names)
        self.assertNotIn("Future PCS path", status)
        self.assertNotIn("Future EM7565 modem validation", status)
        self.assertIn("/usr/sbin/i2cdetect", status)

    def test_ci_compiles_all_python_and_checks_all_shell_modes(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("python -m compileall -q scripts tests web", workflow)
        self.assertIn('for script in scripts/*.sh; do', workflow)


if __name__ == "__main__":
    unittest.main()
