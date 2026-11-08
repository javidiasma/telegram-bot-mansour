"""Persian-safe text drawing for Pillow, plus font download.

Two ways to get correctly joined right-to-left Persian:
  * arabic_reshaper + python-bidi (installed from requirements.txt — used on Railway), or
  * Pillow's own RAQM layout engine, when the system has libraqm/fribidi.
"""
import re
from pathlib import Path

from PIL import ImageFont, features

RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

try:
    import arabic_reshaper
    try:
        from bidi import get_display          # python-bidi >= 0.5
    except ImportError:
        from bidi.algorithm import get_display
    MODE = "reshaper"
except ImportError:
    MODE = "raqm" if features.check("raqm") else "none"


def load_font(path: str | None, size: int):
    layout = ImageFont.Layout.RAQM if MODE == "raqm" else ImageFont.Layout.BASIC
    for p in [path] + FALLBACK_FONTS:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size, layout_engine=layout)
            except OSError:
                continue
    return ImageFont.load_default(size)


def shape(text: str) -> str:
    """Visual-order text for drawing. With RAQM, Pillow shapes it itself."""
    if MODE == "reshaper" and RTL.search(text):
        return get_display(arabic_reshaper.reshape(text))
    return text


def width(draw, text: str, font) -> float:
    return draw.textlength(shape(text), font=font)


def draw(d, xy, text: str, font, fill, align: str = "left", **kw) -> None:
    """align: left = x is the left edge, right = x is the right edge, center = x is the middle."""
    s = shape(text)
    x, y = xy
    w = d.textlength(s, font=font)
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    d.text((x, y), s, font=font, fill=fill, **kw)


FONT_MAGIC = (b"\x00\x01\x00\x00", b"true", b"OTTO")
VAZIR_BASES = [
    "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/",
    "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@master/fonts/ttf/",
]
# Google Fonts' variable build of the same font, used if the files above can't be reached
VAZIR_VARIABLE = "https://raw.githubusercontent.com/google/fonts/main/ofl/vazirmatn/Vazirmatn%5Bwght%5D.ttf"


def font_urls(weight: str, override: str = "") -> list[str]:
    """Candidate download URLs for Vazirmatn-<weight>.ttf ('Bold' or 'Regular')."""
    urls = [override] if override else []
    urls += [base + f"Vazirmatn-{weight}.ttf" for base in VAZIR_BASES]
    urls.append(VAZIR_VARIABLE)
    return urls


async def ensure_font(client, data_dir: Path, urls: list[str] | str) -> str | None:
    """Downloads a Persian font once into DATA_DIR/fonts; tries each URL in turn.
    Returns the file path, or None if no real font file could be downloaded."""
    if isinstance(urls, str):
        urls = [urls]
    for url in urls:
        name = url.rsplit("/", 1)[-1].replace("%5B", "[").replace("%5D", "]")
        path = data_dir / "fonts" / name
        if path.exists() and path.stat().st_size > 10_000:
            return str(path)
        try:
            r = await client.get(url, timeout=60)
            if r.status_code == 200 and r.content[:4] in FONT_MAGIC:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
                return str(path)
        except Exception:  # noqa: BLE001 – try the next mirror
            continue
    return None
