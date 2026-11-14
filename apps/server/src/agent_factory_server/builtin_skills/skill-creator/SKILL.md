---
name: skill-creator
description: |
  编写或修订 Agent 技能包（SKILL.md + 可选参考文档/脚本说明）。
  用户说「做一个 skill」「写技能包」「改 SKILL.md」「沉淀成技能」「帮我生成技能」时使用。
version: 1.0.0
---

# Skill Creator

把可复用流程写成工厂技能包，而不是把长 SOP 塞进系统提示词。

## 何时用

- 用户要沉淀一套可重复执行的步骤（排查、写作、调研、操作手册）
- 需要多文件：主流程 + `references/` + `scripts/` 说明
- 修订已有技能的结构、触发描述或正文

不要把一次性闲聊整理成 skill。

## 包格式（必须遵守）

```text
<name>/
  SKILL.md              # 必需
  references/...        # 可选：只在需要时 skill_read
  scripts/...           # 可选：资料，平台不执行
  assets/...            # 可选：二进制，不可 skill_read
```

`SKILL.md` frontmatter：

```yaml
---
name: my-skill          # 小写字母/数字/连字符；稳定 ID，勿轻易改
description: >-
  一两段触发说明（进 prompt catalog）。写清「何时用 / 何时不用」。
version: 1.0.0          # 可选
---
```

正文从可执行步骤开始；每步有可检查的完成标准；需要附属文件时写明相对路径，例如 `references/checklist.md`。

## 工作流

1. **澄清**：能力边界、触发场景、成功标准、是否需要多文件。
2. **命名**：`name` 简短稳定；`description` 面向模型路由，不要空泛。
3. **起草**：先写 `SKILL.md` 主流程；分支细节进 `references/`；可重复命令进 `scripts/`（仅文档，不执行）。
4. **自检**：
   - frontmatter 有 `name` + `description`
   - `name` 符合 `^[a-z0-9]+(?:-[a-z0-9]+)*$`
   - 正文无绝对路径 / `..`
   - description 能让模型判断何时 `skill_load`
5. **交付**：输出完整文件内容（至少 `SKILL.md`）。说明打成 zip 后上传到：
   - **平台技能**（全厂库，再在 Agent 上启用），或
   - **该 Agent 的 Skills**（仅该 Agent）

## 质量条

- description ≤ 约 500 字，含触发词与反例
- 主流程线性可读；不要把百科全书塞进 `SKILL.md`
- 大段参考资料拆文件，靠 `skill_read` 按需加载
