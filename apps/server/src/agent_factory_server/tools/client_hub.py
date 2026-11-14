from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import WebSocket


class ClientHub:
    """Tracks outbound WebSocket connections from local bridges (opaque connection id)."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            old = self._connections.get(device_id)
            self._connections[device_id] = websocket
        if old is not None and old is not websocket:
            try:
                await old.close()
            except Exception:
                pass

    async def unregister(self, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            current = self._connections.get(device_id)
            if current is websocket:
                self._connections.pop(device_id, None)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._connections

    def online_ids(self) -> list[str]:
        return list(self._connections.keys())

    async def _request(
        self,
        *,
        device_id: str,
        pending_id: str,
        message: dict[str, Any],
        timeout: float,
        timeout_error: str,
    ) -> dict[str, Any]:
        target = (device_id or "").strip()
        if not target:
            return {
                "ok": False,
                "error": "session has no client_device_id; bind a Bridge device to this session",
            }
        websocket = self._connections.get(target)
        if websocket is None:
            return {
                "ok": False,
                "error": f"bridge offline: {target} (session is bound to this device)",
            }

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[pending_id] = future
        try:
            await websocket.send_json(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": timeout_error}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            self._pending.pop(pending_id, None)

    async def call_tool(
        self,
        *,
        device_id: str,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        session_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        return await self._request(
            device_id=device_id,
            pending_id=call_id,
            message={
                "type": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "session_id": session_id,
            },
            timeout=timeout,
            timeout_error=f"tool timeout: {name}",
        )

    async def approval_check(
        self,
        *,
        device_id: str,
        check_id: str,
        name: str,
        arguments: dict[str, Any],
        session_id: str,
        timeout: float,
    ) -> dict[str, Any]:
        """Ask Bridge whether this tool call needs human approval."""
        return await self._request(
            device_id=device_id,
            pending_id=check_id,
            message={
                "type": "approval_check",
                "check_id": check_id,
                "name": name,
                "arguments": arguments,
                "session_id": session_id,
            },
            timeout=timeout,
            timeout_error=f"approval_check timeout: {name}",
        )

    def resolve_tool_result(self, payload: dict[str, Any]) -> None:
        pending_id = payload.get("call_id") or payload.get("check_id")
        if not pending_id:
            return
        future = self._pending.get(str(pending_id))
        if future and not future.done():
            future.set_result(payload)

    def cancel_pending(self, pending_id: str, *, error: str = "cancelled") -> bool:
        """Complete an in-flight Bridge wait early so the run loop can observe cancel."""
        key = str(pending_id or "").strip()
        if not key:
            return False
        future = self._pending.get(key)
        if future is None or future.done():
            return False
        future.set_result({"ok": False, "error": error, "cancelled": True})
        return True

    async def send_tool_cancel(
        self,
        *,
        device_id: str,
        call_id: str,
        session_id: Optional[str] = None,
    ) -> bool:
        """Ask Bridge to abort a running client tool (kill process / cancel work).

        Fire-and-forget: does not wait for a reply. Bridge should still send
        tool_result when possible; late results are ignored if the wait already
        completed via cancel_pending.
        """
        target = (device_id or "").strip()
        key = str(call_id or "").strip()
        if not target or not key:
            return False
        websocket = self._connections.get(target)
        if websocket is None:
            return False
        message: dict[str, Any] = {
            "type": "tool_cancel",
            "call_id": key,
        }
        if session_id:
            message["session_id"] = session_id
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            return False

    @staticmethod
    def parse_message(raw: str | bytes) -> Optional[dict[str, Any]]:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None


client_hub = ClientHub()
