"""
Експеримент на датасеті «2000 сторінок»: кожен раунд — випадкова сторінка
генерує собі допис, політика обирає параметри за спостережуваними ознаками
сторінки, мережа «відповідає» лічильниками з прихованої моделі смаків.

Винагорода — та сама compute_reward, що й у робочій системі, з нормуванням на
типову залученість сторінки. Жаль рахується як псевдожаль: різниця очікуваних
винагород оракула (найкращі параметри для сторінки) і обраних параметрів, тож
шум окремих дописів не спотворює порівняння політик.
"""
import csv
from typing import Any, Dict, List

import numpy as np

from app.decision.baselines import (
    ContextualBanditPolicy,
    FixedHeuristicPolicy,
    GreedyHistoricalAveragePolicy,
    RandomPolicy,
)
from app.decision.feedback import compute_reward
from app.simulation import creator_pages as cp


def load_pages(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [cp.from_row(r) for r in csv.DictReader(f)]


def _expected_reward(page: Dict[str, Any], arms: Dict[str, str]) -> float:
    e = cp.expected_counts(page, arms)
    return compute_reward(**e, segment_baseline=cp.baseline(page))


def run(pages: List[Dict[str, Any]], rounds: int, seed: int, alpha: float = 0.6,
        checkpoints: int = 50) -> Dict[str, Any]:
    rng = np.random.RandomState(seed)
    policies = {
        "bandit": ContextualBanditPolicy(base_context_dim=cp.CONTEXT_DIM, alpha=alpha),
        "random": RandomPolicy(seed=seed),
        "fixed_heuristic": FixedHeuristicPolicy(),
        "greedy_avg": GreedyHistoricalAveragePolicy(),
    }
    cum_r = {k: 0.0 for k in policies}
    cum_regret = {k: 0.0 for k in policies}
    hits = {k: {d: 0 for d in cp.DEFAULT_DIMENSIONS} for k in policies}
    tail = max(rounds // 10, 1)
    curve: Dict[str, List[float]] = {k: [] for k in policies}
    curve_t: List[int] = []
    step = max(rounds // checkpoints, 1)
    oracle_total = 0.0

    order = rng.randint(len(pages), size=rounds)
    for t, idx in enumerate(order, 1):
        page = pages[idx]
        ctx = cp.page_context(page)
        best = cp.best_arms(page)
        r_star = _expected_reward(page, best)
        oracle_total += r_star
        for name, pol in policies.items():
            arms = pol.select(ctx)
            counts = cp.sample_counts(page, arms, rng)
            reward = compute_reward(**counts, segment_baseline=cp.baseline(page))
            pol.update(ctx, arms, reward)
            cum_r[name] += reward
            cum_regret[name] += max(0.0, r_star - _expected_reward(page, arms))
            if t > rounds - tail:
                for d in hits[name]:
                    hits[name][d] += int(arms.get(d) == best[d])
        if t % step == 0:
            curve_t.append(t)
            for name in policies:
                curve[name].append(round(cum_regret[name], 3))

    summary = {
        name: {
            "cumulative_reward": round(cum_r[name], 2),
            "cumulative_regret": round(cum_regret[name], 2),
            "avg_reward": round(cum_r[name] / rounds, 4),
            "optimal_share_last10pct": {d: round(v / tail, 3) for d, v in hits[name].items()},
        }
        for name in policies
    }
    return {"rounds": rounds, "seed": seed, "oracle_reward": round(oracle_total, 2),
            "summary": summary, "curve": {"t": curve_t, **curve}}
