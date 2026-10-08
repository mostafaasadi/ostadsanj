import jdatetime
from datetime import datetime


def to_jalali(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdt.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return None


def to_jalali_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdt.strftime("%Y/%m/%d")
    except Exception:
        return None


def to_jalali_year_month(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    try:
        jdt = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdt.strftime("%Y-%m")
    except Exception:
        return None