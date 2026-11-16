"""Failure, staleness and concurrency behaviour.

These cover the cases where doing the obvious thing would quietly corrupt state: a push
computed from a stale snapshot, a tracker that answers 200 with an HTML error page, two
overlapping edits, and the audit trail that has to survive the rollback of a failed call.
"""

from __future__ import annotations

import asyncio

import respx
from httpx import Response

from tests import fixtures as fx
from tests.conftest import KT


def _routes(mock: respx.MockRouter, offers: list[dict] | None = None) -> respx.Route:
    mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))
    mock.get(f"{KT}/campaigns/211939").mock(return_value=Response(200, json=fx.campaign()))
    mock.post(f"{KT}/report/build").mock(return_value=Response(200, json=fx.EMPTY_REPORT))
    return mock.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(200, json=[fx.google_stream(), fx.offers_stream(offers=offers)])
    )


async def _setup(api, offers: list[dict] | None = None) -> tuple[str, str]:
    campaign = (await api.post("/api/campaigns/import", json={"keitaro_id": 211939})).json()
    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    stream_id = next(s["id"] for s in body["streams"] if s["accepts_offers"])
    return campaign["id"], stream_id


def _shares(body: dict) -> dict[int, int]:
    stream = next(s for s in body["streams"] if s["accepts_offers"])
    return {o["offer_id"]: o["share"] for o in stream["offers"]}


def _total(body: dict) -> int:
    return next(s for s in body["streams"] if s["accepts_offers"])["share_total"]


# ---------------------------------------------------------------- staleness


@respx.mock
async def test_push_refuses_to_overwrite_changes_made_in_keitaro(api):
    """A declarative PUT would delete offers added in the tracker since our last fetch."""
    streams = _routes(
        respx.mock, offers=[fx.stream_offer(1, 3749, 70), fx.stream_offer(2, 3717, 30)]
    )
    campaign_id, stream_id = await _setup(api)
    await api.post(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers", json={"offer_id": 11111}
    )

    # Meanwhile, someone edits the same stream in Keitaro's own admin.
    streams.mock(
        return_value=Response(
            200,
            json=[
                fx.google_stream(),
                fx.offers_stream(
                    offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(9, 19958, 50)]
                ),
            ],
        )
    )
    put = respx.put(f"{KT}/streams/1310640")

    response = await api.post(f"/api/campaigns/{campaign_id}/push")
    assert response.status_code == 409
    message = response.json()["message"]
    assert "19958" in message and "3717" in message
    assert "Fetch streams from KT" in message
    assert not put.called, "nothing may be written once a conflict is detected"


@respx.mock
async def test_push_still_works_when_keitaro_is_unchanged(api):
    _routes(respx.mock, offers=[fx.stream_offer(1, 3749, 70), fx.stream_offer(2, 3717, 30)])
    campaign_id, stream_id = await _setup(api)
    await api.post(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers", json={"offer_id": 11111}
    )
    respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(
            200,
            json=fx.offers_stream(
                offers=[
                    fx.stream_offer(1, 3749, 34),
                    fx.stream_offer(2, 3717, 33),
                    fx.stream_offer(3, 11111, 33),
                ]
            ),
        )
    )
    response = await api.post(f"/api/campaigns/{campaign_id}/push")
    assert response.status_code == 200
    assert response.json()["pushed_streams"] == 1


# ---------------------------------------------------------------- bad responses


@respx.mock
async def test_a_push_response_without_offers_does_not_wipe_them(api):
    """An absent `offers` key means "not echoed", not "this stream is now empty"."""
    _routes(respx.mock)
    campaign_id, stream_id = await _setup(api)
    await api.post(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers", json={"offer_id": 3717}
    )

    respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(200, json={"id": 1310640, "name": "Flow 2"})
    )
    reread = respx.get(f"{KT}/streams/1310640").mock(
        return_value=Response(
            200,
            json=fx.offers_stream(
                offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(2, 3717, 50)]
            ),
        )
    )

    assert (await api.post(f"/api/campaigns/{campaign_id}/push")).status_code == 200
    assert reread.called, "an unechoed write must be reconciled by re-reading the stream"

    body = (await api.get(f"/api/campaigns/{campaign_id}/streams")).json()
    assert _shares(body) == {3749: 50, 3717: 50}
    assert body["has_pending_changes"] is False


@respx.mock
async def test_an_html_error_page_served_with_http_200_is_an_error_not_a_crash(api):
    """A proxy in front of the tracker can answer 200 with HTML; stats must degrade."""
    _routes(respx.mock)
    campaign_id, _ = await _setup(api)
    respx.post(f"{KT}/report/build").mock(
        return_value=Response(200, text="<html><body>502 Bad Gateway</body></html>")
    )

    response = await api.get(f"/api/campaigns/{campaign_id}/streams")
    assert response.status_code == 200
    body = response.json()
    assert _shares(body) == {3749: 100}
    assert next(s for s in body["streams"] if s["accepts_offers"])["stats"]["clicks"] == 0


@respx.mock
async def test_an_html_error_page_on_a_write_is_reported(api):
    _routes(respx.mock)
    respx.post(f"{KT}/campaigns").mock(return_value=Response(200, text="<html>nope</html>"))
    respx.get(f"{KT}/groups").mock(return_value=Response(200, json=fx.GROUPS))
    respx.get(f"{KT}/traffic_sources").mock(return_value=Response(200, json=fx.SOURCES))
    respx.get(f"{KT}/domains").mock(return_value=Response(200, json=fx.DOMAINS))

    response = await api.post(
        "/api/campaigns", json={"name": "x", "geo": "RO", "offer_id": 3749}
    )
    assert response.status_code == 502
    assert "non-JSON" in response.json()["message"]


# ---------------------------------------------------------------- undo


@respx.mock
async def test_bring_back_restores_the_other_offers_shares_too(api):
    """Remove + bring back is an undo: a 70/30 stream must come back as 70/30."""
    _routes(respx.mock, offers=[fx.stream_offer(1, 3749, 70), fx.stream_offer(2, 3717, 30)])
    campaign_id, stream_id = await _setup(api)
    base = f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers"

    removed = (await api.delete(f"{base}/3717")).json()
    assert _shares(removed) == {3749: 100, 3717: 0}

    restored = (await api.post(f"{base}/3717/restore")).json()
    assert _shares(restored) == {3749: 70, 3717: 30}
    assert restored["has_pending_changes"] is False, "an undo must not leave the campaign dirty"


@respx.mock
async def test_bring_back_keeps_a_removed_row_in_place(api):
    """The removed row greys out where it is rather than jumping to the bottom."""
    _routes(
        respx.mock,
        offers=[
            fx.stream_offer(1, 3749, 34),
            fx.stream_offer(2, 3717, 33),
            fx.stream_offer(3, 11111, 33),
        ],
    )
    campaign_id, stream_id = await _setup(api)
    body = (await api.delete(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers/3749"
    )).json()
    stream = next(s for s in body["streams"] if s["accepts_offers"])
    assert [o["offer_id"] for o in stream["offers"]] == [3749, 3717, 11111]


# ---------------------------------------------------------------- concurrency


@respx.mock
async def test_concurrent_edits_still_total_one_hundred(api):
    """Two overlapping adds must not each rebalance their own stale copy."""
    _routes(respx.mock)
    campaign_id, stream_id = await _setup(api)
    base = f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers"

    await asyncio.gather(
        api.post(base, json={"offer_id": 3717}),
        api.post(base, json={"offer_id": 11111}),
    )
    body = (await api.get(f"/api/campaigns/{campaign_id}/streams")).json()
    assert _total(body) == 100
    assert sorted(_shares(body)) == [3717, 3749, 11111]


@respx.mock
async def test_removing_the_same_offer_twice_is_idempotent(api):
    """Double-clicking Remove must not leave the stream at anything but 100%."""
    _routes(respx.mock, offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(2, 3717, 50)])
    campaign_id, stream_id = await _setup(api)
    base = f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers"

    responses = await asyncio.gather(api.delete(f"{base}/3717"), api.delete(f"{base}/3717"))
    assert all(r.status_code == 200 for r in responses)
    body = (await api.get(f"/api/campaigns/{campaign_id}/streams")).json()
    assert _total(body) == 100
    assert _shares(body) == {3749: 100, 3717: 0}


# ---------------------------------------------------------------- audit trail


@respx.mock
async def test_a_failed_push_is_still_recorded(api):
    """The audit log exists for failures, so it must survive the request's rollback."""
    _routes(respx.mock)
    campaign_id, stream_id = await _setup(api)
    await api.post(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers", json={"offer_id": 3717}
    )
    respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(200, json={"error": "Offer not found"})
    )

    assert (await api.post(f"/api/campaigns/{campaign_id}/push")).status_code == 502

    logs = (await api.get(f"/api/campaigns/{campaign_id}/logs")).json()
    failures = [log for log in logs if not log["ok"]]
    assert failures, "the failed PUT must appear in the audit log"
    assert failures[0]["action"] == "push"
    assert failures[0]["method"] == "PUT"
    assert "Offer not found" in failures[0]["error"]


@respx.mock
async def test_a_campaigns_creation_calls_are_filed_against_it(api):
    """Part 1's calls must be findable under the campaign they created."""
    _routes(respx.mock)
    respx.get(f"{KT}/groups").mock(return_value=Response(200, json=fx.GROUPS))
    respx.get(f"{KT}/traffic_sources").mock(return_value=Response(200, json=fx.SOURCES))
    respx.get(f"{KT}/domains").mock(return_value=Response(200, json=fx.DOMAINS))
    respx.post(f"{KT}/campaigns").mock(return_value=Response(200, json=fx.campaign()))
    respx.post(f"{KT}/streams").mock(
        side_effect=[
            Response(200, json=fx.google_stream()),
            Response(200, json=fx.offers_stream()),
        ]
    )

    campaign = (await api.post(
        "/api/campaigns", json={"name": "logged", "geo": "AU", "offer_id": 3749}
    )).json()

    logs = (await api.get(f"/api/campaigns/{campaign['id']}/logs")).json()
    paths = {log["path"] for log in logs}
    assert "campaigns" in paths
    assert "streams" in paths


# ---------------------------------------------------------------- routing


async def test_an_unknown_api_path_is_a_json_404(api):
    """The SPA catch-all must not answer API typos with an HTML page at HTTP 200."""
    response = await api.get("/api/campaignz")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@respx.mock
async def test_bring_back_leaves_no_pin_behind(api):
    """After an undo the stream must behave as if the removal never happened."""
    _routes(respx.mock, offers=[fx.stream_offer(1, 3749, 70), fx.stream_offer(2, 3717, 30)])
    campaign_id, stream_id = await _setup(api)
    base = f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers"

    await api.delete(f"{base}/3717")
    body = (await api.post(f"{base}/3717/restore")).json()
    stream = next(s for s in body["streams"] if s["accepts_offers"])
    assert not any(o["pinned"] for o in stream["offers"]), "the temporary pin must be released"

    # And the next add rebalances everything evenly, as it would have before.
    body = (await api.post(base, json={"offer_id": 11111})).json()
    assert _shares(body) == {3749: 34, 3717: 33, 11111: 33}


@respx.mock
async def test_audit_rows_do_not_fight_the_request_for_the_write_lock(api):
    """Audit rows are written after the request's transaction, not on a second connection.

    Writing them on a parallel connection deadlocks against the request's own open
    transaction on SQLite ("database is locked"), which silently lost the log again.
    """
    _routes(respx.mock)
    campaign_id, stream_id = await _setup(api)

    # A request that writes a lot and then fails: both halves must still be recorded.
    await api.post(
        f"/api/campaigns/{campaign_id}/streams/{stream_id}/offers", json={"offer_id": 3717}
    )
    respx.put(f"{KT}/streams/1310640").mock(return_value=Response(500, text="boom"))
    assert (await api.post(f"/api/campaigns/{campaign_id}/push")).status_code == 502

    logs = (await api.get(f"/api/campaigns/{campaign_id}/logs")).json()
    assert logs, "no audit rows were written at all"
    assert all(log["id"] and log["created_at"] for log in logs), "defaults must be applied"
    assert any(not log["ok"] for log in logs), "the failed call must be there"
