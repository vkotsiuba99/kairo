import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

PLATFORM_ORDER = [
    "telegram", "youtube", "mastodon", "bluesky", "local_archive", "facebook", "instagram", "tiktok",
]


def compute_rfm_features(events: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, float]:
    """
    Computes RFM-like engagement metrics for a user or segment:
    - Recency: normalized recency score (decay over days)
    - Frequency: total number of recorded interactions
    - Monetary (Intensity): weighted sum of views, reactions, comments, shares, saves
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not events:
        return {"recency": 0.0, "frequency": 0.0, "intensity": 0.0}

    # Weight multipliers for engagement types
    weights = {
        "view": 1.0,
        "reaction": 3.0,
        "comment": 5.0,
        "save": 6.0,
        "share": 8.0,
    }

    frequency = float(len(events))
    most_recent = max(
        [e.get("occurred_at") if isinstance(e.get("occurred_at"), datetime)
         else datetime.fromisoformat(str(e.get("occurred_at"))) for e in events]
    )
    days_since = max(0.0, (now - most_recent).total_seconds() / 86400.0)
    recency = float(np.exp(-0.1 * days_since))  # exponential recency decay

    total_intensity = 0.0
    for e in events:
        etype = e.get("event_type", "view")
        eval_ = float(e.get("event_value", 1.0))
        total_intensity += weights.get(etype, 1.0) * eval_

    return {
        "recency": round(recency, 4),
        "frequency": round(frequency, 2),
        "intensity": round(total_intensity, 4),
    }


def build_context_vector(
    segment_centroid: Optional[List[float]],
    platform_code: str,
    timestamp: Optional[datetime] = None,
    rolling_stats: Optional[Dict[str, float]] = None,
    reduced_dim: int = 16,
) -> List[float]:
    """
    Constructs the contextual bandit feature vector x_t:
    [segment_features (reduced centroid or PCA), platform_onehot, hour_norm, day_norm, rolling_engagement_stats]
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    # 1. Segment features (take first reduced_dim or average pools from 768-dim centroid)
    if segment_centroid and len(segment_centroid) >= reduced_dim:
        # Pool to reduced_dim
        chunk_size = len(segment_centroid) // reduced_dim
        seg_feats = [
            float(np.mean(segment_centroid[i * chunk_size : (i + 1) * chunk_size]))
            for i in range(reduced_dim)
        ]
    elif segment_centroid:
        seg_feats = (segment_centroid + [0.0] * reduced_dim)[:reduced_dim]
    else:
        seg_feats = [0.0] * reduced_dim

    # 2. Platform one-hot (8 platforms)
    plat_onehot = [1.0 if platform_code == p else 0.0 for p in PLATFORM_ORDER]

    # 3. Temporal features
    hour_norm = timestamp.hour / 24.0
    day_norm = timestamp.weekday() / 7.0

    # 4. Rolling engagement stats (recency, frequency, intensity, avg_ctr)
    stats = rolling_stats or {}
    r = float(stats.get("recency", 0.5))
    f = float(stats.get("frequency", 1.0))
    ctr = float(stats.get("avg_ctr", 0.05))

    context_vector = seg_feats + plat_onehot + [hour_norm, day_norm, r, min(f / 100.0, 1.0), ctr]
    return context_vector
