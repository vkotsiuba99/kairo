import os
import json
import shutil
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.adapters.base import BaseAdapter, RawEvent, NormalizedEvent, PublishResult
from app.core.config import settings


class LocalArchiveAdapter(BaseAdapter):
    """
    Local Archive Adapter: saves published content and metadata to disk.
    Path: /data/archive/{platform_code}/{yyyy-mm-dd}/{content_item_id}.json
    """

    def __init__(self, credentials: Optional[Dict[str, Any]] = None):
        super().__init__(credentials)
        self.archive_base = self.credentials.get("archive_dir", settings.DATA_ARCHIVE_PATH)

    async def publish(
        self,
        content_item_id: int,
        title: Optional[str],
        body: str,
        media_refs: Dict[str, Any],
        hashtags: List[str],
        format_type: str,
    ) -> PublishResult:
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            platform_code = "local_archive"
            target_dir = os.path.join(self.archive_base, platform_code, today_str)
            os.makedirs(target_dir, exist_ok=True)

            archive_payload = {
                "content_item_id": content_item_id,
                "title": title,
                "body": body,
                "hashtags": hashtags,
                "format": format_type,
                "media_refs": media_refs,
                "archived_at": datetime.now(timezone.utc).isoformat(),
            }

            json_filename = f"{content_item_id}.json"
            json_filepath = os.path.join(target_dir, json_filename)

            with open(json_filepath, "w", encoding="utf-8") as f:
                json.dump(archive_payload, f, ensure_ascii=False, indent=2)

            # Copy media files if they exist locally
            copied_media = []
            for media_type, media_path in media_refs.items():
                if isinstance(media_path, str) and os.path.exists(media_path):
                    ext = os.path.splitext(media_path)[1]
                    dest_file = os.path.join(target_dir, f"{content_item_id}_{media_type}{ext}")
                    shutil.copy2(media_path, dest_file)
                    copied_media.append(dest_file)

            return PublishResult(
                success=True,
                external_post_id=f"local_{content_item_id}_{today_str}",
                url=f"file:///{os.path.abspath(json_filepath).replace(os.sep, '/')}",
                metadata={"file_path": json_filepath, "copied_media": copied_media},
            )
        except Exception as e:
            return PublishResult(success=False, error_message=str(e))

    async def fetch_events(self, since: datetime) -> List[RawEvent]:
        # For local archive, telemetry can be read from simulated reaction log or mock interaction
        events = []
        return events

    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        return NormalizedEvent(
            external_post_id=raw.external_post_id,
            event_type=raw.event_type,
            event_value=raw.event_value,
            occurred_at=raw.occurred_at,
            raw_payload=raw.raw_payload,
        )
