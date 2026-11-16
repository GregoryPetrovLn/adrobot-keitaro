"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.errors import NotFoundError
from app.keitaro.client import KeitaroClient
from app.models import Campaign

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_keitaro(request: Request) -> AsyncIterator[KeitaroClient]:
    """One Keitaro client per request, reusing the app-wide connection pool."""
    http_client: httpx.AsyncClient = request.app.state.http_client
    client = KeitaroClient(get_settings(), client=http_client)
    async with client:
        yield client


KeitaroDep = Annotated[KeitaroClient, Depends(get_keitaro)]


async def get_campaign(campaign_id: str, session: SessionDep) -> Campaign:
    campaign = await session.scalar(select(Campaign).where(Campaign.id == campaign_id))
    if campaign is None:
        raise NotFoundError(f"Campaign {campaign_id} not found.")
    return campaign


CampaignDep = Annotated[Campaign, Depends(get_campaign)]
