"""Persist the Keitaro calls a client made into `SyncLog`.

Audit rows are written in their **own** session and committed immediately. The request's
session is rolled back whenever a Keitaro call fails, and a log that disappears exactly
when something goes wrong is worse than no log at all.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db import get_sessionmaker
from app.keitaro.client import KeitaroClient
from app.models import SyncLog

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 8000


def _truncate(value: Any, limit: int = MAX_BODY_CHARS) -> Any:
    """Keep audit rows small: long payloads are stored as a clipped string."""
    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return value[:limit]
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)[:limit]
    if len(encoded) <= limit:
        return value
    return encoded[:limit] + "…(truncated)"


async def record_calls(
    client: KeitaroClient,
    *,
    action: str,
    campaign_id: str | None = None,
) -> None:
    """Drain the client's call buffer into the audit table and commit it."""
    if not client.calls:
        return

    rows = [
        SyncLog(
            campaign_id=campaign_id,
            action=action,
            method=call.method,
            path=call.path,
            status_code=call.status_code,
            ok=call.ok,
            duration_ms=call.duration_ms,
            request_body=_truncate(call.request_body),
            response_body=_truncate(call.response_body),
            error=call.error,
        )
        for call in client.calls
    ]
    client.calls.clear()

    try:
        async with get_sessionmaker()() as audit:
            audit.add_all(rows)
            await audit.commit()
    except Exception:
        # Never let auditing mask the operation's own outcome.
        logger.exception("could not write %s audit rows for action %r", len(rows), action)
