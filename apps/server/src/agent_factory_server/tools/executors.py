from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import httpx

from agent_factory_server.config import get_settings
from agent_factory_server.domain.schemas import ToolDef
from agent_factory_server.tools.client_hub import client_hub


async def execute_tool(
    tool: ToolDef,
    *,
    arguments: dict[str, Any],
    session_id: str,
    client_device_id: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    call_id: Optional[str] = None,
) -> dict[str, Any]:
    settings = get_settings()
    timeout = float(timeout_seconds or settings.tool_timeout_seconds)
    if tool.execution == "remote_http":
        if not tool.endpoint:
            return {"ok": False, "error": f"tool {tool.name} missing endpoint"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    tool.endpoint,
                    json={
                        "name": tool.name,
                        "arguments": arguments,
                        "session_id": session_id,
                    },
                )
                if resp.status_code >= 400:
                    return {
                        "ok": False,
                        "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                    }
                data = resp.json()
                if isinstance(data, dict) and "ok" in data:
                    return data
                return {"ok": True, "data": data}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if tool.execution == "client":
        # Must match the run item / cancel path call_id (LLM id), not a fresh uuid.
        bridge_call_id = (call_id or "").strip() or str(uuid4())
        result = await client_hub.call_tool(
            device_id=(client_device_id or "").strip(),
            call_id=bridge_call_id,
            name=tool.name,
            arguments=arguments,
            session_id=session_id,
            timeout=timeout,
        )
        if "ok" not in result:
            return {"ok": True, "data": result}
        return result

    if tool.execution == "builtin":
        return {
            "ok": False,
            "error": "builtin tools must be executed by the runtime (memory)",
        }

    return {"ok": False, "error": f"unknown execution type: {tool.execution}"}
