import unittest
from pathlib import Path


class LauncherModelDownloadTests(unittest.TestCase):
    def test_runtime_is_forced_offline(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "launch_seestory.pyw").read_text(encoding="utf-8")
        self.assertIn('"HF_HUB_OFFLINE": "1"', text)
        self.assertIn('"TRANSFORMERS_OFFLINE": "1"', text)
        self.assertNotIn('"HF_HUB_DOWNLOAD_TIMEOUT": "300"', text)

    def test_installer_owns_model_download_and_gpu_smoke_test(self):
        root = Path(__file__).resolve().parents[1]
        bat_path = root / "install_all.bat"
        # Packaged releases contain the runnable BAT. The public source repository
        # intentionally excludes BAT/CMD/LNK files, so CI validates the exact BAT
        # source embedded in BUILD.md instead.
        installer_source = (
            bat_path.read_text(encoding="utf-8")
            if bat_path.exists()
            else (root / "BUILD.md").read_text(encoding="utf-8")
        )
        helper = (root / "install_models.py").read_text(encoding="utf-8")
        self.assertIn('install_models.py" --model default', installer_source)
        self.assertIn('install_models.py" --model photoreal', installer_source)
        self.assertIn("install_and_verify_model", helper)
        self.assertIn("model-smoke-", helper)

    def test_ui_does_not_advertise_runtime_download(self):
        root = Path(__file__).resolve().parents[1]
        js = (root / "app/static/app.js").read_text(encoding="utf-8")
        html = (root / "app/templates/index.html").read_text(encoding="utf-8")
        self.assertNotIn("download about 7 GB", js)
        self.assertNotIn("first run downloads", html.lower())
        self.assertNotIn("first use downloads", html.lower())
        self.assertNotIn("about 7 gb", html.lower())
        self.assertIn("Generating sample", js)


if __name__ == "__main__":
    unittest.main()
