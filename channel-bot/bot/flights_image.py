"""Renders IKA departures as a clean, airport-board style image (1080 px wide, RTL layout)."""
import io
from datetime import datetime

from PIL import Image, ImageDraw

from . import textrender as tr
from .persian import jalali_date, to_fa

W, M = 1080, 48
HEADER_H, COLHEAD_H, ROW_H, FOOTER_H = 212, 54, 104, 96

BG = (13, 21, 36)
ROW_ALT = (18, 28, 46)
LINE = (36, 49, 72)
TEXT = (241, 245, 249)
MUTED = (140, 156, 180)
ACCENT = (245, 179, 66)

COLORS = {
    "ok": (52, 211, 153),
    "delay": (251, 191, 36),
    "cancel": (248, 113, 113),
    "departed": (148, 163, 184),
    "boarding": (96, 165, 250),
}

STATUS_FA = [
    ("cancel", "لغو شد"), ("departed", "پرواز کرد"), ("final call", "آخرین فراخوان"),
    ("boarding", "در حال سوار شدن"), ("gate closed", "گیت بسته شد"), ("gate open", "گیت باز است"),
    ("check-in now", "در حال پذیرش"), ("check-in closed", "پذیرش بسته شد"),
    ("wait for announce", "منتظر اعلام بعدی"), ("delayed", "تأخیر"), ("on time", "به‌موقع"),
    ("scheduled", "طبق برنامه"), ("diverted", "تغییر مسیر"),
]

TERMINALS = {"one": "۱", "1": "۱", "two": "۲", "2": "۲", "salam": "سلام"}

# column right edges (RTL table: time on the right, status on the left)
X_TIME, X_FLIGHT, X_DEST, X_GATE_C, X_STATUS = W - M, 856, 612, 372, 300
FLIGHT_W, DEST_W = 220, 205


def _fa(piece: str) -> str:
    low = piece.lower().strip()
    return next((fa for en, fa in STATUS_FA if en in low), piece.strip())


def status_fa(status: str) -> str:
    """'Delayed - Wait for Announce' -> 'تأخیر - منتظر اعلام بعدی'"""
    return " - ".join(p for p in (_fa(x) for x in status.split(" - ")) if p)


def category(status: str) -> str:
    s = status.lower()
    if "cancel" in s:
        return "cancel"
    if "delay" in s or "wait" in s:
        return "delay"
    if "departed" in s or "airborne" in s:
        return "departed"
    if "board" in s or "final" in s or "gate" in s:
        return "boarding"
    return "ok"


def _blend(c, base, a):
    return tuple(int(c[i] * a + base[i] * (1 - a)) for i in range(3))


def _fit(d, text, font, max_w):
    if tr.width(d, text, font) <= max_w:
        return text
    while text and tr.width(d, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def render_flights(flights: list[dict], now: datetime, bold_font: str | None, regular_font: str | None,
                   signature: str = "", max_rows: int = 30, source: str = "ikac.ir") -> bytes:
    rows = flights[:max_rows]
    extra = len(flights) - len(rows)
    body_h = max(1, len(rows)) * ROW_H
    H = HEADER_H + COLHEAD_H + body_h + FOOTER_H

    B = lambda s: tr.load_font(bold_font, s)          # noqa: E731
    R = lambda s: tr.load_font(regular_font or bold_font, s)  # noqa: E731
    f_title, f_sub, f_date, f_upd = B(54), B(28), B(30), R(24)
    f_col, f_time, f_old, f_code, f_air, f_dest, f_gate, f_pill, f_rem, f_foot = (
        R(23), B(40), R(22), B(30), R(20), B(30), B(32), B(22), R(19), R(22))

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ---- header
    d.rectangle((0, 0, W, 8), fill=ACCENT)
    tr.draw(d, (W - M, 48), "پروازهای خروجی", f_title, TEXT, "right")
    # Persian name and "(IKA)" are drawn separately so the brackets can never be flipped by bidi
    airport_fa = "فرودگاه بین‌المللی امام خمینی"
    tr.draw(d, (W - M, 124), airport_fa, f_sub, MUTED, "right")
    code_x = W - M - tr.width(d, airport_fa, f_sub) - 12
    d.text((code_x - d.textlength("(IKA)", font=f_sub), 124), "(IKA)", font=f_sub, fill=MUTED)
    tr.draw(d, (M, 58), jalali_date(now), f_date, TEXT, "left")
    tr.draw(d, (M, 104), f"به‌روزرسانی {to_fa(now.strftime('%H:%M'))}", f_upd, MUTED, "left")
    d.rounded_rectangle((M, 150, M + 64, 158), radius=4, fill=ACCENT)

    # ---- column headers
    y = HEADER_H
    for x, label, align in ((X_TIME, "ساعت", "right"), (X_FLIGHT, "پرواز", "right"), (X_DEST, "مقصد", "right"),
                            (X_GATE_C, "گیت", "center"), (X_STATUS, "وضعیت", "right")):
        tr.draw(d, (x, y + 12), label, f_col, MUTED, align)
    d.line((M, y + COLHEAD_H - 2, W - M, y + COLHEAD_H - 2), fill=LINE, width=2)

    # ---- rows
    y = HEADER_H + COLHEAD_H
    if not rows:
        tr.draw(d, (W / 2, y + 34), "امروز پرواز خروجی ثبت نشده است.", f_dest, MUTED, "center")
    for i, f in enumerate(rows):
        top = y + i * ROW_H
        if i % 2:
            d.rectangle((0, top, W, top + ROW_H), fill=ROW_ALT)

        status = f.get("status", "")
        cat = category(status)
        color = COLORS[cat]
        faded = cat == "departed"
        main_c = MUTED if faded else TEXT

        # time (new time in accent colour, scheduled time crossed out underneath)
        t, sched = f.get("time", ""), f.get("scheduled", "")
        changed = sched and t and sched != t
        tr.draw(d, (X_TIME, top + 16), t or sched, f_time, ACCENT if changed and not faded else main_c, "right")
        if changed:
            w = tr.width(d, sched, f_old)
            tr.draw(d, (X_TIME, top + 66), sched, f_old, MUTED, "right")
            d.line((X_TIME - w - 2, top + 79, X_TIME + 2, top + 79), fill=MUTED, width=2)

        # flight code + airline
        tr.draw(d, (X_FLIGHT, top + 18), _fit(d, f.get("flight", ""), f_code, FLIGHT_W), f_code, main_c, "right")
        airline = (f.get("airline") or "").title()
        tr.draw(d, (X_FLIGHT, top + 60), _fit(d, airline, f_air, FLIGHT_W), f_air, MUTED, "right")

        # destination (shrinks long city names before cutting them)
        dest, fd = f.get("dest", ""), f_dest
        for size in (30, 27, 24):
            fd = B(size)
            if tr.width(d, dest, fd) <= DEST_W:
                break
        tr.draw(d, (X_DEST, top + 18 + (30 - fd.size) // 2), _fit(d, dest, fd, DEST_W), fd, main_c, "right")
        term = (f.get("terminal") or "").strip()
        if term:
            tr.draw(d, (X_DEST, top + 60), f"ترمینال {TERMINALS.get(term.lower(), term)}", f_air, MUTED, "right")

        # gate
        tr.draw(d, (X_GATE_C, top + 22), to_fa(f.get("gate") or "—"), f_gate, main_c, "center")

        # status pill + remark
        parts = [p.strip() for p in status.split(" - ") if p.strip()]
        pill_text = _fa(parts[0]) if parts else "—"
        remark = " - ".join(_fa(p) for p in parts[1:])
        pw = tr.width(d, pill_text, f_pill) + 36
        pill = (X_STATUS - pw, top + 18, X_STATUS, top + 62)
        d.rounded_rectangle(pill, radius=22, fill=_blend(color, BG, 0.20))
        tr.draw(d, (X_STATUS - pw / 2, top + 26), pill_text, f_pill, color, "center")
        if remark:
            tr.draw(d, (X_STATUS, top + 70), _fit(d, remark, f_rem, X_STATUS - M), f_rem, MUTED, "right")

        d.line((M, top + ROW_H - 1, W - M, top + ROW_H - 1), fill=LINE, width=1)

    # ---- footer
    fy = HEADER_H + COLHEAD_H + body_h
    d.rectangle((0, fy, W, H), fill=(10, 16, 28))
    tr.draw(d, (W - M, fy + 34), f"منبع: {source}", f_foot, MUTED, "right")
    if signature:
        tr.draw(d, (M, fy + 34), signature, f_foot, ACCENT, "left")
    if extra > 0:
        tr.draw(d, (W / 2, fy + 34), f"+{to_fa(extra)} پرواز دیگر", f_foot, MUTED, "center")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
