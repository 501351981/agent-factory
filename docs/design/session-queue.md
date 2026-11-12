# 设计：Session 消息队列

> 状态：已实现 v1  
> 关联：[react-loop.md](./react-loop.md)、[hitl.md](./hitl.md)

---

## 1. 目标

Session 级 FIFO 待办：当前 Run 进行中仍可提交下一条指令；人离开后服务端按序自动续跑。

## 2. 模型

表 `session_queue_items`：

- `queued` → `dispatched`（已开 Run） / `cancelled`
- 绑定 `session_id`；`run_id` 在 dispatch 后回填

同一 Session：**至多 1 个 `running`**；`waiting_human` 时**不** dequeue。

## 3. 流程

```text
POST /api/chat
  ├─ 空闲 → 立刻 start_chat（SSE 跟 Run）
  ├─ running → 入队，短 SSE：queue_enqueued + done(queued)
  └─ waiting_human → 409（须先回答或取消）

Run 终态（非 waiting_human）
  └─ supervisor → dispatch 下一条 queued

waiting_human
  └─ 队列冻结；resume 跑完后再 dispatch
```

## 4. API

- `GET /api/sessions/{id}/queue` — 列出 `queued`
- `DELETE /api/sessions/{id}/queue/{item_id}` — 取消未跑项

## 5. 非目标

- 优先级 / 插队（除取消）
- 并行多 Run
- Redis / 跨进程认领（单进程 asyncio 足够；表结构可演进）
