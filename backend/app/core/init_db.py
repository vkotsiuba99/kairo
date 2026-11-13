import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import engine, Base, AsyncSessionLocal
from app.models.entities import Platform, PolicyArm

logger = logging.getLogger("kairo.init_db")

DEFAULT_PLATFORMS = [
    {
        "code": "telegram",
        "display_name": "Telegram",
        "adapter_class": "app.adapters.telegram.TelegramAdapter",
        "config_schema": {
            "fields": [
                {"name": "bot_token", "type": "string", "required": True, "secret": True},
                {"name": "chat_id", "type": "string", "required": True}
            ]
        }
    },
    {
        "code": "youtube",
        "display_name": "YouTube (Shorts & videos)",
        "adapter_class": "app.adapters.youtube.YouTubeAdapter",
        "config_schema": {
            "fields": [
                {"name": "client_id", "type": "string", "required": True},
                {"name": "client_secret", "type": "string", "required": True, "secret": True},
                {"name": "refresh_token", "type": "string", "required": True, "secret": True}
            ]
        }
    },
    {
        "code": "mastodon",
        "display_name": "Mastodon (ActivityPub)",
        "adapter_class": "app.adapters.mastodon.MastodonAdapter",
        "config_schema": {
            "fields": [
                {"name": "instance_url", "type": "string", "required": True},
                {"name": "access_token", "type": "string", "required": True, "secret": True}
            ]
        }
    },
    {
        "code": "bluesky",
        "display_name": "Bluesky (AT Protocol)",
        "adapter_class": "app.adapters.bluesky.BlueskyAdapter",
        "config_schema": {
            "fields": [
                {"name": "handle", "type": "string", "required": True},
                {"name": "app_password", "type": "string", "required": True, "secret": True}
            ]
        }
    },
    {
        "code": "facebook",
        "display_name": "Facebook (Page)",
        "adapter_class": "app.adapters.facebook.FacebookAdapter",
        "config_schema": {
            "fields": [
                {"name": "page_id", "type": "string", "required": True},
                {"name": "page_access_token", "type": "string", "required": True, "secret": True}
            ]
        }
    },
    {
        "code": "instagram",
        "display_name": "Instagram (professional account)",
        "adapter_class": "app.adapters.instagram.InstagramAdapter",
        "config_schema": {
            "fields": [
                {"name": "ig_user_id", "type": "string", "required": True},
                {"name": "access_token", "type": "string", "required": True, "secret": True}
            ]
        }
    },
    {
        "code": "tiktok",
        "display_name": "TikTok",
        "adapter_class": "app.adapters.tiktok.TikTokAdapter",
        "config_schema": {
            "fields": [
                {"name": "access_token", "type": "string", "required": True, "secret": True},
                {"name": "privacy_level", "type": "string", "required": False, "default": "SELF_ONLY"}
            ]
        }
    },
    {
        "code": "local_archive",
        "display_name": "Local archive",
        "adapter_class": "app.adapters.local_archive.LocalArchiveAdapter",
        "config_schema": {
            "fields": [
                {"name": "archive_dir", "type": "string", "required": False, "default": "data/archive"}
            ]
        }
    }
]

DEFAULT_POLICY_ARMS = [
    # Topics
    ("topic", "ai_breakthroughs"),
    ("topic", "developer_humor"),
    ("topic", "open_source_tools"),
    ("topic", "tech_career_tips"),
    ("topic", "system_architecture"),

    # Formats
    ("format", "text"),
    ("format", "image_post"),
    ("format", "video_short"),
    ("format", "educational_thread"),

    # Tones
    ("tone", "analytical"),
    ("tone", "provocative"),
    ("tone", "humorous"),
    ("tone", "inspirational"),

    # Lengths
    ("length", "short"),
    ("length", "medium"),
    ("length", "long"),

    # Time slots
    ("time_slot", "morning_08_10"),
    ("time_slot", "lunch_12_14"),
    ("time_slot", "evening_18_20"),
    ("time_slot", "night_21_23"),

    # Backends (Cost-aware)
    ("backend", "local"),
    ("backend", "cloud"),
]


async def init_db():
    async with engine.begin() as conn:
        try:
            from sqlalchemy import text
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        except Exception as e:
            logger.info("Extension vector creation skipped: %s", e)
        await conn.run_sync(Base.metadata.create_all)

        # create_all не змінює вже створені таблиці, тому нові колонки
        # додаються окремо й ідемпотентно — без окремого інструмента міграцій.
        from sqlalchemy import text as _text

        added_columns = [
            ("audience_segments", "profile", "JSON"),
            ("audience_segments", "activity_hours", "JSON"),
            ("audience_segments", "source", "VARCHAR(32) DEFAULT 'manual'"),
            ("connected_accounts", "posting_schedule", "JSON"),
            ("posts", "attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("posts", "claimed_at", "TIMESTAMP WITH TIME ZONE"),
        ]
        for table, column, coltype in added_columns:
            try:
                await conn.execute(
                    _text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}")
                )
            except Exception:
                # SQLite не знає IF NOT EXISTS для колонок — пробуємо без нього
                try:
                    await conn.execute(_text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
                except Exception as e:
                    logger.debug("Column %s.%s already present (%s)", table, column, e)

    async with AsyncSessionLocal() as session:
        # Seed platforms if not present
        for plat_data in DEFAULT_PLATFORMS:
            res = await session.execute(select(Platform).where(Platform.code == plat_data["code"]))
            existing = res.scalar_one_or_none()
            if not existing:
                platform = Platform(
                    code=plat_data["code"],
                    display_name=plat_data["display_name"],
                    adapter_class=plat_data["adapter_class"],
                    config_schema=plat_data["config_schema"],
                )
                session.add(platform)

        # Seed policy arms
        for dim, val in DEFAULT_POLICY_ARMS:
            res = await session.execute(
                select(PolicyArm).where(PolicyArm.dimension == dim, PolicyArm.value == val)
            )
            existing = res.scalar_one_or_none()
            if not existing:
                arm = PolicyArm(dimension=dim, value=val)
                session.add(arm)

        await session.flush()

        # Seed / Connect Accounts from environment if configured
        from app.models.entities import ConnectedAccount
        from app.core.security import encrypt_credentials
        from datetime import datetime, timezone
        from app.core.config import settings

        # 1. Local Archive Account (Always active)
        loc_plat = (await session.execute(select(Platform).where(Platform.code == "local_archive"))).scalar_one_or_none()
        if loc_plat:
            acc = (await session.execute(select(ConnectedAccount).where(ConnectedAccount.platform_id == loc_plat.id))).scalar_one_or_none()
            if not acc:
                session.add(ConnectedAccount(
                    platform_id=loc_plat.id,
                    external_account_id="local_primary",
                    credentials_encrypted=encrypt_credentials({"archive_dir": settings.DATA_ARCHIVE_PATH}),
                    status="active",
                    connected_at=datetime.now(timezone.utc),
                ))

        # 2. Telegram Account
        if settings.TELEGRAM_BOT_TOKEN:
            tg_plat = (await session.execute(select(Platform).where(Platform.code == "telegram"))).scalar_one_or_none()
            if tg_plat:
                chat_id = settings.TELEGRAM_CHAT_ID or "@namba_wan1"
                acc = (await session.execute(select(ConnectedAccount).where(ConnectedAccount.platform_id == tg_plat.id))).scalar_one_or_none()
                creds = encrypt_credentials({"bot_token": settings.TELEGRAM_BOT_TOKEN, "chat_id": chat_id})
                if acc:
                    acc.credentials_encrypted = creds
                    acc.external_account_id = chat_id
                    acc.status = "active"
                else:
                    session.add(ConnectedAccount(
                        platform_id=tg_plat.id,
                        external_account_id=chat_id,
                        credentials_encrypted=creds,
                        status="active",
                        connected_at=datetime.now(timezone.utc),
                    ))

        # 3. Bluesky Account
        if settings.BLUESKY_HANDLE and settings.BLUESKY_APP_PASSWORD:
            bsky_plat = (await session.execute(select(Platform).where(Platform.code == "bluesky"))).scalar_one_or_none()
            if bsky_plat:
                acc = (await session.execute(select(ConnectedAccount).where(ConnectedAccount.platform_id == bsky_plat.id))).scalar_one_or_none()
                creds = encrypt_credentials({
                    "handle": settings.BLUESKY_HANDLE,
                    "app_password": settings.BLUESKY_APP_PASSWORD,
                    "service": "https://bsky.social"
                })
                if acc:
                    acc.credentials_encrypted = creds
                    acc.external_account_id = settings.BLUESKY_HANDLE
                    acc.status = "active"
                else:
                    session.add(ConnectedAccount(
                        platform_id=bsky_plat.id,
                        external_account_id=settings.BLUESKY_HANDLE,
                        credentials_encrypted=creds,
                        status="active",
                        connected_at=datetime.now(timezone.utc),
                    ))

        # 4. Mastodon Account
        if settings.MASTODON_INSTANCE_URL and settings.MASTODON_ACCESS_TOKEN:
            masto_plat = (await session.execute(select(Platform).where(Platform.code == "mastodon"))).scalar_one_or_none()
            if masto_plat:
                acc = (await session.execute(select(ConnectedAccount).where(ConnectedAccount.platform_id == masto_plat.id))).scalar_one_or_none()
                creds = encrypt_credentials({
                    "instance_url": settings.MASTODON_INSTANCE_URL,
                    "access_token": settings.MASTODON_ACCESS_TOKEN,
                })
                if acc:
                    acc.credentials_encrypted = creds
                    acc.external_account_id = settings.MASTODON_INSTANCE_URL
                    acc.status = "active"
                else:
                    session.add(ConnectedAccount(
                        platform_id=masto_plat.id,
                        external_account_id=settings.MASTODON_INSTANCE_URL,
                        credentials_encrypted=creds,
                        status="active",
                        connected_at=datetime.now(timezone.utc),
                    ))

        await session.commit()
        logger.info("Database initialized with platforms, policy arms, and connected accounts.")
