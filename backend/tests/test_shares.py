"""Share rebalancing — the arithmetic the editor's numbers depend on."""

from __future__ import annotations

import pytest

from app.errors import ValidationError
from app.services.shares import ShareSlot, rebalance, total_share, validate_total


def slots(*spec: tuple[int, bool]) -> list[ShareSlot]:
    return [ShareSlot(key=str(i), share=share, pinned=pinned)
            for i, (share, pinned) in enumerate(spec)]


def shares(result: list[ShareSlot]) -> list[int]:
    return [s.share for s in result]


def test_single_offer_takes_everything():
    assert shares(rebalance(slots((0, False)))) == [100]


def test_two_offers_split_evenly():
    assert shares(rebalance(slots((100, False), (0, False)))) == [50, 50]


def test_three_offers_give_the_remainder_to_the_first():
    # Matches the reference app: 34 / 33 / 33.
    assert shares(rebalance(slots((0, False), (0, False), (0, False)))) == [34, 33, 33]


def test_four_offers_split_exactly():
    assert shares(rebalance(slots(*[(0, False)] * 4))) == [25, 25, 25, 25]


def test_pinned_offer_is_preserved_and_rest_absorb_the_slack():
    # Reference app: with one 25% offer pinned, the other two become 38 / 37.
    result = rebalance(slots((0, False), (0, False), (25, True)))
    assert shares(result) == [38, 37, 25]
    assert total_share(result) == 100


def test_two_pinned_offers():
    result = rebalance(slots((10, True), (0, False), (30, True)))
    assert shares(result) == [10, 60, 30]
    assert total_share(result) == 100


def test_all_pinned_is_left_untouched():
    # Nothing may legally move; the push-time validator is what complains.
    result = rebalance(slots((40, True), (40, True)))
    assert shares(result) == [40, 40]


def test_overpinned_leaves_unpinned_at_zero():
    result = rebalance(slots((70, True), (60, True), (0, False)))
    assert shares(result) == [70, 60, 0]


def test_empty_is_a_noop():
    assert rebalance([]) == []


@pytest.mark.parametrize("count", range(1, 20))
def test_any_number_of_unpinned_offers_totals_exactly_100(count: int):
    result = rebalance(slots(*[(0, False)] * count))
    assert total_share(result) == 100
    # The split stays as even as integers allow.
    assert max(shares(result)) - min(shares(result)) <= 1


def test_validate_total_accepts_a_correct_split():
    validate_total(rebalance(slots((0, False), (0, False))))


def test_validate_total_rejects_an_overpinned_stream():
    with pytest.raises(ValidationError, match="add up to 100"):
        validate_total(rebalance(slots((70, True), (60, True), (0, False))))


def test_validate_total_allows_an_empty_stream():
    validate_total([])
