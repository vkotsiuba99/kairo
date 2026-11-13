"""
Каталог кандидатів для меню.

Меню не набирається вручну і не вигадується на льоту: тут лежить широкий
список кандидатів, а який з них потрапить в інтерфейс — вирішує замір
(`scripts/build_demand_catalog.py`). Товар без сигналу в меню не показуємо,
скільки б його не хотілося бачити.

Лексикон здебільшого дорівнює самій назві: «Steam Deck» чи «Bambu Lab P1S»
ні з чим не сплутати. Додаткові правила потрібні лише там, де назва є
звичайним словом — `context` вимагає контекстного слова поруч, `exclude`
прибирає відомий омонім.
"""
import re
from dataclasses import dataclass
from typing import Dict, Tuple

from app.demand.products import ProductSpec

CATEGORIES: Dict[str, str] = {
    "games_hw": "Games & gaming hardware",
    "photo": "Photo & optics",
    "computers": "Computers & phones",
    "audio": "Headphones & audio",
    "ereaders": "E-readers",
    "keyboards": "Keyboards & peripherals",
    "making": "3D printing & making",
    "boardgames": "Board games",
    "books": "Books & comics",
    "coffee": "Coffee gear",
    "music": "Music gear",
    "cycling": "Bicycles",
    "outdoor": "Outdoor & hiking gear",
}

# Натяки на рекламу — однакові для всіх товарів.
COMMON_PROMO = (
    r"\d{1,2}%\s*off", r"\bcoupon\b", r"\baffiliate\b",
    r"\bamzn\.to\b", r"\bon sale\b", r"\bbest deal\b",
)

_SPLIT = re.compile(r"[^0-9a-z]+", re.IGNORECASE)


def name_to_pattern(name: str) -> str:
    """«Bambu Lab P1S» -> r'\\bbambu[\\s\\-]*lab[\\s\\-]*p1s\\b'"""
    parts = [p for p in _SPLIT.split(name.lower()) if p]
    return r"\b" + r"[\s\-]*".join(re.escape(p) for p in parts) + r"\b"


def name_to_tag(name: str) -> str:
    return "".join(p for p in _SPLIT.split(name.lower()) if p)


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    category: str
    aliases: Tuple[str, ...] = ()
    # Якщо задано — назва сама по собі неоднозначна і зараховується
    # лише за наявності поруч одного з цих слів.
    context: Tuple[str, ...] = ()
    exclude: Tuple[str, ...] = ()
    # Звужений пошуковий запит для назв-омонімів. Фільтр уміє лише прибирати:
    # у сотні дописів за словом «remarkable» немає жодного про планшет, і після
    # фільтра лишається нуль. Знайти може тільки сам запит.
    queries: Tuple[str, ...] = ()
    # Запит для заміру смуги, якщо він має відрізнятись від запитів збору.
    # Це різні задачі: замір питає «скільки цього бренду взагалі є», збір —
    # «як дістати його дешево». Canon за широким «Canon» дає чесні 54 дописи
    # на добу після фільтра, але 15% корисних сторінок; вузькі запити збирають
    # утричі дешевше, проте занижують обсяг і викинули б бренд із меню.
    measure_query: str = ""

    @property
    def primary_query(self) -> str:
        if self.measure_query:
            return self.measure_query
        return self.queries[0] if self.queries else self.name

    def to_spec(self) -> ProductSpec:
        patterns = tuple(name_to_pattern(n) for n in (self.name, *self.aliases))
        ambiguous = {}
        include: Tuple[str, ...]
        if self.context:
            ambiguous = {p: self.context for p in patterns}
            include = ()
        else:
            include = patterns
        return ProductSpec(
            code=self.code,
            display_name=self.name,
            category=self.category,
            include=include,
            ambiguous=ambiguous,
            exclude=self.exclude,
            promo_hints=COMMON_PROMO,
            mastodon_tags=(name_to_tag(self.name),),
            search_queries=self.queries or (self.name, *self.aliases),
        )


C = Candidate
_BG = ("board game", "boardgame", "tabletop", "meeple", "playthrough", "bgg")
_BOOK = ("book", "novel", "read", "reading", "chapter", "author")
_MANGA = ("manga", "anime", "chapter", "volume", "arc")
_KBD = ("keyboard", "keycap", "switches", "split", "kbd", "ergo")

CANDIDATES: Tuple[Candidate, ...] = (
    # ---------------- Ігри та ігрове залізо ----------------
    C("steam_deck", "Steam Deck", "games_hw", aliases=("steamdeck",)),
    C("rog_ally", "ROG Ally", "games_hw", aliases=("rog ally x",)),
    C("legion_go", "Legion Go", "games_hw"),
    C("switch_2", "Nintendo Switch 2", "games_hw", aliases=("switch 2",)),
    C("ps5_pro", "PlayStation 5 Pro", "games_hw", aliases=("ps5 pro",)),
    C("xbox_series_x", "Xbox Series X", "games_hw"),
    C("analogue_pocket", "Analogue Pocket", "games_hw"),
    C("miyoo_mini", "Miyoo Mini", "games_hw"),
    C("anbernic_rg35xx", "Anbernic RG35XX", "games_hw", aliases=("rg35xx",)),
    C("playdate", "Playdate", "games_hw", context=("panic", "console", "handheld", "crank")),
    C("dualsense", "DualSense", "games_hw"),
    C("eightbitdo", "8BitDo", "games_hw"),
    C("backbone_one", "Backbone One", "games_hw"),
    C("ayaneo", "Ayaneo", "games_hw"),
    C("gpd_win", "GPD Win", "games_hw"),
    C("steam_controller", "Steam Controller", "games_hw"),

    # ---------------- Фото й оптика ----------------
    C("fujifilm_x100vi", "Fujifilm X100VI", "photo", aliases=("x100vi",)),
    C("fujifilm_xt5", "Fujifilm X-T5", "photo", aliases=("x-t5",)),
    C("sony_a7iv", "Sony A7 IV", "photo", aliases=("a7iv",)),
    C("canon_r6", "Canon R6", "photo", aliases=("eos r6",)),
    C("nikon_z6", "Nikon Z6", "photo", aliases=("z6iii",)),
    C("leica_q3", "Leica Q3", "photo"),
    C("ricoh_gr3", "Ricoh GR III", "photo", aliases=("ricoh griii", "gr iiix")),
    C("sigma_bf", "Sigma BF", "photo", context=("camera", "sigma", "lens", "photo")),
    C("lumix_s5", "Lumix S5", "photo", aliases=("panasonic s5",)),
    C("dji_pocket3", "DJI Pocket 3", "photo", aliases=("osmo pocket 3",)),
    C("insta360", "Insta360", "photo"),
    C("gopro_hero", "GoPro Hero", "photo"),
    C("polaroid", "Polaroid", "photo"),
    C("instax", "Instax", "photo"),
    C("hasselblad", "Hasselblad", "photo"),
    C("viltrox", "Viltrox", "photo"),

    # ---------------- Комп'ютери та телефони ----------------
    C("framework_laptop", "Framework Laptop", "computers", aliases=("framework 13", "framework 16")),
    C("mac_mini", "Mac Mini", "computers"),
    C("macbook_air", "MacBook Air", "computers"),
    C("raspberry_pi5", "Raspberry Pi 5", "computers", aliases=("rpi 5",)),
    C("iphone_17", "iPhone 17", "computers"),
    C("iphone_18", "iPhone 18", "computers"),
    C("pixel_10", "Pixel 10", "computers"),
    C("pixel_11", "Pixel 11", "computers"),
    C("galaxy_s26", "Galaxy S26", "computers"),
    C("nothing_phone", "Nothing Phone", "computers"),
    C("fairphone", "Fairphone", "computers"),
    C("system76", "System76", "computers"),
    C("thinkpad_x1", "ThinkPad X1", "computers"),
    C("surface_pro", "Surface Pro", "computers"),
    C("beelink", "Beelink", "computers"),
    C("orange_pi", "Orange Pi", "computers"),

    # ---------------- Навушники та аудіо ----------------
    C("airpods_pro", "AirPods Pro", "audio"),
    C("sony_xm6", "Sony WH-1000XM6", "audio", aliases=("xm6", "wh-1000xm5")),
    C("bose_qc", "Bose QuietComfort", "audio"),
    C("sennheiser_hd600", "Sennheiser HD 600", "audio", aliases=("hd6xx",)),
    C("audeze", "Audeze", "audio"),
    C("hifiman", "HiFiMan", "audio"),
    C("moondrop", "Moondrop", "audio"),
    C("truthear", "Truthear", "audio"),
    C("fiio", "FiiO", "audio"),
    C("qudelix", "Qudelix", "audio"),
    C("topping_dac", "Topping DAC", "audio"),
    C("schiit", "Schiit", "audio"),
    C("beyerdynamic_dt770", "Beyerdynamic DT 770", "audio", aliases=("dt770", "dt900")),
    C("nothing_ear", "Nothing Ear", "audio"),
    C("shokz", "Shokz", "audio"),
    C("sm7b", "Shure SM7B", "audio", aliases=("sm7b",)),

    # ---------------- Електронні книги ----------------
    C("kindle_paperwhite", "Kindle Paperwhite", "ereaders"),
    C("kindle_scribe", "Kindle Scribe", "ereaders"),
    C("kobo_clara", "Kobo Clara", "ereaders"),
    C("kobo_libra", "Kobo Libra", "ereaders"),
    C("kobo_elipsa", "Kobo Elipsa", "ereaders"),
    C("boox_palma", "Boox Palma", "ereaders"),
    C("boox_note_air", "Boox Note Air", "ereaders"),
    C("remarkable_2", "reMarkable 2", "ereaders"),
    C("remarkable_paper_pro", "reMarkable Paper Pro", "ereaders"),
    C("supernote", "Supernote", "ereaders"),
    C("pocketbook", "PocketBook", "ereaders"),
    C("daylight_computer", "Daylight Computer", "ereaders", aliases=("daylight dc1",)),

    # ---------------- Клавіатури та периферія ----------------
    C("keychron_q1", "Keychron Q1", "keyboards"),
    C("keychron_k2", "Keychron K2", "keyboards"),
    C("moonlander", "ZSA Moonlander", "keyboards", aliases=("moonlander",)),
    C("ergodox", "Ergodox", "keyboards"),
    C("glove80", "Glove80", "keyboards"),
    C("kinesis_advantage", "Kinesis Advantage", "keyboards"),
    C("hhkb", "HHKB", "keyboards", aliases=("happy hacking keyboard",)),
    C("mx_master", "MX Master", "keyboards"),
    C("wooting", "Wooting", "keyboards"),
    C("nuphy", "NuPhy", "keyboards"),
    C("corne", "Corne", "keyboards", context=_KBD),
    C("lily58", "Lily58", "keyboards"),
    C("ploopy", "Ploopy", "keyboards"),
    C("razer_basilisk", "Razer Basilisk", "keyboards"),
    C("logitech_mx_keys", "MX Keys", "keyboards"),
    C("dygma_defy", "Dygma Defy", "keyboards"),

    # ---------------- 3D-друк і мейкерство ----------------
    C("bambu_a1", "Bambu Lab A1", "making", aliases=("bambu a1",)),
    C("bambu_p1s", "Bambu Lab P1S", "making", aliases=("p1s",)),
    C("bambu_x1c", "Bambu Lab X1C", "making", aliases=("x1 carbon",)),
    C("bambu_h2d", "Bambu Lab H2D", "making", aliases=("h2d",)),
    C("prusa_mk4", "Prusa MK4", "making"),
    C("prusa_core_one", "Prusa Core One", "making"),
    C("voron", "Voron", "making", context=("3d print", "printer", "klipper", "toolhead", "gantry")),
    C("elegoo_saturn", "Elegoo Saturn", "making"),
    C("anycubic", "Anycubic", "making"),
    C("creality_ender", "Creality Ender", "making", aliases=("ender 3",)),
    C("snapmaker", "Snapmaker", "making"),
    C("glowforge", "Glowforge", "making"),
    C("xtool", "xTool", "making", context=("laser", "engrav", "cutter", "diode")),
    C("arduino", "Arduino", "making"),
    C("esp32", "ESP32", "making"),
    C("pi_pico", "Pi Pico", "making", aliases=("rp2040",)),

    # ---------------- Настільні ігри ----------------
    C("wingspan", "Wingspan", "boardgames", context=_BG),
    C("gloomhaven", "Gloomhaven", "boardgames"),
    C("frosthaven", "Frosthaven", "boardgames"),
    C("ark_nova", "Ark Nova", "boardgames"),
    C("brass_birmingham", "Brass Birmingham", "boardgames"),
    C("terraforming_mars", "Terraforming Mars", "boardgames"),
    C("catan", "Catan", "boardgames"),
    C("azul", "Azul", "boardgames", context=_BG),
    C("spirit_island", "Spirit Island", "boardgames"),
    C("everdell", "Everdell", "boardgames"),
    C("dune_imperium", "Dune Imperium", "boardgames"),
    C("cascadia", "Cascadia", "boardgames", context=_BG),
    C("root_boardgame", "Root", "boardgames", context=_BG),
    C("scythe", "Scythe", "boardgames", context=_BG),
    C("twilight_imperium", "Twilight Imperium", "boardgames"),
    C("heat_ptm", "Heat Pedal to the Metal", "boardgames", aliases=("heat boardgame",)),

    # ---------------- Книги та комікси ----------------
    C("dune_book", "Dune", "books", context=_BOOK, exclude=(r"dune\s+imperium",)),
    C("lotr", "Lord of the Rings", "books", aliases=("lotr",)),
    C("project_hail_mary", "Project Hail Mary", "books"),
    C("wheel_of_time", "Wheel of Time", "books"),
    C("discworld", "Discworld", "books"),
    C("sandman", "Sandman", "books", context=("comic", "gaiman", "graphic novel", "issue")),
    C("one_piece", "One Piece", "books", context=_MANGA),
    C("berserk", "Berserk", "books", context=_MANGA),
    C("chainsaw_man", "Chainsaw Man", "books"),
    C("jujutsu_kaisen", "Jujutsu Kaisen", "books"),
    C("frieren", "Frieren", "books"),
    C("dandadan", "Dandadan", "books"),
    C("saga_comic", "Saga", "books", context=("comic", "vaughan", "image comics", "trade paperback")),
    C("invincible", "Invincible", "books", context=("comic", "kirkman", "issue", "trade")),
    C("dungeon_meshi", "Delicious in Dungeon", "books", aliases=("dungeon meshi",)),
    C("witch_hat_atelier", "Witch Hat Atelier", "books"),

    # ---------------- Кавове обладнання ----------------
    C("aeropress", "AeroPress", "coffee"),
    C("fellow_stagg", "Fellow Stagg", "coffee"),
    C("fellow_ode", "Fellow Ode", "coffee"),
    C("baratza_encore", "Baratza Encore", "coffee"),
    C("comandante_c40", "Comandante C40", "coffee"),
    C("onezpresso", "1Zpresso", "coffee"),
    C("chemex", "Chemex", "coffee"),
    C("hario_v60", "Hario V60", "coffee", aliases=("v60",)),
    C("moccamaster", "Moccamaster", "coffee"),
    C("breville_barista", "Breville Barista", "coffee"),
    C("gaggia_classic", "Gaggia Classic", "coffee"),
    C("flair_espresso", "Flair Espresso", "coffee"),
    C("timemore", "Timemore", "coffee"),
    C("kalita_wave", "Kalita Wave", "coffee"),
    C("la_marzocco", "La Marzocco", "coffee"),
    C("varia_vs3", "Varia VS3", "coffee"),

    # ---------------- Музичне обладнання ----------------
    C("op1_field", "Teenage Engineering OP-1", "music", aliases=("op-1 field",)),
    C("digitakt", "Elektron Digitakt", "music", aliases=("digitakt",)),
    C("moog_matriarch", "Moog Matriarch", "music"),
    C("minilogue", "Korg Minilogue", "music", aliases=("minilogue xd",)),
    C("microfreak", "Arturia MicroFreak", "music", aliases=("microfreak",)),
    C("launchkey", "Novation Launchkey", "music"),
    C("tr8s", "Roland TR-8S", "music"),
    C("behringer_model_d", "Behringer Model D", "music"),
    C("stratocaster", "Stratocaster", "music"),
    C("les_paul", "Les Paul", "music"),
    C("rode_nt1", "Rode NT1", "music"),
    C("ableton_push", "Ableton Push", "music"),
    C("akai_mpc", "Akai MPC", "music"),
    C("polyend_tracker", "Polyend Tracker", "music"),
    C("make_noise", "Make Noise", "music", context=("eurorack", "modular", "synth", "module")),
    C("op_xy", "OP-XY", "music"),

    # ---------------- Велосипеди ----------------
    C("brompton", "Brompton", "cycling"),
    C("surly_lht", "Surly Long Haul Trucker", "cycling", aliases=("surly lht",)),
    C("specialized_tarmac", "Specialized Tarmac", "cycling"),
    C("trek_domane", "Trek Domane", "cycling"),
    C("canyon_aeroad", "Canyon Aeroad", "cycling"),
    C("rad_power", "Rad Power Bikes", "cycling"),
    C("shimano_105", "Shimano 105", "cycling"),
    C("sram_red_axs", "SRAM Red AXS", "cycling"),
    C("garmin_edge", "Garmin Edge", "cycling"),
    C("wahoo_kickr", "Wahoo Kickr", "cycling"),
    C("ortlieb", "Ortlieb", "cycling"),
    C("tern_gsd", "Tern GSD", "cycling"),
    C("omnium_cargo", "Omnium Cargo", "cycling"),
    C("crust_bikes", "Crust Bikes", "cycling"),
    C("rivendell", "Rivendell", "cycling", context=("bike", "bicycle", "frame", "riv", "cycling")),
    C("fairlight_cycles", "Fairlight Cycles", "cycling"),

    # ---------------- Туризм і спорядження ----------------
    C("garmin_fenix", "Garmin Fenix", "outdoor"),
    C("garmin_inreach", "Garmin inReach", "outdoor"),
    C("zpacks_duplex", "Zpacks Duplex", "outdoor"),
    C("big_agnes_copper_spur", "Big Agnes Copper Spur", "outdoor"),
    C("hyperlite", "Hyperlite Mountain Gear", "outdoor", aliases=("hmg",)),
    C("osprey_atmos", "Osprey Atmos", "outdoor"),
    C("nemo_tensor", "Nemo Tensor", "outdoor"),
    C("neoair", "Therm-a-Rest NeoAir", "outdoor", aliases=("neoair",)),
    C("jetboil", "Jetboil", "outdoor"),
    C("pocketrocket", "MSR PocketRocket", "outdoor", aliases=("pocketrocket",)),
    C("sawyer_squeeze", "Sawyer Squeeze", "outdoor"),
    C("darn_tough", "Darn Tough", "outdoor"),
    C("altra_lone_peak", "Altra Lone Peak", "outdoor"),
    C("hoka_speedgoat", "Hoka Speedgoat", "outdoor"),
    C("nano_puff", "Patagonia Nano Puff", "outdoor"),
    C("arcteryx_beta", "Arcteryx Beta", "outdoor", aliases=("arc'teryx beta",)),
)

BY_CODE: Dict[str, Candidate] = {c.code: c for c in CANDIDATES}
