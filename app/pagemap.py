"""
Detect embedded page numbers in an EPUB.

Reflowable EPUBs usually have no fixed pages, but many — especially those made
from a print edition — embed page-break markers:

  * EPUB3:  <span epub:type="pagebreak" .../>, role="doc-pagebreak", or a
            <nav epub:type="page-list"> listing every page in the navigation doc.
  * EPUB2:  anchors like <a id="page_12"/> or id="page12".

When enough are present we treat the book as paginated and report the count, so
SeeStory can size its "pages" to match real pages instead of guessing words.
Detection is conservative (we ignore ambiguous ids like p1/p2 that are usually
paragraph anchors) and never raises — on any problem it just reports no pages.
"""

import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub
import ebooklib

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Only count clearly page-numbered anchors (page_/page-/page12), never bare p\d.
_PAGE_ANCHOR = re.compile(r'\b(?:id|name)\s*=\s*["\']page[-_]?\d{1,5}["\']', re.I)


def _count_markers(html: str) -> int:
    low = html.lower()
    explicit = (low.count('epub:type="pagebreak"') + low.count("epub:type='pagebreak'")
                + low.count('role="doc-pagebreak"') + low.count("role='doc-pagebreak'"))
    if explicit:
        return explicit
    return len(_PAGE_ANCHOR.findall(low))


def detect_pages(epub_path: str) -> dict:
    """Return {'has_pages': bool, 'page_count': int, 'source': str}."""
    none = {"has_pages": False, "page_count": 0, "source": ""}
    if not (epub_path or "").lower().endswith(".epub"):
        return dict(none)
    try:
        book = epub.read_epub(epub_path)
    except Exception:
        return dict(none)

    # 1) A page-list nav is the most authoritative source when present.
    try:
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            html = item.get_content().decode("utf-8", "ignore")
            if "page-list" not in html.lower():
                continue
            soup = BeautifulSoup(html, "lxml")
            nav = (soup.find("nav", attrs={"epub:type": "page-list"})
                   or soup.find(attrs={"epub:type": "page-list"}))
            if nav:
                links = nav.find_all("a")
                if len(links) >= 3:
                    return {"has_pages": True, "page_count": len(links),
                            "source": "page-list"}
    except Exception:
        pass

    # 2) Otherwise tally inline page-break markers across all content docs.
    total = 0
    try:
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            html = item.get_content().decode("utf-8", "ignore")
            total += _count_markers(html)
    except Exception:
        pass

    if total >= 3:
        return {"has_pages": True, "page_count": total, "source": "markers"}
    return dict(none)
