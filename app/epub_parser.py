"""
EPUB parsing: pull readable text out of an .epub and split it into chapters.

We do two things:
  1. Extract each document in spine order and clean it to plain text.
  2. Try to detect chapter boundaries so the user gets a sensible starting
     point. They can always edit, merge, split, or rename afterwards.
"""

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub
import ebooklib

from .html_text import clean_html_to_text as _clean_html_to_text

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@dataclass
class Chapter:
    title: str
    text: str
    # order is assigned by the caller after any manual edits
    order: int = 0


@dataclass
class ParsedBook:
    title: str
    author: str
    chapters: list = field(default_factory=list)


# Headings we treat as a likely chapter start when auto-detecting.
_HEADING_TAGS = ("h1", "h2", "h3")


def _ordered_documents(book):
    """Yield the book's document items in spine (reading) order.

    ebooklib's get_items_of_type() returns items in *manifest* order, which
    often interleaves front/back matter oddly and isn't the order a person sees
    in an e-reader. The spine is the actual reading order, so we follow it and
    only fall back to manifest order for the rare document not listed in the
    spine. This makes the chapter list deterministic and sensibly ordered.
    """
    seen = set()
    spine = getattr(book, "spine", None) or []
    for entry in spine:
        idref = entry[0] if isinstance(entry, (tuple, list)) else entry
        if not idref:
            continue
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
    # Any document the spine didn't reference (uncommon) — keep it, in
    # manifest order, so nothing is silently dropped.
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if id(item) not in seen:
            seen.add(id(item))
            yield item


def _guess_title(html: str, fallback: str) -> str:
    """Use the first heading as a chapter title if there is one."""
    soup = BeautifulSoup(html, "lxml")
    for tag in _HEADING_TAGS:
        h = soup.find(tag)
        if h:
            t = h.get_text(" ", strip=True)
            if t:
                return t[:120]
    return fallback


def parse_epub(path: str) -> ParsedBook:
    """Read an epub and return one Chapter per spine document.

    Each spine item usually corresponds to one chapter in well-made epubs.
    For messy epubs the user can re-split in the UI.
    """
    book = epub.read_epub(path)

    title = "Untitled"
    author = "Unknown"
    if book.get_metadata("DC", "title"):
        title = book.get_metadata("DC", "title")[0][0]
    if book.get_metadata("DC", "creator"):
        author = book.get_metadata("DC", "creator")[0][0]

    chapters = []
    index = 0
    for item in _ordered_documents(book):
        # Skip the navigation document (epub3 nav / ncx toc).
        if getattr(item, "is_chapter", lambda: True)() is False:
            continue
        name = (item.get_name() or "").lower()
        if "nav" in name or "toc" in name or "contents" in name:
            continue

        html = item.get_content().decode("utf-8", errors="ignore")
        # A nav doc is mostly an <ol>/<nav> of links — detect and skip.
        low = html.lower()
        if "<nav" in low or 'epub:type="toc"' in low:
            continue

        text = _clean_html_to_text(html)

        # Skip near-empty documents (covers, blank pages, nav).
        if len(text) < 50:
            continue

        index += 1
        guessed = _guess_title(html, f"Chapter {index}")
        chapters.append(Chapter(title=guessed, text=text, order=index))

    # Re-number cleanly in case some items were skipped.
    for i, ch in enumerate(chapters, start=1):
        ch.order = i

    return ParsedBook(title=title, author=author, chapters=chapters)
