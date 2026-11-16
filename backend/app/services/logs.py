"""Record the Keitaro calls a client made.

Rows are buffered per request (see `app.audit`) and flushed by the session dependency once
the request's transaction is over, so a failed call is still recorded even though its
request was rolled back.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app import audit
from app.keitaro.client import KeitaroClient

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
    """Move the client's call buffer into this request's pending audit rows."""
    for call in client.calls:
        audit.add(
            {
                "campaign_id": campaign_id,
                "action": action,
                "method": call.method,
                "path": call.path,
                "status_code": call.status_code,
                "ok": call.ok,
                "duration_ms": call.duration_ms,
                "request_body": _truncate(call.request_body),
                "response_body": _truncate(call.response_body),
                "error": call.error,
            }
        )
    client.calls.clear()
