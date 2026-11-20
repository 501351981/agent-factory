# 设计：多 Agent 协作（Phase G）

> 状态：实现中 v1  
> 依赖：Phase A–E、HITL、Session 队列  
> 关联：[react-loop.md](./react-loop.md)、[session-model.md](./session-model.md)、[hitl.md](./hitl.md)

---

## 1. 问题

单 Agent Runtime 已达可用。真实协作场景（项目负责人 → PM / 研发 / 测试）需要：

- 用户只面对一个入口 Agent（Lead）
- Lead 能把任务委托给其他已配置的 Agent
- 多角色共享本次任务的产物，而不是互灌长对话
- 全程可观测、可取消、可回放、有预算

非目标（本 Phase）：对等群聊 mesh、自由 handoff 转让会话所有权、跨租户团队市场、孙 Agent 无限递归。

---

## 2. 选定模型

**层级委托（Sub-Agent as Tool）+ Project Board（共享产物）**

```text
User ──► Lead Session / Lead Run
              │
              ├── agent_delegate(PM)   ──► Child Session + Child Run
              ├── agent_delegate(Dev)  ──► …
              └── agent_delegate(QA)   ──► …
                        │
                        ▼
                 Project Board (artifacts)
```

Lead 通过 builtin 工具 `agent_delegate` 拉起子 Run；子结果以**强制摘要**回灌父循环。跨角色状态走 Board。

---

## 3. 领域模型

### 3.1 Agent Team

| 字段 | 说明 |
|---|---|
| `id` | PK |
| `name` | 展示名 |
| `lead_agent_id` | 入口 Agent |
| `members_json` | `[{agent_id, role}]`，role 如 `pm` / `dev` / `qa` |
| `max_depth` | 默认 `1`（子 Agent 不可再 delegate） |
| `max_parallel` | 同轮最大委托数，默认 `3` |
| `max_child_rounds` | 子 Run 轮次上限 |
| `result_max_chars` | 回灌父循环的摘要上限 |
| `allow_async` | 是否允许 `wait=async` |
| `enabled` | 开关 |

Lead Agent 的 `runtime.multi_agent.team_id` 指向该 Team；`enabled=true` 时注入委托工具。

### 3.2 Run 扩展

| 字段 | 说明 |
|---|---|
| `parent_run_id` | 父 Run；根 Run 为空 |
| `root_session_id` | 用户可见的 Lead Session |
| `delegation_call_id` | 父侧 `agent_delegate` 的 call_id |
| `delegation_depth` | 0=用户 Run，1=直接子 Run |

Child Session：`visibility=system`，默认不进用户会话列表；`user_key` 与 Lead 相同。

### 3.3 Project Board

| 实体 | 说明 |
|---|---|
| `board_projects` | 挂在 `lead_session_id`（+可选 `team_id`） |
| `board_artifacts` | `path` 唯一（项目内）；`content`、`version`、`author_agent_id`、`status` |

与 KB 边界：KB = 稳定规范；Board = 本次任务临时产物。

---

## 4. 工具

### 4.1 `agent_delegate`

```json
{
  "agent_id": "optional-uuid",
  "role": "pm",
  "task": "撰写登录页 PRD",
  "context_refs": ["prd/outline.md"],
  "timeout_seconds": 600,
  "max_tool_rounds": 6,
  "wait": "await"
}
```

- `agent_id` 与 `role` 至少其一；`role` 在 Team.members 中解析
- 目标必须在 Team 白名单内
- `timeout_seconds` 默认 **600（10 分钟）**，上限 1800
- `wait=await`：阻塞至子 Run 终态，返回摘要
- `wait=async`：立即返回 `child_run_id` / `ticket`，用 `agent_delegate_status` 轮询（需 `allow_async`）

每次委托会新建一个 `visibility=system` 的子 Session + 子 Run（不进普通会话列表）；管理台在 Lead 会话的「子 Run」页查看，或调 `GET /api/sessions/{lead_session_id}/delegations`。

### 4.2 `agent_delegate_status`

```json
{ "child_run_id": "..." }
```

返回 `running | completed | failed | cancelled | waiting_human` + 摘要（若已结束）。

### 4.3 Board 工具

| 工具 | 作用 |
|---|---|
| `board_list` | 列出当前 Lead Session 下项目产物 |
| `board_read` | 按 path / id 读内容 |
| `board_write` | 创建或更新（version++） |

Board 工具挂在 Team 内**所有**成员（含 Lead）；自动绑定 `root_session_id` 对应的 project（无则创建）。

---

## 5. 运行时行为

### 5.1 委托状态机

```text
parent tool_call(agent_delegate)
  → create child session (visibility=system)
  → create child run (parent_run_id, depth=parent.depth+1)
  → register cancel child under parent
  → run child continue_chat_loop (独立 DB session)
  → summarize final assistant_message (+ artifact refs)
  → parent tool_result
```

深度闸门：`delegation_depth >= max_depth` 时不注入 `agent_delegate`（子默认 depth=1 且 max_depth=1 → 无委托工具）。

### 5.2 取消

`POST /api/runs/{parent}/cancel` → registry 级联 `request_cancel` 所有后代；子循环检查同一 registry。

### 5.3 HITL（v1）

子 Run 触发 `ask_user` / approval 时：**结束子 Run 为 failed**，向父返回

```json
{ "ok": false, "error": "child_hitl_required", "hitl": { ... } }
```

由 Lead 自行 `ask_user` 或改派。完整冒泡（父 waiting_human → resume 子）留待后续小版本。

### 5.4 并行与预算

- 同轮多个 `agent_delegate` 走现有并行工具路径；额外硬上限 `team.max_parallel`
- 子 `max_tool_rounds` ≤ `team.max_child_rounds`
- 回灌文本截断至 `result_max_chars`
- 团队并发：同一 `root_session_id` 下同时 running 的子 Run ≤ `max_parallel`

### 5.5 SSE / 时间线

复用 `tool_call` / `tool_result`；`payload` 增加：

```json
{
  "delegation": {
    "child_run_id": "...",
    "child_agent_id": "...",
    "role": "pm",
    "wait": "await",
    "status": "running|completed|..."
  }
}
```

UI 将 `name=agent_delegate` 渲染为委托卡片；可按 `child_run_id` 拉取子 timeline（管理台）。

---

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| CRUD | `/api/teams` | Team 管理 |
| GET | `/api/teams/{id}/runs` | 某 Team 相关子 Run（可选） |
| GET/POST | `/api/sessions/{id}/board/...` | 按 Lead Session 管理 Board |
| GET | `/api/runs/{id}/children` | 列出直接子 Run |
| GET | `/api/sessions/{id}/delegations` | 列出该 Lead 会话下全部子 Run |

嵌入协议不变：业务只嵌 Lead；`user_key` 贯通子 Session。

---

## 7. 失败语义

| 情况 | 父侧 tool_result |
|---|---|
| 成员不在白名单 | `ok:false` 立即失败 |
| 超深度 / 超并行 | `ok:false` |
| 子超时 | cancel 子 + `ok:false, error:timeout` |
| 子 cancelled | `ok:false, error:cancelled` |
| 子 completed | `ok:true, summary, artifacts[], child_run_id` |
| 子 HITL | `ok:false, error:child_hitl_required, hitl` |

---

## 8. 验收清单

- [x] Team CRUD；Lead runtime 绑定 `team_id` 后出现 `agent_delegate`
- [x] await 委托跑完成员 Agent，摘要回灌
- [x] 父 cancel 级联子 cancel
- [x] depth=1 时子 Agent 无 `agent_delegate`
- [x] Board list/read/write 跨成员可见（同 lead session）
- [x] `wait=async` + `agent_delegate_status` 可轮询
- [x] 管理台可建 Team、查看 Board / 子 Run
- [x] 核心路径单测（`test_multi_agent.py`）

---

## 9. 修订记录

| 日期 | 说明 |
|---|---|
| 2026-11-16 | 初稿并落地 G1–G3 首版 |
