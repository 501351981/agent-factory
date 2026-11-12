from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from demo_bridge.skills_fs import resolve_skills_root, skill_load, skill_read


def echo_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": {"echo": arguments.get("text", "")}}


def get_cwd_tool(_arguments: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": {"cwd": str(Path.cwd())}}


def list_dir_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    path = Path(arguments.get("path") or ".").expanduser()
    if not path.exists():
        return {"ok": False, "error": f"path not found: {path}"}
    if not path.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    entries = sorted(p.name for p in path.iterdir())
    return {"ok": True, "data": {"path": str(path.resolve()), "entries": entries[:100]}}


TOOLS = {
    "echo": echo_tool,
    "get_cwd": get_cwd_tool,
    "list_dir": list_dir_tool,
    "skill_load": skill_load,
    "skill_read": skill_read,
}


async def handle_tool_call(payload: dict[str, Any]) -> dict[str, Any]:
    call_id = payload.get("call_id")
    name = payload.get("name")
    arguments = payload.get("arguments") or {}
    handler = TOOLS.get(name)
    if not handler:
        return {
            "type": "tool_result",
            "call_id": call_id,
            "ok": False,
            "error": f"unsupported tool: {name}",
        }
    try:
        result = handler(arguments if isinstance(arguments, dict) else {})
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}
    return {
        "type": "tool_result",
        "call_id": call_id,
        "ok": result.get("ok", True),
        "data": result.get("data"),
        "error": result.get("error"),
    }


async def run_bridge(server: str, device_id: str, token: str | None) -> None:
    qs = f"device_id={device_id}"
    if token:
        qs += f"&token={token}"
    url = f"{server.rstrip('/')}/api/client/connect?{qs}"
    skills_root = resolve_skills_root()
    print(f"[demo-bridge] connecting {url}")
    print(f"[demo-bridge] skills_root={skills_root}")
    async for websocket in websockets.connect(url, ping_interval=20, ping_timeout=20):
        try:
            await websocket.send(json.dumps({"type": "hello", "device_id": device_id}))
            print(f"[demo-bridge] online as device_id={device_id}")
            async for raw in websocket:
                payload = json.loads(raw)
                msg_type = payload.get("type")
                if msg_type == "tool_call":
                    print(f"[demo-bridge] tool_call {payload.get('name')} {payload.get('call_id')}")
                    result = await handle_tool_call(payload)
                    await websocket.send(json.dumps(result, ensure_ascii=False))
                elif msg_type in {"connected", "hello_ack", "pong"}:
                    print(f"[demo-bridge] << {msg_type}")
        except ConnectionClosed:
            print("[demo-bridge] disconnected, reconnecting...")
            await asyncio.sleep(1)
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent工厂 Demo Bridge")
    parser.add_argument(
        "--server",
        default=os.getenv("FACTORY_WS_BASE", "ws://127.0.0.1:8000"),
        help="WebSocket base, e.g. ws://127.0.0.1:8000",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("FACTORY_DEVICE_ID", "demo-mac"),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("FACTORY_API_KEY", ""),
    )
    parser.add_argument(
        "--skills-dir",
        default=os.getenv("FACTORY_SKILLS_DIR", ""),
        help="Local skills root (overrides FACTORY_SKILLS_DIR)",
    )
    args = parser.parse_args()
    if args.skills_dir:
        os.environ["FACTORY_SKILLS_DIR"] = args.skills_dir
    token = args.token or None
    asyncio.run(run_bridge(args.server, args.device_id, token))


if __name__ == "__main__":
    main()
