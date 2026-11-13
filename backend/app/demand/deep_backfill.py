"""
Глибокий добір історії Bluesky.

Відрізняється від звичайного добору трьома речами, і кожна з них з'ясувалась
дослідом, а не з документації.

По-перше, глибина впирається в обсяг бренду, а не в горизонт індексу: за
запитом «Nintendo» сорок дев'ять сторінок не вивели навіть за межі однієї доби.
Тому зупиняємось за датою, а не за кількістю сторінок.

По-друге, на глибині сервер починає віддавати 502. Раніше колектор на цьому
просто зупинявся і мовчки повертав обрізану історію — тепер повторює з
відступом і зупиняється лише коли повтори вичерпано.

По-третє, двісті тисяч дописів не можна накопичити в пам'яті й записати
одним махом. Пачки віддаються назовні одразу після кожної сторінки.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Optional, Sequence

import httpx

from app.demand.bridges import is_bridged
from app.demand.matcher import LexiconMatcher
from app.demand.products import ProductSpec
from app.demand.store import MentionRecord

logger = logging.getLogger("kairo.demand.deep")


@dataclass
class BackfillStat:
    code: str
    pages: int = 0
    raw_posts: int = 0
    kept: int = 0
    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None
    stopped_by: str = ""
    errors: int = 0

    @property
    def depth_days(self) -> float:
        if not self.oldest or not self.newest:
            return 0.0
        return round((self.newest - self.oldest).total_seconds() / 86400, 1)


class DeepBackfill:
    def __init__(
        self,
        handle: str,
        app_password: str,
        service: str = "https://bsky.social",
        pause_seconds: float = 0.35,
    ):
        self.handle = handle
        self.app_password = app_password
        self.service = service.rstrip("/")
        self.pause = pause_seconds
        self._jwt: Optional[str] = None

    async def authenticate(self, client: httpx.AsyncClient) -> bool:
        resp = await client.post(
            f"{self.service}/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
        )
        if not resp.is_success:
            logger.error("Authentication failed: %s", resp.status_code)
            return False
        self._jwt = resp.json().get("accessJwt")
        return bool(self._jwt)

    @staticmethod
    def _to_records(spec: ProductSpec, matcher: LexiconMatcher, posts: List[dict]) -> List[MentionRecord]:
        out: List[MentionRecord] = []
        for p in posts:
            record = p.get("record") or {}
            text = record.get("text") or ""
            if not matcher.match(text):
                continue
            author = p.get("author") or {}
            reply = record.get("reply") or {}
            parent = (reply.get("parent") or {}).get("uri")
            langs = record.get("langs") or []
            stamp = p.get("indexedAt") or record.get("createdAt") or ""
            try:
                posted_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                posted_at = datetime.now(timezone.utc)
            out.append(MentionRecord(
                platform="bluesky",
                external_id=p.get("uri") or "",
                source="search",
                author_id=author.get("did") or "",
                author_handle=author.get("handle"),
                posted_at=posted_at,
                text=text,
                product_code=spec.code,
                matched_terms=[],
                sku_codes=[],
                looks_promo=False,
                is_bridged=is_bridged(author.get("handle")),
                lang=langs[0] if langs else None,
                is_reply=bool(parent),
                reply_to_external_id=parent,
                like_count=p.get("likeCount") or 0,
                repost_count=p.get("repostCount") or 0,
                reply_count=p.get("replyCount") or 0,
                raw={"cid": p.get("cid"), "indexedAt": p.get("indexedAt")},
            ))
        return out

    async def _fetch(self, client, params, headers, stat: BackfillStat) -> Optional[dict]:
        """Сторінка з повторами. 502 на глибині — норма, а не привід зупинятись."""
        delay = 2.0
        for attempt in range(6):
            try:
                resp = await client.get(
                    f"{self.service}/xrpc/app.bsky.feed.searchPosts",
                    params=params, headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError):
                stat.errors += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if resp.is_success:
                return resp.json()
            if resp.status_code == 429:
                stat.errors += 1
                await asyncio.sleep(30)
                continue
            if resp.status_code >= 500:
                stat.errors += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            logger.warning("searchPosts -> %s, stopping", resp.status_code)
            return None
        return None

    async def run_one(
        self,
        client: httpx.AsyncClient,
        spec: ProductSpec,
        on_batch: Callable[[List[MentionRecord]], Awaitable[None]],
        target_days: int = 180,
        max_pages: int = 500,
    ) -> BackfillStat:
        stat = BackfillStat(code=spec.code)
        matcher = LexiconMatcher((spec,))
        headers = {"Authorization": f"Bearer {self._jwt}"}
        cutoff = datetime.now(timezone.utc) - timedelta(days=target_days)

        for query in spec.search_queries:
            cursor: Optional[str] = None
            beyond_cutoff = 0
            while stat.pages < max_pages:
                params = {"q": query, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                data = await self._fetch(client, params, headers, stat)
                if data is None:
                    stat.stopped_by = "error budget exhausted"
                    break
                posts = data.get("posts") or []
                if not posts:
                    stat.stopped_by = "empty page"
                    break

                stat.pages += 1
                stat.raw_posts += len(posts)
                records = self._to_records(spec, matcher, posts)
                stat.kept += len(records)
                if records:
                    await on_batch(records)
                    for r in records:
                        stat.oldest = r.posted_at if stat.oldest is None or r.posted_at < stat.oldest else stat.oldest
                        stat.newest = r.posted_at if stat.newest is None or r.posted_at > stat.newest else stat.newest

                # Зупинка за датою — за медіаною сторінки, і лише коли дві
                # сторінки поспіль лежать за відсічкою.
                #
                # Спершу тут стояв найстаріший допис сторінки, і це обривало
                # добір передчасно: видача Bluesky не строго хронологічна, тож
                # один випадковий старий допис серед сотні зупиняв пагінацію.
                # Nikon так відрапортував «досягнуто цільової дати» на 60 днях
                # замість 180. Медіана стійка до поодиноких викидів, а вимога
                # двох сторінок поспіль страхує від випадкової старої сторінки.
                stamps = []
                for p in posts:
                    raw_ts = p.get("indexedAt")
                    if not raw_ts:
                        continue
                    try:
                        stamps.append(datetime.fromisoformat(raw_ts.replace("Z", "+00:00")))
                    except ValueError:
                        continue
                if stamps:
                    stamps.sort()
                    median = stamps[len(stamps) // 2]
                    if median <= cutoff:
                        beyond_cutoff += 1
                        if beyond_cutoff >= 2:
                            stat.stopped_by = "target date reached"
                            break
                    else:
                        beyond_cutoff = 0

                cursor = data.get("cursor")
                if not cursor:
                    stat.stopped_by = "cursor exhausted"
                    break
                await asyncio.sleep(self.pause)
            if stat.pages >= max_pages:
                stat.stopped_by = "page limit"
                break
            if stat.stopped_by == "target date reached":
                break
        return stat

    async def run_all(
        self,
        specs: Sequence[ProductSpec],
        on_batch: Callable[[List[MentionRecord]], Awaitable[None]],
        target_days: int = 180,
        max_pages: int = 500,
        on_done: Optional[Callable[[BackfillStat], None]] = None,
    ) -> List[BackfillStat]:
        stats: List[BackfillStat] = []
        async with httpx.AsyncClient(timeout=45.0) as client:
            if not await self.authenticate(client):
                return stats
            for spec in specs:
                stat = await self.run_one(client, spec, on_batch, target_days, max_pages)
                stats.append(stat)
                if on_done:
                    on_done(stat)
        return stats
