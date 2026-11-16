"""Per-campaign locking for staged edits.

Every edit endpoint reads a stream's offers, rebalances them and writes them back. Two
overlapping requests would each rebalance their own copy and the last writer would win,
leaving the stream staged at something other than 100% — easy to hit by double-clicking
"Add" or by keeping two tabs open.

The lock is process-local, which is enough for the single-process deployment the README
describes. Behind several workers the push-time `validate_total` check remains the
backstop: a stream that does not add up to 100% is refused rather than sent to Keitaro.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def campaign_lock(campaign_id: str) -> AsyncIterator[None]:
    async with _locks[campaign_id]:
        yield
