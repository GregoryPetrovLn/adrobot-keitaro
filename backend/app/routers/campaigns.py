"""Campaign creation, import, listing and the stream editor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import CampaignDep, KeitaroDep, SessionDep, SettingsDep
from app.keitaro.client import KeitaroClient
from app.models import Campaign, SyncLog
from app.schemas import (
    CampaignCreate,
    CampaignImport,
    CampaignOut,
    CampaignStreamsOut,
    KeitaroCampaignRef,
    OfferAdd,
    OfferPatch,
    OfferStats,
    PushResult,
    StreamOfferOut,
    StreamOut,
    SyncLogOut,
)
from app.services import campaigns as campaign_service
from app.services import offers as offers_service
from app.services import stats as stats_service
from app.services import streams as stream_service
from app.services.logs import record_calls

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ---------------------------------------------------------------- part 1: creator


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(
    body: CampaignCreate, session: SessionDep, client: KeitaroDep, settings: SettingsDep
) -> CampaignOut:
    await offers_service.ensure_cache(session, client)
    try:
        campaign = await campaign_service.create_campaign(session, client, settings, body)
    finally:
        # Log the attempt whether or not it succeeded — a failed create is the
        # interesting case.
        await record_calls(session, client, action="create_campaign")
    return CampaignOut.model_validate(campaign)


@router.post("/import", response_model=CampaignOut, status_code=201)
async def import_campaign(
    body: CampaignImport, session: SessionDep, client: KeitaroDep
) -> CampaignOut:
    await offers_service.ensure_cache(session, client)
    try:
        campaign = await campaign_service.import_campaign(session, client, body.keitaro_id)
    finally:
        await record_calls(session, client, action="import_campaign")
    return CampaignOut.model_validate(campaign)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(session: SessionDep) -> list[CampaignOut]:
    rows = await session.execute(select(Campaign).order_by(desc(Campaign.created_at)))
    return [CampaignOut.model_validate(c) for c in rows.scalars()]


@router.get("/keitaro", response_model=list[KeitaroCampaignRef])
async def list_keitaro_campaigns(
    session: SessionDep,
    client: KeitaroDep,
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[KeitaroCampaignRef]:
    """Browse campaigns that live in Keitaro, so existing ones can be imported."""
    remote = await client.list_campaigns()
    await record_calls(session, client, action="list_keitaro_campaigns")

    imported = set(
        (await session.execute(select(Campaign.keitaro_id))).scalars()
    )
    term = (q or "").strip().lower()
    results: list[KeitaroCampaignRef] = []
    for item in remote:
        name = str(item.get("name") or "")
        if term and term not in name.lower() and term != str(item.get("id")):
            continue
        results.append(
            KeitaroCampaignRef(
                id=int(item["id"]),
                name=name,
                group=item.get("group") or None,
                state=item.get("state") or None,
                imported=int(item["id"]) in imported,
            )
        )
        if len(results) >= limit:
            break
    return results


@router.get("/{campaign_id}", response_model=CampaignOut)
async def read_campaign(campaign: CampaignDep) -> CampaignOut:
    return CampaignOut.model_validate(campaign)


@router.delete("/{campaign_id}", status_code=204, response_class=Response)
async def forget_campaign(campaign: CampaignDep, session: SessionDep) -> Response:
    """Stop managing a campaign locally. The campaign stays in Keitaro untouched."""
    await session.delete(campaign)
    return Response(status_code=204)


# ---------------------------------------------------------------- part 2: editor


async def build_view(
    campaign: Campaign,
    session: AsyncSession,
    client: KeitaroClient,
    settings: Settings,
    interval: str = stats_service.DEFAULT_INTERVAL,
) -> CampaignStreamsOut:
    """The editor's view of a campaign: staged streams plus click stats per offer.

    Every mutating endpoint returns this so the UI always renders from one authoritative
    payload instead of patching its own local copy.
    """
    resolved_interval = stats_service.normalise_interval(interval)
    campaign_stats = await stats_service.fetch_campaign_stats(
        client, campaign.keitaro_id, resolved_interval
    )
    await record_calls(session, client, action="read_stats", campaign_id=campaign.id)
    return _render(campaign, campaign_stats, resolved_interval, settings.keitaro_base_url)


@router.get("/{campaign_id}/streams", response_model=CampaignStreamsOut)
async def read_streams(
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
    interval: str = Query(default=stats_service.DEFAULT_INTERVAL),
) -> CampaignStreamsOut:
    """Current (staged) view of the campaign's streams, with click stats per offer."""
    return await build_view(campaign, session, client, settings, interval)


@router.post("/{campaign_id}/fetch", response_model=CampaignStreamsOut)
async def fetch_streams(
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
    interval: str = Query(default=stats_service.DEFAULT_INTERVAL),
) -> CampaignStreamsOut:
    """"Fetch streams from KT": hard-reset the local snapshot from the tracker."""
    await offers_service.ensure_cache(session, client)
    try:
        await stream_service.sync_streams_from_keitaro(session, client, campaign)
    finally:
        await record_calls(session, client, action="fetch_streams", campaign_id=campaign.id)
    return await build_view(campaign, session, client, settings, interval)


@router.post("/{campaign_id}/streams/{stream_id}/offers", response_model=CampaignStreamsOut)
async def add_offer(
    stream_id: str,
    body: OfferAdd,
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
) -> CampaignStreamsOut:
    stream = await stream_service.get_stream(session, campaign, stream_id)
    await offers_service.ensure_cache(session, client)
    await stream_service.add_offer(session, stream, body.offer_id)
    return await build_view(campaign, session, client, settings)


@router.delete(
    "/{campaign_id}/streams/{stream_id}/offers/{offer_id}", response_model=CampaignStreamsOut
)
async def remove_offer(
    stream_id: str,
    offer_id: int,
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
) -> CampaignStreamsOut:
    stream = await stream_service.get_stream(session, campaign, stream_id)
    await stream_service.remove_offer(session, stream, offer_id)
    return await build_view(campaign, session, client, settings)


@router.post(
    "/{campaign_id}/streams/{stream_id}/offers/{offer_id}/restore",
    response_model=CampaignStreamsOut,
)
async def restore_offer(
    stream_id: str,
    offer_id: int,
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
) -> CampaignStreamsOut:
    stream = await stream_service.get_stream(session, campaign, stream_id)
    await stream_service.restore_offer(session, stream, offer_id)
    return await build_view(campaign, session, client, settings)


@router.patch(
    "/{campaign_id}/streams/{stream_id}/offers/{offer_id}", response_model=CampaignStreamsOut
)
async def patch_offer(
    stream_id: str,
    offer_id: int,
    body: OfferPatch,
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
) -> CampaignStreamsOut:
    stream = await stream_service.get_stream(session, campaign, stream_id)
    await stream_service.patch_offer(
        session, stream, offer_id, share=body.share, pinned=body.pinned
    )
    return await build_view(campaign, session, client, settings)


@router.post("/{campaign_id}/push", response_model=PushResult)
async def push(campaign: CampaignDep, session: SessionDep, client: KeitaroDep) -> PushResult:
    try:
        applied = await stream_service.push_campaign(session, client, campaign)
    finally:
        await record_calls(session, client, action="push", campaign_id=campaign.id)
    return PushResult(
        pushed_streams=len(applied),
        applied=applied,
        campaign=CampaignOut.model_validate(campaign),
    )


@router.post("/{campaign_id}/reset", response_model=CampaignStreamsOut)
async def reset(
    campaign: CampaignDep,
    session: SessionDep,
    client: KeitaroDep,
    settings: SettingsDep,
) -> CampaignStreamsOut:
    await stream_service.reset_campaign(session, campaign)
    return await build_view(campaign, session, client, settings)


@router.get("/{campaign_id}/logs", response_model=list[SyncLogOut])
async def read_logs(
    campaign: CampaignDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SyncLogOut]:
    rows = await session.execute(
        select(SyncLog)
        .where(SyncLog.campaign_id == campaign.id)
        .order_by(desc(SyncLog.created_at))
        .limit(limit)
    )
    return [SyncLogOut.model_validate(r) for r in rows.scalars()]


# ---------------------------------------------------------------- rendering


def _render(
    campaign: Campaign,
    campaign_stats: stats_service.CampaignStats,
    interval: str,
    keitaro_base_url: str,
) -> CampaignStreamsOut:
    streams: list[StreamOut] = []
    for stream in sorted(campaign.streams, key=lambda s: s.position):
        offer_rows = [
            StreamOfferOut(
                offer_id=o.offer_id,
                offer_name=o.offer_name,
                share=o.share,
                kt_share=o.kt_share,
                pinned=o.pinned,
                removed=o.removed,
                status=o.status,
                position=o.position,
                stats=OfferStats(
                    **campaign_stats.offer(stream.keitaro_id, o.offer_id).as_dict()
                ),
            )
            for o in sorted(stream.offers, key=lambda o: (o.removed, o.position))
        ]
        streams.append(
            StreamOut(
                id=stream.id,
                keitaro_id=stream.keitaro_id,
                name=stream.name,
                schema_type=stream.schema_type,
                action_payload=stream.action_payload,
                position=stream.position,
                state=stream.state,
                filters_summary=_summarise_filters(stream.filters),
                accepts_offers=stream.accepts_offers,
                offers=offer_rows,
                stats=OfferStats(**campaign_stats.stream(stream.keitaro_id).as_dict()),
            )
        )

    return CampaignStreamsOut(
        campaign=CampaignOut.model_validate(campaign),
        streams=streams,
        has_pending_changes=stream_service.has_pending_changes(campaign),
        interval=interval,
        keitaro_url=f"{keitaro_base_url}/admin/#/campaigns/{campaign.keitaro_id}",
    )


def _summarise_filters(filters: list | None) -> str:
    if not filters:
        return "no filters"
    parts: list[str] = []
    for flt in filters:
        if not isinstance(flt, dict):
            continue
        name = flt.get("name", "?")
        mode = flt.get("mode", "?")
        payload = flt.get("payload")
        if isinstance(payload, list):
            value = ", ".join(str(p) for p in payload[:5])
            if len(payload) > 5:
                value += f" +{len(payload) - 5}"
        else:
            value = str(payload)
        parts.append(f"{name} {mode} [{value}]")
    return "; ".join(parts) or "no filters"
