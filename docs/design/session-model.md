# 设计：Session 存储与消息模型

> 状态：草案 v1  
> 对应路线图：Phase B（与 Phase A 强耦合，建议设计一起定、实现可同迭代）  
> 关联：[react-loop.md](./react-loop.md)

---

## 1. 问题

当前模型过于扁平：

```text
sessions 1──* messages(role, content, tool_calls_json, tool_call_id)
```

缺口：

- 分不清「一次用户请求」的边界（无法表达 run 状态：进行中 / 取消 / 超限）  
- 无法干净存 `reasoning`，历史回放与 UI「展开/收起」缺少结构  
- `list_messages` 全量既给 UI 又给 LLM，违反业界「**持久化 ≠ 模型输入**」共识  
- 无分页、无删除；管理台只能看原始行  

目标：Session 成为**可回放的真相来源**，并显式提供两种投影：

1. **UI Timeline** — 完整过程（思考、工具、状态）  
2. **LLM Input** — 合法、成对、经预算裁剪的 OpenAI-compatible messages  

---

## 2. 业界共识

| 实践 | 含义 |
|---|---|
| OpenAI Agents：**Session + RunItems** | 会话存历史；每次 run 产生 typed items；tool call/output **同 call_id**；replay 前要 sanitizer |
| OpenAI：**Generated / Session / Model Input 三视图** | 不要强行共用一个 list |
| LangGraph：**thread_id + checkpoint** | 短程记忆在 thread；persist 后仍要在调模型前 trim/summarize |
| LangChain Messages | `AIMessage.tool_calls` ↔ `ToolMessage.tool_call_id` 成对，删历史时不能拆对 |

我们选 **「OpenAI Items 语义 + 关系型落库」**，不引入 LangGraph 依赖，但保留「线程内短记忆 / 跨会话长记忆」的分界（长记忆 Phase E）。

---

## 3. 领域模型

```text
Session                    # 对话线程（≈ thread_id）
  id, agent_id, title, user_key, status, created_at, updated_at
  │
  ├── Run[]                # 一次用户发送（≈ Agents SDK 的一次 Runner.run）
  │     id, status, user_input, model, rounds_used,
  │     error_message?, started_at, finished_at
  │     │
  │     └── Item[]         # 有序步骤（≈ RunItem）
  │           id, seq, type, payload, created_at
  │
  └── (可选) SessionSummary  # Phase D：压缩后的摘要锚点
```

### 3.1 Session

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | PK |
| `agent_id` | str | 绑定 Agent |
| `title` | str? | 可自动取首条 user，可改名 |
| `user_key` | str? | 业务用户稳定 ID；新建必填；绑定长期记忆 |
| `status` | `active \| archived` | 归档后默认不进列表 |
| `created_at` / `updated_at` | datetime | |

> 会话与设备无关：同一 `sessionId` 可在任意端打开/续聊。本地 Bridge 仅作 client tool 的在线通道，由服务端自动选路，不进入 Session / Chat API。长期记忆见 [memory.md](./memory.md)。

### 3.2 Run

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | PK，即 `run_id` |
| `session_id` | str | FK |
| `status` | 见 react-loop | |
| `user_input` | text | 冗余便于列表展示 |
| `model_name` | str? | 实际模型 |
| `rounds_used` | int | |
| `finish_reason` | str? | `completed \| limit_reached \| cancelled \| error` |
| `error_message` | text? | |
| `started_at` / `finished_at` | datetime | |

约束：同一 Session **同一时刻只允许一个 `running` Run**（简化并发；重复提交返回 409 或排队——首期 409）。

### 3.3 Item

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | PK |
| `run_id` | str | FK |
| `session_id` | str | 冗余索引，便于按 session 拉时间线 |
| `seq` | int | Run 内单调递增 |
| `type` | enum | `user_message \| reasoning \| assistant_message \| tool_call \| tool_result \| system_notice` |
| `call_id` | str? | tool 成对关联 |
| `name` | str? | tool 名 |
| `content` | text? | 文本或 JSON 字符串 |
| `payload_json` | text? | 结构化：args、usage、截断信息、原始 tool_calls 等 |
| `visibility` | `user \| debug` | `debug` 默认不进精简 UI |
| `created_at` | datetime | |

`system_notice`：如「已压缩较早上下文」，只进 UI，不进 LLM。

---

## 4. 三种视图（最重要的设计点）

```text
                ┌──────────────────┐
                │  DB Items (真源)  │
                └────────┬─────────┘
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
    UI Timeline   LLM Messages    Admin/Audit
    (完整过程)     (合法子集)      (含 debug)
```

### 4.1 `to_ui_timeline(session_id)`

按 `created_at/seq` 输出，供聊天页与回放：

```json
{
  "session_id": "...",
  "runs": [
    {
      "run_id": "...",
      "status": "completed",
      "user_message": "...",
      "steps": [
        { "type": "reasoning", "content": "...", "collapsed_hint": "思考 1.2s" },
        { "type": "tool_call", "call_id": "c1", "name": "list_dir", "arguments": {} },
        { "type": "tool_result", "call_id": "c1", "ok": true, "preview": "..." },
        { "type": "assistant_message", "content": "目录如下…" }
      ]
    }
  ]
}
```

UI 规则（给 Phase C）：

- Run `running`：steps 默认展开  
- Run 终态：reasoning / tool 组默认收起，只留摘要条 + 最终 `assistant_message`  

### 4.2 `project_for_llm(session_id, *, budget)`

产出 OpenAI-compatible `messages[]`：

1. `system`（Agent prompt + skills + 未来 memory）  
2. 历史 Run 中投影出的 user / assistant / tool 序列  
3. 当前 Run 已产生的前缀（多轮 tool 时）  

规则：

- `assistant` 若含 `tool_calls`，后面必须紧跟对应 `tool` messages（按 call_id）  
- `reasoning`：仅当模型适配器声明 `requires_reasoning_replay` 时注入（如 DeepSeek）  
- `system_notice` / 纯 UI 字段：丢弃  
- **预算裁剪**（Phase D）在此函数内调用，不在 loop 里散落  

### 4.3 Admin 视图

可看 `payload_json`、错误栈、截断前后对比；聊天 UI 默认不暴露。

---

## 5. 表结构方案对比

### 方案 A — 扩展单表 messages（改动小）

增加 `run_id`, `seq`, `type`, `payload_json`, `visibility`；用 `type` 替代部分 `role` 语义。

- 优点：迁移快  
- 缺点：继续「什么都是 message」，Run 级状态别扭  

### 方案 B — `runs` + `items` 两表（推荐）

- 优点：与业界 Run/Item 对齐；状态机清晰；UI 按 run 折叠自然  
- 缺点：一次迁移 + 双写/回填  

**决议：采用方案 B。** 现有 `messages` 表迁移期保留只读兼容 API，或一次性脚本导入为 items。

### 迁移步骤（实现时）

1. 建 `runs`、`items`  
2. 旧 messages 按时间粗切：连续 user 起新 run；中间 assistant/tool 归入该 run（best-effort）  
3. `/api/sessions/{id}/messages` 改为由 items 投影的**兼容 DTO**（旧前端不炸）  
4. 新增 `/api/sessions/{id}/timeline` 返回结构化 runs/steps  
5. 稳定后弃用写 messages  

---

## 6. API 草图

| API | 说明 |
|---|---|
| `GET /api/sessions` | 分页、`agent_id`、默认排除 archived；平台可 `include_archived` |
| `POST /api/sessions` | 创建空会话 |
| `PATCH /api/sessions/{id}` | 改标题、归档（客户端 AK/SK / embed） |
| `POST /api/sessions/{id}/clear-history` | 软清除：`history_cleared_at` 水位；读接口与 LLM 上下文均过滤 |
| `DELETE /api/sessions/{id}` | 软删 → archived |
| `DELETE /api/sessions/{id}?hard=true` | 硬删相关 rows（仅平台 Key / 管理台） |
| `GET /api/sessions/{id}/timeline` | **主读模型** UI/管理台 |
| `GET /api/sessions/{id}/messages` | 兼容旧扁平列表 |
| `POST /api/chat` | 创建 run + SSE；首包带 `session_id`+`run_id` |
| `POST /api/runs/{run_id}/cancel` | 停止 |

Chat 请求体可保持现状，响应事件增加 `run_id`。

---

## 7. 崩溃与一致性

| 场景 | 策略 |
|---|---|
| 进程在 `running` 中挂掉 | 启动时或下次读取将超时 run 标为 `failed`（`error_message=interrupted`） |
| 已写 tool_call 未写 result | 补偿写 `tool_result(ok=false, error=interrupted)`，保证成对 |
| 客户端断开 / 刷新 | **不取消** Run；后台继续执行；UI 回连后通过 timeline 轮询接上 |
| 进程重启 | 启动时将无活任务的 `running` 标为 `failed` |
| 重复提交同一 session | 若有 running → 409 |
| 显式停止 | `POST /api/runs/{id}/cancel` |

不做完整 LangGraph checkpoint time-travel（成本高）；先保证 **不出现半截 tool 对** 与 **可解释的失败状态**。

---

## 8. 存储内容规范

### tool_call.payload

```json
{
  "id": "call_abc",
  "type": "function",
  "function": { "name": "list_dir", "arguments": "{\"path\":\".\"}" }
}
```

尽量保留厂商原始结构，便于精确 replay。

### tool_result.content

- `content`：给模型的字符串（可已截断）  
- `payload_json.truncated`：是否截断、`original_chars`  
- 超大结果：后续可外置对象存储，item 只留引用（Phase 后置）  

### reasoning.content

纯文本或分段；流式期间可先写临时 row，done 时更新最终全文（或只存最终，delta 仅走 SSE——**推荐 SSE 走 delta、DB 存最终**，减少写放大）。

---

## 9. 与上下文压缩、记忆的接口预留

| 能力 | 挂点 |
|---|---|
| Phase D 压缩 | `project_for_llm` 输入 items → 输出 messages；可写 `system_notice` + `session_summaries` 表 |
| Phase E 记忆 | 不进 items 主链；prompt 组装：`system → skills → memory → projected history` |

切勿把长期记忆行塞进 `items` 假装成 user 消息。

---

## 10. 好的改进点清单（相对现状）

1. **Run 边界**：停止按钮、超限、失败都有归属  
2. **三视图分离**：修掉「UI 要全、模型要干净」的根本矛盾  
3. **call_id 成对不变式**：用 DB 约束或写入校验强制  
4. **时间线 API**：聊天与管理台共用，避免两套组装  
5. **兼容层**：旧 `/messages` 暂留，降低前端爆炸半径  
6. **单 running run**：简单并发模型，先正确再优化  
7. **reasoning 一等公民**：为「展开思考 / 完成后收起」打地基  

---

## 11. 验收标准

- [ ] 新会话多轮 tool 后，刷新页面：思考（若有）、工具、最终回答可还原  
- [ ] `project_for_llm` 输出可被 OpenAI-compatible 接口接受（tool 成对、角色合法）  
- [ ] 人为 kill 进程后，run 不会永远停在 `running`  
- [ ] 管理台能按 run 折叠查看过程  
- [ ] 旧 embed 在兼容 DTO 下仍能加载历史（退化展示可接受）  

---

## 12. 非目标

- 完整分支时间旅行 / 从任意 checkpoint 恢复  
- 多模态附件存 DB  
- 跨 Session 记忆（Phase E）  
- 多租户隔离字段  

---

## 13. 决议摘要

1. **Session → Run → Item**，选两表方案  
2. 强制 **存储 / UI / LLM** 三视图  
3. 新增 **timeline API**；保留 messages 兼容  
4. 同时刻单 running run  
5. reasoning、tool 过程进 Item，供 Phase C 展开收起  

---

## 14. 修订记录

| 日期 | 说明 |
|---|---|
| 2026-11-09 | 初稿：对齐 Agents SDK Items + LangGraph 成对/投影共识 |
