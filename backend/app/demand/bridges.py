"""
Розпізнавання дзеркалених дописів.

Bridgy Fed зшиває Bluesky і федіверс в обидва боки: допис із Bluesky
з'являється в стрічці Mastodon від автора виду `user.bsky.social@bsky.brid.gy`,
а допис із Mastodon потрапляє в Bluesky під хендлом `user.instance.ap.brid.gy`.

Для збирача це означає, що один і той самий текст того самого автора
надходить двома каналами. Якщо цього не помітити, міжплатформенний обсяг
згадок подвоюється — причому нерівномірно, бо мостять лише частину акаунтів,
і сильніше мостять саме активні технічні спільноти, тобто зсув буде
систематичним, а не випадковим шумом.

Рядки не видаляємо: порівняння «скільки згадок бачить кожен майданчик
окремо» саме по собі є спостереженням. Просто позначаємо.
"""
import re
from typing import Optional

BRIDGE_HOSTS = (
    "brid.gy",
    "bsky.brid.gy",
    "ap.brid.gy",
    "web.brid.gy",
    "fed.brid.gy",
)

_HANDLE_RE = re.compile(r"(?:@|\.)(?:bsky|ap|web|fed)\.brid\.gy$", re.IGNORECASE)
_URI_RE = re.compile(r"https?://(?:[\w-]+\.)*brid\.gy/", re.IGNORECASE)


def is_bridged(handle: Optional[str] = None, uri: Optional[str] = None) -> bool:
    if handle and _HANDLE_RE.search(handle.strip()):
        return True
    if uri and _URI_RE.search(uri.strip()):
        return True
    return False
