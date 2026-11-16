"""Small helpers for reading ids out of Keitaro payloads."""

from __future__ import annotations


def as_int(value: object) -> int | None:
    """Parse an id, returning None when it is missing or unparseable."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def as_optional_ref(value: object) -> int | None:
    """Parse a *reference* id, where Keitaro writes 0 to mean "not set"."""
    parsed = as_int(value)
    return parsed or None
