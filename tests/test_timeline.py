import unittest

from app.timeline import parse_youtube_timestamps, segment_book


class _Chapter:
    def __init__(self, title, text):
        self.title = title
        self.text = text


class TimelineTests(unittest.TestCase):
    def test_timestamp_parser_accepts_mmss_and_hmmss(self):
        got = parse_youtube_timestamps("00:00 One\n1:02:03 Two\nnot a chapter")
        self.assertEqual(got, [("One", 0), ("Two", 3_723_000)])

    def test_segment_book_keeps_continuous_final_span(self):
        chapters = [_Chapter("One", "word " * 600)]
        shots = segment_book(chapters, [(0, 60_000)], words_per_page=200, pages_per_shot=1)
        self.assertEqual(shots[0].start_ms, 0)
        self.assertEqual(shots[-1].end_ms, 60_000)
        self.assertTrue(all(a.end_ms == b.start_ms for a, b in zip(shots, shots[1:])))


if __name__ == "__main__":
    unittest.main()
