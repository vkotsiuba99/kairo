"""
Колектор реального часу: Jetstream (AT Protocol).

Jetstream віддає весь потік створення дописів у Bluesky у вигляді JSON,
без авторизації — перевірено рукостисканням 101. Це єдиний спосіб зібрати
дані, яких завтра вже не буде: повної ретроспективи стрічка не зберігає.

Курсор (`time_us`) пишеться на диск, тому перезапуск не створює дірку в даних,
доки розрив не перевищує вікно утримання на боці Jetstream.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import websockets

from app.demand.matcher import LexiconMatcher
from app.demand.store import MentionRecord

logger = logging.getLogger("kairo.demand.jetstream")

ENDPOINTS = (
    "wss://jetstream2.us-east.bsky.network/subscribe",
    "wss://jetstream1.us-west.bsky.network/subscribe",
)
COLLECTION = "app.bsky.feed.post"


def _parse_created_at(value: Optional[str], fallback_us: Optional[int]) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    if fallback_us:
        return datetime.fromtimestamp(fallback_us / 1_000_000, tz=timezone.utc)
    return datetime.now(timezone.utc)


class JetstreamCollector:
    def __init__(
        self,
        matcher: LexiconMatcher,
        cursor_path: str = "data/demand_jetstream.cursor",
        flush_size: int = 25,
        flush_seconds: float = 20.0,
    ):
        self.matcher = matcher
        self.cursor_path = cursor_path
        self.flush_size = flush_size
        self.flush_seconds = flush_seconds
        self._buffer: List[MentionRecord] = []
        self._last_flush = 0.0
        self._cursor: Optional[int] = self._load_cursor()
        self.seen_events = 0
        self.matched_events = 0

    def _load_cursor(self) -> Optional[int]:
        try:
            with open(self.cursor_path, "r", encoding="utf-8") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    def _save_cursor(self) -> None:
        if self._cursor is None:
            return
        os.makedirs(os.path.dirname(self.cursor_path) or ".", exist_ok=True)
        with open(self.cursor_path, "w", encoding="utf-8") as fh:
            fh.write(str(self._cursor))

    def _url(self, endpoint: str) -> str:
        url = f"{endpoint}?wantedCollections={COLLECTION}"
        if self._cursor:
            # Трохи відступаємо назад — краще дублі, які відсіє дедуплікація,
            # ніж пропущені дописи на стику сесій.
            url += f"&cursor={self._cursor - 5_000_000}"
        return url

    def _handle(self, payload: dict) -> List[MentionRecord]:
        if payload.get("kind") != "commit":
            return []
        commit = payload.get("commit") or {}
        if commit.get("operation") != "create" or commit.get("collection") != COLLECTION:
            return []
        record = commit.get("record") or {}
        text = record.get("text") or ""
        self.seen_events += 1
        self._cursor = payload.get("time_us") or self._cursor
        if not text:
            return []

        matches = self.matcher.match(text)
        if not matches:
            return []
        self.matched_events += 1

        did = payload.get("did") or ""
        rkey = commit.get("rkey") or ""
        reply = record.get("reply") or {}
        parent = (reply.get("parent") or {}).get("uri")
        langs = record.get("langs") or []

        out = []
        for m in matches:
            out.append(MentionRecord(
                platform="bluesky",
                external_id=f"at://{did}/{COLLECTION}/{rkey}",
                source="jetstream",
                author_id=did,
                posted_at=_parse_created_at(record.get("createdAt"), payload.get("time_us")),
                text=text,
                product_code=m.product_code,
                matched_terms=m.matched_terms,
                sku_codes=m.sku_codes,
                looks_promo=m.looks_promo,
                lang=langs[0] if langs else None,
                is_reply=bool(parent),
                reply_to_external_id=parent,
                raw={"cid": commit.get("cid"), "rev": commit.get("rev")},
            ))
        return out

    async def run(self, on_batch, stop_after: Optional[float] = None) -> None:
        """
        Читає потік і віддає пачки згадок у `on_batch` (async-callable).
        `stop_after` у секундах зручний для димового прогону.
        """
        loop = asyncio.get_event_loop()
        started = loop.time()
        self._last_flush = started
        backoff = 1.0
        endpoint_idx = 0

        while True:
            if stop_after and loop.time() - started >= stop_after:
                break
            endpoint = ENDPOINTS[endpoint_idx % len(ENDPOINTS)]
            try:
                async with websockets.connect(
                    self._url(endpoint), ping_interval=20, max_size=None
                ) as ws:
                    logger.info("Jetstream connected: %s", endpoint)
                    backoff = 1.0
                    while True:
                        if stop_after and loop.time() - started >= stop_after:
                            break
                        timeout = 5.0
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except asyncio.TimeoutError:
                            raw = None
                        if raw:
                            try:
                                self._buffer.extend(self._handle(json.loads(raw)))
                            except (json.JSONDecodeError, TypeError):
                                continue
                        now = loop.time()
                        if len(self._buffer) >= self.flush_size or (
                            self._buffer and now - self._last_flush >= self.flush_seconds
                        ):
                            batch, self._buffer = self._buffer, []
                            await on_batch(batch)
                            self._save_cursor()
                            self._last_flush = now
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Jetstream dropped (%s), reconnecting in %.0f s",
                               type(exc).__name__, backoff)
                endpoint_idx += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        if self._buffer:
            batch, self._buffer = self._buffer, []
            await on_batch(batch)
        self._save_cursor()
