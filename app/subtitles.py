"""
Subtitle (.srt) generation.

SeeStory's finest audio anchors are the chapter starts (from the YouTube
timestamps) and, within each chapter, the per-shot windows it already computes
by distributing the chapter's real duration across its text. We split each
shot's text into short caption cues and spread them across that shot's window by
word count.

For Parroty's TTS narration (steady pace) this tracks the audio closely. It is
NOT frame-perfect forced alignment — on variable, human-narrated audio it can
drift within a chapter. The text itself is always the exact ebook text.
"""

import re

_WORDS_PER_CUE = 10            # ~one comfortable subtitle line-pair
_SENTENCE_END = re.compile(r"[.!?]['\"\u201d\u2019)]?$")


def _fmt_ts(ms: float) -> str:
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _cue_chunks(text: str):
    """Split text into short cues (~10 words), breaking early at sentence ends."""
    words = (text or "").split()
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= _WORDS_PER_CUE or _SENTENCE_END.search(w):
            if len(cur) >= 4 or _SENTENCE_END.search(w):
                chunks.append(" ".join(cur))
                cur = []
    if cur:
        if chunks and len(cur) < 3:
            chunks[-1] += " " + " ".join(cur)   # avoid a 1–2 word orphan
        else:
            chunks.append(" ".join(cur))
    return chunks


def _wrap(line: str, width: int = 42) -> str:
    """Wrap a cue to at most two lines for readability."""
    if len(line) <= width:
        return line
    words = line.split()
    a, b, n = [], [], 0
    for w in words:
        if n + len(w) + 1 <= width and not b:
            a.append(w); n += len(w) + 1
        else:
            b.append(w)
    out = " ".join(a)
    if b:
        out += "\n" + " ".join(b)
    return out


def shift_srt(text: str, lead_ms: int) -> str:
    """Shift every cue in an existing .srt by lead_ms (for a prepended cover)."""
    if not lead_ms:
        return text
    ts = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

    def _bump(m):
        h, mi, s, ms = (int(g) for g in m.groups())
        total = ((h * 3600 + mi * 60 + s) * 1000 + ms) + int(lead_ms)
        return _fmt_ts(total)

    return ts.sub(_bump, text)


def build_srt(shots, lead_ms: int = 0) -> str:
    """shots: iterable of dicts with 'text', 'start_ms', 'end_ms' (original
    timeline). lead_ms shifts every cue (e.g. for a cover prepended up front)."""
    out = []
    idx = 1
    for s in shots:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        a = float(s["start_ms"]) + lead_ms
        b = float(s["end_ms"]) + lead_ms
        if b <= a:
            continue
        chunks = _cue_chunks(text)
        if not chunks:
            continue
        weights = [max(1, len(c.split())) for c in chunks]
        total = sum(weights)
        t = a
        span = b - a
        for c, wt in zip(chunks, weights):
            dur = span * (wt / total)
            start, end = t, t + dur
            t = end
            out.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{_wrap(c)}\n")
            idx += 1
    return "\n".join(out)
