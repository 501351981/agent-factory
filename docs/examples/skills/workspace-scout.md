# workspace-scout

工厂只绑目录；正文在客户端包。设计见 [docs/design/skills.md](../../design/skills.md)。

## 工厂目录项

```json
{
  "name": "workspace-scout",
  "description": "本地目录/工作区侦察。用户问当前目录、列文件、cwd、workspace 时先 skill_load 再执行。"
}
```

## 客户端包

`apps/demo-bridge/skills/workspace-scout/`

```text
workspace-scout/
  SKILL.md
  reference.md
```

## 行为要点

| 场景 | 行为 |
|---|---|
| 询问当前目录 / 列文件 | `skill_load` → `get_cwd` → `list_dir` → Workspace Report |
| 与文件系统无关 | 不加载本 skill |
| bridge 离线或工具失败 | 如实报错，不编造路径 |
