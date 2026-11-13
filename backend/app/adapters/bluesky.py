import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.adapters.base import (
    ActorInteraction,
    BaseAdapter,
    NormalizedEvent,
    PublishResult,
    RawEvent,
)


class BlueskyAdapter(BaseAdapter):
    """
    Bluesky (AT Protocol) Adapter.
    Credentials:
      - handle: str (e.g. 'myuser.bsky.social')
      - app_password: str
      - service: str (default 'https://bsky.social')
    """

    # AT Protocol віддає лічильники допису одним запитом і показує авторів
    # взаємодій у стрічці сповіщень. Показів (impressions) протокол не публікує.
    COUNTER_METRICS = ["reactions", "comments", "shares"]
    SUPPORTS_PER_ACTOR = True
    TELEMETRY_NOTE = (
        "Likes, replies, reposts and quotes per post plus the authors of interactions. "
        "The AT Protocol does not expose impressions."
    )

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.handle = self.credentials.get("handle", "")
        self.app_password = self.credentials.get("app_password", "")
        self.service = self.credentials.get("service", "https://bsky.social").rstrip("/")
        self.access_jwt: Optional[str] = None
        self.did: Optional[str] = None

    async def _authenticate(self, client: httpx.AsyncClient) -> bool:
        if not self.handle or not self.app_password:
            return False
        try:
            resp = await client.post(
                f"{self.service}/xrpc/com.atproto.server.createSession",
                json={"identifier": self.handle, "password": self.app_password}
            )
            if resp.is_success:
                data = resp.json()
                self.access_jwt = data.get("accessJwt")
                self.did = data.get("did")
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
        if not self.handle or not self.app_password:
            return PublishResult(
                success=True,
                external_post_id=f"bsky_sim_{content_item_id}",
                url=f"https://bsky.app/profile/{self.handle or 'demo'}/post/{content_item_id}",
                metadata={"mode": "simulated", "note": "Bluesky credentials not configured"}
            )

        # Bluesky limits to 300 graphemes
        post_text = body
        if title:
            post_text = f"{title}\n\n{post_text}"
        if hashtags:
            tags_str = " ".join([tag if tag.startswith("#") else f"#{tag}" for tag in hashtags])
            post_text = f"{post_text}\n\n{tags_str}"

        post_text = post_text[:300]

        async with httpx.AsyncClient(timeout=30.0) as client:
            if not await self._authenticate(client):
                return PublishResult(success=False, error_message="Bluesky auth failed")

            headers = {"Authorization": f"Bearer {self.access_jwt}"}
            record: Dict[str, Any] = {
                "text": post_text,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "$type": "app.bsky.feed.post",
            }

            # If image exists, upload blob to Bluesky and embed
            image_path = media_refs.get("image")
            if image_path:
                try:
                    with open(image_path, "rb") as img_file:
                        blob_data = img_file.read()
                    blob_resp = await client.post(
                        f"{self.service}/xrpc/com.atproto.repo.uploadBlob",
                        headers={
                            "Authorization": f"Bearer {self.access_jwt}",
                            "Content-Type": "image/jpeg",
                        },
                        content=blob_data,
                    )
                    if blob_resp.is_success:
                        blob_json = blob_resp.json()
                        blob_ref = blob_json.get("blob")
                        if blob_ref:
                            record["embed"] = {
                                "$type": "app.bsky.embed.images",
                                "images": [
                                    {
                                        "alt": title or "Kairo AI Generated Illustration",
                                        "image": blob_ref,
                                    }
                                ],
                            }
                except Exception as blob_err:
                    pass

            try:
                resp = await client.post(
                    f"{self.service}/xrpc/com.atproto.repo.createRecord",
                    headers=headers,
                    json={
                        "repo": self.did,
                        "collection": "app.bsky.feed.post",
                        "record": record
                    }
                )
                if resp.is_success:
                    data = resp.json()
                    uri = data.get("uri", "")  # at://did:.../app.bsky.feed.post/<rkey>
                    rkey = uri.split("/")[-1] if "/" in uri else uri
                    return PublishResult(
                        success=True,
                        external_post_id=rkey,
                        url=f"https://bsky.app/profile/{self.handle}/post/{rkey}",
                        metadata=data
                    )
                else:
                    return PublishResult(
                        success=False,
                        error_message=f"Bluesky post failed: {resp.status_code} - {resp.text}"
                    )
            except Exception as e:
                return PublishResult(success=False, error_message=str(e))

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not external_post_id:
            return {}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if not await self._authenticate(client):
                    return {}
                uri = (
                    external_post_id
                    if external_post_id.startswith("at://")
                    else f"at://{self.did}/app.bsky.feed.post/{external_post_id}"
                )
                resp = await client.get(
                    f"{self.service}/xrpc/app.bsky.feed.getPosts",
                    params={"uris": uri},
                    headers={"Authorization": f"Bearer {self.access_jwt}"},
                )
            if resp.is_success:
                posts = resp.json().get("posts", [])
                if posts:
                    d = posts[0]
                    return {
                        "reactions": float(d.get("likeCount") or 0),
                        "comments": float(d.get("replyCount") or 0),
                        "shares": float(d.get("repostCount") or 0)
                        + float(d.get("quoteCount") or 0),
                    }
        except Exception:
            pass
        return {}

    async def fetch_interactions(self, since: datetime) -> List[ActorInteraction]:
        out: List[ActorInteraction] = []
        if not self.handle or not self.app_password:
            return out
        reason_map = {"like": "reaction", "repost": "share", "reply": "comment", "quote": "share"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if not await self._authenticate(client):
                    return out
                resp = await client.get(
                    f"{self.service}/xrpc/app.bsky.notification.listNotifications",
                    params={"limit": 50},
                    headers={"Authorization": f"Bearer {self.access_jwt}"},
                )
            if not resp.is_success:
                return out
            for item in resp.json().get("notifications", []):
                kind = reason_map.get(item.get("reason"))
                if not kind:
                    continue
                subject = item.get("reasonSubject") or ""
                rkey = subject.rsplit("/", 1)[-1] if "/" in subject else ""
                author = item.get("author") or {}
                try:
                    occurred = datetime.fromisoformat(
                        item.get("indexedAt", "").replace("Z", "+00:00")
                    )
                except ValueError:
                    occurred = datetime.now(timezone.utc)
                if since and occurred < since:
                    continue
                record = item.get("record") or {}
                out.append(
                    ActorInteraction(
                        external_user_id=str(author.get("handle") or author.get("did") or ""),
                        display_name=author.get("displayName") or author.get("handle") or "",
                        interaction_type=kind,
                        external_post_id=rkey,
                        occurred_at=occurred,
                        text=str(record.get("text") or "")[:500],
                    )
                )
        except Exception:
            return out
        return out

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        # Fetch notifications / interactions from Bluesky
        events = []
        if not self.handle or not self.app_password:
            return events

        async with httpx.AsyncClient(timeout=15.0) as client:
            if not await self._authenticate(client):
                return events
            headers = {"Authorization": f"Bearer {self.access_jwt}"}
            try:
                resp = await client.get(
                    f"{self.service}/xrpc/app.bsky.notification.listNotifications",
                    headers=headers
                )
                if resp.is_success:
                    data = resp.json()
                    for item in data.get("notifications", []):
                        reason = item.get("reason")
                        reason_subject = item.get("reasonSubject", "")
                        rkey = reason_subject.split("/")[-1] if "/" in reason_subject else ""

                        event_type = "reaction" if reason == "like" else "share" if reason == "repost" else "comment"
                        events.append(
                            RawEvent(
                                event_id=item.get("cid", str(datetime.now().timestamp())),
                                external_post_id=rkey,
                                event_type=event_type,
                                event_value=1.0,
                                occurred_at=datetime.now(timezone.utc),
                                raw_payload=item
                            )
                        )
            except Exception:
                pass
        return events

    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        return NormalizedEvent(
            external_post_id=raw.external_post_id,
            event_type=raw.event_type,
            event_value=raw.event_value,
            occurred_at=raw.occurred_at,
            raw_payload=raw.raw_payload,
        )
