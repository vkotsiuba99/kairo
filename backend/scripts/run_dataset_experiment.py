"""
Порівняння політик на датасеті «2000 сторінок» (кілька зерен) → results/dataset_experiment.json
та results/dataset_regret.csv (криві кумулятивного псевдожалю для графіка).

usage: python scripts/run_dataset_experiment.py [--rounds 20000] [--seeds 1 2 3]
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

from app.simulation.dataset_experiment import load_pages, run

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(ROOT, "datasets", "pages_2000.csv"))
    ap.add_argument("--rounds", type=int, default=20000)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--alpha", type=float, default=0.6)
    args = ap.parse_args()

    pages = load_pages(args.dataset)
    runs = []
    for seed in args.seeds:
        t0 = time.time()
        runs.append(run(pages, args.rounds, seed, alpha=args.alpha))
        print(f"seed {seed}: {time.time() - t0:.0f} s", flush=True)

    policies = list(runs[0]["summary"])
    agg = {}
    for p in policies:
        reward = np.array([r["summary"][p]["cumulative_reward"] for r in runs])
        regret = np.array([r["summary"][p]["cumulative_regret"] for r in runs])
        dims = runs[0]["summary"][p]["optimal_share_last10pct"]
        agg[p] = {
            "cumulative_reward_mean": round(float(reward.mean()), 1),
            "cumulative_reward_std": round(float(reward.std()), 1),
            "cumulative_regret_mean": round(float(regret.mean()), 1),
            "cumulative_regret_std": round(float(regret.std()), 1),
            "avg_reward": round(float(reward.mean()) / args.rounds, 4),
            "optimal_share_last10pct": {
                d: round(float(np.mean([r["summary"][p]["optimal_share_last10pct"][d] for r in runs])), 3)
                for d in dims
            },
        }
    oracle = float(np.mean([r["oracle_reward"] for r in runs]))
    for p in policies:  # частка досяжної винагороди (оракул = 1)
        agg[p]["share_of_oracle"] = round(agg[p]["cumulative_reward_mean"] / oracle, 3)

    out_dir = os.path.join(ROOT, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dataset_experiment.json"), "w", encoding="utf-8") as f:
        json.dump({"pages": len(pages), "rounds": args.rounds, "seeds": args.seeds, "alpha": args.alpha,
                   "oracle_reward_mean": round(oracle, 1), "policies": agg}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "dataset_regret.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", *policies])
        for i, t in enumerate(runs[0]["curve"]["t"]):
            w.writerow([t, *[round(float(np.mean([r["curve"][p][i] for r in runs])), 2) for p in policies]])
    print(json.dumps(agg, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
