"""
Навантажувальний тест Kairo: N віртуальних користувачів (сторінки з датасету
«2000 сторінок») одночасно читають стрічку й дашборд, генерують матеріали,
ставлять їх у чергу публікацій і надсилають телеметрію.

Соцмережі й LLM — мок-провайдер (mock_social), тож тест вимірює саму систему:
API, БД, політику, чергу та публікатор. Результати:
  <out>/requests.csv   — кожен запит (час, операція, статус, затримка);
  <out>/timeline.csv   — посекундно: запити, помилки, p95, опубліковано дописів;
  <out>/summary.json   — по операціях: кількість, RPS, p50/p95/p99, частка помилок.

usage:
  python scripts/loadtest.py --base-url http://localhost:3000/api/v1 --users 200 --duration 300
  (перший запуск із --setup підключає мок-акаунти всіх 7 мереж)
"""
import argparse
import asyncio
import csv
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(ROOT))

MOCK = os.getenv("MOCK_URL", "http://mock-social:9100")
MOCK_ACCOUNTS = {
    "telegram": {"bot_token": "1:mock", "chat_id": "@mock"},
    "mastodon": {"instance_url": f"{MOCK}/mastodon", "access_token": "mock"},
    "bluesky": {"handle": "kairo.mock", "app_password": "mock", "service": f"{MOCK}/bluesky"},
    "youtube": {"client_id": "mock", "client_secret": "mock", "refresh_token": "mock"},
    "facebook": {"page_id": "1001", "page_access_token": "mock"},
    "instagram": {"ig_user_id": "2002", "access_token": "mock"},
    "tiktok": {"access_token": "mock"},
}
FORMAT_BY_PLATFORM = {"instagram": "image_post", "tiktok": "video_short", "youtube": "video_short"}
WEIGHTS = [  # (операція, вага)
    ("list_content", 30), ("dashboard", 15), ("list_queue", 10),
    ("generate", 15), ("schedule", 15), ("telemetry", 15),
]


class Recorder:
    def __init__(self):
        self.rows: List[tuple] = []
        self.t0 = time.time()

    async def call(self, client: httpx.AsyncClient, name: str, method: str, url: str, **kw) -> Any:
        start = time.perf_counter()
        status, data = 0, None
        try:
            resp = await client.request(method, url, **kw)
            status = resp.status_code
            if resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
        except Exception as e:  # тайм-аут, обрив з'єднання під час chaos
            status = -1
            data = {"error": str(e)}
        self.rows.append((round(time.time() - self.t0, 3), name, status,
                          round((time.perf_counter() - start) * 1000, 1)))
        return data if 200 <= status < 300 else None


async def setup(base: str, rec: Recorder) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        platforms = await rec.call(c, "setup", "GET", "/platforms") or []
        for p in platforms:
            if p["code"] in MOCK_ACCOUNTS:
                await rec.call(c, "setup", "POST", f"/platforms/{p['id']}/accounts/connect",
                               json={"external_account_id": f"mock_{p['code']}", "credentials": MOCK_ACCOUNTS[p["code"]]})
        print(f"setup: connected {len(MOCK_ACCOUNTS)} mock accounts")


async def virtual_user(uid: int, page: Dict[str, Any], base: str, rec: Recorder, stop_at: float,
                       accounts: Dict[str, int], shared: Dict[str, list], think: float) -> None:
    rnd = random.Random(uid)
    platform = page["platform"]
    my_items: List[int] = []
    ops, weights = zip(*WEIGHTS)
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        while time.time() < stop_at:
            op = rnd.choices(ops, weights)[0]
            if op == "list_content":
                await rec.call(c, op, "GET", "/content", params={"limit": 20})
            elif op == "dashboard":
                await rec.call(c, op, "GET", "/dashboard/metrics")
            elif op == "list_queue":
                await rec.call(c, op, "GET", "/schedule", params={"status": "pending", "limit": 50})
            elif op == "generate":
                data = await rec.call(c, op, "POST", "/content/generate", json={
                    "platform_code": platform, "backend": "local",
                    "format_type": FORMAT_BY_PLATFORM.get(platform)})
                if data:
                    my_items.append(data["content_item"]["id"])
            elif op == "schedule" and my_items and platform in accounts:
                at = datetime.now(timezone.utc) + timedelta(seconds=rnd.uniform(0, 20))
                await rec.call(c, op, "POST", "/schedule", json={
                    "content_item_id": my_items.pop(0), "connected_account_id": accounts[platform],
                    "scheduled_at": at.isoformat()})
            elif op == "telemetry" and shared["published"]:
                code, ext = rnd.choice(shared["published"])
                await rec.call(c, op, "POST", "/telemetry/ingest", json={
                    "platform_code": code, "external_post_id": ext,
                    "event_type": rnd.choice(["view", "reaction", "comment", "share", "save"])})
            await asyncio.sleep(rnd.expovariate(1 / think) if think else 0)


async def sampler(base: str, rec: Recorder, stop_at: float, accounts: Dict[str, int],
                  shared: Dict[str, list], published: List[tuple]) -> None:
    code_by_acc = {v: k for k, v in accounts.items()}
    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        while time.time() < stop_at + 15:  # ще 15 с після навантаження — черга дорізається
            try:
                posts = (await c.get("/schedule", params={"status": "published", "limit": 1000})).json()
                shared["published"] = [(code_by_acc.get(p["connected_account_id"], "local_archive"),
                                        p["external_post_id"]) for p in posts if p.get("external_post_id")]
                metrics = (await c.get("/dashboard/metrics")).json()
                published.append((round(time.time() - rec.t0, 1), metrics.get("published_posts", 0)))
            except Exception:
                published.append((round(time.time() - rec.t0, 1), None))
            await asyncio.sleep(5)


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q / 100 * len(s)))], 1)


def write_reports(rec: Recorder, published: List[tuple], out: str, args) -> Dict[str, Any]:
    os.makedirs(out, exist_ok=True)
    rows = [r for r in rec.rows if r[1] != "setup"]
    with open(os.path.join(out, "requests.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "op", "status", "latency_ms"])
        w.writerows(rows)

    per_sec = defaultdict(list)
    for t, op, st, lat in rows:
        per_sec[int(t)].append((st, lat))
    with open(os.path.join(out, "timeline.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "rps", "errors", "p95_ms"])
        for sec in sorted(per_sec):
            vals = per_sec[sec]
            w.writerow([sec, len(vals), sum(1 for st, _ in vals if not 200 <= st < 300),
                        percentile([lat for _, lat in vals], 95)])
    with open(os.path.join(out, "published.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "published_total"])
        w.writerows(published)

    duration = max((r[0] for r in rows), default=1.0)
    by_op = defaultdict(list)
    for _, op, st, lat in rows:
        by_op[op].append((st, lat))
    summary = {"users": args.users, "duration_s": args.duration, "t0_epoch": round(rec.t0, 3), "requests": len(rows),
               "rps": round(len(rows) / duration, 1), "operations": {}}
    for op, vals in sorted(by_op.items()):
        lats = [lat for st, lat in vals if 200 <= st < 300]
        summary["operations"][op] = {
            "count": len(vals), "rps": round(len(vals) / duration, 2),
            "p50_ms": percentile(lats, 50), "p95_ms": percentile(lats, 95), "p99_ms": percentile(lats, 99),
            "error_rate": round(sum(1 for st, _ in vals if not 200 <= st < 300) / len(vals), 4),
        }
    all_ok = [lat for _, _, st, lat in rows if 200 <= st < 300]
    summary["p95_ms"] = percentile(all_ok, 95)
    summary["error_rate"] = round(sum(1 for r in rows if not 200 <= r[2] < 300) / max(len(rows), 1), 4)
    pub = [p for _, p in published if p is not None]
    summary["published_during_test"] = (pub[-1] - pub[0]) if len(pub) > 1 else 0
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:3000/api/v1")
    ap.add_argument("--users", type=int, default=100)
    ap.add_argument("--duration", type=int, default=120)
    ap.add_argument("--ramp", type=int, default=20, help="seconds to ramp up all users")
    ap.add_argument("--think", type=float, default=1.0, help="mean user think time, s")
    ap.add_argument("--dataset", default=os.path.join(ROOT, "datasets", "pages_2000.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "load"))
    ap.add_argument("--setup", action="store_true")
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        pages = list(csv.DictReader(f))
    rec = Recorder()
    if args.setup:
        await setup(args.base_url, rec)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as c:
        accounts = {a["platform_code"]: a["id"] for a in (await c.get("/accounts")).json()}
    stop_at = time.time() + args.duration
    shared: Dict[str, list] = {"published": []}
    published: List[tuple] = []

    async def start_user(i: int):
        await asyncio.sleep(args.ramp * i / max(args.users, 1))
        await virtual_user(i, pages[i % len(pages)], args.base_url, rec, stop_at, accounts, shared, args.think)

    print(f"{args.users} users, {args.duration} s -> {args.base_url}")
    await asyncio.gather(sampler(args.base_url, rec, stop_at, accounts, shared, published),
                         *(start_user(i) for i in range(args.users)))
    summary = write_reports(rec, published, args.out, args)
    print(json.dumps({k: v for k, v in summary.items() if k != "operations"}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
