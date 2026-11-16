"""Part 2: staged offer editing, rebalancing, push and cancel."""

from __future__ import annotations

import json

import respx
from httpx import Response

from tests import fixtures as fx
from tests.conftest import KT


def _base_routes(mock: respx.MockRouter, offers: list[dict] | None = None) -> None:
    mock.get(f"{KT}/groups").mock(return_value=Response(200, json=fx.GROUPS))
    mock.get(f"{KT}/traffic_sources").mock(return_value=Response(200, json=fx.SOURCES))
    mock.get(f"{KT}/domains").mock(return_value=Response(200, json=fx.DOMAINS))
    mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))
    mock.post(f"{KT}/report/build").mock(return_value=Response(200, json=fx.EMPTY_REPORT))
    mock.get(f"{KT}/campaigns/211939").mock(return_value=Response(200, json=fx.campaign()))
    mock.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(
            200, json=[fx.google_stream(), fx.offers_stream(offers=offers)]
        )
    )


async def _import(api) -> dict:
    response = await api.post("/api/campaigns/import", json={"keitaro_id": 211939})
    assert response.status_code == 201, response.text
    return response.json()


def _offers_stream_of(body: dict) -> dict:
    return next(s for s in body["streams"] if s["schema_type"] == "landings")


def _shares(body: dict) -> dict[int, int]:
    return {o["offer_id"]: o["share"] for o in _offers_stream_of(body)["offers"]}


@respx.mock
async def test_import_reads_streams_and_infers_geo(api):
    _base_routes(respx.mock)
    campaign = await _import(api)
    assert campaign["geo"] == "AU"  # read back off Flow 1's country filter

    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    assert [s["name"] for s in body["streams"]] == ["Flow 1", "Flow 2"]

    google = body["streams"][0]
    assert google["accepts_offers"] is False
    assert google["filters_summary"] == "country accept [AU]"
    assert google["action_payload"] == "https://www.google.com"

    offers_stream = body["streams"][1]
    assert offers_stream["accepts_offers"] is True
    assert offers_stream["offers"][0]["offer_id"] == 3749
    assert offers_stream["offers"][0]["share"] == 100
    assert offers_stream["offers"][0]["status"] == "synced"
    assert offers_stream["offers"][0]["offer_name"].startswith("Miaflow")
    assert body["has_pending_changes"] is False


@respx.mock
async def test_adding_offers_rebalances_without_touching_keitaro(api):
    _base_routes(respx.mock)
    pushed = respx.put(f"{KT}/streams/1310640")
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    url = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"

    body = (await api.post(url, json={"offer_id": 3717})).json()
    assert _shares(body) == {3749: 50, 3717: 50}

    body = (await api.post(url, json={"offer_id": 11111})).json()
    assert _shares(body) == {3749: 34, 3717: 33, 11111: 33}
    assert body["has_pending_changes"] is True

    statuses = {o["offer_id"]: o["status"] for o in _offers_stream_of(body)["offers"]}
    assert statuses == {3749: "changed", 3717: "added", 11111: "added"}

    # Nothing has reached the tracker yet — that is what "Push to KT" is for.
    assert not pushed.called


@respx.mock
async def test_remove_is_soft_and_can_be_brought_back(api):
    _base_routes(
        respx.mock,
        offers=[
            fx.stream_offer(1, 3749, 50),
            fx.stream_offer(2, 3717, 50),
        ],
    )
    campaign = await _import(api)
    first = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    stream_id = _offers_stream_of(first)["id"]
    base = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"

    body = (await api.delete(f"{base}/3717")).json()
    rows = {o["offer_id"]: o for o in _offers_stream_of(body)["offers"]}
    assert rows[3717]["removed"] is True
    assert rows[3717]["status"] == "removed"
    assert rows[3717]["share"] == 0
    assert rows[3749]["share"] == 100  # survivor absorbs the freed traffic

    body = (await api.post(f"{base}/3717/restore")).json()
    assert _shares(body) == {3749: 50, 3717: 50}
    assert body["has_pending_changes"] is False


@respx.mock
async def test_newly_added_offer_is_dropped_outright_when_removed(api):
    """An offer that never reached Keitaro has nothing to "bring back" to."""
    _base_routes(respx.mock)
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    base = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"

    await api.post(base, json={"offer_id": 3717})
    body = (await api.delete(f"{base}/3717")).json()
    assert _shares(body) == {3749: 100}
    assert body["has_pending_changes"] is False


@respx.mock
async def test_pinning_holds_a_share_while_the_rest_rebalance(api):
    _base_routes(
        respx.mock,
        offers=[
            fx.stream_offer(1, 3749, 34),
            fx.stream_offer(2, 3717, 33),
            fx.stream_offer(3, 11111, 33),
        ],
    )
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    base = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"

    # Setting a share pins it by default.
    body = (await api.patch(f"{base}/11111", json={"share": 50})).json()
    assert _shares(body) == {3749: 25, 3717: 25, 11111: 50}
    rows = {o["offer_id"]: o for o in _offers_stream_of(body)["offers"]}
    assert rows[11111]["pinned"] is True

    # Removing an unpinned offer leaves the pin untouched.
    body = (await api.delete(f"{base}/3717")).json()
    assert _shares(body) == {3749: 50, 3717: 0, 11111: 50}

    # Unpinning hands the stream back to even distribution.
    body = (await api.patch(f"{base}/11111", json={"pinned": False})).json()
    assert _shares(body) == {3749: 50, 3717: 0, 11111: 50}


@respx.mock
async def test_push_sends_one_declarative_put_per_changed_stream(api):
    _base_routes(respx.mock)
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    base = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"
    await api.post(base, json={"offer_id": 3717})

    put = respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(
            200,
            json=fx.offers_stream(
                offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(2, 3717, 50)]
            ),
        )
    )

    response = await api.post(f"/api/campaigns/{campaign['id']}/push")
    assert response.status_code == 200, response.text
    assert response.json()["pushed_streams"] == 1

    assert put.call_count == 1
    payload = json.loads(put.calls[0].request.content)
    assert payload == {"offers": [{"offer_id": 3749, "share": 50}, {"offer_id": 3717, "share": 50}]}

    # Keitaro's response becomes the new baseline, so nothing is pending afterwards.
    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    assert body["has_pending_changes"] is False
    assert _shares(body) == {3749: 50, 3717: 50}


@respx.mock
async def test_push_omits_removed_offers_and_forgets_them_locally(api):
    _base_routes(
        respx.mock,
        offers=[fx.stream_offer(1, 3749, 50), fx.stream_offer(2, 3717, 50)],
    )
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    await api.delete(f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers/3717")

    put = respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(
            200, json=fx.offers_stream(offers=[fx.stream_offer(1, 3749, 100)])
        )
    )
    await api.post(f"/api/campaigns/{campaign['id']}/push")

    assert json.loads(put.calls[0].request.content) == {
        "offers": [{"offer_id": 3749, "share": 100}]
    }
    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    assert _shares(body) == {3749: 100}


@respx.mock
async def test_push_with_no_changes_calls_nothing(api):
    _base_routes(respx.mock)
    put = respx.put(f"{KT}/streams/1310640")
    campaign = await _import(api)

    response = await api.post(f"/api/campaigns/{campaign['id']}/push")
    assert response.json()["pushed_streams"] == 0
    assert not put.called


@respx.mock
async def test_cancel_restores_the_last_fetched_state(api):
    _base_routes(
        respx.mock,
        offers=[fx.stream_offer(1, 3749, 70), fx.stream_offer(2, 3717, 30)],
    )
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    base = f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers"

    await api.post(base, json={"offer_id": 11111})
    await api.delete(f"{base}/3749")
    assert (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()[
        "has_pending_changes"
    ] is True

    body = (await api.post(f"/api/campaigns/{campaign['id']}/reset")).json()
    assert _shares(body) == {3749: 70, 3717: 30}
    assert body["has_pending_changes"] is False


@respx.mock
async def test_offers_cannot_be_added_to_a_redirect_stream(api):
    _base_routes(respx.mock)
    campaign = await _import(api)
    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    google_id = body["streams"][0]["id"]

    response = await api.post(
        f"/api/campaigns/{campaign['id']}/streams/{google_id}/offers", json={"offer_id": 3717}
    )
    assert response.status_code == 422
    assert "cannot hold offers" in response.json()["message"]


@respx.mock
async def test_adding_the_same_offer_twice_is_rejected(api):
    _base_routes(respx.mock)
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]

    response = await api.post(
        f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers", json={"offer_id": 3749}
    )
    assert response.status_code == 409


@respx.mock
async def test_fetch_discards_staged_changes_and_reloads_from_keitaro(api):
    _base_routes(respx.mock)
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    await api.post(
        f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers", json={"offer_id": 3717}
    )

    body = (await api.post(f"/api/campaigns/{campaign['id']}/fetch")).json()
    assert _shares(body) == {3749: 100}
    assert body["has_pending_changes"] is False


@respx.mock
async def test_importing_the_same_campaign_twice_conflicts(api):
    _base_routes(respx.mock)
    await _import(api)
    response = await api.post("/api/campaigns/import", json={"keitaro_id": 211939})
    assert response.status_code == 409


@respx.mock
async def test_push_failure_leaves_the_staged_changes_intact(api):
    """A rejected push must not pretend the edit landed."""
    _base_routes(respx.mock)
    campaign = await _import(api)
    stream_id = _offers_stream_of(
        (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    )["id"]
    await api.post(
        f"/api/campaigns/{campaign['id']}/streams/{stream_id}/offers", json={"offer_id": 3717}
    )
    respx.put(f"{KT}/streams/1310640").mock(
        return_value=Response(200, json={"error": "Offer not found"})
    )

    response = await api.post(f"/api/campaigns/{campaign['id']}/push")
    assert response.status_code == 502

    body = (await api.get(f"/api/campaigns/{campaign['id']}/streams")).json()
    assert body["has_pending_changes"] is True
    assert _shares(body) == {3749: 50, 3717: 50}
