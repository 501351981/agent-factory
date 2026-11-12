from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


class EventHub:
    """In-memory fan-out of run events. Survives SSE client disconnect."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._waiters: list[asyncio.Future[None]] = []
        self._closed = False

    def publish(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)
        if event.get("type") == "done":
            self._closed = True

    def close(self) -> None:
        self._closed = True
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def subscribe(self, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        idx = max(0, after)
        while True:
            while idx < len(self._events):
                event = self._events[idx]
                idx += 1
                yield event
                if event.get("type") == "done":
                    return
            if self._closed:
                return
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            await waiter
