import numpy as np
from typing import Dict, List, Any, Tuple, Optional
from app.decision.linucb import LinUCBHead

DEFAULT_DIMENSIONS = {
    "topic": [
        "ai_breakthroughs",
        "developer_humor",
        "open_source_tools",
        "tech_career_tips",
        "system_architecture"
    ],
    "format": [
        "text",
        "image_post",
        "video_short",
        "educational_thread"
    ],
    "tone": [
        "analytical",
        "provocative",
        "humorous",
        "inspirational"
    ],
    "length": [
        "short",
        "medium",
        "long"
    ],
    "time_slot": [
        "morning_08_10",
        "lunch_12_14",
        "evening_18_20",
        "night_21_23"
    ],
    "backend": [
        "local",
        "cloud"
    ]
}


class FactorizedContextualBandit:
    """
    Factorized Contextual Multi-Armed Bandit (LinUCB Cascade).
    Sequentially chooses decisions conditioned on context and preceding arm choices:
      x_1 = context
      x_2 = [x_1, arm_1_onehot]
      x_3 = [x_2, arm_2_onehot]
      ...
    This reduces exponential combinatorial action space (5 x 4 x 4 x 3 x 4 x 2 = 1920 actions)
    to a sequence of 6 low-dimensional tractable linear heads while preserving cross-dimension correlation.
    """

    def __init__(
        self,
        base_context_dim: int = 24,
        dimensions: Optional[Dict[str, List[str]]] = None,
        alpha: float = 1.0,
        use_bias: bool = True,
    ):
        self.base_context_dim = base_context_dim
        self.dimensions = dimensions or DEFAULT_DIMENSIONS
        self.dim_order = list(self.dimensions.keys())
        self.alpha = alpha
        # Постійна одиниця (intercept) у векторі ознак. Без неї θ_a·x має нульове
        # середнє на центрованому контексті, і голова не може представити
        # «ця рука в середньому краща» — політика вироджується у випадкову.
        self.use_bias = use_bias

        # Build cascaded heads with progressively increasing feature dimensions
        self.heads: Dict[str, LinUCBHead] = {}
        curr_dim = base_context_dim + (1 if use_bias else 0)

        for dim_name in self.dim_order:
            arm_names = self.dimensions[dim_name]
            self.heads[dim_name] = LinUCBHead(
                dimension_name=dim_name,
                arm_names=arm_names,
                feature_dim=curr_dim,
                alpha=alpha,
            )
            # Next head receives one-hot of the current dimension
            curr_dim += len(arm_names)

    def _get_arm_onehot(self, dim_name: str, arm_name: str) -> List[float]:
        arms = self.dimensions[dim_name]
        return [1.0 if a == arm_name else 0.0 for a in arms]

    def _prepare_context(self, base_context: List[float]) -> List[float]:
        """Вирівнює контекст до base_context_dim і додає постійну одиницю."""
        ctx = list(base_context)[: self.base_context_dim]
        ctx += [0.0] * (self.base_context_dim - len(ctx))
        return [1.0] + ctx if self.use_bias else ctx

    def cascade(self, base_context: List[float], chosen_arms: Dict[str, str]) -> List[List[float]]:
        """
        Контексти кожної голови для вже відомого рішення — ті самі, що бачив
        select(). Це дає змогу навчати політику з історії в БД, де зберігаються
        лише базовий контекст і обрані руки.
        """
        out: List[List[float]] = []
        curr = self._prepare_context(base_context)
        for dim_name in self.dim_order:
            out.append(list(curr))
            curr = curr + self._get_arm_onehot(dim_name, chosen_arms.get(dim_name, ""))
        return out

    def learn_batch(self, samples: List[Tuple[List[float], Dict[str, str], float]]) -> None:
        """
        Пакетне навчання: A_a += XᵀX, b_a += Xᵀr для кожної руки — той самий
        результат, що й послідовні update(), але векторизовано (сума не залежить
        від порядку спостережень).
        """
        per_arm: Dict[Tuple[str, str], Tuple[List[List[float]], List[float]]] = {}
        for base_context, chosen_arms, reward in samples:
            for dim_name, ctx in zip(self.dim_order, self.cascade(base_context, chosen_arms)):
                arm = chosen_arms.get(dim_name)
                if arm not in self.heads[dim_name].A:
                    continue  # старі рішення без цього виміру (напр., time_slot)
                xs, rs = per_arm.setdefault((dim_name, arm), ([], []))
                xs.append(ctx)
                rs.append(float(reward))
        for (dim_name, arm), (xs, rs) in per_arm.items():
            head = self.heads[dim_name]
            X = np.asarray(xs, dtype=np.float64)
            r = np.asarray(rs, dtype=np.float64)
            head.A[arm] += X.T @ X
            head.b[arm] += X.T @ r
            head.counts[arm] += len(rs)
            head.reward_sums[arm] += float(r.sum())

    def select(
        self,
        base_context: List[float],
        explore: bool = True
    ) -> Tuple[Dict[str, str], Dict[str, float], List[List[float]]]:
        """
        Executes sequential factorized LinUCB selection.
        Returns:
          chosen_arms: Dict[dim, arm_value]
          scores: Dict[dim, score]
          cascaded_contexts: List of context vectors at each step (needed for update)
        """
        chosen_arms: Dict[str, str] = {}
        scores: Dict[str, float] = {}
        cascaded_contexts: List[List[float]] = []

        curr_context = self._prepare_context(base_context)

        for dim_name in self.dim_order:
            cascaded_contexts.append(list(curr_context))
            head = self.heads[dim_name]
            arm, score = head.select_arm(np.array(curr_context), explore=explore)
            chosen_arms[dim_name] = arm
            scores[dim_name] = score

            # Expand context for subsequent heads
            arm_onehot = self._get_arm_onehot(dim_name, arm)
            curr_context = curr_context + arm_onehot

        return chosen_arms, scores, cascaded_contexts

    def update(
        self,
        cascaded_contexts: List[List[float]],
        chosen_arms: Dict[str, str],
        reward: float,
    ):
        """
        Propagates the observed feedback reward back through all LinUCB heads.
        """
        for i, dim_name in enumerate(self.dim_order):
            if dim_name in chosen_arms and i < len(cascaded_contexts):
                arm = chosen_arms[dim_name]
                ctx = np.array(cascaded_contexts[i])
                self.heads[dim_name].update(arm, ctx, reward)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_context_dim": self.base_context_dim,
            "dimensions": self.dimensions,
            "dim_order": self.dim_order,
            "alpha": self.alpha,
            "use_bias": self.use_bias,
            "heads": {name: head.to_dict() for name, head in self.heads.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FactorizedContextualBandit":
        bandit = cls(
            base_context_dim=data["base_context_dim"],
            dimensions=data["dimensions"],
            alpha=data.get("alpha", 1.0),
            use_bias=data.get("use_bias", True),
        )
        if "heads" in data:
            for name, head_dict in data["heads"].items():
                bandit.heads[name] = LinUCBHead.from_dict(head_dict)
        return bandit
