"""
Розклад публікацій каналу: коли саме виходитиме наступний допис.

Два режими:
  * manual — користувач сам задає дні тижня й години (наприклад, «пн, ср, пт о 09:00
    і 18:30») у своєму часовому поясі;
  * auto — система бере години піків кривої активності цільового сегмента
    (ті самі, що показує планувальник), per_day найкращих на добу.

У черзу допис ставиться на найближчий вільний слот: слоти, ближчі за
min_gap_minutes до вже запланованих/опублікованих дописів каналу, пропускаються.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.audience.profiles import peak_hours

DEFAULT_SCHEDULE: Dict[str, Any] = {
    "mode": "auto",
    "slots": [],
    "utc_offset": 2,
    "segment_id": None,
    "per_day": 2,
    "min_gap_minutes": 60,
}

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def normalize_schedule(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {**DEFAULT_SCHEDULE, **(raw or {})}


def _candidates(
    schedule: Dict[str, Any],
    now: datetime,
    activity_hours: Optional[List[float]],
    horizon_days: int,
) -> List[datetime]:
    offset = timedelta(hours=int(schedule["utc_offset"]))
    local_today = (now + offset).date()
    out: List[datetime] = []

    if schedule["mode"] == "manual":
        times = [
            (int(s["weekday"]), *map(int, str(s["time"]).split(":")))
            for s in schedule.get("slots") or []
        ]
    else:
        hours = peak_hours(activity_hours or [], top=max(int(schedule["per_day"]), 1))
        times = [(wd, h, 0) for wd in range(7) for h in hours]

    for day in range(horizon_days + 1):
        date = local_today + timedelta(days=day)
        for weekday, hour, minute in times:
            if date.weekday() != weekday:
                continue
            local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=timezone.utc)
            utc = local - offset
            if utc > now:
                out.append(utc)
    return sorted(out)


def next_free_slot(
    schedule: Dict[str, Any],
    taken: List[datetime],
    now: Optional[datetime] = None,
    activity_hours: Optional[List[float]] = None,
    horizon_days: int = 21,
) -> Optional[datetime]:
    """Найближчий слот розкладу, не ближчий за min_gap до вже зайнятих (усі часи — UTC)."""
    schedule = normalize_schedule(schedule)
    now = now or datetime.now(timezone.utc)
    gap = timedelta(minutes=int(schedule["min_gap_minutes"]))
    taken = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in taken]
    for slot in _candidates(schedule, now, activity_hours, horizon_days):
        if all(abs(slot - t) >= gap for t in taken):
            return slot
    return None


def describe_slot(slot: datetime, utc_offset: int) -> str:
    local = slot + timedelta(hours=utc_offset)
    return f"{WEEKDAY_LABELS[local.weekday()]} {local:%d.%m} at {local:%H:%M}"
