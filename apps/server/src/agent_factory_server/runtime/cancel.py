from __future__ import annotations

import asyncio
from typing import Set


class RunCancelRegistry:
    """In-process cancel flags for active runs (with parent→child cascade)."""

    def __init__(self) -> None:
        self._cancelled: Set[str] = set()
        self._children: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def register_child(self, parent_run_id: str, child_run_id: str) -> None:
        parent = (parent_run_id or "").strip()
        child = (child_run_id or "").strip()
        if not parent or not child:
            return
        async with self._lock:
            self._children.setdefault(parent, set()).add(child)
            # If parent already cancelled, mark child immediately.
            if parent in self._cancelled:
                self._cancelled.add(child)

    async def unregister_child(self, parent_run_id: str, child_run_id: str) -> None:
        parent = (parent_run_id or "").strip()
        child = (child_run_id or "").strip()
        if not parent or not child:
            return
        async with self._lock:
            kids = self._children.get(parent)
            if kids:
                kids.discard(child)
                if not kids:
                    self._children.pop(parent, None)

    async def request_cancel(self, run_id: str) -> None:
        """Cancel ``run_id`` and every registered descendant."""
        target = (run_id or "").strip()
        if not target:
            return
        async with self._lock:
            stack = [target]
            while stack:
                current = stack.pop()
                if current in self._cancelled:
                    continue
                self._cancelled.add(current)
                for child in self._children.get(current, set()):
                    stack.append(child)

    async def is_cancelled(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._cancelled

    async def clear(self, run_id: str) -> None:
        async with self._lock:
            self._cancelled.discard(run_id)
            self._children.pop(run_id, None)


run_cancel_registry = RunCancelRegistry()
