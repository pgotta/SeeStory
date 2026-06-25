"""
Turn Parroty's outputs into a synced visual timeline.

The contract with Parroty is deliberately small and file-based:

  * the same ebook  -> parsed with the SAME parser, so chapters are identical
  * the combined MP3 -> gives the total runtime
  * youtube-chapters-*.txt -> "MM:SS Title" lines = each chapter's start time

From those three we know every chapter's [start_ms, end_ms] in the audio.
Inside a chapter we split the text into "pages" and group pages into "shots"
(one image/clip each). A shot's on-screen time is its share of the chapter's
duration, weighted by how much text it covers — because Parroty narrates at a
near-constant characters-per-second, text length is a good proxy for time, so
the picture changes roughly when the narration reaches that part of the page.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# ── audio + timestamp parsing ────────────────────────────────────────────

_TS_LINE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s+(.*\S)\s*$")


def parse_youtube_timestamps(text: str) -> List[tuple]:
    """Parse Parroty's 'H:MM:SS Title' / 'MM:SS Title' lines.

    Returns [(title, start_ms), ...] in file order. Lines that don't look like
    a timestamp are ignored, so pasting the whole description block is fine.
    """
    out = []
    for line in (text or "").splitlines():
        m = _TS_LINE.match(line)
        if not m:
            continue
        h, mm, ss = m.group(1), m.group(2), m.group(3)
        start_ms = ((int(h or 0) * 3600) + int(mm) * 60 + int(ss)) * 1000
        out.append((m.group(4).strip(), start_ms))
    return out


def audio_duration_ms(path: str) -> int:
    """Duration of an audio/video file in ms via ffprobe (no full decode)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return int(float(r.stdout.strip()) * 1000)
    except Exception:
        pass
    return 0


# ── aligning ebook chapters to the audio's chapter list ──────────────────

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_NAMED = ("prologue", "epilogue", "introduction", "preface", "foreword",
          "afterword", "appendix", "interlude")


def _title_key(s: str):
    """Canonical key for matching titles across the ebook and the timestamps.

    'Chapter 7' / 'CHAPTER 7' / 'Chapter Seven' -> ('n', 7)
    'Prologue' / 'PROLOGUE'                      -> ('prologue',)
    anything else                                -> ('t', normalized text)
    """
    n = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    n = re.sub(r"\s+", " ", n).strip()
    for w in _NAMED:
        if n.startswith(w):
            return (w,)
    m = re.match(r"(?:chapter|chap|ch|part|book)\s*0*(\d+)", n)
    if m:
        return ("n", int(m.group(1)))
    m = re.match(r"(?:chapter|chap|ch|part|book)\s+([a-z]+)", n)
    if m and m.group(1) in _NUM_WORDS:
        return ("n", _NUM_WORDS[m.group(1)])
    if n.isdigit():
        return ("n", int(n))
    return ("t", n)


def align_chapters_to_markers(chapters: List, markers: List[tuple]) -> dict:
    """Match the ebook's chapters to the audio's chapter list (the markers).

    The audio + its YouTube timestamps are the source of truth for what's in the
    narration. The ebook often carries extra front/back matter (About the Book,
    Contents, epigraphs, acknowledgements) that was NOT narrated. We therefore:

      1. find where the audio's first chapter starts inside the ebook (skipping
         front matter), then
      2. walk the markers in order, taking the matching ebook chapter for each
         and skipping any unmatched ebook sections in between.

    Returns {'pairs': [ebook_chapter_or_None per marker], 'skipped': N,
             'start_title': str}.
    """
    n_mark = len(markers)
    if not chapters or not markers:
        return {"pairs": list(chapters[:n_mark]), "skipped": 0, "start_title": ""}

    ch_keys = [_title_key(getattr(c, "title", "")) for c in chapters]
    mk_keys = [_title_key(t) for (t, _) in markers]

    # 1) anchor: first ebook chapter that matches the first marker. If that
    #    title isn't found, try the next couple of markers to locate the anchor.
    start = None
    for j, k in enumerate(ch_keys):
        if k == mk_keys[0]:
            start = j
            break
    if start is None:
        for mi in range(1, min(4, len(mk_keys))):
            for j, k in enumerate(ch_keys):
                if k == mk_keys[mi]:
                    start = max(0, j - mi)
                    break
            if start is not None:
                break
    if start is None:
        start = 0  # no title anchor found — fall back to straight positional

    # 2) greedy walk: for each marker, take the matching ebook chapter (looking a
    #    few ahead to skip interstitials), else the next ebook chapter in order.
    pairs = []
    ci = start
    for mk in mk_keys:
        match = None
        for j in range(ci, min(len(chapters), ci + 8)):
            if ch_keys[j] == mk:
                match = j
                break
        if match is not None:
            pairs.append(chapters[match])
            ci = match + 1
        elif ci < len(chapters):
            pairs.append(chapters[ci])
            ci += 1
        else:
            pairs.append(None)
    return {"pairs": pairs, "skipped": start,
            "start_title": markers[0][0] if markers else ""}


# ── data model ───────────────────────────────────────────────────────────

@dataclass
class Shot:
    """One image/clip and where it lives in the book and the audio."""
    id: str
    chapter_index: int
    chapter_title: str
    shot_in_chapter: int          # 0-based shot number within its chapter
    page_start: int               # 0-based page range this shot covers
    page_end: int
    text: str                     # source text the visual is drawn from
    start_ms: int                 # position in the final audio timeline
    end_ms: int
    is_chapter_start: bool = False
    word_count: int = 0
    # filled in later by the director / image generator / UI
    prompt: str = ""
    backend: str = "stablediffusion"   # 'stablediffusion' | 'copilot' | 'placeholder'
    highlight_score: float = 0.0
    highlighted: bool = False          # routed to Copilot (high quality)
    image_path: Optional[str] = None
    status: str = "pending"            # pending | generating | done | error | skipped
    error: str = ""
    motion: dict = field(default_factory=dict)   # per-shot Ken Burns overrides

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def _split_pages(text: str, words_per_page: int) -> List[str]:
    """Split chapter text into ~equal word-count pages, keeping word order."""
    words = (text or "").split()
    if not words:
        return [""]
    pages, i = [], 0
    while i < len(words):
        pages.append(" ".join(words[i:i + words_per_page]))
        i += words_per_page
    return pages


def segment_book(chapters: List, spans: List[tuple], *,
                 words_per_page: int = 280, pages_per_shot: int = 1) -> List[Shot]:
    """Build the full shot list.

    chapters : list of objects with .title and .text (Parroty's Chapter)
    spans    : [(start_ms, end_ms), ...] aligned 1:1 with chapters
    """
    shots: List[Shot] = []
    for ci, ch in enumerate(chapters):
        c_start, c_end = spans[ci]
        c_dur = max(0, c_end - c_start)
        pages = _split_pages(getattr(ch, "text", "") or "", words_per_page)

        # group pages into shots
        groups = [pages[i:i + pages_per_shot]
                  for i in range(0, len(pages), pages_per_shot)] or [[""]]
        group_words = [max(1, sum(len(p.split()) for p in g)) for g in groups]
        total_words = sum(group_words)

        cursor = c_start
        for si, g in enumerate(groups):
            frac = group_words[si] / total_words
            dur = round(c_dur * frac)
            start = cursor
            end = c_end if si == len(groups) - 1 else min(c_end, cursor + dur)
            cursor = end
            shots.append(Shot(
                id=f"c{ci}s{si}",
                chapter_index=ci,
                chapter_title=getattr(ch, "title", f"Chapter {ci + 1}") or f"Chapter {ci + 1}",
                shot_in_chapter=si,
                page_start=si * pages_per_shot,
                page_end=si * pages_per_shot + len(g) - 1,
                text=" ".join(g).strip(),
                start_ms=start,
                end_ms=end,
                is_chapter_start=(si == 0),
                word_count=group_words[si],
            ))
    return shots


def shots_to_json(shots: List[Shot]) -> list:
    return [asdict(s) | {"duration_ms": s.duration_ms} for s in shots]
