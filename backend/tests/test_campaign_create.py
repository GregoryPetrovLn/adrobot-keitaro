"""Part 1: the campaign creator produces exactly the structure the brief asks for."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from tests import fixtures as fx
from tests.conftest import KT


def _dictionary_routes(mock: respx.MockRouter) -> None:
    mock.get(f"{KT}/groups").mock(return_value=Response(200, json=fx.GROUPS))
    mock.get(f"{KT}/traffic_sources").mock(return_value=Response(200, json=fx.SOURCES))
    mock.get(f"{KT}/domains").mock(return_value=Response(200, json=fx.DOMAINS))
    mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))


@respx.mock
async def test_create_campaign_builds_two_flows(api):
    _dictionary_routes(respx.mock)
    create_campaign = respx.post(f"{KT}/campaigns").mock(
        return_value=Response(200, json=fx.campaign(name="RO test"))
    )
    create_stream = respx.post(f"{KT}/streams").mock(
        side_effect=[
            Response(200, json=fx.google_stream(geo="RO")),
            Response(200, json=fx.offers_stream()),
        ]
    )
    respx.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(200, json=[fx.google_stream(geo="RO"), fx.offers_stream()])
    )

    response = await api.post(
        "/api/campaigns", json={"name": "RO test", "geo": "RO", "offer_id": 3749}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["keitaro_id"] == 211939
    assert body["geo"] == "RO"
    assert body["created_by_app"] is True

    # --- the campaign itself: domain, group and source all configured ---
    campaign_payload = json.loads(create_campaign.calls[0].request.content)
    assert campaign_payload["type"] == "position"
    assert campaign_payload["cost_type"] == "CPC"
    assert campaign_payload["cost_auto"] is True
    assert campaign_payload["group_id"] == 9799
    assert campaign_payload["traffic_source_id"] == 836
    assert campaign_payload["domain_id"] == 1813
    assert campaign_payload["alias"]

    # --- flow 1: everyone from RO goes to Google ---
    flow1 = json.loads(create_stream.calls[0].request.content)
    assert flow1["campaign_id"] == 211939
    assert flow1["schema"] == "redirect"
    assert flow1["action_payload"] == "https://www.google.com"
    assert flow1["position"] == 1
    assert flow1["filters"] == [{"name": "country", "mode": "accept", "payload": ["RO"]}]

    # --- flow 2: everyone else goes to the chosen offer ---
    flow2 = json.loads(create_stream.calls[1].request.content)
    assert flow2["campaign_id"] == 211939
    assert flow2["schema"] == "landings"
    # Keitaro rejects an offer stream without action_type, even though it is unused.
    assert flow2["action_type"] == "http"
    assert flow2["position"] == 2
    assert flow2["offers"] == [{"offer_id": 3749, "share": 100}]


@respx.mock
async def test_geo_is_validated_before_any_call_is_made(api):
    _dictionary_routes(respx.mock)
    created = respx.post(f"{KT}/campaigns")

    response = await api.post(
        "/api/campaigns", json={"name": "bad geo", "geo": "ZZ", "offer_id": 3749}
    )
    assert response.status_code == 422
    assert not created.called


@respx.mock
async def test_unknown_group_is_rejected_without_creating_anything(api):
    _dictionary_routes(respx.mock)
    created = respx.post(f"{KT}/campaigns")

    response = await api.post(
        "/api/campaigns",
        json={"name": "x", "geo": "RO", "offer_id": 3749, "group_id": 404404},
    )
    assert response.status_code == 422
    assert "does not exist" in response.json()["message"]
    assert not created.called


@respx.mock
async def test_failed_stream_creation_rolls_the_campaign_back(api):
    """A half-built campaign is worse than none, so we delete it again."""
    _dictionary_routes(respx.mock)
    respx.post(f"{KT}/campaigns").mock(return_value=Response(200, json=fx.campaign()))
    respx.post(f"{KT}/streams").mock(
        return_value=Response(422, json={"action_type": ["Action Type is required"]})
    )
    deleted = respx.delete(f"{KT}/campaigns/211939").mock(return_value=Response(200, json={}))

    response = await api.post(
        "/api/campaigns", json={"name": "doomed", "geo": "RO", "offer_id": 3749}
    )
    assert response.status_code == 502
    assert deleted.called

    # Nothing was persisted locally either.
    listed = await api.get("/api/campaigns")
    assert listed.json() == []


@respx.mock
async def test_keitaro_inline_error_is_surfaced(api):
    """Keitaro reports some failures as HTTP 200 with an `error` key."""
    _dictionary_routes(respx.mock)
    respx.post(f"{KT}/campaigns").mock(
        return_value=Response(200, json={"error": "Param 'name' is required", "stacktrace": None})
    )

    response = await api.post(
        "/api/campaigns", json={"name": "x", "geo": "RO", "offer_id": 3749}
    )
    assert response.status_code == 502
    assert "Param 'name' is required" in response.json()["message"]


@pytest.mark.parametrize("geo_input,expected", [("ro", "RO"), (" au ", "AU"), ("Mx", "MX")])
@respx.mock
async def test_geo_is_normalised_to_upper_case(api, geo_input, expected):
    _dictionary_routes(respx.mock)
    respx.post(f"{KT}/campaigns").mock(return_value=Response(200, json=fx.campaign()))
    create_stream = respx.post(f"{KT}/streams").mock(
        side_effect=[
            Response(200, json=fx.google_stream(geo=expected)),
            Response(200, json=fx.offers_stream()),
        ]
    )
    respx.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(200, json=[fx.google_stream(geo=expected), fx.offers_stream()])
    )

    response = await api.post(
        "/api/campaigns", json={"name": "n", "geo": geo_input, "offer_id": 3749}
    )
    assert response.status_code == 201
    flow1 = json.loads(create_stream.calls[0].request.content)
    assert flow1["filters"][0]["payload"] == [expected]


@respx.mock
async def test_campaign_policy_follows_configuration(api, monkeypatch):
    """Cost model and uniqueness are settings, not constants baked into the code."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CAMPAIGN_COST_TYPE", "CPM")
    monkeypatch.setenv("CAMPAIGN_COST_CURRENCY", "EUR")
    monkeypatch.setenv("CAMPAIGN_UNIQUENESS_METHOD", "ip")
    monkeypatch.setenv("CAMPAIGN_COOKIES_TTL", "48")
    get_settings.cache_clear()

    _dictionary_routes(respx.mock)
    created = respx.post(f"{KT}/campaigns").mock(return_value=Response(200, json=fx.campaign()))
    respx.post(f"{KT}/streams").mock(
        side_effect=[
            Response(200, json=fx.google_stream()),
            Response(200, json=fx.offers_stream()),
        ]
    )
    respx.get(f"{KT}/campaigns/211939/streams").mock(
        return_value=Response(200, json=[fx.google_stream(), fx.offers_stream()])
    )

    try:
        response = await api.post(
            "/api/campaigns", json={"name": "policy", "geo": "AU", "offer_id": 3749}
        )
        assert response.status_code == 201, response.text
        payload = json.loads(created.calls[0].request.content)
        assert payload["cost_type"] == "CPM"
        assert payload["cost_currency"] == "EUR"
        assert payload["uniqueness_method"] == "ip"
        assert payload["cookies_ttl"] == 48
    finally:
        get_settings.cache_clear()
