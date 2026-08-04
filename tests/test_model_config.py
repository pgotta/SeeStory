import unittest

from app.imagegen import stablediffusion as sd


class ModelConfigTests(unittest.TestCase):
    def test_default_is_diffusers_safetensors_layout(self):
        self.assertEqual(sd.DEFAULT_MODEL, "Lykon/dreamshaper-xl-lightning")
        self.assertEqual(sd.ARTISTIC_MODEL, sd.DEFAULT_MODEL)
        self.assertIn("RealVisXL_V5.0_Lightning", sd.PHOTOREAL_MODEL)
        self.assertNotIn("Juggernaut", sd.DEFAULT_MODEL)

    def test_lightning_generation_defaults(self):
        self.assertEqual(sd._generation_defaults(sd.DEFAULT_MODEL), (4, 2.0))
        self.assertEqual(sd._generation_defaults(sd.ARTISTIC_MODEL), (4, 2.0))
        self.assertEqual(sd._generation_defaults(sd.PHOTOREAL_MODEL), (5, 1.8))

    def test_negative_prompt_targets_common_anatomy_failures(self):
        negative = sd.DEFAULT_NEGATIVE.lower()
        for phrase in (
            "extra fingers", "extra arms", "extra legs", "duplicate person",
            "malformed hands",
        ):
            self.assertIn(phrase, negative)

    def test_runtime_loader_is_cache_only_and_installer_can_download(self):
        import inspect
        source = inspect.getsource(sd._from_pretrained)
        self.assertIn("allow_download: bool = False", source)
        self.assertIn("(True, False) if allow_download else (True,)", source)
        self.assertIn("low_cpu_mem_usage", inspect.getsource(sd._load))

    def test_installer_runs_real_cuda_smoke_inference(self):
        import inspect
        source = inspect.getsource(sd.install_and_verify_model)
        self.assertIn("torch.cuda.reset_peak_memory_stats", source)
        self.assertIn("torch.cuda.max_memory_allocated", source)
        self.assertIn("result = pipe(", source)


if __name__ == "__main__":
    unittest.main()
