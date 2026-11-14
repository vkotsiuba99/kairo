import os
import logging
from typing import AsyncGenerator
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

logger = logging.getLogger("kairo.database")


def _get_sqlite_url() -> str:
    # у кожного сервісу своя БД, тож і свій файл, якщо шлях не задано явно
    rel = settings.SQLITE_DB_PATH or f"data/kairo-{settings.SERVICE}.db"
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", rel))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


def _sqlite_engine(url: str):
    engine = create_async_engine(url, echo=False, connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _record):
        # WAL: читання не блокує запис — у режимі одного процесу сервіси пишуть у
        # спільний файл, поки інший сервіс читає
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def create_engine_instance():
    # If explicitly configured for sqlite or DATABASE_URL provided
    if settings.DATABASE_URL:
        if "sqlite" in settings.DATABASE_URL:
            return _sqlite_engine(settings.DATABASE_URL)
        return create_async_engine(settings.DATABASE_URL, echo=False, pool_size=10, max_overflow=20)

    # If fallback is enabled, test if Postgres server is reachable synchronously or via quick timeout
    if settings.USE_SQLITE_FALLBACK:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect((settings.POSTGRES_SERVER, settings.POSTGRES_PORT))
            sock.close()
            # Postgres port is open!
            return create_async_engine(settings.get_database_url(), echo=False, pool_size=10, max_overflow=20)
        except Exception:
            logger.warning(
                "PostgreSQL at %s:%s is not reachable. Falling back to SQLite at %s",
                settings.POSTGRES_SERVER,
                settings.POSTGRES_PORT,
                settings.SQLITE_DB_PATH
            )
            return _sqlite_engine(_get_sqlite_url())

    return create_async_engine(settings.get_database_url(), echo=False, pool_size=10, max_overflow=20)


engine = create_engine_instance()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
