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
| 管理**本** Agent 的知识库文档（上传/列表/改/删/目录） | 同上 AK/SK（见 §4） |
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

## 4. 文档（知识库）：Agent 自己维护自己的文档

每个 Agent 有自己的一批文档，**不存在平台级 / 全局知识库**；文档也不会跨 Agent 共享。Agent 内分两类：

| 类别 | `scope` | 额外参数 | 谁能看到 |
|---|---|---|---|
| Agent 共享文档 | `agent` | 无（Agent 在 URL 里） | 本 Agent 的所有会话 |
| Agent 个人文档 | `user` | `user_key` | 本 Agent 内该 `user_key` 的会话 |

同一个 `user_key` 在两个 Agent 下的个人文档互不可见。

**鉴权**：本 Agent AK/SK 即可读写自己的文档，不需要管理台、不需要平台 Key；平台 Key 也能操作全部 Agent。

**目录不入库**：由 `path`（如 `售后/退货/退货政策.md`）自动派生，上传即建层，改路径即移动文件。没有 folder 表，也没有单独的建目录接口。

### 4.1 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agents/{agent_id}/knowledge/documents` | 分页列表 |
| GET | `/api/agents/{agent_id}/knowledge/documents/{doc_id}` | 单篇元信息 |
| GET | `/api/agents/{agent_id}/knowledge/documents/{doc_id}/content` | 正文预览（默认前 8000 字） |
| POST | `/api/agents/{agent_id}/knowledge/documents` | 上传 / 覆盖（multipart） |
| PATCH | `/api/agents/{agent_id}/knowledge/documents/{doc_id}` | 改标题 / 摘要 / 路径 / 启停 |
| DELETE | `/api/agents/{agent_id}/knowledge/documents/{doc_id}` | 删除单篇 |
| POST | `/api/agents/{agent_id}/knowledge/folders/move` | 目录重命名 / 移动（前缀批量改写） |
| POST | `/api/agents/{agent_id}/knowledge/folders/delete` | 删除目录及其中全部文档 |

列表查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `scope` | `agent` | `agent` / `user` |
| `user_key` | — | `scope=user` 时筛选单个终端用户；**省略 = 该 Agent 的全部用户** |
| `keyword` | — | 同时匹配 `path` / `title` / `summary` |
| `path_prefix` | — | 只看某个目录子树（按 `前缀/` 匹配） |
| `page` / `page_size` | 1 / 20 | `page_size` 上限 200 |
| `include_disabled` | `false` | 是否带上 `status=disabled` 的文档 |

返回：

```json
{
  "items": [ /* KbDocumentOut */ ],
  "total": 23,
  "page": 1,
  "page_size": 20,
  "user_keys": ["customer-1001", "customer-1002"]
}
```

`user_keys`（仅 `scope=user`）是该 Agent 出现过的全部终端用户，做筛选下拉用。**正文不进列表**，避免大文档拖慢表格 / 同步脚本。

**KbDocumentOut**

| 字段 | 说明 |
|---|---|
| `id` | 文档 id；同路径覆盖时保持不变，引用稳定 |
| `agent_id` | 归属 Agent |
| `scope` | `agent` / `user` |
| `user_key` | 仅 `scope=user` 有值 |
| `path` | 相对路径；目录由它派生 |
| `title` | 展示标题，默认取文件名 |
| `summary` | 一句话摘要（唯一进 prompt 的内容） |
| `summary_source` | `llm`（模型生成）/ `auto`（规则抽取）/ `manual`（人工填写） |
| `size_bytes` / `chars` | 字节数 / 正文字数 |
| `sha256` | 去重与「要不要重传」的判断依据 |
| `status` | `active` / `disabled`（`disabled` 不进运行时） |
| `created_at` / `updated_at` | ISO 时间 |

### 4.2 上传 / 覆盖

```bash
curl -s -X POST "$FACTORY_API_BASE/api/agents/$AGENT_ID/knowledge/documents" \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" \
  -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  -F "file=@./docs/退货政策.md" \
  -F "scope=agent" \
  -F "dir=售后/退货"
```

| multipart 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | `.md` `.markdown` `.txt` `.rst` `.json` `.yaml` `.yml` `.csv`；≤ 2MB；UTF-8 |
| `scope` | 否 | `agent`（默认）/ `user` |
| `user_key` | `scope=user` 必填 | 终端用户稳定 ID（与 Session 的 `user_key` 对齐） |
| `dir` | 否 | 目标目录；**留空 = 根目录**；不存在的目录自动出现 |
| `title` | 否 | 默认取文件名 |
| `summary` | 否 | 填了即标记 `manual`，此后同路径重传不会覆盖 |
| `generate` | 否 | 默认 `true`：未给摘要时由工厂生成（模型优先，失败回落规则抽取） |

- 同 `(agent_id, scope, user_key, path)` 再传 = **覆盖正文**，`id` 不变，`created=false`。
- 摘要失败**不会**让上传失败，只在 `warnings` 里提示（如「未配置可用模型，摘要由本地规则抽取」）。
- 返回 `{ document, created, summary_source, warnings }`。

### 4.3 改元信息 / 下架

```http
PATCH /api/agents/{agent_id}/knowledge/documents/{doc_id}
Content-Type: application/json
X-Access-Key: ...
X-Secret-Key: ...

{ "title": "退货政策", "path": "售后/退货/退货政策.md", "summary": "…", "status": "active" }
```

- 改 `path` 即移动文件（含换目录）；与已有文档冲突 → 400
- 传 `summary` 即标记 `manual`，之后重传不再覆盖
- `status=disabled`：文档仍存在、接口可见，但**不进 prompt，`kb_list` 也看不到**；临时下架优先用它，不要删
- **正文不能用 PATCH 改**：更新正文 = 用同一个 `path` 重新上传（新的 `file`）

### 4.4 目录操作

```http
POST /api/agents/{agent_id}/knowledge/folders/move
{ "scope": "agent", "path": "售后", "new_path": "客户服务" }      # → { "affected": 12 }

POST /api/agents/{agent_id}/knowledge/folders/delete
{ "scope": "agent", "path": "过期制度" }                           # → { "affected": 3 }
```

`scope=user` 时 body 再加 `user_key`。移动按前缀批量改写；目标已存在同路径会被整批拒绝（不会半途改一半）。

> 单篇改名也可以直接 `PATCH { "path": ... }`，不必走 folders API。

### 4.5 典型用法

**把仓库里的文档目录同步上去（幂等）**

```bash
#!/usr/bin/env bash
# 本地 ./knowledge/**.md → 工厂 Agent 共享文档；重复跑只覆盖有变化的文件
set -euo pipefail
cd "${KNOWLEDGE_DIR:-./knowledge}"
find . -type f \( -name '*.md' -o -name '*.txt' \) -print0 |
while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  dir="$(dirname "$rel")"; [ "$dir" = "." ] && dir=""
  curl -sSf -X POST "$FACTORY_API_BASE/api/agents/$AGENT_ID/knowledge/documents" \
    -H "X-Access-Key: $AGENT_ACCESS_KEY" -H "X-Secret-Key: $AGENT_SECRET_KEY" \
    -F "file=@$rel" -F "scope=agent" -F "dir=$dir" > /dev/null
  echo "uploaded $rel"
done
```

**列某目录 / 拿 doc_id**

```bash
curl -s -G "$FACTORY_API_BASE/api/agents/$AGENT_ID/knowledge/documents" \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  --data-urlencode "scope=agent" \
  --data-urlencode "path_prefix=售后/退货" \
  --data-urlencode "page_size=200" |
  jq -r '.items[] | "\(.id)\t\(.path)\t\(.chars)字"'
```

**按 sha256 跳过没变的文件**（省掉摘要生成的模型调用）

```bash
local_sha=$(shasum -a 256 退货政策.md | cut -d' ' -f1)
remote_sha=$(curl -s -G "$FACTORY_API_BASE/api/agents/$AGENT_ID/knowledge/documents" \
  -H "X-Access-Key: $AGENT_ACCESS_KEY" -H "X-Secret-Key: $AGENT_SECRET_KEY" \
  --data-urlencode "scope=agent" --data-urlencode "page_size=200" \
  | jq -r --arg p "售后/退货/退货政策.md" '.items[] | select(.path==$p) | .sha256')

[ "$remote_sha" = "$local_sha" ] && echo "unchanged, skip upload"
```

**给某个终端用户挂专属文档**：加 `-F "scope=user" -F "user_key=customer-1001"`。这类文档只有该 `user_key` 的会话能看到。

**看正文**：`GET .../documents/{doc_id}/content?max_chars=8000` → `{ id, path, content, total_chars, truncated }`。

### 4.6 运行时怎么用这些文档

- prompt 里只注入目录：`path — summary`；正文由 builtin `kb_list` / `kb_read` 按需读取（**Bridge 不必实现**）
- 生效前提：`runtime.knowledge.enabled=true` **且当前确有可见文档**；空知识库不注入、不注册工具
- 预算：`max_catalog_chars`（默认 3000）超限按「去摘要 → 折叠成目录 → 截断」降级；`kb_list` 单次条数受 `max_list_items`
- 个人文档要进上下文：`include_user_docs=true`（默认）且会话有 `user_key`

```json
{ "runtime": { "knowledge": { "enabled": true, "max_catalog_chars": 3000, "include_user_docs": true } } }
```

### 4.7 常见错误

| 状态码 | 原因 |
|---|---|
| 400 | 扩展名不在白名单 / 非 UTF-8 / 空文件 / >2MB / 路径含 `..`、`:` 或层级 >6 / `scope=user` 缺 `user_key` / 路径冲突 |
| 403 | AK/SK 绑定的不是 URL 里的 `agent_id` |
| 404 | Agent 不存在，或 `doc_id` 属于别的 Agent |

---

## 5. Session：创建与上下文注入

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

## 6. 嵌入：token + 页面地址

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

## 7. 推荐工作流（对方仓库的编程 Agent）

```text
1. 向人类确认 FACTORY_API_BASE / CHAT_WEB_ORIGIN / AGENT_ID / AK/SK（不要平台 Key）
2. 复制 docs/integration/agent-factory-integrate/SKILL.md 到对方 Skills 目录
3. 若尚无 Agent：人类在管理台创建并轮换凭证，把 AK/SK 交给接入方
4. 用 AK/SK：GET → 合并 → PATCH tools / skills / prompt
5. 需要业务文档进知识库时，用 AK/SK 上传到本 Agent（§4；目录靠 `dir` / `path`，不用建目录）
6. Bridge 或 remote_http 实现工具；本地写 SKILL.md 正文
7. 用 AK/SK：POST session（带 prompt_vars）或换 embed_token
8. 自检：配置 GET 回来正确、对话能调工具、`kb_list` 能看到上传的文档
```

**原则**：tools / skills / 业务 prompt 只有接入方清楚 → **用本 Agent AK/SK 自助维护**；工厂不发平台 Key 给接入方。

---

## 8. 与管理台的关系

| 事 | 推荐 |
|---|---|
| 试点改 tools/skills/prompt | API / 本 Skill |
| 上传 / 同步 / 改 / 删知识库文档 | **API（§4）**，适合脚本与 CI；管理台「编辑智能体 → 知识库」只做人工抽查 |
| 看用量、记忆、模型探测 | 管理台仍方便 |
| 生产改配置 | API + 密钥管控；管理台可选 |
