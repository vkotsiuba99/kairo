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
        timeout = httpx.Timeout(settings.SERVICE_TIMEOUT_SECONDS, connect=5.0)
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


async def call(service: str, method: str, path: str, **kwargs) -> Any:
    """Викликає /api/v1{path} іншого сервісу й повертає JSON відповіді."""
    try:
        resp = await _client(service).request(method, settings.API_V1_STR + path, **kwargs)
    except httpx.HTTPError as e:
        raise ServiceError(service, 503, f"The {service} service is unavailable: {e}") from e
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ServiceError(service, resp.status_code, detail)
    return resp.json()
