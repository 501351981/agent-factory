# 设计：定时任务（Phase H）

> 状态：实现中 v1（H1–H2 已落地）  
> 依赖：Phase A–E、Session 队列；与 Phase G 正交  
> 关联：[session-model.md](./session-model.md)、[session-queue.md](./session-queue.md)、[react-loop.md](./react-loop.md)、[chat-ux-and-sse.md](./chat-ux-and-sse.md)

---

## 1. 问题

用户常说：「10 分钟后提醒我喝水」「每天 9 点总结进度」「到点后飞书通知我」。

缺口：

- Agent 不能在对话循环里 `sleep` 冒充定时（不持久、不精确、占资源）
- 现有 Session 队列只有「现在排队」，没有 `run_at`
- 没有 Job 持久化与时钟唤醒；进程重启即丢「口头约定」

目标：提供**通用定时能力**——登记任务、多种调度、多种进 Session 的执行方式；到点后复用现有 Run / 工具生态。

**非目标（本 Phase）**

- 飞书 / 企微 / 邮件 / App Push 等**独立出站通道**（用户要飞书时，由到点 Run 内 Agent 调已有工具完成）
- host webhook delivery 层
- 用 Heartbeat / LLM 空转冒充精确定时
- 跨用户广播；多实例分布式 lease（v1 单实例原子抢占即可）

---

## 2. 选定模型

**持久化 Job + Scheduler Worker + 送进 Session**

```text
User ──► Session / Run
              │
              └── schedule_create ──► agent_schedules (DB)
                                              │
                         Scheduler tick ──────┤
                                              ▼
                                    resolve session + run_mode
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
           assistant_message          synthetic_user            isolated_session
           (写可见助手消息)         (隐藏 user + enqueue)     (新建 system Session + Run)
                    │                         │                         │
                    └─────────────────────────┴─────────────────────────┘
                                              │
                                              ▼
                                    现有 ReAct / 工具（含飞书等）
```

与 OpenClaw Automations 同构：**登记与执行分离**；工厂侧执行终点统一为 Session，不另建推送总线。

### 2.1 四维正交

| 维度 | 取值 | 含义 |
|---|---|---|
| `schedule_kind` | `at` / `every` / `cron` | 何时跑 |
| `session_target` | `origin` / `latest` / `session_id` | 跑到哪条会话（`isolated_session` 时忽略） |
| `run_mode` | `assistant_message` / `synthetic_user` / `isolated_session` | 怎么进 Session |
| `payload_text` | string | 固定文案或触发指令 |

---

## 3. 领域模型

### 3.1 `agent_schedules`

| 字段 | 说明 |
|---|---|
| `id` | PK |
| `agent_id` | 所属 Agent |
| `user_key` | 与 Session 一致的用户键 |
| `created_from_session_id` | 建 Job 时的会话（`origin` 解析源） |
| `created_by_run_id` | 可选；创建该 Job 的 Run |
| `name` | 可选展示名 |
| `schedule_kind` | `at` \| `every` \| `cron` |
| `run_at` | 首次/绝对触发时间（UTC，ISO）；`at` 必填 |
| `every_ms` | `every` 间隔毫秒 |
| `cron_expr` | 五段 cron（分 时 日 月 周） |
| `timezone` | cron 壁钟时区，默认 `Asia/Shanghai` |
| `session_target` | `origin` \| `latest` \| `session_id` |
| `session_id` | `session_target=session_id` 时必填 |
| `run_mode` | `assistant_message` \| `synthetic_user` \| `isolated_session` |
| `payload_text` | 助手可见文案，或注入的触发 prompt |
| `status` | `active` \| `paused` \| `completed` \| `cancelled` \| `failed` |
| `delete_after_run` | `at` 默认 `true`；周期任务默认 `false` |
| `next_run_at` | Worker 扫描键；创建时写入首次触发时间 |
| `last_run_at` | 上次实际开火时间 |
| `last_error` | 最近失败原因 |
| `attempt_count` | 连续失败次数（成功清零） |
| `last_fired_session_id` | 审计：实际写入/跑过的 Session |
| `last_fired_run_id` | 审计：触发的 Run（`assistant_message` 可空） |
| `claim_token` / `claimed_at` | 可选；原子抢占防双跑 |
| `created_at` / `updated_at` | 时间戳 |

索引建议：`(status, next_run_at)`；`(agent_id, user_key, status)`。

### 3.2 Session 扩展（`isolated_session`）

新建 Session 时：

- `visibility=system`（不进普通用户会话列表）
- 可选元数据：`schedule_job_id`（列或 JSON），便于管理台反查
- `user_key` / `agent_id` 与 Job 一致

### 3.3 消息 / Item 约定

| run_mode | 写入 | UI | LLM 输入 |
|---|---|---|---|
| `assistant_message` | 可见 assistant item/message | 展示 | 进入历史 |
| `synthetic_user` | `role=user`，`visibility=system`（或等价标记） | **不展示** | **进入**模型上下文 |
| `isolated_session` | 同 `synthetic_user` 的触发消息 + 后续 Run items | 管理台可看；用户列表默认隐藏 | 正常投影 |

触发消息建议前缀，便于审计与过滤：

```text
[schedule:<job_id>] <payload_text>
```

---

## 4. 调度语义

### 4.1 `at`（一次性）

- 创建时：`next_run_at = run_at`
- 成功开火后：若 `delete_after_run` → 删行或 `status=completed`；否则保持 completed 且不再调度
- 默认 `delete_after_run=true`

### 4.2 `every`（固定间隔）

- 创建时：`next_run_at = now + every_ms` 或显式 `run_at` 作为首次
- 成功后：`next_run_at = last_run_at + every_ms`（或 `now + every_ms`，设计实现时二选一并写进代码注释；**推荐** `max(now, last_planned) + every_ms` 避免追赶风暴）
- `every_ms` 下限建议 ≥ 60_000（1 分钟）；更短间隔属滥用，可配上限条数约束

### 4.3 `cron`

- 五段表达式 + `timezone`
- 成功后用 cron 库计算下一次 `next_run_at`（UTC 存储）
- 非法表达式在 `schedule_create` 时拒绝

### 4.4 Worker

```text
loop every TICK (5–10s):
  rows = SELECT ... WHERE status='active' AND next_run_at <= now()
         FOR UPDATE SKIP LOCKED  -- 或 claim_token CAS
  for job in rows:
    claim(job)
    try:
      execute(job)
      on_success(job)   # 推进 next_run_at / completed
    except:
      on_failure(job)   # attempt_count++; 指数退避改 next_run_at；超阈 → failed
```

- 进程启动与 `resume_idle_queues` 同期拉起 Worker
- v1 单工厂实例；多实例后再加 lease TTL
- 开火与 Session 忙冲突：`synthetic_user` / `isolated_session` 走现有 **Session FIFO 队列**，不另造并行通道

### 4.5 `session_target` 解析（到点时）

| 值 | 行为 |
|---|---|
| `origin` | 使用 `created_from_session_id`；若 Session 已删 → 失败 |
| `session_id` | 使用 Job.`session_id`；不存在 → 失败 |
| `latest` | 查询该 `user_key+agent_id` 下 `visibility=user` 且未软删的会话，按 `updated_at` 降序取一条；**若无** → **回退 `origin`**；origin 也无则失败 |

`run_mode=isolated_session` 时不解析 target，始终新建 Session。

---

## 5. 三种执行方式

### 5.1 `assistant_message`

**适用**：文案已定的提醒（喝水、到期提示）。

```text
resolve target session
  → append visible assistant message (payload_text)
  → optional: emit timeline/SSE-friendly item so online UI 可刷新
  → 不调用 LLM，不创建 Run（或仅写审计 Run/item，实现选轻量路径）
```

### 5.2 `synthetic_user`

**适用**：需要推理或工具（「到点打招呼」「查天气再告诉我」「飞书发一条」）。

```text
resolve target session
  → insert hidden user message (payload_text, visibility=system)
  → enqueue_or_start(session, message=payload_text, …)
  → 现有 ReAct：工具 / 记忆 / Board / HITL 全可用
```

对用户可见的是后续助手回复（及工具副作用，如飞书），不是那条仿造 user 消息。

### 5.3 `isolated_session`

**适用**：周期报告、巡检等，避免污染主对话时间线。

```text
create Session(visibility=system, schedule_job_id=…)
  → insert hidden user trigger
  → enqueue_or_start
  → 结果留在专用 Session；管理台按 job 查看
```

若业务要「结果出现在主会话」：应在 `payload_text` 里要求 Agent `board_write` 或再说话术；**或**产品后续加「摘要回写 origin」——**本 Phase 不做自动回写**，避免隐式双写。

### 5.4 选用规则（写入工具 description）

| 用户意图 | 推荐 |
|---|---|
| 固定文案提醒 | `assistant_message` + `origin`/`latest` |
| 要思考 / 调工具（含飞书） | `synthetic_user` + 合适 target |
| 后台任务、怕刷屏 | `isolated_session` |

---

## 6. 工具（builtin）

Agent 配置：`runtime.scheduler.enabled`（默认 `true` 或按产品决定；关闭则不注入工具）。

### 6.1 `schedule_create`

```json
{
  "name": "喝水提醒",
  "schedule_kind": "at",
  "run_at": "2026-11-18T10:10:00+08:00",
  "every_ms": null,
  "cron_expr": null,
  "timezone": "Asia/Shanghai",
  "session_target": "origin",
  "session_id": null,
  "run_mode": "assistant_message",
  "payload_text": "该喝水了 💧",
  "delete_after_run": true
}
```

- `schedule_kind=at`：必填 `run_at`（可接受相对描述由**模型换算成绝对时间**再传入；工具本身只收绝对时间或 epoch）
- `every`：必填 `every_ms`；可选首次 `run_at`
- `cron`：必填 `cron_expr`；可选 `timezone`
- `session_target=session_id` 时必填 `session_id`
- `run_mode=isolated_session` 时可省略 `session_target`（服务端忽略）
- 返回：`{ ok, job_id, next_run_at, … }`

配额（实现期）：同一 `(agent_id, user_key)` 下 `active` Job 上限（如 50），超限拒绝。

### 6.2 `schedule_list`

```json
{ "status": "active" }
```

返回当前用户在该 Agent 下的任务摘要（id / name / next_run_at / run_mode / schedule_kind）。

### 6.3 `schedule_cancel`

```json
{ "job_id": "..." }
```

`status → cancelled`；已 claim 执行中的本次开火不回滚，但不再调度下次。

### 6.4 （H2）`schedule_pause` / `schedule_resume`

暂停将 `status=paused` 且保留 `next_run_at`；恢复改回 `active`（若已过期则立即或顺延一次，实现时定一种并测）。

---

## 7. 运行时行为

### 7.1 创建路径

```text
user turn → tool_call(schedule_create)
  → validate + insert agent_schedules
  → tool_result(job_id, next_run_at)
  → assistant 口头确认
```

### 7.2 开火状态机

```text
active & due
  → claiming
  → executing
       ├─ success → (at & delete) completed | (recurring) active + next_run_at
       └─ failure → active + backoff next_run_at | failed
paused / cancelled / completed / failed → Worker 跳过
```

### 7.3 与 HITL / 队列

- 目标 Session 已有 `running` / `waiting_human`：`enqueue_or_start` 入队，行为与用户手动发送一致
- `assistant_message` 不占用 Run worker；若需在线可见，依赖 timeline 轮询或后续轻量 SSE（H3）

### 7.4 取消级联

- 取消 Job ≠ 取消已在跑的 Run；已 enqueue 的项可保留（与「用户已发送」一致）
- 可选（非 v1）：`schedule_cancel` 同时取消 `last_fired_run_id` 若仍 running

### 7.5 失败语义

| 情况 | 行为 |
|---|---|
| 目标 Session 不存在 | `attempt_count++`；退避；超阈 `failed` |
| `enqueue` / Run 启动失败 | 同上 |
| Run 自身 failed | Job 仍算「已调度成功」还是「开火失败」：**推荐**调度成功（已送达 Session）；Run 失败由 timeline 呈现。周期任务照常推下次 |
| cron 解析错误 | 仅允许在 create 时失败 |

---

## 8. API 与管理台（实现靠后）

| API | 说明 |
|---|---|
| `GET /api/agents/{agent_id}/schedules` | 列表（管理端 / AKSK） |
| `GET /api/schedules/{job_id}` | 详情 |
| `POST /api/schedules/{job_id}/cancel` | 取消 |
| `POST /api/schedules/{job_id}/pause` | H2 |
| `POST /api/schedules/{job_id}/resume` | H2 |

管理台：Agent 编辑页或独立「定时任务」视图；isolated Session 从 Job 详情链过去（类似 Lead「子 Run」）。

chat-ui：无新推送协议；在线用户靠现有 SSE / timeline 看到助手消息。H3 可给定时来源消息加小标记。

---

## 9. 安全与滥用

- Job 必须绑定 `user_key`，不可跨用户写入他人 Session
- `session_id` 目标必须同 `agent_id` + `user_key`
- `payload_text` 长度上限（如 8k）
- active Job 配额；`every_ms` / cron 最小间隔
- 禁止工具接受「sleep_seconds 然后回调」式长阻塞

---

## 10. 验收清单

**H1**

- [x] migrate 出 `agent_schedules`（`create_all` + `AgentScheduleRecord`）
- [x] Worker 能扫 `at` 并开火（`start_scheduler_worker`）
- [x] `assistant_message` 写入目标 Session 可见助手消息且不调 LLM
- [x] `synthetic_user` 经 `enqueue_or_start` 走现有 Run（触发文案带 `[schedule:id]`，timeline 对用户气泡隐藏）
- [x] `schedule_create` / `list` / `cancel` 注入与单测
- [x] 进程启动拉起 Worker（与 `resume_idle_queues` 同期；DB 持久化保证重启后仍 due）

**H2**

- [x] `every` / `cron` + timezone（`croniter`）
- [x] `latest` / `session_id` / `isolated_session`（`sessions.schedule_job_id`）
- [x] pause/resume 工具 + 管理 API（`/api/agents/{id}/schedules`、`/api/schedules/{id}/…`）
- [x] 配额与非法参数拒绝（create 校验）

**H3**

- [x] 管理台列表与 Job → Session 链接（`/schedules` + stats 配额条）
- [x] chat-ui 定时来源标识（`from_schedule` → 「定时」徽标）
- [x] 失败/成功结构化日志 + `/schedules/stats` 配额与状态计数

---

## 11. 分阶段实现（编码不在本文档提交内）

| 子阶段 | 内容 |
|---|---|
| H1 | 表 + migrate + Worker(`at`) + `assistant_message` / `synthetic_user`（`origin`）+ `schedule_*` + 单测 |
| H2 | `every` / `cron`、`latest` / `session_id`、`isolated_session`、暂停、管理 API |
| H3 | 管理台、UI 标识、配额与失败可观测 |

---

## 12. 修订记录

| 日期 | 说明 |
|---|---|
| 2026-11-18 | 草案 v1：Job + Worker + 三 run_mode；明确不做独立推送通道 |
| 2026-11-18 | H1 落地：`agent_schedules`、`schedule_*`、Worker(`at`)、`assistant_message`/`synthetic_user`(origin) |
| 2026-11-18 | H2 落地：every/cron、session_target、isolated_session、pause/resume、管理 API |
| 2026-11-18 | H3 落地：管理台 SchedulesView、stats API、结构化 fire 日志、chat-ui「定时」标记 |
