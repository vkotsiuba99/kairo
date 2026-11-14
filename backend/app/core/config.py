import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    PROJECT_NAME: str = "Kairo"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api/v1"

    # Мікросервіси: процес запускає один сервіс (content, scheduler, publishing,
    # telemetry, media, trends) зі своєю БД. "all" — усі в одному процесі
    # (локальна розробка й тести): код той самий, лише розгортання інше.
    SERVICE: str = "all"
    # Адреси інших сервісів; порожньо — сервіс у цьому ж процесі
    CONTENT_URL: str = ""
    SCHEDULER_URL: str = ""
    PUBLISHING_URL: str = ""
    TELEMETRY_URL: str = ""
    MEDIA_URL: str = ""
    TRENDS_URL: str = ""
    SERVICE_TIMEOUT_SECONDS: float = 120.0
    # Брокер подій (RabbitMQ); порожньо — шина в пам'яті процесу
    RABBITMQ_URL: str = ""
    OUTBOX_POLL_SECONDS: float = 1.0
    OUTBOX_BATCH_SIZE: int = 100

    # Security & Encryption for Connected Account credentials (AES-256-GCM)
    # Must be 32 bytes (e.g. 64 hex characters or 44 base64 chars)
    ENCRYPTION_KEY: str = Field(
        default="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        description="Hex-encoded 32-byte key for AES-256-GCM credentials encryption"
    )

    # Database
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "kairo"
    POSTGRES_PASSWORD: str = "kairo_secret"
    POSTGRES_DB: str = "kairo"
    DATABASE_URL: Optional[str] = None

    # SQLite fallback mode for zero-dependency standalone testing
    SQLITE_DB_PATH: str = ""  # порожньо — data/kairo-<SERVICE>.db
    USE_SQLITE_FALLBACK: bool = True

    # Черга публікацій — таблиця posts: воркер атомарно «захоплює» дописи,
    # тому кілька реплік публікатора не опублікують той самий допис двічі.
    RUN_SCHEDULER_IN_API: bool = True       # цикл диспетчера черги в сервісі Scheduler
    SCHEDULER_INTERVAL_SECONDS: int = 30
    PUBLISH_BATCH_SIZE: int = 20
    PUBLISH_CONCURRENCY: int = 8
    PUBLISH_MAX_ATTEMPTS: int = 3
    PUBLISH_RETRY_BASE_SECONDS: int = 30    # затримка повтору: 30 с, 60 с, 120 с…
    PUBLISH_CLAIM_TIMEOUT_SECONDS: int = 600  # захоплений, але не завершений допис повертається в чергу

    # Стан бандита відтворюється з БД не частіше, ніж раз на цей інтервал
    BANDIT_STATE_TTL_SECONDS: int = 30
    TELEMETRY_POLL_SECONDS: int = 300

    # Базові адреси API платформ. Окремі змінні потрібні, щоб навантажувальні
    # тести й тести адаптерів ішли на мок-провайдер, а не в реальні мережі.
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    GRAPH_API_BASE: str = "https://graph.facebook.com/v21.0"   # Facebook Pages + Instagram
    TIKTOK_API_BASE: str = "https://open.tiktokapis.com"
    GOOGLE_API_BASE: str = "https://www.googleapis.com"
    GOOGLE_OAUTH_TOKEN_URL: str = "https://oauth2.googleapis.com/token"
    # Instagram і TikTok забирають медіа за публічним URL (PULL_FROM_URL):
    # адреса, за якою зовні доступний /media цього сервера.
    PUBLIC_BASE_URL: str = ""

    # LLM Inference (Primary: LM Studio with Ternary Bonsai 27B)
    LLM_BASE_URL: str = "http://localhost:1234/v1"
    LLM_API_KEY: str = "lm-studio"
    # Локальна 27B-модель відповідає десятки секунд — коротший таймаут
    # означав би, що локальний бекенд ніколи не встигає і все йде в хмару.
    LLM_TIMEOUT_SECONDS: float = 180.0
    # Коротка перевірка «модель узагалі піднімається» перед довгою генерацією.
    LLM_PREFLIGHT_SECONDS: float = 20.0
    LLM_MODEL_NAME: str = "ternary-bonsai-27b"
    LLM_EMBEDDING_MODEL: str = "nomic-embed-text-v1.5"

    # Cloud Reserve (Fallback / Cost-aware arm: OpenRouter with Kimi K2.6)
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL_NAME: str = "moonshotai/kimi-k2.6"
    COST_AWARE_LAMBDA: float = 0.05

    # Kimi (Moonshot) напряму, без посередника. Ключ уже лежав у credentials.env,
    # але базового URL і моделі не було — виклик нікуди не йшов.
    KIMI_API_KEY: Optional[str] = None
    KIMI_BASE_URL: str = "https://api.moonshot.ai/v1"
    # k3 замість k2.6: на тому самому тесті 12.9 с проти 44.8 при однаковій
    # точності 10/10 і втричі меншій відповіді. k2.6 «думає вголос» на 1831
    # токен, k3 — на 572, і платимо ми і за це, і за час.
    KIMI_MODEL_NAME: str = "kimi-k3"  # cost penalty in reward formula: reward' = reward - lambda * cost_usd

    # Image Generation
    IMAGE_MODEL_NAME: str = "prism-ml/bonsai-image-ternary-4B-gemlite-2bit"

    # Social accounts credentials from env
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    BLUESKY_HANDLE: Optional[str] = None
    BLUESKY_APP_PASSWORD: Optional[str] = None

    MASTODON_INSTANCE_URL: Optional[str] = None
    MASTODON_ACCESS_TOKEN: Optional[str] = None

    # TTS / Video composition
    TTS_SERVICE_URL: str = "http://localhost:8000/api/v1/media"  # озвучення — у сервісі Media
    FFMPEG_BINARY: str = "ffmpeg"

    # Storage paths
    DATA_ARCHIVE_PATH: str = "data/archive"
    DATA_MEDIA_PATH: str = "data/media"

    def get_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    def get_sync_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL.replace("+asyncpg", "")
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
