"""
Синхронні (REST) виклики між сервісами.

Команди, на які потрібна відповідь одразу (опублікувати допис, згенерувати
ілюстрацію, прочитати матеріал), ідуть через REST; факти, на які інші сервіси
реагують пізніше, — через події (app.events).

Порожня адреса сервісу (наприклад, MEDIA_URL="") означає, що він працює в цьому
ж процесі (режим SERVICE=all): виклик іде через ASGI без мережі, але тим самим
шляхом і з тими самими схемами, що й між контейнерами.
"""

import asyncio
import time
from typing import Any, Dict, Tuple

import httpx

from app.core.config import settings

_clients: Dict[Tuple[str, int], httpx.AsyncClient] = {}


class ServiceError(Exception):
    """Помилка іншого сервісу; статус і текст передаються клієнтові як є."""

    def __init__(self, service: str, status_code: int, detail: Any):
        super().__init__(f"{service}: {status_code} {detail}")
        self.service, self.status_code, self.detail = service, status_code, detail


def _client(service: str) -> httpx.AsyncClient:
    # клієнт прив'язаний до циклу подій (у тестах цикл свій для кожного тесту)
    key = (service, id(asyncio.get_running_loop()))
    if key not in _clients:
        base = getattr(settings, f"{service.upper()}_URL")
        timeout = httpx.Timeout(settings.SERVICE_TIMEOUT_SECONDS, connect=2.0)  # у мережі кластера з'єднання — мілісекунди; мертвий сервіс не тримає запит
        if base:
            _clients[key] = httpx.AsyncClient(
                base_url=base, timeout=timeout,
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            )
        else:
            from app.main import app

            _clients[key] = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://inprocess", timeout=timeout
            )
    return _clients[key]


# Автоматичний вимикач (circuit breaker): після невдалого з'єднання сервіс вважається
# недоступним BREAKER_SECONDS, і виклики одразу отримують 503 без мережі й DNS. Інакше
# диспетчер черги, повторюючи публікації, засипав мертвий сервіс з'єднаннями, а повільні
# пошуки DNS його імені займали пул потоків і гальмували виклики до живих сервісів.
BREAKER_SECONDS = 5.0
_open_until: Dict[str, float] = {}
_probing: set = set()


async def call(service: str, method: str, path: str, **kwargs) -> Any:
    """Викликає /api/v1{path} іншого сервісу й повертає JSON відповіді."""
    if service in _open_until:
        # Напіввідкритий стан: після паузи до сервісу йде лише одна пробна спроба, решта
        # одразу отримує 503. Інакше всі накопичені виклики разом ішли б у мережу й DNS.
        if time.monotonic() < _open_until[service] or service in _probing:
            raise ServiceError(service, 503, f"The {service} service is unavailable (circuit open)")
        _probing.add(service)
    try:
        resp = await _client(service).request(method, settings.API_V1_STR + path, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        _open_until[service] = time.monotonic() + BREAKER_SECONDS
        raise ServiceError(service, 503, f"The {service} service is unavailable: {e}") from e
    except httpx.HTTPError as e:
        raise ServiceError(service, 503, f"The {service} service is unavailable: {e}") from e
    else:
        _open_until.pop(service, None)  # сервіс відповів — вимикач закрито
    finally:
        _probing.discard(service)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ServiceError(service, resp.status_code, detail)
    return resp.json()
