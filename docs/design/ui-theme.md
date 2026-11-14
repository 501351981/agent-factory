# 设计：Agent UI 主题（嵌入聊天页）

> 状态：已实现 v1  
> 关联：`packages/chat-ui` CSS 变量、`Agent.ui_theme`

---

## 1. 目标

业务嵌入官方 `/embed` 时，用 **Agent 级主题 token** 对齐品牌色，无需改 chat-ui 源码或注入任意 CSS。

---

## 2. 方案

| 层 | 行为 |
|---|---|
| Agent | `ui_theme: { preset?, bg?, accent?, … }` |
| 服务端 | 白名单 key + 安全值校验后落库 `ui_theme_json` |
| chat-ui | 拉 Agent 后映射为 `--af-*`，挂在 `.af-chat` 的 `style` |

**不做**：任意 `custom_css` 字符串（XSS / 样式逃逸）。

### Token → CSS

| 分组 | token | CSS 变量 |
|---|---|---|
| 页面 | `bg` `surface` `border` `borderSoft` `text` `muted` `accent` `accentSoft` | `--af-*` |
| 用户消息 | `userBubble` `userBubbleText` `userBubbleBorder` | `--af-user-bubble*` |
| 助手消息 | `assistantBubble` `assistantBubbleText` `assistantBubbleBorder` | `--af-assistant-bubble*` |
| 输入框 | `composerBg` `composerCard` `composerBorder` `composerText` `composerMuted` `composerSendBg` `composerSendText` | `--af-composer-*` |
| 圆角 | `radiusLg` `radiusMd` `radiusBubble` | `--af-radius-*` |
| 预设 | `preset: "dark"` | 展开深色默认后再可单项覆盖 |

允许值：`#rgb(a)`、`rgb/rgba/hsl/hsla(...)`、`transparent`、`Npx|rem|%`。含 `;` `{` `url(` 等一律丢弃。

挂载方式：chat-ui 把变量写在 `.af-chat` 的 inline `style` 上，覆盖 `style.css` 默认值；子组件（含 scoped 的 Composer）通过继承使用同一组变量，避免被局部硬编码盖住（关键面已改为 `var(--af-*)`）。

PATCH 时 `ui_theme` **整表替换**；`{}` 恢复默认。

---

## 3. 验收

- [ ] 管理台设深色预设 → embed 背景/文字变深  
- [ ] `PATCH` 只改 `accent` → 聊天气泡等强调色更新  
- [ ] 非法值被丢掉，不影响其它 token  
