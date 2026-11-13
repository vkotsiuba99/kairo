"""
Дешева стратифікація корпусу перед розміткою.

Замір показав, що 97% дописів — це `usage` («знято на мою камеру») і `none`
(новини, стороння балаканина). Ганяти їх крізь мовну модель означає платити
за передбачуваний результат.

Але просто викинути їх не можна: частки стадій рахуються саме з випадкової
вибірки, і якщо вирізати з неї найчастіший клас, решта задереться в рази.
Тому фільтр нічого не викидає — він ділить корпус на страти. З кожної
береться своя частка, а при агрегації кожна згадка входить із вагою,
оберненою до ймовірності її відбору. Так економія не псує оцінок.

Правило навмисно зміщене в бік повноти, а не точності: пропустити сигнальний
допис у масову страту — втратити його назавжди, а зайвий `usage` у сигнальній
страті коштує лише одного виклику моделі.

────────────────────────────────────────────────────────────────────────────
СТАН: НЕ ПІДКЛЮЧЕНО ДО КОНВЕЄРА. Перевірка на 1114 розмічених згадках:

    повнота за сигнальними стадіями     34%
    interest                            12%
    repurchase                           0%

Правила ловлять третину. Причина видна на пропущених дописах: половина з них
не англійською («Ich will mehr eine gebrauchte Kamera anschaffen», «欲しい»,
«estou querendo comprar»), а решта — ідіоми, які регулярним виразом не
описати: «it's time to cheat in our relationship» як відмова, «one of my most
painful regrets» як інтерес.

Смисловий варіант (символьні n-грами TF-IDF + логістична регресія, п'ять
фолдів) кращий, але теж не окупається: при повноті 90% пропускає 45.5%
корпусу, тобто економить лише 2.2 рази. Причина — на навчанні лише 39
сигнальних прикладів із 1114.

Обидва варіанти стануть придатними після накопичення розмітки: це класичний
bootstrapping, де кожна ітерація дає більше рідкісних прикладів і дозволяє
підняти поріг. Поки цього немає, застосовувати фільтр означає тихо втратити
дві третини найцінніших стадій.

І головне обмеження, яке не зникне ніколи: до ВИПАДКОВОЇ вибірки фільтр
незастосовний узагалі. Саме з неї беруться частки стадій, і будь-яке
просіювання їх зміщує. Фільтр — інструмент масової розмітки, не замір.
────────────────────────────────────────────────────────────────────────────
"""
import re
from typing import Dict, List, Tuple

# Страти.
SIGNAL = "signal_likely"   # можливо, стадія попиту — віддаємо моделі щедро
BULK = "bulk"              # майже напевно usage або none — беремо рідко

# Дієслова й звороти шляху до покупки. Свідомо широкі: краще зайвий виклик,
# ніж загублена рідкісна стадія.
_SIGNAL_PATTERNS: Tuple[str, ...] = (
    # намір і бажання
    r"\bi\s+want\b", r"\bwant\s+(?:one|this|a|an)\b", r"\bwish\s+i\s+had\b",
    r"\btempted\b", r"\bgas\b(?:\s|$)", r"\bsaving\s+up\b", r"\bon\s+my\s+wishlist\b",
    r"\bgonna\s+(?:buy|get|grab|order)\b", r"\bgoing\s+to\s+(?:buy|get|order)\b",
    r"\bi'?ll\s+(?:buy|get|order|grab)\b", r"\bdecided\s+to\s+(?:buy|get)\b",
    # розгляд і порівняння
    r"\bworth\s+it\b", r"\bshould\s+i\b", r"\bthinking\s+(?:about|of)\s+(?:buying|getting)\b",
    r"\bvs\.?\b", r"\bor\s+the\b.*\?", r"\brecommend\b", r"\badvice\b",
    r"\bwhich\s+(?:one|should)\b", r"\bany\s+(?:good|thoughts)\b",
    # замовлення й доставка
    r"\bordered\b", r"\bpre-?order\b", r"\bon\s+(?:its|the)\s+way\b",
    r"\bshipped\b", r"\btracking\b", r"\barriv(?:es|ing|ed)\b", r"\bdeliver(?:y|ed)\b",
    r"\bwaiting\s+for\b", r"\bshould\s+(?:get|have)\s+it\b",
    # отримання
    r"\bjust\s+(?:got|bought|picked\s+up|received)\b", r"\bfinally\s+(?:got|arrived|here)\b",
    r"\bunbox", r"\bnew\s+to\s+me\b", r"\bfirst\s+(?:impressions|shots|day)\b",
    r"\bcame\s+in\s+today\b", r"\bit'?s\s+here\b",
    # повторна покупка й вихід
    r"\bsecond\s+one\b", r"\banother\s+one\b", r"\breplac(?:e|ing|ed)\b",
    r"\bupgrad(?:e|ing|ed)\s+(?:from|to)\b",
    r"\bcancel(?:led|ling)?\b", r"\breturn(?:ed|ing)\b", r"\bsold\s+my\b",
    r"\bregret\b", r"\bchanged\s+my\s+mind\b", r"\bwent\s+with\b",
    # дефіцит і ціна — сильні маркери попиту
    r"\bsold\s+out\b", r"\bout\s+of\s+stock\b", r"\bin\s+stock\b", r"\brestock\b",
    r"\bback\s+in\s+stock\b", r"\bscalper", r"\bwaitlist\b", r"\bbacko?rder\b",
    r"\bhow\s+much\b", r"\bprice\b", r"\bdeal\b", r"\bdiscount\b",
    r"\bwhere\s+to\s+buy\b", r"\blink\b\?",
)

_SIGNAL_RE = [re.compile(p, re.IGNORECASE) for p in _SIGNAL_PATTERNS]

# Питання майже завжди означає розгляд, і коштує це одного символа.
_QUESTION_RE = re.compile(r"\?")


def stratum(text: str) -> str:
    if not text:
        return BULK
    if any(r.search(text) for r in _SIGNAL_RE):
        return SIGNAL
    if _QUESTION_RE.search(text):
        return SIGNAL
    return BULK


def stratify(texts: List[str]) -> Dict[str, List[int]]:
    """Повертає індекси текстів за стратами."""
    out: Dict[str, List[int]] = {SIGNAL: [], BULK: []}
    for i, text in enumerate(texts):
        out[stratum(text)].append(i)
    return out
