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

CAPTION_LIMIT = 2200


class InstagramAdapter(BaseAdapter):
    """
    Instagram (Graph API, публікація контенту для професійних акаунтів).
    Credentials:
      - ig_user_id: str
      - access_token: str (дозволи instagram_basic, instagram_content_publish)

    Публікація у два кроки: створення контейнера POST /{ig_user_id}/media
    (image_url або video_url для Reels) і POST /{ig_user_id}/media_publish.
    Instagram забирає медіа за публічним URL, тому потрібен PUBLIC_BASE_URL;
    дописів без зображення чи відео платформа не приймає.
    """

    COUNTER_METRICS = ["views", "reactions", "comments", "saves", "shares"]
    SUPPORTS_PER_ACTOR = False
    TELEMETRY_NOTE = (
        "Media fields return like_count and comments_count; Insights adds views, "
        "saves and shares. The API does not reveal who liked a post."
    )

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.user_id = self.credentials.get("ig_user_id", "")
        self.token = self.credentials.get("access_token", "")
        self.base = settings.GRAPH_API_BASE.rstrip("/")

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        if not self.user_id or not self.token:
            return PublishResult(
                success=True,
                external_post_id=f"ig_sim_{content_item_id}",
                url=f"https://instagram.com/p/{content_item_id}",
                metadata={"mode": "simulated", "note": "Instagram token not configured"},
            )

        video_url = public_media_url(media_refs, "video_url")
        image_url = public_media_url(media_refs, "image_url")
        if not video_url and not image_url:
            return PublishResult(
                success=False,
                error_message="Instagram publishes only photos or videos from a public URL "
                "(the post needs an image and PUBLIC_BASE_URL must be set)",
            )

        caption = f"{title}\n\n{body}" if title else body
        if hashtags:
            caption = f"{caption}\n\n{tags_line(hashtags[:30])}"  # не більше 30 хештегів
        caption = caption[:CAPTION_LIMIT]

        container = {"caption": caption, "access_token": self.token}
        if video_url:
            container.update(media_type="REELS", video_url=video_url)
        else:
            container["image_url"] = image_url

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self.base}/{self.user_id}/media", data=container)
                data = resp.json()
                if not resp.is_success or "id" not in data:
                    return self._error(data, resp)
                creation_id = data["id"]

                if video_url:  # відео обробляється асинхронно — чекаємо FINISHED
                    for _ in range(30):
                        st = (
                            await client.get(
                                f"{self.base}/{creation_id}",
                                params={"fields": "status_code", "access_token": self.token},
                            )
                        ).json()
                        if st.get("status_code") == "FINISHED":
                            break
                        if st.get("status_code") == "ERROR":
                            return PublishResult(success=False, error_message="Instagram: video processing failed")
                        await asyncio.sleep(2)

                pub = await client.post(
                    f"{self.base}/{self.user_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": self.token},
                )
                pdata = pub.json()
                if not pub.is_success or "id" not in pdata:
                    return self._error(pdata, pub)
        except Exception as e:
            return PublishResult(success=False, error_message=str(e))

        media_id = str(pdata["id"])
        return PublishResult(
            success=True,
            external_post_id=media_id,
            url=f"https://www.instagram.com/p/{media_id}",
            metadata={"creation_id": creation_id, **pdata},
        )

    @staticmethod
    def _error(data: Dict[str, Any], resp: httpx.Response) -> PublishResult:
        msg = (data.get("error") or {}).get("message") or resp.text
        return PublishResult(success=False, error_message=f"Instagram: {msg}", metadata=data)

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not self.token or not external_post_id:
            return {}
        out: Dict[str, float] = {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                fields = await client.get(
                    f"{self.base}/{external_post_id}",
                    params={"fields": "like_count,comments_count", "access_token": self.token},
                )
                if fields.is_success:
                    d = fields.json()
                    out["reactions"] = float(d.get("like_count") or 0)
                    out["comments"] = float(d.get("comments_count") or 0)
                insights = await client.get(
                    f"{self.base}/{external_post_id}/insights",
                    params={"metric": "views,saved,shares", "access_token": self.token},
                )
            if insights.is_success:
                names = {"views": "views", "saved": "saves", "shares": "shares"}
                for m in insights.json().get("data", []):
                    values = m.get("values") or [{}]
                    value = (m.get("total_value") or {}).get("value", values[0].get("value"))
                    if m.get("name") in names and value is not None:
                        out[names[m["name"]]] = float(value)
        except Exception:
            return out
        return out

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
