from typing import Dict, Any, Optional
from app.core.config import settings


def compute_reward(
    views: float,
    reactions: float,
    comments: float,
    saves: float,
    shares: float = 0.0,
    cost_usd: float = 0.0,
    segment_baseline: Optional[Dict[str, float]] = None,
    weights: Optional[Dict[str, float]] = None,
    cost_lambda: Optional[float] = None,
) -> float:
    """
    Computes scalar feedback reward:
      reward = w1*norm(views) + w2*norm(reactions) + w3*norm(comments) + w4*norm(saves) + w5*norm(shares) - lambda*cost_usd
    Using segment min-max normalization to preserve comparability across different audience sizes.
    """
    if weights is None:
        weights = {
            "views": 0.15,
            "reactions": 0.35,
            "comments": 0.25,
            "saves": 0.15,
            "shares": 0.10,
        }

    if cost_lambda is None:
        cost_lambda = settings.COST_AWARE_LAMBDA

    baseline = segment_baseline or {
        "max_views": 1000.0,
        "max_reactions": 100.0,
        "max_comments": 25.0,
        "max_saves": 20.0,
        "max_shares": 15.0,
    }

    def _norm(val: float, max_val: float) -> float:
        if max_val <= 0:
            return 0.0
        return min(max(val / max_val, 0.0), 1.0)

    norm_v = _norm(views, baseline.get("max_views", 1000.0))
    norm_r = _norm(reactions, baseline.get("max_reactions", 100.0))
    norm_c = _norm(comments, baseline.get("max_comments", 25.0))
    norm_s = _norm(saves, baseline.get("max_saves", 20.0))
    norm_sh = _norm(shares, baseline.get("max_shares", 15.0))

    raw_engagement_reward = (
        weights["views"] * norm_v
        + weights["reactions"] * norm_r
        + weights["comments"] * norm_c
        + weights["saves"] * norm_s
        + weights["shares"] * norm_sh
    )

    # Cost-aware adjustment (Section 11.3)
    cost_penalty = cost_lambda * max(0.0, cost_usd)
    final_reward = max(0.0, raw_engagement_reward - cost_penalty)

    return round(final_reward, 6)
