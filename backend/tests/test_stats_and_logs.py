"""Stats mapping and the audit trail."""

from __future__ import annotations

import respx
from httpx import Response

from app.services.stats import ALLOWED_INTERVALS, normalise_interval
from tests import fixtures as fx
from tests.conftest import KT

STREAM_ROWS = {
    "rows": [{"stream_id": 1310640, "clicks": 13, "campaign_unique_clicks": 4, "bots": 2,
              "conversions": 1, "leads": 1, "sales": 0, "revenue": 7.5}],
    "total": 1,
}
OFFER_ROWS = {
    "rows": [
        {"stream_id": 1310640, "offer_id": 3749, "clicks": 9, "campaign_unique_clicks": 3,
         "bots": 1, "conversions": 1, "leads": 1, "sales": 0, "revenue": 7.5},
        # offer_id 0 = the click never reached an offer; it must not become a row.
        {"stream_id": 1310640, "offer_id": 0, "clicks": 4, "campaign_unique_clicks": 1,
         "bots": 1, "conversions": 0, "leads": 0, "sales": 0, "revenue": 0},
    ],
    "total": 2,
}


def _routes(mock: respx.MockRouter, reports: list[Response]) -> None:
    mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))
    mock.get(f"{KT}/campaigns/211939").mock(return_value=Response(200, json=fx.campaign()))
    mock.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(200, json=[fx.google_stream(), fx.offers_stream()])
    )
    mock.post(f"{KT}/report/build").mock(side_effect=reports)


@respx.mock
async def test_stats_land_on_the_right_stream_and_offer(api):
    _routes(
        respx.mock,
        # Importing a campaign reads no reports; the two calls below belong to GET /streams.
        [Response(200, json=STREAM_ROWS), Response(200, json=OFFER_ROWS)],
    )
    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()

    offers_stream = body["streams"][1]
    assert offers_stream["stats"]["clicks"] == 13
    assert offers_stream["stats"]["unique_clicks"] == 4
    assert offers_stream["stats"]["bots"] == 2

    row = offers_stream["offers"][0]
    assert row["offer_id"] == 3749
    assert row["stats"] == {"clicks": 9, "unique_clicks": 3, "bots": 1, "conversions": 1,
                            "leads": 1, "sales": 0, "revenue": 7.5}

    # The redirect stream had no rows, so it reports zeroes rather than borrowing them.
    assert body["streams"][0]["stats"]["clicks"] == 0


@respx.mock
async def test_the_page_still_renders_when_the_report_builder_fails(api):
    _routes(
        respx.mock,
        [Response(500, text="boom"), Response(500, text="boom")],
    )
    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    response = await api.get(f"/api/campaigns/{campaign['id']}/streams")
    assert response.status_code == 200
    assert response.json()["streams"][1]["stats"]["clicks"] == 0


@respx.mock
async def test_every_keitaro_call_is_audited(api):
    _routes(respx.mock, [Response(200, json=fx.EMPTY_REPORT)] * 4)
    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    await api.get(f"/api/campaigns/{campaign['id']}/streams")

    logs = (await api.get(f"/api/campaigns/{campaign['id']}/logs")).json()
    assert logs, "expected the campaign's Keitaro calls to be recorded"
    assert {log["action"] for log in logs} >= {"read_stats"}
    assert all(log["ok"] for log in logs)
    assert all(log["duration_ms"] >= 0 for log in logs)


def test_unknown_intervals_fall_back_to_today():
    assert normalise_interval("this_week_or_so") == "today"
    assert normalise_interval(None) == "today"
    assert normalise_interval("7_days_ago") == "7_days_ago"


def test_keitaros_rejected_interval_spellings_are_not_allowed():
    """Keitaro names these "<n>_days_ago"; "last_7_days" makes it fail to parse a date."""
    for rejected in ("last_7_days", "last_30_days", "current_month"):
        assert rejected not in ALLOWED_INTERVALS
        assert normalise_interval(rejected) == "today"


def test_the_ui_interval_options_are_all_accepted_by_keitaro():
    """Keep the frontend dropdown and the server-side allowlist in sync."""
    ui_options = {
        "today", "yesterday", "this_week", "7_days_ago",
        "30_days_ago", "this_month", "last_month", "all_time",
    }
    assert ui_options <= ALLOWED_INTERVALS


@respx.mock
async def test_timestamps_are_always_serialised_as_utc(api):
    """Values that round-trip through SQLite come back naive; the API must still say UTC."""
    _routes(respx.mock, [Response(200, json=fx.EMPTY_REPORT)] * 4)
    created = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    assert created["created_at"].endswith("Z")

    reread = (await api.get("/api/campaigns")).json()[0]
    assert reread["created_at"].endswith("Z")
    assert reread["created_at"] == created["created_at"]
    assert reread["streams_fetched_at"].endswith("Z")


@respx.mock
async def test_editing_reuses_recent_stats_instead_of_rebuilding_reports(api):
    """Retyping shares must not hammer Keitaro's heaviest endpoint once per click."""
    reports = respx.mock.post(f"{KT}/report/build").mock(
        return_value=Response(200, json=STREAM_ROWS)
    )
    respx.mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))
    respx.mock.get(f"{KT}/campaigns/211939").mock(return_value=Response(200, json=fx.campaign()))
    respx.mock.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(
            200,
            json=[
                fx.google_stream(),
                fx.offers_stream(
                    offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(2, 3717, 50)]
                ),
            ],
        )
    )

    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    await api.get(f"/api/campaigns/{campaign['id']}/streams")
    after_first_read = reports.call_count
    assert after_first_read == 2, "one read builds the stream and the stream+offer reports"

    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    assert reports.call_count == after_first_read, "a second read inside the TTL is served warm"
    # The cached numbers are still the real ones, not zeros.
    assert next(s for s in body["streams"] if s["accepts_offers"])["stats"]["clicks"] == 13


@respx.mock
async def test_fetch_from_keitaro_always_rebuilds_the_reports(api):
    """"Fetch streams from KT" means "show me the truth", so it bypasses the cache."""
    reports = respx.mock.post(f"{KT}/report/build").mock(
        return_value=Response(200, json=fx.EMPTY_REPORT)
    )
    _routes(respx.mock, [Response(200, json=fx.EMPTY_REPORT)] * 2)
    respx.mock.post(f"{KT}/report/build").mock(return_value=Response(200, json=fx.EMPTY_REPORT))

    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    await api.get(f"/api/campaigns/{campaign['id']}/streams")
    warm = reports.call_count

    await api.post(f"/api/campaigns/{campaign['id']}/fetch")
    assert reports.call_count > warm
