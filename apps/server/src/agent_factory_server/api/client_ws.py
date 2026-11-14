from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from agent_factory_server.auth import require_api_key
from agent_factory_server.config import get_settings
from agent_factory_server.tools.client_hub import client_hub

router = APIRouter(tags=["client"])


@router.get("/api/client/devices")
async def list_online_devices(_: None = Depends(require_api_key)) -> dict:
    """Online Bridge device ids (for session binding)."""
    ids = client_hub.online_ids()
    return {"devices": ids, "count": len(ids)}


@router.websocket("/api/client/connect")
async def client_connect(
    websocket: WebSocket,
    device_id: str = Query(...),
    token: str | None = Query(default=None),
) -> None:
    expected = get_settings().factory_api_key.strip()
    if expected and token != expected:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    await client_hub.register(device_id, websocket)
    try:
        await websocket.send_json({"type": "connected", "device_id": device_id})
        while True:
            raw = await websocket.receive_text()
            payload = client_hub.parse_message(raw)
            if not payload:
                continue
            msg_type = payload.get("type")
            if msg_type == "hello":
                await websocket.send_json({"type": "hello_ack", "device_id": device_id})
            elif msg_type in ("tool_result", "approval_check_result"):
                client_hub.resolve_tool_result(payload)
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await client_hub.unregister(device_id, websocket)
