# 设计：记忆（Phase E）

> 状态：已实现 v1.1（三层）  
> 依赖：Phase A–D  
> 关联：[session-model.md](./session-model.md)、[context-budget.md](./context-budget.md)

---

## 1. 分层（产品语义）

| 层 | kind | 是什么 | Prompt 策略 |
|---|---|---|---|
| L1 画像 | `profile` | 稳定偏好、客观事实、硬约束 | **每轮注入**全文（有条数/字数上限） |
| L2 实践目录 | `playbook` | 可复用实践的短标题/摘要（目录项） | **每轮注入**目录（key + 短 content） |
| L3 实践明细 | `detail` | 某实践的步骤、网址、坑、勿用路径 | **默认不注入**；`memory_read` 按 key 展开 |

短期对话过程仍是 **Session 历史 + Phase D 摘要**，不进记忆表。

唯一键：`(agent_id, user_key, kind, key)` upsert。  
`detail` 与对应 `playbook` **共用同一 `key`**（例如 `baidu_stock_query`）。

---

## 2. 写什么 / 不写什么

| 写入 | 示例 |
|---|---|
| profile | 语言偏好、称呼、岗位、永久约束 |
| playbook | 「查询百度股票的最佳实践」 |
| detail | 具体接口/页面、步骤顺序、失败过的路径 |

不写：一次性任务中间态、大段工具原文、整段聊天回放。

写入仍为 **显式工具**（v1 无自动抽取）。

---

## 3. Prompt 顺序

```text
[0] system (+ skills **catalog** only；正文经 skill_load)
[1] memory block:
      ## Profile
      ## Playbooks (catalog)   # 仅目录，无 detail 正文
[2] context_summary
[3..] recent runs
```

相关任务出现时，模型对 `key` 调 `memory_read(kind=detail)` 取 L3。

---

## 4. 工具

| 工具 | 要点 |
|---|---|
| `memory_write` | `kind`, `key`, `content`；写 `detail` 时建议已有或同时维护 `playbook` |
| `memory_read` | `kind?`, `key?`, `query?`；可读任意层；查 detail 用同 key |
| `memory_forget` | `kind?`, `key`；若删 playbook 且未指定 kind，可级联删同 key 的 detail |

---

## 5. API

- Session 仍绑定 `user_key`
- `/api/memories` 支持 `kind` 过滤
- 管理台按 kind 展示 / 删除

---

## 6. 膨胀治理

- **配额**：`MemoryPolicy` 挂在 Agent `runtime.memory`（创建/编辑可调）：每层条数与单条字数上限。  
- **淘汰**：`evict_when_full` 时超配额将最旧未 pinned 条目 `active=false`（冷目录，仍可 `memory_read`）；关闭则写失败。  
- **写入纪律**：工厂侧实现——① `memory_write` 工具描述；② 每轮 memory block 固定短指令。**不改**业务 `system_prompt` 原文。  
- **注入 vs 存储**：仅 `active=true` 的 profile/playbook 进 prompt；detail 永不默认注入。  
- **可观测**：`GET /api/memories/usage?agent_id=&user_key=`；管理台展示活跃/冷用量。

---

## 7. 验收

- [x] profile + playbook 注入；detail 不默认注入  
- [x] 同 key 的 playbook/detail 可分别读写  
- [x] 无 user_key 不注入记忆  
- [x] Agent 可配配额；超限可淘汰或拒绝  
- [x] 写入纪律在工具描述 + memory block  

