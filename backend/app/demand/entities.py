"""
Схема підсистеми прогнозування попиту.

Розділення навмисне: сира згадка (`social_mentions`) ніколи не змінюється
після запису, а всі інтерпретації живуть окремо (`mention_annotations`).
Це дозволяє переанотувати весь корпус іншою моделлю, не перезбираючи дані —
а перезібрати їх неможливо, бо ретроспективи стрічки не існує.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, ForeignKey, Float,
    Boolean, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.types import CompatibleJSON, JsonList


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DemandProduct(Base):
    """Товар або родина товарів, за якою ведеться спостереження."""
    __tablename__ = "demand_products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(64), unique=True, index=True, nullable=False)
    display_name = Column(String(128), nullable=False)
    category = Column(String(64), nullable=False)
    lexicon = Column(CompatibleJSON, default=dict, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # --- Паспорт сигналу: підстава пускати товар у меню ---
    # Заповнюється заміром, а не руками. Товар без сигналу в меню не показуємо,
    # інакше інтерфейс обіцяє прогноз там, де прогнозувати нема з чого.
    posts_per_day = Column(Float, nullable=True)
    unique_author_ratio = Column(Float, nullable=True)   # автори / дописи
    duplicate_ratio = Column(Float, nullable=True)       # частка копіпасти
    promo_ratio = Column(Float, nullable=True)
    history_days = Column(Float, nullable=True)          # глибина доступної ретроспективи
    sample_size = Column(Integer, nullable=True)
    # ok | thin | no_signal | astroturf | promo_heavy
    verdict = Column(String(32), nullable=True, index=True)
    in_menu = Column(Boolean, default=False, nullable=False, index=True)
    measured_at = Column(DateTime(timezone=True), nullable=True)

    mentions = relationship("SocialMention", back_populates="product", cascade="all, delete-orphan")
    observations = relationship("DemandObservation", back_populates="product", cascade="all, delete-orphan")


class SocialMention(Base):
    """Сирий допис. Незмінний після вставки."""
    __tablename__ = "social_mentions"
    __table_args__ = (
        # Ключ ОБОВʼЯЗКОВО включає товар. Той самий допис законно належить і
        # товару, і бренду («Fujifilm X-series» та «Fujifilm»), і якщо ключем
        # є лише (платформа, id), другий власник мовчки втрачає допис. Саме так
        # у Fujifilm утворилась діра в три доби: ранній товарний збір забрав
        # свіжі дні собі, і бренду вони вже не дістались.
        UniqueConstraint("product_id", "platform", "external_id",
                         name="uq_mention_product_platform_external"),
        Index("ix_mention_product_posted", "product_id", "posted_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("demand_products.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False, index=True)           # bluesky | mastodon
    # Мости (bsky.brid.gy) загортають цілий URL усередину URI — 256 не вистачає.
    external_id = Column(String(512), nullable=False)                   # at:// URI або status id
    source = Column(String(32), nullable=False)                         # jetstream | search | tag
    author_id = Column(String(256), nullable=False, index=True)
    author_handle = Column(String(512), nullable=True)
    posted_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    text = Column(Text, nullable=False)
    lang = Column(String(16), nullable=True)
    is_reply = Column(Boolean, default=False, nullable=False)
    reply_to_external_id = Column(String(512), nullable=True)
    # Лічильники — знімок на момент збору. Для дописів із jetstream вони нульові:
    # допис щойно створено, реакції ще попереду. Довантажуються окремим проходом.
    like_count = Column(Integer, default=0, nullable=False)
    repost_count = Column(Integer, default=0, nullable=False)
    reply_count = Column(Integer, default=0, nullable=False)
    counters_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    matched_terms = Column(JsonList, default=list, nullable=False)
    sku_codes = Column(JsonList, default=list, nullable=False)
    looks_promo = Column(Boolean, default=False, nullable=False)
    # Допис, дзеркалений мостом між Bluesky і федіверсом. Той самий автор і той
    # самий текст приходять з обох майданчиків, тому в міжплатформенних
    # агрегатах такі рядки треба рахувати один раз, інакше обсяг подвоїться.
    is_bridged = Column(Boolean, default=False, nullable=False, index=True)
    text_hash = Column(String(64), nullable=False, index=True)           # виявлення копіпасти
    raw = Column(CompatibleJSON, default=dict, nullable=False)

    product = relationship("DemandProduct", back_populates="mentions")
    annotation = relationship("MentionAnnotation", back_populates="mention",
                              uselist=False, cascade="all, delete-orphan")


class MentionAnnotation(Base):
    """Результат розбору допису мовною моделлю."""
    __tablename__ = "mention_annotations"
    __table_args__ = (
        UniqueConstraint("mention_id", "model_name", name="uq_annotation_mention_model"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    mention_id = Column(Integer, ForeignKey("social_mentions.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name = Column(String(128), nullable=False)
    relevant = Column(Boolean, default=True, nullable=False)
    # awareness | consideration | intent | purchase | repurchase | churn
    intent_stage = Column(String(32), nullable=True, index=True)
    # owns | considering | wants | none
    ownership = Column(String(32), nullable=True)
    # in_stock | sold_out | waiting | restock | scalped | none
    scarcity = Column(String(32), nullable=True, index=True)
    sentiment = Column(Float, nullable=True)
    aspects = Column(JsonList, default=list, nullable=False)
    # review | unboxing | question | complaint | news | showcase | other
    post_format = Column(String(32), nullable=True)
    is_ad = Column(Boolean, default=False, nullable=False)
    confidence = Column(Float, nullable=True)
    annotated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    mention = relationship("SocialMention", back_populates="annotation")


class DemandObservation(Base):
    """Цільовий ряд — проксі реального попиту."""
    __tablename__ = "demand_observations"
    __table_args__ = (
        UniqueConstraint("product_id", "source", "observed_on", "sku_code",
                         name="uq_observation_product_source_day"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("demand_products.id", ondelete="CASCADE"), nullable=False, index=True)
    sku_code = Column(String(64), nullable=True)
    source = Column(String(48), nullable=False)      # google_trends | review_velocity | stock | sales
    observed_on = Column(Date, nullable=False, index=True)
    value = Column(Float, nullable=False)
    meta = Column(CompatibleJSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    product = relationship("DemandProduct", back_populates="observations")


class DailyFeature(Base):
    """Добові ознаки — вхід моделі прогнозу."""
    __tablename__ = "demand_daily_features"
    __table_args__ = (
        UniqueConstraint("product_id", "platform", "day", name="uq_feature_product_platform_day"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("demand_products.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)    # bluesky | mastodon | all
    day = Column(Date, nullable=False, index=True)
    mention_count = Column(Integer, default=0, nullable=False)
    unique_authors = Column(Integer, default=0, nullable=False)
    # Розклад за стадіями воронки — саме він, а не загальний обсяг, несе сигнал.
    intent_shares = Column(CompatibleJSON, default=dict, nullable=False)
    scarcity_counts = Column(CompatibleJSON, default=dict, nullable=False)
    mean_sentiment = Column(Float, nullable=True)
    engagement_sum = Column(Integer, default=0, nullable=False)
    duplicate_ratio = Column(Float, default=0.0, nullable=False)
    promo_ratio = Column(Float, default=0.0, nullable=False)
    extras = Column(CompatibleJSON, default=dict, nullable=False)
    computed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
