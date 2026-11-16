"""Offer catalogue search — the autocomplete's only data source."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from tests import fixtures as fx
from tests.conftest import KT


def _routes(mock: respx.MockRouter) -> None:
    mock.get(f"{KT}/offers").mock(return_value=Response(200, json=fx.OFFERS))


@respx.mock
async def test_search_by_name_is_case_insensitive(api):
    _routes(respx.mock)
    found = (await api.get("/api/offers?q=miaflow")).json()
    assert {o["id"] for o in found} == {3749, 3717}


@respx.mock
async def test_search_by_exact_id(api):
    _routes(respx.mock)
    found = (await api.get("/api/offers?q=11111")).json()
    assert [o["id"] for o in found] == [11111]


@respx.mock
async def test_an_empty_query_lists_the_catalogue(api):
    _routes(respx.mock)
    assert len((await api.get("/api/offers")).json()) == len(fx.OFFERS)


@pytest.mark.parametrize(
    "term",
    [
        "99999999999999999999",          # larger than a 64-bit integer
        "10062100621006210062",
        "1" * 40,
        "0" * 30,
    ],
)
@respx.mock
async def test_a_huge_numeric_query_finds_nothing_instead_of_crashing(api, term):
    """Binding an oversized id overflowed SQLite's INTEGER and returned HTTP 500."""
    _routes(respx.mock)
    response = await api.get(f"/api/offers?q={term}")
    assert response.status_code == 200
    assert response.json() == []


@respx.mock
async def test_sql_wildcards_in_the_query_are_not_treated_as_patterns(api):
    """A bare % must not match everything."""
    _routes(respx.mock)
    assert (await api.get("/api/offers?q=%25")).json() == []


@respx.mock
async def test_a_whitespace_query_lists_the_catalogue(api):
    _routes(respx.mock)
    assert len((await api.get("/api/offers?q=%20%20")).json()) == len(fx.OFFERS)


@respx.mock
async def test_underscore_is_matched_literally(api):
    """`_` is LIKE's single-character wildcard; offer names are full of real ones."""
    _routes(respx.mock)
    found = (await api.get("/api/offers?q=DT_0309")).json()
    assert [o["id"] for o in found] == [11111]

    # The same query with the underscore acting as a wildcard would still match;
    # a character that is genuinely absent must not.
    assert (await api.get("/api/offers?q=DT_0399")).json() == []
