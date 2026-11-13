from datetime import datetime
from typing import List, Dict, Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class ConnectAccountRequest(BaseModel):
    external_account_id: str
    credentials: Dict[str, Any]


class ConnectedAccountResponse(BaseModel):
    id: int
    platform_id: int
    platform_code: str
    external_account_id: str
    status: str
    connected_at: datetime


class TelemetryIngestRequest(BaseModel):
    platform_code: str
    external_post_id: str
    event_type: str  # view, reaction, comment, save, share
    event_value: float = 1.0
    occurred_at: Optional[datetime] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)


class AudienceSegmentResponse(BaseModel):
    id: int
    label: str
    size: int
    updated_at: datetime
    centroid_sample: List[float] = Field(default_factory=list)


class GenerateContentRequest(BaseModel):
    platform_code: str = "telegram"
    topic: Optional[str] = None       # None means bandit policy decides
    format_type: Optional[str] = None # None means bandit policy decides
    tone: Optional[str] = None
    length_bucket: Optional[str] = None
    backend: Optional[str] = "local"
    segment_id: Optional[int] = None
    auto_schedule_minutes: Optional[int] = None  # if set, immediately schedule for publication


class ContentItemResponse(BaseModel):
    id: int
    platform_id: int
    platform_code: str
    format: str
    tone: str
    length_bucket: str
    title: Optional[str]
    body: str
    hashtags: List[str]
    media_refs: Dict[str, Any]
    status: str
    created_at: datetime


class ContentItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    hashtags: Optional[List[str]] = None



class SchedulePostRequest(BaseModel):
    content_item_id: int
    connected_account_id: int
    scheduled_at: datetime


class ScheduleSlot(BaseModel):
    weekday: int = Field(ge=0, le=6)                       # 0 — понеділок
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM за місцевим часом


class PostingSchedule(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    slots: List[ScheduleSlot] = Field(default_factory=list)
    utc_offset: int = Field(default=2, ge=-12, le=14)
    segment_id: Optional[int] = None
    per_day: int = Field(default=2, ge=1, le=6)
    min_gap_minutes: int = Field(default=60, ge=0, le=1440)

    @model_validator(mode="after")
    def _manual_needs_slots(self):
        if self.mode == "manual" and not self.slots:
            raise ValueError("Add at least one slot to a custom schedule")
        return self


class QueuePostRequest(BaseModel):
    content_item_id: int
    connected_account_id: int


class PostResponse(BaseModel):
    id: int
    content_item_id: int
    connected_account_id: int
    scheduled_at: datetime
    published_at: Optional[datetime]
    external_post_id: Optional[str]
    status: str
    error_message: Optional[str]


class ExperimentCreateRequest(BaseModel):
    name: str = "Comparative Evaluation of Bandit vs Baselines"
    n_rounds: int = 100
    seed: int = 42
    alpha: float = 0.8


class ExperimentResponse(BaseModel):
    id: int
    name: str
    policy_type: str
    started_at: datetime
    finished_at: Optional[datetime]
    summary: Dict[str, Any] = Field(default_factory=dict)


class DashboardMetricsResponse(BaseModel):
    system_status: Dict[str, Any]
    total_posts: int
    published_posts: int
    total_telemetry_events: int
    active_experiments: int
    latest_experiment_summary: Optional[Dict[str, Any]]
    recent_content: List[ContentItemResponse]
