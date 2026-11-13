from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class RawEvent:
    event_id: str
    external_post_id: str
    event_type: str  # view, reaction, comment, save, share
    event_value: float
    occurred_at: datetime
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEvent:
    external_post_id: str
    event_type: str  # view, reaction, comment, save, share
    event_value: float
    occurred_at: datetime
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActorInteraction:
    """
    Пособітійна взаємодія з відомим автором.

    Потрібна не для винагороди (її рахують агрегатні лічильники), а для
    побудови профілів аудиторії: саме звідси система дізнається, ХТО реагує.
    """
    external_user_id: str
    display_name: str
    interaction_type: str  # reaction, comment, share
    external_post_id: str
    occurred_at: datetime
    text: str = ""


def public_media_url(media_refs: Dict[str, Any], key: str) -> Optional[str]:
    """
    Публічна адреса медіафайлу для платформ, що забирають медіа за URL
    (Instagram, TikTok PULL_FROM_URL): PUBLIC_BASE_URL + /media/… з media_refs.
    """
    from app.core.config import settings

    path = media_refs.get(key)
    if not path or not settings.PUBLIC_BASE_URL:
        return None
    return settings.PUBLIC_BASE_URL.rstrip("/") + "/" + str(path).split("?")[0].lstrip("/")


def tags_line(hashtags: List[str]) -> str:
    return " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags)


@dataclass
class PublishResult:
    success: bool
    external_post_id: Optional[str] = None
    url: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAdapter(ABC):
    """
    Unified Platform-Agnostic Adapter interface.
    Each adapter implements both telemetry collection and content publication.
    """

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        self.credentials = credentials or {}

    @abstractmethod
    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        """Publishes content to the platform."""
        pass

    @abstractmethod
    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        """Fetches raw events from the platform since the specified datetime."""
        pass

    @abstractmethod
    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        """Normalizes a platform-specific event into the common Telemetry schema."""
        pass

    # ------------------------------------------------------------------
    # Телеметрія: що саме платформа справді віддає
    # ------------------------------------------------------------------
    # Оголошення можливостей тримається поруч із реалізацією, щоб інтерфейс і
    # пояснювальна записка не розходилися з фактом. Порожній список означає,
    # що сигнал недоступний у публічному API, а не що його «забули зробити».
    AVAILABLE: bool = True
    UNAVAILABLE_REASON: str = ""
    COUNTER_METRICS: List[str] = []      # які агрегатні лічильники доступні
    SUPPORTS_PER_ACTOR: bool = False     # чи видно, хто саме взаємодіяв
    TELEMETRY_NOTE: str = ""

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        """
        Поточні агрегатні лічильники конкретного допису.

        Повертає абсолютні значення (скільки всього реакцій/коментарів зараз),
        а не приріст: різницю рахує воркер, порівнюючи з уже збереженим. Такий
        підхід ідемпотентний — повторний опит не подвоює телеметрію.
        """
        return {}

    async def fetch_channel_counters(self) -> Dict[str, Dict[str, float]]:
        """
        Лічильники, які платформа віддає пачкою (наприклад, через потік
        оновлень), у вигляді {external_post_id: {метрика: абсолютне значення}}.
        """
        return {}

    async def fetch_interactions(self, since: datetime) -> List["ActorInteraction"]:
        """Взаємодії з відомими авторами — джерело профілів аудиторії."""
        return []
