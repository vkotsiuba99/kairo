from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Numeric, Boolean
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.types import CompatibleJSON, VectorType, JsonList


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Platform(Base):
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(64), unique=True, index=True, nullable=False)
    display_name = Column(String(128), nullable=False)
    adapter_class = Column(String(256), nullable=False)
    config_schema = Column(CompatibleJSON, nullable=True, default=dict)

    accounts = relationship("ConnectedAccount", back_populates="platform", cascade="all, delete-orphan")
    content_items = relationship("ContentItem", back_populates="platform")


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    platform_id = Column(Integer, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False, index=True)
    external_account_id = Column(String(256), nullable=False)
    credentials_encrypted = Column(Text, nullable=True)  # AES-256-GCM encrypted
    connected_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    status = Column(String(32), default="active", nullable=False)  # active, disconnected, error

    platform = relationship("Platform", back_populates="accounts")
    publications = relationship("Publication", back_populates="account", cascade="all, delete-orphan")


class ContentItem(Base):
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    generation_job_id = Column(Integer, ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=False, index=True)
    topic_cluster_id = Column(Integer, nullable=True)
    format = Column(String(64), nullable=False)  # text, image_post, video_short, article
    tone = Column(String(64), nullable=False)    # provocative, educational, humorous, analytical
    length_bucket = Column(String(32), nullable=False)  # short, medium, long
    title = Column(String(256), nullable=True)
    body = Column(Text, nullable=False)
    media_refs = Column(CompatibleJSON, default=dict, nullable=False)  # {"images": [...], "video": "..."}
    hashtags = Column(JsonList, default=list, nullable=False)
    status = Column(String(32), default="draft", nullable=False)  # draft, ready, published, archived
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    platform = relationship("Platform", back_populates="content_items")
    generation_job = relationship("GenerationJob", foreign_keys=[generation_job_id], back_populates="content_item")
    policy_decision = relationship("PolicyDecision", back_populates="content_item", uselist=False)


class Post(Base):
    """Черга публікацій сервісу Scheduler.

    Матеріал і канал належать іншим сервісам, тому замість зовнішніх ключів тут
    їхні ідентифікатори й копія того, що потрібно для публікації.
    """
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    content_item_id = Column(Integer, nullable=False, index=True)
    connected_account_id = Column(Integer, nullable=False, index=True)
    platform_code = Column(String(64), nullable=False, default="")
    account_label = Column(String(256), nullable=True)
    format = Column(String(64), nullable=False, default="text")
    title = Column(String(256), nullable=True)
    body = Column(Text, nullable=False, default="")
    hashtags = Column(JsonList, default=list, nullable=False)
    media_refs = Column(CompatibleJSON, default=dict, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    external_post_id = Column(String(256), nullable=True)
    status = Column(String(32), default="pending", nullable=False)  # pending, publishing, published, failed, cancelled
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)       # спроби публікації (повтори з відкладенням)
    claimed_at = Column(DateTime(timezone=True), nullable=True)  # коли воркер захопив допис


class ChannelPlan(Base):
    """Розклад публікацій каналу (сервіс Scheduler).

    {"mode": "auto"|"manual", "slots": [{"weekday": 0, "time": "09:00"}],
     "utc_offset": 2, "segment_id": null, "per_day": 2, "min_gap_minutes": 60}
    """
    __tablename__ = "channel_plans"

    account_id = Column(Integer, primary_key=True)
    schedule = Column(CompatibleJSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Publication(Base):
    """Журнал успішних публікацій сервісу Publishing — з нього опитуються лічильники."""
    __tablename__ = "publications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    post_id = Column(Integer, nullable=False, unique=True, index=True)
    content_item_id = Column(Integer, nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("connected_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform_code = Column(String(64), nullable=False)
    external_post_id = Column(String(256), nullable=True)
    url = Column(String(512), nullable=True)
    published_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    account = relationship("ConnectedAccount", back_populates="publications")


class TrackedPost(Base):
    """Опубліковані дописи, відомі сервісу Telemetry з події post.published."""
    __tablename__ = "tracked_posts"

    post_id = Column(Integer, primary_key=True)
    content_item_id = Column(Integer, nullable=False, index=True)
    platform_code = Column(String(64), nullable=False, index=True)
    external_post_id = Column(String(256), nullable=True, index=True)
    counter_metrics = Column(JsonList, default=list, nullable=False)
    published_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    post_id = Column(Integer, nullable=True, index=True)
    platform_code = Column(String(64), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)  # view, reaction, comment, save, share
    event_value = Column(Numeric(12, 4), default=1.0, nullable=False)
    occurred_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    raw_payload = Column(CompatibleJSON, default=dict, nullable=False)


class AudienceProfile(Base):
    __tablename__ = "audience_profiles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connected_account_id = Column(Integer, nullable=False, index=True)
    external_user_id = Column(String(256), nullable=False, index=True)
    first_seen = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    interest_embedding = Column(VectorType(768), nullable=True)
    engagement_features = Column(CompatibleJSON, default=dict, nullable=False)  # RFM stats, preferred formats


class AudienceSegment(Base):
    __tablename__ = "audience_segments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connected_account_id = Column(Integer, nullable=True, index=True)
    label = Column(String(128), nullable=False)
    centroid = Column(VectorType(768), nullable=True)
    size = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    # Портрет сегмента, зібраний користувачем із фіксованих варіантів
    # (вік, зайнятість, регіон, інтереси, пристрій, вікна активності).
    profile = Column(CompatibleJSON, nullable=True, default=dict)
    # 24 ваги — відносна активність сегмента по годинах доби у його часовому
    # поясі. Саме з цієї кривої планувальник бере рекомендований час публікації.
    activity_hours = Column(CompatibleJSON, nullable=True, default=list)
    source = Column(String(32), default="manual", nullable=False)  # manual | clustering

    policy_decisions = relationship("PolicyDecision", back_populates="segment")


class PolicyArm(Base):
    __tablename__ = "policy_arms"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    dimension = Column(String(64), nullable=False, index=True)  # topic, format, tone, length, time_slot, backend
    value = Column(String(128), nullable=False)
    embedding = Column(VectorType(768), nullable=True)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True)
    segment_id = Column(Integer, ForeignKey("audience_segments.id", ondelete="SET NULL"), nullable=True)
    context_vector = Column(CompatibleJSON, default=list, nullable=False)
    chosen_arms = Column(CompatibleJSON, default=dict, nullable=False)  # {"topic": "ai_tech", "format": "text", ...}
    policy_version = Column(String(64), default="factorized_linucb_v2", nullable=False)
    decided_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    content_item = relationship("ContentItem", back_populates="policy_decision")
    segment = relationship("AudienceSegment", back_populates="policy_decisions")
    rewards = relationship("PolicyReward", back_populates="policy_decision", cascade="all, delete-orphan")


class PolicyReward(Base):
    __tablename__ = "policy_rewards"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    policy_decision_id = Column(Integer, ForeignKey("policy_decisions.id", ondelete="CASCADE"), nullable=False, index=True)
    reward = Column(Numeric(12, 6), nullable=False)
    computed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_decision = relationship("PolicyDecision", back_populates="rewards")


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    policy_type = Column(String(64), nullable=False)  # bandit, random, fixed_heuristic, greedy_avg
    config = Column(CompatibleJSON, default=dict, nullable=False)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    simulation_runs = relationship("SimulationRun", back_populates="experiment", cascade="all, delete-orphan")
    metrics_snapshots = relationship("MetricsSnapshot", back_populates="experiment", cascade="all, delete-orphan")


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    experiment_id = Column(Integer, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True)
    seed = Column(Integer, default=42, nullable=False)
    audience_profile_config = Column(CompatibleJSON, default=dict, nullable=False)
    n_rounds = Column(Integer, default=100, nullable=False)
    results = Column(CompatibleJSON, default=dict, nullable=False)

    experiment = relationship("Experiment", back_populates="simulation_runs")


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    experiment_id = Column(Integer, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, index=True)
    round_number = Column(Integer, nullable=False, index=True)
    cumulative_reward = Column(Numeric(14, 6), nullable=False)
    regret = Column(Numeric(14, 6), nullable=False)
    ctr = Column(Numeric(8, 6), nullable=False)
    computed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    experiment = relationship("Experiment", back_populates="metrics_snapshots")


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    content_item_id = Column(Integer, nullable=True)
    content_type = Column(String(32), nullable=False)  # text, image, video
    backend = Column(String(32), default="local", nullable=False)  # local, cloud
    model_name = Column(String(128), nullable=False)
    prompt_template_id = Column(String(128), nullable=True)
    status = Column(String(32), default="pending", nullable=False)  # pending, processing, completed, failed
    requested_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), default=0.0, nullable=False)
    error = Column(Text, nullable=True)

    content_item = relationship("ContentItem", back_populates="generation_job", uselist=False)


class OutboxEvent(Base):
    """Подія, записана в тій самій транзакції, що й зміна даних (transactional outbox).

    Ретранслятор відправляє її в брокер і позначає sent_at; тому подія не губиться,
    навіть якщо сервіс упав одразу після commit.
    """
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_id = Column(String(64), unique=True, nullable=False)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(CompatibleJSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True, index=True)


class ProcessedEvent(Base):
    """Оброблені вхідні події: повторна доставка тієї самої події нічого не змінює."""
    __tablename__ = "processed_events"

    event_id = Column(String(64), primary_key=True)
    consumer = Column(String(32), primary_key=True)
    processed_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
