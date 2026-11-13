import random
import numpy as np
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Tuple
from app.decision.factorized_bandit import DEFAULT_DIMENSIONS, FactorizedContextualBandit


class BasePolicy(ABC):
    @abstractmethod
    def select(self, context: List[float]) -> Dict[str, str]:
        """Selects arm values for each dimension."""
        pass

    @abstractmethod
    def update(self, context: List[float], chosen_arms: Dict[str, str], reward: float):
        """Updates internal statistics based on reward."""
        pass


class RandomPolicy(BasePolicy):
    """
    Baseline 1: Uniform random policy across all dimensions.
    """
    def __init__(self, dimensions: Dict[str, List[str]] = None, seed: int = 42):
        self.dimensions = dimensions or DEFAULT_DIMENSIONS
        self.rng = random.Random(seed)

    def select(self, context: List[float]) -> Dict[str, str]:
        return {dim: self.rng.choice(arms) for dim, arms in self.dimensions.items()}

    def update(self, context: List[float], chosen_arms: Dict[str, str], reward: float):
        pass  # Non-learning baseline


class FixedHeuristicPolicy(BasePolicy):
    """
    Baseline 2: Fixed rule-based heuristic (as used by Buffer/Later/Hootsuite).
    Always selects predetermined defaults:
    - topic: ai_breakthroughs
    - format: text
    - tone: analytical
    - length: medium
    - time_slot: evening_18_20
    - backend: local
    """
    def __init__(self, defaults: Dict[str, str] = None):
        self.defaults = defaults or {
            "topic": "ai_breakthroughs",
            "format": "text",
            "tone": "analytical",
            "length": "medium",
            "time_slot": "evening_18_20",
            "backend": "local",
        }

    def select(self, context: List[float]) -> Dict[str, str]:
        return dict(self.defaults)

    def update(self, context: List[float], chosen_arms: Dict[str, str], reward: float):
        pass  # Static rule baseline


class GreedyHistoricalAveragePolicy(BasePolicy):
    """
    Baseline 3: Greedy Historical Average (MAB without exploration / UCB).
    Selects the arm with the highest observed empirical mean reward for each dimension.
    """
    def __init__(self, dimensions: Dict[str, List[str]] = None):
        self.dimensions = dimensions or DEFAULT_DIMENSIONS
        self.counts: Dict[str, Dict[str, int]] = {
            dim: {arm: 0 for arm in arms} for dim, arms in self.dimensions.items()
        }
        self.sums: Dict[str, Dict[str, float]] = {
            dim: {arm: 0.0 for arm in arms} for dim, arms in self.dimensions.items()
        }

    def select(self, context: List[float]) -> Dict[str, str]:
        chosen = {}
        for dim, arms in self.dimensions.items():
            best_arm = arms[0]
            best_mean = -float("inf")
            for arm in arms:
                c = self.counts[dim][arm]
                mean_r = (self.sums[dim][arm] / c) if c > 0 else 0.0
                if mean_r > best_mean:
                    best_mean = mean_r
                    best_arm = arm
            chosen[dim] = best_arm
        return chosen

    def update(self, context: List[float], chosen_arms: Dict[str, str], reward: float):
        for dim, arm in chosen_arms.items():
            if dim in self.counts and arm in self.counts[dim]:
                self.counts[dim][arm] += 1
                self.sums[dim][arm] += reward


class ContextualBanditPolicy(BasePolicy):
    """
    Proposed Policy: Factorized LinUCB Contextual Bandit.
    """
    def __init__(self, base_context_dim: int = 24, dimensions: Dict[str, List[str]] = None, alpha: float = 1.0):
        self.bandit = FactorizedContextualBandit(
            base_context_dim=base_context_dim,
            dimensions=dimensions,
            alpha=alpha
        )
        self.last_cascaded_contexts: List[List[float]] = []

    def select(self, context: List[float]) -> Dict[str, str]:
        chosen, scores, cascaded = self.bandit.select(context, explore=True)
        self.last_cascaded_contexts = cascaded
        return chosen

    def update(self, context: List[float], chosen_arms: Dict[str, str], reward: float):
        if self.last_cascaded_contexts:
            self.bandit.update(self.last_cascaded_contexts, chosen_arms, reward)
