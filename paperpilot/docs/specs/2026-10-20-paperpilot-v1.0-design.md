# PaperPilot v1.0「自适应研究 Agent」设计

日期：2026-10-20  
状态：已确认，进入实现  
范围：PaperPilot。

## 1. 目标

把 v0.9 的固定七步任务升级为可自适应重规划的研究 Agent：

- 不以“走完七步”为目标，而以“证据是否足够、问题是否解决、预算是否允许”决定下一步；
- 能在检索不足时扩展检索、在证据不足时主动追问、在论证薄弱时补读/补证据；
- 保留人工决策点：改选论文、选择候选方向、补充约束、停止并收束；
- 默认离线可完整运行；LLM 只作为规划/批判/简报润色的可选增强；
- 所有决策、证据、开放问题、预算与动作轨迹可审计。

v1.0 完成标准：给定一个研究目标，Agent 能自主产生一条**非固定长度**的动作序列，并在证据不足或预算耗尽时给出带有明确边界的简报。

## 2. 非目标

- 不做多用户账号、共享研究库、跨设备同步。
- 不做论文代码沙箱执行、候选集外向量索引、图表数字化。
- 不做 SSE 流式输出；继续 1.5 秒轮询。
- 不把 Agent 推断当作实验事实；不伪造引用。
- 不修改演示视频、封面、字幕或交付包。

## 3. 架构

### 3.1 模块划分

- `app/services/agent_state.py`：运行状态、动作、证据、开放问题、预算、路径、原子存储、v0.9 迁移。
- `app/services/agent_policy.py`：纯函数 Planner/Critic：
  - `choose_next(run) -> plan | None`
  - `critique(run, step) -> observation`
  - `should_pause_before(run, plan) -> bool`
  - `budget_exhausted(run) -> str | None`
- `app/services/agent_actions.py`：动作执行器与简报：
  - `execute_action(run, plan) -> dict`
  - `build_report(run) -> (markdown, mode)`
  - `report_markdown(run_id)`
- `app/services/agent_runs.py`：生命周期、单 worker、动作循环、API 服务、旧测试兼容导出。
- 继续复用 `search / analysis / research / argument / ask / retrieval / llm`。

### 3.2 运行状态

`steps` 保留给 API/UI，但语义改为“已执行动作”，数量与顺序动态：

```json
{
  "version": 2,
  "id": "run-abc123",
  "goal": "面向存算一体的低功耗 ADC",
  "source": "demo",
  "mode": "checkpoint",
  "status": "awaiting_approval",
  "paper_ids": ["P1", "P2", "P3"],
  "selected_topic": null,
  "budget": {"max_actions": 14, "max_seconds": 300, "max_papers": 6, "max_llm_calls": 4},
  "budget_used": {"actions": 3, "seconds": 2, "llm_calls": 0},
  "plan": {
    "tool": "read",
    "title": "精读：NOVA-CIM",
    "rationale": "已有 3 篇候选，但只精读 2 篇；对比前先补齐第 3 篇证据。",
    "input": {"paper_id": "P3"},
    "requires_approval": false
  },
  "steps": [
    {
      "id": "act-001",
      "tool": "search",
      "title": "检索候选论文",
      "status": "done",
      "rationale": "建立初始候选集。",
      "input": {"query": "存算一体 低功耗 ADC"},
      "artifact": {"candidates": [], "selected": []},
      "observation": {"summary": "检索到 5 篇，选择 3 篇。", "coverage": 0.0},
      "started_at": "2026-10-20 22:00:00",
      "finished_at": "2026-10-20 22:00:01",
      "attempts": 1,
      "checkpoint": true
    }
  ],
  "evidence": [
    {"id": "ev-001", "paper_id": "P1", "title": "NOVA-CIM", "section": "Abstract",
     "page": 1, "claim": "ADC 是模拟 CIM 的能耗瓶颈", "quote": "...",
     "verified": true, "kind": "problem", "source_action": "act-002"}
  ],
  "open_questions": [
    {"id": "q-001", "question": "不同工作的 ADC 能耗是否可直接比较？", "status": "open",
     "priority": "high", "kind": "evidence", "evidence_ids": [], "source": "critic", "resolution": ""}
  ],
  "decisions": [
    {"at": "2026-10-20 22:00:01", "kind": "papers", "choice": ["P1", "P2", "P3"], "note": "检索检查点"}
  ],
  "critic": {"coverage": 0.33, "evidence_score": 0.5, "saturation": 0.8,
             "last_observation": "...", "next_hint": "read_more"},
  "report": {},
  "events": [],
  "legacy": false,
  "created_at": "...",
  "updated_at": "..."
}
```

状态：`queued / running / awaiting_approval / completed / failed / interrupted / cancelled`。  
动作状态：`running / done / failed / skipped`。

### 3.3 动作工具

| tool | 说明 | 关键输入 | 产出 |
|---|---|---|---|
| `search` | 初始或扩展检索 | query, reason | candidates/selected |
| `read` | 精读一篇未读论文 | paper_id | 摘要、问题/方法、局限、证据 |
| `ask` | 用证据问答补开放问题 | question_id, question | 答案、引用、核对结果 |
| `compare` | 对当前已读论文对比 | paper_ids | 共识、分歧、缺口 |
| `topic` | 方法簇与候选问题 | paper_ids | questions、论证卡、阅读路径 |
| `draft` | 生成相关工作骨架 | selected_topic | Markdown |
| `review` | 主张核查 + 论证体检 | draft | 支持度、未支撑句、修改动作 |
| `report` | 收束简报 | 全部状态 | Markdown、模式 |

### 3.4 Planner 策略

按以下顺序选择“下一个最有用的动作”，并保证每类动作有上限：

1. 无检索结果 → `search(goal)`。
2. 有候选但未选论文 → `search` 自动选前 `min(max_papers, 候选数)`；若候选为 0 → 扩展检索或收束。
3. 已选论文中存在未读 → `read(next)`，直到读满 `min(selected, max_papers)`。
4. 已读 ≥2 且对比缺失或对比的 `paper_ids` 与当前已读集合不一致 → `compare`。
5. 对比完成且选题缺失/过期 → `topic`。
6. 有高优先级开放问题且尚未追问、提问次数 <3 → `ask(question)`。
7. 选题完成且没有写作骨架 → `draft`。
8. 写作骨架完成且没有核查 → `review`。
9. 核查存在未支撑句或论证分 <65，且预算允许、扩展次数 <2、还有开放问题 → `ask` 或 `search(expansion)`；否则 → `report`。
10. 报告完成或预算耗尽 → 结束 / 收束报告。

预算耗尽时，若报告未生成，强制生成一次 `report`（报告动作不因预算被跳过）；若报告已生成则完成。

### 3.5 Critic

每个动作完成后计算：

- `coverage`：已读论文 / 目标论文数；
- `evidence_score`：引用核验通过率或论证支持率；
- `saturation`：本动作新增证据量 / 账本总量；
- `open_questions`：检索无结果、对比缺口、未支撑主张转化为开放问题；
- `next_hint`：`continue / read_more / expand_search / ask / human / finalize`。

Critic 先规则化，保证离线确定性；LLM 只可补充开放问题与下一步建议，且必须经过工具白名单和 JSON 校验。

### 3.6 三档自主程度

- `auto`：自动执行，直到完成、预算耗尽或 Critic 判定必须请人确认（严重矛盾/证据不足且无可用动作）。
- `checkpoint`（默认）：在 `search`、`topic` 动作完成后暂停；在 Critic 建议扩展检索或出现严重矛盾时暂停；其余自动。
- `step`：每个动作执行前暂停，用户点“执行下一步”才执行。

人工动作：

- `approve`：批准下一步；可携带 `paper_ids`（检索检查点改选）、`topic_id`（选择候选方向）、`constraint`（补充约束）。
- `stop`：停止自主探索，强制生成当前证据边界内的简报。
- `retry`：失败动作重置后重试。
- `skip`：跳过当前计划（记录决策，由 Planner 选择替代动作）。
- `cancel` / `resume`：取消或恢复。

### 3.7 预算

默认：14 个动作、300 秒、6 篇论文、4 次 LLM 调用。  
时间从 `created_at` 起算；动作数在每个动作开始时 +1；LLM 调用由可选规划/批判/报告记录。预算耗尽写入 `events` 和简报。

### 3.8 证据账本与开放问题

- 证据来自 `read`（问题/方法/局限/结果）和 `ask`（引用 quote）；去重键为 `paper_id + section + quote`。
- 引用核验沿用 v0.9 `retrieval.verify_citations`；未核验证据标记 `verified=false`。
- 开放问题有 `status: open/resolved/blocked`、`priority`、`kind: evidence/claim`、`source`；`ask` 或扩展检索可解决，收束时未解决项进入简报“未解决问题”。

## 4. API

- `POST /api/agent/runs`
  ```json
  {"goal":"...", "source":"demo", "mode":"checkpoint",
   "max_actions":14, "max_seconds":300, "max_papers":6, "max_llm_calls":4}
  ```
- `GET /api/agent/runs`：摘要。
- `GET /api/agent/runs/{id}`：完整状态（含 plan/steps/evidence/open_questions/budget/critic）。
- `POST /api/agent/runs/{id}/action`：approve/retry/skip/cancel/resume/stop；可带 paper_ids/topic_id/constraint。
- `GET /api/agent/runs/{id}/report`：Markdown。
- `DELETE /api/agent/runs/{id}`：删除。

## 5. 前端

研究任务页升级为：

- 启动表单：目标、来源、自主程度、最大动作数、最多论文数；
- 顶部“下一步计划”卡：工具、为什么要做、等待确认/预算；
- 动作时间线：每个动作显示 rationale、产出、Critic 观察；
- 右侧账本：证据（含 verified）、开放问题、预算、人工决策；
- 检查点：检索选论文、选题选方向；
- 按钮：执行下一步/批准/停止并生成简报/重试/取消/恢复/下载报告；
- 移动端单列，无横向溢出。

## 6. 迁移

- `version < 2` 或存在 `steps` 但无 `plan/budget` 的 v0.9 运行：读取时迁移。
- 固定 step 映射为 action（`tool=step.id`）；已完成任务可查看和下载旧报告；未完成任务尽量从下一步恢复，否则标记 `legacy` 只读。
- 迁移结果写回存储，保留 `legacy: true` 与 `migrated_from: "0.9"`。

## 7. 验收

1. `auto` 模式可在无人工干预下完成一个 3–4 篇 demo 任务，动作数不固定，报告含证据账本与未解决问题。
2. `checkpoint` 模式在检索和选题后暂停；改选论文/选择方向后继续。
3. `step` 模式每个动作前暂停，批准一次执行一个动作。
4. 证据不足场景触发额外 `ask` 或 `search`，至少产生 1 次重规划。
5. 预算耗尽时停止探索并生成带“未解决问题”的简报，不无限循环。
6. 旧 v0.9 任务可查看；未完成的能恢复或明确只读。
7. 现有 41 条回归保持通过；新增 Planner/Critic/动作/worker/API 测试通过。
8. 浏览器 1600×1200 与 390px：0 console error、无横向溢出。
9. 打包不含运行时任务、上传快照、密钥、研究库、来源配置、术语包、历史或缓存。
10. 演示视频资产零改动。

## 8. 风险与取舍

- 自适应规划可能增加动作数；用预算、每类动作上限、饱和度和去重控制。
- 规则 Planner 的上限是启发式，不等同于专家策略；简报必须暴露未解决问题。
- LLM 规划/批判可能不稳定；默认关闭，所有输出需验证，失败回退规则。
- 单进程单 worker 仍不适合多用户并发；文档明确。
