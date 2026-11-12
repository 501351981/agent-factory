# 设计：上下文预算与压缩（Phase D）

> 状态：已实现 v1  
> 依赖：Phase A/B/C（Run/Item + loop + SSE）  
> 关联：[session-model.md](./session-model.md)、[react-loop.md](./react-loop.md)

---

## 1. 问题

- `project_for_llm` 全量塞历史，长会话必爆窗口  
- 工具结果曾在写入 DB 时截断，UI 丢失原文  
- 无水位、无摘要锚点、无压缩可观测性  

目标：**持久化完整、模型输入有界、可回放、可复用摘要、前缀缓存友好**。

---

## 2. 原则

1. DB / Timeline = 全文真相；压缩只作用于 LLM 投影  
2. 默认 `auto` 压缩，不问用户（不做 `ask`）  
3. 当前 Run 的未闭合 tool 对不可拆、不可丢  
4. 先 L1/L2 便宜手段，再 L3 窗口，最后 L4 摘要  
5. 摘要带 `covered_run_ids` / `covered_until_run_id` 水位，按需增量合并，不每轮重生成  

---

## 3. 预算与水位

```text
budget = context_window_tokens - reserve_output_tokens - estimate(system+skills)
soft   = budget * soft_ratio   # 默认 0.70
hard   = budget * hard_ratio   # 默认 0.85
estimate = ceil(chars / chars_per_token)  # 默认 4，宁可高估
```

| 区间 | 行为 |
|---|---|
| `< soft` | 复用已有摘要 + 投影（仍做 L1 单条 tool 上限） |
| `soft..hard` | L1 + L2；**不改** summary 文本 |
| `>= hard` | L1–L3；若窗口外未覆盖则 L4 增量摘要 |
| 压完仍 `> budget` | Run `failed`，明确 overflow |

检查点：每次即将调用 chat.completions 之前（含同一 Run 内多轮 tool）。

---

## 4. 分层策略

| 层 | 作用 |
|---|---|
| L1 | 投影时截断 tool_result（DB 存全文） |
| L2 | 非 keep_last / 非当前 Run 的 tool 对 → 短 stub（`compacted: true`） |
| L3 | 仅展开最近 `keep_last_runs` + 当前 Run |
| L4 | 窗口外旧 Run → `context_summary` item，投影为 system 后的稳定段 |

消息布局：

```text
[0] system
[1] context_summary   # 可选；仅 L4 更新
[2..] recent runs
```

---

## 5. 摘要水位

`items.type = context_summary`，`payload`：

- `covered_run_ids` / `covered_until_run_id`  
- `created_from_summary_id?`  
- `token_estimate_before` / `after`  
- `layer: "L4"`  

投影：水位线及更早不展开全文；之后照常展开。

---

## 6. 事件与调试

- SSE：`context_compacted`（layers、before/after、covered_run_ids、summarize_failed?）  
- `GET /api/sessions/{id}/debug/llm-messages` 导出最近投影  

---

## 7. 配置

**ModelProfile**：`context_window_tokens`、`reserve_output_tokens`  

**AgentRuntimeConfig.context_policy**：

```text
mode: auto | off
soft_ratio / hard_ratio
keep_last_runs
tool_result_max_chars
summarize_enabled
summarize_max_input_chars
chars_per_token
```

---

## 8. 非本版

- 询问用户是否压缩  
- 精确 tokenizer / Anthropic cache_control  
- 长期记忆（Phase E）  

---

## 9. 验收

- [x] soft 触发 L2、hard 触发 L4；摘要可复用与增量  
  → `apps/server/tests/test_context_assemble.py`  
  （`test_soft_applies_l2_*` / `test_hard_triggers_l4_summary_reusable` / `test_l4_incremental_*`）
- [x] UI timeline 仍见工具全文  
  → 压缩只改 LLM 投影；assemble 测断言 timeline `tool_result` 仍为全文
- [x] 当前 Run tool 对不被拆  
  → 含进行中未闭合 tool_call：投影保留 call + 合成 missing result，不 fold
- [x] overflow 有明确失败  
  → assemble 置 `overflow`；loop 发 `context_overflow` 并 `failed`  
  （`test_overflow_*` / `test_context_overflow_fails_run_with_code`）
- [x] 单测覆盖估点分层与水位  
  → `test_context.py`（L1/fold）+ `test_context_assemble.py`（soft/hard/L4/overflow）

### 自动化命令

```bash
uv run --project apps/server python -m unittest \
  apps.server.tests.test_context \
  apps.server.tests.test_context_assemble \
  apps.server.tests.test_loop_contracts.LoopContractsTest.test_context_overflow_fails_run_with_code
```
