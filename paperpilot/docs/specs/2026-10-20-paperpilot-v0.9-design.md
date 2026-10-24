# PaperPilot v0.9「研究任务闭环」设计

日期：2026-10-20  
状态：已确认，进入实现  
范围：PaperPilot。

## 1. 目标

把现有十二个工作区收敛成一条可恢复、可观察的研究任务链：

```
研究目标
  → 检索与筛选
  → 精读证据
  → 横向对比
  → 缺口与选题
  → 写作骨架
  → 论证核查
  → 带引用的研究简报
```

用户可以在关键步骤暂停、改选论文、重试或跳过；任务状态落盘，重启后可恢复。默认离线可完整运行；配置 OpenAI-compatible 模型后获得增强的总结、问答、对比与报告能力。

同时补上三项能力：

1. 混合证据检索：BM25 + 术语/章节意图扩展 + 可选 embedding 重排；引用原文核对。
2. 模型增强：统一 LLM 客户端、重试/超时/JSON 校验/embedding；所有入口保留离线回退。
3. 交付打磨：上传论文跨重启恢复、原子写入、健康信息、测试、文档、截图与打包。

## 2. 非目标

- 不引入 Celery、Redis、Postgres、向量数据库或前端构建链。
- 不做多用户账号、权限、实时协作。
- 不承诺离线模式具备真正的语义向量理解；离线为 BM25 + 词形/术语扩展 + 章节意图，配置 embedding 后才启用向量通道。
- 不自动替用户写完整论文；写作骨架与论证建议仍需人工判断。
- 不修改演示视频、封面、字幕或交付包。

## 3. 架构

### 3.1 任务层

新增 `app/services/agent_runs.py`：

- 单一后台 worker 线程 + `queue.Queue`，按顺序执行任务。
- 任务与步骤状态持久化在 `data/agent_runs.json`；完整报告在 `data/agent_runs/<run_id>/report.md`；选中论文快照在 `data/agent_runs/<run_id>/papers.json`。
- 任务 ID：`run-<sha1(goal + timestamp + nonce)[:10]>`。
- 单实例、单进程假设；重启时把 `running/queued` 标为 `interrupted`，允许用户恢复。

步骤定义：

| id | 标题 | 调用的现有能力 | 默认检查点 |
|---|---|---|---|
| `search` | 检索与筛选 | `planner.build_plan` + `search.search` + `planner.annotate_results` | 是 |
| `read` | 精读证据 | `analysis.read_paper` | 否 |
| `compare` | 横向对比 | `analysis.compare` | 否 |
| `topic` | 缺口与选题 | `research.topic_studio` | 是 |
| `draft` | 写作骨架 | `research.draft` | 否 |
| `review` | 论证核查 | `research.check_claims` + `argument.argument_review` | 否 |
| `report` | 研究简报 | 本地模板；可选 LLM 润色 | 否 |

### 3.2 任务数据模型

```json
{
  "id": "run-abc123",
  "goal": "面向存算一体的低功耗 ADC 设计",
  "source": "demo",
  "status": "awaiting_approval",
  "current_step": "search",
  "checkpoints": ["search", "topic"],
  "max_papers": 4,
  "paper_ids": ["P1", "P2"],
  "steps": [
    {
      "id": "search",
      "title": "检索与筛选",
      "status": "done",
      "started_at": "2026-10-20 21:00:00",
      "finished_at": "2026-10-20 21:00:02",
      "summary": "从 demo 检索到 5 篇，已选 4 篇。",
      "artifact": {"candidates": [], "selected": []},
      "error": "",
      "attempts": 1
    }
  ],
  "events": [{"at": "2026-10-20 21:00:02", "type": "checkpoint", "message": "等待确认检索结果"}],
  "report": {"path": "agent_runs/run-abc123/report.md", "chars": 4200, "mode": "offline"},
  "created_at": "2026-10-20 21:00:00",
  "updated_at": "2026-10-20 21:00:02"
}
```

状态机：

```
queued → running → awaiting_approval → running → ... → completed
                  ↘ failed → (resume) → running
                  ↘ cancelled
running/queued (进程重启) → interrupted → (resume) → queued
```

动作：`approve`、`retry`、`skip`、`cancel`、`resume`。`approve` 可携带新的 `paper_ids`，用于检索检查点改选论文。

### 3.3 API

- `POST /api/agent/runs`：创建并启动任务。
- `GET /api/agent/runs`：任务摘要列表。
- `GET /api/agent/runs/{run_id}`：完整任务（含步骤产物）。
- `POST /api/agent/runs/{run_id}/action`： approve / retry / skip / cancel / resume。
- `GET /api/agent/runs/{run_id}/report`：下载 Markdown 研究简报。
- `DELETE /api/agent/runs/{run_id}`：删除任务与产物。

请求示例：

```json
{
  "goal": "面向存算一体的低功耗 ADC 设计",
  "source": "demo",
  "max_papers": 4,
  "checkpoints": ["search", "topic"]
}
```

### 3.4 前端

新增「研究任务」工作区：

- 启动表单：目标、来源、最多论文数、检查点。
- 任务列表：状态、目标、当前步骤、更新时间。
- 任务详情：垂直时间线，每步显示状态、摘要、可展开产物。
- 检索检查点：复选框改选论文，批准后继续。
- 操作按钮：批准继续、重试本步、跳过、取消、恢复、下载简报。
- 运行中每 1.5 秒轮询；离开页面或任务结束后停止。

## 4. 混合检索与引用核验

新增 `app/services/retrieval.py`：

- `rank(question, passages, top_k)`：
  - 基础排序沿用现有 `ask._rank_planned`；
  - 对前 40 个候选计算本地词法余弦（查询原词 + 术语扩展）；
  - 如配置 embedding，取查询与段落的向量余弦；
  - 用 RRF 融合基础顺序、词法顺序、向量顺序；无向量时只融合前两者。
- `verify_citations(citations, ranked)`：检查引用 quote 是否真实出现在对应段落；返回 `verified / total / unverified_indices`。
- `mode`：`bm25`、`hybrid`、`embedding`，在 `/api/ask` 返回值与界面中如实展示。
- 缓存：embedding 存 `data/cache/embeddings.json`，按模型 + 文本哈希；限制条目数量。
- 接入 `ask.answer`；`research.check_claims` 继续使用 `ask._rank`，避免行为突变。

离线边界：本地词法扩展不等于语义理解；文档和 UI 明确标注。

## 5. 模型增强

升级 `app/llm.py`：

- `chat(..., timeout, retries)`：超时与指数退避重试；4xx 不重试。
- `embed(texts, model=None)`：OpenAI-compatible `/embeddings`；无 key 或失败返回 `None`。
- `status()`：enabled / model / base_url / timeout / embeddings。
- `chat_json` 保持 JSON 容错；任务报告与对比在异常时回退离线模板。

增强点：

- 证据问答：强化“只用证据、引用编号白名单、证据不足拒答”提示。
- 任务报告：LLM 润色研究简报，失败回退本地模板；输出前校验引用编号。
- 对比/选题：现有 LLM 路径保留，补失败回退与模式标记。
- `/api/meta` 暴露 `llm` 状态；前端显示「离线规则 / 模型增强」。

## 6. 交付

- 版本 0.9.0。
- 上传论文持久化：`data/uploads/<id>.paper.json`，启动时恢复。
- 任务、上传、缓存有上限与清理；JSON 写入尽量原子化。
- 测试：现有 41 回归 + 73 专项必须保持通过；新增任务生命周期、检查点、重启恢复、检索融合、引用核验、LLM 回退、API 与上传恢复测试。
- 文档：README、CHANGELOG、说明文档、真机能力报告、发布检查清单、截图。
- 打包：更新 `PaperPilot_Project.zip`；演示视频文件不动。
- 部署：在凭据允许时同步 GitHub / ModelScope / Pages；否则给出可复现命令与待办。

## 7. 验收标准

1. 离线模式可创建一个任务，完整跑完七步，生成带引用与核查统计的 Markdown 简报。
2. 在 `search` 检查点暂停；改选论文后批准，后续步骤使用新论文集合。
3. 进程重启后，运行中的任务变为 `interrupted`，执行 `resume` 可从下一步继续。
4. `retry` 只重置当前及后续步骤，不重复已完成的前序步骤。
5. 问答返回 `retrieval.mode` 与引用核对结果；未配置 embedding 时仍可工作。
6. 无 LLM key 时，所有增强入口回退离线；配置 mock LLM 时，任务报告走 LLM 分支且引用编号受校验。
7. 现有 41/41 回归与 73/73 专项测试保持通过；新增测试通过。
8. 新前端在 1600×1200 与 390px 宽度下无横向溢出，0 console error。
9. 打包产物不含 secrets、来源配置、术语包、历史、研究库、缓存或上传论文。
10. 演示视频目录与本轮改动零接触。

## 8. 风险与取舍

- 单 worker/JSON 适合单实例部署，不适合多用户并发；文档明确。
- 任务步骤复用现有规则服务，质量受 PDF 抽取与词法检索限制；UI 展示证据边界。
- 长任务可能持续数十秒；轮询与取消降低等待焦虑，但不做流式 token。
- 真实 LLM 质量取决于第三方模型；本地测试使用 mock，不宣称真实模型效果。
