---
name: agent-factory-integrate
description: >-
  将当前业务项目接入 Agent工厂（agent-factory）：配置 Agent 的 prompt/tools/skills、
  创建带 prompt_vars 的 Session、嵌入聊天页、实现 Bridge/remote_http 工具。
  当用户说接入工厂、改 Agent 配置、创建 session、embed iframe、配置 tools/skills 时使用。
---

# Agent工厂接入（给编程 Agent 用）

## 0. 先问人类要这些（缺一不可就停下问）

向用户确认并记下（可写进项目 `.env` / 本地配置，**不要把 Secret 提交进 git**）：

| 变量 | 含义 | 示例 |
|---|---|---|
| `FACTORY_API_BASE` | 工厂 HTTP API | `http://127.0.0.1:8000` |
| `CHAT_WEB_ORIGIN` | 聊天页 / embed 源 | `http://localhost:8801` |
| `AGENT_ID` | 本业务对应的 Agent | uuid |
| `AGENT_ACCESS_KEY` / `AGENT_SECRET_KEY` | **本 Agent 凭证**：改配置、建 Session、换 embed（**仅后端**） | `ak_…` / `sk_…` |
| `BRIDGE_DEVICE_ID` | 本地工具设备 ID | `my-app-dev` |

缺 `AGENT_ID` / AK/SK 时：请人类在管理台创建 Agent 并轮换凭证后提供。

OpenAPI：`{FACTORY_API_BASE}/docs`

鉴权：改本 Agent、建 Session、换 embed → Header `X-Access-Key` + `X-Secret-Key`。

人话文档：[client-guide.md](../client-guide.md) · API 速查：[api-for-coding-agents.md](../api-for-coding-agents.md)

---

## 1. 何时启用

- 用户要「接 Agent工厂 / 换底座 / 改 tools skills prompt」
- 要创建会话并注入业务上下文（userId、订单号等）
- 要嵌入聊天 iframe 或实现 client Bridge

## 2. 何时不要

- 纯改业务 UI 文案、与工厂无关
- 用户只要看管理台操作、明确不写代码

---

## 3. Procedure（按序）

### A. 探测工厂

```bash
curl -s "$FACTORY_API_BASE/api/health"
```

期望：`{"status":"ok","service":"agent-factory"}`

### B. 更新本 Agent（用 AK/SK，无需登录管理台、无需平台 Key）

首次创建 Agent 由工厂侧完成；接入后日常改 prompt/tools/skills：

```http
PATCH {FACTORY_API_BASE}/api/agents/{AGENT_ID}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json
```

读当前配置（同样用 AK/SK；只能读自己的 Agent）：

```http
GET {FACTORY_API_BASE}/api/agents/{AGENT_ID}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
```

Body 字段（均可选，PATCH 只改传入的）：

```json
{
  "name": "ops-assistant",
  "system_prompt": "你是运维助手。用户 {{userId}}，环境 {{env}}。",
  "system_prompts": { "zh": "...", "en": "..." },
  "model_profile_id": null,
  "tools": [ /* ToolDef[] 见下 */ ],
  "skills": [ /* SkillDef[] 见下 */ ],
  "suggested_prompts": ["查看当前目录", "帮我排查报错"],
  "runtime": {
    "memory": { "enabled": true }
  }
}
```

**ToolDef**

```json
{
  "name": "list_dir",
  "description": "列出目录",
  "execution": "client",
  "endpoint": null,
  "parameters": {
    "type": "object",
    "properties": { "path": { "type": "string" } }
  },
  "require_approval": false
}
```

- `execution`: `client` | `remote_http`（`builtin` 由工厂注入，不要手写 ask_user）
- `remote_http` 必须有 `endpoint`（工厂会 POST `{name,arguments,session_id}`）
- 高危工具：`require_approval: true`

**SkillDef（工厂只存目录）**

```json
{
  "name": "workspace-scout",
  "description": "本地工作区侦察；问目录时先 skill_load"
}
```

- `name`：小写字母/数字/连字符  
- **正文不在工厂**：放在业务 Bridge 的 `skills/<name>/SKILL.md`

轮换本 Agent 凭证（旧 SK 立即失效；新 SK 只返回一次）：

```http
POST {FACTORY_API_BASE}/api/agents/{AGENT_ID}/credentials/rotate
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
```

### C. 创建 Session（带业务上下文）

```http
POST {FACTORY_API_BASE}/api/sessions
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json

{
  "agent_id": "{AGENT_ID}",
  "user_key": "stable-business-user-id",
  "title": "工单 SO-8899",
  "locale": "zh",
  "client_device_id": "{BRIDGE_DEVICE_ID}",
  "prompt_vars": {
    "userId": "u_1001",
    "env": "staging",
    "orderId": "SO-8899"
  }
}
```

- `prompt_vars` 只在**创建时**渲染进该 Session 的 `system_prompt` 快照  
- Agent 模板用 `{{userId}}` 这类占位符；未传变量 → 空字符串  
- `user_key`：长期记忆作用域；可与 `userId` 相同

### D. 嵌入聊天页

业务后端先换 token：

```http
POST {FACTORY_API_BASE}/api/auth/embed-token
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json

{ "user_key": "stable-business-user-id", "ttl_seconds": 3600 }
```

iframe：

```text
{CHAT_WEB_ORIGIN}/embed?agentId={AGENT_ID}&token={embed_token}&userKey={user_key}&userId={userId}&deviceId={BRIDGE_DEVICE_ID}&sessionId={optional}&locale=zh
```

- 已有 `sessionId`：续聊  
- 无 `sessionId`：embed 可新建；或宿主自建 Session 后 `postMessage` `set_session`  
- `userId` 查询参数会映射为 `prompt_vars.userId`（仅 embed 快捷路径；更多变量请 API 建 Session）

宿主 postMessage 约定见 client-guide。

### E. 实现工具运行时（Bridge）

参考：`apps/demo-bridge`。

- WS：`{FACTORY_WS}/api/client/connect?device_id=...`  
- 收 `tool_call` → 回 `tool_result`  
- 业务 client 工具按 Agent.tools 自行实现  

若 Agent 要提供 Skill 能力（配置了 `skills[]`），Bridge **须实现**：

| 工具 | 参数 | 行为 |
|---|---|---|
| `skill_load` | `name` | 读本地 `skills/<name>/SKILL.md`，返回 `{ok, data: {skill_md, files}}` |
| `skill_read` | `name`, `path` | 读包内相对文件；拒绝 `..` / 绝对路径 |

Skill 正文放 `skills/<name>/SKILL.md`；Agent.skills 只配目录（name + description）。参考 `skills_fs.py`。

**remote_http**：业务服务实现 POST endpoint，返回 `{ok,data}` / `{ok,error}`

### F. 自检

1. `GET /api/agents/{id}` 看到 tools/skills/prompt 已更新  
2. 建 Session 后 `GET /api/sessions/{id}` 的 `system_prompt` 已替换变量  
3. embed 能发消息；client 工具能在 timeline 出现  
4. 改配置用 AK/SK 调 PATCH

---

## 4. Guardrails

- 用本 Agent 的 AK/SK 调接口；Secret 只放后端，不进前端 / embed URL / git  
- 更新 `tools` / `skills` 前先 GET，合并后再整表 PATCH  
- 缺地址或凭证时问用户  


## 5. 完成后告诉用户

- 用了的 `AGENT_ID`、embed 示例 URL（token 打码）  
- Bridge `deviceId` 与 skills 目录路径  
- 若轮换了 SK：提醒只显示一次，需存进业务后端密钥管理  
