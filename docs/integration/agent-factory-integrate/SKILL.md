---
name: agent-factory-integrate
description: >-
  将当前业务项目接入 Agent工厂（agent-factory）：在业务里嵌聊天页、后端换 embed_token、
  用 AK/SK 配置 prompt/tools/ui_theme、上传工厂技能包、建 Session、重命名/清空/软删会话、
  实现 Bridge（含 approval_check / tool_cancel）或 remote_http。
  用户说接入工厂、嵌 iframe、换底座、改 Agent 配置/主题样式、创建 session、
  清除聊天记录、删除会话、重命名会话、工具审批/ask_client、停止工具/tool_cancel、配置 tools/skills 时使用。
---

# Agent工厂接入（给编程 Agent 用）

在**当前业务仓库**完成接入：工厂提供通用 Runtime + 官方聊天页；本仓库只做实例差异化与嵌入胶水。

## 0. 先问人类要这些（缺一不可就停下问）

写入业务 `.env` / 密钥管理（**Secret 不进 git、不进前端**）：

| 变量 | 含义 | 示例 |
|---|---|---|
| `FACTORY_API_BASE` | 工厂 HTTP API | `http://127.0.0.1:8000` |
| `CHAT_WEB_ORIGIN` | 聊天页 / embed 源 | `http://localhost:8801` |
| `AGENT_ID` | 本业务 Agent | uuid |
| `AGENT_ACCESS_KEY` / `AGENT_SECRET_KEY` | 本 Agent 凭证 | `ak_…` / `sk_…` |
| `BRIDGE_DEVICE_ID` | 有 client 工具时的设备 ID | `my-app-dev` |

缺 `AGENT_ID` / AK/SK：请人类在工厂管理台创建 Agent 并轮换凭证后提供。

鉴权：改配置、建 Session、换 embed → `X-Access-Key` + `X-Secret-Key`。  
OpenAPI：`{FACTORY_API_BASE}/docs`

---

## 1. 何时启用 / 不要

**启用**：接工厂、换底座、嵌聊天、改 tools/skills/prompt/主题、建 Session、写 Bridge。  
**不要**：纯改业务文案；用户只要点管理台、明确不写代码。

---

## 2. 业务里要集成什么（总览）

```text
┌─ 业务仓库 ─────────────────────────────────────────┐
│  前端宿主页：iframe → CHAT_WEB_ORIGIN/embed         │
│  业务后端：AK/SK 换 embed_token；可选建 Session      │
│  Bridge（可选）：client 工具 / approval_check / tool_cancel │
│  业务 HTTP（可选）：remote_http endpoint             │
└──────────────────────────┬──────────────────────────┘
                           │
              FACTORY_API_BASE / CHAT_WEB_ORIGIN
                           ▼
                   Agent 工厂（已部署；技能包正文在工厂）
```

在业务仓库落地这几块（按需）：

| 落地项 | 做什么 |
|---|---|
| **后端换 token** | 接口用 AK/SK 调工厂 `POST /api/auth/embed-token`，把 `embed_token` 给前端 |
| **宿主页 iframe** | 嵌入 `/embed?...`；生产必须带 `token` |
| **（可选）Session** | 后端建会话 / 改标题 / 清空记录 / 软删；宿主 `postMessage` 切会话 |
| **（可选）Bridge** | WS 连工厂，执行 `client` 工具；有 `ask_client` → **必须** `approval_check`；有长任务 → **必须** `tool_cancel` |
| **（可选）remote_http** | 业务 API 接收工厂 POST 工具调用 |
| **配置 Agent** | AK/SK：`PATCH /api/agents/{id}` 维护 prompt/tools/`ui_theme`；技能包用 skill-packages API（或管理台上传） |

最小可聊通路径：**后端换 token + 宿主 iframe**（可不接 Bridge；技能包也不依赖 Bridge）。

---

## 3. Procedure（按序在业务仓库执行）

### A. 探测工厂

```bash
curl -s "$FACTORY_API_BASE/api/health"
```

期望：`{"status":"ok","service":"agent-factory"}`

### B. 业务后端：换 embed_token

在业务后端新增接口（示例语义），**仅服务端**持有 SK：

```http
POST {FACTORY_API_BASE}/api/auth/embed-token
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json

{ "user_key": "<业务稳定用户ID>", "ttl_seconds": 3600 }
```

返回的 `embed_token` 下发给已登录前端。`user_key` 与登录用户稳定 ID 对齐（记忆作用域）。

### C. 业务前端：嵌入聊天页

```html
<iframe
  id="agent-frame"
  src="{CHAT_WEB_ORIGIN}/embed?agentId={AGENT_ID}&token={embed_token}&userKey={user_key}&userId={userId}&deviceId={BRIDGE_DEVICE_ID}&locale=zh"
  style="width:100%;height:640px;border:0"
  allow="clipboard-write"
></iframe>
```

| 参数 | 说明 |
|---|---|
| `agentId` | 必填 |
| `token` | 生产必填：上一步 `embed_token` |
| `userKey` | 新建会话必填；建议=业务用户稳定 ID |
| `userId` | 映射为 `prompt_vars.userId`（快捷路径） |
| `deviceId` | 有 Bridge 时与 `--device-id` 一致 |
| `sessionId` | 可选；续聊 |
| `locale` | `zh` / `en` / `ja` |

更多变量：后端先 `POST /api/sessions` 带 `prompt_vars`，再把 `sessionId` 写入 iframe 或 `postMessage`。

**宿主 ↔ iframe（可选）**

- 工厂 → 宿主：`source: 'agent-factory'`，如 `session`（回传 sessionId）  
- 宿主 → 工厂：`source: 'agent-factory-host'`，如 `set_session` / `clear_session`  

### D. 用 AK/SK 更新本 Agent 配置

日常改 prompt / tools / `ui_theme`（先 GET 再合并 PATCH；`tools` / `ui_theme` 为整表替换）。**技能包不要靠 PATCH `skills` 手写正文**，见下方「技能包」。

```http
GET|PATCH {FACTORY_API_BASE}/api/agents/{AGENT_ID}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
```

```json
{
  "system_prompt": "你是运维助手。用户 {{userId}}，环境 {{env}}。",
  "tools": [ /* ToolDef */ ],
  "suggested_prompts": ["查看当前目录"],
  "ui_theme": { "preset": "dark", "accent": "#2563eb" },
  "runtime": {
    "memory": { "enabled": true },
    "max_tool_rounds": 8,
    "max_run_tokens": 1000000
  }
}
```

**运行限制（成本护栏，可选 PATCH）**：`runtime.max_tool_rounds`（1–32，单条用户消息内模型轮次）、`max_run_tokens`（单 Run 累计 token，0=服务端默认 1M；成本敏感建议 200k–500k）、`run_timeout_seconds`（墙钟）、`tool_timeout_seconds`、`parallel_tool_calls`（同轮多工具是否并发，默认 true）。管理台 Agent 编辑页「运行限制」可视化配置；`ask_user` / 审批恢复不会重置预算。

**ToolDef**

```json
{
  "name": "run_command",
  "description": "在 Bridge 执行 shell",
  "execution": "client",
  "endpoint": null,
  "parameters": {
    "type": "object",
    "properties": { "command": { "type": "string" } },
    "required": ["command"]
  },
  "require_approval": true,
  "approval_policy": {
    "default": "ask_client",
    "allow": [
      { "command": { "regex": "^(ls|pwd|df|free|ps|uptime|whoami)\\b" } }
    ],
    "always_require": [
      { "command": { "regex": "\\b(rm\\s+-rf|mkfs|dd\\s+if=|shutdown|reboot)\\b" } }
    ]
  }
}
```

- `execution`：`client` | `remote_http`（勿手写 builtin `ask_user`）  
- `remote_http` 必填 `endpoint`；工厂 POST `{name,arguments,session_id}`，期望 `{ok,data}` / `{ok,error}`  
  - Agent 开并行时，同一轮会对 `endpoint` **并发发起多个 POST**（≤4）；业务接口需并发安全，别依赖「同一时刻只有一个请求」
- `require_approval`：总开关；`false` 从不弹审批  
- `approval_policy`（可选）：总开关为 true 时生效。顺序：`always_require` → `allow` → `default`  
  - `default`：`require` | `skip` | **`ask_client`**  
  - 无 policy = 每次都审；匹配器用字面量或 `{"regex"}` / `{"eq"}` / `{"contains"}`  
- **何时必须改 Bridge**：任意 ToolDef 出现 `approval_policy.default: "ask_client"` 时，业务 Bridge **必须**实现 WS `approval_check`（不是 Agent tools 里的工具名，是协议消息）。只配正则 `allow`/`always_require` 且 `default` 为 `require`/`skip` 时，**不必**实现。详见 §F。  
- Bridge 仍应对危险命令做硬拒绝，审批只是 UX 闸门

#### 技能包（工厂托管）

技能是含 `SKILL.md` 的 zip。正文存工厂；prompt 只有 catalog（`name` + `description`）。`skill_load` / `skill_read` 为工厂 **builtin**，**Bridge 不必实现**。

两层：

| 层 | 用途 | API |
|---|---|---|
| **平台技能库** | 全厂一份（如 `skill-creator`）；Agent 启用后进 catalog | `/api/platform-skills`（平台 Key） |
| **Agent 自有包** | 仅该 Agent | `/api/agents/{id}/skill-packages`（AK/SK） |

```http
POST /api/agents/{AGENT_ID}/skill-mounts/{name}     # 启用平台技能
DELETE /api/agents/{AGENT_ID}/skill-mounts/{name}   # 取消启用
```

包格式：`SKILL.md`（frontmatter: `name` / `description` / `version?`）+ 可选 `references/` / `scripts/`（不执行）/ `assets/`。

Agent 自有包上传：

```bash
curl -s -X POST "$FACTORY_API_BASE/api/agents/$AGENT_ID/skill-packages" \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" \
  -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  -F "file=@./my-skill.zip" \
  -F "force=false"
```

`force` / `keep_description` / `expected_name` 同管理台覆盖语义。同名不可同时「平台挂载 + 自有包」。

运行时：`skill_load` / `skill_read`；投影上限默认 40000；超长用 `offset` 翻页。

#### 改嵌入页主题（`ui_theme`）

业务要品牌色对齐时，PATCH 本 Agent 的 `ui_theme`（**只传下列字段名**，勿注入任意 CSS）。先 GET，合并后**整表写回**；`{}` 恢复默认浅色。

```http
PATCH {FACTORY_API_BASE}/api/agents/{AGENT_ID}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json

{
  "ui_theme": {
    "preset": "dark",
    "bg": "#0f1115",
    "text": "#f3f4f6",
    "userBubble": "#2563eb",
    "userBubbleText": "#ffffff",
    "assistantBubble": "#1a1d24",
    "assistantBubbleText": "#f8fafc",
    "composerCard": "#1a1d24",
    "composerSendBg": "#3b82f6",
    "radiusBubble": "18px"
  }
}
```

**字段字典（JSON key = 标识，必须原样使用）**

| 标识 `key` | 含义 | 作用范围 | CSS 变量 |
|---|---|---|---|
| `preset` | 预设：`light` / `dark`；`dark` 先展开一套默认色，再可被下面字段覆盖 | 全局 | — |
| `bg` | 聊天页大背景 | 页面 | `--af-bg` |
| `surface` | 表面/卡片底（推荐问等） | 页面 | `--af-surface` |
| `border` | 主边框色 | 页面 | `--af-border` |
| `borderSoft` | 弱分隔线 | 页面 | `--af-border-soft` |
| `text` | 主文字色（过程条「任务完成」、正文默认） | 页面 | `--af-text` |
| `muted` | 次要文字（占位、弱提示） | 页面 | `--af-muted` |
| `accent` | 强调色 | 页面 | `--af-accent` |
| `accentSoft` | 强调浅底（禁用按钮等） | 页面 | `--af-accent-soft` |
| `userBubble` | 用户消息气泡背景 | 用户消息 | `--af-user-bubble` |
| `userBubbleText` | 用户消息文字 | 用户消息 | `--af-user-bubble-text` |
| `userBubbleBorder` | 用户消息边框（可用 `transparent`） | 用户消息 | `--af-user-bubble-border` |
| `assistantBubble` | 助手消息气泡背景 | 助手消息 | `--af-assistant-bubble` |
| `assistantBubbleText` | 助手消息文字（**含 Markdown 正文**） | 助手消息 | `--af-assistant-bubble-text` |
| `assistantBubbleBorder` | 助手消息边框 | 助手消息 | `--af-assistant-bubble-border` |
| `composerBg` | 底部输入区渐变底色 | 输入框 | `--af-composer-bg` |
| `composerCard` | 输入卡片背景 | 输入框 | `--af-composer-card` |
| `composerBorder` | 输入卡片边框 | 输入框 | `--af-composer-border` |
| `composerText` | 输入中的文字色 | 输入框 | `--af-composer-text` |
| `composerMuted` | 输入占位符 / 弱文案 | 输入框 | `--af-composer-muted` |
| `composerSendBg` | 发送/停止按钮背景（启用态） | 输入框 | `--af-composer-send-bg` |
| `composerSendText` | 发送/停止按钮图标色 | 输入框 | `--af-composer-send-text` |
| `radiusLg` | 大圆角（输入卡片等） | 圆角 | `--af-radius-lg` |
| `radiusMd` | 中圆角 | 圆角 | `--af-radius-md` |
| `radiusBubble` | 消息气泡圆角 | 圆角 | `--af-radius-bubble` |

深色主题最少要配：`bg` + `text` + `userBubble`/`userBubbleText` + `assistantBubble`/`assistantBubbleText` + `composerCard`/`composerText`（否则易出现深底深字）。

允许值：`#rgb`/`#rrggbb(aa)`、`rgb/rgba/hsl/hsla(...)`、`transparent`、`Npx|rem|%`。含 `;` `{` `url(` 等非法片段会被丢弃。改完刷新 embed 生效。

### E. 创建 Session（需要业务上下文时）

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
  "prompt_vars": { "userId": "u_1001", "env": "staging", "orderId": "SO-8899" }
}
```

`prompt_vars` 仅创建时渲染进该 Session 的 `system_prompt` 快照。

### E2. 会话生命周期（业务后端用 AK/SK）

鉴权同创建：`X-Access-Key` + `X-Secret-Key`（embed 也可，且绑定 `user_key` 时只能动对应用户会话）。

**重命名**

```http
PATCH {FACTORY_API_BASE}/api/sessions/{session_id}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
Content-Type: application/json

{ "title": "工单 SO-8899 · 跟进" }
```

**清除聊天记录（软清除）**

```http
POST {FACTORY_API_BASE}/api/sessions/{session_id}/clear-history
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
```

- 写入 `history_cleared_at` 水位；旧 messages / timeline **接口不再返回**，也**不进**后续 LLM 上下文  
- 会话本身保留（可继续聊）；会取消排队中的消息与进行中的 run  
- 库内旧数据仍在，直到管理台硬删

**删除会话（软删除）**

```http
DELETE {FACTORY_API_BASE}/api/sessions/{session_id}
X-Access-Key: {AGENT_ACCESS_KEY}
X-Secret-Key: {AGENT_SECRET_KEY}
```

或 `PATCH` `{ "status": "archived" }`。列表与续聊视为不存在（404）；数据保留。永久清理由工厂管理台处理，客户端不要调硬删。

### F. Bridge（有 `execution: client` 工具时）

- WS：`{FACTORY_WS}/api/client/connect?device_id={BRIDGE_DEVICE_ID}`  
- 收 `tool_call` → 回 `tool_result`（按 Agent.tools 实现业务工具）  
- **不需要**实现 `skill_load` / `skill_read`（工厂 builtin）

| 条件 | Bridge 必须实现 |
|---|---|
| 有任一 `execution: client` 工具 | `tool_call` / `tool_result` |
| **任一工具** `require_approval` + `approval_policy.default: "ask_client"` | **`approval_check` / `approval_check_result`（WS 消息，不是 ToolDef）**；未实现则离线/超时会被当成「需审批」 |
| 有可长时间运行的 `client` 工具（shell / 子进程 / 远程作业） | **`tool_cancel`**：用户点「停止」时工厂会下发；Bridge 必须真正中止本地工作 |
| Agent 开启并行工具（默认开） | **同时处理多个 `tool_call`**：同一轮最多 4 个并发到达，Bridge 需并发执行或自行加锁 |

**架构注意（取消 / 并行能生效的前提）**

1. `tool_call` **不要阻塞 WS 读循环**：用 `asyncio.create_task`（或等价）并发执行，才能边跑工具边收 `tool_cancel`  
2. **同一轮可能同时收到多个 `tool_call`**（Agent 未关闭并行时，最多 4 个并发）。Bridge 必须能同时处理多个调用，**不要**假设「一次只来一个」；若业务有状态机/单文件锁，**优先在管理台关闭该 Agent 的「并行执行工具」**（`runtime.parallel_tool_calls: false`），而不是在 Bridge 里静默丢弃并发调用  
3. 用工厂下发的 **`call_id` 原样**登记进行中任务 / 进程；取消与结果都按同一 `call_id`  
4. 聊天「停止」= 工厂停收 + 取消等待 + **向 Bridge 发 `tool_cancel`**；若不实现取消，远端进程会继续跑完

**`tool_cancel` 协议（长任务必做）**

工厂 → Bridge：

```json
{
  "type": "tool_cancel",
  "call_id": "call_...",
  "session_id": "..."
}
```

Bridge 收到后应：

1. 按 `call_id` 找到进行中的工作（子进程 / 任务 / 远程 job）  
2. **真正中止**：`kill` 进程组、取消 async task、调用业务侧 abort API 等  
3. 若尚未回过结果，回一条 `tool_result`（`call_id` 原样）：

```json
{
  "type": "tool_result",
  "call_id": "call_...",
  "ok": false,
  "error": "cancelled",
  "cancelled": true
}
```

参考：`apps/demo-bridge` 的 `handle_tool_cancel` + `run_command`（`start_new_session` + `killpg`）。

- 工厂**先解除等待再转发** `tool_cancel`，所以 Bridge 事后补发的 `tool_result` 会被忽略 —— 不必重试，也不要因为收到 `tool_cancel` 就停止回结果（正常完成时照常回）
- 未绑定 `client_device_id` 的会话收不到 `tool_cancel`（工厂无处可发）；这类会话的远端工作只能靠工具自身超时

**`ask_client` 协议（必做 checklist）**

工厂 → Bridge：

```json
{
  "type": "approval_check",
  "check_id": "apprchk_...",
  "name": "run_command",
  "arguments": { "command": "free -h" },
  "session_id": "..."
}
```

Bridge → 工厂（`check_id` 原样带回）：

```json
{
  "type": "approval_check_result",
  "check_id": "apprchk_...",
  "ok": true,
  "require": false,
  "reason": "只读查询免审"
}
```

- `require: false` → 工厂直接执行该工具；`true` → 聊天里弹审批卡  
- 这是 Bridge 本地策略，**不要**注册成 Agent `tools[]` 里的工具  
- 参考：`apps/demo-bridge` 的 `handle_approval_check`

可参考工厂仓库 `apps/demo-bridge`、`apps/demo-web`（宿主嵌入）。

### G. 自检

1. 业务后端能换到 `embed_token`  
2. 宿主 iframe 能发消息、流式回复  
3. `GET /api/agents/{id}` 配置正确；有 Session 时变量已渲染  
4. 有技能包时：`GET .../skill-packages` 能列出；对话里可 `skill_load`（无需 Bridge）  
5. 有 Bridge 时 timeline 出现业务工具调用  
6. 若 tools 含 `ask_client`：Bridge 能收到 `approval_check` 并回 `approval_check_result`  
7. 有长任务工具时：点「停止」后 Bridge 收到 `tool_cancel`，本地进程/任务被中止（不是只停聊天流）  
8. Agent 开并行时：连发两次同一轮工具能同时执行（不是第二个排队等第一个）  

---

## 4. Guardrails

- 只用本 Agent AK/SK；Secret 仅后端  
- PATCH `tools`/`ui_theme` 前先 GET 再合并（整表替换）  
- 技能用 skill-packages 上传/覆盖/改 description；不要往 Agent JSON 塞正文，不要让 Bridge 实现 skill 工具  
- 缺地址或凭证时问用户  

## 5. 完成后告诉用户

- 业务里改了哪些文件（换 token 接口、宿主页、Bridge）  
- `AGENT_ID`、embed URL 形态（token 打码）、有 Bridge 时的 `deviceId`  
- 若上传了技能包：包名 / version  
- 若轮换了 SK：提醒只显示一次，写入业务密钥管理  
