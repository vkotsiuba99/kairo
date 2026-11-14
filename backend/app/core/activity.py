"""Спільний формат записів журналу подій (кожен сервіс віддає свої, Telemetry зводить)."""

from datetime import datetime, timezone
from typing import Optional


def iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
