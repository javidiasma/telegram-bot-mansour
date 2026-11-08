"""Small helpers for Persian digits and Jalali dates."""
from datetime import datetime

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)
_TO_FA = str.maketrans("0123456789", FA_DIGITS)

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
WEEKDAYS_FROM_SAT = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def to_en(text: str) -> str:
    return text.translate(_TO_EN)


def to_fa(text) -> str:
    return str(text).translate(_TO_FA)


def fmt_num(value: float) -> str:
    """265400 -> ۲۶۵,۴۰۰"""
    if float(value).is_integer():
        return to_fa(f"{int(value):,}")
    return to_fa(f"{value:,.2f}")


def jalali_date(dt: datetime) -> str:
    """e.g. یکشنبه ۱۷ آبان ۱۴۰۵ (falls back to the Gregorian date if jdatetime is missing)."""
    try:
        import jdatetime
    except ImportError:
        return dt.strftime("%Y-%m-%d")
    j = jdatetime.date.fromgregorian(date=dt.date())
    return f"{WEEKDAYS_FROM_SAT[j.weekday()]} {to_fa(j.day)} {MONTHS[j.month - 1]} {to_fa(j.year)}"
