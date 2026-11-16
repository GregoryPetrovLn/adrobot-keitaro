"""Async SQLAlchemy engine/session wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import audit
from app.config import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        # SQLite has no pool to configure and chokes on pool kwargs.
        kwargs: dict[str, object] = {"echo": False, "future": True}
        if not url.startswith("sqlite"):
            kwargs |= {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True}
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite(_engine)
    return _engine


def _configure_sqlite(engine: AsyncEngine) -> None:
    """WAL plus a busy timeout, so a reader never trips over a concurrent writer."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction-scoped session per request.

    Audit rows buffered during the request are written afterwards, once the request's own
    transaction is over — including when it failed, which is the case the log exists for.
    """
    audit.begin()
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            await _flush_audit(session)
            raise
        await _flush_audit(session)


async def _flush_audit(session: AsyncSession) -> None:
    from app.models import SyncLog

    rows = audit.drain()
    if not rows:
        return
    try:
        await session.execute(insert(SyncLog), rows)
        await session.commit()
    except Exception:
        # Auditing must never mask the operation's own outcome.
        await session.rollback()
        logger.exception("could not write %s audit rows", len(rows))


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
