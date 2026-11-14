"""
Події між сервісами: transactional outbox + шина (RabbitMQ або пам'ять процесу).

Сервіс не публікує подію в брокер напряму: emit() додає її в таблицю outbox у тій
самій транзакції, що й зміну даних. Ретранслятор (relay) відправляє непередані
події в брокер і позначає їх відправленими. Тому подія не губиться, якщо сервіс
упаде після commit, а гарантія доставки — «щонайменше один раз».

Споживач обробляє подію ідемпотентно: ідентифікатор події записується в
processed_events у тій самій транзакції, що й наслідки обробки, тож повторна
доставка нічого не змінює.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.entities import OutboxEvent, ProcessedEvent

logger = logging.getLogger("kairo.events")

EXCHANGE = "kairo.events"
CLAIM_TIMEOUT_SECONDS = 30

Handler = Callable[[AsyncSession, Dict[str, Any]], Awaitable[None]]
# тип події -> {сервіс-споживач: обробник}
_HANDLERS: Dict[str, Dict[str, Handler]] = {}


def handles(event_type: str, consumer: str):
    """Реєструє обробник події для сервісу-споживача."""
    def register(fn: Handler) -> Handler:
        consumers = _HANDLERS.setdefault(event_type, {})
        assert consumer not in consumers, f"{consumer} already handles {event_type}"
        consumers[consumer] = fn
        return fn
    return register


def emit(db: AsyncSession, event_type: str, payload: Dict[str, Any]) -> None:
    """Додає подію в outbox; вона піде в брокер після commit цієї ж сесії."""
    db.add(OutboxEvent(event_id=uuid.uuid4().hex, event_type=event_type, payload=payload))


def event_types_for(consumer: str) -> List[str]:
    return sorted(t for t, consumers in _HANDLERS.items() if consumer in consumers)


async def dispatch(consumer: str, event: Dict[str, Any]) -> bool:
    """Обробляє подію для споживача рівно один раз. False — подію вже оброблено."""
    fn = _HANDLERS.get(event["type"], {}).get(consumer)
    if fn is None:
        return False
    async with AsyncSessionLocal() as db:
        if await db.get(ProcessedEvent, (event["id"], consumer)):
            return False
        db.add(ProcessedEvent(event_id=event["id"], consumer=consumer))
        await fn(db, event["payload"])
        try:
            await db.commit()
        except IntegrityError:  # іншу репліку випередили — її результат уже записано
            await db.rollback()
            return False
    return True


class MemoryBus:
    """Шина в пам'яті процесу: для режиму SERVICE=all і тестів."""

    def __init__(self, consumers: Set[str]):
        self.consumers = consumers

    async def start(self) -> None:
        pass

    async def publish(self, event: Dict[str, Any]) -> None:
        for consumer in _HANDLERS.get(event["type"], {}):
            if consumer in self.consumers:
                await dispatch(consumer, event)

    async def close(self) -> None:
        pass


class RabbitBus:
    """RabbitMQ: topic-обмінник kairo.events, у кожного сервісу своя durable-черга."""

    def __init__(self, url: str, consumers: Set[str]):
        self.url, self.consumers = url, consumers
        self.connection = self.channel = self.exchange = None

    async def start(self) -> None:
        import aio_pika

        self.connection = await aio_pika.connect_robust(self.url)
        self.channel = await self.connection.channel(publisher_confirms=True)
        await self.channel.set_qos(prefetch_count=16)
        self.exchange = await self.channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        for consumer in self.consumers:
            types = event_types_for(consumer)
            if not types:
                continue
            queue = await self.channel.declare_queue(f"kairo.{consumer}", durable=True)
            for event_type in types:
                await queue.bind(self.exchange, routing_key=event_type)
            await queue.consume(self._callback(consumer))
            logger.info("Consuming %s for %s", ", ".join(types), consumer)

    def _callback(self, consumer: str):
        async def on_message(message) -> None:
            try:
                await dispatch(consumer, json.loads(message.body))
                await message.ack()
            except Exception as e:
                logger.warning("Event %s for %s failed: %s", message.message_id, consumer, e)
                await asyncio.sleep(1)  # без паузи повтор крутився б у тугому циклі
                await message.nack(requeue=True)
        return on_message

    async def publish(self, event: Dict[str, Any]) -> None:
        import aio_pika

        await self.exchange.publish(
            aio_pika.Message(
                json.dumps(event, default=str).encode(),
                message_id=event["id"],
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=event["type"],
        )

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()


def make_bus(consumers: Set[str]):
    if settings.RABBITMQ_URL:
        return RabbitBus(settings.RABBITMQ_URL, consumers)
    if settings.SERVICE != "all":
        logger.warning("RABBITMQ_URL is not set: events reach only services in this process")
    return MemoryBus(consumers)


async def relay_once(bus) -> int:
    """Відправляє непередані події з outbox. Повертає кількість відправлених."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=CLAIM_TIMEOUT_SECONDS)
    async with AsyncSessionLocal() as db:
        candidates = (
            await db.execute(
                select(OutboxEvent.id)
                .where(OutboxEvent.sent_at.is_(None))
                .where((OutboxEvent.claimed_at.is_(None)) | (OutboxEvent.claimed_at < stale))
                .order_by(OutboxEvent.id)
                .limit(settings.OUTBOX_BATCH_SIZE)
            )
        ).scalars().all()
        claimed = []
        for event_id in candidates:  # атомарне захоплення, як у черзі публікацій
            res = await db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id, OutboxEvent.sent_at.is_(None))
                .where((OutboxEvent.claimed_at.is_(None)) | (OutboxEvent.claimed_at < stale))
                .values(claimed_at=now)
            )
            if res.rowcount == 1:
                claimed.append(event_id)
        await db.commit()
        if not claimed:
            return 0

        rows = (
            await db.execute(select(OutboxEvent).where(OutboxEvent.id.in_(claimed)).order_by(OutboxEvent.id))
        ).scalars().all()
        sent = 0
        for row in rows:
            event = {
                "id": row.event_id,
                "type": row.event_type,
                "payload": row.payload,
                "occurred_at": row.created_at.isoformat() if row.created_at else None,
            }
            try:
                await bus.publish(event)
            except Exception as e:  # залишається в outbox і піде після тайм-ауту захоплення
                logger.warning("Event %s (%s) not sent: %s", row.event_id, row.event_type, e)
                break
            row.sent_at = datetime.now(timezone.utc)
            sent += 1
            await db.commit()
        return sent


async def drain(bus=None, rounds: int = 20) -> int:
    """Відправляє все з outbox, поки є що відправляти (тести, режим одного процесу)."""
    from app.services import local_services

    bus = bus or MemoryBus(set(local_services()))
    total = 0
    for _ in range(rounds):
        sent = await relay_once(bus)
        total += sent
        if not sent:
            break
    return total


async def run_relay(bus, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            sent = await relay_once(bus)
        except Exception as e:  # БД або брокер тимчасово недоступні
            logger.warning("Outbox relay failed: %s", e)
            sent = 0
        if not sent:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.OUTBOX_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
