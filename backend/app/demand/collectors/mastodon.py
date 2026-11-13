"""
Колектор Mastodon: публічні стрічки тегів плюс повнотекстовий пошук.

Об'єми тут на порядок менші за Bluesky (десятки дописів на добу проти сотень),
але аудиторія інша, і саме розбіжність між майданчиками дає матеріал:
тег #steamdeck на mastodon.social окупували агрегатори знижок — 182 дописи
на добу за середньої залученості 0.5, тоді як на Bluesky той самий товар
обговорюють 88 різних авторів зі ста. Один товар, два профілі забруднення.
"""
import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.demand.bridges import is_bridged
from app.demand.matcher import LexiconMatcher
from app.demand.products import ProductSpec
from app.demand.store import MentionRecord

logger = logging.getLogger("kairo.demand.mastodon")

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(content: str) -> str:
    """Mastodon віддає тіло допису як HTML."""
    text = content.replace("</p><p>", "\n\n").replace("<br />", "\n").replace("<br>", "\n")
    return html.unescape(_TAG_RE.sub("", text)).strip()


class MastodonCollector:
    def __init__(
        self,
        instance_url: str,
        matcher: LexiconMatcher,
        access_token: Optional[str] = None,
    ):
        self.instance = instance_url.rstrip("/")
        self.matcher = matcher
        self.token = access_token

    def _headers(self) -> Dict[str, str]:
        h = {"User-Agent": "kairo-demand/0.1"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _to_records(self, statuses: List[Dict[str, Any]], source: str) -> List[MentionRecord]:
        out: List[MentionRecord] = []
        for s in statuses:
            if s.get("reblog"):
                # Репост не є самостійним висловлюванням: оригінал уже
                # або зібрано, або буде зібрано. Підсилення рахуємо
                # лічильником на оригіналі, а не новим рядком.
                continue
            text = strip_html(s.get("content") or "")
            matches = self.matcher.match(text)
            if not matches:
                continue
            account = s.get("account") or {}
            bridged = is_bridged(account.get("acct"), s.get("uri"))
            try:
                posted_at = datetime.fromisoformat((s.get("created_at") or "").replace("Z", "+00:00"))
            except ValueError:
                posted_at = datetime.now(timezone.utc)
            for m in matches:
                out.append(MentionRecord(
                    platform="mastodon",
                    # uri глобально унікальний, на відміну від id, який
                    # локальний для інстансу.
                    external_id=s.get("uri") or f"{self.instance}/{s.get('id')}",
                    source=source,
                    author_id=account.get("id") or "",
                    author_handle=account.get("acct"),
                    posted_at=posted_at,
                    text=text,
                    product_code=m.product_code,
                    matched_terms=m.matched_terms,
                    sku_codes=m.sku_codes,
                    looks_promo=m.looks_promo,
                    is_bridged=bridged,
                    lang=s.get("language"),
                    is_reply=bool(s.get("in_reply_to_id")),
                    reply_to_external_id=s.get("in_reply_to_id"),
                    like_count=s.get("favourites_count") or 0,
                    repost_count=s.get("reblogs_count") or 0,
                    reply_count=s.get("replies_count") or 0,
                    raw={"url": s.get("url"), "instance": self.instance},
                ))
        return out

    async def poll_tags(
        self,
        specs: List[ProductSpec],
        pages_per_tag: int = 3,
        pause_seconds: float = 1.0,
    ) -> List[MentionRecord]:
        collected: List[MentionRecord] = []
        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            for spec in specs:
                for tag in spec.mastodon_tags:
                    max_id: Optional[str] = None
                    for _ in range(pages_per_tag):
                        params: Dict[str, Any] = {"limit": 40}
                        if max_id:
                            params["max_id"] = max_id
                        resp = await client.get(
                            f"{self.instance}/api/v1/timelines/tag/{tag}", params=params
                        )
                        if resp.status_code == 429:
                            await asyncio.sleep(30)
                            continue
                        if not resp.is_success:
                            logger.warning("Tag #%s -> %s", tag, resp.status_code)
                            break
                        statuses = resp.json()
                        if not statuses:
                            break
                        collected.extend(self._to_records(statuses, "tag"))
                        max_id = statuses[-1].get("id")
                        await asyncio.sleep(pause_seconds)
        return collected

    async def search(
        self,
        specs: List[ProductSpec],
        pause_seconds: float = 1.0,
    ) -> List[MentionRecord]:
        """Повнотекстовий пошук — лише з токеном."""
        if not self.token:
            logger.info("No Mastodon token, full-text search skipped")
            return []
        collected: List[MentionRecord] = []
        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            for spec in specs:
                for query in spec.search_queries:
                    resp = await client.get(
                        f"{self.instance}/api/v2/search",
                        params={"q": query, "type": "statuses", "limit": 40},
                    )
                    if not resp.is_success:
                        logger.warning("Search \"%s\" -> %s", query, resp.status_code)
                        continue
                    collected.extend(
                        self._to_records(resp.json().get("statuses") or [], "search")
                    )
                    await asyncio.sleep(pause_seconds)
        return collected
