"""Dollar rate from navasan.net.

The navasan.net home page HTML contains placeholder numbers that JavaScript replaces later,
so we read the daily archive page (dayRates.php?item=usd_sell), which carries the real
prices in plain HTML. If NAVASAN_API_KEY is set, the official API is used instead.
"""
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from ..persian import to_en

PAGE_URL = "https://www.navasan.net/dayRates.php?item={item}"

ITEM_TITLES = {
    "usd_sell": "دلار تهران (فروش)",
    "usd_buy": "دلار تهران (خرید)",
    "usd": "دلار آمریکا (اسکناس)",
    "eur": "یورو",
}

_NUM = r"(\d{1,3}(?:,\d{3})+|\d{4,})"            # 265,400 or 265400
_TIME = re.compile(r"(\d{1,2})\s*:\s*(\d{2})")


class SourceError(Exception):
    pass


@dataclass
class Rate:
    title: str
    price: float                      # toman
    change_pct: float | None = None
    change_abs: float | None = None
    high: float | None = None
    low: float | None = None
    open: float | None = None
    updated: str | None = None        # "12:52"
    source: str = "navasan.net"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _number_after(lines: list[str], label: str, start: int = 0) -> tuple[float | None, int]:
    """First big number appearing on the line of `label` or within the next few lines."""
    for i in range(start, len(lines)):
        if label in lines[i]:
            for j in range(i, min(i + 4, len(lines))):
                m = re.search(_NUM, lines[j].replace(label, ""))
                if m:
                    return _num(m.group(1)), j
            return None, i
    return None, start


def parse_day_rates(html: str, item: str = "usd_sell") -> Rate:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [to_en(l.strip()) for l in soup.get_text("\n").splitlines() if l.strip()]

    price, idx = _number_after(lines, "آخرین نرخ")
    if price is None:
        # fallback: last row of the intraday table (time | price | change)
        rows = [r for r in soup.select("table tr") if _TIME.search(to_en(r.get_text(" ")))]
        for r in reversed(rows):
            m = re.search(_NUM, to_en(r.get_text(" ")))
            if m:
                price = _num(m.group(1))
                break
    if price is None or price < 1000:
        raise SourceError("Could not find the dollar price on navasan.net (page layout may have changed).")

    rate = Rate(title=ITEM_TITLES.get(item, item), price=price)
    # change % and update time usually sit right after the latest price
    for line in lines[idx: idx + 4]:
        if rate.change_pct is None:
            m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", line)
            if m:
                rate.change_pct = float(m.group(1))
        if rate.updated is None:
            m = _TIME.search(line)
            if m:
                rate.updated = f"{int(m.group(1)):02d}:{m.group(2)}"
    rate.high, _ = _number_after(lines, "بیشترین")
    rate.low, _ = _number_after(lines, "کمترین")
    rate.open, _ = _number_after(lines, "شروع بازار")
    return rate


async def fetch_rate(client, item: str = "usd_sell", api_key: str = "", api_url: str = "") -> Rate:
    if api_key:
        r = await client.get(api_url, params={"api_key": api_key})
        r.raise_for_status()
        data = r.json().get(item)
        if not data:
            raise SourceError(f"Navasan API returned no '{item}' item.")
        return Rate(
            title=ITEM_TITLES.get(item, item),
            price=_num(str(data["value"])),
            change_abs=float(data["change"]) if data.get("change") not in (None, "") else None,
            updated=str(data.get("date", ""))[-8:-3] or None,
            source="navasan.net",
        )
    r = await client.get(PAGE_URL.format(item=item))
    r.raise_for_status()
    return parse_day_rates(r.text, item)
