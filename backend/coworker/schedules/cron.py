"""Cron expression math via ``croniter`` (industry-standard).

Only 5-field POSIX expressions are supported (no seconds), matching the product
decision. Timezone handling uses the schedule's IANA timezone; croniter is fed a
tz-aware base and returns tz-aware datetimes, so DST transitions behave like
every mainstream scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def timezone_ok(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - fall back to UTC on a bad stored tz
        return ZoneInfo("UTC")


def cron_ok(expr: str) -> bool:
    """True when ``expr`` is a valid 5-field cron expression."""
    text = (expr or "").strip()
    if len(text.split()) != 5:
        return False
    return bool(croniter.is_valid(text))


def next_run(expr: str, tz_name: str, base: datetime | None = None) -> datetime:
    """The next occurrence strictly after ``base`` (default: now) in ``tz_name``."""
    tz = resolve_tz(tz_name)
    anchor = base.astimezone(tz) if base is not None else datetime.now(tz)
    return croniter((expr or "").strip(), anchor).get_next(datetime)


def prev_run(expr: str, tz_name: str, base: datetime | None = None) -> datetime:
    tz = resolve_tz(tz_name)
    anchor = base.astimezone(tz) if base is not None else datetime.now(tz)
    return croniter((expr or "").strip(), anchor).get_prev(datetime)


def preview(expr: str, tz_name: str, count: int = 5, base: datetime | None = None) -> list[str]:
    """ISO timestamps of the next ``count`` occurrences."""
    tz = resolve_tz(tz_name)
    anchor = base.astimezone(tz) if base is not None else datetime.now(tz)
    it = croniter((expr or "").strip(), anchor)
    out: list[str] = []
    for _ in range(max(1, min(count, 20))):
        out.append(it.get_next(datetime).isoformat())
    return out


def describe(expr: str) -> str:
    """A short human-readable summary (English fallback for exotic expressions)."""
    fields = (expr or "").strip().split()
    if len(fields) != 5:
        return expr
    minute, hour, dom, month, dow = fields
    try:
        if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
            return "Every minute"
        if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
            return f"Every {minute[2:]} minutes"
        time_part = None
        if minute.isdigit() and hour.isdigit():
            time_part = f"{int(hour):02d}:{int(minute):02d}"
        if time_part and dom == "*" and month == "*" and dow == "*":
            return f"Every day at {time_part}"
        if time_part and dom == "*" and month == "*" and dow != "*":
            days = ", ".join(WEEKDAY_NAMES[int(d) % 7] for d in dow.split(",") if d.strip().isdigit())
            return f"Weekly on {days} at {time_part}" if days else f"Weekly at {time_part}"
        if time_part and dom != "*" and month == "*" and dow == "*":
            return f"Monthly on day {dom} at {time_part}"
        if time_part and month != "*" and dom != "*" and dow == "*":
            return f"Yearly on {month}/{dom} at {time_part}"
        if hour == "*" and minute.isdigit():
            return f"Hourly at minute {int(minute)}"
    except Exception:  # noqa: BLE001
        pass
    return expr


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def clamp_sleep(seconds: float, cap: float) -> float:
    return max(1.0, min(float(seconds), float(cap)))
