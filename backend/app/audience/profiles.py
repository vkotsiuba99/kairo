"""
Ручний портрет цільової аудиторії.

Автоматична сегментація за телеметрією працює лише тоді, коли телеметрія вже
накопичена. Поки її немає (а на старті її немає завжди), сегмент задає сам
користувач — вибором із фіксованих варіантів, без вільного вводу. З цього
вибору детерміновано виводяться дві речі:

  * крива добової активності (24 ваги) — її використовує планувальник;
  * текстовий опис інтересів — з нього будується вектор сегмента.

Саме тому «офісний працівник» і «фрилансер» отримують різний час публікації:
різниця не декларується в тексті роботи, а рахується з їхніх кривих.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Довідники варіантів (усе, що бачить користувач у формі)
# ---------------------------------------------------------------------------

OCCUPATIONS = {
    "school": "School, classes until 3–4 pm",
    "student": "University students",
    "office_9_18": "Office job, 9 to 6",
    "remote": "Remote work",
    "shift": "Shift work",
    "night_shift": "Night shifts",
    "blue_collar": "Manufacturing, early mornings",
    "service": "Service sector, evening shifts",
    "freelance": "Freelance, flexible hours",
    "business": "Business owners",
    "military": "Military service",
    "parental_leave": "Parental leave",
    "retired": "Retired",
    "searching": "Job seekers",
}

AGE_GROUPS = {
    "under_18": "under 18",
    "18_24": "18–24",
    "25_34": "25–34",
    "35_44": "35–44",
    "45_plus": "45 and older",
}

# Регіон → (назва, стандартне зміщення UTC, правило літнього часу)
REGIONS = {
    "ua": ("Ukraine", 2, "eu"),
    "eu": ("Europe (CET)", 1, "eu"),
    "na": ("North America (Eastern)", -5, "us"),
    "me_asia": ("Middle East & Asia (UTC+4)", 4, None),
}

LANGUAGES = {
    "en": "English",
    "uk": "Ukrainian",
}

DEVICES = {
    "mobile": "Mostly smartphone",
    "desktop": "Mostly desktop",
    "mixed": "Mixed",
}

INTERESTS = {
    # Технології
    "dev": "Software development",
    "ai": "Artificial intelligence",
    "devops": "Infrastructure & DevOps",
    "cybersec": "Cybersecurity",
    "gadgets": "Gadgets & hardware",
    "gamedev": "Game development",
    # Творчість
    "music": "Music",
    "guitar": "Playing instruments",
    "country": "Country & folk",
    "hiphop": "Hip-hop",
    "painting": "Painting",
    "photo": "Photography",
    "design": "Design",
    "writing": "Writing & literature",
    "movies": "Movies & TV series",
    "anime": "Anime",
    # Спорт і рух
    "football": "Football",
    "karate": "Martial arts",
    "fitness": "Fitness & gym",
    "running": "Running",
    "cycling": "Cycling",
    "chess": "Chess",
    "esports": "Esports",
    "games": "Video games",
    # Природа й хобі
    "fishing": "Fishing",
    "hunting": "Hunting",
    "garden": "Gardening",
    "pets": "Pets",
    "travel": "Travel",
    "cooking": "Cooking",
    "auto": "Cars",
    "diy": "DIY",
    # Життя й суспільство
    "career": "Career",
    "finance": "Personal finance",
    "startups": "Startups & product",
    "education": "Education & learning",
    "science": "Science",
    "history": "History",
    "psychology": "Psychology",
    "health": "Health",
    "parenting": "Parenting",
    "volunteering": "Volunteering",
    "fashion": "Fashion & style",
    "humor": "Humor",
}

# Годинні зсуви, які дають самі інтереси. Час активності не питається у
# користувача — він виводиться з того, чим аудиторія живе.
INTEREST_SHIFTS: Dict[str, Dict[str, float]] = {
    "games": {"22": 1.5, "23": 1.5, "0": 1.4, "1": 1.3, "19": 1.2, "20": 1.2},
    "esports": {"21": 1.4, "22": 1.5, "23": 1.5, "0": 1.4, "1": 1.3},
    "gamedev": {"21": 1.3, "22": 1.4, "23": 1.3},
    "anime": {"22": 1.4, "23": 1.4, "0": 1.3},
    "fishing": {"4": 1.8, "5": 1.9, "6": 1.7, "7": 1.4, "20": 1.2},
    "hunting": {"4": 1.7, "5": 1.8, "6": 1.6, "19": 1.2},
    "garden": {"6": 1.5, "7": 1.5, "8": 1.4, "18": 1.3, "19": 1.3},
    "fitness": {"6": 1.4, "7": 1.5, "8": 1.3, "19": 1.4, "20": 1.4},
    "running": {"6": 1.5, "7": 1.5, "20": 1.3},
    "karate": {"18": 1.4, "19": 1.5, "20": 1.4},
    "football": {"19": 1.4, "20": 1.5, "21": 1.4},
    "cycling": {"7": 1.3, "17": 1.3, "18": 1.3},
    "music": {"20": 1.3, "21": 1.4, "22": 1.3},
    "guitar": {"20": 1.3, "21": 1.4, "22": 1.3},
    "country": {"19": 1.3, "20": 1.3},
    "hiphop": {"21": 1.3, "22": 1.4, "23": 1.3},
    "movies": {"20": 1.4, "21": 1.5, "22": 1.4},
    "cooking": {"11": 1.3, "12": 1.3, "17": 1.4, "18": 1.5},
    "parenting": {"12": 1.3, "13": 1.3, "21": 1.5, "22": 1.4},
    "pets": {"7": 1.3, "8": 1.3, "19": 1.3, "20": 1.3},
    "auto": {"8": 1.3, "18": 1.4, "19": 1.3},
    "travel": {"12": 1.2, "21": 1.3},
    "education": {"15": 1.3, "16": 1.4, "17": 1.3},
    "psychology": {"21": 1.3, "22": 1.3},
    "finance": {"9": 1.3, "10": 1.3, "18": 1.2},
    "career": {"9": 1.2, "12": 1.3, "19": 1.2},
    "humor": {"12": 1.3, "13": 1.3, "22": 1.3},
    "health": {"7": 1.3, "20": 1.3},
    "volunteering": {"10": 1.2, "18": 1.3},
}

SIZE_OPTIONS = {
    "100": "up to 100 followers",
    "500": "about 500",
    "1000": "about 1,000",
    "5000": "about 5,000",
    "20000": "20,000 or more",
}

SEGMENT_NAMES = [
    "School students",
    "Parents of school students",
    "University students",
    "Young professionals",
    "Office workers",
    "Remote workers",
    "Freelancers",
    "Entrepreneurs & founders",
    "Software developers",
    "Designers",
    "Musicians",
    "Creative communities",
    "Gamers",
    "Esports audience",
    "Amateur athletes",
    "Fishing enthusiasts",
    "Hunters",
    "Gardeners",
    "Home cooks",
    "Car enthusiasts",
    "Travelers",
    "Pet owners",
    "Young parents",
    "Teachers & educators",
    "Healthcare workers",
    "Military & veterans",
    "Volunteers",
    "Retirees",
    "Local business",
    "Big-city residents",
    "Small towns & villages",
    "General audience",
]

# ---------------------------------------------------------------------------
# Базові добові криві за типом зайнятості (індекс = година місцевого часу)
# ---------------------------------------------------------------------------

BASE_CURVES: Dict[str, List[float]] = {
    # Школа: уроки до 15, сплеск одразу після них і перед сном
    "school": [
        0.10, 0.05, 0.02, 0.01, 0.01, 0.02, 0.10, 0.25, 0.15, 0.10, 0.10, 0.15,
        0.20, 0.35, 0.65, 0.85, 0.90, 0.85, 0.80, 0.90, 1.00, 0.95, 0.70, 0.35,
    ],
    "student": [
        0.25, 0.15, 0.08, 0.04, 0.03, 0.04, 0.10, 0.30, 0.45, 0.45, 0.45, 0.50,
        0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.95, 1.00, 0.95, 0.80, 0.50,
    ],
    "office_9_18": [
        0.05, 0.02, 0.01, 0.01, 0.02, 0.05, 0.15, 0.45, 0.55, 0.25, 0.20, 0.25,
        0.65, 0.60, 0.30, 0.25, 0.30, 0.50, 0.80, 0.95, 1.00, 0.90, 0.60, 0.25,
    ],
    "remote": [
        0.10, 0.05, 0.03, 0.02, 0.02, 0.05, 0.15, 0.40, 0.65, 0.70, 0.70, 0.65,
        0.60, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.80, 0.55, 0.25,
    ],
    "shift": [
        0.30, 0.25, 0.20, 0.20, 0.25, 0.35, 0.50, 0.55, 0.50, 0.45, 0.45, 0.50,
        0.55, 0.50, 0.45, 0.45, 0.50, 0.60, 0.70, 0.75, 0.80, 0.80, 0.60, 0.40,
    ],
    # Нічні зміни: активність зміщена на глибоку ніч і час перед виходом
    "night_shift": [
        0.70, 0.75, 0.70, 0.60, 0.50, 0.40, 0.35, 0.30, 0.25, 0.20, 0.20, 0.25,
        0.35, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.70, 0.65, 0.60, 0.65, 0.70,
    ],
    "blue_collar": [
        0.08, 0.04, 0.02, 0.02, 0.10, 0.35, 0.55, 0.45, 0.25, 0.20, 0.20, 0.30,
        0.55, 0.45, 0.30, 0.35, 0.60, 0.80, 0.90, 0.95, 0.85, 0.65, 0.35, 0.15,
    ],
    "service": [
        0.30, 0.20, 0.12, 0.06, 0.04, 0.06, 0.15, 0.35, 0.55, 0.65, 0.65, 0.55,
        0.45, 0.40, 0.35, 0.35, 0.40, 0.45, 0.50, 0.55, 0.65, 0.80, 0.90, 0.60,
    ],
    "freelance": [
        0.15, 0.10, 0.05, 0.03, 0.03, 0.05, 0.15, 0.35, 0.60, 0.75, 0.80, 0.80,
        0.70, 0.65, 0.70, 0.75, 0.80, 0.80, 0.75, 0.80, 0.90, 0.85, 0.60, 0.30,
    ],
    "business": [
        0.10, 0.05, 0.03, 0.02, 0.03, 0.10, 0.30, 0.60, 0.80, 0.85, 0.85, 0.80,
        0.70, 0.70, 0.75, 0.75, 0.70, 0.70, 0.70, 0.75, 0.80, 0.70, 0.45, 0.20,
    ],
    "military": [
        0.15, 0.10, 0.08, 0.06, 0.08, 0.15, 0.30, 0.35, 0.30, 0.25, 0.25, 0.30,
        0.45, 0.40, 0.35, 0.35, 0.40, 0.50, 0.65, 0.80, 0.90, 0.85, 0.60, 0.30,
    ],
    # Догляд за дитиною: вікна під час денного сну і пізно ввечері
    "parental_leave": [
        0.20, 0.12, 0.08, 0.05, 0.05, 0.10, 0.25, 0.35, 0.40, 0.45, 0.50, 0.60,
        0.75, 0.80, 0.70, 0.55, 0.45, 0.40, 0.45, 0.55, 0.75, 0.95, 1.00, 0.55,
    ],
    "retired": [
        0.05, 0.03, 0.02, 0.02, 0.05, 0.20, 0.45, 0.70, 0.85, 0.90, 0.90, 0.85,
        0.70, 0.65, 0.70, 0.75, 0.80, 0.80, 0.75, 0.70, 0.60, 0.40, 0.20, 0.08,
    ],
    "searching": [
        0.20, 0.15, 0.08, 0.04, 0.04, 0.08, 0.20, 0.45, 0.70, 0.80, 0.80, 0.75,
        0.65, 0.60, 0.60, 0.60, 0.60, 0.65, 0.70, 0.75, 0.80, 0.75, 0.55, 0.30,
    ],
}

# Вікова поправка: молодші зміщені у ніч, старші — у ранок.
AGE_SHIFT = {"under_18": 1, "18_24": 2, "25_34": 0, "35_44": -1, "45_plus": -2}


def _shift(curve: List[float], hours: int) -> List[float]:
    if not hours:
        return list(curve)
    return [curve[(i - hours) % 24] for i in range(24)]


def build_activity_hours(profile: Dict[str, Any]) -> List[float]:
    """Повертає 24 нормовані ваги активності сегмента за його портретом."""
    occupation = profile.get("occupation") or "office_9_18"
    curve = list(BASE_CURVES.get(occupation, BASE_CURVES["office_9_18"]))

    curve = _shift(curve, AGE_SHIFT.get(profile.get("age") or "25_34", 0))

    # Інтереси самі зміщують активність: рибалка встає до світанку, геймер
    # сидить за північ. Користувач нічого не вказує руками.
    for interest in profile.get("interests") or []:
        for hour_str, factor in INTEREST_SHIFTS.get(interest, {}).items():
            curve[int(hour_str) % 24] *= factor

    # Мобільна аудиторія активніша у дорозі — вранці та ввечері.
    if (profile.get("device") or "mixed") == "mobile":
        for hour in (7, 8, 9, 18, 19, 20):
            curve[hour] *= 1.2

    peak = max(curve) or 1.0
    return [round(v / peak, 4) for v in curve]


def _last_sunday(year: int, month: int) -> int:
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d.day - (d.weekday() + 1) % 7


def _is_summer_time(rule: Optional[str], now: datetime) -> bool:
    """Чи діє літній час: ЄС — з останньої неділі березня до останньої неділі жовтня (01:00 UTC),
    США — з другої неділі березня до першої неділі листопада (наближено за датою UTC)."""
    y = now.year
    if rule == "eu":
        start = datetime(y, 3, _last_sunday(y, 3), 1, tzinfo=timezone.utc)
        end = datetime(y, 10, _last_sunday(y, 10), 1, tzinfo=timezone.utc)
        return start <= now < end
    if rule == "us":
        first_sun_mar = 1 + (6 - datetime(y, 3, 1).weekday()) % 7
        first_sun_nov = 1 + (6 - datetime(y, 11, 1).weekday()) % 7
        start = datetime(y, 3, first_sun_mar + 7, 7, tzinfo=timezone.utc)
        end = datetime(y, 11, first_sun_nov, 6, tzinfo=timezone.utc)
        return start <= now < end
    return False


def timezone_offset(profile: Dict[str, Any], now: Optional[datetime] = None) -> int:
    """Поточне зміщення UTC регіону з урахуванням літнього часу (Україна: +2 взимку, +3 влітку)."""
    _, base, rule = REGIONS.get(profile.get("region") or "ua", REGIONS["ua"])
    return base + int(_is_summer_time(rule, now or datetime.now(timezone.utc)))


def describe_profile(profile: Dict[str, Any]) -> str:
    """Текстовий опис сегмента — і для показу, і як джерело вектора інтересів."""
    parts: List[str] = []
    if profile.get("age"):
        parts.append("age " + AGE_GROUPS.get(profile["age"], ""))
    if profile.get("occupation"):
        parts.append(OCCUPATIONS.get(profile["occupation"], ""))
    if profile.get("region"):
        parts.append(REGIONS.get(profile["region"], ("",))[0])
    interests = [INTERESTS.get(i, i) for i in (profile.get("interests") or [])]
    if interests:
        parts.append("interests: " + ", ".join(interests))
    if profile.get("language"):
        parts.append("language: " + LANGUAGES.get(profile["language"], profile["language"]))
    return "; ".join([p for p in parts if p])


def peak_hours(activity_hours: List[float], top: int = 3) -> List[int]:
    if not activity_hours:
        return [19]
    ranked = sorted(range(len(activity_hours)), key=lambda h: activity_hours[h], reverse=True)
    return sorted(ranked[:top])


def suggest_slots(
    activity_hours: List[float],
    tz_offset: int,
    days: int = 3,
    per_day: int = 2,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Рекомендовані моменти публікації: найактивніші години сегмента у його
    місцевому часі, переведені в UTC і відфільтровані від уже минулих.
    """
    now = now or datetime.now(timezone.utc)
    hours = peak_hours(activity_hours, top=max(per_day, 1))
    slots: List[Dict[str, Any]] = []

    for day in range(days + 1):
        local_date = (now + timedelta(hours=tz_offset)).date() + timedelta(days=day)
        for hour in hours:
            local_dt = datetime(local_date.year, local_date.month, local_date.day, hour)
            utc_dt = local_dt.replace(tzinfo=timezone.utc) - timedelta(hours=tz_offset)
            if utc_dt <= now:
                continue
            slots.append(
                {
                    "at_utc": utc_dt.isoformat(),
                    "local_hour": hour,
                    "local_label": f"{local_dt:%d.%m} at {local_dt:%H}:00",
                    "score": round(activity_hours[hour], 3) if activity_hours else 0.0,
                }
            )

    slots.sort(key=lambda s: s["at_utc"])
    return slots[: days * max(per_day, 1)]


def taxonomy() -> Dict[str, Any]:
    """Довідники для форми — фронтенд не тримає власних копій списків."""
    return {
        "names": SEGMENT_NAMES,
        "occupations": [{"id": k, "label": v} for k, v in OCCUPATIONS.items()],
        "ages": [{"id": k, "label": v} for k, v in AGE_GROUPS.items()],
        "regions": [{"id": k, "label": v[0], "utc_offset": timezone_offset({"region": k})}
                    for k, v in REGIONS.items()],
        "languages": [{"id": k, "label": v} for k, v in LANGUAGES.items()],
        "devices": [{"id": k, "label": v} for k, v in DEVICES.items()],
        "interests": [{"id": k, "label": v} for k, v in INTERESTS.items()],
        "sizes": [{"id": k, "label": v} for k, v in SIZE_OPTIONS.items()],
    }
