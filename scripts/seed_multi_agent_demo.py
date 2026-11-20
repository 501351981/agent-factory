#!/usr/bin/env python3
"""Seed a ready-to-try multi-agent demo (lead / pm / dev / qa).

Usage:
  python3 scripts/seed_multi_agent_demo.py
  # or with custom admin password:
  ADMIN_PASSWORD=xxx python3 scripts/seed_multi_agent_demo.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("FACTORY_API_BASE", "http://127.0.0.1:8000").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "liyulin6688")
CHAT_ORIGIN = os.environ.get("CHAT_WEB_ORIGIN", "http://localhost:8801").rstrip("/")
ADMIN_ORIGIN = os.environ.get("ADMIN_WEB_ORIGIN", "http://localhost:8802").rstrip("/")
MODEL_ID = os.environ.get("DEMO_MODEL_PROFILE_ID", "").strip()


def req(method: str, path: str, body=None, *, token: str | None = None, headers: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    token = req(
        "POST",
        "/api/auth/admin-login",
        {"username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )["token"]

    models = req("GET", "/api/models", token=token)
    model_id = MODEL_ID
    if not model_id:
        default = next((m for m in models if m.get("is_default") and m.get("enabled")), None)
        enabled = next((m for m in models if m.get("enabled")), None)
        pick = default or enabled
        if not pick:
            raise SystemExit("no enabled model profile; create one in admin first")
        model_id = pick["id"]

    agents = {a["name"]: a for a in req("GET", "/api/agents", token=token)}
    specs = [
        {
            "name": "demo-lead",
            "welcome_title": "我是项目负责人",
            "welcome_subtitle": "跟我说需求，我会拆给产品 / 研发 / 测试一起做",
            "suggested_prompts": [
                "做一个「用户登录」小功能：先出 PRD，再出实现方案，最后出测试要点",
                "看看 Board 上现在有哪些产物",
            ],
            "system_prompt": (
                "你是「项目负责人 Agent」。用户只和你对话。\n\n"
                "你有一支团队，请用工具协作，不要自己空想整份交付：\n"
                "1. 用 agent_delegate(role=\"pm\", task=...) 让产品经理写/改 PRD，并要求他 board_write 到 prd/...\n"
                "2. 用 agent_delegate(role=\"dev\", task=...) 让程序员基于 PRD 出实现方案，board_write 到 design/...\n"
                "3. 用 agent_delegate(role=\"qa\", task=...) 让测试出测试要点，board_write 到 qa/...\n"
                "4. 委托时把相关 path 放进 context_refs，例如 [\"prd/login.md\"]\n"
                "5. 最后用 board_list 汇总，用简洁中文向用户汇报进度与结论\n\n"
                "规则：优先串行 PM → Dev → QA；wait 默认 await；以 tool_result / Board 为准，不要伪造。"
            ),
        },
        {
            "name": "demo-pm",
            "welcome_title": "产品经理",
            "system_prompt": (
                "你是产品经理 Agent。收到负责人委托后：\n"
                "1. 输出简洁可执行的 PRD（目标、用户故事、验收标准）\n"
                "2. 必须调用 board_write(path=\"prd/<主题>.md\", title=..., content=...) 落盘\n"
                "3. 回复里只给摘要 + 已写入的 path"
            ),
        },
        {
            "name": "demo-dev",
            "welcome_title": "程序员",
            "system_prompt": (
                "你是程序员 Agent。收到委托后：\n"
                "1. 若有 context / Board 上的 PRD，先理解再设计\n"
                "2. 给出实现方案（模块、接口、关键流程、风险）；本 Demo 不写真实代码仓库\n"
                "3. 必须 board_write(path=\"design/<主题>.md\", ...)\n"
                "4. 回复摘要 + path"
            ),
        },
        {
            "name": "demo-qa",
            "welcome_title": "测试",
            "system_prompt": (
                "你是测试 Agent。收到委托后：\n"
                "1. 基于 PRD / 设计写测试要点（功能、边界、回归）\n"
                "2. 必须 board_write(path=\"qa/<主题>.md\", ...)\n"
                "3. 回复摘要 + path"
            ),
        },
    ]

    created: dict[str, dict] = {}
    for spec in specs:
        name = spec["name"]
        body = {
            "system_prompt": spec["system_prompt"],
            "system_prompts": {"zh": spec["system_prompt"]},
            "model_profile_id": model_id,
            "welcome_title": spec.get("welcome_title"),
            "welcome_subtitle": spec.get("welcome_subtitle"),
            "suggested_prompts": spec.get("suggested_prompts") or [],
        }
        if name in agents:
            created[name] = req("PATCH", f"/api/agents/{agents[name]['id']}", body, token=token)
        else:
            created[name] = req(
                "POST",
                "/api/agents",
                {
                    **body,
                    "name": name,
                    "tools": [],
                    "skills": [],
                    "runtime": {
                        "memory": {"enabled": True},
                        "knowledge": {"enabled": False},
                    },
                },
                token=token,
            )
        print(f"agent {name} {created[name]['id']}")

    teams = req("GET", "/api/teams", token=token)
    team = next((t for t in teams if t["name"] == "Demo研发小队"), None)
    team_body = {
        "name": "Demo研发小队",
        "lead_agent_id": created["demo-lead"]["id"],
        "members": [
            {"agent_id": created["demo-pm"]["id"], "role": "pm"},
            {"agent_id": created["demo-dev"]["id"], "role": "dev"},
            {"agent_id": created["demo-qa"]["id"], "role": "qa"},
        ],
        "max_depth": 1,
        "max_parallel": 3,
        "max_child_rounds": 6,
        "result_max_chars": 3000,
        "allow_async": True,
        "enabled": True,
    }
    if team:
        team = req("PATCH", f"/api/teams/{team['id']}", team_body, token=token)
    else:
        team = req("POST", "/api/teams", team_body, token=token)
    print(f"team {team['id']} {team['name']}")

    lead = created["demo-lead"]
    runtime = dict(lead.get("runtime") or {})
    runtime["multi_agent"] = {"enabled": True, "team_id": team["id"]}
    runtime.setdefault("max_tool_rounds", 12)
    runtime.setdefault("parallel_tool_calls", True)
    lead = req("PATCH", f"/api/agents/{lead['id']}", {"runtime": runtime}, token=token)

    cred = req("POST", f"/api/agents/{lead['id']}/credentials/rotate", {}, token=token)
    embed = req(
        "POST",
        "/api/auth/embed-token",
        {"user_key": "demo-user", "ttl_seconds": 86400},
        headers={
            "X-Access-Key": cred["access_key"],
            "X-Secret-Key": cred["secret_key"],
        },
    )
    url = (
        f"{CHAT_ORIGIN}/embed?agentId={lead['id']}"
        f"&token={embed['embed_token']}"
        f"&userKey=demo-user&userId=demo-user&deviceId=demo-mac"
    )
    print()
    print("=== Demo ready ===")
    print(f"CHAT  {url}")
    print(f"TEAM  {ADMIN_ORIGIN}/teams")
    print(f"LEAD  {ADMIN_ORIGIN}/agents/{lead['id']}")
    print()
    print("试着对负责人说：")
    print("  做一个「用户登录」小功能：先出 PRD，再出实现方案，最后出测试要点")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
