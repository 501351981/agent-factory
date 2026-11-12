from __future__ import annotations

import asyncio
from typing import Set


class RunCancelRegistry:
    """In-process cancel flags for active runs."""

    def __init__(self) -> None:
        self._cancelled: Set[str] = set()
        self._lock = asyncio.Lock()

    async def request_cancel(self, run_id: str) -> None:
        async with self._lock:
            self._cancelled.add(run_id)

    async def is_cancelled(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._cancelled

    async def clear(self, run_id: str) -> None:
        async with self._lock:
            self._cancelled.discard(run_id)


run_cancel_registry = RunCancelRegistry()
