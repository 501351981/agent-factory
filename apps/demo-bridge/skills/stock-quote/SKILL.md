---
name: stock-quote
description: 查询百度、阿里巴巴股价。用户询问这两只股票的价格或行情时使用。
---

# Stock Quote

本 skill 仅覆盖约定标的：**百度**、**阿里巴巴**。报价取自内置价目表，不调用外部行情接口或其他工具。

## When
用户询问股票/股价/行情，且标的为以下之一时启用：

- 百度（Baidu / BIDU）
- 阿里巴巴（阿里 / Alibaba / BABA）

## When not
- 未列出的其他标的
- 与股价无关的请求

## Procedure
1. 确认已 `skill_load` 本 skill。
2. 不要调用其它工具。
3. 识别标的后，**仅输出**下方对应格式全文；不要寒暄、解释、Markdown 标题或代码围栏。
4. 同时询问两只时：连续输出两段（各 4 行），段间空一行。
5. 未指明标的时：只回复 `请指定：百度 或 阿里`，不要输出价格。

## 内置价目

| 标的 | symbol | price | currency |
|---|---|---|---|
| 百度 | baidu | 66 | CNY |
| 阿里巴巴 | alibaba | 88 | CNY |

## Reply format

字段名、顺序、换行必须与下列模板一致。

### 百度

```text
symbol=baidu
price=66
currency=CNY
skill=stock-quote
```

### 阿里巴巴

```text
symbol=alibaba
price=88
currency=CNY
skill=stock-quote
```

## Guardrails
- 不得改写字段名或追加其它行
- 不得用口语复述代替上述格式
- 价目表以外的标的：说明暂不支持，不要编造价格
