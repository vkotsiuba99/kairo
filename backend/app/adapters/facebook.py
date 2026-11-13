from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.adapters.base import (
    BaseAdapter,
    NormalizedEvent,
    PublishResult,
    RawEvent,
    tags_line,
)
from app.core.config import settings


class FacebookAdapter(BaseAdapter):
    """
    Facebook Pages (Graph API).
    Credentials:
      - page_id: str
      - page_access_token: str (токен сторінки з дозволом pages_manage_posts)

    Текст — POST /{page_id}/feed, фото — /{page_id}/photos, відео — /{page_id}/videos.
    """

    COUNTER_METRICS = ["reactions", "comments", "shares"]
    SUPPORTS_PER_ACTOR = False
    TELEMETRY_NOTE = (
        "The Graph API returns total reactions, comments and shares per post. "
        "Page reach is available via Insights with the read_insights permission."
    )

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.page_id = self.credentials.get("page_id", "")
        self.token = self.credentials.get("page_access_token", "")
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
        if not self.page_id or not self.token:
            return PublishResult(
                success=True,
                external_post_id=f"fb_sim_{content_item_id}",
                url=f"https://facebook.com/{content_item_id}",
                metadata={"mode": "simulated", "note": "Facebook page token not configured"},
            )

        text = f"{title}\n\n{body}" if title else body
        if hashtags:
            text = f"{text}\n\n{tags_line(hashtags)}"

        params = {"access_token": self.token}
        video, image = media_refs.get("video"), media_refs.get("image")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if video:
                    with open(video, "rb") as f:
                        resp = await client.post(
                            f"{self.base}/{self.page_id}/videos",
                            params=params, data={"description": text}, files={"source": f},
                        )
                elif image:
                    with open(image, "rb") as f:
                        resp = await client.post(
                            f"{self.base}/{self.page_id}/photos",
                            params=params, data={"caption": text}, files={"source": f},
                        )
                else:
                    resp = await client.post(
                        f"{self.base}/{self.page_id}/feed", params=params, data={"message": text}
                    )
            data = resp.json()
        except Exception as e:
            return PublishResult(success=False, error_message=str(e))

        if not resp.is_success or "error" in data:
            msg = (data.get("error") or {}).get("message") or resp.text
            return PublishResult(success=False, error_message=f"Facebook: {msg}", metadata=data)
        # /photos повертає post_id допису стрічки, /feed і /videos — id
        post_id = str(data.get("post_id") or data.get("id"))
        return PublishResult(
            success=True,
            external_post_id=post_id,
            url=f"https://www.facebook.com/{post_id}",
            metadata=data,
        )

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not self.token or not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base}/{external_post_id}",
                    params={
                        "access_token": self.token,
                        "fields": "reactions.summary(total_count),comments.summary(total_count),shares",
                    },
                )
            if not resp.is_success:
                return {}
            d = resp.json()
            return {
                "reactions": float(((d.get("reactions") or {}).get("summary") or {}).get("total_count") or 0),
                "comments": float(((d.get("comments") or {}).get("summary") or {}).get("total_count") or 0),
                "shares": float((d.get("shares") or {}).get("count") or 0),
            }
        except Exception:
            return {}

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        return []  # телеметрія збирається через fetch_post_metrics

    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        return NormalizedEvent(
            external_post_id=raw.external_post_id,
            event_type=raw.event_type,
            event_value=raw.event_value,
            occurred_at=raw.occurred_at,
            raw_payload=raw.raw_payload,
        )
