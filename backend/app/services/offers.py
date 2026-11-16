"""Offer catalogue cache.

`GET /admin_api/v1/offers` ignores `name` and `limit` and returns the entire catalogue on
every call, so the autocomplete would be re-downloading ~170 records per keystroke. We
mirror it into SQL once and search locally; `sync` refreshes it on demand.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keitaro.client import KeitaroClient
from app.models import Offer, utcnow

logger = logging.getLogger(__name__)


async def sync_offers(session: AsyncSession, client: KeitaroClient) -> tuple[int, datetime]:
    raw = await client.list_offers()
    now = utcnow()

    existing = {o.id: o for o in (await session.execute(select(Offer))).scalars()}
    seen: set[int] = set()

    for item in raw:
        offer_id = int(item["id"])
        seen.add(offer_id)
        offer = existing.get(offer_id) or Offer(id=offer_id, name="")
        offer.name = str(item.get("name") or f"Offer {offer_id}")
        offer.group_name = item.get("group") or None
        countries = item.get("country")
        offer.countries = countries if isinstance(countries, list) else None
        offer.payout_value = _as_float(item.get("payout_value"))
        offer.payout_currency = item.get("payout_currency") or None
        offer.state = item.get("state") or "active"
        offer.synced_at = now
        if offer_id not in existing:
            session.add(offer)

    stale = set(existing) - seen
    if stale:
        await session.execute(delete(Offer).where(Offer.id.in_(stale)))

    await session.flush()
    logger.info("offer cache synced: %s offers (%s removed)", len(seen), len(stale))
    return len(seen), now


async def ensure_cache(session: AsyncSession, client: KeitaroClient) -> None:
    """Populate the cache the first time it is needed."""
    count = await session.scalar(select(func.count()).select_from(Offer))
    if not count:
        await sync_offers(session, client)


# Offer ids are ordinary 32-bit integers. Anything larger cannot match one, and binding
# it would overflow the database's INTEGER type (SQLite raises OverflowError) rather than
# simply finding nothing.
MAX_OFFER_ID = 2**31 - 1


def _like_pattern(term: str) -> str:
    """Build a contains-pattern, escaping LIKE's own wildcards.

    Offer names are full of brackets and punctuation, and a user typing `%` or `_` means
    those characters — not "match anything".
    """
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_offers(session: AsyncSession, query: str | None, limit: int = 25) -> list[Offer]:
    stmt = select(Offer).where(Offer.state == "active")
    if query:
        term = query.strip()
        if term:
            conditions = [func.lower(Offer.name).like(_like_pattern(term), escape="\\")]
            if term.isdigit() and int(term) <= MAX_OFFER_ID:
                conditions.append(Offer.id == int(term))
            stmt = stmt.where(or_(*conditions))
    stmt = stmt.order_by(Offer.name).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def get_names(session: AsyncSession, offer_ids: set[int]) -> dict[int, str]:
    if not offer_ids:
        return {}
    rows = await session.execute(select(Offer.id, Offer.name).where(Offer.id.in_(offer_ids)))
    return {row[0]: row[1] for row in rows}


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
