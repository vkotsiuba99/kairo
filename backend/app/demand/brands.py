"""
Кандидати рівня бренду.

Перехід з окремих товарів на бренди має дві причини, і друга важливіша.

Перша очевидна: назву бренду пишуть правильно, а модель — як завгодно.
`reMarkable 2` дав нуль саме тому, що люди пишуть «reMarkable», «rM2»,
«my remarkable»; бренд такої проблеми не має.

Друга — арифметична. Ціль розкладається на дев'ять стадій воронки. При трьох
дописах на добу (а саме стільки давали Pi Pico, Wingspan, Analogue Pocket)
частка стадії — це нуль або одиниця, тобто шум. Лише на рівні бренду, де
дописів десятки й сотні, частки стають вимірюваними величинами.

Відбір іде смугою, а не за максимумом. Зверху смугу обмежує не смак, а
пагінація: Nintendo дає близько 4900 дописів на добу, і 49 сторінок пошуку
не вивели навіть за межі однієї доби. Глибока історія для таких брендів
недосяжна в принципі — лише накопичення вперед через Jetstream.
"""
from typing import Dict, Tuple

from app.demand.catalog import Candidate

C = Candidate

# Смуга придатності (дописів на добу).
BAND_MIN = 40.0    # нижче — частки стадій воронки не рахуються
BAND_MAX = 400.0   # вище — 180 днів не вичерпати за розумний час

_CAM = ("camera", "lens", "photo", "shot", "mirrorless", "shoot", "film", "iso")
_COFFEE = ("coffee", "espresso", "brew", "grinder", "kettle", "pour over", "beans")
_SYNTH = ("synth", "eurorack", "modular", "sampler", "sequencer", "drum machine", "midi")

BRANDS: Tuple[Candidate, ...] = (
    # --- Ігри та ігрове залізо ---
    C("b_nintendo", "Nintendo", "games_hw"),
    C("b_playstation", "PlayStation", "games_hw"),
    C("b_xbox", "Xbox", "games_hw"),
    C("b_valve", "Valve", "games_hw",
      context=("steam", "deck", "half-life", "portal", "index", "hardware")),
    C("b_anbernic", "Anbernic", "games_hw"),
    C("b_ayaneo", "Ayaneo", "games_hw"),
    C("b_8bitdo", "8BitDo", "games_hw"),
    C("b_analogue", "Analogue", "games_hw",
      context=("pocket", "duo", "console", "fpga", "cartridge", "retro")),

    # --- Фото й оптика ---
    C("b_fujifilm", "Fujifilm", "photo"),
    # «Canon» у фандомному сенсі («це канон») забиває видачу: 49 377 сирих
    # дописів заради 7 188 наших, сім сторінок із восьми марно. Збираємо
    # набором вузьких запитів, а смугу міряємо широким — інакше бренд
    # вилетить із меню за заниженим обсягом.
    C("b_canon", "Canon", "photo", context=_CAM + ("eos", "rf ", "dslr"),
      measure_query="Canon",
      queries=("Canon EOS", "Canon R6", "Canon R5", "Canon camera", "Canon lens")),
    C("b_nikon", "Nikon", "photo"),
    C("b_leica", "Leica", "photo"),
    C("b_ricoh", "Ricoh", "photo"),
    C("b_sigma", "Sigma", "photo", context=_CAM + ("art", "contemporary", "fp")),
    C("b_lumix", "Lumix", "photo"),
    C("b_dji", "DJI", "photo"),
    C("b_gopro", "GoPro", "photo"),
    C("b_insta360", "Insta360", "photo"),
    C("b_polaroid", "Polaroid", "photo"),
    C("b_hasselblad", "Hasselblad", "photo"),

    # --- Комп'ютери та телефони ---
    C("b_apple", "Apple", "computers",
      context=("iphone", "macbook", "ipad", "mac ", "airpods", "ios", "macos", "watch")),
    C("b_framework", "Framework", "computers",
      context=("laptop", "mainboard", "expansion card", "diy edition", "repairable", "16")),
    C("b_raspberry_pi", "Raspberry Pi", "computers"),
    C("b_system76", "System76", "computers"),
    C("b_thinkpad", "ThinkPad", "computers"),
    C("b_fairphone", "Fairphone", "computers"),
    C("b_pixel", "Pixel", "computers",
      context=("google", "phone", "android", "tensor", "camera", "grapheneos"),
      queries=("Google Pixel",)),
    C("b_beelink", "Beelink", "computers"),

    # --- Навушники та аудіо ---
    C("b_sennheiser", "Sennheiser", "audio"),
    C("b_bose", "Bose", "audio"),
    C("b_audiotechnica", "Audio-Technica", "audio"),
    C("b_beyerdynamic", "Beyerdynamic", "audio"),
    C("b_moondrop", "Moondrop", "audio"),
    C("b_fiio", "FiiO", "audio"),
    C("b_hifiman", "HiFiMan", "audio"),
    C("b_audeze", "Audeze", "audio"),
    C("b_shure", "Shure", "audio"),
    C("b_shokz", "Shokz", "audio"),

    # --- Електронні книги ---
    C("b_kobo", "Kobo", "ereaders"),
    C("b_boox", "Boox", "ereaders"),
    # «remarkable» — звичайний англійський прикметник, і без контексту бренд
    # показував 375 дописів на добу чистого шуму.
    C("b_remarkable", "reMarkable", "ereaders",
      context=("tablet", "e-ink", "eink", "e-paper", "paper pro", "rm2",
               "stylus", "handwriting", "notebook", "marker"),
      queries=("reMarkable tablet",)),
    C("b_pocketbook", "PocketBook", "ereaders"),
    C("b_supernote", "Supernote", "ereaders"),
    C("b_kindle", "Kindle", "ereaders"),

    # --- Клавіатури та периферія ---
    C("b_keychron", "Keychron", "keyboards"),
    C("b_zsa", "ZSA", "keyboards", context=("keyboard", "moonlander", "voyager", "ergo", "oryx")),
    C("b_wooting", "Wooting", "keyboards"),
    C("b_nuphy", "NuPhy", "keyboards"),
    C("b_logitech", "Logitech", "keyboards"),
    C("b_razer", "Razer", "keyboards"),

    # --- 3D-друк і мейкерство ---
    C("b_bambu_lab", "Bambu Lab", "making"),
    C("b_prusa", "Prusa", "making"),
    C("b_creality", "Creality", "making"),
    C("b_elegoo", "Elegoo", "making"),
    C("b_anycubic", "Anycubic", "making"),
    C("b_snapmaker", "Snapmaker", "making"),
    C("b_arduino", "Arduino", "making"),
    C("b_adafruit", "Adafruit", "making"),
    C("b_sparkfun", "SparkFun", "making"),

    # --- Настільні ігри ---
    C("b_stonemaier", "Stonemaier", "boardgames"),
    C("b_asmodee", "Asmodee", "boardgames"),
    C("b_ravensburger", "Ravensburger", "boardgames"),
    C("b_fantasy_flight", "Fantasy Flight", "boardgames"),

    # --- Кавове обладнання ---
    C("b_aeropress", "AeroPress", "coffee"),
    C("b_hario", "Hario", "coffee"),
    C("b_fellow", "Fellow", "coffee", context=_COFFEE + ("stagg", "ode", "opus")),
    C("b_baratza", "Baratza", "coffee"),
    C("b_comandante", "Comandante", "coffee"),
    C("b_timemore", "Timemore", "coffee"),
    C("b_moccamaster", "Moccamaster", "coffee"),
    C("b_breville", "Breville", "coffee"),
    C("b_gaggia", "Gaggia", "coffee"),

    # --- Музичне обладнання ---
    C("b_elektron", "Elektron", "music"),
    C("b_moog", "Moog", "music"),
    C("b_korg", "Korg", "music"),
    C("b_roland", "Roland", "music", context=_SYNTH + ("tr-", "sh-", "juno", "jupiter")),
    C("b_arturia", "Arturia", "music"),
    C("b_novation", "Novation", "music"),
    C("b_behringer", "Behringer", "music"),
    C("b_fender", "Fender", "music",
      context=("guitar", "bass", "amp", "telecaster", "stratocaster", "jazzmaster",
               "pickup", "strat", "tele", "squier", "precision"),
      queries=("Fender guitar",)),
    C("b_gibson", "Gibson", "music", context=("guitar", "les paul", "sg", "pickup", "amp")),
    C("b_teenage_engineering", "Teenage Engineering", "music"),
    C("b_akai", "Akai", "music"),
    C("b_polyend", "Polyend", "music"),

    # --- Велосипеди ---
    C("b_shimano", "Shimano", "cycling"),
    C("b_sram", "SRAM", "cycling"),
    C("b_brompton", "Brompton", "cycling"),
    C("b_surly", "Surly", "cycling", context=("bike", "bicycle", "frame", "cycling", "tubing")),
    C("b_specialized", "Specialized", "cycling",
      context=("bike", "bicycle", "tarmac", "stumpjumper", "cycling")),
    C("b_canyon", "Canyon", "cycling",
      context=("bike", "bicycle", "aeroad", "endurace", "cycling", "grail")),
    C("b_trek", "Trek", "cycling",
      context=("bike", "bicycle", "domane", "madone", "cycling", "checkpoint")),
    C("b_garmin", "Garmin", "cycling"),
    C("b_wahoo", "Wahoo", "cycling", context=("kickr", "bike", "cycling", "trainer", "elemnt")),
    C("b_ortlieb", "Ortlieb", "cycling"),

    # --- Туризм і спорядження ---
    # Patagonia — ще й регіон, а на додачу тег окупував агрегатор оголошень,
    # який дослівно повторює той самий лістинг (44% дублів у замірі).
    C("b_patagonia", "Patagonia", "outdoor",
      context=("jacket", "fleece", "puffer", "hoody", "vest", "synchilla",
               "nano puff", "torrentshell", "baggies", "outdoor gear"),
      exclude=(r"\+\s*shipping", r"lowest of \d+", r"current size", r"bid"),
      queries=("Patagonia jacket",)),
    C("b_arcteryx", "Arcteryx", "outdoor", aliases=("arc'teryx",)),
    C("b_osprey", "Osprey", "outdoor",
      context=("backpack", "pack", "hiking", "atmos", "aether", "trail")),
    C("b_zpacks", "Zpacks", "outdoor"),
    C("b_hyperlite", "Hyperlite Mountain Gear", "outdoor"),
    C("b_thermarest", "Therm-a-Rest", "outdoor"),
    C("b_msr", "MSR", "outdoor", context=("tent", "stove", "pocketrocket", "hiking", "backpack")),
    C("b_jetboil", "Jetboil", "outdoor"),
    C("b_sawyer", "Sawyer", "outdoor", context=("filter", "squeeze", "water", "hiking", "backpack")),
    C("b_darn_tough", "Darn Tough", "outdoor"),
    C("b_hoka", "Hoka", "outdoor"),
    C("b_altra", "Altra", "outdoor"),
    C("b_big_agnes", "Big Agnes", "outdoor"),
)

BY_CODE: Dict[str, Candidate] = {b.code: b for b in BRANDS}
