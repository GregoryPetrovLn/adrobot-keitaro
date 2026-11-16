"""Offer catalogue: cached search for the autocomplete, plus an explicit refresh."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import KeitaroDep, SessionDep
from app.schemas import OfferOut, OfferSyncResult
from app.services import offers as offers_service
from app.services.logs import record_calls

router = APIRouter(prefix="/offers", tags=["offers"])


@router.get("", response_model=list[OfferOut])
async def search_offers(
    session: SessionDep,
    client: KeitaroDep,
    q: str | None = Query(default=None, description="Search by offer name or exact id"),
    limit: int = Query(default=25, ge=1, le=200),
) -> list[OfferOut]:
    await offers_service.ensure_cache(session, client)
    await record_calls(session, client, action="offers_cache")
    found = await offers_service.search_offers(session, q, limit)
    return [OfferOut.model_validate(o) for o in found]


@router.post("/sync", response_model=OfferSyncResult)
async def sync_offers(session: SessionDep, client: KeitaroDep) -> OfferSyncResult:
    count, synced_at = await offers_service.sync_offers(session, client)
    await record_calls(session, client, action="offers_sync")
    return OfferSyncResult(synced=count, synced_at=synced_at)
