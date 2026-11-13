# Kairo

**Adaptive social media content generation driven by audience profiles.**

Kairo decides *what*, *how* and *when* to post for a given audience segment, writes the text,
draws an illustration, publishes through a platform adapter, collects the audience's reaction and
learns from it. The decision module is a factorized contextual bandit (a cascade of LinUCB heads)
that picks six parameters per post: topic, format, tone, length, time slot and compute backend
(local or cloud model).

Part of a master's thesis (Igor Sikorsky Kyiv Polytechnic Institute, 2026).

## Architecture

```
frontend (React, nginx) ──► api (FastAPI, N replicas) ──► PostgreSQL 16 + pgvector
                                 │                             ▲  (posts table = publish queue,
                                 │ LLM / embeddings            │   decisions + rewards = policy state)
                                 ▼                             │
                    LM Studio / OpenRouter          publisher (N replicas) ──► platform adapters
                                                    worker-telemetry ──────► platform metrics
```

* **Audience** — segments are built from fixed dictionaries (age, occupation, region, language,
  device, interests) or clustered from subscriber embeddings. Each segment has an interest
  centroid and a 24-hour activity curve; the timezone offset is DST-aware.
* **Decision** — context = segment centroid features + platform one-hot + hour/day + stats
  (32 dims). Each LinUCB head sees the arms chosen by the previous heads. Policy state is rebuilt
  from the `decisions`/`rewards` tables (latest reward per decision, cached for
  `BANDIT_STATE_TTL_SECONDS`), so every API replica uses the same policy and restarts lose nothing.
* **Generation** — local LLM via LM Studio (OpenAI-compatible) with an OpenRouter fallback;
  illustrations are generated in a worker thread so the event loop is never blocked.
* **Scheduling** — every channel has a posting schedule: *auto* (peak hours of the segment's
  activity curve) or *custom* (weekdays + times + UTC offset). "Queue by schedule" takes the next
  free slot that keeps `min_gap` from other posts of the channel.
* **Publish queue** — the `posts` table itself. A publisher claims due posts with an atomic
  `UPDATE … WHERE status = 'pending'`, publishes up to `PUBLISH_CONCURRENCY` at once, retries with
  exponential backoff (30 s, 60 s, 120 s) and returns stale claims to the queue after
  `PUBLISH_CLAIM_TIMEOUT_SECONDS`. Delivery is at-least-once. "Publish now" uses the same claim.
* **Telemetry** — metrics are polled from the platforms (or ingested via `/telemetry/ingest`),
  turned into a reward (`compute_reward`, normalised by the segment baseline, minus
  `COST_AWARE_LAMBDA × cost`) and fed back to the policy.

Supported networks: Telegram, YouTube, Facebook Pages, Instagram (Graph content publishing),
TikTok (Content Posting API), Mastodon, Bluesky, plus a local archive. Instagram and TikTok pull
media by URL, so `PUBLIC_BASE_URL` must point to a publicly reachable address of the frontend.

## Quick start (Docker)

```bash
cp .env.example .env          # set ENCRYPTION_KEY and the model endpoints
docker compose up -d --build
```

| What | URL |
|---|---|
| UI | http://localhost:3000 |
| API (proxied by nginx) | http://localhost:3000/api/v1/dashboard/metrics |
| Generated media | http://localhost:3000/media/… |

The API port itself is not published by compose; in local development the API docs are at
http://localhost:8000/docs.

Services: `postgres`, `api` (scalable: `--scale api=3`), `publisher` (scalable), `worker-telemetry`,
`worker-tts`, `frontend`. The scheduler loop runs only in `publisher`
(`RUN_SCHEDULER_IN_API=false` in compose).

## Local development (no Docker)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000 --reload             # SQLite fallback if Postgres is down

cd ../frontend
npm install
npm run dev                                           # http://localhost:5173
```

With `RUN_SCHEDULER_IN_API=true` (default) the API also runs the publish loop, so no separate
publisher is needed in development.

## Configuration

See `.env.example`. The essentials:

| Variable | Purpose |
|---|---|
| `ENCRYPTION_KEY` | 64 hex chars, AES-256-GCM key for stored channel credentials |
| `POSTGRES_*`, `USE_SQLITE_FALLBACK` | database |
| `LLM_BASE_URL`, `LLM_MODEL_NAME`, `LLM_EMBEDDING_MODEL` | local model (LM Studio) |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL_NAME`, `COST_AWARE_LAMBDA` | cloud fallback and its cost penalty |
| `PUBLIC_BASE_URL` | public URL for media (Instagram, TikTok) |
| `SCHEDULER_INTERVAL_SECONDS`, `PUBLISH_*` | publish queue tuning |
| `*_API_BASE`, `GOOGLE_OAUTH_TOKEN_URL` | platform endpoints (overridden by the mock provider in tests) |

Channels are connected in the **Channels** tab; the form is built from each adapter's
credential schema. Telegram, Bluesky and Mastodon can also be pre-connected from `.env`.

## Tests

```bash
cd backend
pytest -q
```

Covers the publish queue (claiming, retries, stale claims), schedule planning, policy rebuild
from the database, telemetry ingest and the platform adapters against mocked HTTP.

## Dataset experiment ("2000 pages")

A synthetic dataset of 2000 creator pages across 7 networks (`backend/datasets/pages_2000.csv`)
and 50 000 historical posts (`posts_history.csv`). Each page has observable features and a hidden
taste matrix used only by the simulator.

```bash
cd backend
python scripts/generate_dataset.py --pages 2000 --posts 25 --seed 2026
python scripts/run_dataset_experiment.py --rounds 20000 --seeds 1 2 3
```

Results go to `backend/results/` (`dataset_experiment.json`, `dataset_regret.csv`). With the
default settings the bandit reaches 95 % of the oracle reward (random 59.5 %, fixed heuristic
57.9 %, greedy average 52.7 %).

## Load and chaos testing

`docker-compose.loadtest.yml` replaces every external network and the LLM with `mock_social`
(Telegram, Graph API, TikTok, Google/YouTube, Mastodon, Bluesky and an OpenAI-compatible LLM;
latency, error rate and time scale are configurable).

```bash
docker compose -p kairo -f docker-compose.yml -f docker-compose.loadtest.yml \
    up -d --build --scale api=3 --scale publisher=2
docker compose -p kairo -f docker-compose.yml -f docker-compose.loadtest.yml \
    run --rm --no-deps loadgen python scripts/loadtest.py --setup --users 200 --duration 300 \
    --base-url http://frontend/api/v1 --out results/load/u200
# chaos, from the host, in parallel with the load:
python backend/scripts/chaos.py --project kairo --services api publisher worker-telemetry
# clean up images and volumes afterwards:
docker compose -p kairo -f docker-compose.yml -f docker-compose.loadtest.yml down -v --rmi all
```

`loadtest.py` writes per-request logs, a per-second timeline and a summary (RPS, p50/p95/p99,
error rate, published posts). `chaos.py` kills a random container with `docker kill`, keeps it
down and starts it again, logging every event.

`scripts/load_report.py <run dir>` turns a run into the table numbers (RPS, p95, errors, lost and
duplicate posts, publish lag); `scripts/load_model.py` extrapolates measured runs with operational laws
(response time law R = N/X − Z, utilization law U = X·D).

Results on one 16-core machine (3 API replicas × 2 uvicorn workers, 2 publishers, 300 s per run):

| Users | RPS | Mean response | p95 read | p95 generate | Errors | Published / lost / duplicate |
|---|---|---|---|---|---|---|
| 50 | 15.7 | 2.2 s | 3.2 s | 24.6 s | 0.8 % | 522 / 0 / 0 |
| 100 | 16.0 | 5.3 s | 19.0 s | 36.8 s | 4.0 % | 414 / 0 / 2 |
| 200 (model) | 16.0 | 11.5 s | 41.5 s | > 60 s | ≈ 7.9 % | — |
| 400 (model) | 16.0 | 24.1 s | > 60 s | > 60 s | ≈ 13.3 % | — |

The stand saturates at ≈16 RPS; the model built on the 50-user run predicted the 100-user mean response
within 2.3 %. CPU demand is 0.57–0.75 core·s per request, almost all of it in the API (generation with
video encoding); PostgreSQL needs ≤ 0.014 core·s, so growth beyond this point means more API nodes or a
separate video worker, not a bigger database. The first runs exposed four defects, all fixed: missing
`greenlet` (`sqlalchemy[asyncio]`), ffmpeg and the WAV fallback blocking the event loop, OpenBLAS
spinning 16 threads per process (`OPENBLAS_NUM_THREADS=1` in the image), and `docker compose run`
resetting the API scale (`--no-deps`). Larger runs and the chaos scenario were not executed locally.

## Deployment

`deploy/terraform` describes AWS infrastructure (VPC, EKS, RDS for PostgreSQL 16, ECR);
`deploy/helm/kairo` deploys the services to Kubernetes (HPA and PodDisruptionBudget for the
API, separate publisher and telemetry deployments, ingress for the frontend). These are prepared
but have not been applied to a real cloud account.

## Repository layout

```
backend/
  app/
    adapters/      telegram, youtube, facebook, instagram, tiktok, mastodon, bluesky, local archive
    api/v1/        platforms, audience, content, schedule, telemetry, experiments, dashboard, logs
    audience/      embeddings, clustering, segment profiles, activity curves
    decision/      LinUCB, factorized bandit, baselines, reward, policy state
    generation/    LLM client, prompts, images, video
    scheduler/     schedule planner, publish queue runner
    simulation/    synthetic audience, "2000 pages" model, experiments
    telemetry/     metrics collector
  mock_social/     mock platform + LLM provider for load tests
  datasets/        "2000 pages" dataset
  scripts/         workers, dataset tools, load and chaos tests
  tests/
frontend/          React 19 + Vite + Tailwind
deploy/            Terraform and Helm
docker-compose.yml, docker-compose.loadtest.yml
```

## Known limitations

* Instagram publishing needs a Business/Creator account and app review; TikTok direct posting
  needs an audited app (unaudited apps can only post `SELF_ONLY`).
* Delivery is at-least-once: a crash between the platform's response and the database commit
  can produce a duplicate post.
* The dataset is synthetic: real counterfactual reactions (how the same audience would react to
  a different variant of a post) are not observable.
