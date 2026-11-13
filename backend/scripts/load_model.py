"""
Load model: extrapolates measured load runs to higher user counts with operational laws
(closed interactive system: N users, think time Z).

  Response time law   R = N / X - Z
  Utilization law     U_k = X * D_k  ->  D_k = U_k / X   (service demand of resource k per request)
  Asymptotic bound    X(N) <= min(N / (D + Z), 1 / D_max)

The runs show X already flat between 50 and 100 users, i.e. the stand is saturated: X(N) = X_max and
R(N) = N / X_max - Z. Latency percentiles are taken from the measured distribution at the highest
measured load, scaled by R(N) / R(measured); requests whose scaled latency exceeds the client timeout
count as errors. These are estimates, not measurements.

usage: python scripts/load_model.py results/load/u50 results/load/u100 --watch ../../_tmp/watch.log
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
from typing import Dict, List

THINK_S = 1.0           # mean think time of a virtual user (loadtest.py --think)
TIMEOUT_MS = 60_000     # client timeout of loadtest.py
READ_OPS = {"list_content", "dashboard", "list_queue"}


def pct(values: List[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q / 100 * len(s)))] if s else 0.0


def cpu_cores(watch_log: str, t0: float, dur: float) -> Dict[str, float]:
    """Mean CPU (cores) per service from docker stats lines in the watchdog log, inside [t0, t0+dur]."""
    day = dt.datetime.fromtimestamp(t0).date()
    acc: Dict[str, List[float]] = {}
    for line in open(watch_log, encoding="utf-8", errors="replace"):
        m = re.match(r"(\d\d):(\d\d):(\d\d) C=", line)
        if not m:
            continue
        ts = dt.datetime.combine(day, dt.time(*map(int, m.groups()))).timestamp()
        if ts < t0 and t0 - ts > 12 * 3600:      # crossed midnight
            ts += 86400
        if not (t0 + 20 <= ts <= t0 + dur):       # skip ramp-up
            continue
        sample: Dict[str, float] = {}
        for name, cpu in re.findall(r"(?:hackwork|kairo)-([a-z-]+?)(?:-\d+|-run-[0-9a-f]+)=([\d.]+)%", line):
            if name == "loadgen":
                continue
            sample[name] = sample.get(name, 0.0) + float(cpu) / 100
        for k, v in sample.items():
            acc.setdefault(k, []).append(v)
    return {k: round(sum(v) / len(v), 2) for k, v in acc.items()}


def load_run(run: str, watch: str) -> Dict:
    s = json.load(open(os.path.join(run, "summary.json"), encoding="utf-8"))
    with open(os.path.join(run, "requests.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["op"] != "setup"]
    x = len(rows) / s["duration_s"]
    cores = cpu_cores(watch, s["t0_epoch"], s["duration_s"]) if watch else {}
    return {"users": s["users"], "x": x, "r": s["users"] / x - THINK_S, "rows": rows, "cores": cores}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--watch", default="")
    ap.add_argument("--users", type=int, nargs="+", default=[200, 400, 800])
    a = ap.parse_args()
    runs = [load_run(r, a.watch) for r in a.runs]
    base = runs[-1]                                    # highest measured load
    x_max = max(r["x"] for r in runs)
    out = {"measured": [], "model": []}
    for r in runs:
        total = sum(r["cores"].values())
        out["measured"].append({
            "users": r["users"], "rps": round(r["x"], 1), "mean_response_s": round(r["r"], 2),
            "cpu_cores": round(total, 2), "cpu_by_service": r["cores"],
            "cpu_core_s_per_request": round(total / r["x"], 3) if total else None,
        })
    if len(runs) > 1:                                  # check: predict the last run from the first one
        first = runs[0]
        pred = base["users"] / first["x"] - THINK_S
        out["validation"] = {"predicted_mean_response_s": round(pred, 2), "measured_mean_response_s": round(base["r"], 2),
                             "error_pct": round(100 * abs(pred - base["r"]) / base["r"], 1)}
    for n in a.users:
        r_n = n / x_max - THINK_S
        k = r_n / base["r"]
        lat = [(row["op"], float(row["latency_ms"]) * k, 200 <= int(row["status"]) < 300) for row in base["rows"]]
        err = sum(1 for _, ms, ok in lat if not ok or ms > TIMEOUT_MS) / len(lat)

        def p95(ops) -> object:
            # a percentile past the client timeout is not a latency any more: more than 5 % time out
            v = pct([ms for op, ms, ok in lat if op in ops and ok], 95)
            return f">{TIMEOUT_MS // 1000} s (timeout)" if v > TIMEOUT_MS else round(v)

        out["model"].append({
            "users": n, "rps": round(x_max, 1), "mean_response_s": round(r_n, 1),
            "p95_read_ms": p95(READ_OPS), "p95_generate_ms": p95({"generate"}),
            "error_pct": round(100 * err, 1),
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
