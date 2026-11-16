from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("KEITARO_BASE_URL", "https://kt.test")
os.environ.setdefault("KEITARO_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEFAULT_TRAFFIC_SOURCE_ID", "836")
os.environ.setdefault("DEFAULT_DOMAIN_ID", "1813")
os.environ.setdefault("DEFAULT_GROUP_ID", "9799")

from app import db as db_module  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.deps import get_keitaro  # noqa: E402
from app.keitaro.client import KeitaroClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

KT = "https://kt.test/admin_api/v1"


@pytest.fixture(autouse=True)
def clear_stats_cache():
    """The stats cache is process-global; tests must not inherit each other's numbers."""
    from app.services import stats

    stats._cache.clear()
    yield
    stats._cache.clear()


@pytest_asyncio.fixture
async def engine():
    # StaticPool keeps the one in-memory database alive across sessions.
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def app(engine, monkeypatch):
    """App wired to the in-memory database and a stub-friendly Keitaro client."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db_module, "_engine", engine, raising=False)
    monkeypatch.setattr(db_module, "_sessionmaker", maker, raising=False)

    application = create_app()

    async def _keitaro() -> AsyncIterator[KeitaroClient]:
        async with httpx.AsyncClient(timeout=5.0) as http_client:
            client = KeitaroClient(get_settings(), client=http_client)
            async with client:
                yield client

    application.dependency_overrides[get_keitaro] = _keitaro
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    # Run the lifespan so app.state.http_client exists even though the dep is overridden.
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        yield client
