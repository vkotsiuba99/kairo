from app.adapters.base import BaseAdapter, RawEvent, NormalizedEvent, PublishResult
from app.adapters.telegram import TelegramAdapter
from app.adapters.youtube import YouTubeAdapter
from app.adapters.mastodon import MastodonAdapter
from app.adapters.bluesky import BlueskyAdapter
from app.adapters.local_archive import LocalArchiveAdapter
from app.adapters.registry import get_adapter_for_account, get_adapter_by_code, BUILTIN_ADAPTERS

__all__ = [
    "BaseAdapter",
    "RawEvent",
    "NormalizedEvent",
    "PublishResult",
    "TelegramAdapter",
    "YouTubeAdapter",
    "MastodonAdapter",
    "BlueskyAdapter",
    "LocalArchiveAdapter",
    "get_adapter_for_account",
    "get_adapter_by_code",
    "BUILTIN_ADAPTERS",
]
