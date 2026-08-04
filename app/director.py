"""Turn each timed text shot into one concise, visually coherent image prompt.

The director is deliberately deterministic and local.  It finds a concrete
sentence, strips dialogue/genre words that make image models draw text, and adds
consistent art direction.  Human scenes receive extra anatomy guidance because
hands, limbs and duplicated bodies are common diffusion failure modes.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List

from .timeline import Shot

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
_TENSION = {
    "suddenly", "scream", "screamed", "blood", "death", "died", "killed",
    "fire", "burning", "explosion", "gun", "knife", "fell", "crash", "storm",
    "betrayed", "revealed", "secret", "truth", "discovered", "vanished",
    "chase", "ran", "fled", "battle", "fight", "war", "kiss", "kissed",
    "wept", "tears", "darkness", "terror", "horror", "monster", "ghost",
}
_HUMAN = {
    "man", "woman", "boy", "girl", "child", "person", "people", "mother",
    "father", "mom", "dad", "brother", "sister", "husband", "wife", "soldier",
    "doctor", "teacher", "officer", "he", "she", "him", "her", "his", "hers",
}

_STOP_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_QUOTE = re.compile(r"[\"“”‘’']")
_DIALOGUE = re.compile(r"^\s*[\"“].*?[\"”]\s*$")
_SPEECH_SPAN = re.compile(r"[\"“][^\"”]*[\"”]")


def _strip_dialogue(s: str) -> str:
    s = _SPEECH_SPAN.sub("", s or "")
    s = _QUOTE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;:—-")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", (text or "").lower())


def _imagery_score(text: str) -> int:
    return sum(1 for w in _words(text) if w in _IMAGERY)


def _tension_score(text: str) -> int:
    return sum(1 for w in _words(text) if w in _TENSION)


def _has_human(text: str) -> bool:
    return any(w in _HUMAN for w in _words(text))


def _focus_candidates(text: str, max_chars: int = 280) -> list[str]:
    """Return paintable sentences from best to worst, without duplicates."""
    sents = [s.strip() for s in _STOP_SENTENCE.split(text or "") if s.strip()]
    if not sents:
        return []
    scored = []
    for idx, sent in enumerate(sents):
        score = _imagery_score(sent) * 2 + _tension_score(sent)
        if _DIALOGUE.match(sent):
            score -= 5
        if "?" in sent:
            score -= 3
        if sent.lstrip()[:1] in '"“‘\'':
            score -= 2
        score -= abs(len(sent) - 150) / 180.0
        scored.append((score, idx, sent))
    scored.sort(key=lambda item: item[0], reverse=True)

    out: list[str] = []
    seen: set[str] = set()
    for _score, idx, raw in scored:
        best = _strip_dialogue(raw) or _QUOTE.sub("", raw)
        low = _words(best)
        pronoun_heavy = bool(low) and low[0] in {"he", "she", "they", "his", "her", "their"}
        if pronoun_heavy and idx > 0:
            context = _strip_dialogue(sents[idx - 1])
            if context and len(context) <= 140:
                best = f"{context}. {best}"
        best = best[:max_chars].strip()
        key = re.sub(r"[^a-z0-9]+", " ", best.lower()).strip()
        if best and key and key not in seen:
            seen.add(key)
            out.append(best)
    return out


def pick_focus_sentence(text: str, max_chars: int = 280) -> str:
    """Pick the most paintable non-dialogue sentence in a passage."""
    candidates = _focus_candidates(text, max_chars=max_chars)
    return candidates[0] if candidates else ""


def _focus_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _too_similar_focus(candidate: str, previous: str) -> bool:
    """Guard against near-identical consecutive storyboard scenes."""
    a, b = _focus_key(candidate), _focus_key(previous)
    if not a or not b:
        return False
    if a == b:
        return True
    # Long scene sentences can differ by a couple of words yet still generate
    # essentially the same picture.  Keep this deliberately conservative.
    return SequenceMatcher(None, a, b).ratio() >= 0.88


class StyleBible:
    PRESETS = {
        "photoreal": "photorealistic cinematic still, natural light, realistic skin texture, "
                     "anatomically correct proportions, coherent composition, sharp detail",
        "cinematic": "cinematic painterly illustration, dramatic natural lighting, rich depth, "
                     "coherent composition, detailed",
        "storybook": "warm storybook watercolor illustration, soft edges, gentle light, "
                     "hand-painted texture, coherent composition",
        "noir": "moody film-noir illustration, high contrast, deep shadows, rain-slicked, "
                "monochrome with one warm accent, coherent composition",
        "oil": "classical oil painting, visible brushwork, golden-hour light, romantic realism, "
               "coherent composition",
        "ink": "detailed pen-and-ink illustration with selective watercolor washes, fine "
               "linework, coherent composition",
    }

    def __init__(self, style_key: str = "cinematic", custom_style: str = "", entities: dict | None = None):
        self.style_key = style_key
        self.custom_style = custom_style.strip()
        self.entities = entities or {}

    @property
    def style(self) -> str:
        return self.custom_style or self.PRESETS.get(self.style_key, self.PRESETS["cinematic"])

    def entity_hints(self, text: str) -> str:
        low = (text or "").lower()
        hints = [desc for name, desc in self.entities.items()
                 if name and name.lower() in low and desc]
        return "; ".join(hints)

    def to_json(self) -> dict:
        return {"style_key": self.style_key, "custom_style": self.custom_style,
                "entities": self.entities}


_BAD_PROMPT_TERMS = re.compile(
    r"\b(audio ?book|book|novel|ebook|e-book|paperback|hardcover|cover|"
    r"title|titled|chapter|page|text|words?|lettering|caption|subtitles?|"
    r"logo|watermark|signature|"
    r"thriller|mystery|suspense|horror|romance|fantasy|sci-?fi|"
    r"science fiction|drama|comedy|crime|noir fiction|"
    r"adult[- ]?oriented|adults?|nsfw|explicit|erotic|porn(ographic)?|"
    r"gore|gory|graphic)\b",
    re.I,
)


def _scrub(s: str) -> str:
    s = _BAD_PROMPT_TERMS.sub("", s or "")
    s = re.sub(r"\s*,(?:\s*,)+", ", ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;")


def _approx_tokens(s: str) -> int:
    return int(len(s.split()) * 1.35) + s.count(",")


def _trim_to_tokens(s: str, max_tokens: int) -> str:
    out: list[str] = []
    for word in s.split():
        out.append(word)
        if _approx_tokens(" ".join(out)) >= max_tokens:
            break
    return " ".join(out).rstrip(" ,;:—-")


def build_prompt(shot: Shot, bible: StyleBible, *, focus_override: str | None = None) -> str:
    focus = _scrub(_QUOTE.sub("", focus_override if focus_override is not None
                             else pick_focus_sentence(shot.text)))
    focus = re.sub(r"\s+", " ", focus).strip().rstrip(".")
    if not focus:
        focus = "a quiet atmospheric scene"

    hints = _scrub(bible.entity_hints(shot.text))
    style = _scrub(bible.style) or bible.PRESETS.get(
        bible.style_key, bible.PRESETS["cinematic"]
    )
    anatomy = (
        "natural human anatomy, realistic hands, five fingers per hand, "
        "two arms and two legs, no duplicated body parts"
        if _has_human(shot.text) else ""
    )
    coherence = "single coherent scene, one moment, physically plausible composition"

    tail = ", ".join(part for part in (hints, anatomy, coherence, style) if part)
    budget = 72 - _approx_tokens(tail) - 1
    focus = _trim_to_tokens(focus, max(10, budget))
    return ", ".join(part for part in (focus, hints, anatomy, coherence, style) if part)


def direct(shots: List[Shot], bible: StyleBible) -> None:
    """Write prompts while avoiding recycled scene choices within a chapter.

    Normally each shot receives non-overlapping source text, so duplicate focus
    sentences should be rare.  EPUBs with repeated headers or unusual markup can
    still expose the same sentence twice; in that case prefer the next-best
    concrete sentence before allowing a repeated image concept.
    """
    history: dict[int, list[str]] = {}
    for shot in shots:
        prior = history.setdefault(shot.chapter_index, [])
        candidates = _focus_candidates(shot.text)
        focus = ""
        for candidate in candidates:
            # Compare against the last few scenes in this chapter.  Looking at a
            # small window prevents obvious repetition without forcing distant,
            # intentionally recurring motifs to become unrelated.
            if not any(_too_similar_focus(candidate, old) for old in prior[-4:]):
                focus = candidate
                break
        if not focus:
            focus = candidates[0] if candidates else ""
        shot.prompt = build_prompt(shot, bible, focus_override=focus)
        if focus:
            prior.append(focus)
