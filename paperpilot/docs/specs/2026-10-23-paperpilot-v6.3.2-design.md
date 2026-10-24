# PaperPilot v6.3.2 设计：真实模型路径的三处错位

日期：2026-10-23

## 背景

v6.3.1 的离线一致性审计（42 项）全部通过，但模型路径的 10 项审计因供应商配额耗尽未跑完。获得可用端点后，用 `deepseek-v4.1-flash-1017` 跑 `scripts/live_audit_consistency.py`，暴露了三处“部件之间对不上”：

1. Agent 简报被判定为 `offline`，调用审计出现 `schema` 降级。
2. 模型版简报的正文带 `[Pn]`，但没有 `## 参考文献`。
3. Copilot 回答里出现 4 个片段编号，`citations` 只返回 2 条。

## 根因与修复

### 1. 推理额度耗尽导致的空正文

`llm.chat` 对空 `content` 抛 `LLMBadResponse`，网关按原 `max_tokens` 重试一次。推理型模型在 2600 token 上限下把全部额度用于隐藏推理：`finish_reason=length`、`reasoning_tokens=2600`、`content=""`；同预算重试仍然为空，简报回退离线。

- `llm.extract_content` 在空正文时读取 `usage.completion_tokens_details.reasoning_tokens` 与 `finish_reason`，把“推理占满上限”标记在 `LLMBadResponse.reasoning_exhausted`。
- `llm_gateway._call_with_empty_retry` 只在这种情况下把重试上限提高到 16384；普通空回复保持原上限。审计只记录最终结果，成功重试不会留下 `schema` 降级。

### 2. 模型简报缺少题录

`_with_legend` 只重建图例，不检查参考文献。模型可以省略 `## 参考文献`，正文编号因此悬空；模型自写的题录也可能把编号指向错误的论文。

- 抽出 `_reference_line` / `_references_block`，离线与模型共用同一题录格式（作者、标题、会议、年份、DOI/URL）。
- `_with_legend` 找到 `## 参考文献` 段时用运行论文顺序重建该段，保留其后的章节；没有该段时追加到文末。

### 3. Copilot 边界引用丢失

`_online_reply` 先计算 `answer_indices` 和 `citations`，再拼接模型返回的 `boundary`。边界里的 `[n]` 不会进入引用列表，读者看到编号却找不到条目。

- 先拼接 `证据边界：…`，再用 `grounding.citation_indices` 一次性解析全文编号；`validate_answer` 同样校验拼接后的文本，越界编号仍触发 `citation_out_of_range` 回退。

## 审计脚本同步

- L4 的数字基准改用运行目录下的 `papers.json`（含摘要、tldr、指标、结果），不再只用项目记忆里的题录；正文提取跳过所有 Markdown 标题，避免把 `### 6.1` 当成数据。
- L5 新增“引用列表条数 ≥ 回答中不同 `[n]` 数量”。
- L6 的数字来源加入引用条目的 `title` 与 `locator`。

## 验证

- 真实端点（阿里云百炼兼容模式 · `deepseek-v4.1-flash-1017`）：10/10。
- 全量 590 条测试、90/90 验收、42/42 离线审计、41/41 回归、`eval_content --strict` 退出码 0。
- 新增测试：推理耗尽标记与预算提升、空回复原预算保持、模型题录重建、Copilot 边界引用。

## 边界

- 真实模型结果只覆盖一个供应商与一个模型；不替代人工质量评分与人工金标集。
- 16384 是空正文重试时的上限，不是所有调用的默认值；非推理模型成功调用不会因此增加生成长度。
