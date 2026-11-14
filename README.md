# Kairo

**Adaptive social media content generation driven by audience profiles.**

Kairo decides *what*, *how* and *when* to post for a given audience segment, writes the text,
draws an illustration, publishes through a platform adapter, collects the audience's reaction and
learns from it. The decision module is a factorized contextual bandit (a cascade of LinUCB heads)
that picks six parameters per post: topic, format, tone, length, time slot and compute backend
(local or cloud model).

Part of a master's thesis (Igor Sikorsky Kyiv Polytechnic Institute, 2026).

## Architecture

Version 2 splits the system into six services, each with its own database. Version 1 (a modular
monolith with one shared PostgreSQL) is kept on the `v1-shared-db` branch and the `v1.0` tag.

```
                    gateway (nginx: UI + /api/v1/* routing)
   ┌───────────┬───────────┬────────────┬───────────┬─────────┬─────────┐
 content    scheduler   publishing   telemetry    media     trends
 (bandit,   (schedules, (credentials,(rewards,    (images,  (mention
 segments,  publish     7 adapters,  dashboard,   video,    collectors,
 generation) queue)     metrics)     log)         speech)   topics)
   DB          DB          DB           DB        storage     DB
   └───────────┴─────── RabbitMQ (events, transactional outbox) ───────┘
```

| Service | Owns | Talks to |
|---|---|---|
| **content** | segments and subscriber profiles, policy decisions and rewards, generated items, experiments | media (REST: images, video), scheduler (REST: enqueue) |
| **scheduler** | channel schedules, the publish queue (`posts`) | content, publishing (REST) |
| **publishing** | platforms, connected accounts (encrypted credentials), publication log | emits `post.published`, `post.metrics`, `audience.interactions` |
| **telemetry** | tracked posts, telemetry events; dashboard and activity log (aggregated from the others) | consumes `post.published`, `post.metrics`; emits `reward.computed` |
| **media** | generated files (no database) | — |
| **trends** | mention collectors, demand catalog | — |

* **Commands vs facts.** A call that needs an answer now (publish a post, draw an image, read an
  item) is REST; a fact others react to later is an event. Events are written to an `outbox` table
  in the same transaction as the change and relayed to RabbitMQ (topic exchange `kairo.events`,
  one durable queue per service); consumers record processed event ids in the same transaction
  as their effects, so redelivery changes nothing. Delivery is at-least-once.
* **Delayed feedback fits events.** Reactions arrive hours later anyway: Publishing polls the
  counters and emits `post.metrics`, Telemetry turns increments into a reward and emits
  `reward.computed`, Content stores it and the policy is rebuilt from its own database.
* **Audience** — segments are built from fixed dictionaries (age, occupation, region, language,
  device, interests) or clustered from subscriber embeddings. Each segment has an interest
  centroid and a 24-hour activity curve; the timezone offset is DST-aware.
* **Decision** — context = segment centroid features + platform one-hot + hour/day + stats
  (32 dims). Each LinUCB head sees the arms chosen by the previous heads. Policy state is rebuilt
  from the decisions/rewards tables of the content database (latest reward per decision, cached for
  `BANDIT_STATE_TTL_SECONDS`), so every content replica uses the same policy.
* **Generation** — local LLM via LM Studio (OpenAI-compatible) with an OpenRouter fallback;
  illustrations, video and speech come from the media service, which scales on its own.
* **Scheduling** — every channel has a posting schedule: *auto* (peak hours of the segment's
  activity curve) or *custom* (weekdays + times + UTC offset). "Queue by schedule" takes the next
  free slot that keeps `min_gap` from other posts of the channel.
* **Publish queue** — the `posts` table of the scheduler, with a copy of the item to publish.
  Scheduler replicas claim due posts with an atomic `UPDATE … WHERE status = 'pending'`, ask
  publishing to post them (up to `PUBLISH_CONCURRENCY` at once), retry with exponential backoff
  (30 s, 60 s, 120 s) and return stale claims to the queue after `PUBLISH_CLAIM_TIMEOUT_SECONDS`.
  The broker is deliberately not the queue: scheduled posts wait for days, can be cancelled or
  moved, and their state is shown in the UI.

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
| API through the gateway | http://localhost:3000/api/v1/dashboard/metrics |
| Generated media | http://localhost:3000/media/… |
| RabbitMQ console | http://localhost:15672 |

Containers: `postgres` (one server, a database per service), `rabbitmq`, `content`, `scheduler`,
`publishing`, `publishing-metrics` (counter polling), `telemetry`, `media`, `trends`, `gateway`.
Every service scales with `--scale <service>=N`; `/…/internal/…` routes are reachable only between
services, the gateway returns 404 for them.

## Local development (no Docker)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000 --reload             # SERVICE=all, SQLite, in-process event bus

cd ../frontend
npm install
npm run dev                                           # http://localhost:5173
```

`SERVICE=all` (the default) runs all six services in one process with the same boundaries:
REST between services goes through ASGI, events through an in-process bus, data through the
service's own tables. A single service is started with `SERVICE=<name>`; with `CONTENT_URL`,
`SCHEDULER_URL`, … and `RABBITMQ_URL` set it talks to the others over the network.

## Configuration

See `.env.example`. The essentials:

| Variable | Purpose |
|---|---|
| `ENCRYPTION_KEY` | 64 hex chars, AES-256-GCM key for stored channel credentials |
| `SERVICE` | which service the process runs (`content`, …, `trends`, or `all`) |
| `CONTENT_URL`, `SCHEDULER_URL`, `PUBLISHING_URL`, `TELEMETRY_URL`, `MEDIA_URL`, `TRENDS_URL` | addresses of the other services (empty — same process) |
| `RABBITMQ_URL` | event broker (empty — in-process bus) |
| `POSTGRES_*`, `USE_SQLITE_FALLBACK`, `SQLITE_DB_PATH` | the service's own database |
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

Covers the publish queue (claiming, retries, stale claims, an unavailable publishing service),
schedule planning, policy rebuild from the database, telemetry increments and reward weights,
the cross-service flow (generate → publish → `post.published` → reaction → `reward.computed`),
idempotent event handling and the platform adapters against mocked HTTP.

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
    up -d --build --scale content=3 --scale scheduler=2 --scale publishing=2 --scale media=2
docker compose -p kairo -f docker-compose.yml -f docker-compose.loadtest.yml \
    run --rm --no-deps loadgen python scripts/loadtest.py --setup --users 50 --duration 300 \
    --base-url http://gateway/api/v1 --out results/load/v2/u50
# chaos, from the host, in parallel with the load:
python backend/scripts/chaos.py --project kairo --services content scheduler publishing
# clean up images and volumes afterwards:
docker compose -p kairo -f docker-compose.yml -f docker-compose.loadtest.yml down -v --rmi all
```

`loadtest.py` writes per-request logs, a per-second timeline and a summary (RPS, p50/p95/p99,
error rate, published posts). `chaos.py` kills a random container with `docker kill`, keeps it
down and starts it again, logging every event.

`scripts/load_report.py <run dir>` turns a run into the table numbers (RPS, p95, errors, lost and
duplicate posts, publish lag); `scripts/load_model.py` extrapolates measured runs with operational laws
(response time law R = N/X − Z, utilization law U = X·D).

Version 1 (modular monolith, shared database; tag `v1.0`) on one 16-core machine (3 API replicas × 2 uvicorn workers, 2 publishers, 300 s per run):

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

`deploy/terraform` describes AWS infrastructure: VPC, EKS, RDS for PostgreSQL 16 (one server, a
database per service), Amazon MQ for RabbitMQ, EFS for the shared media storage, ECR.
`deploy/helm/kairo` deploys every service as its own Deployment and Service (HPA and
PodDisruptionBudget where configured), the counter-polling worker, the gateway behind an ingress,
the media volume and a pre-install job that creates the service databases. These are prepared
but have not been applied to a real cloud account.

## Repository layout

```
backend/
  app/
    adapters/      telegram, youtube, facebook, instagram, tiktok, mastodon, bluesky, local archive
    api/v1/        routes of every service (see app/services.py for who serves what)
    audience/      embeddings, clustering, segment profiles, activity curves
    decision/      LinUCB, factorized bandit, baselines, reward, policy state
    generation/    LLM client, prompts, images, video
    scheduler/     schedule planner, publish queue dispatcher
    publishing/    publishing through adapters, counter polling
    simulation/    synthetic audience, "2000 pages" model, experiments
    telemetry/     increments, rewards
    services.py    which tables and routes each service owns
    events.py      transactional outbox, event bus (RabbitMQ / in-process), idempotent consumers
    handlers.py    every cross-service event subscription in one place
  mock_social/     mock platform + LLM provider for load tests
  datasets/        "2000 pages" dataset
  scripts/         workers, dataset tools, load and chaos tests
  tests/
frontend/          React 19 + Vite + Tailwind
deploy/            Terraform, Helm, Postgres init script
docker-compose.yml, docker-compose.loadtest.yml
```

## Known limitations

* Instagram publishing needs a Business/Creator account and app review; TikTok direct posting
  needs an audited app (unaudited apps can only post `SELF_ONLY`).
* Delivery is at-least-once: a crash between the platform's response and the database commit
  can produce a duplicate post.
* Data across services converges with a small delay (events are asynchronous): the dashboard and
  the activity log show each service's state at the time of the request.
* The dataset is synthetic: real counterfactual reactions (how the same audience would react to
  a different variant of a post) are not observable.
