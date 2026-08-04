import unittest

from app.html_text import clean_html_to_text as _clean_html_to_text


class EpubParserTests(unittest.TestCase):
    def test_nested_divs_do_not_duplicate_child_paragraphs(self):
        html = """
        <html><body>
          <div class="chapter">
            <h1>Chapter One</h1>
            <div class="body">
              <p>Ellie remembered her mother standing at the church door.</p>
              <p>Sarah stayed awake and listened to the rain.</p>
            </div>
          </div>
        </body></html>
        """
        text = _clean_html_to_text(html)
        self.assertEqual(text.count("Ellie remembered her mother"), 1)
        self.assertEqual(text.count("Sarah stayed awake"), 1)
        self.assertEqual(text.count("Chapter One"), 1)

    def test_inline_spans_are_kept_once(self):
        html = "<p>She <span>walked through</span> the garden.</p>"
        text = _clean_html_to_text(html)
        self.assertEqual(text.count("walked through"), 1)


if __name__ == "__main__":
    unittest.main()
