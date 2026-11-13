import re
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


class MastodonAdapter(BaseAdapter):
    """
    Mastodon (ActivityPub / REST API) Adapter.
    Credentials:
      - instance_url: str (e.g. 'https://mastodon.social')
      - access_token: str
    """

    # Mastodon віддає лічильники допису напряму і показує, хто саме взаємодіяв,
    # тому це найповніша телеметрія в системі — окрім переглядів, яких у
    # публічному API немає взагалі.
    COUNTER_METRICS = ["reactions", "comments", "shares"]
    SUPPORTS_PER_ACTOR = True
    TELEMETRY_NOTE = (
        "Favourites, replies and boosts per post plus the authors of interactions. "
        "Mastodon does not count views at all."
    )

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.instance_url = self.credentials.get("instance_url", "https://mastodon.social").rstrip("/")
        self.access_token = self.credentials.get("access_token", "")

    async def fetch_post_metrics(self, external_post_id: str) -> Dict[str, float]:
        if not self.access_token or not external_post_id:
            return {}
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.instance_url}/api/v1/statuses/{external_post_id}", headers=headers
                )
            if resp.is_success:
                d = resp.json()
                return {
                    "reactions": float(d.get("favourites_count") or 0),
                    "comments": float(d.get("replies_count") or 0),
                    "shares": float(d.get("reblogs_count") or 0),
                }
        except Exception:
            pass
        return {}

    async def fetch_interactions(self, since: datetime) -> List[ActorInteraction]:
        if not self.access_token:
            return []
        headers = {"Authorization": f"Bearer {self.access_token}"}
        type_map = {"favourite": "reaction", "reblog": "share", "mention": "comment"}
        out: List[ActorInteraction] = []
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.instance_url}/api/v1/notifications?limit=40", headers=headers
                )
            if not resp.is_success:
                return []
            for n in resp.json():
                kind = type_map.get(n.get("type"))
                status = n.get("status") or {}
                account = n.get("account") or {}
                if not kind or not status:
                    continue
                try:
                    occurred = datetime.fromisoformat(
                        n.get("created_at", "").replace("Z", "+00:00")
                    )
                except ValueError:
                    occurred = datetime.now(timezone.utc)
                if since and occurred < since:
                    continue
                out.append(
                    ActorInteraction(
                        external_user_id=str(account.get("acct") or account.get("id") or ""),
                        display_name=account.get("display_name") or account.get("username") or "",
                        interaction_type=kind,
                        external_post_id=str(status.get("id") or ""),
                        occurred_at=occurred,
                        text=re.sub(r"<[^>]+>", " ", status.get("content") or "")[:500],
                    )
                )
        except Exception:
            return out
        return out

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        if not self.access_token:
            return PublishResult(
                success=True,
                external_post_id=f"masto_sim_{content_item_id}",
                url=f"{self.instance_url}/@bot/{content_item_id}",
                metadata={"mode": "simulated", "note": "Mastodon token not configured"}
            )

        status_text = body
        if title:
            status_text = f"{title}\n\n{status_text}"
        if hashtags:
            tags_str = " ".join([tag if tag.startswith("#") else f"#{tag}" for tag in hashtags])
            status_text = f"{status_text}\n\n{tags_str}"

        # Mastodon typically limits to 500 characters
        status_text = status_text[:500]

        headers = {"Authorization": f"Bearer {self.access_token}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                media_ids = []
                image_path = media_refs.get("image")
                if image_path:
                    # Upload media first
                    with open(image_path, "rb") as mf:
                        media_resp = await client.post(
                            f"{self.instance_url}/api/v2/media",
                            headers=headers,
                            files={"file": mf}
                        )
                    if media_resp.is_success:
                        media_ids.append(media_resp.json().get("id"))

                payload = {"status": status_text}
                if media_ids:
                    payload["media_ids"] = media_ids

                resp = await client.post(
                    f"{self.instance_url}/api/v1/statuses",
                    headers=headers,
                    json=payload
                )
                if resp.is_success:
                    data = resp.json()
                    return PublishResult(
                        success=True,
                        external_post_id=str(data["id"]),
                        url=data.get("url"),
                        metadata=data
                    )
                else:
                    return PublishResult(
                        success=False,
                        error_message=f"Mastodon error: {resp.status_code} - {resp.text}"
                    )
            except Exception as e:
                return PublishResult(success=False, error_message=str(e))

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        events = []
        if not self.access_token:
            return events

        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(f"{self.instance_url}/api/v1/notifications", headers=headers)
                if resp.is_success:
                    notifs = resp.json()
                    for n in notifs:
                        notif_type = n.get("type")
                        status_info = n.get("status")
                        if not status_info:
                            continue
                        post_id = str(status_info.get("id"))

                        event_map = {
                            "favourite": "reaction",
                            "reblog": "share",
                            "mention": "comment",
                        }
                        if notif_type in event_map:
                            events.append(
                                RawEvent(
                                    event_id=str(n.get("id")),
                                    external_post_id=post_id,
                                    event_type=event_map[notif_type],
                                    event_value=1.0,
                                    occurred_at=datetime.now(timezone.utc),
                                    raw_payload=n
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
