# 设计：Skills（按需加载）

> 状态：已实现 v1  
> 依赖：ReAct loop、client tool 通道  
> 关联：[context-budget.md](./context-budget.md)、[memory.md](./memory.md)

---

## 1. 问题

Skill 不是一句风格提示，而是可含多文件（`SKILL.md`、参考文档、脚本说明）的能力包。若把全文写入 Agent 配置并每轮注入 system：

- 浪费上下文预算（与 Phase D 冲突）
- 无法表达多文件包
- 本地路径 / 脚本能力不应放进工厂进程

---

## 2. 选定方案

**工厂只持有技能目录（name + description）；正文与附属文件在客户端本地包中，经 `skill_load` / `skill_read` 按需进入对话。**

对齐 Cursor progressive disclosure，并与现有 `execution: client` 工具对称。

| 层 | 存放 | 是否常驻 prompt |
|---|---|---|
| 目录 | Agent.`skills[]` = `{name, description}` | **是**（仅短描述） |
| 正文 | 客户端 `skills/<name>/SKILL.md` | **否**；`skill_load` 后作为 tool_result |
| 附属文件 | 同目录相对路径 | **否**；`skill_read` 按需 |

**非目标（本版）**：工厂技能包仓库/上传分发、服务端托管正文、隐式自动注入正文。

---

## 3. Prompt 组装

```text
[0] system_prompt
    + Skills catalog（name + description + 「须先 skill_load」纪律）
[1] memory block
[2] context_summary
[3..] history + 当前 turn（含按需 load 的 tool_result）
```

纪律（工厂写入 catalog 段，不改业务 system_prompt 原文）：

- 目录匹配用户意图时，先 `skill_load(name)`，再按返回的 `SKILL.md` 执行
- 未 load 不得假装已掌握正文 SOP
- 与任务无关的 skill 不要 load

---

## 4. 工具

若 Agent 要提供 Skill 能力（配置了 `skills[]`），运行时会合并下列 client 工具；Bridge **须实现**二者：

| 工具 | 参数 | 行为 |
|---|---|---|
| `skill_load` | `name` | Bridge 读 `SKILL.md`，返回正文 + 包内文件清单 |
| `skill_read` | `name`, `path` | 读包内相对文件；禁止 `..` / 绝对路径 / 逃逸 |

未配置 `skills[]` 时不注入；纯 remote_http、不用 Skill 的 Bridge 可不实现。

失败语义：`{ok:false, error}`（未安装、路径非法、bridge 离线）。

---

## 5. 客户端目录约定

```text
$FACTORY_SKILLS_DIR/          # 优先
  <skill-name>/
    SKILL.md                  # 必需
    reference.md              # 可选
    scripts/…                 # 可选（说明或由其它 client tools 执行）
```

解析顺序：`FACTORY_SKILLS_DIR` → `cwd/skills` → `demo-bridge` 包旁 `skills/` → `~/.agent-factory/skills`。

`name`：小写字母/数字/连字符，与目录名一致。

---

## 6. Schema

```text
SkillDef { name: str, description: str }
```

遗留字段 `content`：读入时丢弃，不进 prompt。

---

## 7. 聊天 UI：`/` 唤起

`packages/chat-ui` Composer 使用 **contenteditable**（对齐 `demo/ai-chat-input`）：输入 `/` 弹出技能目录；选中后以**行内不可编辑 chip**插入（与正文同一行）。发送时 chip 序列化为 `/skill-name`，并在 message 前注入 `skill_load(name=...)` 指令（仍走纯文本 Chat API）。

## 8. 验收清单

- [x] Agent 仅配置 name+description 时，debug 的 system 段无 SKILL 正文  
  → `apps/server/tests/test_skills.py`（含真实 `workspace-scout/SKILL.md` 不泄漏）
- [x] 正例触发：`skill_load` → 再调业务 tools；timeline 可见  
  → `test_loop_contracts.test_skill_load_then_business_tool_on_timeline`  
  → 手工：问「看看当前目录」应 `skill_load` → `get_cwd`/`list_dir` → Workspace Report
- [x] 负例：无关问题不调用 `skill_load`  
  → catalog 纪律写入 system；手工：闲聊/客服类问题不应 load  
  → 对照 skill：`stock-quote` 仅在问百度/阿里股价时启用
- [x] bridge 离线 / 未安装 skill：tool_result 报错，不编造正文  
  → `test_skill_load_errors_when_bridge_offline` + `demo-bridge/tests/test_skills_fs.py`
- [x] `skill_read` 无法读出包外路径  
  → `demo-bridge/tests/test_skills_fs.py`（`..` / 绝对路径 / 逃逸）
- [x] 输入 `/` 可选中 skill；发送注入 `skill_load` 指令  
  → `packages/chat-ui`：`buildSkillPrefixedMessage` + Composer chip；`pnpm --dir packages/chat-ui test`  
  → 手工：选 `/workspace-scout` 发送后 timeline 出现 `skill_load`

### 自动化命令

```bash
uv run --project apps/server python -m unittest apps.server.tests.test_skills apps.server.tests.test_loop_contracts
uv run --project apps/demo-bridge python -m unittest discover -s apps/demo-bridge/tests -v
pnpm --dir packages/chat-ui test
```

---

## 9. 后续（可选 Phase 2）

独立「技能库」页：上传 zip、版本、向客户端同步。运行时默认仍由客户端加载；工厂不做每轮全文注入。
