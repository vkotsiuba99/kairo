import os
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.adapters.base import BaseAdapter, RawEvent, NormalizedEvent, PublishResult
from app.core.config import settings


class YouTubeAdapter(BaseAdapter):
    """
    YouTube Data API v3 Adapter.
    Credentials:
      - client_id: str
      - client_secret: str
      - refresh_token: str
    """

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.client_id = self.credentials.get("client_id", "")
        self.client_secret = self.credentials.get("client_secret", "")
        self.refresh_token = self.credentials.get("refresh_token", "")
        self.access_token: Optional[str] = None

    async def _refresh_access_token(self, client: httpx.AsyncClient) -> bool:
        if not self.refresh_token or not self.client_id or not self.client_secret:
            return False
        try:
            resp = await client.post(
                settings.GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                }
            )
            if resp.is_success:
                data = resp.json()
                self.access_token = data.get("access_token")
                return True
        except Exception:
            pass
        return False

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        video_path = media_refs.get("video")

        if not self.refresh_token or not video_path or not os.path.exists(video_path):
            # Demo / simulation response
            video_id = f"yt_sim_{content_item_id}"
            is_shorts = format_type == "video_short" or "#shorts" in (title or "").lower() or "#shorts" in body.lower()
            url = f"https://youtube.com/shorts/{video_id}" if is_shorts else f"https://youtube.com/watch?v={video_id}"
            return PublishResult(
                success=True,
                external_post_id=video_id,
                url=url,
                metadata={"mode": "simulated", "note": "YouTube OAuth or local video not fully present"}
            )

        async with httpx.AsyncClient(timeout=120.0) as client:
            if not await self._refresh_access_token(client):
                return PublishResult(success=False, error_message="Failed to refresh Google OAuth token")

            # Prepare metadata
            tags = [t.lstrip("#") for t in hashtags]
            if format_type == "video_short" and "shorts" not in tags:
                tags.append("shorts")

            snippet = {
                "title": (title or f"Video #{content_item_id}")[:100],
                "description": body[:5000],
                "tags": tags,
                "categoryId": "28",  # Science & Technology
            }
            status = {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            }

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(os.path.getsize(video_path)),
            }

            try:
                # Step 1: Initiate resumable upload session
                init_url = (
                    f"{settings.GOOGLE_API_BASE.rstrip('/')}/upload/youtube/v3/videos"
                    "?uploadType=resumable&part=snippet,status"
                )
                init_resp = await client.post(
                    init_url,
                    headers=headers,
                    json={"snippet": snippet, "status": status}
                )

                if init_resp.status_code != 200:
                    return PublishResult(success=False, error_message=f"Upload init failed: {init_resp.text}")

                upload_url = init_resp.headers.get("Location")
                if not upload_url:
                    return PublishResult(success=False, error_message="No resumable upload URL returned")

                # Step 2: Upload video file
                with open(video_path, "rb") as vf:
                    upload_resp = await client.put(
                        upload_url,
                        headers={"Content-Type": "video/mp4"},
                        content=vf.read()
                    )

                if upload_resp.status_code in (200, 201):
                    data = upload_resp.json()
                    vid = data.get("id")
                    url = f"https://youtube.com/shorts/{vid}" if format_type == "video_short" else f"https://youtube.com/watch?v={vid}"
                    return PublishResult(
                        success=True,
                        external_post_id=vid,
                        url=url,
                        metadata=data
                    )
                else:
                    return PublishResult(success=False, error_message=f"Upload failed: {upload_resp.text}")

            except Exception as e:
                return PublishResult(success=False, error_message=str(e))

    # Відео для Shorts система збирає з ілюстрації та синтезованого озвучення
    # (VideoComposer) — без окремої відео-моделі, тож це слайд-відео, а не
    # повноцінний ролик. Телеметрія YouTube найповніша з усіх платформ.
    COUNTER_METRICS = ["views", "reactions", "comments"]
    SUPPORTS_PER_ACTOR = False
    TELEMETRY_NOTE = (
        "Data API v3 returns viewCount, likeCount and commentCount per video; "
        "the Analytics API adds impressions, CTR and watch time."
    )

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not external_post_id or external_post_id.startswith("yt_sim_"):
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if not await self._refresh_access_token(client):
                    return {}
                resp = await client.get(
                    f"{settings.GOOGLE_API_BASE.rstrip('/')}/youtube/v3/videos",
                    params={"part": "statistics", "id": external_post_id},
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
            items = resp.json().get("items", []) if resp.is_success else []
            if not items:
                return {}
            s = items[0].get("statistics", {})
            return {
                "views": float(s.get("viewCount") or 0),
                "reactions": float(s.get("likeCount") or 0),
                "comments": float(s.get("commentCount") or 0),
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
