"""
Генерує синтетичний датасет «2000 сторінок авторів» (datasets/):

  pages_2000.csv     — сторінки: мережа, ніша, аудиторія, підписники, залученість
                       і прихована матриця смаків latent_affinity (лише для оцінки);
  posts_history.csv  — по 25 історичних дописів кожної сторінки з лічильниками
                       переглядів, реакцій, коментарів, поширень і збережень.

Історія згенерована «людською» політикою: 60 % дописів — звичні для автора
параметри, решта — випадкові. Датасет відтворюваний (seed).

usage: python scripts/generate_dataset.py [--pages 2000] [--posts 25] [--seed 2026]
"""
import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

from app.decision.factorized_bandit import DEFAULT_DIMENSIONS
from app.simulation import creator_pages as cp

OUT = os.path.join(os.path.dirname(__file__), "..", "datasets")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2000)
    ap.add_argument("--posts", type=int, default=25)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    pages = cp.sample_pages(args.pages, args.seed)
    rng = np.random.RandomState(args.seed + 1)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)

    with open(os.path.join(OUT, f"pages_{args.pages}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cp.to_row(pages[0])))
        w.writeheader()
        w.writerows(cp.to_row(p) for p in pages)

    fields = ["post_id", "page_id", "platform", "published_at_utc", "local_hour", *DEFAULT_DIMENSIONS,
              "views", "reactions", "comments", "shares", "saves"]
    n_posts = 0
    with open(os.path.join(OUT, "posts_history.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for page in pages:
            habit = {d: v[rng.randint(len(v))] for d, v in DEFAULT_DIMENSIONS.items()}
            for k in range(args.posts):
                arms = {d: (habit[d] if rng.rand() < 0.6 else v[rng.randint(len(v))])
                        for d, v in DEFAULT_DIMENSIONS.items()}
                local_hour = cp.SLOT_LOCAL_HOUR[cp.SLOTS.index(arms["time_slot"])]
                day = end - timedelta(days=int(rng.randint(1, 90)))
                published = day.replace(hour=0, minute=int(rng.randint(60))) + timedelta(
                    hours=local_hour - page["utc_offset"])
                n_posts += 1
                w.writerow({
                    "post_id": f"{page['page_id']}-{k + 1:02d}",
                    "page_id": page["page_id"],
                    "platform": page["platform"],
                    "published_at_utc": published.isoformat(),
                    "local_hour": local_hour,
                    **arms,
                    **cp.sample_counts(page, arms, rng),
                })

    by_platform = Counter(p["platform"] for p in pages)
    followers = np.array([p["followers"] for p in pages])
    print(f"pages={len(pages)} posts={n_posts}")
    print("platforms:", dict(by_platform))
    print(f"followers: median={int(np.median(followers))} p90={int(np.percentile(followers, 90))} "
          f"max={followers.max()}")


if __name__ == "__main__":
    main()
