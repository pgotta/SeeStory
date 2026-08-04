"""Create a simple local frame used only by the Ken Burns motion preview.

This is not an image-generation fallback. Real storyboard images always use the
local diffusion model; this frame only gives the motion controls something to
animate before the user has generated a sample image.
"""
from __future__ import annotations

import hashlib
import textwrap

from PIL import Image, ImageDraw, ImageFont


def _tones(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    top = (28 + digest[0] % 40, 24 + digest[1] % 40, 30 + digest[2] % 50)
    bottom = (8 + digest[3] % 24, 6 + digest[4] % 20, 10 + digest[5] % 26)
    return top, bottom


def _font(size: int):
    for name in ("DejaVuSerif.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def create(out_path: str, *, w: int = 1280, h: int = 720) -> str:
    top, bottom = _tones("SeeStory motion preview")
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    # Draw the gradient in horizontal bands rather than assigning every pixel in
    # Python. This is much faster and is more than smooth enough for a preview.
    for y in range(h):
        frac = y / max(1, h - 1)
        color = tuple(int(a + (b - a) * frac) for a, b in zip(top, bottom))
        draw.line((0, y, w, y), fill=color)

    text = textwrap.fill("Generate a sample image for a real motion preview", width=30)
    draw.multiline_text(
        (70, h // 2 - 50), text, font=_font(34), fill=(233, 220, 196), spacing=10
    )
    draw.rectangle([70, h - 86, 390, h - 84], fill=(232, 162, 60))
    draw.text((70, h - 70), "SeeStory motion preview", font=_font(22), fill=(232, 162, 60))
    img.save(out_path, "JPEG", quality=90)
    return out_path
