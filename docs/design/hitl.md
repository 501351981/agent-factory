# 设计：Human-in-the-Loop（人工介入）

> 状态：已实现 v1.1（Ask User + Approval）  
> 关联：[react-loop.md](./react-loop.md)、[chat-ux-and-sse.md](./chat-ux-and-sse.md)

---

## 1. 目标

支持 Agent 在 ReAct 中途暂停、释放 worker，用户介入后**同一 Run**继续：

| 形态 | 触发 | 用户动作 |
|---|---|---|
| **Ask User** | builtin `ask_user` | 回答 / 选项 |
| **Approval** | `ToolDef.require_approval=true` 的工具在执行前 | approve / edit / reject |

---

## 2. 职责划分

| 工厂 | 客户端 |
|---|---|
| Ask：`ask_user`；Approval：执行前闸门 | 问答卡 / 审批卡 |
| Run → `waiting_human`，落库后**释放 Task** | `POST .../resume` |
| resume 写 `tool_result`（Ask）或执行工具后再写（Approval） | 订阅 SSE / timeline |

---

## 3. 状态与资源

```text
running → (ask_user | require_approval tool) → waiting_human  → (resume) → running → …
                                                    │
                                                    └─(timeout / 新消息抢占)→ cancelled
```

- 进入 `waiting_human` 后 worker **结束**，不占用 asyncio Task / LLM
- 仅 DB 保留 pending `tool_call` + `waiting_expires_at`（默认 24h）
- `heal_orphaned_runs` **不**把 `waiting_human` 标失败
- **同一 Session 同时只允许一种 HITL**（复用同一 `waiting_human`，不新增 Run status）

Pending 识别：

- Ask：`tool_call.name == ask_user` 且无同 `call_id` 的 `tool_result`
- Approval：`tool_call.payload.hitl == "approval"` 且无同 `call_id` 的 `tool_result`

---

## 4. Approval 细节

### ToolDef

```text
require_approval: bool = false   # 仅 client / remote_http 有意义；builtin 忽略
```

闸门位置：持久化 `tool_call` **之后**、`execute_tool` **之前**。  
同轮若先碰到 Approval，后续兄弟 tool_call 写 `skipped_due_to_approval`（与 Ask 对称）。

### SSE

- `tool_approval_required`：`run_id`, `call_id`, `name`, `arguments`, `expires_at`
- `done`：`status=waiting_human`

### Resume

`POST /api/runs/{run_id}/resume`

```json
{
  "call_id": "...",
  "decision": "approve | edit | reject",
  "edited_arguments": { },
  "answer": null,
  "selected_option": null
}
```

| decision | 行为 |
|---|---|
| `approve` | 用原 `arguments` 执行工具 → 写 `tool_result` → continue |
| `edit` | 用 `edited_arguments`（必填 object）执行 → 写 `tool_result`（含 `edited:true`）→ continue |
| `reject` | 不执行；写 `{ok:false, rejected:true, error}` → continue（模型可改道） |

Ask 路径仍用 `answer` / `selected_option`（`decision` 可省略）。

---

## 5. Ask User（既有）

### SSE

- `human_input_required`：`run_id`, `call_id`, `question`, `options?`, `allow_free_text`, `expires_at`
- `done`：`status=waiting_human`

### Resume

```json
{ "call_id": "...", "answer": "用户文本", "selected_option": null }
```

---

## 6. 同 Session 新消息 / 队列

`waiting_human` 期间禁止入队与开新 Run（HTTP 409）；须先 `resume` 或取消。  
Session FIFO 队列在 waiting 时冻结（见 [session-queue.md](./session-queue.md)）。

---

## 7. 非目标

- 长期挂起跨月恢复的复杂图编排
- 多问题 / 多审批并行（同轮只处理第一个 HITL 闸门）
- 按角色的审批流 / 多级审批

---

## 8. 验收

### Ask User

- [x] Agent 始终暴露 builtin `ask_user`
- [x] 调用后 Run → `waiting_human`，worker / Task 释放
- [x] SSE：`human_input_required` + `done(status=waiting_human)`
- [x] `POST /api/runs/{id}/resume` 写 tool_result 并继续同 Run
- [x] 超时 / 同会话新消息抢占 → cancelled
- [x] chat-ui 问答卡片（选项 + 自由文本）
- [x] `heal_orphaned_runs` 不误伤 `waiting_human`

### Approval

- [x] `ToolDef.require_approval` 可配置；闸门在 execute 前
- [x] SSE：`tool_approval_required` + `done(waiting_human)`；释放 worker
- [x] resume：approve 执行 / edit 改参执行 / reject 不执行并回灌
- [x] timeline / 刷新可还原审批卡（`payload.hitl=approval`）
- [x] chat-ui 审批卡（批准 / 修改 / 拒绝）
- [x] 单测覆盖闸门与三种 decision → `apps/server/tests/test_approval_hitl.py`
