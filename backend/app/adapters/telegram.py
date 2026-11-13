import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.adapters.base import BaseAdapter, RawEvent, NormalizedEvent, PublishResult
from app.core.config import settings


class TelegramAdapter(BaseAdapter):
    """
    Telegram Bot API Adapter.
    Credentials:
      - bot_token: str
      - chat_id: str (e.g. '@mychannel' or numeric id)
    """

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.bot_token = self.credentials.get("bot_token", "")
        self.chat_id = self.credentials.get("chat_id", "")
        self.base_url = f"{settings.TELEGRAM_API_BASE.rstrip('/')}/bot{self.bot_token}"

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        if not self.bot_token or not self.chat_id:
            # Demo/offline fallback simulation
            return PublishResult(
                success=True,
                external_post_id=f"tg_sim_{content_item_id}",
                url=f"https://t.me/c/{content_item_id}",
                metadata={"mode": "simulated", "note": "Telegram bot_token not configured"}
            )

        import html
        text_body = html.escape(body)
        text_title = html.escape(title) if title else ""
        text_to_send = f"<b>{text_title}</b>\n\n{text_body}" if text_title else text_body
        if hashtags:
            tags_str = " ".join([tag if tag.startswith("#") else f"#{tag}" for tag in hashtags])
            text_to_send = f"{text_to_send}\n\n{html.escape(tags_str)}"

        plain_text = f"{title}\n\n{body}" if title else body
        if hashtags:
            tags_str = " ".join([tag if tag.startswith("#") else f"#{tag}" for tag in hashtags])
            plain_text = f"{plain_text}\n\n{tags_str}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # If image exists
                image_path = media_refs.get("image")
                video_path = media_refs.get("video")

                if video_path:
                    url = f"{self.base_url}/sendVideo"
                    with open(video_path, "rb") as vf:
                        resp = await client.post(
                            url,
                            data={"chat_id": self.chat_id, "caption": text_to_send[:1024], "parse_mode": "HTML"},
                            files={"video": vf},
                        )
                elif image_path:
                    url = f"{self.base_url}/sendPhoto"
                    with open(image_path, "rb") as imgf:
                        resp = await client.post(
                            url,
                            data={"chat_id": self.chat_id, "caption": text_to_send[:1024], "parse_mode": "HTML"},
                            files={"photo": imgf},
                        )
                else:
                    url = f"{self.base_url}/sendMessage"
                    resp = await client.post(
                        url,
                        json={
                            "chat_id": self.chat_id,
                            "text": text_to_send[:4096],
                            "parse_mode": "HTML",
                            "disable_web_page_preview": False,
                        },
                    )
                    # Fallback to plain text if HTML parsing failed
                    if not resp.is_success or not resp.json().get("ok"):
                        resp = await client.post(
                            url,
                            json={
                                "chat_id": self.chat_id,
                                "text": plain_text[:4096],
                                "disable_web_page_preview": False,
                            },
                        )

                data = resp.json()
                if data.get("ok"):
                    msg_id = str(data["result"]["message_id"])
                    chat_username = data["result"]["chat"].get("username", "")
                    post_url = f"https://t.me/{chat_username}/{msg_id}" if chat_username else f"https://t.me/c/{self.chat_id.replace('@', '')}/{msg_id}"
                    return PublishResult(
                        success=True,
                        external_post_id=msg_id,
                        url=post_url,
                        metadata=data["result"],
                    )
                else:
                    desc = data.get("description", "Telegram API error")
                    if "not a member" in desc.lower() or "chat not found" in desc.lower() or "bot was blocked" in desc.lower():
                        desc = f"The bot is not an admin of the channel ({desc}). Open {self.chat_id} in Telegram -> Manage channel -> Administrators -> add the bot with posting rights."
                    return PublishResult(
                        success=False,
                        error_message=desc,
                        metadata=data,
                    )
            except Exception as e:
                return PublishResult(success=False, error_message=str(e))

    # Bot API не віддає ані переглядів, ані авторів реакцій у каналі: перегляди
    # живуть лише в MTProto, а реакції в каналі анонімні. Тому з Telegram
    # знімається єдиний сигнал — агрегатна кількість реакцій на власних дописах.
    COUNTER_METRICS = ["reactions"]
    SUPPORTS_PER_ACTOR = False
    TELEMETRY_NOTE = (
        "The Bot API exposes only the total number of reactions on own posts. "
        "Views and reaction authors are not available to bots in channels."
    )

    async def fetch_channel_counters(self) -> Dict[str, Dict[str, float]]:
        """
        Абсолютні лічильники реакцій із потоку оновлень.

        Telegram надсилає message_reaction_count із повним підсумком по допису,
        тому значення можна брати як є: приріст порахує воркер.
        """
        counters: Dict[str, Dict[str, float]] = {}
        if not self.bot_token:
            return counters
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.base_url}/getUpdates",
                    params={"allowed_updates": '["message_reaction_count"]'},
                )
            data = resp.json()
            if not data.get("ok"):
                return counters
            for update in data.get("result", []):
                block = update.get("message_reaction_count")
                if not block:
                    continue
                post_id = str(block.get("message_id"))
                total = sum(float(r.get("total_count") or 0) for r in block.get("reactions", []))
                counters[post_id] = {"reactions": total}
        except Exception:
            return counters
        return counters

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        events = []
        if not self.bot_token:
            return events

        # Pull updates via getUpdates
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(f"{self.base_url}/getUpdates")
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        if "message_reaction" in update:
                            rx = update["message_reaction"]
                            events.append(
                                RawEvent(
                                    event_id=str(update["update_id"]),
                                    external_post_id=str(rx.get("message_id")),
                                    event_type="reaction",
                                    event_value=1.0,
                                    occurred_at=datetime.fromtimestamp(rx.get("date", 0), tz=timezone.utc),
                                    raw_payload=rx,
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
