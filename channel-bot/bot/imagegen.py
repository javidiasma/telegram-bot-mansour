"""AI background image + crisp text overlay (Persian-safe).

AI models write Persian text badly, so the AI only paints the background and the bot
draws the admin's text on top with a real Persian font (Vazirmatn).
"""
import base64
import io
import random
import re
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont

SIZE = 1080
RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
]


class ImageError(Exception):
    pass


# ---------------------------------------------------------------- AI background
def build_prompt(text: str, description: str | None) -> str:
    base = description.strip() if description else f"A beautiful artistic background inspired by: {text}"
    return (f"{base}. High quality, cinematic lighting, rich colors, simple composition with calm space "
            "in the lower half. Absolutely no text, no letters, no words, no watermark, no logo.")


async def generate_background(client, prompt: str, openai_key: str = "", openai_model: str = "gpt-image-1",
                              pollinations_url: str = "https://image.pollinations.ai/prompt/") -> bytes:
    if openai_key:
        r = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {openai_key}"},
            json={"model": openai_model, "prompt": prompt, "size": "1024x1024", "n": 1},
            timeout=180,
        )
        if r.status_code >= 400:
            raise ImageError(f"OpenAI error {r.status_code}: {r.text[:300]}")
        item = r.json()["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        img = await client.get(item["url"], timeout=120)
        img.raise_for_status()
        return img.content

    url = pollinations_url + quote(prompt)
    r = await client.get(
        url,
        params={"width": SIZE, "height": SIZE, "nologo": "true", "seed": random.randint(1, 10**9)},
        timeout=180,
    )
    if r.status_code >= 400 or not r.headers.get("content-type", "").startswith("image"):
        raise ImageError(f"Image service error {r.status_code}")
    return r.content


# ---------------------------------------------------------------- font
async def ensure_font(client, data_dir: Path, font_url: str) -> str | None:
    path = data_dir / "fonts" / "Vazirmatn-Bold.ttf"
    if path.exists() and path.stat().st_size > 10_000:
        return str(path)
    try:
        r = await client.get(font_url, timeout=60)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return str(path)
    except Exception:
        return next((p for p in FALLBACK_FONTS if Path(p).exists()), None)


def _load_font(font_path: str | None, size: int):
    if font_path:
        try:
            return ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)
        except OSError:
            pass
    return ImageFont.load_default(size)


# ---------------------------------------------------------------- text rendering
def _shape(line: str) -> str:
    """Join Persian letters and put them in visual (right-to-left) order."""
    if not RTL.search(line):
        return line
    try:
        import arabic_reshaper
        try:
            from bidi import get_display          # python-bidi >= 0.5
        except ImportError:
            from bidi.algorithm import get_display
    except ImportError:
        return line
    return get_display(arabic_reshaper.reshape(line))


def _wrap(text: str, font, draw, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.splitlines() or [""]:
        words, cur = para.split(), ""
        for w in words:
            cand = f"{cur} {w}".strip()
            if draw.textlength(_shape(cand), font=font) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def render_text(background: bytes, text: str, font_path: str | None) -> bytes:
    img = Image.open(io.BytesIO(background)).convert("RGB")
    # center-crop to square and resize
    w, h = img.size
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((SIZE, SIZE))

    probe = ImageDraw.Draw(img)
    max_w = int(SIZE * 0.84)
    for size in range(96, 31, -4):
        font = _load_font(font_path, size)
        lines = _wrap(text.strip(), font, probe, max_w)
        line_h = int(size * 1.55)
        if len(lines) * line_h <= SIZE * 0.5:
            break

    block_h = len(lines) * line_h
    pad = int(size * 0.6)
    top = int(SIZE * 0.70 - block_h / 2)
    top = max(pad, min(top, SIZE - block_h - pad * 2))

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    widest = max((probe.textlength(_shape(l), font=font) for l in lines), default=0)
    box_w = min(SIZE - 40, widest + pad * 2)
    od.rounded_rectangle(
        ((SIZE - box_w) / 2, top - pad, (SIZE + box_w) / 2, top + block_h + pad / 2),
        radius=28, fill=(0, 0, 0, 140),
    )
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)
    stroke = max(2, size // 28)
    for i, line in enumerate(lines):
        shaped = _shape(line)
        lw = draw.textlength(shaped, font=font)
        draw.text(((SIZE - lw) / 2, top + i * line_h), shaped, font=font,
                  fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0))

    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()
