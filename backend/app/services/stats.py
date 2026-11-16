"""Per-stream and per-offer click statistics from Keitaro's report builder."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.errors import KeitaroError
from app.keitaro.client import KeitaroClient

logger = logging.getLogger(__name__)

METRICS = [
    "clicks",
    "campaign_unique_clicks",
    "bots",
    "conversions",
    "leads",
    "sales",
    "revenue",
]

# Intervals accepted by Keitaro's report/build `range.interval`, verified against the
# tracker. Note the naming: it is "7_days_ago", not "last_7_days" — Keitaro rejects the
# latter with a confusing "The timezone could not be found in the database" error, because
# it falls through to parsing the value as a free-form date string.
ALLOWED_INTERVALS = {
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "7_days_ago",
    "14_days_ago",
    "30_days_ago",
    "this_month",
    "last_month",
    "all_time",
}

DEFAULT_INTERVAL = "today"


@dataclass
class Stats:
    clicks: int = 0
    unique_clicks: int = 0
    bots: int = 0
    conversions: int = 0
    leads: int = 0
    sales: int = 0
    revenue: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "clicks": self.clicks,
            "unique_clicks": self.unique_clicks,
            "bots": self.bots,
            "conversions": self.conversions,
            "leads": self.leads,
            "sales": self.sales,
            "revenue": round(self.revenue, 2),
        }


@dataclass
class CampaignStats:
    """Report rows indexed the way the UI needs them."""

    by_stream: dict[int, Stats] = field(default_factory=dict)
    by_stream_offer: dict[tuple[int, int], Stats] = field(default_factory=dict)
    available: bool = True
    error: str | None = None

    def stream(self, stream_id: int) -> Stats:
        return self.by_stream.get(stream_id, Stats())

    def offer(self, stream_id: int, offer_id: int) -> Stats:
        return self.by_stream_offer.get((stream_id, offer_id), Stats())


def normalise_interval(interval: str | None) -> str:
    if not interval:
        return DEFAULT_INTERVAL
    value = interval.strip()
    return value if value in ALLOWED_INTERVALS else DEFAULT_INTERVAL


async def fetch_campaign_stats(
    client: KeitaroClient, keitaro_campaign_id: int, interval: str = DEFAULT_INTERVAL
) -> CampaignStats:
    """Fetch click stats for one campaign, grouped by stream and by stream+offer.

    Statistics are a nice-to-have on an editor screen: if the report builder is unavailable
    we return empty counters flagged `available=False` rather than failing the whole page.
    """
    result = CampaignStats()
    filters = [
        {"name": "campaign_id", "operator": "EQUALS", "expression": str(keitaro_campaign_id)}
    ]

    try:
        stream_report = await client.build_report(
            grouping=["stream_id"], metrics=METRICS, interval=interval, filters=filters
        )
        offer_report = await client.build_report(
            grouping=["stream_id", "offer_id"], metrics=METRICS, interval=interval, filters=filters
        )
    except KeitaroError as exc:
        logger.warning("stats unavailable for campaign %s: %s", keitaro_campaign_id, exc.message)
        return CampaignStats(available=False, error=exc.message)

    for row in stream_report.get("rows", []):
        stream_id = _as_int(row.get("stream_id"))
        if stream_id:
            result.by_stream[stream_id] = _row_to_stats(row)

    for row in offer_report.get("rows", []):
        stream_id = _as_int(row.get("stream_id"))
        offer_id = _as_int(row.get("offer_id"))
        # offer_id 0 means the click never reached an offer (bot, filter, redirect stream).
        if stream_id and offer_id:
            result.by_stream_offer[(stream_id, offer_id)] = _row_to_stats(row)

    return result


def _row_to_stats(row: dict) -> Stats:
    return Stats(
        clicks=_as_int(row.get("clicks")),
        unique_clicks=_as_int(row.get("campaign_unique_clicks")),
        bots=_as_int(row.get("bots")),
        conversions=_as_int(row.get("conversions")),
        leads=_as_int(row.get("leads")),
        sales=_as_int(row.get("sales")),
        revenue=_as_float(row.get("revenue")),
    )


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
