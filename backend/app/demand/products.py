"""
Реєстр товарів і лексиконів для збору згадок.

Лексикон — не одне ключове слово, а чотири набори правил:
  include      — що вважаємо згадкою;
  require_any  — дизамбігуація: для неоднозначних термінів потрібен контекст;
  exclude      — омоніми, які треба відкинути;
  sku          — розкладка родини товарів на конкретні моделі.

Заміри показали, що SKU-запит («x100vi») дає 5.5 допису/добу, а бренд
(«fujifilm») — 84. Тому збираємо на рівні родини, а до SKU спускаємось
аллокацією вже на етапі ознак.
"""
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class ProductSpec:
    code: str
    display_name: str
    category: str
    include: Tuple[str, ...]
    exclude: Tuple[str, ...] = ()
    # Терміни, які самі по собі неоднозначні → вимагають контекстного слова поруч.
    ambiguous: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    # Патерни, що натякають на рекламу/агрегатор, а не на живу згадку.
    promo_hints: Tuple[str, ...] = ()
    sku: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    # Теги Mastodon, які тягнемо поллінгом публічних стрічок.
    mastodon_tags: Tuple[str, ...] = ()
    # Запити для авторизованого добору історії через searchPosts.
    search_queries: Tuple[str, ...] = ()


FUJIFILM_X = ProductSpec(
    code="fujifilm_x",
    display_name="Fujifilm X-series",
    category="camera",
    include=(
        r"fujifilm",
        r"fujinon",
        r"\bx100\s?-?(vi|v|f|t|s)\b",
        r"\bx-?t[1-5]\b",
        r"\bx-?s\s?-?[12]0\b",
        r"\bx-?pro\s?-?[123]\b",
        r"\bx-?e[1-5]\b",
        r"\bgfx\s?-?(50|100)\b",
        r"\binstax\b",
    ),
    ambiguous={
        # «fuji» сам по собі — це гора, яблуко і телеканал.
        r"\bfuji\b": (
            r"camera", r"lens", r"photo", r"photograph", r"shot", r"shoot",
            r"film simulation", r"recipe", r"viewfinder", r"jpeg", r"mirrorless",
            r"\bx100", r"\bx-?t", r"\bgfx",
        ),
    },
    exclude=(
        r"mount\s+fuji", r"fuji\s?-?san\b", r"fujisan",
        r"fuji\s?tv", r"fujitv",
        r"fuji\s+apple",
    ),
    promo_hints=(r"\d{1,2}%\s*off", r"\baffiliate\b", r"\bcoupon\b", r"\bamzn\.to\b"),
    sku={
        # x100v не повинен ловити x100vi — звідси негативний lookahead.
        "x100vi": (r"\bx100\s?-?vi\b",),
        "x100v": (r"\bx100\s?-?v(?!i)\b",),
        "x_t5": (r"\bx-?t5\b",),
        "x_s20": (r"\bx-?s\s?-?20\b",),
        "gfx100": (r"\bgfx\s?-?100\b",),
    },
    mastodon_tags=("fujifilm", "fujix", "x100vi", "believeinfilm", "photography"),
    search_queries=("fujifilm", "x100vi", "fujifilm x100", "x-t5", "fujinon"),
)

STEAM_DECK = ProductSpec(
    code="steam_deck",
    display_name="Steam Deck",
    category="handheld_console",
    include=(
        r"steam\s?-?deck",
        r"\bsteamdeck\b",
    ),
    exclude=(),
    # На Mastodon цей тег окупували агрегатори знижок: 182 дописи/добу
    # за середньої залученості 0.5. Помічаємо, але не викидаємо —
    # порівняння чистого й засміченого потоку саме по собі є результатом.
    promo_hints=(
        r"\d{1,2}%\s*off", r"\bon sale\b", r"\bdeal of\b", r"\bcoupon\b",
        r"\bsteam\s?db\b", r"\bisthereanydeal\b", r"\bhistorical low\b",
    ),
    sku={
        "oled": (r"\boled\b",),
        "lcd": (r"\blcd\b",),
        "512gb": (r"\b512\s?gb\b",),
        "1tb": (r"\b1\s?tb\b",),
    },
    mastodon_tags=("steamdeck", "steam", "linuxgaming", "handheldgaming"),
    search_queries=("steam deck", "steamdeck", "steam deck oled"),
)

# Резерв: за потреби розширення до третьої категорії.
RASPBERRY_PI = ProductSpec(
    code="raspberry_pi",
    display_name="Raspberry Pi",
    category="sbc",
    include=(r"raspberry\s?pi", r"\brpi\s?-?[45]\b", r"\bpi\s?zero\b"),
    ambiguous={r"\brpi\b": (r"raspberry", r"gpio", r"sbc", r"pinout", r"hat\b")},
    exclude=(r"raspberry\s+pie", r"\bpi\s+day\b"),
    promo_hints=(r"\d{1,2}%\s*off", r"\bin stock at\b"),
    sku={"pi5": (r"\bpi\s?-?5\b", r"\brpi\s?-?5\b"), "pi4": (r"\bpi\s?-?4\b",)},
    mastodon_tags=("raspberrypi", "homelab"),
    search_queries=("raspberry pi", "raspberry pi 5"),
)

ACTIVE_PRODUCTS: Tuple[ProductSpec, ...] = (FUJIFILM_X, STEAM_DECK)
ALL_PRODUCTS: Tuple[ProductSpec, ...] = (FUJIFILM_X, STEAM_DECK, RASPBERRY_PI)

BY_CODE = {p.code: p for p in ALL_PRODUCTS}
