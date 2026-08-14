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


if __name__ == "__main__":
    unittest.main()
