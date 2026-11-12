# 设计：ReAct 循环引擎

> 状态：草案 v1  
> 对应路线图：Phase A  
> 关联：[session-model.md](./session-model.md)、后续 `chat-ux-and-sse.md`

---

## 1. 问题

当前 `runtime/loop.py` 是一个「能跑」的 for-loop：

- 无显式状态，难取消、难观测、难测试  
- 超 `max_tool_rounds` 直接 `error`，体验差  
- LLM 非流式；无 thinking / reasoning 回传  
- 每轮从 DB 全量读 history，语义上把「存储」和「模型输入」绑死  
- 并行多个 `tool_calls` 时串行执行，且缺少统一错误包装  

目标：做成业界常见的 **Agent Run Loop**——一次用户输入对应一次 Run，循环直到终态，过程可流式对外、可落库回放。

---

## 2. 业界共识（我们对齐什么）

| 来源 | 可借鉴点 |
|---|---|
| **OpenAI Agents SDK** | 一次 `Runner.run` = 一次应用层 turn；内部循环：调模型 → 有 tool 就执行再继续 → 无 tool 则结束；产物是类型化 **RunItem**（message / tool_call / tool_output / reasoning）；**持久化视图 ≠ 下一次模型输入视图** |
| **LangGraph** | 显式状态 + 检查点；`tool_calls` 必须与对应 `ToolMessage` 成对；**持久化不全量喂给模型**，裁剪/摘要在「调模型前」做 |
| **经典 ReAct 论文** | Thought → Act → Observe 交替；实现上现代模型多把 Thought 收进 `reasoning` / 隐藏链，Act 收成 tool_calls |
| **ai-ops-agent** | 超轮次再无 tools 收尾总结；DeepSeek `thinking` + `reasoning_content` 必须回传 |

我们**不引入**完整图编排框架，但采用同样的语义分层：

```text
Session (对话线程)
  └── Run / Turn (一次用户请求的执行)
        └── Items (类型化步骤：reasoning / tool_call / tool_result / message)
```

---

## 3. 核心概念

### 3.1 Run（推荐对外仍叫 turn，对内称 run）

一次 `POST /api/chat`（带一条 user message）开启一个 **Run**：

| 字段 | 说明 |
|---|---|
| `run_id` | UUID |
| `session_id` | 所属会话 |
| `agent_id` | 执行时的 Agent（配置快照可选后续再做） |
| `status` | `queued \| running \| completed \| failed \| cancelled \| limit_reached` |
| `model` / `temperature` | 本 Run 实际使用 |
| `rounds_used` | 已消耗的模型调用轮数（含收尾轮） |
| `started_at` / `finished_at` | 时间 |

### 3.2 状态机

```text
                    ┌──────────────┐
                    │   created    │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
         ┌─────────│   running    │◄────────────┐
         │          └──────┬───────┘             │
         │                 │                     │
         │         call model (stream)           │
         │                 │                     │
         │     ┌───────────┴───────────┐         │
         │     ▼                       ▼         │
         │  has tool_calls?        final text    │
         │     │                       │         │
         │     ▼                       ▼         │
         │  execute tools ──observe──► completed │
         │     │                                 │
         │     └──── rounds < max? ──────────────┘
         │              │ no
         │              ▼
         │        limit policy
         │         ├─ summarize → completed (limit_reached)
         │         └─ stop → limit_reached
         │
         ├─ cancel ──► cancelled
         └─ error  ──► failed
```

说明：

- **一轮（model round）** = 一次 LLM 调用（可能伴随一组 tool 执行）  
- **Observe** = 把 tool 结果写回 item 流，并进入下一轮模型调用  
- 取消可在「等 LLM」或「等 tool」时发生；已产生的 items 保留，Run 标 `cancelled`

### 3.3 Item 类型（对齐 OpenAI RunItem 思想，精简集）

| type | 含义 | 是否进入模型输入 |
|---|---|---|
| `user_message` | 用户本轮输入 | 是 |
| `reasoning` | 模型思考（thinking / reasoning_content） | **按厂商要求**：DeepSeek tool 多轮必须回传；UI 始终可展示 |
| `assistant_message` | 助手可见文本（可流式） | 是（最终文本） |
| `tool_call` | 一次函数调用（name/args/`call_id`） | 是（挂在 assistant tool_calls） |
| `tool_result` | 对应 `call_id` 的结果 | 是（role=tool） |
| `run_status` | 系统事件：压缩提示、limit、error（仅 UI/审计） | **否** |

硬约束（业界踩坑总结）：

1. 每个 `tool_call.call_id` 必须有且仅有一个配对的 `tool_result`（失败也要有 result）  
2. 同一轮多个 tool_calls：先全部落 `tool_call`，再执行；结果按 call_id 关联  
3. 禁止把「仅 UI 的 status」塞进 messages 列表喂给模型  

---

## 4. 配置面（挂在 Agent，带全局默认）

```json
{
  "runtime": {
    "max_tool_rounds": 8,
    "on_max_rounds": "summarize",
    "tool_timeout_seconds": 30,
    "run_timeout_seconds": 300,
    "parallel_tool_calls": false,
    "tool_result_max_chars": 12000
  }
}
```

| 项 | 默认 | 说明 |
|---|---|---|
| `max_tool_rounds` | 8 | 单 Run 内「带 tools 的模型轮」上限；收尾 summarize 另计 1 次 |
| `on_max_rounds` | `summarize` | `summarize`：无 tools 再问一次；`stop`：结束并标 `limit_reached` |
| `parallel_tool_calls` | `false` | 首期串行更稳；后续可对无副作用 tool 并行 |
| `tool_result_max_chars` | 12000 | 回灌模型前截断（头尾保留），完整结果可仍存 DB |

全局 `Settings.max_tool_rounds` 仅作默认值，**Agent 配置优先**。

---

## 5. 循环伪代码（目标形态）

```text
run = create_run(session, user_message)
emit turn_start / run_start
persist user_message item

rounds = 0
while rounds < max_tool_rounds:
  if cancelled: finalize(cancelled); return

  input = project_for_llm(session, run)   # 见 session-model：≠ 全量 UI items
  rounds += 1
  response = stream_llm(input, tools=..., thinking=...)

  persist/stream reasoning deltas
  if response.tool_calls:
     persist assistant stub + tool_calls
     for call in tool_calls:
        emit tool_call_*
        result = execute_tool(call) or error_result
        persist tool_result (截断进模型投影，全文可存 raw)
        emit tool_result
     continue

  persist assistant_message
  emit message_* / turn_done(completed)
  return

# 触达上限
if on_max_rounds == summarize:
  response = llm(input, tools=None)
  persist assistant_message
  emit turn_done(limit_reached)
else:
  emit turn_done(limit_reached)  # 无最终文本或附带系统提示
```

---

## 6. 流式与厂商差异

### 6.1 统一内部事件（给 SSE 用，Phase C 细化）

先在 loop 内产出结构化 dict，chat API 原样 SSE 出去：

- `run_start` / `run_done`  
- `reasoning_delta` / `reasoning_done`  
- `tool_call` / `tool_result`  
- `message_delta` / `message_done`  
- `error` / `cancelled`  

兼容过渡：旧前端仍可把 `message_done` 当整段 `message`；新前端走 delta。

### 6.2 DeepSeek / 思考模型

对齐 ai-ops-agent：

- 请求体在 deepseek 模型上带 `thinking: { type: "enabled" }`（可配置关闭）  
- 响应中的 `reasoning_content` 必须写入 item，并在后续轮次按厂商规则回传  
- UI 展示用 `reasoning_*` 事件；**不要**把思考内容默认拼进用户可见 `message`

### 6.3 OpenAI-compatible 流式

- `chat.completions` stream：解析 `delta.content`、`delta.tool_calls`（分片拼 args）、若有 `delta.reasoning_content` 同样处理  
- 工具轮：流式拼完 tool_calls 后再执行（避免半截 args 调 tool）

---

## 7. 取消与超时

| 机制 | 行为 |
|---|---|
| 客户端断开 / 刷新 | **不 cancel**；Run 在后台任务继续；仅显式 `POST /api/runs/{id}/cancel` 才停止 |
| `run_timeout_seconds` | 墙钟超时 → `failed` 或 `cancelled`，原因写入 run |
| `tool_timeout_seconds` | 单 tool 超时 → 写入失败 `tool_result`，**继续**循环（让模型自己决定是否重试） |

首期实现：

1. Chat Run 在后台任务执行；SSE 仅订阅事件，断线不 cancel  
2. 显式 cancel API（嵌入页「停止」按钮）  
3. 进程重启时 heal 无活任务的 `running` 行 

---

## 8. Tool 执行改进点

相对现状：

1. **结果契约**：统一 `{ ok, data?, error?, meta? }`；未知异常不得裸抛穿出 loop  
2. **截断**：`tool_result_max_chars` 只影响「投影给模型」；DB 可存 `content` + `content_full` 或外置  
3. **并行**：配置打开后，同轮无依赖的 calls 用 `asyncio.gather`；有副作用的 tool 可标 `serial: true`（后续）  
4. **HITL 扩展点**：item 可处于 `tool_call` + `status=awaiting_approval`；本 Phase 只留状态位，完整 UI 后置  

---

## 9. 与 Session 存储的边界

Loop **不**直接 `SELECT * FROM messages` 当唯一真相。正确分工：

| 模块 | 职责 |
|---|---|
| Loop | 推进状态机、调 LLM、调 tool、emit 事件、调用 repository 追加 item |
| Session repository | 持久化 Run/Item；提供 `iter_ui_timeline()` 与 `project_for_llm()` |
| Context budget（Phase D） | 在 `project_for_llm()` 内做裁剪/摘要 |

详见 [session-model.md](./session-model.md)。

---

## 10. 测试计划（精品门槛）

| 用例 | 期望 |
|---|---|
| 无 tool 纯问答 | 1 轮模型 → `completed`，有 assistant item |
| 单 tool 成功 | assistant(tool_calls) + tool_result + 最终文本 |
| 多 tool 同轮 | 每个 call_id 成对；顺序稳定 |
| tool 抛错 | 仍有 tool_result(ok=false)；模型可继续 |
| 触达 max rounds + summarize | 最后一轮无 tools；status=`limit_reached` 或 completed+标记 |
| 取消 | 已有 items 保留；status=`cancelled` |
| DeepSeek reasoning 回传（集成/录制） | 第二轮请求 messages 含 reasoning 字段（按适配器） |

全部可用 mock LLM（固定返回 tool_calls / 文本）做单测，不依赖真实 Key。

---

## 11. 迁移策略

1. **不一次砍掉**现有 flat `messages` 表：先引入 `runs` + 扩展字段，或双写  
2. Loop 先改为「写 RunItem 语义」，repository 层兼容旧 row → 新投影  
3. SSE 先 **增量事件 + 保留旧 `message`/`done`**，前端平滑升级  

具体表结构见 Session 设计。

---

## 12. 非目标（本 Phase）

- 多 Agent handoff / 子 Agent  
- 完整 HITL 审批流 UI  
- 分布式 run worker  
- 自动长期记忆抽取  

---

## 13. 决议摘要（准备实现时遵守）

1. 采用 **Session → Run → Items** 三层，而不是继续只有 messages 扁平等级  
2. **三种视图分离**：存储 / UI 时间线 / 模型输入  
3. 上限默认 **summarize 收尾**，不默认硬 error  
4. **call_id 成对**、失败也写 result  
5. 思考内容独立 item + 事件，按厂商规则回传  
6. Agent 级 runtime 配置覆盖全局默认  

---

## 14. 修订记录

| 日期 | 说明 |
|---|---|
| 2026-11-09 | 初稿：对齐 Agents SDK / LangGraph 共识，结合本仓库现状 |
