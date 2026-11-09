"""IKA departures from Flightradar24 (works from servers outside Iran, unlike ikac.ir).

Flightradar24's airport pages (flightradar24.com/data/airports/ika/departures) are filled in by the browser
from this JSON endpoint. It is not an official public API: it can change, rate-limit or block a server
without notice. The ikac.ir code is kept, commented out, in jobs.build_flights.

    result.response.airport.pluginData.schedule.departures.data[i].flight = {
        identification: {number: {default: 'IR713'}},
        status: {text: 'Estimated dep 14:30', generic: {status: {text: 'estimated', ...}}},
        airline: {name: 'Iran Air'},
        airport: {origin: {info: {terminal, gate}}, destination: {name, code: {iata}, position: {region: {city}}}},
        time: {scheduled: {departure: <unix>}, estimated: {departure}, real: {departure}},
    }
"""
from datetime import datetime, timedelta

from .ikac import SourceError

API_URL = "https://api.flightradar24.com/common/v1/airport.json"
HEADERS = {"Referer": "https://www.flightradar24.com/", "Origin": "https://www.flightradar24.com",
           "Accept": "application/json"}
DELAY_MIN = 15          # estimated this many minutes later than scheduled -> shown as delayed


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _hhmm(ts, tz) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M") if ts else ""


def _status(generic: str, sched, est, real) -> str:
    """Flightradar24 status -> the English words the image/status_fa already translate."""
    g = (generic or "").lower()
    if "cancel" in g:
        return "Cancelled"
    if "divert" in g:
        return "Diverted"
    if real or g in ("departed", "landed", "en-route", "airborne"):
        return "Departed"
    if g == "delayed" or (sched and est and est - sched >= DELAY_MIN * 60):
        return "Delayed"
    if est or g == "estimated":
        return "On Time"
    return "Scheduled"


def parse_departures(data: dict, tz, now: datetime, hours_back: int = 1, hours_ahead: int = 24) -> list[dict]:
    items = _get(data, "result", "response", "airport", "pluginData", "schedule", "departures", "data")
    if items is None:
        raise SourceError("اطلاعات پروازها از Flightradar24 دریافت نشد؛ احتمالاً پاسخ سایت تغییر کرده "
                          "یا دسترسی این سرور محدود شده است.")
    start = now - timedelta(hours=hours_back)
    end = now + timedelta(hours=hours_ahead)
    flights = []
    for item in items:
        f = item.get("flight") if isinstance(item, dict) else None
        if not f:
            continue
        sched = _get(f, "time", "scheduled", "departure")
        if not sched:
            continue
        when = datetime.fromtimestamp(sched, tz)
        if not start <= when <= end:
            continue
        est = _get(f, "time", "estimated", "departure")
        real = _get(f, "time", "real", "departure")
        dest = _get(f, "airport", "destination") or {}
        city = _get(dest, "position", "region", "city") or dest.get("name") or _get(dest, "code", "iata") or ""
        origin = _get(f, "airport", "origin", "info") or {}
        flights.append({
            "time": _hhmm(real or est or sched, tz),
            "scheduled": _hhmm(sched, tz),
            "date": when.date().isoformat(),
            "sort": sched,
            "flight": _get(f, "identification", "number", "default") or _get(f, "identification", "callsign") or "",
            "airline": _get(f, "airline", "name") or "",
            "dest": city,
            "terminal": str(origin.get("terminal") or ""),
            "gate": str(origin.get("gate") or ""),
            "status": _status(_get(f, "status", "generic", "status", "text"), sched, est, real),
        })
    flights.sort(key=lambda x: x["sort"])
    return flights


async def fetch_departures(client, tz) -> list[dict]:
    now = datetime.now(tz)
    params = {
        "code": "ika",
        "plugin[]": "schedule",
        "plugin-setting[schedule][mode]": "departures",
        "plugin-setting[schedule][timestamp]": int((now - timedelta(hours=1)).timestamp()),
        "page": 1,
        "limit": 100,
    }
    r = await client.get(API_URL, params=params, headers=HEADERS)
    if r.status_code in (401, 402, 403, 429, 451):
        raise SourceError(f"Flightradar24 درخواست این سرور را رد کرد (کد {r.status_code}).")
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as e:
        raise SourceError("پاسخ Flightradar24 قابل خواندن نبود.") from e
    return parse_departures(data, tz, now)
