"""Departure flights from the IKA airport website (ikac.ir).

The page layout could not be inspected ahead of time (the site refuses automated readers),
so this parser is deliberately generic: it looks for an HTML table that has a destination
column and maps columns by their header words (English or Persian). If the site renders
flights with JavaScript, parsing fails with a clear error and the admin is notified.
"""
import re

from bs4 import BeautifulSoup

from ..persian import to_en

HEADER_KEYS = {
    "time": ("time", "std", "schedule", "زمان", "ساعت"),
    "date": ("date", "تاریخ"),
    "flight": ("flight", "پرواز", "شماره"),
    "airline": ("airline", "carrier", "ایرلاین", "شرکت", "هواپیمایی"),
    "dest": ("destination", "to", "city", "مقصد"),
    "origin": ("origin", "from", "مبدا", "مبدأ"),
    "status": ("status", "remark", "وضعیت"),
    "gate": ("gate", "counter", "گیت", "کانتر"),
    "type": ("type", "nature", "نوع"),
}
DEPARTURE_WORDS = ("depart", "خروجی", "پرواز خروجی", "عزیمت")
ARRIVAL_WORDS = ("arriv", "ورودی", "پرواز ورودی")


class SourceError(Exception):
    pass


def _cell_text(cell) -> str:
    return " ".join(to_en(cell.get_text(" ")).split())


def _map_headers(headers: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for i, h in enumerate(headers):
        low = h.lower()
        for key, words in HEADER_KEYS.items():
            if key in mapping.values():
                continue
            if any(w == low or w in low.split() or (len(w) > 3 and w in low) for w in words):
                mapping[i] = key
                break
    return mapping


def _context_text(table) -> str:
    """Text around a table (caption, previous heading, tab title) used to tell departures from arrivals."""
    parts = []
    if table.caption:
        parts.append(table.caption.get_text(" "))
    prev = table.find_previous(["h1", "h2", "h3", "h4", "h5", "a", "li", "span"])
    if prev:
        parts.append(prev.get_text(" "))
    for parent in table.parents:
        ident = " ".join(filter(None, [parent.get("id", ""), " ".join(parent.get("class", []) or [])]))
        if ident:
            parts.append(ident)
        if parent.name == "body":
            break
    return to_en(" ".join(parts)).lower()


def parse_departures(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    best: list[dict] = []
    best_score = -1
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        mapping = _map_headers([_cell_text(c) for c in header_cells])
        if "dest" not in mapping.values() and "flight" not in mapping.values():
            continue
        flights = []
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            item = {key: _cell_text(cells[i]) for i, key in mapping.items() if i < len(cells)}
            if not any(item.values()):
                continue
            t = item.get("type", "").lower()
            if t and any(w in t for w in ARRIVAL_WORDS):
                continue
            flights.append(item)
        if not flights:
            continue
        ctx = _context_text(table)
        score = len(flights)
        score += 1000 if any(w in ctx for w in DEPARTURE_WORDS) else 0
        score -= 1000 if any(w in ctx for w in ARRIVAL_WORDS) and not any(w in ctx for w in DEPARTURE_WORDS) else 0
        score += 500 if "dest" in mapping.values() and "origin" not in mapping.values() else 0
        if score > best_score:
            best, best_score = flights, score
    if not best:
        raise SourceError(
            "No departures table found on ikac.ir. The page may load flights with JavaScript, "
            "block foreign servers, or have changed layout."
        )
    # sort by time if we have HH:MM values
    def key(f):
        m = re.search(r"(\d{1,2}):(\d{2})", f.get("time", ""))
        return (int(m.group(1)), int(m.group(2))) if m else (99, 99)
    return sorted(best, key=key)


async def fetch_departures(client, url: str) -> list[dict]:
    r = await client.get(url)
    r.raise_for_status()
    return parse_departures(r.text)
