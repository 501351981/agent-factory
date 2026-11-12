# Agent 工厂核心能力规划

> 状态：修订草案  
> 基线：`v0.1.0` 可跑通的单实例编排骨架  
> 导向：**先把 Agent 基础能力做成精品**，再谈鉴权与工程加固；**不做多租户**

本文是总路线。每个 Phase 开工前，应另开一份 `docs/design/<topic>.md` 把协议、数据模型和边界定清楚，再写代码。宁可慢一点，也不要「能跑就算完」。

---

## 1. 我们要做成什么样

目标不是「能调 LLM + 能调 tool 的 demo」，而是一套可嵌入业务的 **Agent Runtime**：

- 对话过程可观察、可回放、可中断  
- 工具调用是一等公民（过程可见，结果可折叠）  
- 长对话不崩（上下文有预算、有压缩策略）  
- 跨会话有记忆（可配置、可审计、可遗忘）  
- ReAct 循环语义清晰、参数可配、失败可恢复  

鉴权、部署、可观测等很重要，但排在 **核心对话能力达标之后**。

---

## 2. 能力地图（先做什么）

```text
┌─────────────────────────────────────────────────────────┐
│                    Agent Runtime 核心                    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ ReAct    │  │ Session  │  │ 对话体验 (SSE/UI)    │  │
│  │ 循环引擎 │←→│ 存储模型 │←→│ 思考/工具展开·收起   │  │
│  └────┬─────┘  └────┬─────┘  └──────────┬───────────┘  │
│       │             │                     │             │
│  ┌────▼─────┐  ┌────▼─────┐  ┌───────────▼───────────┐ │
│  │ 轮次/停止│  │ 上下文   │  │ 记忆 (长期；短期=历史) │ │
│  │ 上限策略 │  │ 预算压缩 │  │                       │ │
│  └──────────┘  └──────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼  (核心达标后再做)
              鉴权 · 密钥 · 部署 · 观测 · Tool 生态增强
```

| 优先级 | 能力 | 一句话 |
|---|---|---|
| P0 | **ReAct 循环引擎** | 清晰的 Thought→Act→Observe 状态机，可配轮次、可收尾、可取消 |
| P0 | **Session / 消息模型** | 会话与消息结构化存储，支持回放与 UI 重建 |
| P0 | **对话展示** | 生成中展开思考/工具过程，完成后收起；真流式 |
| P1 | **上下文预算与压缩** | 有上限、有策略、有可观测的裁剪结果 |
| P1 | **记忆** | 短期=会话历史；长期=跨会话可审计记忆 |
| P2 | **鉴权与工程加固** | API Key / 登录、密钥保护、部署与测试 |
| 暂缓 | 多租户 | **明确不做** |

Tool 的 `client` / `remote_http` 分流保持现有方向，但 Phase 内只做「循环与展示所需的最小完备」，SDK / Hub 外置等后置。

---

## 3. 精品标准（每一步都要过）

做任何一个核心能力时，默认要同时满足：

1. **有设计文档**：问题、方案对比、选定方案、数据模型、事件协议、失败语义、非目标  
2. **有明确状态机**：不允许「隐式靠 if 堆出来」；状态可画图  
3. **协议前后端一致**：SSE / WS 事件字段有版本意识（至少文档化）  
4. **可回放**：同一 session 刷新页面后，UI 能还原关键过程信息  
5. **可配置**：轮次、压缩阈值、记忆开关等挂在 Agent / 全局配置，而不是写死常量  
6. **可测**：核心路径有自动化（单测或契约测）；手工验收清单写在设计文档里  
7. **可观测**：关键决策（触发压缩、触达轮次上限、记忆读写）有日志或事件  

达不到以上标准，不算「做完」，只算实验分支。

---

## 4. 现状差距（相对精品目标）

| 能力 | 现在 | 缺口 |
|---|---|---|
| ReAct | `loop.py` 简单 for-loop + tool_calls | 无显式状态；超轮次直接 error；无取消；无收尾总结轮 |
| Session | SQLite messages：role/content/tool_calls | 缺 reasoning、run/turn 结构、消息分页、删除/归档、运行中快照 |
| 对话展示 | SSE 有 tool 事件，助手正文整段蹦出 | 无 token 流；无 thinking 流；工具过程不能「进行中展开 / 完成后收起」 |
| 轮次 | 全局 `max_tool_rounds=8` | 未按 Agent 配置；无策略（硬停 / 总结收尾 / 询问用户） |
| 上下文 | 全量 history 塞进 LLM | 无 token 估算；无预算；无压缩/摘要 |
| 记忆 | 无 | 无跨会话长期记忆 |
| 鉴权 | 可选 API Key | 后置即可 |

---

## 5. 分阶段设计（核心优先）

### Phase A — ReAct 循环引擎（地基）

**目标**：把「能跑」升级成「语义清晰、可配置、可恢复」的 Agent Loop。

**设计要点（开工前写 `docs/design/react-loop.md`）**

- 显式状态机建议：  
  `idle → reasoning → (tool_pending → tool_running → observing)* → answering → done | cancelled | failed`
- **最大轮次**分层：  
  - `max_tool_rounds`：单次 user turn 内 tool 循环上限（Agent 可配，全局有默认）  
  - `max_tokens_per_turn` / 墙钟超时：防止挂死  
- 触达上限的策略（可配，默认「总结收尾」而非生硬报错）：  
  1. 再请求一次 **无 tools** 的收尾回答  
  2. 或返回结构化 `limit_reached`，由 UI 提示用户继续  
- **取消**：客户端 abort → 服务端取消 in-flight LLM/tool，消息落库为 `cancelled`  
- Tool 结果回灌规范：截断策略、错误包装（`ok/error`），避免脏数据污染下一轮  
- DeepSeek 等：`reasoning_content` / `thinking` 在循环内正确回传（否则多轮 tool 会漂）

**完成标准**

- [ ] 状态机与配置项有设计文档  
- [ ] Agent 可配 `max_tool_rounds` 与上限策略  
- [ ] 超限走收尾策略，而不是裸 error  
- [ ] 支持取消  
- [ ] loop 核心单测（无真实 Key：mock LLM）  

**非本阶段**：HITL 确认弹窗可先留扩展点，完整交互可放到展示 Phase。

---

### Phase B — Session 存储与消息模型

**目标**：Session 成为「可回放的真相来源」，而不仅是 chat log 字符串列表。

**设计要点（`docs/design/session-model.md`）**

建议消息模型升级为「用户一轮 = 一个 Turn」，Turn 内含有序 Steps：

```text
Session
  └── Turn (user_message + status + created_at)
        ├── Step: reasoning (thinking 增量/全文)
        ├── Step: tool_call (name, args, call_id)
        ├── Step: tool_result (ok/data/error, latency)
        ├── Step: assistant_text (最终或流式落库)
        └── meta: model, rounds_used, truncated?, cancelled?
```

落库可以是：

- **方案 1**：继续单表 messages，用 `turn_id` / `step_type` / `payload_json` 扩展（改动小）  
- **方案 2**：`turns` + `steps` 两表（更清晰，推荐精品路线）  

需同时定义：

- 列表/详情/删除/重命名  
- 历史分页（管理台与聊天加载）  
- 「进行中的 turn」崩溃恢复策略（进程挂了如何标记 failed）  
- 哪些字段给 LLM，哪些仅给 UI（避免把展示噪音喂回模型）

**完成标准**

- [ ] 设计文档含表结构与迁移计划  
- [ ] 刷新页面可还原：用户话、思考（若有）、工具调用与结果、最终回答  
- [ ] 管理台 Session 可看结构化过程，不只纯文本  
- [ ] 存储与 `messages_for_llm()` 投影分离（UI 完整 ≠ 喂给模型完整）  

---

### Phase C — 对话展示与流式体验（你点名的体验）

**目标**：回复过程中展开思考与工具信息，完成后收起；过程丝滑、可回看。

**设计要点（`docs/design/chat-ux-and-sse.md`）**

**SSE 事件建议（版本化，示例）**

| 事件 | 用途 |
|---|---|
| `turn_start` | 新一轮开始 |
| `reasoning_delta` / `reasoning_done` | 思考流式 |
| `tool_call_start` / `tool_call_args` | 工具调用出现 |
| `tool_result` | 工具结果 |
| `message_delta` / `message_done` | 助手正文流式 |
| `turn_done` / `error` / `cancelled` | 收尾 |

**UI 交互（精品级）**

- 进行中：自动展开「思考中…」「正在调用 list_dir…」时间线  
- 完成后：默认收起为摘要条（如「已完成 2 个工具调用 · 思考 1.2s」），可手动展开  
- 历史回放：与实时同一套组件，避免两套渲染逻辑  
- 停止按钮：对应 Phase A 的取消  
- 无障碍与性能：长 tool 输出虚拟折叠，避免 DOM 爆炸  

**完成标准**

- [ ] 真 token 流式（不再整段 `message`）  
- [ ] 思考 / 工具过程：进行中展开、完成后默认收起  
- [ ] 刷新后展示行为一致  
- [ ] `packages/chat-ui` 成为嵌入侧唯一聊天体验来源  

**推荐实现顺序**：先定 SSE 契约 → 后端 loop 发齐事件 → 再改 `AgentChat.vue`（避免 UI 先猜协议）。

---

### Phase D — 上下文上限与压缩策略

**目标**：长会话稳定、可控、行为可解释。

**设计要点（`docs/design/context-budget.md`）**

- **预算来源**：模型上下文窗口（可配在 model profile）− 预留（回答 / tool 输出）− system / skills / 记忆占用  
- **计量**：至少有一种可用的 token 估算（按模型 tokenizer 或保守字符启发式，需标明误差）  
- **分层策略**（可组合，按优先级丢/压）：  
  1. Tool 结果截断（保留头尾 + hash/摘要）  
  2. 丢弃最旧的「中间 tool 细节」，保留 user + 最终 assistant  
  3. 滑动窗口：保留最近 N turn 全文  
  4. **摘要压缩**：更旧 turn 收成 `summary` 消息（需单独 role 或 system 段，并落库）  
- **可见性**：压缩发生时打事件/日志；可选在 UI 显示「已压缩较早对话」  
- **绝不默默丢当前 turn 的关键 tool 结果**（否则 ReAct 直接坏）

**完成标准**

- [ ] Agent / 模型可配上下文预算与策略  
- [ ] 超预算时行为符合策略，且可测  
- [ ] 压缩后的「喂给模型的 messages」可在调试接口或日志中导出  

---

### Phase E — 记忆

**目标**：记住该记住的，忘掉该忘掉的；与「上下文压缩摘要」边界清晰。

**设计要点（`docs/design/memory.md`）**

建议拆成两层：

| 类型 | 生命周期 | 用途 |
|---|---|---|
| **短期** | 单 session | 对话历史本身 + Phase D 摘要；不另建记忆表 |
| **长期** | 跨 session（`agent_id + user_key`） | 稳定偏好、环境事实；显式工具写入 |

关键设计决策：

- 记忆是 **显式工具**（`memory_write` / `memory_read`）还是 **隐式抽取**？精品路线建议：**先显式工具 + 可选事后抽取**，避免黑盒乱写  
- 作用域键：在无多租户前提下，至少要有 `agent_id + user_key`（会话本身不绑定设备）  
- 冲突、过期、用户「忘记」指令  
- 与 Phase D 摘要的关系：摘要是上下文优化；记忆是可检索的长期状态——**不要混成一个表糊弄**  

**完成标准**

- [ ] 记忆读写 API / tool 可用，管理台可查看与删除  
- [ ] Prompt 组装顺序固定：`system → skills → memory → history(compressed) → current`  
- [ ] 有「不写记忆 / 强制遗忘」的路径  

---

### Phase F — 鉴权与工程加固（后置）

在 A～E 达到精品标准后再做：

- 强制 API Key / 管理台登录  
- 模型 Key 加密存放  
- Compose 部署、基础 CI、结构化日志  
- Tool HITL（高危确认）可与鉴权同期或紧随  

**明确不做**：多租户 / 复杂 RBAC 平台化。

---

## 6. 推荐实施顺序（严格执行）

```text
A ReAct 引擎
  → B Session/消息模型
    → C 流式 + 思考/工具展示（展开收起）
      → D 上下文预算与压缩
        → E 记忆
          → F 鉴权与工程加固
```

原因：

- 没有清晰循环与消息模型，展示层会反复返工  
- 没有稳定 Session 投影，压缩和记忆会对错数据  
- 鉴权不影响「对话是否精品」，故后置  

每个 Phase：**设计文档评审 → 实现 → 验收清单 → 进度打勾**。

---

## 7. 近期第一件事（进行中）

A–E、Skills、压缩验收、Approval HITL、模型 Key 加密已落地；CI 暂缓。下一优先：鉴权收尾或其他产品打磨。

1. ✅ [docs/design/react-loop.md](./design/react-loop.md)  
2. ✅ [docs/design/session-model.md](./design/session-model.md)  
3. ✅ [docs/design/chat-ux-and-sse.md](./design/chat-ux-and-sse.md)  
4. ✅ [docs/design/context-budget.md](./design/context-budget.md)  
5. ✅ [docs/design/memory.md](./design/memory.md)  
6. ✅ [docs/design/hitl.md](./design/hitl.md)（Ask User + Approval）  
7. ✅ [docs/design/skills.md](./design/skills.md)（目录 + 按需加载闭环）  


---

## 8. 非目标（本规划周期内）

- 多租户、复杂组织权限  
- 自研模型训练  
- 工厂进程内做业务 SSH/运维（应在 Bridge / 业务侧）  
- 一上来微服务拆分  
- 为赶进度牺牲协议一致性与可回放性  

---

## 9. 文档索引（随实现补充）

| 文档 | 状态 |
|---|---|
| [docs/roadmap.md](./roadmap.md) | 本文件 |
| [docs/design/react-loop.md](./design/react-loop.md) | 草案 v1 |
| [docs/design/session-model.md](./design/session-model.md) | 草案 v1 |
| [docs/design/chat-ux-and-sse.md](./design/chat-ux-and-sse.md) | 草案 v1 / 已实现首版 |
| [docs/design/context-budget.md](./design/context-budget.md) | 已实现 v1（水位契约测 + 验收打勾） |
| [docs/design/memory.md](./design/memory.md) | 实现中 v1 |
| [docs/design/hitl.md](./design/hitl.md) | 已实现 v1.1（Ask User + Approval） |
| [docs/design/session-queue.md](./design/session-queue.md) | 已实现 v1 |
| [docs/design/skills.md](./design/skills.md) | 已实现 v1（目录 + 客户端按需加载；验收打勾） |
| [docs/design/model-key-encryption.md](./design/model-key-encryption.md) | 已实现 v1 |

---

## 10. 进度

- [x] Phase A：ReAct 循环引擎（首版：Run 状态机、轮次/收尾、取消、Item 事件；真流式待 C）  
- [x] Phase B：Session / 消息模型（`runs`/`items`、timeline、`project_for_llm`、兼容 messages）  
- [x] Phase C：对话流式与展示（token/reasoning 流式、Turn 展开收起、停止、timeline 回放）  
- [x] Phase D：上下文上限与压缩（双水位、L1–L4、摘要水位、debug 投影；契约测验收打勾）  
- [x] Phase E：记忆（三层：profile / playbook / detail；显式工具；Turn 注入目录+画像）  
- [x] HITL Ask User：`ask_user` → `waiting_human`（释放 worker）→ resume；超时；chat-ui 问答卡  
- [x] HITL Approval：`require_approval` 闸门 → `tool_approval_required` → approve/edit/reject；chat-ui 审批卡  
- [x] Session 队列：FIFO 入队、Run 终态自动 dispatch、waiting_human 冻结、重启续跑  
- [x] Skills 闭环：catalog-only、Bridge 路径防护、loop 正例/离线、Composer `/` 注入、验收打勾  
- [x] Phase F（部分）：模型 API Key 落盘加密（`enc:v1:` Fernet；启动迁移明文）  
- [ ] Phase F（剩余）：鉴权收尾；CI 暂缓  


---

## 11. 修订记录

| 日期 | 说明 |
|---|---|
| 2026-11-09 | 初稿：偏平台化 / 多租户 |
| 2026-11-09 | **重写**：以 Agent 核心能力精品化为先；鉴权后置；明确不做多租户 |
| 2026-11-09 | 产出 Phase A/B 设计草案：`react-loop` + `session-model` |
| 2026-11-09 | 落地 A/B 首版：`runs`/`items`、loop 重构、timeline/cancel、兼容 messages |
| 2026-11-09 | Phase C：LLM 真流式、Turn 展开/收起、停止、timeline 回放 |
| 2026-11-10 | Phase D：上下文预算/压缩（投影截断、折叠、窗口、摘要水位、SSE/debug） |
| 2026-11-10 | Phase E：记忆（user_key、仅长期记忆、builtin 工具、Turn 注入、管理台） |
| 2026-11-10 | Phase E：记忆升级为三层 profile/playbook/detail |
| 2026-11-11 | HITL Ask User：waiting_human 释放 worker、resume、超时/抢占、chat-ui 问答卡 |
| 2026-11-11 | Session FIFO 队列：入队/取消/自动续跑，waiting_human 冻结 |
| 2026-11-11 | Skills：工厂仅目录（name+description）；`skill_load`/`skill_read` 客户端按需加载 |
| 2026-11-12 | Skills 闭环验收：Bridge/loop/Composer 单测补齐；`skills.md` §8 打勾 |
| 2026-11-12 | 上下文压缩可信化：soft/L2、hard/L4、当前 Run 不拆、overflow 契约测；`context-budget.md` §9 打勾 |
| 2026-11-12 | Approval HITL：`require_approval`、SSE 审批、approve/edit/reject resume、chat-ui 审批卡 |
| 2026-11-12 | 模型 API Key 落盘加密：`MODEL_SECRETS_KEY` + Fernet；启动 reseal 明文；CI 暂缓 |
