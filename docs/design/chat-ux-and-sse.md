# 设计：对话流式与展示（Phase C）

> 状态：实现中 v1（含虚拟列表）  
> 依赖：Phase A/B（Run/Item + loop）

## SSE 事件（v1）

| type | 说明 |
|---|---|
| `session` / `run_start` | 会话与 Run 开始 |
| `reasoning_delta` / `reasoning_done` | 思考流式与收尾 |
| `message_delta` / `message` / `message_done` | 正文流式（保留 message 兼容） |
| `tool_call` / `tool_result` | 工具调用与结果 |
| `cancelled` / `error` / `done` | 终态 |

## UI 规则

- Run `running`：过程区（思考 + 工具）**默认展开**
- Run 终态：过程区**默认收起**为摘要条，可手动展开
- 历史通过 `/timeline` 回放，与实时同一套 Turn 组件
- 停止：仅「停止」按钮 → `POST /api/runs/{id}/cancel`（刷新/离开不断开任务）
- 刷新后续看：`GET /api/runs/{id}/events` 回放 EventHub 缓冲并继续实时 delta；hub 不可用时才退回 timeline 轮询

## 长对话渲染

- 列表使用 `@tanstack/vue-virtual`：**估算高度 + `measureElement` 实测**（过程区展开会经 ResizeObserver 重测）
- 仅挂载视口附近 Turn；流式粘底仍用 `stickToBottom` + `scrollToIndex(end)`
- 后续可再加「最近 N 轮分页加载」降低首包 timeline 体积

## 非本版

- 真 HITL 审批 UI（Ask User 已另见 hitl.md）
- timeline 分页（虚拟列表之上的数据层裁剪）
