"""
Always-available image backend: a captioned gradient frame.

It needs no GPU, no model download, and no login, so the full pipeline —
timing, Ken Burns motion, and final assembly — can be tested the moment the app
starts. Swap in Stable Diffusion or Copilot for real art when you're ready; the
storyboard, sync, and video build are identical either way.
"""

import hashlib
import textwrap

from PIL import Image, ImageDraw, ImageFont


def _hue_from(seed: str):
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    # two muted, cinematic tones for a vertical gradient
    top = (28 + h[0] % 40, 24 + h[1] % 40, 30 + h[2] % 50)
    bot = (8 + h[3] % 24, 6 + h[4] % 20, 10 + h[5] % 26)
    return top, bot


def _font(size):
    for name in ("DejaVuSerif.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate(prompt: str, out_path: str, *, w: int = 1280, h: int = 720,
             label: str = "stable diffusion", **_) -> str:
    top, bot = _hue_from(prompt)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        f = y / max(1, h - 1)
        r = int(top[0] + (bot[0] - top[0]) * f)
        g = int(top[1] + (bot[1] - top[1]) * f)
        b = int(top[2] + (bot[2] - top[2]) * f)
        for x in range(w):
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    # caption block
    title = (prompt or "").split(",")[0].strip()[:120] or "untitled scene"
    body = _font(40)
    wrapped = textwrap.fill(title, width=34)
    d.multiline_text((70, h // 2 - 60), wrapped, font=body,
                     fill=(233, 220, 196), spacing=10)
    tag = _font(22)
    d.text((70, h - 70), f"SeeStory placeholder · {label}", font=tag,
           fill=(232, 162, 60))
    # thin amber rule
    d.rectangle([70, h - 86, 70 + 320, h - 84], fill=(232, 162, 60))
    img.save(out_path, "JPEG", quality=90)
    return out_path
