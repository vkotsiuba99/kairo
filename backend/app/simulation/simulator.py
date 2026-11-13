import numpy as np
from typing import Dict, List, Any, Optional
from app.simulation.audience_model import SyntheticAudienceSegment, SegmentSimulationConfig
from app.decision.baselines import (
    RandomPolicy,
    FixedHeuristicPolicy,
    GreedyHistoricalAveragePolicy,
    ContextualBanditPolicy,
    BasePolicy,
)
from app.decision.feedback import compute_reward
from app.decision.factorized_bandit import DEFAULT_DIMENSIONS


class MultiPolicySimulator:
    """
    Simulation orchestrator running parallel evaluations of all 4 content selection policies:
    1. Contextual Bandit (Factorized LinUCB)
    2. Random Choice
    3. Fixed Heuristic (Buffer/Later rule-based approach)
    4. Greedy Historical Average
    """

    def __init__(
        self,
        n_rounds: int = 150,
        n_users: int = 400,
        seed: int = 42,
        alpha: float = 0.8,
    ):
        self.n_rounds = n_rounds
        self.seed = seed
        self.dim = 24
        self.alpha = alpha

        # Pre-assign arm direction vectors in feature space
        rng = np.random.RandomState(seed)
        self.arm_vectors: Dict[str, Dict[str, np.ndarray]] = {}
        for dim, arms in DEFAULT_DIMENSIONS.items():
            self.arm_vectors[dim] = {}
            for arm in arms:
                vec = rng.randn(self.dim)
                vec /= np.linalg.norm(vec)
                self.arm_vectors[dim][arm] = vec

    def _arms_to_vector(self, chosen_arms: Dict[str, str]) -> np.ndarray:
        combined = np.zeros(self.dim, dtype=np.float64)
        for dim, arm in chosen_arms.items():
            if dim in self.arm_vectors and arm in self.arm_vectors[dim]:
                combined += self.arm_vectors[dim][arm]
        norm = np.linalg.norm(combined)
        return combined / norm if norm > 0 else combined

    def run_simulation(self) -> Dict[str, Any]:
        policies: Dict[str, BasePolicy] = {
            "bandit": ContextualBanditPolicy(base_context_dim=self.dim, alpha=self.alpha),
            "random": RandomPolicy(seed=self.seed),
            "fixed_heuristic": FixedHeuristicPolicy(),
            "greedy_avg": GreedyHistoricalAveragePolicy(),
        }

        # Independent simulated environments initialized with identical user distributions
        envs = {
            name: SyntheticAudienceSegment(
                SegmentSimulationConfig(num_users=300, dim=self.dim, seed=self.seed)
            )
            for name in policies
        }
        # Oracle environment for regret benchmark
        oracle_env = SyntheticAudienceSegment(
            SegmentSimulationConfig(num_users=300, dim=self.dim, seed=self.seed)
        )

        history: Dict[str, Dict[str, List[float]]] = {
            name: {
                "round": [],
                "reward": [],
                "cumulative_reward": [],
                "regret": [],
                "cumulative_regret": [],
                "ctr": [],
            }
            for name in policies
        }

        cum_rewards = {name: 0.0 for name in policies}
        cum_regrets = {name: 0.0 for name in policies}

        # Synthetic context stream
        ctx_rng = np.random.RandomState(self.seed + 10)

        for r in range(1, self.n_rounds + 1):
            # Generate common step context
            base_context = ctx_rng.randn(self.dim)
            base_context /= np.linalg.norm(base_context)
            context_list = base_context.tolist()

            # 1. Oracle step (computes upper-bound benchmark reward)
            # Sample multiple candidate arm sets and pick the best for oracle
            best_oracle_reward = 0.0
            for _ in range(25):
                cand_arms = {
                    dim: ctx_rng.choice(arms) for dim, arms in DEFAULT_DIMENSIONS.items()
                }
                cand_vec = self._arms_to_vector(cand_arms)
                stats = oracle_env.evaluate_content_step(cand_vec)
                r_val = compute_reward(
                    views=stats["views"],
                    reactions=stats["reactions"],
                    comments=stats["comments"],
                    saves=stats["saves"],
                    shares=stats["shares"],
                    cost_usd=0.001 if cand_arms.get("backend") == "cloud" else 0.0,
                )
                if r_val > best_oracle_reward:
                    best_oracle_reward = r_val

            # 2. Evaluate each policy
            for name, policy in policies.items():
                chosen = policy.select(context_list)
                content_vec = self._arms_to_vector(chosen)
                stats = envs[name].evaluate_content_step(content_vec)

                cost = 0.001 if chosen.get("backend") == "cloud" else 0.0
                reward = compute_reward(
                    views=stats["views"],
                    reactions=stats["reactions"],
                    comments=stats["comments"],
                    saves=stats["saves"],
                    shares=stats["shares"],
                    cost_usd=cost,
                )

                # Update policy with observed reward
                policy.update(context_list, chosen, reward)

                # Calculate regret
                instant_regret = max(0.0, best_oracle_reward - reward)
                cum_rewards[name] += reward
                cum_regrets[name] += instant_regret

                # Record history
                history[name]["round"].append(r)
                history[name]["reward"].append(round(reward, 4))
                history[name]["cumulative_reward"].append(round(cum_rewards[name], 4))
                history[name]["regret"].append(round(instant_regret, 4))
                history[name]["cumulative_regret"].append(round(cum_regrets[name], 4))
                history[name]["ctr"].append(stats["ctr"])

        # Summary comparison metrics
        summary = {}
        for name in policies:
            summary[name] = {
                "total_cumulative_reward": round(cum_rewards[name], 2),
                "total_cumulative_regret": round(cum_regrets[name], 2),
                "final_avg_ctr": round(float(np.mean(history[name]["ctr"][-30:])), 4),
                "avg_reward_last_50": round(float(np.mean(history[name]["reward"][-50:])), 4),
            }

        return {
            "n_rounds": self.n_rounds,
            "seed": self.seed,
            "summary": summary,
            "history": history,
        }
