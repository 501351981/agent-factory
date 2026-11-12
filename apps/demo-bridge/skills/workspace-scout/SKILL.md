---
name: workspace-scout
description: 本地目录/工作区侦察。用户问当前目录、列文件、workspace 时使用。
---

# Workspace Scout

## When
用户询问本地目录 / cwd / 工作区 / 「当前目录」「看看目录」「列一下文件」「workspace」，或明确要文件夹列表时启用。

## When not
闲聊、业务/客服问题、与本机文件系统无关的请求——不要启用；也不要主动扫盘。

## Procedure
1. 先调用 `get_cwd`。
2. 再调用 `list_dir`：路径优先用用户给出的；否则用 `get_cwd` 结果。
3. 仅根据工具结果作答；工具失败时如实说明错误，禁止编造路径或文件名。
4. 回复必须使用下列结构（语言跟随会话 locale），且全文 ≤ 12 行；报告标题前不要加寒暄：

### Workspace Report
- cwd: <绝对路径>
- entries: <数量>
- highlight: <最多 5 个名称；目录优先>
- note: <一句风险或空目录说明；无可省略>
- skill: workspace-scout

## Guardrails
- 不要对敏感路径（如 `~/.ssh`、钥匙串、浏览器 Profile）主动深入列举；若用户强求，先警告再按工具结果简短列出名称。
- 不要把完整超长列表原样贴出；超过 20 项只报数量 + highlight。
- 需要补充说明时可读同包 `reference.md`（`skill_read`）。
