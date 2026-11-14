"""
Сервіси Kairo: хто якими даними володіє і які маршрути обслуговує.

Кожен сервіс створює у своїй БД лише власні таблиці; дані інших сервісів він
отримує через REST або події, а не запитами до чужих таблиць.
"""

from typing import Dict, List

from app.core.config import settings

# Таблиці, якими володіє сервіс (довідник платформ — однакова незмінна копія в двох сервісах)
SERVICE_TABLES: Dict[str, List[str]] = {
    "content": [
        "platforms", "generation_jobs", "content_items", "audience_profiles", "audience_segments",
        "policy_arms", "policy_decisions", "policy_rewards",
        "experiments", "simulation_runs", "metrics_snapshots",
    ],
    "scheduler": ["posts", "channel_plans"],
    "publishing": ["platforms", "connected_accounts", "publications"],
    "telemetry": ["tracked_posts", "telemetry_events"],
    "media": [],
    "trends": [
        "demand_products", "social_mentions", "mention_annotations",
        "demand_observations", "demand_daily_features",
    ],
}

# Службові таблиці подій — у кожному сервісі, що має БД
INFRA_TABLES = ["outbox_events", "processed_events"]

# Модулі app.api.v1, підключені в сервісі
SERVICE_ROUTERS: Dict[str, List[str]] = {
    "content": ["content", "audience", "experiments"],
    "scheduler": ["schedule"],
    "publishing": ["platforms", "publishing"],
    "telemetry": ["telemetry", "dashboard", "logs"],
    "media": ["media"],
    "trends": ["demand"],
}

ALL = list(SERVICE_TABLES)


def local_services() -> List[str]:
    """Сервіси, що працюють у цьому процесі."""
    if settings.SERVICE == "all":
        return ALL
    if settings.SERVICE not in SERVICE_TABLES:
        raise ValueError(f"Unknown SERVICE={settings.SERVICE!r}; expected one of {ALL} or 'all'")
    return [settings.SERVICE]


def local_tables() -> List[str]:
    tables: List[str] = []
    for service in local_services():
        tables += [t for t in SERVICE_TABLES[service] if t not in tables]
    return tables + INFRA_TABLES if tables else []
