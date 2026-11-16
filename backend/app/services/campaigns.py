"""Campaign creation and import — part 1 of the task.

A created campaign is the shape the brief asks for:

    Flow 1  "Google"  schema=redirect  -> https://www.google.com, filter: country accept [GEO]
    Flow 2  "Offers"  schema=landings  -> the chosen offer at 100%

plus a configured domain, campaign group and traffic source.
"""

from __future__ import annotations

import logging
import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ConflictError, KeitaroError, NotFoundError, ValidationError
from app.keitaro.client import KeitaroClient
from app.models import Campaign, utcnow
from app.schemas import CampaignCreate
from app.services import geo
from app.services.streams import sync_streams_from_keitaro

logger = logging.getLogger(__name__)

GOOGLE_STREAM_NAME = "Flow 1"
OFFERS_STREAM_NAME = "Flow 2"


def make_alias(name: str) -> str:
    """A URL-safe, unique-ish campaign alias derived from the name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "campaign"
    return f"{slug}-{secrets.token_hex(3)}"


def build_campaign_payload(
    data: CampaignCreate, settings: Settings, *, alias: str
) -> dict[str, object]:
    name = f"{settings.campaign_name_prefix}{data.name}"
    payload: dict[str, object] = {
        "name": name,
        "alias": alias,
        "type": "position",          # flows are evaluated top-down, which the brief relies on
        "state": "active",
        "cost_type": "CPC",
        "cost_value": 0,
        "cost_currency": "USD",
        "cost_auto": True,           # "Из параметра 'cost'"
        "uniqueness_method": "ip_ua",
        "cookies_ttl": 24,
    }
    group_id = data.group_id if data.group_id is not None else settings.default_group_id
    source_id = (
        data.traffic_source_id
        if data.traffic_source_id is not None
        else settings.default_traffic_source_id
    )
    domain_id = data.domain_id if data.domain_id is not None else settings.default_domain_id

    if group_id is not None:
        payload["group_id"] = group_id
    if source_id is not None:
        payload["traffic_source_id"] = source_id
    if domain_id is not None:
        payload["domain_id"] = domain_id
    return payload


def build_google_stream_payload(
    campaign_id: int, geo_code: str, redirect_url: str
) -> dict[str, object]:
    return {
        "campaign_id": campaign_id,
        "name": GOOGLE_STREAM_NAME,
        "type": "regular",
        "schema": "redirect",
        "action_type": "http",
        "action_payload": redirect_url,
        "position": 1,
        "state": "active",
        "collect_clicks": True,
        "filters": [{"name": "country", "mode": "accept", "payload": [geo_code]}],
    }


def build_offers_stream_payload(campaign_id: int, offer_id: int) -> dict[str, object]:
    return {
        "campaign_id": campaign_id,
        "name": OFFERS_STREAM_NAME,
        "type": "regular",
        "schema": "landings",
        # Keitaro validates `action_type` even for offer streams; "http" with an empty
        # payload is exactly what the tracker UI submits.
        "action_type": "http",
        "action_payload": "",
        "position": 2,
        "state": "active",
        "collect_clicks": True,
        "offers": [{"offer_id": offer_id, "share": 100}],
    }


async def create_campaign(
    session: AsyncSession,
    client: KeitaroClient,
    settings: Settings,
    data: CampaignCreate,
) -> Campaign:
    """Create the campaign and both flows in Keitaro, then mirror it locally.

    If a flow fails to create, the half-built campaign is deleted from Keitaro so the user
    is not left with a broken campaign to clean up by hand.
    """
    dictionaries = await _resolve_dictionaries(client, settings, data)

    alias = make_alias(data.name)
    payload = build_campaign_payload(data, settings, alias=alias)
    created = await client.create_campaign(payload)
    keitaro_id = int(created["id"])
    logger.info("created keitaro campaign %s (%s)", keitaro_id, created.get("name"))

    try:
        await client.create_stream(
            build_google_stream_payload(keitaro_id, data.geo, settings.google_redirect_url)
        )
        await client.create_stream(build_offers_stream_payload(keitaro_id, data.offer_id))
    except KeitaroError:
        logger.exception("stream creation failed, rolling back campaign %s", keitaro_id)
        await _rollback_campaign(client, keitaro_id)
        raise

    campaign = Campaign(
        keitaro_id=keitaro_id,
        name=str(created.get("name") or data.name),
        alias=str(created.get("alias") or alias),
        geo=data.geo,
        group_id=_as_int_or_none(created.get("group_id")),
        group_name=dictionaries["group_name"],
        traffic_source_id=_as_int_or_none(created.get("traffic_source_id")),
        traffic_source_name=dictionaries["source_name"],
        domain_id=_as_int_or_none(created.get("domain_id")),
        domain_name=_domain_name(created.get("domain")) or dictionaries["domain_name"],
        campaign_url=_campaign_url(created),
        created_by_app=True,
    )
    session.add(campaign)
    await session.flush()

    await sync_streams_from_keitaro(session, client, campaign)
    campaign.streams_fetched_at = utcnow()
    return campaign


async def import_campaign(
    session: AsyncSession, client: KeitaroClient, keitaro_id: int
) -> Campaign:
    """Pull an existing Keitaro campaign under this app's management."""
    existing = await session.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if existing is not None:
        raise ConflictError(
            f"Campaign {keitaro_id} is already imported.", details={"campaign_id": existing.id}
        )

    try:
        remote = await client.get_campaign(keitaro_id)
    except KeitaroError as exc:
        if exc.keitaro_status == 404:
            raise NotFoundError(f"Campaign {keitaro_id} does not exist in Keitaro.") from exc
        raise

    campaign = Campaign(
        keitaro_id=keitaro_id,
        name=str(remote.get("name") or f"Campaign {keitaro_id}"),
        alias=remote.get("alias"),
        geo=None,
        group_id=_as_int_or_none(remote.get("group_id")),
        group_name=remote.get("group") or None,
        traffic_source_id=_as_int_or_none(remote.get("traffic_source_id")),
        domain_id=_as_int_or_none(remote.get("domain_id")),
        domain_name=_domain_name(remote.get("domain")),
        campaign_url=_campaign_url(remote),
        created_by_app=False,
    )
    session.add(campaign)
    await session.flush()

    await sync_streams_from_keitaro(session, client, campaign)
    campaign.streams_fetched_at = utcnow()
    campaign.geo = _infer_geo(campaign)
    return campaign


async def _resolve_dictionaries(
    client: KeitaroClient, settings: Settings, data: CampaignCreate
) -> dict[str, str | None]:
    """Validate the chosen group/source/domain and capture their display names."""
    group_id = data.group_id if data.group_id is not None else settings.default_group_id
    source_id = (
        data.traffic_source_id
        if data.traffic_source_id is not None
        else settings.default_traffic_source_id
    )
    domain_id = data.domain_id if data.domain_id is not None else settings.default_domain_id

    if source_id is None:
        raise ValidationError(
            "A traffic source is required. Pick one in the form or set "
            "DEFAULT_TRAFFIC_SOURCE_ID in .env."
        )
    if domain_id is None:
        raise ValidationError(
            "A domain is required. Pick one in the form or set DEFAULT_DOMAIN_ID in .env."
        )
    if group_id is None:
        raise ValidationError(
            "A campaign group is required. Pick or create one in the form, or set "
            "DEFAULT_GROUP_ID in .env."
        )

    groups = {int(g["id"]): str(g.get("name") or "") for g in await client.list_campaign_groups()}
    sources = {int(s["id"]): str(s.get("name") or "") for s in await client.list_traffic_sources()}
    domains = {int(d["id"]): str(d.get("name") or "") for d in await client.list_domains()}

    if group_id not in groups:
        raise ValidationError(f"Campaign group {group_id} does not exist in Keitaro.")
    if source_id not in sources:
        raise ValidationError(f"Traffic source {source_id} does not exist in Keitaro.")
    if domain_id not in domains:
        raise ValidationError(f"Domain {domain_id} does not exist in Keitaro.")

    return {
        "group_name": groups[group_id],
        "source_name": sources[source_id],
        "domain_name": domains[domain_id],
    }


async def _rollback_campaign(client: KeitaroClient, keitaro_id: int) -> None:
    try:
        await client.delete_campaign(keitaro_id)
    except KeitaroError:
        # Best effort: the original failure is the one worth reporting.
        logger.warning("could not roll back campaign %s", keitaro_id)


def _infer_geo(campaign: Campaign) -> str | None:
    """Read the geo back off an imported campaign's country filter, if it has one."""
    for stream in campaign.streams:
        for flt in stream.filters or []:
            if isinstance(flt, dict) and flt.get("name") == "country":
                payload = flt.get("payload")
                if isinstance(payload, list) and payload:
                    code = str(payload[0]).upper()
                    if geo.is_valid(code):
                        return code
    return None


def _campaign_url(remote: dict) -> str | None:
    domain = remote.get("domain")
    alias = remote.get("alias")
    if not domain or not alias:
        return None
    return f"{str(domain).rstrip('/')}/{alias}"


def _domain_name(domain: object) -> str | None:
    if not domain:
        return None
    return str(domain).replace("https://", "").replace("http://", "").strip("/") or None


def _as_int_or_none(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed or None
