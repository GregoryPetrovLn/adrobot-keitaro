"""Canned Keitaro responses, shaped exactly like the live tracker's."""

from __future__ import annotations

GROUPS = [{"id": 9799, "name": "VZ adrobot test", "position": 1, "type": "campaigns"}]
SOURCES = [{"id": 836, "name": "FORTESTS", "state": "active"}]
DOMAINS = [{"id": 1813, "name": "kylovoa.com", "is_ssl": True, "state": "active"}]

OFFERS = [
    {
        "id": 3749,
        "name": "Miaflow [BEAUTY-RO-BE_0009] [pl - - spin ro -] 2.",
        "group": "BE Miaflow RO",
        "country": ["Romania"],
        "payout_value": 0,
        "payout_currency": "USD",
        "state": "active",
    },
    {
        "id": 3717,
        "name": "Miaflow [BEAUTY-RO-BE_0008] [pl - - prize ro -] 1.",
        "group": "BE Miaflow RO",
        "country": ["Romania"],
        "payout_value": 0,
        "payout_currency": "USD",
        "state": "active",
    },
    {
        "id": 11111,
        "name": "11111 FitoMishki [Weight Loss-CO-DT_0309] [pl es -]",
        "group": "DT FitoMishki CO",
        "country": ["Colombia"],
        "payout_value": 12.5,
        "payout_currency": "USD",
        "state": "active",
    },
]


def campaign(campaign_id: int = 211939, **overrides: object) -> dict:
    base = {
        "id": campaign_id,
        "name": "test campaign",
        "alias": "test-campaign-abc123",
        "type": "position",
        "state": "active",
        "cost_type": "CPC",
        "cost_value": 0,
        "cost_currency": "USD",
        "cost_auto": True,
        "group_id": 9799,
        "group": "VZ adrobot test",
        "traffic_source_id": 836,
        "domain_id": 1813,
        "domain": "https://kylovoa.com/",
        "postbacks": [],
    }
    base.update(overrides)
    return base


def stream_offer(link_id: int, offer_id: int, share: int, stream_id: int = 1310640) -> dict:
    return {
        "id": link_id,
        "stream_id": stream_id,
        "offer_id": offer_id,
        "state": "active",
        "share": share,
        "created_at": "2026-11-16 17:17:26",
        "updated_at": "2026-11-16 17:17:26",
    }


def google_stream(stream_id: int = 1310638, geo: str = "AU") -> dict:
    return {
        "id": stream_id,
        "type": "regular",
        "name": "Flow 1",
        "campaign_id": 211939,
        "position": 1,
        "state": "active",
        "action_type": "http",
        "action_payload": "https://www.google.com",
        "schema": "redirect",
        "collect_clicks": True,
        "filters": [
            {"id": 1, "stream_id": stream_id, "name": "country", "mode": "accept",
             "payload": [geo], "oid": 1}
        ],
        "triggers": [],
        "landings": [],
        "offers": [],
    }


def offers_stream(stream_id: int = 1310640, offers: list[dict] | None = None) -> dict:
    return {
        "id": stream_id,
        "type": "regular",
        "name": "Flow 2",
        "campaign_id": 211939,
        "position": 2,
        "state": "active",
        "action_type": "http",
        "action_payload": "",
        "schema": "landings",
        "collect_clicks": True,
        "filters": [],
        "triggers": [],
        "landings": [],
        "offers": offers if offers is not None else [stream_offer(2059389, 3749, 100)],
    }


EMPTY_REPORT = {"rows": [], "total": 0}
