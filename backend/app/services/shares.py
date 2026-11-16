"""Share rebalancing.

Keitaro splits traffic between the offers of a stream by integer percentages. Two rules
drive every edit in the UI:

1. The active offers of a stream must sum to exactly 100.
2. A *pinned* offer keeps the share the user typed; only unpinned offers absorb the slack.

Unpinned offers share the remaining percentage points *evenly* — their previous values are
not preserved proportionally. Integer division leaves a remainder, which is handed out one
point at a time to the earliest unpinned offers. That matches what the reference tool does:
three offers become 34/33/33, and dropping one of four while a 25% offer is pinned leaves
38/37/25. Pinning is the mechanism for holding a share you do not want redistributed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.errors import ValidationError


@dataclass
class ShareSlot:
    """One offer participating in a rebalance. `key` is only used for stable ordering."""

    key: str
    share: int
    pinned: bool


def rebalance(slots: list[ShareSlot]) -> list[ShareSlot]:
    """Return `slots` with unpinned shares redistributed so the total is exactly 100.

    Pinned shares are preserved as-is. If pinned shares already total 100 or more, the
    unpinned offers get 0 — the caller surfaces that as a validation error on push rather
    than silently un-pinning something the user asked to hold.
    """
    if not slots:
        return slots

    pinned = [s for s in slots if s.pinned]
    unpinned = [s for s in slots if not s.pinned]

    if not unpinned:
        # Everything is pinned: nothing we may legally move. Leave the user's numbers alone.
        return slots

    pinned_total = sum(max(0, s.share) for s in pinned)
    remaining = max(0, 100 - pinned_total)

    base, extra = divmod(remaining, len(unpinned))
    for index, slot in enumerate(unpinned):
        slot.share = base + (1 if index < extra else 0)
    return slots


def total_share(slots: list[ShareSlot]) -> int:
    return sum(s.share for s in slots)


def validate_total(slots: list[ShareSlot]) -> None:
    """Raise if the shares cannot be pushed to Keitaro as-is."""
    if not slots:
        return
    total = total_share(slots)
    if total != 100:
        raise ValidationError(
            f"Offer shares must add up to 100%, got {total}%. "
            "Unpin at least one offer so the remainder can be distributed."
        )
