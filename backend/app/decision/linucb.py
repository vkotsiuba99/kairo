import numpy as np
from typing import Dict, List, Tuple, Optional


class LinUCBHead:
    """
    LinUCB with disjoint linear models for each arm in a specific decision dimension.
    Supports online updates: A_a = I_d + sum(x x^T), b_a = sum(r x).
    Selection: argmax_a ( x^T θ_a + alpha * sqrt(x^T A_a^-1 x) )
    where θ_a = A_a^-1 b_a.
    """

    def __init__(self, dimension_name: str, arm_names: List[str], feature_dim: int, alpha: float = 1.0):
        self.dimension_name = dimension_name
        self.arm_names = arm_names
        self.feature_dim = feature_dim
        self.alpha = alpha

        # Per-arm parameters
        # A_a: (d x d) identity matrix initially
        self.A: Dict[str, np.ndarray] = {
            arm: np.identity(feature_dim, dtype=np.float64) for arm in arm_names
        }
        # b_a: (d,) zero vector initially
        self.b: Dict[str, np.ndarray] = {
            arm: np.zeros(feature_dim, dtype=np.float64) for arm in arm_names
        }
        # Count of times each arm was chosen
        self.counts: Dict[str, int] = {arm: 0 for arm in arm_names}
        # Sum of rewards for greedy fallback
        self.reward_sums: Dict[str, float] = {arm: 0.0 for arm in arm_names}

    def select_arm(self, context: np.ndarray, explore: bool = True) -> Tuple[str, float]:
        """
        Selects the best arm for the given context vector x.
        If explore=False, acts purely greedily (theta^T x).
        Returns (chosen_arm, predicted_score).
        """
        x = np.asarray(context, dtype=np.float64)
        if len(x) != self.feature_dim:
            # Pad or truncate to match feature_dim
            if len(x) < self.feature_dim:
                x = np.pad(x, (0, self.feature_dim - len(x)))
            else:
                x = x[:self.feature_dim]

        best_arm = self.arm_names[0]
        max_p = -float("inf")

        for arm in self.arm_names:
            A_inv = np.linalg.pinv(self.A[arm])
            theta = A_inv @ self.b[arm]
            expected_payoff = float(theta @ x)

            if explore:
                variance = float(x.T @ A_inv @ x)
                confidence_interval = self.alpha * np.sqrt(max(0.0, variance))
                p = expected_payoff + confidence_interval
            else:
                p = expected_payoff

            if p > max_p:
                max_p = p
                best_arm = arm

        return best_arm, float(max_p)

    def update(self, arm: str, context: np.ndarray, reward: float):
        """
        Online update of LinUCB matrices with observed reward:
        A_a <- A_a + x x^T
        b_a <- b_a + r x
        """
        if arm not in self.A:
            return

        x = np.asarray(context, dtype=np.float64)
        if len(x) != self.feature_dim:
            if len(x) < self.feature_dim:
                x = np.pad(x, (0, self.feature_dim - len(x)))
            else:
                x = x[:self.feature_dim]

        self.A[arm] += np.outer(x, x)
        self.b[arm] += reward * x
        self.counts[arm] += 1
        self.reward_sums[arm] += reward

    def to_dict(self) -> Dict:
        return {
            "dimension_name": self.dimension_name,
            "arm_names": self.arm_names,
            "feature_dim": self.feature_dim,
            "alpha": self.alpha,
            "counts": self.counts,
            "reward_sums": self.reward_sums,
            "A": {k: v.tolist() for k, v in self.A.items()},
            "b": {k: v.tolist() for k, v in self.b.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LinUCBHead":
        head = cls(
            dimension_name=data["dimension_name"],
            arm_names=data["arm_names"],
            feature_dim=data["feature_dim"],
            alpha=data.get("alpha", 1.0),
        )
        head.counts = data.get("counts", head.counts)
        head.reward_sums = data.get("reward_sums", head.reward_sums)
        if "A" in data:
            head.A = {k: np.array(v, dtype=np.float64) for k, v in data["A"].items()}
        if "b" in data:
            head.b = {k: np.array(v, dtype=np.float64) for k, v in data["b"].items()}
        return head
