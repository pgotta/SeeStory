"""
The "scene director": decide WHAT to draw for each shot and WHO draws it.

Everything here runs locally with no credentials. For each shot it:
  1. finds the most visually concrete moment in the page text,
  2. writes an image-generation prompt, seeded with a persistent style bible
     so recurring people/places look the same every time they appear,
  3. scores how exciting/visual the moment is, and
  4. routes the shot to a backend — Stable Diffusion for the everyday pages,
     Copilot (higher quality) for the standout moments, under a hard cap so a
     personal Copilot session is never hammered.

An optional Copilot text pass can rewrite the prompt for the highlighted
shots, but it is never required — the heuristic alone produces usable prompts.
"""

import re
from typing import List

from .timeline import Shot


# Words that signal something worth *seeing* — concrete, paintable nouns.
_IMAGERY = {
    "storm", "rain", "lightning", "thunder", "wind", "wave", "ocean", "sea",
    "lighthouse", "mountain", "forest", "tree", "river", "fire", "flame",
    "snow", "ice", "fog", "mist", "cloud", "sky", "sun", "moon", "star",
    "castle", "tower", "bridge", "city", "street", "ship", "boat", "train",
    "horse", "wolf", "bird", "dragon", "sword", "candle", "lantern", "window",
    "door", "garden", "field", "desert", "cliff", "cave", "ruins", "church",
    "ballroom", "throne", "blood", "shadow", "dawn", "dusk", "sunset",
    "sunrise", "rose", "flower", "mansion", "cottage", "harbor", "valley",
}

# Words that signal a charged / pivotal moment worth a *premium* visual.
_TENSION = {
    "suddenly", "scream", "screamed", "blood", "death", "died", "killed",
    "fire", "burning", "explosion", "gun", "knife", "fell", "crash", "storm",
    "betrayed", "revealed", "secret", "truth", "discovered", "vanished",
    "chase", "ran", "fled", "battle", "fight", "war", "kiss", "kissed",
    "wept", "tears", "darkness", "terror", "horror", "monster", "ghost",
}

_STOP_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_QUOTE = re.compile(r"[\"“”‘’']")
_DIALOGUE = re.compile(r"^\s*[\"“].*?[\"”]\s*$")
_SPEECH_SPAN = re.compile(r"[\"“][^\"”]*[\"”]")   # a run of quoted speech


def _strip_dialogue(s: str) -> str:
    """Remove quoted speech from a sentence, leaving the descriptive remainder."""
    s = _SPEECH_SPAN.sub("", s or "")
    s = _QUOTE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;:—-")


def _words(text: str):
    return re.findall(r"[a-z']+", (text or "").lower())


def _imagery_score(text: str) -> int:
    return sum(1 for w in _words(text) if w in _IMAGERY)


def _tension_score(text: str) -> int:
    return sum(1 for w in _words(text) if w in _TENSION)


def pick_focus_sentence(text: str, max_chars: int = 240) -> str:
    """The single most paintable sentence in the passage."""
    sents = [s.strip() for s in _STOP_SENTENCE.split(text or "") if s.strip()]
    if not sents:
        return ""
    # Prefer concrete, descriptive prose. Dialogue and questions ("You trying to
    # tell us what to do?") describe no scene and make the image model hallucinate,
    # so they're pushed down hard.
    scored = []
    for s in sents:
        score = _imagery_score(s) * 2 + _tension_score(s)
        if _DIALOGUE.match(s):                 # a whole line of speech
            score -= 4
        if "?" in s or "!" in s:               # questions/exclamations: usually speech
            score -= 2
        if s.lstrip()[:1] in '"“‘\'':          # starts mid-dialogue
            score -= 2
        scored.append((score, len(s), s))
    scored.sort(key=lambda t: (-t[0], abs(t[1] - 140)))
    best = _strip_dialogue(scored[0][2])
    if not best:                               # whole sentence was quoted speech
        best = _QUOTE.sub("", scored[0][2])
    return best[:max_chars].strip()


class StyleBible:
    """The look of the book + consistent descriptions of recurring entities.

    `style` is appended to every prompt (the art direction). `entities` maps a
    name -> a short fixed description; whenever a shot's text mentions that
    name, the description is folded into the prompt so the character/place is
    rendered consistently across the whole book.
    """

    PRESETS = {
        "photoreal": "photorealistic photograph, 85mm lens, natural light, "
                     "sharp focus, realistic skin texture, beautiful people "
                     "and scenery",
        "cinematic": "cinematic painterly illustration, dramatic lighting, "
                     "rich depth of field, atmospheric, detailed",
        "storybook": "warm storybook watercolor illustration, soft edges, "
                     "gentle light, hand-painted texture",
        "noir": "moody film-noir illustration, high contrast, deep shadows, "
                "rain-slicked, monochrome with a single warm accent",
        "oil": "classical oil painting, visible brushwork, golden-hour light, "
               "romantic realism",
        "ink": "detailed pen-and-ink illustration with selective watercolor "
               "washes, fine linework",
    }

    def __init__(self, style_key: str = "cinematic",
                 custom_style: str = "", entities: dict = None):
        self.style_key = style_key
        self.custom_style = custom_style.strip()
        self.entities = entities or {}

    @property
    def style(self) -> str:
        return self.custom_style or self.PRESETS.get(self.style_key,
                                                     self.PRESETS["cinematic"])

    def entity_hints(self, text: str) -> str:
        low = (text or "").lower()
        hints = [desc for name, desc in self.entities.items()
                 if name and name.lower() in low and desc]
        return "; ".join(hints)

    def to_json(self):
        return {"style_key": self.style_key, "custom_style": self.custom_style,
                "entities": self.entities}


# Words that make image models render literal text / book-cover titles, or that
# trip Copilot's content filter (so it declines and returns no image). We strip
# them from the art-direction style and the assembled prompt. This is why a
# "custom style" like "thriller, dan brown book" produced giant garbled titles
# and made Copilot bail — the model saw "book" and drew a cover.
_BAD_PROMPT_TERMS = re.compile(
    r"\b(audio ?book|book|novel|ebook|e-book|paperback|hardcover|cover|"
    r"title|titled|chapter|page|text|words?|lettering|caption|subtitles?|"
    r"logo|watermark|signature|"
    r"thriller|mystery|suspense|horror|romance|fantasy|sci-?fi|"
    r"science fiction|drama|comedy|crime|noir fiction|"
    r"adult[- ]?oriented|adults?|nsfw|explicit|erotic|porn(ographic)?|"
    r"gore|gory|graphic)\b",
    re.I)


def _scrub(s: str) -> str:
    """Drop text-inducing / filter-tripping tokens and tidy leftover commas."""
    s = _BAD_PROMPT_TERMS.sub("", s or "")
    s = re.sub(r"\s*,(?:\s*,)+", ", ", s)        # collapse empty commas
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;")


def _approx_tokens(s: str) -> int:
    """Rough CLIP token count — enough to keep us under the 77-token limit."""
    return int(len(s.split()) * 1.35) + s.count(",")


def _trim_to_tokens(s: str, max_tokens: int) -> str:
    """Keep whole words from the front until we'd exceed the budget."""
    out = []
    for w in s.split():
        out.append(w)
        if _approx_tokens(" ".join(out)) >= max_tokens:
            break
    return " ".join(out).rstrip(" ,;:—-")


def build_prompt(shot: Shot, bible: StyleBible) -> str:
    """Compose a clean image prompt for a shot."""
    focus = pick_focus_sentence(shot.text)
    focus = _QUOTE.sub("", focus)
    focus = re.sub(r"\s+", " ", focus).strip().rstrip(".")
    if not focus:
        focus = f"a quiet scene from {shot.chapter_title}"
    hints = bible.entity_hints(shot.text)
    style = _scrub(bible.style)
    if not style:
        # The custom style was all genre/non-visual words (e.g. "thriller") and
        # scrubbed away — fall back to the chosen preset so every image still has
        # real art direction instead of none.
        style = bible.PRESETS.get(bible.style_key, bible.PRESETS["cinematic"])
    # CLIP only reads ~77 tokens. Reserve room for the style (and any entity
    # hints), then trim the scene sentence to fit so nothing is silently cut —
    # the scene stays first (most important) and the style always survives.
    tail = ", ".join(p for p in (hints, style) if p)
    budget = 70 - _approx_tokens(tail) - 1
    focus = _trim_to_tokens(focus, max(8, budget))
    parts = [focus] + ([hints] if hints else []) + [style]
    return ", ".join(parts)


def score_highlight(shot: Shot) -> float:
    """How much this moment deserves a premium (Copilot) render."""
    s = _tension_score(shot.text) * 2.0 + _imagery_score(shot.text) * 1.0
    if shot.is_chapter_start:
        s += 1.5
    return round(s, 2)


def direct(shots: List[Shot], bible: StyleBible) -> None:
    """Fill prompt + highlight_score on every shot (in place)."""
    for sh in shots:
        sh.prompt = build_prompt(sh, bible)
        sh.highlight_score = score_highlight(sh)


def route_backends(shots: List[Shot], *, mode: str = "both",
                   sd_backend: str = "stablediffusion",
                   copilot_every_pages: int = 10,
                   copilot_cap: int = 30) -> dict:
    """Assign .backend / .highlighted across all shots.

    mode: 'sd_only' | 'copilot_only' | 'both'
    Returns a small summary dict for the UI.
    """
    total_pages = sum((s.page_end - s.page_start + 1) for s in shots) or 1

    if mode == "copilot_only":
        for s in shots:
            s.backend, s.highlighted = "copilot", True
        # still respect the cap: beyond the cap, fall back to SD/placeholder
        for s in sorted(shots, key=lambda x: -x.highlight_score)[copilot_cap:]:
            s.backend, s.highlighted = sd_backend, False
        used = sum(1 for s in shots if s.backend == "copilot")
        return {"copilot": used, "sd": len(shots) - used, "total": len(shots)}

    # default everything to the SD-class backend first
    for s in shots:
        s.backend, s.highlighted = sd_backend, False

    if mode == "sd_only":
        return {"copilot": 0, "sd": len(shots), "total": len(shots)}

    # mode == 'both': promote the strongest moments to Copilot, but no more than
    # roughly one per `copilot_every_pages` pages and never past the hard cap.
    budget = min(copilot_cap, max(1, total_pages // max(1, copilot_every_pages)))
    candidates = sorted(shots, key=lambda x: -x.highlight_score)
    promoted, last_start = 0, {}
    min_gap_ms = 60_000  # don't put two Copilot shots within a minute of audio
    for s in candidates:
        if promoted >= budget:
            break
        if s.highlight_score <= 0 and not s.is_chapter_start:
            continue
        # spacing: keep premium shots spread out across the runtime
        too_close = any(abs(s.start_ms - t) < min_gap_ms for t in last_start.values())
        if too_close:
            continue
        s.backend, s.highlighted = "copilot", True
        last_start[s.id] = s.start_ms
        promoted += 1
    return {"copilot": promoted, "sd": len(shots) - promoted,
            "total": len(shots), "budget": budget}
