import importlib
from typing import Dict, Any, Type
from app.adapters.base import BaseAdapter
from app.adapters.telegram import TelegramAdapter
from app.adapters.youtube import YouTubeAdapter
from app.adapters.mastodon import MastodonAdapter
from app.adapters.bluesky import BlueskyAdapter
from app.adapters.local_archive import LocalArchiveAdapter
from app.adapters.facebook import FacebookAdapter
from app.adapters.instagram import InstagramAdapter
from app.adapters.tiktok import TikTokAdapter
from app.core.security import decrypt_credentials
from app.models.entities import ConnectedAccount, Platform

BUILTIN_ADAPTERS: Dict[str, Type[BaseAdapter]] = {
    "telegram": TelegramAdapter,
    "youtube": YouTubeAdapter,
    "mastodon": MastodonAdapter,
    "bluesky": BlueskyAdapter,
    "facebook": FacebookAdapter,
    "instagram": InstagramAdapter,
    "tiktok": TikTokAdapter,
    "local_archive": LocalArchiveAdapter,
}


def get_adapter_for_account(account: ConnectedAccount) -> BaseAdapter:
    """
    Instantiates the appropriate adapter for a connected account,
    decrypting its credentials.
    """
    platform_code = account.platform.code if account.platform else ""
    credentials = {}
    if account.credentials_encrypted:
        try:
            credentials = decrypt_credentials(account.credentials_encrypted)
        except Exception:
            credentials = {}

    if platform_code in BUILTIN_ADAPTERS:
        return BUILTIN_ADAPTERS[platform_code](credentials)

    # Dynamic import if custom adapter class is specified
    if account.platform and account.platform.adapter_class:
        try:
            module_name, class_name = account.platform.adapter_class.rsplit(".", 1)
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            return cls(credentials)
        except Exception:
            pass

    return LocalArchiveAdapter(credentials)


def get_adapter_by_code(platform_code: str, credentials: Dict[str, Any] = None) -> BaseAdapter:
    cls = BUILTIN_ADAPTERS.get(platform_code, LocalArchiveAdapter)
    return cls(credentials or {})
