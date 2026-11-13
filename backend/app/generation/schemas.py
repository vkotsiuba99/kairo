from typing import List, Optional
import re

from pydantic import BaseModel, Field


# Замовник вимагає текст без емодзі. Інструкції в промпті модель час від часу
# ігнорує, тому вивід додатково чиститься детерміновано — на рівні схеми,
# через яку проходить і локальний, і хмарний бекенд.
_EMOJI_RE = re.compile(
    "["
    "🀀-🫿"
    "←-⇿"
    "⌀-⏿"
    "─-◿"
    "☀-➿"
    "⬀-⯿"
    "️‍⃣"
    "]+"
)


def strip_emoji(text: Optional[str]) -> Optional[str]:
    """Прибирає емодзі й прибирає за собою зайві пробіли."""
    if not text:
        return text
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ 	]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ 	]+([,.;:!?])", r"", cleaned)
    cleaned = re.sub(r"(?m)^[ 	]+", "", cleaned)
    cleaned = re.sub(r"(?m)[ 	]+$", "", cleaned)
    return cleaned.strip()


def normalize_paragraphs(text: Optional[str]) -> Optional[str]:
    """
    Відновлює абзацну структуру, коли модель віддала суцільний блок.

    Частина моделей ігнорує вимогу розділяти абзаци подвійним переносом і
    зшиває пункти списку в один рядок через « - ». У читанні це виглядає як
    стіна тексту, тому структура відновлюється детерміновано, а не проханням
    до моделі.
    """
    if not text:
        return text

    body = text.replace("\r\n", "\n").strip()

    # Пункти списку, зшиті в рядок: « ... . - наступний пункт»
    body = re.sub(r"(?<=[.!?:])\s+[-–—]\s+", "\n\n- ", body)
    body = re.sub(r"(?<=[.!?:])\s+(\d+[.)])\s+", r"\n\n\1 ", body)

    if "\n\n" not in body:
        # Жодного маркера — групуємо речення по три, щоб текст лишався читабельним
        sentences = re.split(r"(?<=[.!?])\s+", body)
        if len(sentences) > 3:
            chunks = [
                " ".join(sentences[i : i + 3]).strip()
                for i in range(0, len(sentences), 3)
            ]
            body = "\n\n".join(c for c in chunks if c)

    # Одиночні переноси всередині абзацу лишаємо, зайві потрійні прибираємо
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def derive_title(body: Optional[str], limit: int = 90) -> Optional[str]:
    """
    Складає заголовок із тексту, коли модель не повернула поле `title`.

    За схемою заголовок необов'язковий, тому частина відповідей приходить без
    нього — і матеріал показувався як «Без заголовка». Замість того щоб
    покладатися на слухняність моделі, заголовок виводиться з першого речення
    тексту: він гарантовано є при будь-якому бекенді, включно з офлайновим
    шаблонізатором.
    """
    if not body:
        return None

    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    # прибираємо маркер списку чи нумерацію, якщо текст починається з пункту
    first_line = re.sub(r"^[-\u2013\u2014\u2022*]+\s*|^\d+[.)]\s*", "", first_line).strip()
    if not first_line:
        return None

    title = re.split(r"(?<=[.!?])\s", first_line, maxsplit=1)[0].strip()

    if len(title) > limit:
        title = title[:limit].rsplit(" ", 1)[0].rstrip(" ,;:\u2014-") + "\u2026"

    title = title.rstrip(" .")
    return title or None


class GeneratedContentSchema(BaseModel):
    """
    Structured output schema for LLM generation.
    Enforced via JSON Schema response_format.
    """
    title: Optional[str] = Field(
        default=None,
        description="Catchy title or hook for the post/video"
    )
    body: str = Field(
        ...,
        description="Main post text or script content formatted appropriately for the target platform"
    )
    hashtags: List[str] = Field(
        default_factory=list,
        description="List of relevant hashtags without '#' prefix"
    )
    media_suggestion: Optional[str] = Field(
        default=None,
        description="Visual prompt description for image/video keyframe generation"
    )

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        if isinstance(obj, dict):
            obj["title"] = strip_emoji(obj.get("title"))
            cleaned_body = strip_emoji(obj.get("body")) or obj.get("body")
            obj["body"] = normalize_paragraphs(cleaned_body) or cleaned_body
            if not (obj.get("title") or "").strip():
                obj["title"] = derive_title(obj.get("body"))
            tags = obj.get("hashtags")
            if isinstance(tags, str):
                obj["hashtags"] = [t.strip().lstrip("#") for t in tags.replace(",", " ").split() if t.strip()]
            elif isinstance(tags, list):
                obj["hashtags"] = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
            obj["hashtags"] = [
                t for t in (strip_emoji(t) for t in obj.get("hashtags") or []) if t
            ]
        return super().model_validate(obj, *args, **kwargs)


CONTENT_JSON_SCHEMA = {
    "name": "generated_content",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": ["string", "null"]},
            "body": {"type": "string"},
            "hashtags": {
                "type": "array",
                "items": {"type": "string"}
            },
            "media_suggestion": {"type": ["string", "null"]}
        },
        "required": ["body", "hashtags"],
        "additionalProperties": False
    }
}
