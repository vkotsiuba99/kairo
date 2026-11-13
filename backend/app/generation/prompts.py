from typing import Dict, Any

# Мова матеріалу задається портретом сегмента (поле language); системні
# інструкції моделі — англійською.
LANGUAGE_NAMES = {"en": "English", "uk": "Ukrainian"}

PLATFORM_CONSTRAINTS = {
    "telegram": {
        "min_length": 1200,
        "max_length": 3800,
        "formatting": (
            "A complete analytical article or detailed long read (1,200–2,800 characters). "
            "REQUIRED STRUCTURE: "
            "1. A hook headline without emoji. "
            "2. An opening paragraph with context and a sharp problem. "
            "3. A detailed breakdown in 3–5 sections (dash bullet lists, concrete architectural or code details). "
            "4. Practical takeaways and advice. "
            "5. A question to subscribers at the end to start a discussion. "
            "NEVER produce one or two short sentences or a brief teaser!"
        ),
        "description": "Telegram channel (readers expect a deep, useful, structured long read)",
    },
    "mastodon": {
        "min_length": 250,
        "max_length": 490,
        "formatting": "A substantive status (250–480 characters) with a developed idea, a clear argument and 3–5 hashtags.",
        "description": "Mastodon, a decentralized network (thoughtful engineering community)",
    },
    "bluesky": {
        "min_length": 180,
        "max_length": 290,
        "formatting": "A sharp, concise post (180–290 characters) with a strong hook, the key insight and hashtags.",
        "description": "Bluesky on the AT Protocol (fast, dynamic feed)",
    },
    "youtube": {
        "min_length": 800,
        "max_length": 4500,
        "formatting": "A YouTube Shorts / video script: a strong hook (0–3 s), voice-over text with timings and visual notes.",
        "description": "YouTube Shorts script",
    },
    "facebook": {
        "min_length": 600,
        "max_length": 2500,
        "formatting": "A page post (600–2,000 characters): a clear opening line, 2–4 short paragraphs and a question to the audience.",
        "description": "Facebook page (broad audience, conversational tone)",
    },
    "instagram": {
        "min_length": 300,
        "max_length": 2100,
        "formatting": "An image caption (300–1,800 characters): a hook in the first line, short paragraphs, up to 10 relevant hashtags.",
        "description": "Instagram professional account (visual-first feed, caption limit 2,200)",
    },
    "tiktok": {
        "min_length": 150,
        "max_length": 2100,
        "formatting": "A short-video or photo-post caption (150–600 characters) plus a punchy spoken script outline for the first 3 seconds.",
        "description": "TikTok (short vertical video, very fast feed)",
    },
    "local_archive": {
        "min_length": 1000,
        "max_length": 4000,
        "formatting": "A complete archival post with a headline, details and a description of the media.",
        "description": "Archive master post",
    },
}

TOPIC_DESCRIPTIONS = {
    "ai_breakthroughs": "New discoveries and breakthroughs in artificial intelligence, open LLM architectures (DeepSeek, LLaMA, Qwen), reasoning models and their impact on software development",
    "developer_humor": "Self-ironic observations about engineers' everyday life: Friday deploys, debugging in production, legacy code, on-call nights and deadlines",
    "open_source_tools": "An in-depth look at the most interesting open-source libraries, CLI utilities and tools that streamline a developer's workflow",
    "tech_career_tips": "Practical advice on engineering growth, moving to Senior/Staff/Lead level, architectural thinking and managing complexity",
    "system_architecture": "A deep dive into fault-tolerant distributed systems: CQRS, event sourcing, the Saga pattern, distributed queues, sharding and caching",
}

TONE_INSTRUCTIONS = {
    "analytical": "A deep, analytical, expert tone focused on technical details, comparison, pros and cons and architectural trade-offs.",
    "provocative": "A sharp, bold, polemical tone that breaks popular myths and sparks a heated technical discussion in the comments.",
    "humorous": "A witty, lively tone with subtle professional irony, metaphors every developer understands and meme references.",
    "inspirational": "An inspiring, energetic tone that motivates readers to build complex systems, experiment and learn cutting-edge technologies.",
}


# Формулювання, якими модель зазвичай заповнює обсяг, коли їй нічого сказати.
# Перелічені явно: заборона за списком працює краще, ніж абстрактна вимога
# «пиши змістовно».
BANNED_FILLER = [
    "in today's fast-paced world",
    "it's no secret that",
    "plays a crucial role",
    "is evolving rapidly",
    "it is hard to overestimate",
    "let's dive in",
    "in this article we will explore",
    "I hope this information was helpful",
    "in conclusion",
]


def build_system_prompt(platform_code: str, language: str = "en") -> str:
    constraint = PLATFORM_CONSTRAINTS.get(platform_code, PLATFORM_CONSTRAINTS["telegram"])
    min_len = constraint.get("min_length", 1000)
    max_len = constraint.get("max_length", 3500)
    banned = "; ".join(f'"{p}"' for p in BANNED_FILLER)
    lang = LANGUAGE_NAMES.get(language, "English")

    return (
        f"You are a practitioner who writes about what you have actually done, for "
        f"{constraint['description']}. Write in {lang}. Your reputation rests on "
        f"specifics: the reader must learn something new and be able to verify or apply it.\n\n"
        f"MAIN RULE: every paragraph makes a concrete claim with a reason. A paragraph that "
        f"can be removed without losing meaning should not be written — a shorter text is "
        f"better than a padded one.\n\n"
        f"MUST INCLUDE:\n"
        f"1. Specifics: names of tools, places, models, techniques, numbers, timeframes, "
        f"prices, versions — things that can be checked. At least three concrete details.\n"
        f"2. Practical experience: what it really looks like, what goes wrong, how long it "
        f"takes, what it costs.\n"
        f"3. A trade-off or limitation: where the approach fails, who it does not suit, what "
        f"it costs. A text without a single 'but' is an ad, not help.\n"
        f"4. A concrete next step for the reader that they can start today.\n\n"
        f"STRICTLY FORBIDDEN:\n"
        f"- Empty intros and bureaucratic language. Banned phrases: {banned}.\n"
        f"- Generic advice like 'practice is key' or 'it depends' without explaining what it "
        f"depends on.\n"
        f"- Restating the headline in the first paragraph: start with the substance.\n"
        f"- Emoji, icons or pictograms anywhere (title, body, hashtags). Use a plain dash for lists.\n"
        f"- A short teaser instead of the actual post.\n\n"
        f"FORMAT: {constraint['formatting']}\n"
        f"LENGTH: {min_len}–{max_len} characters; separate paragraphs with a blank line.\n\n"
        f"MEDIA PROMPT: the 'media_suggestion' field must be in English only. Describe a "
        f"physical scene or object (a workshop, tools, people at work, materials, a landscape). "
        f"NEVER ask for diagrams, charts, schemes, user interfaces, screens or any text in the "
        f"image. Do not mention colors or style — the brand style is added automatically.\n\n"
        f"Return STRICTLY valid JSON with no extra text:\n"
        f"{{\n"
        f'  "title": "A concrete headline: what exactly the post is about",\n'
        f'  "body": "The full multi-paragraph post...",\n'
        f'  "hashtags": ["tag1", "tag2", "tag3"],\n'
        f'  "media_suggestion": "Physical scene description in English"\n'
        f"}}"
    )


def build_user_prompt(
    topic: str,
    format_type: str,
    tone: str,
    length_bucket: str,
    audience_interests: str = "",
) -> str:
    topic_desc = TOPIC_DESCRIPTIONS.get(topic, topic)
    tone_desc = TONE_INSTRUCTIONS.get(tone, tone)

    length_guidance = {
        "short": "Concise but substantive (2–3 clear paragraphs with the key points)",
        "medium": "A developed analytical post (4–5 structured paragraphs, detailed breakdown)",
        "long": "A full technical long read (5–7 paragraphs, detailed bullet points, examples and conclusions, 1,500+ characters)",
    }.get(length_bucket, "A full, developed analytical post")

    return (
        f"Create a detailed post from these inputs:\n"
        f"- Topic: {topic} ({topic_desc})\n"
        f"- Content format: {format_type}\n"
        f"- Tone: {tone} ({tone_desc})\n"
        f"- Length: {length_guidance}\n"
        + (f"- Audience: {audience_interests}. Match their level, vocabulary and daily life.\n" if audience_interests else "")
        + "\nWrite as if you were explaining it to a friend who is really about to do it.\n"
        + "Check yourself before answering: are there at least three verifiable details; is at "
        + "least one limitation or trade-off named; does the last paragraph make the next step "
        + "clear? If not, rewrite.\n"
        + "Write the 'media_suggestion' prompt STRICTLY in English as a description of a real "
        + "physical scene without lettering, diagrams or interfaces."
    )
