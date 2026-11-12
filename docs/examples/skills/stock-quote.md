# stock-quote

查询百度、阿里巴巴股价；回复必须使用固定键值格式。

## 工厂目录项

```json
{
  "name": "stock-quote",
  "description": "查询百度、阿里巴巴股价。用户询问这两只股票的价格或行情时使用。"
}
```

## 客户端包

`apps/demo-bridge/skills/stock-quote/SKILL.md`

## 行为要点

| 用户意图 | 回复 |
|---|---|
| 百度股价 | `symbol=baidu` / `price=66` / `currency=CNY` / `skill=stock-quote` |
| 阿里股价 | `symbol=alibaba` / `price=88` / `currency=CNY` / `skill=stock-quote` |
