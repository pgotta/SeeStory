import tempfile
import unittest
from pathlib import Path

from app.validation import clean_motion, safe_session_dir


DEFAULT_MOTION = {
    "zoom": "in", "pan": "none", "intensity": 35, "speed": 50,
    "fade_in": 0.6, "fade_out": 0.6, "opacity": 100,
}


class ValidationTests(unittest.TestCase):
    def test_motion_is_clamped_and_whitelisted(self):
        motion = clean_motion({
            "zoom": "sideways", "pan": "left", "intensity": 999,
            "speed": -10, "fade_in": 99, "opacity": 1,
        }, DEFAULT_MOTION)
        self.assertEqual(motion["zoom"], DEFAULT_MOTION["zoom"])
        self.assertEqual(motion["pan"], "left")
        self.assertEqual(motion["intensity"], 100)
        self.assertEqual(motion["speed"], 0)
        self.assertEqual(motion["fade_in"], 2.5)
        self.assertEqual(motion["opacity"], 20)

    def test_session_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "good-session").mkdir()
            self.assertEqual(safe_session_dir(td, "good-session"), str(Path(td, "good-session")))
            self.assertIsNone(safe_session_dir(td, "../good-session"))
            self.assertIsNone(safe_session_dir(td, "_sample"))


if __name__ == "__main__":
    unittest.main()
