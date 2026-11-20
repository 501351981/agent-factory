# 设计：知识库（轻量、无向量模型）

> 状态：已实现 v1.1（文档严格归属 Agent）  
> 关联：[skills.md](./skills.md)、[memory.md](./memory.md)、[context-budget.md](./context-budget.md)  
> **接口用法 / 上传脚本 / 错误码见集成文档**：[api-for-coding-agents.md §4](../integration/api-for-coding-agents.md)、[agent-factory-integrate/SKILL.md](../integration/agent-factory-integrate/SKILL.md)。
> 本文只写设计取舍与运行时行为。

目标：**不引入 embedding / 向量库**，用「目录 + 摘要进上下文，正文按需读取」解决中小规模文档（几十到几百篇）的检索。适合客服话术、公司制度、产品手册、客户合同等场景。

---

## 1. 五个产品约束

1. **树形管理**：文档按 `path`（如 `售后/退货/退货政策.md`）组织；目录由路径自动派生，不单独建目录表。
2. **每篇文档都必须归属于某个 Agent**：不存在平台级/全局知识库。知识库是 Agent 的一部分，谁能用这个 Agent 谁才可能看到这些文档。
3. **Agent 内再分两种文档**：`scope=agent` 为该 Agent 的共享文档（所有会话可见）；`scope=user` 为该 Agent 下某个终端用户的文档（只有 `user_key` 相同的会话可见）。同一个 `user_key` 在别的 Agent 下看不到本篇。
4. **摘要由工厂生成**：上传时服务端立即生成摘要（优先模型，失败回落规则抽取）；客户端只传正文。
5. **总量可控**：只把「路径 + 摘要」注入 system prompt，`max_catalog_chars` 每 Agent 可配（默认 3000 字）；超预算按固定阶梯降级。

因此管理台不提供独立的「知识库」菜单：入口只有一个——`编辑智能体 → 知识库`。

---

## 2. 数据模型

表 `kb_documents`（`apps/server/src/agent_factory_server/db/models.py`）：

| 字段 | 说明 |
|---|---|
| `id` | 文档 id；同路径覆盖时保持不变（便于引用） |
| `agent_id` | 归属的 Agent，**必填**（外键指向 `agents.id`） |
| `scope` | `agent`（Agent 共享）/ `user`（Agent 下的个人） |
| `user_key` | `scope=user` 必填；`scope=agent` 为空串 |
| `path` | 相对路径，唯一键 `(agent_id, scope, user_key, path)` |
| `title` | 展示标题，默认取文件名 |
| `summary` | 一句话摘要（进上下文） |
| `summary_source` | `llm` / `auto` / `manual` |
| `text_content` | 正文（不进 prompt，`kb_read` 分页读取） |
| `sha256` / `size_bytes` | 上传去重与展示 |
| `status` | `active` / `disabled` |

没有 `folders` 表：目录是路径的投影。

- 上传 `售后/退货/` 下的文件会自动出现这两级目录。
- 目录重命名 = 批量改写前缀（`move_kb_folder`）。
- 目录删除 = 删除前缀下全部文档（`delete_kb_folder`）。

---

## 3. 上传与摘要

上传走 `POST /api/agents/{agent_id}/knowledge/documents`（multipart，字段表见集成文档 §4.2）。Agent 由 URL 决定，请求体不传 `agent_id`——结构上不可能往别的 Agent 里写。

摘要由**工厂**在服务端生成（`runtime/knowledge_summary.py`），客户端只负责传正文：

1. 取默认模型配置 → 一次低温度调用，限 120 字；
2. 无模型 / 超时 / 报错 → 规则抽取（去 frontmatter、代码块、HTML、Markdown 标记，取前 1–2 句）；
3. 正文为空 → 用标题兜底。

两条产品约定：

- **上传不会因摘要失败而失败**：失败只降级 `summary_source` 并回 `warnings`。
- **人工摘要优先**：`summary_source=manual` 后，同路径重传不复写摘要；要改只能显式再传 `summary`。

---

## 4. Prompt 注入与预算降级

每轮 run 开始时只加载**本 Agent 的共享文档 + 当前会话 `user_key` 在本 Agent 下的个人文档**，渲染成目录块追加到 system prompt：

```text
# 知识库（仅目录）
(正文不在提示中。先 kb_list 浏览目录，再 kb_read 读正文；没写的不要编造。)
- `售后/退货政策.md` — 七天无理由退货条件与运费承担
- `售后/发票/电子发票.md` — 电子发票申请路径与时效
```

`runtime/knowledge.max_catalog_chars`（默认 3000）是**硬上限**，按阶梯降级，绝不截断到半行：

| 阶梯 | 内容 | 何时使用 |
|---|---|---|
| L0 | `path — summary` | 预算充足（默认路径） |
| L1 | 仅 `path` | 摘要放不下 |
| L2 | 按一级目录折叠：`- 售后/ (12 篇)` + 提示「用 kb_list 展开」 | 路径也放不下 |
| L3 | 只保留能放下的目录行，末尾截断 | 极端情况 |

技能目录（skills catalog）、记忆块与知识库块互不挤占：各自独立计算后拼进 system prompt。

---

## 5. 运行时工具

`enabled=true` 且**当前确有可见文档**时才注册（空知识库不占工具位）：

| 工具 | 参数 | 行为 |
|---|---|---|
| `kb_list` | `path?` | 列出目录直接子目录（含文档数）与文档（title / summary / chars / doc_id），按 `max_list_items` 截断 |
| `kb_read` | `doc_id`, `offset?` | 按 8000 字分页返回正文；带 `total_chars` / `next_offset`，直到 `next_offset=null` |

可见性在工具执行时再次校验：先要求 `doc.agent_id == 当前 agent_id`，再要求 `scope=agent` 或 `doc.user_key == 会话 user_key`；越权一律返回「文档不存在或无权访问」，不泄漏存在性。

分页大小 8000 < 默认 `tool_result_max_chars=12000`，避免线性文档被上下文投影层首尾拼接。

---

## 6. Agent 配置

`agent.runtime.knowledge`：

```json
{
  "enabled": true,
  "max_catalog_chars": 3000,
  "include_user_docs": true,
  "max_list_items": 50
}
```

管理台「编辑智能体 → 知识库」可调；缺省字段由 `KnowledgePolicy` 默认值补齐，旧 Agent 无需迁移。

---

## 7. 管理 API（细节见集成文档）

所有接口挂在 `/api/agents/{agent_id}/knowledge/...`，`{agent_id}` 同时是权限边界；文档归属、作用域、分页、目录前缀、错误码等**完整契约与调用脚本统一维护在集成文档**，本文不重复：

- [api-for-coding-agents.md §4 文档（知识库）](../integration/api-for-coding-agents.md) —— 字段表、分页语义、上传/覆盖、目录操作、幂等同步脚本、常见错误
- [agent-factory-integrate/SKILL.md](../integration/agent-factory-integrate/SKILL.md) —— 编程 Agent 可直接照做的 checklist

这里只记三条会反向影响设计的约束：

1. 写权限由 `enforce_agent_scope` 收口：AK/SK 只能动自己绑定的 Agent，平台 Key 可动全部；跨 Agent 取 `doc_id` 一律 404（不泄漏存在性）。
2. 列表必须分页且**不返回正文**：正文走独立 `/content` 接口，否则几百篇文档的表格首屏就要序列化整个语料。
3. 管理台不设独立菜单，入口固定在 `编辑智能体 → 知识库`（表格 + 上传弹窗 + 编辑弹窗 + 详情抽屉）。知识库是 Agent 的属性，独立菜单会诱导「先建库再挂 Agent」的错误心智。

---

## 8. 为什么不直接上向量检索

- 文档量不大时，目录 + 摘要已经能让模型判断「该读哪篇」，且完全可解释、可人工修正。
- 中文短查询在 SQLite trigram FTS 下召回很差（2 字查询基本 0 命中），调参成本高于收益。
- 没有 embedding 模型也能上线；后续需要语义召回时，可在 `kb_list` 内加一层可选的向量/全文召回，不影响现有目录协议。

---

## 9. 验收

- [x] 路径即目录：上传自动建层、目录可移动/删除
- [x] 文档必须挂在某个 Agent 下；不存在平台级/全局知识库，也没有独立菜单
- [x] Agent 共享文档仅本 Agent 可见；个人文档仅本 Agent + 同 `user_key` 可读（换 Agent 同 key 也读不到）
- [x] 上传即生成摘要；模型不可用自动回落规则抽取
- [x] 目录注入严格受 `max_catalog_chars` 约束，超限按阶梯降级
- [x] `kb_list` / `kb_read` 分页与越权隔离
- [x] 空知识库不注入、不注册工具
- [x] 删除 Agent 时一并清理其文档（不留谁也看不到的孤儿）
