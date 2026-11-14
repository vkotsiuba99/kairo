"""
Load/chaos report: turns one run directory of loadtest.py (+ chaos.py, + the DB/mock snapshots taken
after the queue drained) into the numbers for the thesis tables.

Inputs in <run dir>:
  requests.csv, summary.json        — loadtest.py
  chaos_events.csv                  — chaos.py (optional)
  posts.csv                         — posts table after drain: id,status,attempts,scheduled,published (epoch s)
  mock_stats.json                   — GET /stats of the mock provider after drain

usage: python scripts/load_report.py results/load/u100 [results/load/chaos100 ...]
"""
import csv
import json
import os
import sys
from typing import Any, Dict, List

READ_OPS = {"list_content", "dashboard", "list_queue"}


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q / 100 * len(s)))], 1)


def load_rows(path: str, t0: float) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        return [{"t": t0 + float(t), "op": op, "ok": 200 <= int(st) < 300, "ms": float(ms)}
                for t, op, st, ms in r if op != "setup"]


def report(run: str) -> Dict[str, Any]:
    summary = json.load(open(os.path.join(run, "summary.json"), encoding="utf-8"))
    t0 = summary["t0_epoch"]
    rows = load_rows(os.path.join(run, "requests.csv"), t0)
    dur = summary["duration_s"]
    out: Dict[str, Any] = {
        "run": os.path.basename(run.rstrip("/\\")),
        "users": summary["users"],
        "requests": len(rows),
        "rps": round(len(rows) / dur, 1),
        "p95_read_ms": pct([r["ms"] for r in rows if r["op"] in READ_OPS and r["ok"]], 95),
        "p95_generate_ms": pct([r["ms"] for r in rows if r["op"] == "generate" and r["ok"]], 95),
        "p95_all_ms": pct([r["ms"] for r in rows if r["ok"]], 95),
        "error_pct": round(100 * sum(not r["ok"] for r in rows) / max(len(rows), 1), 2),
    }

    posts_path = os.path.join(run, "posts.csv")
    if os.path.exists(posts_path):
        with open(posts_path, encoding="utf-8") as f:
            posts = list(csv.DictReader(f))
        by = {}
        for p in posts:
            by[p["status"]] = by.get(p["status"], 0) + 1
        published = [p for p in posts if p["status"] == "published"]
        lag = [float(p["published"]) - float(p["scheduled"]) for p in published if p["published"] and p["scheduled"]]
        out.update({
            "posts_total": len(posts),
            "posts_by_status": by,
            "published": by.get("published", 0),
            "lost": by.get("pending", 0) + by.get("publishing", 0),   # still not done after the drain
            "retried": sum(1 for p in posts if int(p["attempts"] or 0) > 1),
            "lag_p50_s": pct(lag, 50),
            "lag_p95_s": pct(lag, 95),
            "lag_max_s": round(max(lag), 1) if lag else 0.0,
        })
        mock_path = os.path.join(run, "mock_stats.json")
        if os.path.exists(mock_path):
            mock = json.load(open(mock_path, encoding="utf-8"))
            # the mock counts every post a platform actually created; more than the DB recorded = duplicates
            out["mock_published"] = mock["published"]
            out["duplicates"] = max(mock["published"] - out["published"], 0)

    chaos_path = os.path.join(run, "chaos_events.csv")
    if os.path.exists(chaos_path):
        with open(chaos_path, encoding="utf-8") as f:
            events = list(csv.DictReader(f))
        per: Dict[str, Dict[str, Any]] = {}
        windows = []
        for e in events:
            c0 = float(e["t0_epoch"])
            k, s = c0 + float(e["t_kill"]), c0 + float(e["t_start"])
            windows.append((k, s + 15))
            win = [r for r in rows if k <= r["t"] <= s + 15]
            fails = [r for r in win if not r["ok"]]
            d = per.setdefault(e["service"], {"kills": 0, "win_req": 0, "win_err": 0, "impact_s": [],
                                              "ops": {}, "pub_during": 0, "pub_after_60s": 0})
            d["kills"] += 1
            d["win_req"] += len(win)
            d["win_err"] += len(fails)
            for r in win:  # які операції зачепила відмова, а які працювали далі
                n, f = d["ops"].get(r["op"], (0, 0))
                d["ops"][r["op"]] = (n + 1, f + (not r["ok"]))
            if posts_path and os.path.exists(posts_path):
                pub_t = [float(p["published"]) for p in posts if p["published"]]
                d["pub_during"] += sum(1 for t in pub_t if k <= t <= s)
                d["pub_after_60s"] += sum(1 for t in pub_t if s < t <= s + 60)
            # how long requests kept failing after the kill (0 = nobody noticed)
            d["impact_s"].append(round(max(r["t"] for r in fails) - k, 1) if fails else 0.0)
            if posts_path and os.path.exists(posts_path):
                hit = [float(p["published"]) - float(p["scheduled"]) for p in posts
                       if p["published"] and p["scheduled"] and k <= float(p["scheduled"]) <= s]
                d.setdefault("lag_in_window", []).extend(hit)
        outside = [r for r in rows if not any(a <= r["t"] <= b for a, b in windows)]
        out["baseline_error_pct"] = round(100 * sum(not r["ok"] for r in outside) / max(len(outside), 1), 2)
        out["chaos"] = {
            svc: {
                "kills": d["kills"],
                "error_pct_in_window": round(100 * d["win_err"] / max(d["win_req"], 1), 2),
                "impact_max_s": max(d["impact_s"]),
                "publish_lag_max_s": round(max(d.get("lag_in_window") or [0.0]), 1),
                "error_pct_by_op": {op: round(100 * f / n, 1) for op, (n, f) in sorted(d["ops"].items())},
                "published_during_outage": d["pub_during"],
                "published_60s_after": d["pub_after_60s"],
            } for svc, d in sorted(per.items())
        }
    return out


if __name__ == "__main__":
    results = [report(r) for r in sys.argv[1:]]
    print(json.dumps(results, ensure_ascii=False, indent=2))
