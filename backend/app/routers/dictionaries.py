"""Keitaro reference data used by the campaign form."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import KeitaroDep, SessionDep, SettingsDep
from app.schemas import (
    Country,
    DictionaryDefaults,
    Dictionaries,
    DomainRef,
    GroupCreate,
    NamedRef,
)
from app.services import geo
from app.services.logs import record_calls

router = APIRouter(tags=["dictionaries"])


@router.get("/dictionaries", response_model=Dictionaries)
async def read_dictionaries(
    client: KeitaroDep, session: SessionDep, settings: SettingsDep
) -> Dictionaries:
    groups = await client.list_campaign_groups()
    sources = await client.list_traffic_sources()
    domains = await client.list_domains()
    await record_calls(session, client, action="dictionaries")

    return Dictionaries(
        groups=[NamedRef(id=int(g["id"]), name=str(g.get("name") or "")) for g in groups],
        traffic_sources=[
            NamedRef(id=int(s["id"]), name=str(s.get("name") or "")) for s in sources
        ],
        domains=[
            DomainRef(
                id=int(d["id"]),
                name=str(d.get("name") or ""),
                url=f"https://{d.get('name')}" if d.get("is_ssl") else f"http://{d.get('name')}",
            )
            for d in domains
        ],
        defaults=DictionaryDefaults(
            group_id=settings.default_group_id,
            traffic_source_id=settings.default_traffic_source_id,
            domain_id=settings.default_domain_id,
        ),
    )


@router.post("/dictionaries/groups", response_model=NamedRef, status_code=201)
async def create_group(body: GroupCreate, client: KeitaroDep, session: SessionDep) -> NamedRef:
    created = await client.create_campaign_group(body.name)
    await record_calls(session, client, action="create_group")
    return NamedRef(id=int(created["id"]), name=str(created.get("name") or body.name))


@router.get("/countries", response_model=list[Country])
async def list_countries() -> list[Country]:
    return [Country(code=code, name=name) for code, name in geo.COUNTRIES]
