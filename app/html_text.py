"""Shared HTML/EPUB text extraction helpers.

The key rule is that every DOM text node must be read exactly once.  Reading a
wrapper <div> and each nested <p> separately duplicates prose in many EPUBs.
"""
from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup

_BLOCK_TAGS = (
    "p", "div", "section", "article", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "li",
)
_SEMANTIC_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre")
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}


def make_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html or "", "lxml")
    for bad in soup(["script", "style", "nav"]):
        bad.decompose()
    return soup


def clean_html_to_text(html: str) -> str:
    """Return readable text in DOM order without duplicating nested containers."""
    soup = make_soup(html)

    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Insert boundaries, then do a single text traversal. Nested blocks can add
    # whitespace but cannot cause the underlying prose to be read twice.
    for el in soup.find_all(_BLOCK_TAGS):
        el.append("\n\n")

    text = soup.get_text(" ", strip=False).replace("\r", "")
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def semantic_blocks(html_or_soup) -> list[tuple[str, bool]]:
    """Return (text, is_heading) blocks without wrapper-container duplication."""
    soup = html_or_soup if isinstance(html_or_soup, BeautifulSoup) else make_soup(html_or_soup)
    blocks: list[tuple[str, bool]] = []
    for el in soup.find_all(list(_SEMANTIC_TAGS) + ["div"]):
        # A div is useful only when it is a leaf-like text container. If it owns
        # paragraphs/headings/list items, those semantic children carry the text.
        if el.name == "div" and el.find(_SEMANTIC_TAGS):
            continue
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not text:
            continue
        if blocks and text == blocks[-1][0]:
            continue
        blocks.append((text, el.name in _HEADING_TAGS))
    return blocks
