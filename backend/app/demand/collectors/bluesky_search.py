"""
Добір історії Bluesky через авторизований пошук.

`app.bsky.feed.searchPosts` без токена віддає 403, з app-password — працює
і гортається курсором. Це єдиний доступний шматок ретроспективи, тому саме
з нього формується стартовий корпус, поки jetstream накопичує реальний час.

Пошук повертає лічильники реакцій, яких у jetstream немає за визначенням:
там допис щойно створено. Тому ці два джерела не дублюють, а доповнюють одне
одного, і перетин по (platform, external_id) є штатним.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.demand.bridges import is_bridged
from app.demand.matcher import LexiconMatcher
from app.demand.products import ProductSpec
from app.demand.store import MentionRecord

logger = logging.getLogger("kairo.demand.bsky_search")


class BlueskySearchCollector:
    def __init__(
        self,
        handle: str,
        app_password: str,
        matcher: LexiconMatcher,
        service: str = "https://bsky.social",
    ):
        self.handle = handle
        self.app_password = app_password
        self.matcher = matcher
        self.service = service.rstrip("/")
        self._jwt: Optional[str] = None

    async def _authenticate(self, client: httpx.AsyncClient) -> bool:
        resp = await client.post(
            f"{self.service}/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
        )
        if not resp.is_success:
            logger.error("Bluesky authentication failed: %s", resp.status_code)
            return False
        self._jwt = resp.json().get("accessJwt")
        return bool(self._jwt)

    def _to_records(self, posts: List[Dict[str, Any]]) -> List[MentionRecord]:
        out: List[MentionRecord] = []
        for p in posts:
            record = p.get("record") or {}
            text = record.get("text") or ""
            matches = self.matcher.match(text)
            if not matches:
                # Пошук працює за своїм індексом і повертає дописи, яких наш
                # лексикон не визнає. Довіряємо лексикону — він єдиний для
                # обох джерел, інакше корпус буде зібрано за різними правилами.
                continue
            author = p.get("author") or {}
            bridged = is_bridged(author.get("handle"))
            reply = record.get("reply") or {}
            parent = (reply.get("parent") or {}).get("uri")
            langs = record.get("langs") or []
            indexed = p.get("indexedAt") or record.get("createdAt")
            try:
                posted_at = datetime.fromisoformat((indexed or "").replace("Z", "+00:00"))
            except ValueError:
                posted_at = datetime.now(timezone.utc)
            for m in matches:
                out.append(MentionRecord(
                    platform="bluesky",
                    external_id=p.get("uri") or "",
                    source="search",
                    author_id=author.get("did") or "",
                    author_handle=author.get("handle"),
                    posted_at=posted_at,
                    text=text,
                    product_code=m.product_code,
                    matched_terms=m.matched_terms,
                    sku_codes=m.sku_codes,
                    looks_promo=m.looks_promo,
                    is_bridged=bridged,
                    lang=langs[0] if langs else None,
                    is_reply=bool(parent),
                    reply_to_external_id=parent,
                    like_count=p.get("likeCount") or 0,
                    repost_count=p.get("repostCount") or 0,
                    reply_count=p.get("replyCount") or 0,
                    raw={"cid": p.get("cid"), "indexedAt": p.get("indexedAt")},
                ))
        return out

    async def backfill(
        self,
        specs: List[ProductSpec],
        max_pages_per_query: int = 10,
        pause_seconds: float = 1.0,
    ) -> List[MentionRecord]:
        collected: List[MentionRecord] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            if not await self._authenticate(client):
                return collected
            headers = {"Authorization": f"Bearer {self._jwt}"}

            for spec in specs:
                for query in spec.search_queries:
                    cursor: Optional[str] = None
                    for page in range(max_pages_per_query):
                        params = {"q": query, "limit": 100}
                        if cursor:
                            params["cursor"] = cursor
                        resp = await client.get(
                            f"{self.service}/xrpc/app.bsky.feed.searchPosts",
                            params=params, headers=headers,
                        )
                        if resp.status_code == 429:
                            logger.warning("Rate limited, pausing 30 s")
                            await asyncio.sleep(30)
                            continue
                        if not resp.is_success:
                            logger.warning("searchPosts %s -> %s", query, resp.status_code)
                            break
                        data = resp.json()
                        posts = data.get("posts") or []
                        if not posts:
                            break
                        collected.extend(self._to_records(posts))
                        cursor = data.get("cursor")
                        if not cursor:
                            break
                        await asyncio.sleep(pause_seconds)
                    logger.info("Query \"%s\": %d mentions in total", query, len(collected))
        return collected
