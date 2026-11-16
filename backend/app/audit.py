"""Request-scoped buffer for Keitaro audit rows.

Audit rows must outlive a failed request — a log that disappears exactly when something
breaks is worse than none. They cannot simply be written on a second connection either:
the request's own transaction still holds SQLite's write lock at that point, and the
second writer deadlocks against it ("database is locked").

So calls are buffered here as plain data and written by the session dependency *after* the
request's transaction has committed or rolled back, when the lock is free.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_pending: ContextVar[list[dict[str, Any]] | None] = ContextVar("audit_rows", default=None)


def begin() -> None:
    """Start a fresh buffer for this request."""
    _pending.set([])


def add(row: dict[str, Any]) -> None:
    buffer = _pending.get()
    if buffer is None:
        buffer = []
        _pending.set(buffer)
    buffer.append(row)


def drain() -> list[dict[str, Any]]:
    """Take everything buffered so far, leaving the buffer empty."""
    buffer = _pending.get()
    if not buffer:
        return []
    _pending.set([])
    return buffer
