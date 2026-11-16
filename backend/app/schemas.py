"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services import geo


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- dictionaries


class NamedRef(BaseModel):
    id: int
    name: str


class DomainRef(BaseModel):
    id: int
    name: str
    url: str


class Dictionaries(BaseModel):
    groups: list[NamedRef]
    traffic_sources: list[NamedRef]
    domains: list[DomainRef]
    defaults: "DictionaryDefaults"


class DictionaryDefaults(BaseModel):
    group_id: int | None = None
    traffic_source_id: int | None = None
    domain_id: int | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class Country(BaseModel):
    code: str
    name: str


# ---------------------------------------------------------------- offers


class OfferOut(ORMModel):
    id: int
    name: str
    group_name: str | None = None
    countries: list[str] | None = None
    payout_value: float | None = None
    payout_currency: str | None = None


class OfferSyncResult(BaseModel):
    synced: int
    synced_at: datetime


# ---------------------------------------------------------------- campaigns


class CampaignCreate(BaseModel):
    """Part 1 of the task: name + geo + offer is all the user has to supply."""

    name: str = Field(min_length=1, max_length=200)
    geo: str = Field(min_length=2, max_length=2, description="ISO-3166 alpha-2 country code")
    offer_id: int = Field(gt=0)
    group_id: int | None = None
    traffic_source_id: int | None = None
    domain_id: int | None = None

    @field_validator("geo")
    @classmethod
    def _valid_geo(cls, v: str) -> str:
        code = v.strip().upper()
        if not geo.is_valid(code):
            raise ValueError(f"'{v}' is not a known ISO-3166 alpha-2 country code")
        return code

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return v.strip()


class CampaignImport(BaseModel):
    keitaro_id: int = Field(gt=0)


class CampaignOut(ORMModel):
    id: str
    keitaro_id: int
    name: str
    alias: str | None = None
    geo: str | None = None
    group_name: str | None = None
    traffic_source_name: str | None = None
    domain_name: str | None = None
    campaign_url: str | None = None
    created_by_app: bool
    streams_fetched_at: datetime | None = None
    created_at: datetime


class KeitaroCampaignRef(BaseModel):
    id: int
    name: str
    group: str | None = None
    state: str | None = None
    imported: bool = False


# ---------------------------------------------------------------- streams / offers


class OfferStats(BaseModel):
    clicks: int = 0
    unique_clicks: int = 0
    bots: int = 0
    conversions: int = 0
    leads: int = 0
    sales: int = 0
    revenue: float = 0.0


class StreamOfferOut(BaseModel):
    offer_id: int
    offer_name: str | None = None
    share: int
    kt_share: int | None = None
    pinned: bool
    removed: bool
    status: str
    position: int
    stats: OfferStats = Field(default_factory=OfferStats)


class StreamOut(BaseModel):
    id: str
    keitaro_id: int
    name: str
    schema_type: str
    action_payload: str | None = None
    position: int
    state: str
    filters_summary: str
    accepts_offers: bool
    offers: list[StreamOfferOut]
    stats: OfferStats = Field(default_factory=OfferStats)


class CampaignStreamsOut(BaseModel):
    campaign: CampaignOut
    streams: list[StreamOut]
    has_pending_changes: bool
    interval: str
    keitaro_url: str


class OfferAdd(BaseModel):
    offer_id: int = Field(gt=0)


class OfferPatch(BaseModel):
    share: int | None = Field(default=None, ge=0, le=100)
    pinned: bool | None = None


class PushResult(BaseModel):
    pushed_streams: int
    applied: list[str]
    campaign: CampaignOut


# ---------------------------------------------------------------- logs


class SyncLogOut(ORMModel):
    id: str
    action: str
    method: str
    path: str
    status_code: int | None
    ok: bool
    duration_ms: int
    error: str | None
    created_at: datetime
    request_body: dict | list | None = None
    response_body: dict | list | str | None = None


Dictionaries.model_rebuild()
