"""AI background image + crisp text overlay (Persian-safe).

AI models write Persian text badly, so the AI only paints the background and the bot
draws the admin's text on top with a real Persian font (Vazirmatn).
"""
import base64
import io
import logging

from PIL import Image, ImageDraw

from . import textrender as tr

SIZE = 1080
log = logging.getLogger(__name__)


class ImageError(Exception):
    pass


# ---------------------------------------------------------------- optional prompt writer (chat model)
PROMPT_SYSTEM = (
    "You write prompts for an AI image generator. The user gives the text that will be printed on a "
    "picture (often Persian) and optionally an idea for the background. Reply with ONE English prompt, "
    "at most 60 words, describing only the background scene: subject, mood, style, lighting and colors. "
    "Never ask for any text, letters, words or logos in the image. Reply with the prompt only."
)


async def write_prompt(client, base_url: str, api_key: str, model: str,
                       text: str, description: str | None) -> str | None:
    """Uses an OpenAI-compatible chat model to turn the admin's (Persian) input into a good English
    image prompt. Returns None on any failure so the caller can fall back to the plain prompt."""
    if not (base_url and api_key and model):
        return None
    try:
        r = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "max_tokens": 200, "temperature": 0.7, "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": f"Text on the image: {text}\n"
                                            f"Background idea: {description or '(none, choose something fitting)'}"},
            ]},
            timeout=60,
        )
        if r.status_code != 200:
            log.warning("prompt writer returned %s: %s", r.status_code, r.text[:200])
            return None
        content = r.json()["choices"][0]["message"]["content"].strip().strip('"')
        return content[:600] or None
    except Exception:  # noqa: BLE001
        log.exception("prompt writer failed")
        return None


# ---------------------------------------------------------------- AI background
def build_prompt(text: str, description: str | None) -> str:
    base = description.strip() if description else f"A beautiful artistic background inspired by: {text}"
    return (f"{base}. High quality, cinematic lighting, rich colors, simple composition with calm space "
            "in the lower half. Absolutely no text, no letters, no words, no watermark, no logo.")


async def generate_background(client, prompt: str, base_url: str, api_key: str, model: str) -> bytes:
    """Calls an OpenAI-compatible /images/generations endpoint (GapGPT, OpenAI, ...)."""
    if not (base_url and api_key and model):
        raise ImageError("سرویس ساخت تصویر تنظیم نشده است (IMAGE_API_BASE_URL / IMAGE_API_KEY / IMAGE_MODEL).")
    r = await client.post(
        base_url.rstrip("/") + "/images/generations",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "prompt": prompt, "size": "1024x1024", "n": 1},
        timeout=180,
    )
    if r.status_code >= 400:
        raise ImageError(f"خطای سرویس تصویر ({r.status_code}): {r.text[:300]}")
    try:
        item = r.json()["data"][0]
    except Exception as e:  # noqa: BLE001
        raise ImageError(f"پاسخ نامعتبر از سرویس تصویر: {r.text[:300]}") from e
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        img = await client.get(item["url"], timeout=120)
        img.raise_for_status()
        return img.content
    raise ImageError("سرویس تصویر هیچ تصویری برنگرداند.")


def _shape(line: str) -> str:
    return tr.shape(line)


def _load_font(font_path, size):
    return tr.load_font(font_path, size)


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
