# PaperPilot v5.1 补全与缺陷修复设计规格

- 日期：2026-10-22
- 版本：5.1.0
- 状态：已实施
- 前置版本：v5.0.0 可交付稿

## 1. 背景

v5.0 交付时明确留下了四项未完成能力与一处已知判定缺陷：

1. PDF 导出未实现（`format=pdf` 返回 422）。
2. 跨论文矛盾只有规则检测，没有模型复核入口。
3. 订阅调度只验证了启停与幂等，没有验证并发执行。
4. 人工金标集只有脚本，没有候选池、校验与双标注合并流程。
5. 同领域跨论文句子在 0.20–0.45 重合区被判 `partial`，而不是 `unsupported`。

v5.1 的目标是把前四项补成可运行、可测试、可交接的能力，并修复第五项；**不伪造人工标注数据**。

## 2. 范围

### 2.1 本版范围

- PDF 导出：XeLaTeX 渲染，工具链可选，缺失时明确报错。
- 矛盾模型复核：确认/排除/存疑，只读裁决，离线降级。
- 订阅并发：单进程内 per-subscription 非阻塞锁与运行列表。
- 判定修复：数值覆盖与系统/方法标记检查。
- 金标集工作台：候选抽取、校验、双标注合并、分歧日志。
- 真机评测脚本：无 key 退出 2，不产生假指标。

### 2.2 明确不做

- 不内置 TeX 发行版；不把 PDF 失败静默降级成 LaTeX。
- 不做跨进程/分布式订阅锁；单 Worker 语义不变。
- 不让模型改写 `paper_a/paper_b`、指标名、引句或严重度。
- 不机器生成 `label` 冒充人工标注。
- 不承诺 PDF 排版达到期刊模板级；公式仍按源码保留。

## 3. PDF 导出

### 3.1 工具链

- 优先 `PAPERPILOT_XELATEX`，否则 `shutil.which("xelatex")`。
- 没有工具链：`PDFUnavailable` → API **503**（v5.0 计划写的 409 语义不准确，本版改为 503）。
- 编译失败或超时：`PDFBuildError` → API **422**，消息截取最后 600 字日志。

### 3.2 文档模型

- `ctexart`、A4、2.5cm 页边距，标题/副标题/作者与单位/摘要/关键词/基金。
- 章节、段落、列表、引用块；`quote`/`note` 使用 `quote` 环境。
- 数字样式用 `\cite{safe_key}` + 手工 `thebibliography`；作者-年份样式直接使用
  `citation_styles.marker()` 文本，避免 LaTeX 给 `\cite` 标签再加一层方括号。
- 所有正文与文献条目经 `draft_export._tex_escape` 转义；公式保留 LaTeX 源码。
- 连续编译两次，第二次解析引用；`-no-shell-escape`、`-halt-on-error`、120s 超时。

### 3.3 前端

- 草稿工具栏与稿件抽屉新增“导出 PDF”。
- 导出统一改为 `fetch`：非 2xx 读取 `detail` 弹 toast；成功按
  `Content-Disposition` 的 `filename*` 命名并用 blob 下载。

## 4. 矛盾模型复核

### 4.1 输入与输出

- 只处理 `status == "open"` 且无 `review` 的条目，单次最多 20 条（HTTP 层 1–20，默认 8）。
- 模型 schema：`{"verdict": "confirmed|dismissed|uncertain", "reason": str, "quote_a?": str, "quote_b?": str}`。
- 写回只合并 `review` 与 `status`，原文、指标、严重度不变。
- `dismissed` 条目从草稿核查中排除；`confirmed`/`uncertain` 保留并展示复核理由。

### 4.2 防伪造与降级

- 模型若回显引句，必须是原引句的归一化子串，否则该条复核作废并计入 `rejected_quote`。
- 未配置 key、`use_llm=False` 或网关失败：`degraded=True` + `degrade_reason`，**不写盘**。
- 模型始终不是裁决者：`uncertain` 保留给人工判断。

## 5. 订阅并发

- `_RUNNING` 集合 + 每订阅一把 `threading.Lock`；`run_once(block=False)` 抢不到锁返回
  `{"skipped": "busy"}`，不写游标、不重复抓取。
- `due()` 跳过运行中的订阅；`scheduler_status()["running_ids"]` 暴露运行列表。
- 调度循环逐条捕获异常，一个坏订阅不会卡住同一 tick 的其余订阅。
- 锁在 `finally` 释放；传输失败后下一次仍可运行。
- 已知边界：只保证单进程；多 Worker 需外部锁，不在本版。

## 6. 判定修复（`grounding.classify_claim`）

新增信号：

- `number_overlap`：主张中的数值在证据中出现的比例。
- `marker_overlap`：主张中的系统/方法名称（长度≥3 的拉丁词，去停用词与纯数字）在证据中出现的比例。

规则顺序：

1. 重合度 < `CLAIM_PARTIAL_OVERLAP` → `unsupported`。
2. 重合度 < `CLAIM_SUPPORT_OVERLAP` 且 `number_overlap < 0.5` → `unsupported`
   （同领域另一篇论文：词汇够像，但测量值属于另一组数据）。
3. 重合度 < 支持阈值且 `marker_overlap < 0.25` → `unsupported`
   （方法/系统名称大多不在证据中）。
4. 主张明显长于证据 → `partial`。
5. 个别数值缺失 → `partial`。
6. 重合度达标 → `supported`。
7. 其余 → `partial`。

否定与方向冲突仍最后处理。逐字句只改一个数字时重合度仍高，继续走“个别数值缺失 → partial”。

## 7. 金标集工作台

### 7.1 命令

| 命令 | 作用 |
|---|---|
| `build [--source demo|parsed|all] [--papers FILE] [--out DIR]` | 抽取候选 claim/quote，`label` 留空 |
| `check FILE [--strict]` | 校验 id/标签/引句子串/主题规模 |
| `merge A B --out C` | 合并两份独立标注，一致采用，分歧留空并记录 |
| `stats` | 各主题论文数、候选数、已标注数、缺口 |

### 7.2 数据

- 候选目录：`data/eval/golden/candidates/<topic>.json`，`provenance: human-required`。
- 候选不进入 `eval_content`（`load_golden_dir` 非递归）。
- 合并文件：`provenance: human`；有分歧时 `status: needs-adjudication`，`eval_content` 跳过。
- 目标：4 主题 × 6 篇、每主题 ≥50 条、合计 ≥200 条、双人独立标注。

### 7.3 当前离线状态

- 可用语料只有演示数据与 `data/parsed`，`cim-adc` 5 篇 / 36 条、`llm-inference-quantization` 4 篇 / 32 条；
  `rag-pipelines` 与 `recommender-systems` 为 0。
- 因此人工标注仍未完成；工作台与候选池已就绪，标注者需用 `--papers` 或解析后的论文补齐语料。

## 8. 真机评测

- `scripts/eval_real_llm.py --check` 打印 endpoint/model/routes；无 key 退出 2。
- `--claims` 在已标注金标集上跑 `online_predictor`，记录规则/模型一致、分歧、降级、拒引句。
- `--contradictions PROJECT` 调在线复核。
- 无 key 或没有已标注集合时退出 2，不写报告；报告含 `real_model: true` 与实际耗时。

## 9. API 变更

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{id}/draft/export?format=pdf` | 200 PDF / 503 无工具链 / 422 编译失败 |
| POST | `/api/projects/{id}/contradictions/review` | `{use_llm, limit}`，离线降级 |
| GET | `/api/subscriptions` | `scheduler.running_ids` 新增 |

`/api/meta` 特性位 38 → **41**，新增 `pdf`、`contradiction_review`、`subscription_concurrency`。

## 10. 验收

- 全量测试 **494/494**（v5.0 为 461，新增 33）。
- 主回归 41/41；`eval_agent` PASS；`eval_workspace` PASS；`eval_assistant` 13/13。
- 端到端验收 **69/69**（v5.0 为 65，新增 PDF、离线复核不改写、特性位、调度运行列表）。
- `eval_content --strict` 退出码 0；构造集 32 条 + 同领域硬负例 37 条，指标 1.000。
- 本机 XeLaTeX 实际产出 PDF（验收 82,670 字节，`%PDF` 头）。
- 无 key 时 `eval_real_llm.py --check` 退出 2，不产生报告。

## 11. 风险

| 风险 | 缓解 |
|---|---|
| 无 TeX 的部署环境 | 503 + 安装提示；DOCX/Markdown/LaTeX 不受影响 |
| 模型复核过度自信 | 只读裁决；引句回显校验；存疑保留人工 |
| 数值规则误伤同义改写 | 只在重合度未达支持阈值时生效；数值一半以上匹配即不触发 |
| 标记规则误伤中文主张 | 只在存在拉丁标记时生效 |
| 多人标注流于形式 | 合并脚本强制未标注不可合并、分歧不可删除 |
| 跨进程并发 | 文档明确单进程边界；不承诺分布式 |
