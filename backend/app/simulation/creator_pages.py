"""
Синтетична модель сторінок авторів (датасет «2000 сторінок»).

Кожна сторінка має спостережувані ознаки (мережа, ніша, вікова група аудиторії,
пікове вікно активності, частка мобільних переглядів, кількість підписників) і
приховану «смакову» матрицю latent_affinity — наскільки аудиторії подобається
кожне значення кожного виміру рішення. Прихована частина залежить від
спостережуваних ознак лінійно плюс індивідуальний шум сторінки, тож контекстна
політика здатна її вивчити лише частково — як і в реальності.

Цей самий модуль генерує CSV (scripts/generate_dataset.py) і рахує очікувану
реакцію в експерименті (app/simulation/dataset_experiment.py).
"""

import json
import math
from typing import Any, Dict, List

import numpy as np

from app.decision.factorized_bandit import DEFAULT_DIMENSIONS

PLATFORMS = ["telegram", "instagram", "tiktok", "facebook", "youtube", "mastodon", "bluesky"]
PLATFORM_P = [0.20, 0.18, 0.16, 0.14, 0.12, 0.10, 0.10]
AGES = ["under_18", "18_24", "25_34", "35_44", "45_plus"]
AGE_P = {
    "tiktok": [0.25, 0.35, 0.25, 0.10, 0.05],
    "instagram": [0.10, 0.35, 0.30, 0.15, 0.10],
    "youtube": [0.15, 0.25, 0.25, 0.20, 0.15],
    "telegram": [0.05, 0.20, 0.35, 0.25, 0.15],
    "facebook": [0.02, 0.10, 0.25, 0.30, 0.33],
    "mastodon": [0.02, 0.15, 0.40, 0.28, 0.15],
    "bluesky": [0.03, 0.22, 0.40, 0.22, 0.13],
}
REACH = {"telegram": 0.45, "instagram": 0.25, "tiktok": 0.60, "facebook": 0.08,
         "youtube": 0.30, "mastodon": 0.20, "bluesky": 0.25}
BASE_ER = {"telegram": 0.030, "instagram": 0.040, "tiktok": 0.060, "facebook": 0.015,
           "youtube": 0.035, "mastodon": 0.025, "bluesky": 0.030}
FORMAT_AFF = {  # text, image_post, video_short, educational_thread
    "telegram": [0.5, 0.3, -0.3, 0.4], "instagram": [-0.6, 0.7, 0.5, -0.2],
    "tiktok": [-0.8, 0.1, 0.9, -0.6], "facebook": [0.3, 0.4, 0.3, 0.0],
    "youtube": [-0.5, -0.4, 0.9, -0.3], "mastodon": [0.5, 0.2, -0.4, 0.4],
    "bluesky": [0.5, 0.3, -0.4, 0.3],
}
TONE_AFF = {  # analytical, provocative, humorous, inspirational
    "under_18": [-0.3, 0.3, 0.6, 0.1], "18_24": [-0.1, 0.4, 0.5, 0.1],
    "25_34": [0.3, 0.1, 0.2, 0.0], "35_44": [0.4, -0.1, 0.0, 0.2],
    "45_plus": [0.3, -0.3, -0.1, 0.4],
}
SLOT_BY_AGE = {  # імовірності пікового вікна: ранок, обід, вечір, ніч
    "under_18": [0.05, 0.15, 0.35, 0.45], "18_24": [0.05, 0.15, 0.40, 0.40],
    "25_34": [0.15, 0.20, 0.50, 0.15], "35_44": [0.25, 0.25, 0.40, 0.10],
    "45_plus": [0.45, 0.25, 0.25, 0.05],
}
TOPICS = DEFAULT_DIMENSIONS["topic"]
SLOTS = DEFAULT_DIMENSIONS["time_slot"]
SLOT_LOCAL_HOUR = [9, 13, 19, 22]
OFFSETS = [(2, 0.55), (1, 0.20), (-5, 0.10), (0, 0.08), (9, 0.07)]
CONTEXT_DIM = 24
TASTE_SCALE = 0.6  # множник залученості: exp(TASTE_SCALE * сума смаків)


def sample_pages(n: int, seed: int) -> List[Dict[str, Any]]:
    rng = np.random.RandomState(seed)
    pages = []
    for i in range(n):
        platform = PLATFORMS[rng.choice(len(PLATFORMS), p=PLATFORM_P)]
        age = AGES[rng.choice(len(AGES), p=AGE_P[platform])]
        niche = TOPICS[rng.randint(len(TOPICS))]
        peak = int(rng.choice(4, p=SLOT_BY_AGE[age]))
        young = age in ("under_18", "18_24")
        mobile = float(np.clip(rng.normal(0.85 if young or platform == "tiktok" else 0.65, 0.08), 0.3, 0.98))
        followers = int(np.clip(rng.lognormal(8.0, 1.4), 80, 3_000_000))
        offset = OFFSETS[rng.choice(len(OFFSETS), p=[p for _, p in OFFSETS])][0]
        er = float(BASE_ER[platform] * rng.lognormal(0, 0.35))

        def noise(k: int) -> np.ndarray:
            return rng.normal(0, 0.12, k)

        topic_aff = np.full(len(TOPICS), -0.2)
        topic_aff[TOPICS.index(niche)] = 0.8
        topic_aff[rng.randint(len(TOPICS))] += 0.25  # побічний інтерес
        fmt = np.array(FORMAT_AFF[platform]) + (np.array([-0.1, 0.0, 0.25, -0.1]) if young else 0)
        tone = np.array(TONE_AFF[age]) + (np.array([0, 0, 0.3, 0]) if niche == "developer_humor" else 0)
        short_pref = 1.2 * (mobile - 0.65)
        length = np.array([short_pref, 0.1, -short_pref + (0.2 if platform in ("telegram", "facebook") else 0.0)])
        slot = np.full(4, -0.2)
        slot[peak] = 0.6
        slot[(peak + 1) % 4] = 0.1
        backend = np.array([0.0, 0.03])  # хмарна модель трохи якісніша, але платна

        pages.append({
            "page_id": f"p{i + 1:04d}",
            "platform": platform,
            "niche": niche,
            "audience_age": age,
            "peak_slot": SLOTS[peak],
            "utc_offset": offset,
            "mobile_share": round(mobile, 3),
            "followers": followers,
            "base_engagement_rate": round(er, 5),
            "latent_affinity": {
                "topic": (topic_aff + noise(5)).round(3).tolist(),
                "format": (fmt + noise(4)).round(3).tolist(),
                "tone": (tone + noise(4)).round(3).tolist(),
                "length": (length + noise(3)).round(3).tolist(),
                "time_slot": (slot + noise(4)).round(3).tolist(),
                "backend": backend.tolist(),
            },
        })
    return pages


def page_context(page: Dict[str, Any]) -> List[float]:
    """Спостережувані ознаки сторінки — контекст для політики (23 ознаки + доповнення до 24)."""
    x = [1.0 if page["platform"] == p else 0.0 for p in PLATFORMS]
    x += [1.0 if page["audience_age"] == a else 0.0 for a in AGES]
    x += [1.0 if page["niche"] == t else 0.0 for t in TOPICS]
    x += [1.0 if page["peak_slot"] == s else 0.0 for s in SLOTS]
    x += [float(page["mobile_share"]), math.log10(max(page["followers"], 1)) / 6]
    return x + [0.0] * (CONTEXT_DIM - len(x))


def taste(page: Dict[str, Any], arms: Dict[str, str]) -> float:
    aff = page["latent_affinity"]
    return sum(aff[d][DEFAULT_DIMENSIONS[d].index(a)] for d, a in arms.items() if d in aff)


def best_arms(page: Dict[str, Any]) -> Dict[str, str]:
    """Оракул: смаки адитивні, тож оптимум — найкраще значення в кожному вимірі."""
    return {d: DEFAULT_DIMENSIONS[d][int(np.argmax(v))] for d, v in page["latent_affinity"].items()}


def baseline(page: Dict[str, Any]) -> Dict[str, float]:
    """Подвоєні лічильники типового допису сторінки — нормування винагороди (compute_reward)."""
    v0 = page["followers"] * REACH[page["platform"]]
    r0 = v0 * page["base_engagement_rate"]
    return {"max_views": 2 * v0, "max_reactions": 2 * r0, "max_comments": 2 * 0.12 * r0 + 1,
            "max_saves": 2 * 0.10 * r0 + 1, "max_shares": 2 * 0.08 * r0 + 1}


def expected_counts(page: Dict[str, Any], arms: Dict[str, str]) -> Dict[str, float]:
    m = math.exp(TASTE_SCALE * taste(page, arms))
    views = page["followers"] * REACH[page["platform"]] * (0.8 + 0.2 * m)
    reactions = views * min(0.9, page["base_engagement_rate"] * m)
    return {"views": views, "reactions": reactions, "comments": 0.12 * reactions,
            "shares": 0.08 * reactions, "saves": 0.10 * reactions}


def sample_counts(page: Dict[str, Any], arms: Dict[str, str], rng: np.random.RandomState) -> Dict[str, int]:
    e = expected_counts(page, arms)
    views = int(rng.poisson(e["views"]))
    p = e["reactions"] / e["views"] if e["views"] else 0.0
    reactions = int(rng.binomial(views, min(p, 0.95)))
    return {
        "views": views,
        "reactions": reactions,
        "comments": int(rng.binomial(reactions, 0.12)),
        "shares": int(rng.binomial(reactions, 0.08)),
        "saves": int(rng.binomial(reactions, 0.10)),
    }


def to_row(page: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: v for k, v in page.items() if k != "latent_affinity"}
    row["latent_affinity"] = json.dumps(page["latent_affinity"])
    return row


def from_row(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        **row,
        "utc_offset": int(row["utc_offset"]),
        "mobile_share": float(row["mobile_share"]),
        "followers": int(row["followers"]),
        "base_engagement_rate": float(row["base_engagement_rate"]),
        "latent_affinity": json.loads(row["latent_affinity"]),
    }
