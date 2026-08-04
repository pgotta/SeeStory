import unittest

from app.director import StyleBible, build_prompt, direct
from app.timeline import Shot


class DirectorTests(unittest.TestCase):
    def shot(self, text: str) -> Shot:
        return Shot(
            id="c0s0", chapter_index=0, chapter_title="Chapter 1",
            shot_in_chapter=0, page_start=0, page_end=0, text=text,
            start_ms=0, end_ms=10_000,
        )

    def test_human_scene_gets_anatomy_and_coherence_guidance(self):
        prompt = build_prompt(
            self.shot('She stepped through the rain into the lantern-lit street.'),
            StyleBible("cinematic"),
        ).lower()
        self.assertIn("five fingers", prompt)
        self.assertIn("two arms and two legs", prompt)
        self.assertIn("single coherent scene", prompt)

    def test_prompt_scrubs_text_inducing_genre_words(self):
        prompt = build_prompt(
            self.shot('The detective crossed the shadowy city street beneath the rain.'),
            StyleBible("cinematic", "thriller book cover, cinematic painting"),
        ).lower()
        self.assertNotIn("book cover", prompt)
        self.assertNotIn("thriller", prompt)
        self.assertIn("cinematic painting", prompt)

    def test_direct_avoids_reusing_same_focus_when_alternative_exists(self):
        text = (
            "The girl stood beside the rain-streaked window watching the storm. "
            "Her mother crossed the candlelit room and opened the old wooden door. "
            "A pale dawn spread over the quiet garden outside."
        )
        a = self.shot(text)
        b = self.shot(text)
        b.id = "c0s1"
        b.shot_in_chapter = 1
        b.start_ms, b.end_ms = 10_000, 20_000
        direct([a, b], StyleBible("cinematic"))
        self.assertNotEqual(a.prompt, b.prompt)



if __name__ == "__main__":
    unittest.main()
