"""
Chaos-експеримент у стилі Chaos Monkey для docker compose: під час навантаження
«вбиває» контейнери (docker kill — аварійне завершення без коректної зупинки),
тримає їх вимкненими downtime секунд і запускає знову (як це зробив би оркестратор).

Два режими:
  * випадковий — через випадкові інтервали одна випадкова репліка одного з сервісів;
  * план (--plan) — повна відмова сервісу: усі його репліки водночас, у заданий момент,
    по черзі для кількох сервісів. Так видно, що відмова одного сервісу не зупиняє решту.

Журнал подій: <out>/chaos_events.csv (t_kill, t_start, service, container, t0_epoch).
Час — від старту скрипта; t0_epoch зводить його з loadtest.py.

usage:
  python scripts/chaos.py --services content scheduler publishing --duration 300 \
         --interval 40 --downtime 15 --project kairo
  python scripts/chaos.py --plan 40:publishing:60,125:content:45,190:rabbitmq:40,250:scheduler:30
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


def outage(project: str, service: str, downtime: float, t0: float, victims=None):
    """Вимикає вказані (або всі) контейнери сервісу, тримає downtime с і запускає знову."""
    victims = victims or containers(project, service)
    if not victims:
        return None
    t_kill = round(time.time() - t0, 1)
    subprocess.run(["docker", "kill", *victims], capture_output=True)
    print(f"[{t_kill:7.1f}] kill {service} x{len(victims)}", flush=True)
    time.sleep(downtime)
    subprocess.run(["docker", "start", *victims], capture_output=True)
    t_start = round(time.time() - t0, 1)
    print(f"[{t_start:7.1f}] start {service} x{len(victims)}", flush=True)
    return (t_kill, t_start, service, " ".join(v[:12] for v in victims))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", nargs="+", default=["content", "scheduler", "publishing"])
    ap.add_argument("--project", default="kairo")
    ap.add_argument("--duration", type=int, default=300)
    ap.add_argument("--interval", type=float, default=40, help="mean interval between failures, s")
    ap.add_argument("--downtime", type=float, default=15)
    ap.add_argument("--warmup", type=float, default=30, help="initial seconds without failures (baseline)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--plan", default="", help="offset:service:downtime,… — outages of whole services")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "load"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    events = []
    if args.plan:
        for step in args.plan.split(","):
            offset, service, downtime = step.split(":")
            time.sleep(max(0.0, t0 + float(offset) - time.time()))
            e = outage(args.project, service, float(downtime), t0)
            if e:
                events.append(e)
    else:
        rnd = random.Random(args.seed)
        time.sleep(args.warmup)
        while time.time() - t0 < args.duration - args.downtime:
            service = rnd.choice(args.services)
            victims = containers(args.project, service)
            if victims:
                e = outage(args.project, service, args.downtime, t0, [rnd.choice(victims)])
                events.append(e)
            time.sleep(rnd.expovariate(1 / args.interval))

    with open(os.path.join(args.out, "chaos_events.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_kill", "t_start", "service", "container", "t0_epoch"])
        w.writerows([(*e, round(t0, 3)) for e in events])
    print(f"failures injected: {len(events)}")


if __name__ == "__main__":
    main()
