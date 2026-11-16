"""Persist the Keitaro calls a client made into `SyncLog`."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.keitaro.client import KeitaroClient
from app.models import SyncLog


def _truncate(value: Any, limit: int = 8000) -> Any:
    """Keep audit rows small: long payloads are stored as a clipped string."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value[:limit]
    try:
        import json

        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)[:limit]
    if len(encoded) <= limit:
        return value
    return encoded[:limit] + "…(truncated)"


async def record_calls(
    session: AsyncSession,
    client: KeitaroClient,
    *,
    action: str,
    campaign_id: str | None = None,
) -> None:
    """Drain the client's call buffer into the audit table."""
    for call in client.calls:
        session.add(
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
        )
    client.calls.clear()
    await session.flush()
