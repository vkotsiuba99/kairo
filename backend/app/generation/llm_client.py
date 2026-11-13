import json
import time
import logging
from typing import Dict, Any, List, Tuple, Optional
import httpx
from app.core.config import settings
from app.generation.schemas import GeneratedContentSchema, CONTENT_JSON_SCHEMA, strip_emoji
from app.generation.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger("kairo.llm")

# Запобіжник локального бекенду.
#
# Коли LM Studio не може підняти жодну модель, кожна спроба коштує хвилини:
# завантаження 27B-чекпоінта не переривається швидко. Після невдачі локальний
# тир вимикається на кілька хвилин, і запити одразу йдуть у резерв — користувач
# отримує матеріал за секунди, а не чекає п’ять хвилин на таймаути. Щойно
# вікно спливає, локальна модель пробується знову без жодних ручних дій.
_LOCAL_BREAKER: Dict[str, Any] = {"open_until": 0.0, "reason": ""}
LOCAL_BREAKER_COOLDOWN_SECONDS = 300.0


def local_backend_state() -> Dict[str, Any]:
    """Чи вимкнений зараз локальний бекенд запобіжником і чому."""
    remaining = _LOCAL_BREAKER["open_until"] - time.time()
    return {
        "disabled": remaining > 0,
        "seconds_left": int(max(0, remaining)),
        "reason": _LOCAL_BREAKER["reason"],
    }

TOPICS_JSON_SCHEMA = {
    "name": "topic_suggestions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "topics": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["topics"],
        "additionalProperties": False,
    },
}


class GenerationResult:
    def __init__(
        self,
        content: GeneratedContentSchema,
        backend_used: str,
        model_name: str,
        latency_ms: int,
        cost_usd: float,
        raw_response: Dict[str, Any],
    ):
        self.content = content
        self.backend_used = backend_used
        self.model_name = model_name
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd
        self.raw_response = raw_response


class MultiBackendLLMClient:
    """
    Unified client for LLM generation:
    - Primary: LM Studio (Ternary Bonsai 27B) at LLM_BASE_URL
    - Cloud Reserve: OpenRouter (moonshotai/kimi-k2.6) at OPENROUTER_BASE_URL
    - Offline fallback: Deterministic generator when no LLM service is active
    """

    def __init__(self):
        self.local_base_url = settings.LLM_BASE_URL
        self.local_api_key = settings.LLM_API_KEY
        self.local_model = settings.LLM_MODEL_NAME

        self.cloud_base_url = settings.OPENROUTER_BASE_URL
        self.cloud_api_key = settings.OPENROUTER_API_KEY
        self.cloud_model = settings.OPENROUTER_MODEL_NAME

    async def _call_openai_compatible(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        timeout: float = 40.0,
        response_format: Optional[Dict[str, Any]] = None,
        _allow_format_downgrade: bool = True,
        max_tokens: int = 2500,
    ) -> Tuple[Optional[str], int, Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {api_key or 'test'}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": f"{system_prompt}\nCRITICAL: Respond ONLY with valid JSON. No chain of thought, no conversational filler.",
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format or {"type": "json_object"},
        }

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if resp.is_success:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    raw_text = msg.get("content")
                    if raw_text and raw_text.strip():
                        return raw_text, latency_ms, data
                    # Fallback: if model put output into reasoning or reasoning_content
                    reasoning_text = msg.get("reasoning") or msg.get("reasoning_content")
                    if reasoning_text and "{" in reasoning_text:
                        return reasoning_text, latency_ms, data
                logger.warning("LLM call succeeded (%s) but empty content returned: %s", model, data)
                return None, latency_ms, data
            else:
                body = resp.text or ""
                # Різні збірки LM Studio приймають різні режими структурованого
                # виводу: одні лише json_schema, інші лише json_object. Замість
                # того щоб падати, повторюємо запит у простому текстовому режимі —
                # JSON усе одно витягується парсером із тіла відповіді.
                if (
                    _allow_format_downgrade
                    and resp.status_code == 400
                    and "response_format" in body
                ):
                    logger.info(
                        "Model %s does not accept this structured-output mode — retrying as plain text",
                        model,
                    )
                    return await self._call_openai_compatible(
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        temperature=temperature,
                        timeout=timeout,
                        response_format={"type": "text"},
                        _allow_format_downgrade=False,
                    )
                logger.warning("LLM call failed for model %s (%s): %s", model, resp.status_code, body)
                return None, latency_ms, {"error": body, "status_code": resp.status_code}

    async def _local_model_candidates(self) -> List[str]:
        """
        Перелік локальних моделей у порядку спроби.

        Перша — та, що вказана в конфігурації. Далі йдуть інші завантажені в
        LM Studio чат-моделі: якщо основний чекпоінт не піднімається (типова
        ситуація, коли відеопам’ять зайнята іншим процесом), система не падає,
        а бере те, що реально доступне, і чесно записує в завдання, якою саме
        моделлю згенеровано матеріал.
        """
        candidates = [self.local_model] if self.local_model else []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.local_base_url.rstrip('/')}/models")
            if resp.is_success:
                for item in resp.json().get("data", []):
                    model_id = item.get("id", "")
                    # embedding-моделі не вміють chat/completions
                    if not model_id or "embed" in model_id.lower():
                        continue
                    if model_id not in candidates:
                        candidates.append(model_id)
        except Exception as e:
            logger.info("Could not list local models: %s", e)
        # Обмежуємо перебір: кожна невдала спроба завантажити 27B-чекпоінт
        # коштує десятки секунд, і нескінченний перебір гірший за швидкий
        # перехід до резервного бекенду.
        return candidates[:2]

    async def _local_model_ready(self, model: str) -> bool:
        """
        Коротка перевірка, чи здатна модель відповісти взагалі.

        Мертвий локальний бекенд інакше з’їдає повний таймаут генерації на
        кожного кандидата — кілька хвилин очікування там, де достатньо
        секунд, щоб зрозуміти: чекпоінт не піднімається.
        """
        try:
            async with httpx.AsyncClient(timeout=settings.LLM_PREFLIGHT_SECONDS) as client:
                resp = await client.post(
                    f"{self.local_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.local_api_key or 'test'}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 4,
                    },
                )
            if resp.is_success:
                return True
            logger.info("Model %s failed the readiness check: %s", model, resp.text[:160])
        except Exception as e:
            logger.info("Model %s did not answer the readiness check: %s", model, e or "timeout")
        return False

    async def suggest_topics(
        self,
        audience_description: str,
        interests: List[str],
        language: str = "uk",
        count: int = 8,
    ) -> List[str]:
        """
        Пропонує теми під конкретний портрет аудиторії.

        Набір тем не зашитий у код: він виводиться з опису сегмента, тому для
        студентів, підприємців чи нічної аудиторії пропозиції будуть різними.
        Якщо модель недоступна, повертається детермінований набір із самих
        інтересів сегмента — форма інтерфейсу лишається робочою.
        """
        system_prompt = (
            "You are a content-plan editor. Suggest specific post topics for the given "
            "target audience in English. Topics must be concrete (not 'about technology' but "
            "a specific question or claim), vary in angle, contain no emoji and be at most "
            '90 characters each. Return JSON like {"topics": ["topic 1", "topic 2"]}.'
        )
        user_prompt = (
            f"Target audience: {audience_description or 'a broad technical audience'}.\n"
            f"Number of topics: {count}."
        )

        raw = None
        candidates = [] if local_backend_state()["disabled"] else await self._local_model_candidates()
        for candidate in candidates:
            if not await self._local_model_ready(candidate):
                continue
            try:
                raw, _latency, _meta = await self._call_openai_compatible(
                    base_url=self.local_base_url,
                    api_key=self.local_api_key,
                    model=candidate,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.9,
                    timeout=min(settings.LLM_TIMEOUT_SECONDS, 120.0),
                    response_format={"type": "json_schema", "json_schema": TOPICS_JSON_SCHEMA},
                )
            except Exception as e:
                logger.info("Local model %s did not answer the topic request: %s", candidate, e)
                raw = None
            if raw:
                break

        if candidates and not raw:
            # Той самий запобіжник, що й у генерації: якщо локальний бекенд не
            # відповів, наступні запити не мають знову чекати на нього.
            _LOCAL_BREAKER["open_until"] = time.time() + LOCAL_BREAKER_COOLDOWN_SECONDS
            _LOCAL_BREAKER["reason"] = "local model did not answer the topic request"

        if raw:
            try:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("```")[1]
                    cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
                data = json.loads(cleaned[cleaned.index("{"): cleaned.rindex("}") + 1])
                topics = data.get("topics") or data.get("themes") or []
                topics = [strip_emoji(str(t)).strip() for t in topics if str(t).strip()]
                if topics:
                    return topics[:count]
            except Exception as e:
                logger.warning("Could not parse the topics response: %s", e)

        return self._fallback_topics(interests, count)

    @staticmethod
    def _fallback_topics(interests: List[str], count: int) -> List[str]:
        """
        Запасний набір тем, коли модель недоступна.

        Кути подачі навмисно нейтральні: той самий перелік має однаково
        працювати і для розробки, і для риболовлі чи бойових мистецтв.
        Назви інтересів беруться з єдиного довідника, щоб у формі й у темах
        не розходилися формулювання.
        """
        from app.audience.profiles import INTERESTS

        angles = [
            "common beginner mistakes",
            "where to start",
            "what changed this year",
            "myths people still believe",
            "underrated techniques",
            "what to try this week",
            "how not to lose motivation",
            "a story few people know",
        ]

        chosen = [INTERESTS.get(i, i) for i in (interests or ["dev"])]
        out: List[str] = []
        seen = set()

        # Спершу проходимо всі інтереси з одним кутом, потім міняємо кут —
        # так у видачі не домінує один інтерес.
        for i in range(count):
            topic = chosen[i % len(chosen)]
            angle = angles[(i // len(chosen)) % len(angles)]
            line = f"{topic[:1].upper()}{topic[1:]}: {angle}"
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
            if len(out) >= count:
                return out

        # Якщо комбінацій усе ще замало — добираємо рештою пар без повторів
        for angle in angles:
            for topic in chosen:
                line = f"{topic[:1].upper()}{topic[1:]}: {angle}"
                if line not in seen:
                    seen.add(line)
                    out.append(line)
                if len(out) >= count:
                    return out
        return out

    def _parse_content(self, text: str) -> Optional[GeneratedContentSchema]:
        try:
            # Clean possible markdown wrapping ```json ... ```
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "body" in parsed and isinstance(parsed["body"], str):
                parsed["body"] = (
                    parsed["body"]
                    .replace("<br>", "\n")
                    .replace("<br/>", "\n")
                    .replace("<br />", "\n")
                )
            return GeneratedContentSchema.model_validate(parsed)
        except Exception:
            # Fallback: attempt to find outermost JSON object {...}
            try:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    sub = text[start : end + 1]
                    parsed = json.loads(sub)
                    if isinstance(parsed, dict) and "body" in parsed and isinstance(parsed["body"], str):
                        parsed["body"] = (
                            parsed["body"]
                            .replace("<br>", "\n")
                            .replace("<br/>", "\n")
                            .replace("<br />", "\n")
                        )
                    return GeneratedContentSchema.model_validate(parsed)
            except Exception as e:
                logger.warning("Failed to parse structured JSON: %s. Raw text snippet: %s", e, text[:150])
            return None

    def _offline_fallback_generation(
        self,
        platform_code: str,
        topic: str,
        format_type: str,
        tone: str,
        length_bucket: str,
    ) -> GeneratedContentSchema:
        """
        Deterministic offline fallback that still produces a readable English post.
        Used when neither the local model nor the cloud fallback is reachable
        (tests, offline demos).
        """
        drafts = {
            "ai_breakthroughs": (
                "Small open models now beat last year's giants on code review",
                "We replaced a 70B cloud model with an 8B local one for pull-request review "
                "and kept 92% of the useful comments while cutting latency from 14 s to 3 s.",
                ["Quantize to 4-bit first: the quality loss was under 2% on our review set",
                 "Keep a cloud fallback for diffs longer than 2,000 lines",
                 "Measure on your own repositories, not on public benchmarks"],
            ),
            "developer_humor": (
                "The Friday deploy that taught us to love feature flags",
                "At 17:58 a one-line config change took down checkout for 40 minutes. "
                "The fix was not heroics but a boring switch we now flip before every release.",
                ["Every risky change ships behind a flag that is off by default",
                 "Rollback is a button, not a meeting",
                 "Friday deploys are fine when undo takes ten seconds"],
            ),
            "open_source_tools": (
                "Five open-source CLI tools that removed an hour of toil a week",
                "We audited our team's terminal history for a month and replaced the slowest "
                "manual steps with tools that are free and take minutes to install.",
                ["ripgrep and fd instead of grep and find: 5–10x faster on a monorepo",
                 "just as a readable task runner instead of a 600-line Makefile",
                 "k9s for day-to-day Kubernetes debugging without long kubectl commands"],
            ),
            "tech_career_tips": (
                "What actually changed when I moved from Senior to Staff engineer",
                "The job stopped being about the hardest ticket and became about the decisions "
                "nobody owned: migrations, interfaces between teams and saying no early.",
                ["Write one design doc a month, even a two-page one",
                 "Track decisions you unblocked, not lines of code",
                 "Find a sponsor, not only a mentor"],
            ),
            "system_architecture": (
                "Designing for failure: what a 2 a.m. outage taught us about queues",
                "A single slow consumer filled our queue and took the API down with it. "
                "Back-pressure, idempotent handlers and retries with jitter fixed it for good.",
                ["Bound every queue and decide what happens when it is full",
                 "Make handlers idempotent so retries are safe",
                 "Add jitter to retries to avoid thundering herds"],
            ),
            "indie_games": (
                "Three indie heroes worth meeting this winter",
                "The most talked-about releases of the season came from studios of fewer than ten people, "
                "and each of them builds its whole world around one memorable character.",
                ["A pixel-art knight crossing a lantern-lit cave kingdom: tight combat, zero tutorials",
                 "A fox courier delivering letters around a cozy island town: twenty-minute sessions, no grind",
                 "A small maintenance robot repairing a drifting space station: puzzles told without words"],
            ),
        }
        media = {
            "indie_games": "Three original indie game heroes side by side: a small pixel-art knight, "
                           "a fox courier with a satchel and a round maintenance robot",
        }
        key = topic.strip().lower().replace(" ", "_")  # тема з поля вводу: "Indie games" -> indie_games
        title, lead, points = drafts.get(
            key, (f"Notes on {topic.replace('_', ' ')}", "A short practical note.", ["Start small", "Measure", "Iterate"])
        )

        if platform_code in ("bluesky", "tiktok"):
            body = f"{lead.split('. ')[0]}. {points[0]}."
        elif platform_code in ("mastodon", "instagram"):
            body = f"{lead}\n\n- {points[0]}\n- {points[1]}"
        else:
            bullets = "\n".join(f"- {p}" for p in points)
            body = (
                f"{lead}\n\n{bullets}\n\n"
                "The trade-off: none of this is free — each change took a few days to roll out "
                "and needed someone to own it.\n\nWhat would you try first?"
            )

        hashtags = [key, "tech", "development", "ai", "kairo"]
        media_prompt = media.get(key) or f"Futuristic cybernetic illustration representing {topic.replace('_', ' ')}, cinematic lighting, 8k resolution, octane render"

        return GeneratedContentSchema(
            title=title,
            body=body,
            hashtags=hashtags,
            media_suggestion=media_prompt,
        )

    async def generate_content(
        self,
        platform_code: str,
        topic: str,
        format_type: str,
        tone: str,
        length_bucket: str,
        backend_preference: str = "local",
        audience_interests: str = "",
        language: str = "en",
    ) -> GenerationResult:
        system_prompt = build_system_prompt(platform_code, language)
        user_prompt = build_user_prompt(topic, format_type, tone, length_bucket, audience_interests)

        # 1. Preferred route: Local LM Studio (Ternary Bonsai 27B)
        # Таймаут береться з конфігурації: локальна 27B-модель пише відповідь
        # десятки секунд, і надто короткий ліміт мовчки відправляв би кожне
        # завдання у хмару, попри вибраний користувачем локальний бекенд.
        skip_local = local_backend_state()["disabled"]
        if backend_preference == "local" and skip_local:
            logger.info(
                "Local backend temporarily disabled by the circuit breaker (%s) — using the fallback",
                _LOCAL_BREAKER["reason"],
            )

        if backend_preference == "local" and not skip_local:
            for candidate in await self._local_model_candidates():
                if not await self._local_model_ready(candidate):
                    continue
                try:
                    raw_text, latency, resp_data = await self._call_openai_compatible(
                        base_url=self.local_base_url,
                        api_key=self.local_api_key,
                        model=candidate,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        timeout=settings.LLM_TIMEOUT_SECONDS,
                        response_format={
                            "type": "json_schema",
                            "json_schema": CONTENT_JSON_SCHEMA,
                        },
                    )
                    if raw_text:
                        parsed = self._parse_content(raw_text)
                        if parsed:
                            if candidate != self.local_model:
                                logger.warning(
                                    "Primary local model %s unavailable — generated with %s",
                                    self.local_model,
                                    candidate,
                                )
                            logger.info("Local generation succeeded: %s in %d ms", candidate, latency)
                            _LOCAL_BREAKER["open_until"] = 0.0
                            _LOCAL_BREAKER["reason"] = ""
                            return GenerationResult(
                                content=parsed,
                                backend_used="local",
                                model_name=candidate,
                                latency_ms=latency,
                                cost_usd=0.0,
                                raw_response=resp_data,
                            )
                    logger.warning("Local model %s returned no usable answer", candidate)
                except Exception as e:
                    logger.warning("Local model %s unavailable: %s", candidate, e)
            _LOCAL_BREAKER["open_until"] = time.time() + LOCAL_BREAKER_COOLDOWN_SECONDS
            _LOCAL_BREAKER["reason"] = "no local model answered"
            logger.warning(
                "No local model answered — switching to the fallback and disabling "
                "the local tier for %d s",
                int(LOCAL_BREAKER_COOLDOWN_SECONDS),
            )

        # 2. Cloud Tier: OpenRouter (with candidate fallback)
        if self.cloud_api_key:
            cloud_candidates = [self.cloud_model]
            for fb in ["meta-llama/llama-3.3-70b-instruct", "deepseek/deepseek-chat"]:
                if fb not in cloud_candidates:
                    cloud_candidates.append(fb)

            for c_model in cloud_candidates:
                try:
                    logger.info("Attempting Cloud generation with model: %s", c_model)
                    raw_text, latency, resp_data = await self._call_openai_compatible(
                        base_url=self.cloud_base_url,
                        api_key=self.cloud_api_key,
                        model=c_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        timeout=30.0,
                    )
                    if raw_text:
                        parsed = self._parse_content(raw_text)
                        if parsed:
                            tokens = resp_data.get("usage", {}).get("total_tokens", 500)
                            cost_usd = (tokens / 1_000_000) * 0.60
                            logger.info("Cloud generation succeeded with model: %s (latency: %dms)", c_model, latency)
                            return GenerationResult(
                                content=parsed,
                                backend_used="cloud",
                                model_name=c_model,
                                latency_ms=latency,
                                cost_usd=cost_usd,
                                raw_response=resp_data,
                            )
                except Exception as e:
                    logger.warning("Cloud OpenRouter call error with model %s: %s", c_model, e)

        # 3. Deterministic offline fallback (reproducible for tests & offline run)
        content = self._offline_fallback_generation(platform_code, topic, format_type, tone, length_bucket)
        return GenerationResult(
            content=content,
            backend_used="offline_fallback",
            model_name="deterministic_rule_engine",
            latency_ms=12,
            cost_usd=0.0,
            raw_response={"mode": "fallback"},
        )
