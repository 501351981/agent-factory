# 设计：Skills（按需加载 + 工厂技能包）

> 状态：v3（平台技能库 + Agent 挂载已实现）  
> 依赖：ReAct loop、Agent 管理台  
> 关联：[context-budget.md](./context-budget.md)、[memory.md](./memory.md)

---

## 1. 问题

Skill 是可含多文件的能力包（`SKILL.md`、参考文档、脚本说明、资源），不是一句风格提示。若把全文写入 Agent 配置并每轮注入 system：

- 浪费上下文预算
- 无法表达多文件包
- 脚本执行边界不清

---

## 2. 分层

| 层 | 归属 | 正文 | Catalog | 读取 |
|---|---|---|---|---|
| **平台技能库** | 全厂 | `platform_skill_packages` | Agent **启用**后 `origin=platform` | builtin |
| **Agent 包** | 单 Agent | `skill_packages` | `origin=package` | builtin |
| **用户私有包**（后续） | `agent_id + user_key` | 待定 | 仅该用户 | builtin |

常驻 prompt 的永远只有 catalog（`name + description`）。  
`skill_load` / `skill_read` 为工厂 builtin；**不要求 Bridge 实现**。

`skill-creator` 属于**平台技能库**（启动时种子），不是代码 builtin 工具。

---

## 3. 包格式（zip）

```text
<name>/
  SKILL.md              # frontmatter: name / description / version?
  references/...
  scripts/...           # 只存不跑
  assets/...
```

- 平台包与 Agent 包共用文件表 `skill_package_files`
- **不做**通用 `skill_exec`

---

## 4. Schema

```text
SkillDef { name, description, origin: package | platform | client }

platform_skill_packages(name UNIQUE, description, version?, source, ...)
agent_skill_mounts(agent_id, package_id) UNIQUE(agent_id, package_id)

skill_packages(agent_id, name, ...) UNIQUE(agent_id, name)
skill_package_files(package_id, path, ...)
```

同名：Agent 自有包与平台挂载**不可并存**。

---

## 5. API

```text
# 平台库（平台 Key）
GET|POST          /api/platform-skills
GET|PATCH|DELETE  /api/platform-skills/{name}

# Agent 启用平台技能
GET|POST|DELETE   /api/agents/{id}/skill-mounts[/{name}]

# Agent 自有包
GET|POST          /api/agents/{id}/skill-packages
GET|PATCH|DELETE  /api/agents/{id}/skill-packages/{name}
```

覆盖安装只保留当前一份；可选 `keep_description`。

---

## 6. 运行时

1. catalog 来自 `Agent.skills[]`（安装/挂载时同步）  
2. `skill_load` / `skill_read`：先解析 Agent 自有包，再解析已挂载平台包  
3. 投影上限：`context_policy.skill_result_max_chars`（默认 40000）

---

## 7. 管理台

- **平台技能**页：上传 / 改 description / 删除；展示启用 Agent 数；种子 `skill-creator`  
- **Agent 编辑 → Skills**：启用/取消平台技能 + 本 Agent 包上传/更新/删除  

---

## 8. 验收

- [x] 启动种子 `skill-creator`  
- [x] 挂载后 catalog `origin=platform`，无 Bridge 可 `skill_load`  
- [x] 同名自有包与挂载冲突拒绝  
- [x] 平台技能页 + Agent 启用 UI  

```bash
uv run --project apps/server python -m unittest \
  apps.server.tests.test_skill_packages \
  apps.server.tests.test_platform_skills \
  apps.server.tests.test_skills
```

---

## 9. 后续

- 用户私有包 + `skill_save` / `skill_delete`  
- 导出 zip；hub URL 安装  
