import math
import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class SyntheticUser:
    user_id: int
    interest_embedding: np.ndarray  # unit vector
    activity_rate: float            # multiplier on engagement base rate


@dataclass
class SegmentSimulationConfig:
    segment_name: str = "tech_enthusiasts"
    num_users: int = 500
    dim: int = 24
    lognormal_mu: float = 2.1
    lognormal_sigma: float = 0.9
    base_rate: float = 0.12
    content_match_weight: float = 0.65
    novelty_decay: float = 0.18
    fatigue_window_rounds: int = 8
    seed: int = 42


class SyntheticAudienceSegment:
    """
    Simulated audience segment with parametric response delay and novelty decay fatigue.
    Accurately reproduces human social media engagement dynamics for offline policy evaluation.
    """

    def __init__(self, config: Optional[SegmentSimulationConfig] = None):
        self.config = config or SegmentSimulationConfig()
        self.rng = np.random.RandomState(self.config.seed)
        self.users: List[SyntheticUser] = []
        self.recent_content_embeddings: List[np.ndarray] = []
        self._init_users()

    def _init_users(self):
        # Generate segment centroid
        centroid = self.rng.randn(self.config.dim)
        centroid /= np.linalg.norm(centroid)
        self.centroid = centroid

        for i in range(self.config.num_users):
            # Perturb centroid with noise
            noise = self.rng.randn(self.config.dim) * 0.35
            user_vec = centroid + noise
            user_vec /= np.linalg.norm(user_vec)
            activity = float(self.rng.gamma(shape=2.0, scale=0.5))  # skewed activity
            self.users.append(SyntheticUser(user_id=i, interest_embedding=user_vec, activity_rate=activity))

        # Матричні представлення тих самих користувачів: крок симуляції рахується
        # векторизовано, інакше прогін на тисячі раундів впирається в цикл Python.
        self.interest_matrix = np.array([u.interest_embedding for u in self.users])
        self.activity_vector = np.array([u.activity_rate for u in self.users])

    def evaluate_content_step(self, content_vec: np.ndarray) -> Dict[str, Any]:
        """
        Simulates audience interactions for a single content publication step.
        """
        c_norm = np.linalg.norm(content_vec)
        if c_norm > 0:
            c_vec = content_vec / c_norm
        else:
            c_vec = content_vec

        # Calculate novelty penalty (fatigue from recently seen similar content)
        fatigue_penalty = 0.0
        for past_vec in self.recent_content_embeddings[-self.config.fatigue_window_rounds:]:
            sim = float(np.dot(c_vec, past_vec))
            if sim > 0.5:
                fatigue_penalty += self.config.novelty_decay * (sim - 0.5)

        n = len(self.users)

        # Імовірність залучення для кожного користувача одразу, без циклу
        cosine_match = self.interest_matrix @ c_vec
        p_engage = (
            self.config.base_rate * self.activity_vector
            + self.config.content_match_weight * np.maximum(0.0, cosine_match)
            - fatigue_penalty
        )
        p_engage = np.clip(p_engage, 0.01, 0.95)

        viewed = self.rng.rand(n) < np.minimum(p_engage * 3.0, 0.98)
        reacted = viewed & (self.rng.rand(n) < p_engage)
        commented = reacted & (self.rng.rand(n) < 0.25 * p_engage)
        saved = reacted & (self.rng.rand(n) < 0.15 * p_engage)
        shared = reacted & (self.rng.rand(n) < 0.10 * p_engage)

        total_views = int(viewed.sum())
        total_reactions = int(reacted.sum())
        total_comments = int(commented.sum())
        total_saves = int(saved.sum())
        total_shares = int(shared.sum())

        delays = (
            self.rng.lognormal(
                mean=self.config.lognormal_mu,
                sigma=self.config.lognormal_sigma,
                size=total_views,
            )
            if total_views
            else np.array([])
        )

        self.recent_content_embeddings.append(c_vec)

        median_delay = float(np.median(delays)) if len(delays) else 8.0
        ctr = (total_reactions / total_views) if total_views > 0 else 0.0

        return {
            "views": total_views,
            "reactions": total_reactions,
            "comments": total_comments,
            "saves": total_saves,
            "shares": total_shares,
            "median_delay_minutes": round(median_delay, 2),
            "ctr": round(ctr, 4),
        }
