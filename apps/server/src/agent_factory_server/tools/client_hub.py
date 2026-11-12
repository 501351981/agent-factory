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
        self._pending[call_id] = future
        try:
            await websocket.send_json(
                {
                    "type": "tool_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "session_id": session_id,
                }
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"tool timeout: {name}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        finally:
            self._pending.pop(call_id, None)

    def resolve_tool_result(self, payload: dict[str, Any]) -> None:
        call_id = payload.get("call_id")
        if not call_id:
            return
        future = self._pending.get(call_id)
        if future and not future.done():
            future.set_result(payload)

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
