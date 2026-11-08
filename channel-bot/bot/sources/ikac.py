"""Departure flights from the IKA airport website (ikac.ir).

The page HTML embeds the flight data as a JavaScript object:

    window.flightsJSON = {
        arrivals:   [ { id: '...', airline: '...', ... }, ... ],
        departures: [ { id: '28129', iata_code: 'CPN7918', airline: 'CASPIAN AIRLINES',
                        airport: 'Najaf', scheduled_time: '10:00:00', actual_time: '14:08:00',
                        estimated_time: '12:00:00', gate: '9', dispatch_status: 'Departed',
                        remark: 'Check-in Now', terminal: 'Salam', ... }, ... ]
    }

and the visible list is built from it in the browser. We read the departures array directly.
"""
import re


class SourceError(Exception):
    pass


_PAIR = re.compile(r"(\w+)\s*:\s*'((?:[^'\\]|\\.)*)'")


def _extract_array(text: str, key: str) -> str | None:
    """Returns the text between the brackets of `key: [ ... ]`, respecting quotes and nesting."""
    m = re.search(rf"\b{key}\s*:\s*\[", text)
    if not m:
        return None
    i, depth, quote = m.end(), 1, None
    start = i
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


def _objects(block: str) -> list[dict]:
    items, depth, start, quote = [], 0, None, None
    for i, ch in enumerate(block):
        if quote:
            if ch == quote and block[i - 1] != "\\":
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                body = block[start + 1:i]
                items.append({k: v.replace("\\'", "'").strip() for k, v in _PAIR.findall(body)})
    return items


def _hhmm(value: str) -> str:
    m = re.match(r"(\d{1,2}):(\d{2})", value or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def parse_departures(html: str) -> list[dict]:
    block = _extract_array(html, "departures")
    if block is None:
        raise SourceError("اطلاعات پروازها در سایت ikac.ir پیدا نشد؛ احتمالاً ساختار سایت تغییر کرده "
                          "یا سایت دسترسی این سرور را بسته است.")
    flights = []
    for raw in _objects(block):
        scheduled = _hhmm(raw.get("scheduled_time", ""))
        current = _hhmm(raw.get("actual_time", "")) or _hhmm(raw.get("estimated_time", "")) or scheduled
        status = raw.get("dispatch_status", "")
        if raw.get("remark"):
            status = f"{status} - {raw['remark']}" if status else raw["remark"]
        flights.append({
            "time": current,
            "scheduled": scheduled,
            "date": raw.get("scheduled_date", ""),
            "flight": raw.get("iata_code") or raw.get("flight_number", ""),
            "airline": raw.get("airline", ""),
            "dest": raw.get("airport", "") + (f" / {raw['airport_two']}" if raw.get("airport_two") else ""),
            "terminal": raw.get("terminal", ""),
            "gate": raw.get("gate", ""),
            "status": status,
        })
    flights = [f for f in flights if f["flight"] or f["dest"]]
    flights.sort(key=lambda f: (f["date"], f["scheduled"]))
    return flights


async def fetch_departures(client, url: str) -> list[dict]:
    r = await client.get(url)
    r.raise_for_status()
    return parse_departures(r.text)
