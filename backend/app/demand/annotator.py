"""
Анотатор згадок: локальна модель як вимірювальний прилад.

Модель тут нічого не прогнозує — вона перетворює вільний текст у структуру.
Саме це перетворення й дає головну ознаку: не «скільки дописів на добу»,
а «скільки людей на якій стадії шляху до покупки».

Сходинка стадій розширена на вимогу замовника: людина, яка написала «куплю»,
вже в воронці, і не враховувати її — означає занижувати попит. Але з цього
випливає й зворотне — «передумав» мусить попит віднімати, інакше індекс
уміє тільки зростати.

Окремо розведено «отримаю / чекаю доставку»: це не намір, а вже здійснена
покупка, товар просто в дорозі. Якщо змішати їх із «хочу», сходинка перестає
бути монотонною.

Дописи йдуть пачками: виклик на кожен окремо при 27B-моделі означав би
десятки годин на корпус.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.core.config import settings
from app.generation.llm_client import MultiBackendLLMClient

logger = logging.getLogger("kairo.demand.annotator")

# Порядок важливий: індекс у списку є порядковим номером стадії, і саме за ним
# перевіряється монотонність шляху автора.
STAGES = (
    "none",           # товар згадано, але не як предмет споживання
    "awareness",      # «бачив», «що це взагалі»
    "interest",       # «хочу», «гарний», «мрію»
    "consideration",  # «думаю між X і Y», «варто брати?»
    "intent",         # «куплю», «замовлю цього тижня»
    "ordered",        # «замовив», «чекаю доставку», «отримаю у вівторок»
    "received",       # «прийшло», «розпакував»
    "usage",          # «місяць користуюсь»
    "repurchase",     # «беру другий», «закінчився»
)
# Стадія поза сходинкою: вихід із воронки, віднімається від індексу.
REJECTION = "rejection"
ALL_STAGES = STAGES + (REJECTION,)

# Стадії, де транзакція вже відбулась. Використовується для виявлення
# конверсії у траєкторії автора: «хочу» -> «місяць користуюсь» означає, що
# людина таки купила.
TRANSACTED = ("ordered", "received", "usage", "repurchase")

# Свіжа покупка — лише ці стадії дають одиницю в індекс попиту. «Користуюсь»
# сюди не входить: це слід минулої покупки, а не сьогоднішнього попиту.
# З вагою одиниця кожен наявний власник роздував би індекс накопиченою базою.
FRESH_PURCHASE = ("ordered", "received", "repurchase")

SCARCITY = ("none", "in_stock", "sold_out", "waiting", "restock", "scalped")
FORMATS = ("review", "unboxing", "question", "complaint", "news", "showcase", "other")

ANNOTATION_SCHEMA: Dict[str, Any] = {
    "name": "mention_annotations",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "relevant": {"type": "boolean"},
                        "stage": {"type": "string", "enum": list(ALL_STAGES)},
                        "scarcity": {"type": "string", "enum": list(SCARCITY)},
                        "sentiment": {"type": "number"},
                        "format": {"type": "string", "enum": list(FORMATS)},
                        "is_ad": {"type": "boolean"},
                    },
                    "required": ["i", "relevant", "stage", "scarcity",
                                 "sentiment", "format", "is_ad"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You label social media posts about a consumer product.
For each numbered post return one object. Do not invent posts.

stage — the author's position on the path to purchase:
  none          the author states no personal position: company news, market
                commentary, third-party observation about availability or prices
  awareness     just noticed it exists
  interest      wants it, admires it, no decision yet
  consideration weighing options, comparing, asking whether to buy
  intent        has decided to buy, purchase has NOT happened yet
  ordered       already paid or ordered; waiting for delivery ("will get it Tuesday")
  received      it arrived, unboxing
  usage         owns it and is using it
  repurchase    buying another one, or replacing a consumed one
  rejection     decided against it, cancelled, returned, sold it

Critical: "I will get it on Tuesday" means it is already ordered, not intent.
"I want one" is interest. "I'm buying it next week" is intent.

scarcity — exactly one of: none, in_stock, sold_out, waiting, restock, scalped.
Use it whenever availability is mentioned, including in posts whose stage is none:
"sold out everywhere, scalpers asking 2x" is stage=none, scarcity=scalped.
sentiment — number from -1.0 to 1.0.
is_ad — true for marketing, affiliate or deal-aggregator posts.

Answer with JSON only. Keep reasoning to a minimum; the labels are short."""


@dataclass
class Annotation:
    index: int
    relevant: bool
    stage: str
    scarcity: str
    sentiment: float
    post_format: str
    is_ad: bool


def stage_rank(stage: str) -> int:
    """Порядковий номер стадії; відмова має ранг -1 — це вихід, а не крок."""
    if stage == REJECTION:
        return -1
    return STAGES.index(stage) if stage in STAGES else 0


def is_transacted(stage: str) -> bool:
    return stage in TRANSACTED


def _extract_items(text: str) -> List[Dict[str, Any]]:
    """
    Дістає список позначок із відповіді.

    Moonshot приймає `response_format: json_schema`, але не застосовує його:
    модель повертає голий масив замість об'єкта з полем items. Тому розбір
    приймає обидві форми, а не наполягає на схемі — покладатись на неї як на
    гарантію тут не можна.
    """
    def _pick(obj: Any) -> List[Dict[str, Any]]:
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            for key in ("items", "data", "results", "annotations"):
                if isinstance(obj.get(key), list):
                    return [x for x in obj[key] if isinstance(x, dict)]
            if "stage" in obj:          # одна позначка без обгортки
                return [obj]
            # Словник, ключем якого є номер допису: {"0": {...}, "1": {...}}.
            # Саме так відповідають kimi-k3 і llama-3.3, причому поля `i`
            # усередині може не бути зовсім. Номер беремо з ключа.
            numbered = []
            for key, val in obj.items():
                if not isinstance(val, dict):
                    continue
                try:
                    idx = int(key)
                except (TypeError, ValueError):
                    continue
                numbered.append({**val, "i": val.get("i", idx)})
            if numbered:
                return numbered
        return []

    for candidate in (text, ):
        try:
            got = _pick(json.loads(candidate))
            if got:
                return got
        except (json.JSONDecodeError, TypeError):
            pass
    # Модель іноді обрамлює відповідь текстом або ```json попри інструкцію.
    for pattern in (r"\[.*\]", r"\{.*\}"):
        match = re.search(pattern, text or "", re.DOTALL)
        if match:
            try:
                got = _pick(json.loads(match.group(0)))
                if got:
                    return got
            except json.JSONDecodeError:
                continue
    return []


class MentionAnnotator:
    """
    `backend="local"` — LM Studio на цій машині.
    `backend="kimi"`  — Moonshot напряму.

    Розмітка корпусу — це десятки тисяч викликів, і тримати під ними локальну
    27B-модель означає зайняти відеокарту на добу. Вибір бекенда винесено в
    параметр саме тому, що це рішення про ресурси, а не про якість.
    """

    def __init__(self, batch_size: int = 10, temperature: float = 0.0,
                 backend: str = "local"):
        self.client = MultiBackendLLMClient()
        self.batch_size = batch_size
        self.temperature = temperature
        self.backend = backend
        # Розмітка платна, і рахунок треба бачити під час прогону, а не після.
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.failures = 0
        if backend == "kimi":
            self.base_url = settings.KIMI_BASE_URL
            self.api_key = settings.KIMI_API_KEY or ""
            self.model_name = settings.KIMI_MODEL_NAME
            # Moonshot відхиляє будь-яку температуру крім одиниці. Для розмітки
            # це означає, що відтворюваність доведеться забезпечувати схемою
            # відповіді та перевіркою, а не нульовою температурою.
            self.temperature = 1.0
            self.max_tokens = 32000
            # Модель із міркуванням відповідає десятки секунд, а під
            # паралельним навантаженням — довше: Moonshot ставить виклики в
            # чергу, і затримка росте разом із паралельністю. На 180 с зривалась
            # кожна пачка, на 420 с при восьми потоках — кожна пʼята.
            # Розумний режим: паралельність 4-5 і щедрий потолок.
            self.timeout = 900.0
        else:
            self.base_url = self.client.local_base_url
            self.api_key = self.client.local_api_key
            self.model_name = self.client.local_model
            self.max_tokens = 4000
            self.timeout = 180.0

    def _build_prompt(self, product_name: str, texts: Sequence[str]) -> str:
        lines = [f"Product: {product_name}", "", "Posts:"]
        for i, text in enumerate(texts):
            flat = re.sub(r"\s+", " ", text).strip()[:400]
            lines.append(f"[{i}] {flat}")
        lines.append("")
        lines.append(f"Return exactly {len(texts)} objects, one per post, field i = post number.")
        return "\n".join(lines)

    async def annotate_batch(
        self, product_name: str, texts: Sequence[str], timeout: Optional[float] = None
    ) -> List[Annotation]:
        """
        Ніколи не кидає виняток: розмітка йде сотнями пачок, і обрив однієї
        не має валити прогін. Невдала пачка повертає порожній список і
        рахується у `failures` — її просто розмітять наступного разу.
        """
        if not texts:
            return []
        raw = None
        _data: Dict[str, Any] = {}
        for attempt in range(2):
            try:
                raw, _latency, _data = await self.client._call_openai_compatible(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model_name,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=self._build_prompt(product_name, texts),
                    temperature=self.temperature,
                    timeout=timeout or self.timeout,
                    response_format={"type": "json_schema", "json_schema": ANNOTATION_SCHEMA},
            # Kimi — модель із міркуванням: на бюджеті 2500 усі токени йшли в
            # reasoning_content, а content лишався порожнім (finish_reason=length).
                    max_tokens=self.max_tokens,
                )
                break
            except Exception as exc:
                if attempt == 1:
                    self.calls += 1
                    self.failures += 1
                    logger.warning("Batch failed (%s), skipping", type(exc).__name__)
                    return []
                await asyncio.sleep(5)
        self.calls += 1
        usage = (_data or {}).get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        if not raw:
            self.failures += 1
            logger.warning("Empty model response for a batch of %d posts", len(texts))
            return []
        items = _extract_items(raw)
        if not items:
            self.failures += 1
            logger.warning("Could not parse the model response: %s", (raw or "")[:160])
            return []

        out: List[Annotation] = []
        for item in items:
            try:
                idx = int(item["i"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 <= idx < len(texts):
                continue
            stage = item.get("stage") or "none"
            if stage not in ALL_STAGES:
                stage = "none"
            scarcity = item.get("scarcity") or "none"
            if scarcity not in SCARCITY:
                scarcity = "none"
            fmt = item.get("format") or "other"
            if fmt not in FORMATS:
                fmt = "other"
            try:
                sentiment = max(-1.0, min(1.0, float(item.get("sentiment", 0.0))))
            except (TypeError, ValueError):
                sentiment = 0.0
            out.append(Annotation(
                index=idx,
                relevant=bool(item.get("relevant", True)),
                stage=stage,
                scarcity=scarcity,
                sentiment=sentiment,
                post_format=fmt,
                is_ad=bool(item.get("is_ad", False)),
            ))
        return out
