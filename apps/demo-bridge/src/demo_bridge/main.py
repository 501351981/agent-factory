from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
from pathlib import Path
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from demo_bridge.skills_fs import resolve_skills_root, skill_load, skill_read

# Demo allowlist for approval_check when Agent policy default=ask_client.
_SAFE_COMMAND = re.compile(
    r"^(ls|pwd|df|free|ps|uptime|whoami|echo|cat|head|tail|sleep)\b",
    re.IGNORECASE,
)

# In-flight client tools keyed by factory call_id (same id as tool_call / tool_cancel).
_active_procs: dict[str, asyncio.subprocess.Process] = {}
_active_tasks: dict[str, asyncio.Task[Any]] = {}


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


async def run_command_tool(call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Long-running shell demo — must be killable via tool_cancel."""
    command = str(arguments.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "command is required"}
    # New session so we can kill the whole process group on cancel.
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    _active_procs[call_id] = proc
    try:
        stdout_b, stderr_b = await proc.communicate()
        return {
            "ok": proc.returncode == 0,
            "data": {
                "command": command,
                "exit_code": proc.returncode,
                "stdout": (stdout_b or b"").decode("utf-8", errors="replace")[:8000],
                "stderr": (stderr_b or b"").decode("utf-8", errors="replace")[:4000],
            },
            "error": None
            if proc.returncode == 0
            else f"exit_code={proc.returncode}",
        }
    except asyncio.CancelledError:
        return {"ok": False, "error": "cancelled", "cancelled": True}
    finally:
        _active_procs.pop(call_id, None)


TOOLS = {
    "echo": echo_tool,
    "get_cwd": get_cwd_tool,
    "list_dir": list_dir_tool,
    "skill_load": skill_load,
    "skill_read": skill_read,
}


def _kill_process(call_id: str) -> bool:
    proc = _active_procs.get(call_id)
    if proc is None or proc.returncode is not None:
        return False
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except Exception:
        try:
            proc.kill()
        except Exception:
            return False
    return True


async def handle_tool_cancel(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Abort in-flight work for call_id. Prefer killing the process group."""
    call_id = str(payload.get("call_id") or "").strip()
    if not call_id:
        return None
    killed = _kill_process(call_id)
    task = _active_tasks.get(call_id)
    if task and not task.done():
        task.cancel()
    print(f"[demo-bridge] tool_cancel call_id={call_id} killed={killed}")
    # Reply so factory can finalize even if cancel_pending already raced ahead.
    return {
        "type": "tool_result",
        "call_id": call_id,
        "ok": False,
        "error": "cancelled",
        "cancelled": True,
    }


async def _execute_tool_call(payload: dict[str, Any]) -> dict[str, Any]:
    call_id = str(payload.get("call_id") or "")
    name = payload.get("name")
    arguments = payload.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}

    if name == "run_command":
        result = await run_command_tool(call_id, arguments)
    else:
        handler = TOOLS.get(name)
        if not handler:
            return {
                "type": "tool_result",
                "call_id": call_id,
                "ok": False,
                "error": f"unsupported tool: {name}",
            }
        try:
            result = handler(arguments)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}

    return {
        "type": "tool_result",
        "call_id": call_id,
        "ok": result.get("ok", True),
        "data": result.get("data"),
        "error": result.get("error"),
        **(
            {"cancelled": True}
            if result.get("cancelled")
            else {}
        ),
    }


async def _run_tool_and_reply(websocket: Any, payload: dict[str, Any]) -> None:
    call_id = str(payload.get("call_id") or "")
    try:
        result = await _execute_tool_call(payload)
        await websocket.send(json.dumps(result, ensure_ascii=False))
    except asyncio.CancelledError:
        # tool_cancel may already have sent a cancelled tool_result.
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "tool_result",
                        "call_id": call_id,
                        "ok": False,
                        "error": "cancelled",
                        "cancelled": True,
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "tool_result",
                        "call_id": call_id,
                        "ok": False,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass
    finally:
        _active_tasks.pop(call_id, None)


def handle_approval_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide whether the factory should pause for human approval.

    Business bridges should replace this with their own policy (allowlists,
    ticket exemptions, etc.). Demo: safe read-only shell prefixes skip approval.
    """
    check_id = payload.get("check_id")
    name = str(payload.get("name") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    require = True
    reason = "default require"
    if name in {"list_dir", "get_cwd", "echo"}:
        require = False
        reason = f"demo allow tool {name}"
    elif name == "run_command":
        command = str(arguments.get("command") or "").strip()
        if command and _SAFE_COMMAND.search(command):
            require = False
            reason = "demo safe command prefix"
        else:
            reason = "demo unsafe or unknown command"
    return {
        "type": "approval_check_result",
        "check_id": check_id,
        "ok": True,
        "require": require,
        "reason": reason,
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
                    call_id = str(payload.get("call_id") or "")
                    print(f"[demo-bridge] tool_call {payload.get('name')} {call_id}")
                    # Concurrent: keep reading WS so tool_cancel can arrive mid-flight.
                    task = asyncio.create_task(_run_tool_and_reply(websocket, payload))
                    if call_id:
                        _active_tasks[call_id] = task
                elif msg_type == "tool_cancel":
                    result = await handle_tool_cancel(payload)
                    if result is not None:
                        await websocket.send(json.dumps(result, ensure_ascii=False))
                elif msg_type == "approval_check":
                    print(
                        f"[demo-bridge] approval_check {payload.get('name')} "
                        f"{payload.get('check_id')}"
                    )
                    result = handle_approval_check(payload)
                    print(f"[demo-bridge] approval_check → require={result.get('require')}")
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
