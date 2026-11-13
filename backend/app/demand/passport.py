"""
Замір сигналу по товару — підстава пускати його в меню.

Одного запиту на 100 дописів достатньо, щоб отримати всі потрібні числа:
за яке вікно ці сто дописів набралися (звідси інтенсивність), скільки серед
них різних авторів (звідси накрутка), скільки повторів тексту і скільки
реклами. Дешево і не потребує накопичення.

Вердикт свідомо не бінарний. «Мало даних» і «накрутка» — різні хвороби:
перша лікується часом, друга не лікується взагалі.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import httpx

from app.demand.catalog import Candidate
from app.demand.matcher import LexiconMatcher, text_hash

logger = logging.getLogger("kairo.demand.passport")

# Пороги. Підібрані так, щоб меню лишалось чесним, а не великим.
MIN_POSTS_PER_DAY = 3.0        # нижче — говорити нема про що
THIN_POSTS_PER_DAY = 15.0      # нижче — беремо, але позначаємо як тонкий сигнал
MIN_AUTHOR_RATIO = 0.35        # нижче — пише кілька акаунтів, а не спільнота
MAX_PROMO_RATIO = 0.40         # вище — говорить реклама, не люди
MIN_SAMPLE = 8                 # менше — статистика не має сенсу
# Копіпаста. Patagonia проходила замір із 44% дослівних повторів: тег окупував
# агрегатор оголошень. Обсяг у такого товару є, попиту за ним не видно.
MAX_DUPLICATE_RATIO = 0.30

_PROMO_RE = [re.compile(p, re.IGNORECASE) for p in (
    r"\d{1,2}%\s*off", r"\bcoupon\b", r"\baffiliate\b",
    r"\bamzn\.to\b", r"\bon sale\b", r"\bbest deal\b",
)]


@dataclass
class Passport:
    code: str
    name: str
    category: str
    sample_size: int = 0
    posts_per_day: float = 0.0
    unique_author_ratio: float = 0.0
    duplicate_ratio: float = 0.0
    promo_ratio: float = 0.0
    history_days: float = 0.0   # ширина вікна вибірки, НЕ глибина ретроспективи
    verdict: str = "no_signal"
    measured_at: Optional[datetime] = None
    # Скільки дописів віддав пошук до фільтрації лексиконом. Розрив між
    # raw_hits і sample_size показує, наскільки назва товару неоднозначна.
    raw_hits: int = 0

    @property
    def in_menu(self) -> bool:
        return self.verdict in ("ok", "thin")


def _window_days(posts: List[dict]) -> float:
    stamps: List[datetime] = []
    for post in posts:
        raw = post.get("indexedAt") or (post.get("record") or {}).get("createdAt") or ""
        try:
            stamps.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(stamps) < 2:
        return 0.0
    return (max(stamps) - min(stamps)).total_seconds() / 86400


def evaluate(
    code: str,
    name: str,
    category: str,
    posts: List[dict],
    window_days: Optional[float] = None,
) -> Passport:
    """
    Рахує метрики по вибірці дописів Bluesky.

    `window_days` — ширина вікна ВСІЄЇ видачі пошуку, а не лише релевантних
    дописів. Це принципово: якщо зі ста знайдених нашими є п'ять, вони можуть
    лежати купно, і поділивши п'ять на їхній власний проміжок, ми отримаємо
    вигадану інтенсивність. Ділити треба на те вікно, яке пошук реально оглянув.
    """
    p = Passport(code=code, name=name, category=category,
                 measured_at=datetime.now(timezone.utc))
    p.sample_size = len(posts)
    if len(posts) < MIN_SAMPLE:
        p.verdict = "no_signal"
        return p

    stamps: List[datetime] = []
    authors, hashes = set(), []
    promo = 0
    for post in posts:
        raw = post.get("indexedAt") or (post.get("record") or {}).get("createdAt") or ""
        try:
            stamps.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            pass
        authors.add((post.get("author") or {}).get("did") or "")
        text = (post.get("record") or {}).get("text") or ""
        hashes.append(text_hash(text))
        if any(r.search(text) for r in _PROMO_RE):
            promo += 1

    span = window_days if window_days is not None else _window_days(posts)
    p.history_days = round(span, 2)
    if span > 0:
        p.posts_per_day = round(len(posts) / span, 2)
    elif len(stamps) >= 2:
        p.posts_per_day = float(len(posts))

    p.unique_author_ratio = round(len(authors) / len(posts), 3)
    p.duplicate_ratio = round(1 - len(set(hashes)) / len(hashes), 3)
    p.promo_ratio = round(promo / len(posts), 3)

    # Порядок перевірок важливий: накрутку діагностуємо раніше за обсяг,
    # бо накручений товар часто саме обсягом і виглядає пристойно.
    if p.unique_author_ratio < MIN_AUTHOR_RATIO:
        p.verdict = "astroturf"
    elif p.duplicate_ratio > MAX_DUPLICATE_RATIO:
        p.verdict = "spam_dupes"
    elif p.promo_ratio > MAX_PROMO_RATIO:
        p.verdict = "promo_heavy"
    elif p.posts_per_day < MIN_POSTS_PER_DAY:
        p.verdict = "no_signal"
    elif p.posts_per_day < THIN_POSTS_PER_DAY:
        p.verdict = "thin"
    else:
        p.verdict = "ok"
    return p


class PassportMeter:
    def __init__(self, handle: str, app_password: str, service: str = "https://bsky.social"):
        self.handle = handle
        self.app_password = app_password
        self.service = service.rstrip("/")
        self._jwt: Optional[str] = None

    async def _authenticate(self, client: httpx.AsyncClient) -> bool:
        resp = await client.post(
            f"{self.service}/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
        )
        if not resp.is_success:
            logger.error("Authentication failed: %s", resp.status_code)
            return False
        self._jwt = resp.json().get("accessJwt")
        return bool(self._jwt)

    async def measure_all(
        self,
        candidates: Sequence[Candidate],
        pause_seconds: float = 0.6,
        on_progress=None,
    ) -> Dict[str, Passport]:
        out: Dict[str, Passport] = {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            if not await self._authenticate(client):
                return out
            headers = {"Authorization": f"Bearer {self._jwt}"}
            for i, cand in enumerate(candidates, 1):
                posts: List[dict] = []
                try:
                    resp = await client.get(
                        f"{self.service}/xrpc/app.bsky.feed.searchPosts",
                        params={"q": cand.primary_query, "limit": 100}, headers=headers,
                    )
                    if resp.status_code == 429:
                        await asyncio.sleep(30)
                        resp = await client.get(
                            f"{self.service}/xrpc/app.bsky.feed.searchPosts",
                            params={"q": cand.primary_query, "limit": 100}, headers=headers,
                        )
                    if resp.is_success:
                        posts = resp.json().get("posts") or []
                except Exception as exc:
                    logger.warning("Measurement %s failed: %s", cand.code, type(exc).__name__)
                # Пошук Bluesky не знає наших правил і на назву-омонім
                # віддає сміття: «Make Noise» — це переважно англійський
                # зворот «шуміти», а не виробник синтезаторів. Тому вибірку
                # проганяємо через той самий лексикон, яким збиратимемо корпус,
                # інакше паспорт міряє не той товар.
                matcher = LexiconMatcher((cand.to_spec(),))
                kept = [
                    p for p in posts
                    if matcher.match((p.get("record") or {}).get("text") or "")
                ]
                pp = evaluate(cand.code, cand.name, cand.category, kept,
                              window_days=_window_days(posts))
                pp.raw_hits = len(posts)
                out[cand.code] = pp
                if on_progress:
                    on_progress(i, len(candidates), pp)
                await asyncio.sleep(pause_seconds)
        return out
