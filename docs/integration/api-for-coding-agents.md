# 给编程 Agent 的 API 契约

> 读者：Cursor / Claude Code / 其他仓库里的编程 Agent  
> 目标：不登录管理台，用 HTTP 完成 Agent 配置、Session、嵌入准备  
> 配套 Skill（可复制到对方项目 `.cursor/skills` 或 Skills 目录）：  
> [`agent-factory-integrate/SKILL.md`](./agent-factory-integrate/SKILL.md)

人类向导：[client-guide.md](./client-guide.md) · 交互 OpenAPI：`{FACTORY_API_BASE}/docs`

---

## 1. 需要几个地址 / 密钥

| 名称 | 谁用 | 说明 |
|---|---|---|
| **工厂 API** `FACTORY_API_BASE` | 业务后端 + 编程 Agent | 如 `http://127.0.0.1:8000` |
| **聊天页** `CHAT_WEB_ORIGIN` | iframe `src` | 如 `http://localhost:8801`（`apps/chat-web`） |
| **Agent AK/SK** | 接入方日常操作 | 改**本** Agent 配置、轮换凭证、建 Session、换 embed；`X-Access-Key` / `X-Secret-Key`；SK 不进前端 |
| **平台 Key** `FACTORY_API_KEY` | **仅工厂运维** | 创建/删除 Agent、列全部 Agent、管模型；**不要发给接入方编程 Agent** |
| **WS** | Bridge 客户端 | 通常同主机 `ws(s)://…`，路径 `/api/client/connect` |

**缺地址时**：编程 Agent 必须向人类询问工厂 API / 聊天页 / 本 Agent 的 ID+AK/SK，不要猜测生产域名，**不要索要平台 Key**。

健康检查：`GET {FACTORY_API_BASE}/api/health`

---

## 2. 鉴权怎么选

| 场景 | 鉴权 |
|---|---|
| 读/改**本** Agent（prompt/tools/skills）、轮换/吊销本 Agent 凭证 | Agent `X-Access-Key` + `X-Secret-Key`（仅能操作绑定的 `agent_id`） |
| 建 Session、换 `embed_token` | 同上 AK/SK |
| 创建 / 删除 Agent、`GET /api/agents` 列表、管 ModelProfile | `X-API-Key`（平台，运维） |
| 浏览器聊天 | `Authorization: Bearer <embed_token>`（**不能**改 Agent 配置） |
| 开发全开 | 未配置 `FACTORY_API_KEY` 时平台接口可无 Key（仅本地） |

---

## 3. Agent：改 prompt / tools / skills（核心）

```http
GET    /api/agents                      # 平台 Key
POST   /api/agents                      # 平台 Key（首次建号）
GET    /api/agents/{agent_id}           # 平台 或 本 Agent AK/SK
PATCH  /api/agents/{agent_id}           # 平台 或 本 Agent AK/SK（推荐接入方用 AK/SK）
DELETE /api/agents/{agent_id}           # 平台 Key
POST   /api/agents/{agent_id}/credentials/rotate   # 平台 或 本 Agent AK/SK
POST   /api/agents/{agent_id}/credentials/revoke   # 平台 或 本 Agent AK/SK
```

用 AK/SK 时，服务端会校验凭证绑定的 `agent_id`；改别人的 Agent → 403。`embed_token` 不能 PATCH。

### PATCH 示例：只更新工具与 Skills 目录

```bash
# 1) 先 GET，避免整表替换时丢掉未改字段
curl -s \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" \
  -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  "$FACTORY_API_BASE/api/agents/$AGENT_ID" > /tmp/agent.json

# 2) 合并后 PATCH（tools / skills 为整表替换）
curl -s -X PATCH \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" \
  -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "system_prompt": "你是运维助手。用户 {{userId}}，工单 {{orderId}}。",
    "tools": [
      {
        "name": "list_dir",
        "description": "列出目录",
        "execution": "client",
        "parameters": {
          "type": "object",
          "properties": { "path": { "type": "string" } }
        },
        "require_approval": false
      }
    ],
    "skills": [
      {
        "name": "workspace-scout",
        "description": "本地工作区侦察；问目录时先 skill_load"
      }
    ]
  }' \
  "$FACTORY_API_BASE/api/agents/$AGENT_ID"
```

### 字段契约

**ToolDef**

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 工具名 |
| `description` | string | 给模型看 |
| `execution` | `client` \| `remote_http` \| `builtin` | 业务只配前两种；`ask_user` 等 builtin 由工厂注入 |
| `endpoint` | string? | `remote_http` 必填 |
| `parameters` | JSON Schema object | 默认 `{type:object,properties:{}}` |
| `require_approval` | bool | 审批总开关；true 时再评估 `approval_policy` |
| `approval_policy` | object\|null | `default`=`require`\|`skip`\|`ask_client` + `allow[]` / `always_require[]`（正则或问 Bridge，见 hitl.md） |

**SkillDef（仅目录）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 与客户端 `skills/<name>/SKILL.md` 对应 |
| `description` | string | 触发说明；**不要**把正文放进工厂 |

**Prompt**

- `system_prompt`：默认/兼容字段；支持 `{{var}}`
- `system_prompts`: `{ "zh"|"en"|"ja": "..." }` 多语言模板
- 会话创建时用 `prompt_vars` 渲染，结果写入该 Session 快照

**ui_theme（嵌入页主题）**

- 可选 dict：`preset`（`light`/`dark`）+ 颜色/圆角 token（`bg`、`accent`、`userBubble`、`radiusLg`…）
- 映射 chat-ui 的 `--af-*`；PATCH 整表替换；详见 [ui-theme.md](../design/ui-theme.md)

---

## 4. Session：创建与上下文注入

```http
POST /api/sessions
Content-Type: application/json

{
  "agent_id": "...",
  "user_key": "business-user-stable-id",
  "title": "可选",
  "locale": "zh",
  "client_device_id": "bridge-device-id",
  "prompt_vars": { "userId": "u_1", "orderId": "SO-1" }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `agent_id` | 是 | |
| `user_key` | 是 | 记忆作用域；embed 绑定用户 |
| `prompt_vars` | 否 | 仅创建时渲染；之后改 Agent 模板不影响已有 Session |
| `client_device_id` | 否 | 绑定 Bridge 设备，跑 client 工具 |

会话维护（AK/SK 或 embed；须在 Agent 作用域内）：

| 方法 | 说明 |
|---|---|
| `GET /api/sessions` | 列表（不含 `archived`） |
| `GET /api/sessions/{id}` | 详情（已归档对客户端 404） |
| `PATCH /api/sessions/{id}` | 改 `title` / `status`（`active`\|`archived`）/ `client_device_id` |
| `POST /api/sessions/{id}/clear-history` | **软清除**：水位隐藏旧消息；不进 LLM 上下文 |
| `DELETE /api/sessions/{id}` | **软删除** → `archived` |
| `DELETE /api/sessions/{id}?hard=true` | **硬删除**（仅平台 Key） |

消息与 timeline：`GET .../messages`、`GET .../timeline`（均尊重清除水位）。发消息走 `/api/chat`（SSE）或 embed UI。

---

## 5. 嵌入：token + 页面地址

```http
POST /api/auth/embed-token
X-Access-Key: ak_...
X-Secret-Key: sk_...

{ "user_key": "business-user-stable-id", "ttl_seconds": 3600 }
```

页面：

```text
{CHAT_WEB_ORIGIN}/embed?agentId=...&token=...&userKey=...&userId=...&deviceId=...&sessionId=...&locale=zh
```

宿主 ↔ iframe：`agent-factory:v1` postMessage（`ready` / `set_session` / `focus_composer` 等）见 client-guide。

---

## 6. 推荐工作流（对方仓库的编程 Agent）

```text
1. 向人类确认 FACTORY_API_BASE / CHAT_WEB_ORIGIN / AGENT_ID / AK/SK（不要平台 Key）
2. 复制 docs/integration/agent-factory-integrate/SKILL.md 到对方 Skills 目录
3. 若尚无 Agent：人类在管理台创建并轮换凭证，把 AK/SK 交给接入方
4. 用 AK/SK：GET → 合并 → PATCH tools / skills / prompt
5. Bridge 或 remote_http 实现工具；本地写 SKILL.md 正文
6. 用 AK/SK：POST session（带 prompt_vars）或换 embed_token
7. 自检：配置 GET 回来正确、对话能调工具
```

**原则**：tools / skills / 业务 prompt 只有接入方清楚 → **用本 Agent AK/SK 自助维护**；工厂不发平台 Key 给接入方。

---

## 7. 与管理台的关系

| 事 | 推荐 |
|---|---|
| 试点改 tools/skills/prompt | API / 本 Skill |
| 看用量、记忆、模型探测 | 管理台仍方便 |
| 生产改配置 | API + 密钥管控；管理台可选 |
