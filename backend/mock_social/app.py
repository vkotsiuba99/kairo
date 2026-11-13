"""
Мок-провайдер соціальних мереж і LLM для тестів та навантажувальних прогонів.

Емулює саме ті ендпоінти, які викликають адаптери Kairo, тож перевіряється
реальний код адаптерів, а не заглушки. Адаптери спрямовуються сюди змінними
TELEGRAM_API_BASE, GRAPH_API_BASE, TIKTOK_API_BASE, GOOGLE_API_BASE,
GOOGLE_OAUTH_TOKEN_URL, LLM_BASE_URL та облікові дані Mastodon/Bluesky
(instance_url / service).

Поведінка налаштовується змінними середовища:
  MOCK_LATENCY_MS      середня затримка відповіді мережі (типово 80)
  MOCK_LLM_LATENCY_MS  затримка «генерації» LLM (типово 600)
  MOCK_ERROR_RATE      частка відмов публікації з 503 (типово 0)
  MOCK_TIME_SCALE      прискорення часу для росту залученості (типово 60:
                       хвилина прогону = година життя допису)
  MOCK_DATASET         CSV сторінок (datasets/pages_2000.csv) — підписники
                       і рівень залученості для реалістичних лічильників

Запуск: uvicorn mock_social.app:app --port 9100
"""

import asyncio
import csv
import hashlib
import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LATENCY = float(os.getenv("MOCK_LATENCY_MS", "80")) / 1000
LLM_LATENCY = float(os.getenv("MOCK_LLM_LATENCY_MS", "600")) / 1000
ERROR_RATE = float(os.getenv("MOCK_ERROR_RATE", "0"))
TIME_SCALE = float(os.getenv("MOCK_TIME_SCALE", "60"))

app = FastAPI(title="Kairo mock social provider")

# Пам'ять одного процесу: мок запускається одним воркером uvicorn.
POSTS: Dict[str, Dict[str, Any]] = {}
STATS = {"published": 0, "failed": 0, "llm_calls": 0, "requests": 0}


def _load_pages() -> List[Dict[str, Any]]:
    path = os.getenv("MOCK_DATASET", "")
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [
            {"followers": int(r["followers"]), "er": float(r["base_engagement_rate"])}
            for r in csv.DictReader(f)
        ]


PAGES = _load_pages()


def _h(*parts: Any) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


async def _delay(base: Optional[float] = None) -> None:
    STATS["requests"] += 1
    base = LATENCY if base is None else base
    if base > 0:
        await asyncio.sleep(random.uniform(0.5, 1.5) * base)


def _fail() -> Optional[JSONResponse]:
    if ERROR_RATE and random.random() < ERROR_RATE:
        STATS["failed"] += 1
        return JSONResponse({"error": {"message": "mock: service unavailable"}}, status_code=503)
    return None


def _new_post(network: str, text: str = "", prefix: str = "") -> str:
    pid = f"{prefix}{10_000_000 + len(POSTS) + 1}"
    POSTS[f"{network}:{pid}"] = {"created": time.time(), "text": text}
    STATS["published"] += 1
    return pid


def _counts(network: str, pid: str) -> Dict[str, int]:
    """Лічильники ростуть за насичувальною кривою від «віку» допису."""
    meta = POSTS.get(f"{network}:{pid}")
    created = meta["created"] if meta else time.time() - 3600 / TIME_SCALE
    age_h = max(0.0, (time.time() - created) * TIME_SCALE / 3600)
    if PAGES:
        page = PAGES[_h(network, pid) % len(PAGES)]
        followers, er = page["followers"], page["er"]
    else:
        followers, er = 1000 + _h(pid) % 20000, 0.03 + (_h("er", pid) % 50) / 1000
    quality = 0.5 + (_h("q", network, pid) % 100) / 100  # «влучність» допису
    views = int(followers * 0.35 * quality * (1 - math.exp(-age_h / 6)))
    reactions = int(views * er * quality)
    return {
        "views": views,
        "reactions": reactions,
        "comments": int(reactions * 0.12),
        "shares": int(reactions * 0.08),
        "saves": int(reactions * 0.10),
    }


def _recent(network: str, limit: int = 40) -> List[str]:
    keys = [k.split(":", 1)[1] for k in POSTS if k.startswith(f"{network}:")]
    return keys[-limit:]


INTERESTS = ["machine learning", "web development", "cybersecurity", "game dev", "startups", "design"]


def _interactions(network: str) -> List[Dict[str, Any]]:
    """Взаємодії конкретних користувачів — для профілів аудиторії."""
    out = []
    for pid in _recent(network, 10):
        c = _counts(network, pid)
        for i in range(min(c["reactions"] + c["comments"], 6)):
            uid = _h(network, pid, i) % 3000
            kind = ["favourite", "reblog", "mention"][_h(uid, pid, i) % 3]
            out.append({"uid": uid, "pid": pid, "kind": kind,
                        "text": f"Interesting take on {INTERESTS[uid % len(INTERESTS)]}"})
    return out


@app.get("/stats")
async def stats():
    return {**STATS, "posts": len(POSTS), "dataset_pages": len(PAGES)}


# ------------------------------------------------------------------ Telegram
@app.post("/telegram/bot{token}/{method}")
async def tg_send(token: str, method: str, request: Request):
    await _delay()
    if method not in ("sendMessage", "sendPhoto", "sendVideo"):
        return {"ok": False, "description": f"mock: unknown method {method}"}
    if (resp := _fail()) is not None:
        return resp
    mid = _new_post("telegram")
    return {"ok": True, "result": {"message_id": int(mid), "chat": {"username": "mock_channel"}}}


@app.get("/telegram/bot{token}/getUpdates")
async def tg_updates(token: str):
    await _delay()
    result = [
        {
            "update_id": int(pid),
            "message_reaction_count": {
                "message_id": int(pid),
                "reactions": [{"type": {"type": "emoji", "emoji": "👍"}, "total_count": _counts("telegram", pid)["reactions"]}],
            },
        }
        for pid in _recent("telegram")
    ]
    return {"ok": True, "result": result}


# ------------------------------------------------------------------ Mastodon
@app.post("/mastodon/api/v2/media")
async def masto_media():
    await _delay()
    return {"id": str(_h(time.time()))}


@app.post("/mastodon/api/v1/statuses")
async def masto_status(request: Request):
    await _delay()
    if (resp := _fail()) is not None:
        return resp
    body = await request.json()
    pid = _new_post("mastodon", body.get("status", ""))
    return {"id": pid, "url": f"https://mock.social/@kairo/{pid}"}


@app.get("/mastodon/api/v1/statuses/{pid}")
async def masto_get(pid: str):
    await _delay()
    c = _counts("mastodon", pid)
    return {"id": pid, "favourites_count": c["reactions"], "replies_count": c["comments"], "reblogs_count": c["shares"]}


@app.get("/mastodon/api/v1/notifications")
async def masto_notifications():
    await _delay()
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return [
        {
            "id": f"{x['pid']}-{x['uid']}",
            "type": x["kind"],
            "created_at": now,
            "account": {"acct": f"user{x['uid']}@mock.social", "display_name": f"User {x['uid']}"},
            "status": {"id": x["pid"], "content": f"<p>{x['text']}</p>"},
        }
        for x in _interactions("mastodon")
    ]


# ------------------------------------------------------------------ Bluesky
@app.post("/bluesky/xrpc/com.atproto.server.createSession")
async def bsky_session():
    await _delay()
    return {"accessJwt": "mock-jwt", "did": "did:plc:mock"}


@app.post("/bluesky/xrpc/com.atproto.repo.uploadBlob")
async def bsky_blob():
    await _delay()
    return {"blob": {"$type": "blob", "ref": {"$link": "mock"}, "mimeType": "image/jpeg", "size": 1}}


@app.post("/bluesky/xrpc/com.atproto.repo.createRecord")
async def bsky_record(request: Request):
    await _delay()
    if (resp := _fail()) is not None:
        return resp
    body = await request.json()
    rkey = _new_post("bluesky", (body.get("record") or {}).get("text", ""))
    return {"uri": f"at://did:plc:mock/app.bsky.feed.post/{rkey}", "cid": f"cid{rkey}"}


@app.get("/bluesky/xrpc/app.bsky.feed.getPosts")
async def bsky_posts(uris: str):
    await _delay()
    rkey = uris.rsplit("/", 1)[-1]
    c = _counts("bluesky", rkey)
    return {"posts": [{"uri": uris, "likeCount": c["reactions"], "replyCount": c["comments"],
                       "repostCount": c["shares"], "quoteCount": 0}]}


@app.get("/bluesky/xrpc/app.bsky.notification.listNotifications")
async def bsky_notifications():
    await _delay()
    reason = {"favourite": "like", "reblog": "repost", "mention": "reply"}
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return {
        "notifications": [
            {
                "reason": reason[x["kind"]],
                "reasonSubject": f"at://did:plc:mock/app.bsky.feed.post/{x['pid']}",
                "author": {"handle": f"user{x['uid']}.mock.bsky", "displayName": f"User {x['uid']}"},
                "indexedAt": now,
                "record": {"text": x["text"]},
            }
            for x in _interactions("bluesky")
        ]
    }


# ------------------------------------------------------------------ YouTube
@app.post("/google/token")
async def google_token():
    await _delay()
    return {"access_token": "mock-google-token", "expires_in": 3600}


@app.post("/google/upload/youtube/v3/videos")
async def yt_init(request: Request):
    await _delay()
    sid = str(_h(time.time(), random.random()))
    return JSONResponse({}, headers={"Location": f"{str(request.base_url).rstrip('/')}/google/upload/session/{sid}"})


@app.put("/google/upload/session/{sid}")
async def yt_upload(sid: str):
    await _delay()
    if (resp := _fail()) is not None:
        return resp
    return {"id": _new_post("youtube")}


@app.get("/google/youtube/v3/videos")
async def yt_stats(id: str):
    await _delay()
    c = _counts("youtube", id)
    return {"items": [{"id": id, "statistics": {"viewCount": str(c["views"]), "likeCount": str(c["reactions"]),
                                                "commentCount": str(c["comments"])}}]}


# ------------------------------------------------------------------ Facebook + Instagram (Graph API)
@app.post("/graph/{owner}/{edge}")
async def graph_post(owner: str, edge: str):
    await _delay()
    if edge in ("feed", "photos", "videos"):
        if (resp := _fail()) is not None:
            return resp
        pid = _new_post("facebook", prefix=f"{owner}_")  # id допису стрічки: {page}_{post}
        return {"id": pid, "post_id": pid} if edge == "photos" else {"id": pid}
    if edge == "media":
        return {"id": f"container{_h(owner, time.time(), random.random())}"}
    if edge == "media_publish":
        if (resp := _fail()) is not None:
            return resp
        return {"id": _new_post("instagram")}
    return JSONResponse({"error": {"message": f"mock: unknown edge {edge}"}}, status_code=400)


@app.get("/graph/{obj}/insights")
async def graph_insights(obj: str, metric: str = ""):
    await _delay()
    c = _counts("instagram", obj)
    mapping = {"views": c["views"], "saved": c["saves"], "shares": c["shares"], "reach": int(c["views"] * 0.8)}
    return {"data": [{"name": m, "period": "lifetime", "values": [{"value": mapping.get(m, 0)}]}
                     for m in metric.split(",") if m]}


@app.get("/graph/{obj}")
async def graph_get(obj: str, fields: str = ""):
    await _delay()
    if "status_code" in fields:
        return {"id": obj, "status_code": "FINISHED"}
    if "like_count" in fields:
        c = _counts("instagram", obj)
        return {"id": obj, "like_count": c["reactions"], "comments_count": c["comments"]}
    c = _counts("facebook", obj)
    return {"id": obj, "reactions": {"summary": {"total_count": c["reactions"]}},
            "comments": {"summary": {"total_count": c["comments"]}}, "shares": {"count": c["shares"]}}


# ------------------------------------------------------------------ TikTok
@app.post("/tiktok/v2/post/publish/{kind}/init/")
async def tt_init(kind: str):
    await _delay()
    if (resp := _fail()) is not None:
        return resp
    vid = _new_post("tiktok")
    return {"data": {"publish_id": f"v_pub_{vid}"}, "error": {"code": "ok", "message": ""}}


@app.post("/tiktok/v2/post/publish/status/fetch/")
async def tt_status(request: Request):
    await _delay()
    publish_id = (await request.json()).get("publish_id", "")
    return {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [publish_id.replace("v_pub_", "")]},
            "error": {"code": "ok"}}


@app.post("/tiktok/v2/video/query/")
async def tt_query(request: Request):
    await _delay()
    ids = ((await request.json()).get("filters") or {}).get("video_ids") or []
    videos = []
    for vid in ids:
        c = _counts("tiktok", vid)
        videos.append({"id": vid, "view_count": c["views"], "like_count": c["reactions"],
                       "comment_count": c["comments"], "share_count": c["shares"]})
    return {"data": {"videos": videos}, "error": {"code": "ok"}}


# ------------------------------------------------------------------ LLM (OpenAI-сумісний)
@app.get("/llm/v1/models")
async def llm_models():
    return {"data": [{"id": "mock-llm"}, {"id": "mock-embed"}]}


@app.post("/llm/v1/chat/completions")
async def llm_chat(request: Request):
    body = await request.json()
    STATS["llm_calls"] += 1
    if (body.get("max_tokens") or 0) <= 8:  # перевірка готовності моделі
        await _delay()
        content = "ok"
    else:
        await _delay(LLM_LATENCY)
        system = (body.get("messages") or [{}])[0].get("content", "")
        if '"topics"' in system:
            content = json.dumps({"topics": [f"Topic {i + 1}: a practical breakdown" for i in range(8)]}, ensure_ascii=False)
        else:
            n = _h(time.time(), random.random()) % 1000
            content = json.dumps(
                {
                    "title": f"Mock post #{n}",
                    "body": "Test text generated by the mock LLM provider for a load-test run.",
                    "hashtags": ["kairo", "loadtest"],
                    "media_suggestion": "Minimal flat illustration of a content pipeline",
                },
                ensure_ascii=False,
            )
    return {
        "id": "mock-completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"total_tokens": 420},
    }


@app.post("/llm/v1/embeddings")
async def llm_embeddings(request: Request):
    await _delay()
    text = str((await request.json()).get("input", ""))
    rng = random.Random(_h(text))
    vec = [rng.gauss(0, 1) for _ in range(768)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return {"data": [{"embedding": [v / norm for v in vec]}]}
