"""Stream editor — part 2 of the task.

Edits are *staged* locally and applied to Keitaro only when the user pushes, mirroring the
reference tool: add an offer, soft-remove one (with "bring back"), pin a share so it stays
put, let the rest rebalance, then push or cancel.

The local `StreamOffer` rows hold both sides of that: `kt_share` is what Keitaro last
reported, `share`/`pinned`/`removed` is what the user currently wants.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, KeitaroError, NotFoundError, ValidationError
from app.keitaro.client import KeitaroClient
from app.models import Campaign, Stream, StreamOffer, utcnow
from app.services import offers as offers_service
from app.services.ids import as_int
from app.services.shares import ShareSlot, rebalance, validate_total

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- fetch from Keitaro


async def sync_streams_from_keitaro(
    session: AsyncSession,
    client: KeitaroClient,
    campaign: Campaign,
    *,
    verify_exists: bool = True,
) -> None:
    """Replace the local snapshot with Keitaro's current state.

    This is the "Fetch streams from KT" button: it is a hard reset, so any staged change
    that was not pushed is discarded. The UI warns before calling it.
    """
    # Relationship access must never trigger a lazy load inside async code, so the
    # collection is explicitly (re)loaded here.
    await session.refresh(campaign, attribute_names=["streams"])

    if verify_exists:
        await _assert_campaign_exists(client, campaign)
    remote_streams = await client.list_streams(campaign.keitaro_id)
    offer_ids = {
        int(o["offer_id"])
        for s in remote_streams
        for o in (s.get("offers") or [])
        if o.get("offer_id")
    }
    names = await offers_service.get_names(session, offer_ids)

    existing = {s.keitaro_id: s for s in campaign.streams}
    seen: set[int] = set()

    for raw in remote_streams:
        kt_id = int(raw["id"])
        seen.add(kt_id)
        stream = existing.get(kt_id)
        if stream is None:
            stream = Stream(keitaro_id=kt_id, name="", schema_type="")
            campaign.streams.append(stream)

        stream.name = str(raw.get("name") or f"Stream {kt_id}")
        stream.schema_type = str(raw.get("schema") or "")
        stream.action_type = raw.get("action_type")
        stream.action_payload = raw.get("action_payload")
        stream.position = int(raw.get("position") or 0)
        stream.state = str(raw.get("state") or "active")
        stream.filters = raw.get("filters") or []

        _replace_offers(stream, raw.get("offers") or [], names)

    for kt_id, stream in list(existing.items()):
        if kt_id not in seen:
            campaign.streams.remove(stream)

    campaign.streams_fetched_at = utcnow()
    await session.flush()
    logger.info("synced %s streams for campaign %s", len(seen), campaign.keitaro_id)


def _replace_offers(stream: Stream, raw_offers: list[dict], names: dict[int, str]) -> None:
    """Reconcile a stream's offer rows with what Keitaro just reported.

    Rows are matched on `offer_id` and updated in place rather than cleared and recreated:
    a clear-then-append would make SQLAlchemy emit the INSERT before the DELETE in the same
    flush and trip the `(stream_id, offer_id)` unique constraint.
    """
    by_offer = {o.offer_id: o for o in stream.offers}
    seen: set[int] = set()

    for position, raw in enumerate(raw_offers):
        offer_id = int(raw["offer_id"])
        share = int(raw.get("share") or 0)
        seen.add(offer_id)

        row = by_offer.get(offer_id)
        if row is None:
            row = StreamOffer(offer_id=offer_id)
            stream.offers.append(row)

        row.offer_name = names.get(offer_id) or row.offer_name
        row.keitaro_link_id = as_int(raw.get("id"))
        row.kt_share = share
        row.share = share
        # A fetch is a hard reset: staged pins and removals do not survive it.
        row.pinned = False
        row.removed = False
        row.position = position

    for offer_id, row in by_offer.items():
        if offer_id not in seen:
            stream.offers.remove(row)


# ---------------------------------------------------------------- staged edits


def active_offers(stream: Stream) -> list[StreamOffer]:
    # `Stream.offers` is already ordered by position by the relationship.
    return [o for o in stream.offers if not o.removed]


def apply_rebalance(stream: Stream) -> None:
    """Recompute unpinned shares so the stream's active offers total 100%."""
    active = active_offers(stream)
    slots = [ShareSlot(key=str(o.offer_id), share=o.share, pinned=o.pinned) for o in active]
    rebalance(slots)
    for offer, slot in zip(active, slots, strict=True):
        offer.share = slot.share
    for offer in stream.offers:
        if offer.removed:
            offer.share = 0


async def add_offer(
    session: AsyncSession, stream: Stream, offer_id: int
) -> StreamOffer:
    existing = next((o for o in stream.offers if o.offer_id == offer_id), None)
    if existing is not None:
        if existing.removed:
            # Re-adding something staged for removal is just "bring back".
            existing.removed = False
            existing.pinned = False
            apply_rebalance(stream)
            await session.flush()
            return existing
        raise ConflictError(f"Offer {offer_id} is already attached to stream '{stream.name}'.")

    if not stream.accepts_offers:
        raise ValidationError(
            f"Stream '{stream.name}' is a '{stream.schema_type}' stream and cannot hold offers. "
            "Only streams with schema 'landings' accept offers."
        )

    names = await offers_service.get_names(session, {offer_id})
    next_position = max((o.position for o in stream.offers), default=-1) + 1
    offer = StreamOffer(
        offer_id=offer_id,
        offer_name=names.get(offer_id),
        keitaro_link_id=None,
        kt_share=None,
        share=0,
        pinned=False,
        removed=False,
        position=next_position,
    )
    stream.offers.append(offer)
    apply_rebalance(stream)
    await session.flush()
    return offer


async def remove_offer(session: AsyncSession, stream: Stream, offer_id: int) -> None:
    offer = _find_offer(stream, offer_id)
    if offer.kt_share is None:
        # Never existed in Keitaro — drop it outright, there is nothing to bring back to.
        # `delete-orphan` turns the detach into a DELETE at flush time.
        stream.offers.remove(offer)
    else:
        offer.removed = True
        offer.pinned = False
        offer.share = 0
    apply_rebalance(stream)
    await session.flush()


async def restore_offer(session: AsyncSession, stream: Stream, offer_id: int) -> StreamOffer:
    """Bring a staged-for-removal offer back, undoing the removal rather than re-splitting.

    Removing an offer hands its traffic to the others, so a naive restore would leave a
    70/30 stream at 50/50 — a round trip that changes the numbers is not an undo. Pinning
    the offer's Keitaro share just long enough to rebalance lets the others fall back to
    exactly what they were. The pin is then released, so the result is indistinguishable
    from never having removed the offer at all.
    """
    offer = _find_offer(stream, offer_id)
    offer.removed = False
    was_pinned = offer.pinned
    if offer.kt_share is not None:
        offer.share = offer.kt_share
        offer.pinned = True
    apply_rebalance(stream)
    offer.pinned = was_pinned
    await session.flush()
    return offer


async def patch_offer(
    session: AsyncSession,
    stream: Stream,
    offer_id: int,
    *,
    share: int | None,
    pinned: bool | None,
) -> StreamOffer:
    offer = _find_offer(stream, offer_id)
    if offer.removed:
        raise ValidationError(
            f"Offer {offer_id} is staged for removal. Bring it back before editing its share."
        )

    if pinned is not None:
        offer.pinned = pinned
    if share is not None:
        offer.share = share
        # Typing a share is an explicit intent to hold it, so pin it unless told otherwise.
        if pinned is None:
            offer.pinned = True

    apply_rebalance(stream)
    await session.flush()
    return offer


async def reset_campaign(session: AsyncSession, campaign: Campaign) -> None:
    """Discard every staged change, returning to the last state fetched from Keitaro."""
    for stream in campaign.streams:
        for offer in list(stream.offers):
            if offer.kt_share is None:
                stream.offers.remove(offer)
            else:
                offer.share = offer.kt_share
                offer.removed = False
                offer.pinned = False
    await session.flush()


def has_pending_changes(campaign: Campaign) -> bool:
    return any(o.is_dirty for s in campaign.streams for o in s.offers)


def dirty_streams(campaign: Campaign) -> list[Stream]:
    return [s for s in campaign.streams if any(o.is_dirty for o in s.offers)]


# ---------------------------------------------------------------- push to Keitaro


async def push_campaign(
    session: AsyncSession, client: KeitaroClient, campaign: Campaign
) -> list[str]:
    """Apply staged changes with one `PUT /streams/{id}` per modified stream.

    The offers array Keitaro accepts is declarative, so a single call per stream covers
    additions, removals and re-weighting at once. That also means a push computed from a
    stale snapshot would *delete* anything added in Keitaro since the last fetch, so each
    stream is re-read first and a push is refused if the tracker has moved underneath us.
    """
    changed = dirty_streams(campaign)
    if not changed:
        return []

    for stream in changed:
        active = active_offers(stream)
        slots = [ShareSlot(key=str(o.offer_id), share=o.share, pinned=o.pinned) for o in active]
        validate_total(slots)

    await _assert_campaign_exists(client, campaign)
    remote_streams = {int(s["id"]): s for s in await client.list_streams(campaign.keitaro_id)}
    for stream in changed:
        _assert_not_stale(stream, remote_streams.get(stream.keitaro_id))

    applied: list[str] = []
    for stream in changed:
        active = active_offers(stream)
        payload = [{"offer_id": o.offer_id, "share": o.share} for o in active]
        description = _describe_change(stream)
        response = await client.set_stream_offers(stream.keitaro_id, payload)
        applied.append(description)
        if not _absorb_response(stream, response):
            # The write went through but we cannot trust the echo; re-read instead of
            # guessing, so the local baseline still matches the tracker.
            logger.warning(
                "stream %s did not echo its offers on update; re-reading", stream.keitaro_id
            )
            fresh = await client.get_stream(stream.keitaro_id)
            _absorb_response(stream, fresh)

    campaign.streams_fetched_at = utcnow()
    await session.flush()
    logger.info("pushed %s streams for campaign %s", len(changed), campaign.keitaro_id)
    return applied


async def _assert_campaign_exists(client: KeitaroClient, campaign: Campaign) -> None:
    """Fail loudly if the campaign was deleted in Keitaro.

    Deleting a campaign there orphans its streams rather than removing them: they stay
    readable *and* writable, so a push would report success while writing into a campaign
    that no longer exists.
    """
    try:
        await client.get_campaign(campaign.keitaro_id)
    except KeitaroError as exc:
        if exc.keitaro_status == 404:
            raise ConflictError(
                f"Campaign {campaign.keitaro_id} no longer exists in Keitaro — it was "
                "deleted there. Use 'Forget' to drop it from this list.",
                details={"keitaro_id": campaign.keitaro_id},
            ) from exc
        raise


def _assert_not_stale(stream: Stream, remote: dict | None) -> None:
    """Refuse to push over offers that changed in Keitaro since our last fetch."""
    if remote is None:
        raise ConflictError(
            f"Stream '{stream.name}' no longer exists in Keitaro. "
            "Fetch the campaign again before pushing."
        )

    remote_shares = {
        int(o["offer_id"]): int(o.get("share") or 0) for o in (remote.get("offers") or [])
    }
    local_baseline = {
        o.offer_id: o.kt_share for o in stream.offers if o.kt_share is not None
    }
    if remote_shares != local_baseline:
        added = sorted(set(remote_shares) - set(local_baseline))
        dropped = sorted(set(local_baseline) - set(remote_shares))
        detail = []
        if added:
            detail.append(f"offers {added} were added there")
        if dropped:
            detail.append(f"offers {dropped} were removed there")
        if not detail:
            detail.append("their shares were changed there")
        raise ConflictError(
            f"Stream '{stream.name}' changed in Keitaro since you last fetched it — "
            f"{', '.join(detail)}. Pushing now would overwrite that. "
            "Use 'Fetch streams from KT' and redo your changes.",
            details={"stream_id": stream.keitaro_id},
        )


def _describe_change(stream: Stream) -> str:
    added = [o.offer_id for o in stream.offers if o.status == "added"]
    removed = [o.offer_id for o in stream.offers if o.status == "removed"]
    changed = [o.offer_id for o in stream.offers if o.status == "changed"]
    parts = [f"stream '{stream.name}'"]
    if added:
        parts.append(f"+{len(added)} offer(s) {added}")
    if removed:
        parts.append(f"-{len(removed)} offer(s) {removed}")
    if changed:
        parts.append(f"~{len(changed)} share(s) {changed}")
    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def _absorb_response(stream: Stream, response: dict) -> bool:
    """Adopt Keitaro's post-write state as the new baseline.

    Returns False when the response did not echo the stream's offers. An absent `offers`
    key is not the same as "this stream has no offers", and treating them alike would
    delete every local row and report the wipe as a successful push.
    """
    if not isinstance(response, dict) or "offers" not in response:
        return False

    remote = {int(o["offer_id"]): o for o in (response.get("offers") or [])}

    for offer in list(stream.offers):
        raw = remote.get(offer.offer_id)
        if raw is None:
            stream.offers.remove(offer)
            continue
        share = int(raw.get("share") or 0)
        offer.kt_share = share
        offer.share = share
        offer.removed = False
        offer.pinned = False
        offer.keitaro_link_id = as_int(raw.get("id"))

    for position, offer in enumerate(sorted(stream.offers, key=lambda o: o.position)):
        offer.position = position
    return True


def _find_offer(stream: Stream, offer_id: int) -> StreamOffer:
    offer = next((o for o in stream.offers if o.offer_id == offer_id), None)
    if offer is None:
        raise NotFoundError(f"Offer {offer_id} is not attached to stream '{stream.name}'.")
    return offer


async def reload(session: AsyncSession, campaign: Campaign) -> Campaign:
    """Re-read a campaign and its staged offers from the database.

    The request's session loads the campaign before the per-campaign lock is taken, so by
    the time an edit runs another request may already have committed. `populate_existing`
    makes the fresh rows overwrite what the identity map cached, which is what keeps two
    concurrent edits from each rebalancing a stale copy.
    """
    found = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign.id)
        .execution_options(populate_existing=True)
    )
    if found is None:
        raise NotFoundError(f"Campaign {campaign.id} no longer exists.")
    return found


async def get_stream(session: AsyncSession, campaign: Campaign, stream_id: str) -> Stream:
    stream = await session.scalar(
        select(Stream)
        .where(Stream.id == stream_id, Stream.campaign_id == campaign.id)
        .execution_options(populate_existing=True)
    )
    if stream is None:
        raise NotFoundError(f"Stream {stream_id} does not belong to this campaign.")
    return stream
