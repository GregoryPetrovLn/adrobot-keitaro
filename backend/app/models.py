"""SQLAlchemy models.

The local database is a *staging area* in front of Keitaro, not a second source of truth:

* `Campaign`/`Stream`/`StreamOffer` mirror what we last read from Keitaro and hold the
  user's pending edits until they press "Push to Keitaro".
* `Offer` is a read-through cache of the tracker's offer catalogue, because
  `GET /admin_api/v1/offers` ignores `name`/`limit` filters and always returns the full
  list — searching it locally is what makes the autocomplete usable.
* `SyncLog` records every outbound Keitaro call so failures are debuggable after the fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Campaign(Base, TimestampMixin):
    """A Keitaro campaign this app manages (created here, or imported from the tracker)."""

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    geo: Mapped[str | None] = mapped_column(String(8), nullable=True)

    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    campaign_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by_app: Mapped[bool] = mapped_column(Boolean, default=False)
    streams_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    streams: Mapped[list[Stream]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="Stream.position",
        lazy="selectin",
    )


class Stream(Base, TimestampMixin):
    """A Keitaro stream (поток) belonging to a campaign."""

    __tablename__ = "streams"
    __table_args__ = (UniqueConstraint("campaign_id", "keitaro_id", name="uq_stream_campaign_kt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    keitaro_id: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(255))
    schema_type: Mapped[str] = mapped_column(String(32))  # redirect | landings | action
    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(32), default="active")
    filters: Mapped[list | None] = mapped_column(JSON, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list[StreamOffer]] = relationship(
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="StreamOffer.position",
        lazy="selectin",
    )

    @property
    def accepts_offers(self) -> bool:
        return self.schema_type == "landings"


class StreamOffer(Base, TimestampMixin):
    """One offer attached to a stream, plus the pending edit staged on top of it.

    `kt_share` is the share Keitaro last reported; `share` is what the user currently wants.
    `kt_share is None` means the row exists only locally (a pending addition).
    """

    __tablename__ = "stream_offers"
    __table_args__ = (
        UniqueConstraint("stream_id", "offer_id", name="uq_stream_offer"),
        Index("ix_stream_offers_stream", "stream_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    stream_id: Mapped[str] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"))
    offer_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    keitaro_link_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kt_share: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    stream: Mapped[Stream] = relationship(back_populates="offers")

    @property
    def status(self) -> str:
        if self.removed:
            return "removed"
        if self.kt_share is None:
            return "added"
        if self.share != self.kt_share:
            return "changed"
        return "synced"

    @property
    def is_dirty(self) -> bool:
        return self.status != "synced"


class Offer(Base):
    """Local cache of the Keitaro offer catalogue (used for autocomplete)."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Keitaro offer id
    name: Mapped[str] = mapped_column(String(512), index=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    countries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    payout_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    payout_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncLog(Base):
    """Audit trail of outbound Keitaro calls."""

    __tablename__ = "sync_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_body: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    response_body: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
