"""AI poster images.

The admin writes one request; the image model designs the whole poster, Persian text included.
The older "AI background + text drawn by the bot" method (render_text) is kept below but no longer used.
"""
import asyncio
import base64
import io
import logging

from PIL import Image, ImageDraw

from . import textrender as tr

SIZE = 1080
log = logging.getLogger(__name__)


TEMPORARY = (403, 408, 429, 500, 502, 503, 504)   # provider errors that are usually gone on the next try
RETRY_WAIT = 6


def friendly(e: Exception) -> str:
    """Short Persian explanation for the admin; the full error stays in the log."""
    status = getattr(e, "status", None)
    if status in (403, 429, 500, 502, 503, 504) and "api_provider_error" in str(e):
        return ("سرویس GapGPT درخواست را به سرویس تصویر نرساند (خطای موقت سمت GapGPT، کد "
                f"{status}). چند دقیقه دیگر «تلاش دوباره» را بزنید. اگر مدام تکرار شد، اعتبار حساب و دسترسی "
                "به مدل تصویر را در پنل GapGPT چک کنید یا IMAGE_FALLBACK_MODEL را تنظیم کنید.")
    if status == 401:
        return "کلید API سرویس تصویر نامعتبر است (کد ۴۰۱). IMAGE_API_KEY / CHAT_API_KEY را در Railway چک کنید."
    if status == 402:
        return "اعتبار حساب GapGPT تمام شده است (کد ۴۰۲)."
    return str(e)


class ImageError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- poster prompt (one step)
# The admin writes ONE message (usually Persian) with what the poster is about and the text it should show.
# The image model designs the whole poster, text included, like a graphic designer would.
POSTER_RULES = (
    "Design a professional, eye-catching Persian (Farsi) social-media advertising poster for a travel agency, "
    "in vertical portrait format, like a top Instagram ad by an Iranian graphic designer. "
    "Use a stunning, photorealistic, high-detail scene that fits the subject as the background, with cinematic "
    "warm lighting and rich colors. Lay out the information in clear, well-designed graphic elements: a big bold "
    "headline in a decorative brush-stroke or ribbon banner at the top; small rounded pill badges, each with a simple "
    "icon (calendar for dates, plate/cloche for meals, plane, hotel, etc.); a bold red price badge if there is a "
    "price; and a contact bar with a phone icon at the bottom if there is a phone number. Strong contrast so every "
    "word is easy to read. All text must be in Persian (Farsi), right-to-left, using a bold modern Persian typeface "
    "(like Vazirmatn or Lalezar), with correctly connected letters, spelled EXACTLY as given; Persian digits. "
    "Write only the texts given below; do not invent extra words, slogans, logos or watermarks. "
    "The request may also contain instructions to you (words like poster/پوستر, design, image, or a description of "
    "the scene and mood): follow them, but never print them on the poster - only print the actual information "
    "(destination/title, dates, meal plan, hotel, price, phone, etc.)."
)

PROMPT_SYSTEM = (
    "You turn a travel agency admin's short request (usually Persian) into a prompt for an AI image model that "
    "designs a complete Persian advertising poster. Reply with ONE English prompt of at most 180 words: describe the "
    "background scene and mood in detail, then list every text element that must appear, each in double quotes, "
    "copied EXACTLY in Persian from the request (title, dates, meal plan, price, phone, etc.), saying where each goes "
    "(headline banner at top, icon pill badges, red price badge, contact bar at bottom). Do not translate, add or "
    "change any Persian text. Reply with the prompt only."
)


async def write_prompt(client, base_url: str, api_key: str, model: str, request: str) -> str | None:
    """Optional: a chat model turns the admin's request into a detailed poster brief.
    Returns None on any failure so the caller falls back to the plain prompt."""
    if not (base_url and api_key and model):
        return None
    try:
        r = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "max_tokens": 500, "temperature": 0.6, "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": request},
            ]},
            timeout=60,
        )
        if r.status_code != 200:
            log.warning("prompt writer returned %s: %s", r.status_code, r.text[:200])
            return None
        content = r.json()["choices"][0]["message"]["content"].strip().strip('"')
        return content[:1500] or None
    except Exception:  # noqa: BLE001
        log.exception("prompt writer failed")
        return None


def build_poster_prompt(request: str, brief: str | None, edits: list[str] | None = None) -> str:
    """The admin's own words always go in, so no text is lost even if the brief missed something."""
    parts = [POSTER_RULES]
    if brief:
        parts.append(f"Design brief: {brief}")
    parts.append(f"The admin's request (in Persian), with the exact texts to show on the poster:\n{request}")
    if edits:
        parts.append("Changes the admin asked for after seeing earlier versions (apply all of them; "
                     "they override the request where they conflict):\n" + "\n".join(f"- {e}" for e in edits))
    return "\n\n".join(parts)


EDIT_RULES = (
    "Edit this Persian (Farsi) advertising poster. Apply ONLY the change described below and keep everything else "
    "the same: layout, background, colors, style, and every Persian text exactly as it is (same spelling, correctly "
    "connected right-to-left letters, Persian digits), unless the change is about that text. If the change gives "
    "new Persian text, write it exactly as given.\n\nRequested change (may be in Persian):\n"
)


async def edit_image(client, image: bytes, change: str, base_url: str, api_key: str, model: str,
                     size: str = "1024x1536", quality: str = "high") -> bytes:
    """Calls an OpenAI-compatible /images/edits endpoint with the current poster and the requested change.
    Raises ImageError (with .status) if the provider can't do it, so the caller can fall back to regenerating."""
    if not (base_url and api_key and model):
        raise ImageError("سرویس ساخت تصویر تنظیم نشده است (IMAGE_API_BASE_URL / IMAGE_API_KEY / IMAGE_MODEL).")
    data = {"model": model, "prompt": EDIT_RULES + change, "n": "1"}
    if size:
        data["size"] = size
    if quality:
        data["quality"] = quality
    r = await client.post(base_url.rstrip("/") + "/images/edits", headers={"Authorization": f"Bearer {api_key}"},
                          data=data, files={"image": ("poster.png", image, "image/png")}, timeout=300)
    return await _read_image(client, r)


async def _read_image(client, r) -> bytes:
    if r.status_code >= 400:
        raise ImageError(f"خطای سرویس تصویر ({r.status_code}): {r.text[:300]}", r.status_code)
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


# ---------------------------------------------------------------- image API
async def generate_image(client, prompt: str, base_url: str, api_key: str, model: str,
                         size: str = "1024x1536", quality: str = "high") -> bytes:
    """Calls an OpenAI-compatible /images/generations endpoint (GapGPT, OpenAI, ...)."""
    if not (base_url and api_key and model):
        raise ImageError("سرویس ساخت تصویر تنظیم نشده است (IMAGE_API_BASE_URL / IMAGE_API_KEY / IMAGE_MODEL).")
    url = base_url.rstrip("/") + "/images/generations"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {"model": model, "prompt": prompt, "size": size, "n": 1}
    if quality:
        body["quality"] = quality
    r = await client.post(url, headers=headers, json=body, timeout=300)
    if r.status_code in TEMPORARY:
        # GapGPT's upstream provider often rejects one request and accepts the next one
        log.warning("image API returned %s; retrying once: %s", r.status_code, r.text[:200])
        await asyncio.sleep(RETRY_WAIT)
        r = await client.post(url, headers=headers, json=body, timeout=300)
    if r.status_code == 400 and ("quality" in body or size != "1024x1024"):
        # some models/providers reject these options – retry with the plain defaults
        log.warning("image API rejected size/quality (%s); retrying with defaults", r.text[:200])
        r = await client.post(url, headers=headers, json={"model": model, "prompt": prompt, "n": 1,
                                                           "size": "1024x1024"}, timeout=300)
    return await _read_image(client, r)


def to_jpeg(data: bytes) -> bytes:
    """Re-encodes the model's PNG as a good JPEG (smaller upload, no crop, no overlay)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=93)
    return out.getvalue()


# ---------------------------------------------------------------- old method (kept, not used):
# AI background + the bot drawing the text in a box with a Persian font.
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
