"""
Chaos-експеримент у стилі Chaos Monkey для docker compose: під час навантаження
через випадкові інтервали «вбиває» випадковий контейнер обраних сервісів
(docker kill — аварійне завершення без коректної зупинки), тримає його вимкненим
downtime секунд і запускає знову (як це зробив би оркестратор).

Журнал подій: <out>/chaos_events.csv (t_kill, t_start, service, container).
Час — від старту скрипта; t0_epoch у першому рядку журналу зводить його з loadtest.py.

usage:
  python scripts/chaos.py --services api publisher worker-telemetry --duration 300 \
         --interval 40 --downtime 15 --project kairo
"""
import argparse
import csv
import os
import random
import subprocess
import time


def containers(project: str, service: str) -> list:
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
         "--filter", f"label=com.docker.compose.service={service}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", nargs="+", default=["api", "publisher", "worker-telemetry"])
    ap.add_argument("--project", default="kairo")
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--interval", type=float, default=40, help="mean interval between failures, s")
    ap.add_argument("--downtime", type=float, default=15)
    ap.add_argument("--warmup", type=float, default=30, help="initial seconds without failures (baseline)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "load"))
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    events = []
    time.sleep(args.warmup)
    while time.time() - t0 < args.duration - args.downtime:
        service = rnd.choice(args.services)
        victims = containers(args.project, service)
        if victims:
            cid = rnd.choice(victims)
            t_kill = round(time.time() - t0, 1)
            subprocess.run(["docker", "kill", cid], capture_output=True)
            print(f"[{t_kill:7.1f}] kill {service} {cid[:12]}", flush=True)
            time.sleep(args.downtime)
            subprocess.run(["docker", "start", cid], capture_output=True)
            t_start = round(time.time() - t0, 1)
            print(f"[{t_start:7.1f}] start {service} {cid[:12]}", flush=True)
            events.append((t_kill, t_start, service, cid[:12]))
        time.sleep(rnd.expovariate(1 / args.interval))

    with open(os.path.join(args.out, "chaos_events.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_kill", "t_start", "service", "container", "t0_epoch"])
        w.writerows([(*e, round(t0, 3)) for e in events])
    print(f"failures injected: {len(events)}")


if __name__ == "__main__":
    main()
