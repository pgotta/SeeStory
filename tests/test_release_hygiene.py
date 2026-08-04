import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseHygieneTests(unittest.TestCase):
    def test_retired_cloud_provider_name_is_absent(self):
        forbidden = ("co" + "pilot").lower()
        checked_suffixes = {".py", ".pyw", ".js", ".css", ".html", ".md", ".txt"}
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in checked_suffixes:
                continue
            if any(part in {"venv", ".git", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if forbidden in text:
                hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])

    def test_flask_ui_has_no_source_selector_or_readiness_strip(self):
        html = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")
        js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        self.assertNotIn("mode-cards", html)
        self.assertNotIn("class=\"badges\"", html)
        self.assertNotIn("data-seg=\"backend\"", js)
        self.assertNotIn("sm-proc", html + js)

    def test_bat_files_remain_gitignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.bat", ignore)

    def test_build_md_mirrors_packaged_bat_files(self):
        import re
        build = (ROOT / "BUILD.md").read_text(encoding="utf-8")
        for name in (
            "install_all.bat", "setup.bat", "run.bat", "stop.bat",
            "shutdown_diagnostic.bat", "check_gpu.bat",
            "install_stable_diffusion.bat",
        ):
            match = re.search(
                rf"### `{re.escape(name)}`\n\n```bat\n(.*?)\n```",
                build, flags=re.S,
            )
            self.assertIsNotNone(match, name)
            bat_path = ROOT / name
            if bat_path.exists():
                actual = bat_path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
                self.assertEqual(match.group(1).replace("\r\n", "\n").rstrip(), actual, name)


if __name__ == "__main__":
    unittest.main()
