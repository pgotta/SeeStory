"""Generate SeeStory PNG and Windows ICO assets from vector-like Pillow drawing."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
STATIC = ROOT / "app" / "static"


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (size, size))
    px = image.load()
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        for x in range(size):
            px[x, y] = color
    return image.convert("RGBA")


def draw_icon(size: int = 1024) -> Image.Image:
    scale = size / 512.0
    s = lambda value: round(value * scale)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = _gradient(size, (43, 33, 24), (8, 9, 11))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((s(22), s(22), s(490), s(490)), radius=s(108), fill=255)
    canvas.alpha_composite(Image.composite(bg, Image.new("RGBA", (size, size)), mask))
    draw = ImageDraw.Draw(canvas, "RGBA")

    draw.rounded_rectangle((s(31), s(31), s(481), s(481)), radius=s(99), outline=(240, 173, 79, 190), width=s(8))

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    gd.ellipse((s(62), s(32), s(450), s(420)), fill=(248, 196, 111, 95))
    glow = glow.filter(ImageFilter.GaussianBlur(s(55)))
    canvas.alpha_composite(glow)
    draw = ImageDraw.Draw(canvas, "RGBA")

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((s(111), s(373), s(401), s(431)), fill=(0, 0, 0, 145))
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(18)))
    canvas.alpha_composite(shadow)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Open dark book behind the owl.
    left_book = [(s(256), s(190)), (s(225), s(168)), (s(189), s(148)), (s(151), s(140)), (s(116), s(141)), (s(116), s(301)), (s(166), s(299)), (s(215), s(317)), (s(256), s(349))]
    right_book = [(size - x, y) for x, y in left_book]
    draw.polygon(left_book, fill=(32, 22, 15, 255), outline=(223, 148, 55, 255))
    draw.line(left_book + [left_book[0]], fill=(223, 148, 55, 255), width=s(9), joint="curve")
    draw.polygon(right_book, fill=(28, 21, 16, 255), outline=(223, 148, 55, 255))
    draw.line(right_book + [right_book[0]], fill=(223, 148, 55, 255), width=s(9), joint="curve")

    # Lit pages.
    left_page = [(s(256), s(298)), (s(226), s(280)), (s(196), s(267)), (s(166), s(261)), (s(140), s(260)), (s(140), s(371)), (s(185), s(370)), (s(222), s(385)), (s(256), s(414))]
    right_page = [(size - x, y) for x, y in left_page]
    draw.polygon(left_page, fill=(241, 218, 165, 255), outline=(246, 217, 155, 255))
    draw.line(left_page + [left_page[0]], fill=(246, 217, 155, 255), width=s(5), joint="curve")
    draw.polygon(right_page, fill=(235, 207, 153, 255), outline=(246, 217, 155, 255))
    draw.line(right_page + [right_page[0]], fill=(246, 217, 155, 255), width=s(5), joint="curve")
    draw.line((s(256), s(299), s(256), s(415)), fill=(120, 80, 45, 210), width=s(5))
    for y in (289, 320):
        draw.arc((s(157), s(y - 4), s(241), s(y + 36)), 195, 335, fill=(138, 97, 60, 110), width=s(7))
        draw.arc((s(271), s(y - 4), s(355), s(y + 36)), 205, 345, fill=(138, 97, 60, 110), width=s(7))

    # Owl face, tufts, eyes, and beak.
    draw.line((s(155), s(158), s(200), s(200)), fill=(240, 173, 79, 255), width=s(13))
    draw.line((s(357), s(158), s(312), s(200)), fill=(240, 173, 79, 255), width=s(13))
    draw.ellipse((s(142), s(161), s(268), s(293)), fill=(239, 228, 201, 255), outline=(213, 141, 50, 255), width=s(9))
    draw.ellipse((s(244), s(161), s(370), s(293)), fill=(239, 228, 201, 255), outline=(213, 141, 50, 255), width=s(9))
    draw.ellipse((s(178), s(203), s(232), s(257)), fill=(21, 17, 15, 255))
    draw.ellipse((s(280), s(203), s(334), s(257)), fill=(21, 17, 15, 255))
    draw.ellipse((s(208), s(210), s(224), s(226)), fill=(255, 200, 106, 255))
    draw.ellipse((s(310), s(210), s(326), s(226)), fill=(255, 200, 106, 255))
    beak = [(s(256), s(254)), (s(231), s(288)), (s(281), s(288))]
    draw.polygon(beak, fill=(240, 166, 64, 255), outline=(138, 78, 31, 255))
    draw.line(beak + [beak[0]], fill=(138, 78, 31, 255), width=s(4), joint="curve")

    # Film-frame accents.
    for x, y in ((74, 81), (394, 81), (74, 412), (394, 412)):
        draw.rounded_rectangle((s(x), s(y), s(x + 44), s(y + 19)), radius=s(7), fill=(240, 173, 79, 230))

    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    ASSETS.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    image = draw_icon(1024)
    image.save(ASSETS / "SeeStory.png", "PNG", optimize=True)
    image.save(STATIC / "seestory-icon.png", "PNG", optimize=True)
    image.save(
        ASSETS / "SeeStory.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    if not args.quiet:
        print(f"Created {ASSETS / 'SeeStory.ico'}")
        print(f"Created {ASSETS / 'SeeStory.png'}")
        print(f"Created {STATIC / 'seestory-icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
