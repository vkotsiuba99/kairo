import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.adapters.base import (
    BaseAdapter,
    NormalizedEvent,
    PublishResult,
    RawEvent,
    public_media_url,
    tags_line,
)
from app.core.config import settings

PUBLISH_PREFIX = "publish:"  # допис ще обробляється — зберігаємо publish_id


class TikTokAdapter(BaseAdapter):
    """
    TikTok (Content Posting API — пряма публікація; Display API — статистика).
    Credentials:
      - access_token: str (scopes video.publish, video.list)
      - privacy_level: str (SELF_ONLY для застосунків без аудиту TikTok)

    Відео — POST /v2/post/publish/video/init/, фото-допис —
    POST /v2/post/publish/content/init/ (media_type=PHOTO); медіа TikTok забирає
    за публічним URL (PULL_FROM_URL), текстових дописів платформа не має.
    """

    COUNTER_METRICS = ["views", "reactions", "comments", "shares"]
    SUPPORTS_PER_ACTOR = False
    TELEMETRY_NOTE = (
        "The Display API returns views, likes, comments and shares per video; "
        "it does not reveal who interacted."
    )

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.token = self.credentials.get("access_token", "")
        self.privacy = self.credentials.get("privacy_level", "SELF_ONLY")
        self.base = settings.TIKTOK_API_BASE.rstrip("/")

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=UTF-8"}

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        if not self.token:
            return PublishResult(
                success=True,
                external_post_id=f"tt_sim_{content_item_id}",
                url=f"https://www.tiktok.com/@demo/video/{content_item_id}",
                metadata={"mode": "simulated", "note": "TikTok access token not configured"},
            )

        video_url = public_media_url(media_refs, "video_url")
        image_url = public_media_url(media_refs, "image_url")
        if not video_url and not image_url:
            return PublishResult(
                success=False,
                error_message="TikTok publishes only videos or photos from a public URL "
                "(the post needs media and PUBLIC_BASE_URL must be set)",
            )

        description = f"{body}\n\n{tags_line(hashtags)}" if hashtags else body
        if video_url:
            endpoint = "/v2/post/publish/video/init/"
            payload = {
                "post_info": {"title": f"{title or ''} {description}".strip()[:2200], "privacy_level": self.privacy},
                "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
            }
        else:
            endpoint = "/v2/post/publish/content/init/"
            payload = {
                "post_info": {
                    "title": (title or "")[:90],
                    "description": description[:4000],
                    "privacy_level": self.privacy,
                },
                "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0, "photo_images": [image_url]},
                "post_mode": "DIRECT_POST",
                "media_type": "PHOTO",
            }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{self.base}{endpoint}", headers=self._headers, json=payload)
                data = resp.json()
                err = data.get("error") or {}
                if not resp.is_success or err.get("code", "ok") != "ok":
                    return PublishResult(success=False, error_message=f"TikTok: {err.get('message') or resp.text}")
                publish_id = data["data"]["publish_id"]
                post_id = await self._resolve(client, publish_id, attempts=5)
        except Exception as e:
            return PublishResult(success=False, error_message=str(e))

        external = post_id or f"{PUBLISH_PREFIX}{publish_id}"
        return PublishResult(
            success=True,
            external_post_id=external,
            url=f"https://www.tiktok.com/video/{post_id}" if post_id else None,
            metadata={"publish_id": publish_id},
        )

    async def _resolve(self, client: httpx.AsyncClient, publish_id: str, attempts: int) -> Optional[str]:
        """Номер опублікованого відео з publish_id (TikTok обробляє медіа асинхронно)."""
        for i in range(attempts):
            resp = await client.post(
                f"{self.base}/v2/post/publish/status/fetch/",
                headers=self._headers,
                json={"publish_id": publish_id},
            )
            d = (resp.json() or {}).get("data") or {}
            if d.get("status") == "PUBLISH_COMPLETE":
                ids = d.get("publicaly_available_post_id") or []  # саме так написано в API TikTok
                return str(ids[0]) if ids else None
            if d.get("status") == "FAILED":
                return None
            if i + 1 < attempts:
                await asyncio.sleep(1)
        return None

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not self.token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                video_id = external_post_id
                if video_id.startswith(PUBLISH_PREFIX):
                    video_id = await self._resolve(client, video_id[len(PUBLISH_PREFIX):], attempts=1)
                    if not video_id:
                        return {}
                resp = await client.post(
                    f"{self.base}/v2/video/query/",
                    params={"fields": "id,view_count,like_count,comment_count,share_count"},
                    headers=self._headers,
                    json={"filters": {"video_ids": [video_id]}},
                )
            videos = ((resp.json() or {}).get("data") or {}).get("videos") or []
            if not resp.is_success or not videos:
                return {}
            v = videos[0]
            return {
                "views": float(v.get("view_count") or 0),
                "reactions": float(v.get("like_count") or 0),
                "comments": float(v.get("comment_count") or 0),
                "shares": float(v.get("share_count") or 0),
            }
        except Exception:
            return {}

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        return []

    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        return NormalizedEvent(
            external_post_id=raw.external_post_id,
            event_type=raw.event_type,
            event_value=raw.event_value,
            occurred_at=raw.occurred_at,
            raw_payload=raw.raw_payload,
        )
