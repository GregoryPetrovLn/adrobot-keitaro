"""Thin async client for the Keitaro admin API (`/admin_api/v1`).

Everything here was verified against a live Keitaro 10.3.16 instance. The non-obvious bits
are called out inline, because the published docs disagree with the implementation:

* Streams are created with ``POST /streams`` and ``campaign_id`` in the **body** —
  ``POST /campaigns/{id}/streams`` returns 404.
* ``schema="landings"`` still requires ``action_type`` to be set (``"http"`` with an empty
  payload is what the Keitaro UI itself sends).
* ``PUT /streams/{id}`` with an ``offers`` array is *declarative*: the array replaces the
  stream's offer list wholesale, so adding, removing and re-weighting is a single call.
* ``GET /groups`` requires a ``type`` query parameter (``campaigns``/``offers``/...).
* ``GET /offers`` ignores ``name``/``limit``; it always returns the whole catalogue.
* Errors come back with HTTP 200 as ``{"error": "..."}`` in some cases and as 422 with a
  ``{"field": ["message"]}`` map in others, so both shapes are normalised here.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.errors import KeitaroError, KeitaroNotConfiguredError

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]


class KeitaroCall:
    """What happened on one outbound request — fed straight into `SyncLog`."""

    __slots__ = ("method", "path", "status_code", "ok", "duration_ms", "request_body",
                 "response_body", "error")

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.status_code: int | None = None
        self.ok = False
        self.duration_ms = 0
        self.request_body: Any = None
        self.response_body: Any = None
        self.error: str | None = None


class KeitaroClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._external_client = client
        self._client = client
        self.calls: list[KeitaroCall] = []

    # ------------------------------------------------------------------ plumbing

    async def __aenter__(self) -> KeitaroClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.keitaro_timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._external_client is None and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("KeitaroClient must be used as an async context manager")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: JSONDict | None = None,
    ) -> Any:
        if not self._settings.keitaro_api_key:
            raise KeitaroNotConfiguredError(
                "KEITARO_API_KEY is not set. Copy .env.example to .env and fill it in."
            )

        url = f"{self._settings.keitaro_api_url}/{path.lstrip('/')}"
        call = KeitaroCall(method, path)
        call.request_body = json
        self.calls.append(call)
        started = time.perf_counter()

        try:
            response = await self._require_client().request(
                method,
                url,
                json=json,
                params=params,
                headers={"Api-Key": self._settings.keitaro_api_key, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            call.duration_ms = int((time.perf_counter() - started) * 1000)
            call.error = str(exc)
            logger.error("keitaro %s %s failed: %s", method, path, exc)
            raise KeitaroError(f"Keitaro is unreachable: {exc}", path=path) from exc

        call.duration_ms = int((time.perf_counter() - started) * 1000)
        call.status_code = response.status_code

        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text[:2000]
        call.response_body = payload

        if response.status_code >= 400:
            message, details = _describe_error(payload, response.status_code)
            call.error = message
            logger.error("keitaro %s %s -> %s: %s", method, path, response.status_code, message)
            raise KeitaroError(
                message, status_code=response.status_code, details=details, path=path
            )

        # Keitaro also signals failures with HTTP 200 + {"error": "..."}.
        if isinstance(payload, dict) and "error" in payload and len(payload) <= 2:
            message = str(payload["error"])
            call.error = message
            logger.error("keitaro %s %s -> inline error: %s", method, path, message)
            raise KeitaroError(message, status_code=response.status_code, path=path)

        # A reverse proxy in front of the tracker can answer 200 with an HTML error page.
        # Callers expect JSON, so turn that into a KeitaroError here rather than letting a
        # str reach code that will call .get() on it.
        if not isinstance(payload, dict | list):
            call.error = "Keitaro returned a non-JSON body"
            logger.error("keitaro %s %s -> non-JSON body: %.200s", method, path, payload)
            raise KeitaroError(
                "Keitaro returned a non-JSON response. The tracker or a proxy in front of "
                "it is probably misconfigured.",
                status_code=response.status_code,
                details=str(payload)[:500],
                path=path,
            )

        call.ok = True
        return payload

    # ------------------------------------------------------------------ dictionaries

    async def list_campaign_groups(self) -> list[JSONDict]:
        return await self._request("GET", "groups", params={"type": "campaigns"})

    async def create_campaign_group(self, name: str) -> JSONDict:
        return await self._request("POST", "groups", json={"name": name, "type": "campaigns"})

    async def list_traffic_sources(self) -> list[JSONDict]:
        return await self._request("GET", "traffic_sources")

    async def list_domains(self) -> list[JSONDict]:
        return await self._request("GET", "domains")

    async def list_offers(self) -> list[JSONDict]:
        return await self._request("GET", "offers")

    # ------------------------------------------------------------------ campaigns

    async def list_campaigns(self) -> list[JSONDict]:
        return await self._request("GET", "campaigns")

    async def get_campaign(self, campaign_id: int) -> JSONDict:
        return await self._request("GET", f"campaigns/{campaign_id}")

    async def create_campaign(self, payload: JSONDict) -> JSONDict:
        return await self._request("POST", "campaigns", json=payload)

    async def delete_campaign(self, campaign_id: int) -> Any:
        return await self._request("DELETE", f"campaigns/{campaign_id}")

    # ------------------------------------------------------------------ streams

    async def list_streams(self, campaign_id: int) -> list[JSONDict]:
        return await self._request("GET", f"campaigns/{campaign_id}/streams")

    async def get_stream(self, stream_id: int) -> JSONDict:
        return await self._request("GET", f"streams/{stream_id}")

    async def create_stream(self, payload: JSONDict) -> JSONDict:
        # `campaign_id` belongs in the body here; the nested route does not exist.
        return await self._request("POST", "streams", json=payload)

    async def update_stream(self, stream_id: int, payload: JSONDict) -> JSONDict:
        return await self._request("PUT", f"streams/{stream_id}", json=payload)

    async def set_stream_offers(self, stream_id: int, offers: list[JSONDict]) -> JSONDict:
        """Replace a stream's offer list. ``offers`` is ``[{"offer_id": int, "share": int}]``."""
        return await self.update_stream(stream_id, {"offers": offers})

    # ------------------------------------------------------------------ reports

    async def build_report(
        self,
        *,
        grouping: list[str],
        metrics: list[str],
        interval: str = "today",
        timezone: str = "UTC",
        filters: list[JSONDict] | None = None,
        limit: int = 1000,
    ) -> JSONDict:
        return await self._request(
            "POST",
            "report/build",
            json={
                "range": {"interval": interval, "timezone": timezone},
                "columns": [],
                "metrics": metrics,
                "grouping": grouping,
                "filters": filters or [],
                "limit": limit,
            },
        )


def _describe_error(payload: Any, status_code: int) -> tuple[str, Any]:
    """Normalise Keitaro's two error shapes into (message, details)."""
    if isinstance(payload, dict):
        if "error" in payload:
            return str(payload["error"]), payload
        # 422 validation: {"action_type": ["Action Type is required"], ...}
        parts: list[str] = []
        for field, messages in payload.items():
            if isinstance(messages, list):
                parts.append(f"{field}: {'; '.join(str(m) for m in messages)}")
        if parts:
            return " | ".join(parts), payload
    if isinstance(payload, str) and payload:
        return f"Keitaro returned HTTP {status_code}", payload
    return f"Keitaro returned HTTP {status_code}", payload
