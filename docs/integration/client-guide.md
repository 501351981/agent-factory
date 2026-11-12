# Agent 客户端接入说明

> 面向：要把旧业务 Agent 换成 **agent-factory** 的研发同学  
> 目标：搞清楚**业务侧要做什么**、**不要做什么**，以及最小可跑通路径

配套示例：`apps/demo-web`（iframe 宿主）、`apps/demo-bridge`（本地 Tool 客户端）。

**给对方仓库编程 Agent（Cursor 等）用的契约与 Skill**（推荐优先看）：

- [api-for-coding-agents.md](./api-for-coding-agents.md) — 地址清单、鉴权、PATCH Agent / Session / embed  
- [agent-factory-integrate/SKILL.md](./agent-factory-integrate/SKILL.md) — 可复制到对方项目的接入 Skill  

配置 Agent 的 tools / skills / prompt **不必登录管理台**，用**本 Agent 的 AK/SK** 调 `PATCH /api/agents/{id}` 即可（不要发平台 `FACTORY_API_KEY` 给接入方）。只有接入方知道真实工具面时，应由对方编程 Agent 维护配置。

---

## 1. 先判断：现在能不能换

**可以开始用真实项目试接**，建议先选一个**低风险、工具面清晰**的旧 Agent 做试点，而不是一次替换全部。

当前底座已具备：

| 能力 | 状态 |
|---|---|
| 对话 / ReAct / 流式 / 取消 | ✅ |
| 会话回放、队列、Ask User / 工具审批 | ✅ |
| 上下文压缩、长期记忆 | ✅ |
| Skills 按需加载（客户端正文） | ✅ |
| iframe 嵌入 + AK/SK embed token | ✅ |
| client Bridge / remote_http 工具 | ✅ |
| 模型 Key 落盘加密 | ✅ |
| CI / 多租户 / 复杂 RBAC | ❌ 不做或不优先 |

**不适合一上来就全量替换的情况**：强依赖旧框架专有中间件、需要多租户隔离、或工具协议与现有 Bridge/HTTP 差太远——先试点再扩。

---

## 2. 职责划分（最重要）

```text
┌──────────────────┐     iframe / API      ┌─────────────────────┐
│  业务系统（宿主）  │ ◄──────────────────► │  Agent 工厂（中心）   │
│  - 登录态/用户 ID  │   embed_token        │  - LLM 循环 / Session │
│  - 嵌入聊天页     │   postMessage        │  - 记忆 / 压缩 / HITL │
│  - 业务后端换 token│                      │  - 管理台配置 Agent   │
└──────────────────┘                      └──────────┬──────────┘
                                                     │
                          client tools / skills      │ WebSocket
                                                     ▼
                                          ┌─────────────────────┐
                                          │  Agent 客户端 Bridge │
                                          │  - 执行本机/内网工具  │
                                          │  - 存放 SKILL.md     │
                                          └─────────────────────┘
```

| 角色 | 做什么 | 不要做 |
|---|---|---|
| **工厂 server** | 推理、会话、记忆、调度工具 | 不跑业务 SSH/私有运维脚本 |
| **chat-web `/embed`** | 官方聊天 UI | 不存 Secret Key |
| **业务宿主** | 鉴权、选用户、嵌 iframe、可选管 session | 浏览器里不要放 SK |
| **Bridge** | 执行 `client` 工具 + 读本地 skills | 不替代工厂做 LLM |

---

## 3. 改造清单（旧项目 → 工厂）

按顺序做；多数项目 **1～4 就能先聊起来**。

### Step 1 — 工厂侧准备（管理台）

1. 部署/启动工厂（server + admin + chat-web）
2. 配置**模型**（DeepSeek / OpenAI 兼容等），填 API Key  
3. 创建 **Agent**：系统提示词、tools、skills 目录项、记忆开关  
4. **生成 AK/SK**（管理台「凭证」），SK 只展示一次，交给**业务后端**保管  
5. 记下 `agentId`

提示词可用变量，例如 `{{userId}}`；embed 的 `userId` 会映射进 `prompt_vars`。

### Step 2 — 业务后端换 embed token（必做，生产）

**Secret Key 只能留在业务后端**，浏览器只拿短时 `embed_token`。

```http
POST /api/auth/embed-token
Headers:
  X-Access-Key: ak_xxx
  X-Secret-Key: sk_xxx
Content-Type: application/json

{
  "user_key": "业务侧稳定用户ID",
  "ttl_seconds": 3600
}
```

返回：`embed_token`、`expires_at`、`agent_id`、`user_key`。

> 开发环境若未设 `FACTORY_API_KEY` / 未强制凭证，可用 demo-web 摸流程；**接真业务请走 AK/SK**。

### Step 3 — 宿主页 iframe 嵌入

```html
<iframe
  id="agent-frame"
  src="https://<CHAT_WEB>/embed?agentId=<AGENT_ID>&token=<EMBED_TOKEN>&locale=zh&userKey=<USER_KEY>&userId=<USER_ID>&deviceId=<DEVICE_ID>"
  style="width:100%;height:640px;border:0;border-radius:8px"
  allow="clipboard-write"
></iframe>
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `agentId` | ✅ | 管理台 Agent ID |
| `token` | 生产 ✅ | 上一步的 `embed_token` |
| `userKey` | 新建会话 ✅ | 长期记忆作用域；建议等于业务用户稳定 ID |
| `userId` | 建议 | 进 system prompt 模板 `{{userId}}` |
| `deviceId` | 有本地工具时 ✅ | 与 Bridge `--device-id` 一致 |
| `sessionId` | 可选 | 续聊；没有则新建 |
| `locale` | 可选 | `zh` / `en` / `ja` |

参考实现：`apps/demo-web`；管理台「复制嵌入代码」也有片段。

### Step 4 —（可选）宿主接管 Session

默认 embed 可自己建会话。若业务要**自建/列表/切换会话**，用 `postMessage`：

**工厂 → 宿主**（`source: 'agent-factory'`）

| type | 含义 |
|---|---|
| `session` | 当前 `sessionId`（新建或切换后） |
| `session_create_request` | 请求宿主创建会话（`sessionCreateMode=host` 时） |

**宿主 → 工厂**（`source: 'agent-factory-host'`）

| type | 含义 |
|---|---|
| `set_session` | `{ sessionId }` 注入/切换会话 |
| `clear_session` | 清空，回到新对话 |
| `session_create_failed` | `{ error }` 创建失败 |

宿主需自行调用工厂 Session API（带平台 Key 或 Agent 凭证，按你的鉴权部署）。最小试点可先不用 host 模式。

### Step 5 — 工具接到哪里

管理台 Agent.tools 里声明工具，`execution` 二选一（或混用）：

#### A. `client`（本机 / 内网，经 Bridge）

1. 参考 `apps/demo-bridge` 起一个 WebSocket 客户端  
2. 连接：`ws(s)://<FACTORY>/api/client/connect?device_id=<DEVICE_ID>&token=<可选>`  
3. 收到 `{ type: "tool_call", call_id, name, arguments }` → 执行 → 回 `{ type: "tool_result", call_id, ok, data?, error? }`  
4. iframe 带同一 `deviceId`；Session 会粘性绑定该设备  
5. 高危工具可在 ToolDef 加 `"require_approval": true`（聊天里先审批再执行）

内置 demo 工具名：`echo` / `get_cwd` / `list_dir`；可按业务改名并实现。

#### B. `remote_http`（业务 HTTP）

工厂 POST 到你的 `endpoint`：

```json
{ "name": "tool_name", "arguments": { }, "session_id": "..." }
```

期望返回：`{ "ok": true, "data": ... }` 或 `{ "ok": false, "error": "..." }`（或兼容直接 JSON 数据）。

### Step 6 — Skills（可选）

- 要开 Skill 能力时：Agent 配 `skills[]` 目录；Bridge 实现 `skill_load` / `skill_read`  
- **工厂**：`{ "name", "description" }`；**客户端**：`skills/<name>/SKILL.md`  
- 聊天输入 `/` 可选技能  

约定见 [skills.md](../design/skills.md)；参考 `apps/demo-bridge`。

### Step 7 — 记忆与用户标识

- `userKey` 必须稳定（同一人跨会话一致）  
- 记忆按 `agent_id + user_key` 隔离  
- 管理台「记忆」页可查看/删除  

---

## 4. 推荐试点路径

```text
第 1 天：iframe 嵌入 + 纯对话（可先不接工具）
第 2 天：接 1～2 个 remote_http 或 client 工具
第 3 天：迁一个 Skill；打开 require_approval 试高危工具
之后：  对照旧 Agent 验收清单逐项打勾，再扩量
```

验收建议：

- [ ] 嵌入页能发消息、流式回复  
- [ ] 刷新后同一 `sessionId` 能回放  
- [ ] 工具调用过程可见；审批/Ask User 能走完  
- [ ] Bridge 离线时错误明确，不静默打到别的机器  
- [ ] 记忆只作用于同一 `userKey`  

---

## 5. 安全红线

1. **SK / 模型供应商 Key 永不进前端打包与 URL**  
2. 浏览器只持有短时 `embed_token`  
3. 生产设置 `FACTORY_API_KEY`、`MODEL_SECRETS_KEY`、`EMBED_TOKEN_SECRET`  
4. 高危 client 工具开 `require_approval`  
5. `deviceId` 与真实设备对应，避免多人共用同一 demo 设备 ID  

---

## 6. 常见问题

**Q: 旧项目是自研 chat UI，能否不 iframe？**  
可以，但要自己对接 Chat SSE / Session / resume 等 API（见 OpenAPI `/docs`）。**试点强烈建议先 iframe**，少踩协议坑。

**Q: 必须上 Bridge 吗？**  
只有需要 `execution: client` 或本地 Skills 时才需要。纯云端 HTTP 工具用 `remote_http` 即可。

**Q: 和 Cursor / 其它 Agent 框架什么关系？**  
工厂是你们的**可嵌入业务 Runtime**；对方 Cursor 等**编程 Agent** 用 [api-for-coding-agents.md](./api-for-coding-agents.md) + [接入 Skill](./agent-factory-integrate/SKILL.md) 调 API 改配置、建 Session，而不是靠登录管理台。

**Q: 换环境 URL？**  
改业务侧的 `CHAT_WEB_ORIGIN` 与 `FACTORY_API_BASE`；Agent 配置用 API 在目标环境重建或迁移 DB。

**Q: 缺工厂地址时编程 Agent 怎么办？**  
向人类询问 `FACTORY_API_BASE`、`CHAT_WEB_ORIGIN`、`AGENT_ID` 与 AK/SK，不要猜生产域名，**不要索要平台 `FACTORY_API_KEY`**。

---

## 7. 文档与示例索引

| 资源 | 用途 |
|---|---|
| 本文件 | 业务改造总览（人读） |
| [api-for-coding-agents.md](./api-for-coding-agents.md) | 编程 Agent 用的 API / 地址契约 |
| [agent-factory-integrate/SKILL.md](./agent-factory-integrate/SKILL.md) | 复制到对方项目的接入 Skill |
| [README.md](../../README.md) | 本地启动 |
| `apps/demo-web` | 宿主嵌入完整示例 |
| `apps/demo-bridge` | Bridge 最小实现 |
| [skills.md](../design/skills.md) | Skills 约定 |
| [hitl.md](../design/hitl.md) | Ask User / Approval |
| http://127.0.0.1:8000/docs | OpenAPI |

---

## 8. 给改造同学的一句话

> **业务负责：人是谁、页嵌哪里、工具在哪跑；工厂负责：怎么想、怎么记、怎么把工具过程聊清楚。**  
> 先 iframe 跑通对话，再迁工具与 Skill，不要一上来重写聊天 UI。  
> tools / skills / prompt 交给对方编程 Agent 用 API 维护。
