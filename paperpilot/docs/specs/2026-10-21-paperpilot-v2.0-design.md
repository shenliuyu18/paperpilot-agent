# PaperPilot v2.0 研究工作区设计规格

- 日期：2026-10-21
- 版本：2.0.0
- 状态：待用户评审
- 前置版本：v1.0.0 自适应研究 Agent
- 实施计划：待规格获批后编写

## 1. 背景与目标

v1.0 把固定流水线升级为 Planner–Critic 自适应循环，单次研究任务已经具备证据账本、开放问题、决策和预算。但任务结束后，证据、问题、决策和写作成果都停留在单次运行里；学生无法把多轮研究组织成持续项目，也无法把证据直接变成可编辑、可核查、可导出的论文草稿。

v2.0 的目标是把 PaperPilot 从“单次研究任务”升级为“研究工作区”：

1. **研究项目**：以项目为顶层，承载研究问题、范围、约束和多轮运行。
2. **项目记忆**：证据、开放问题、决策和论文元数据跨运行沉淀、去重、复用。
3. **实时可干预**：运行过程可观测，支持暂停、恢复、重新规划、切换自主程度、补充约束、加入论文和注入问题。
4. **结构化写作**：把项目证据变成可编辑的章节草稿，支持段落级证据锚点、引用、核查、版本与导出。
5. **可比较的多轮研究**：同一项目的多次运行可以并排比较动作、证据、问题、决策与预算。

## 2. 范围与非目标

### 2.1 本版范围

- 新增 `Project` 顶层实体与项目主页。
- 新增项目级记忆及与运行的幂等对账。
- 新增项目内多轮运行、记忆种子和运行对比。
- 新增 SSE 实时快照流和七种运行中控制指令。
- 新增结构化草稿工作台、`write` Agent 动作、草稿核查与四种导出。
- 兼容 v1.0 独立任务；v1.0 任务可归档进项目。
- 更新测试、评测、文档、截图、打包与三端部署。

### 2.2 明确不做

- 不做账号体系、多用户隔离、实时协作或云端同步。
- 不引入数据库或分布式 Worker；继续使用单进程单 Worker 和原子 JSON 存储。
- 不做 DOCX/PDF 排版、分页预览或期刊模板适配。
- 不做富文本编辑器；草稿使用结构化 Markdown 文本块。
- 不自动改写用户正文；核查只标记问题并给建议。
- 不把核查结果当成事实真伪的最终裁决。
- 不改动既有演示视频（SVD）的任何内容或素材。
- 不做有损图片压缩、降采样或字体子集化。

## 3. 用户故事与关键流程

### 3.1 创建项目并开始第一轮研究

1. 用户在“研究项目”中新建项目，填写标题、研究问题、范围、约束和成功标准。
2. 用户从项目内启动第一轮运行，选择来源、自主程度和预算。
3. Agent 执行自适应动作，证据、问题、决策实时回写项目记忆。
4. 运行结束后，项目主页显示证据数量、未解决问题、最近决策和下一步建议。

### 3.2 基于已有记忆继续研究

1. 用户在第一轮结束后点击“基于项目记忆新建运行”。
2. 新运行带入已验证证据、未解决问题、已选方向和论文快照。
3. Planner 在已有证据上继续检索、追问、对比或写作，不重复已有结论。
4. 报告标注“沿用的项目证据”及其来源运行。

### 3.3 实时观察并干预

1. 用户打开运行详情，SSE 实时显示计划、动作开始/结束、证据新增、Critic 观察和预算。
2. 用户在当前动作执行中点击“暂停”，界面提示“将在当前动作结束后生效”。
3. 当前动作结束后运行进入 `paused`；用户补充约束、注入问题或切换自主程度，然后恢复。
4. 用户点击“重新规划”，Planner 丢弃未执行计划并基于最新约束重新选择下一步。

### 3.4 从证据到草稿

1. 用户打开项目“草稿”页，创建章节或从写作骨架导入。
2. Agent 的 `write` 动作按章节生成带证据锚点的草稿块；离线模式明确标注“待改写”。
3. 用户编辑段落、插入引用、绑定/解绑证据。
4. 用户运行“草稿核查”，系统标记无证据主张、缺失引用、引用不在项目内、数值不一致和重复段落。
5. 用户保存版本、查看块级差异，并导出 Markdown、LaTeX + BibTeX、BibTeX 或 JSON。

### 3.5 比较两轮研究

1. 用户在项目“对比”页选择 2–3 次运行。
2. 系统并排显示动作序列、工具使用、证据数量与重叠、开放问题解决情况、决策、预算和报告长度。
3. 用户据此判断哪一轮覆盖更充分，或基于差异启动下一轮。

## 4. 架构总览

```text
浏览器
  ├─ 项目主页 / 项目详情
  ├─ 运行详情（SSE + 轮询降级）
  └─ 草稿工作台
        │
FastAPI 路由
  ├─ project_service     项目生命周期、运行归属、记忆种子、归档
  ├─ draft_service       草稿读写、版本、核查、导出
  ├─ agent_runs          Worker、运行生命周期、控制指令
  ├─ agent_stream        SSE 订阅、快照广播、心跳
  ├─ agent_compare       运行对比（派生，不新增事实）
  └─ agent_actions/policy 现有自适应循环 + write/review 扩展
        │
存储
  ├─ data/projects.json
  ├─ data/projects/<project_id>/memory.json
  ├─ data/projects/<project_id>/draft.json
  ├─ data/projects/<project_id>/versions/<version_id>.json
  ├─ data/agent_runs.json
  └─ data/agent_runs/<run_id>/report.md
```

模块边界：

- `project_state.py`：项目/记忆模型、原子读写、确定性 ID、记忆提升、对账、上限裁剪。
- `project_service.py`：项目创建/更新/归档/删除、运行归属、活动运行互斥、记忆种子、v1.0 任务归档。
- `draft_state.py`：草稿模型、规范化、版本快照、修订号、上限校验。
- `draft_service.py`：草稿保存、证据/引用校验、核查、导出、Agent 写入。
- `agent_stream.py`：订阅者注册、快照广播、心跳、慢消费者处理。
- `agent_compare.py`：运行对比指标与证据差异。
- `agent_runs.py`：现有 Worker 增加控制边界、暂停状态、SSE 广播。
- 前端新增 `projects.js`、`draft.js`、`agent-live.js`，通过 `window.PaperPilot` 命名空间接入现有 `app.js`。

## 5. 数据模型

### 5.1 Project

`data/projects.json`：

```json
{
  "version": 1,
  "projects": {
    "prj-0123456789": {
      "version": 1,
      "id": "prj-0123456789",
      "title": "面向存算一体的低功耗 ADC 设计",
      "brief": {
        "question": "如何在保持精度的同时降低 ADC 功耗？",
        "scope": "近五年 CIM/ADC 架构与电路实现",
        "constraints": ["优先开放获取论文", "关注 28nm 及以下工艺"],
        "success_criteria": "形成可辩护的候选方向与证据链"
      },
      "stage": "exploring",
      "status": "active",
      "tags": ["CIM", "ADC"],
      "run_ids": [],
      "active_run_id": null,
      "counts": {
        "runs": 0,
        "evidence": 0,
        "open_questions": 0,
        "draft_sections": 0,
        "draft_blocks": 0
      },
      "created_at": "2026-10-21 10:00:00",
      "updated_at": "2026-10-21 10:00:00"
    }
  }
}
```

字段约束：

- `id` 由服务端生成，格式 `prj-` + 10 位小写十六进制。
- `title` 2–80 字符；`question` 2–500 字符；`scope` 0–1000 字符。
- `constraints` 最多 8 条，每条 2–200 字符；`success_criteria` 0–500 字符。
- `tags` 最多 8 个，每个 1–24 字符。
- `stage` ∈ `exploring | reading | writing | review | done`。
- `status` ∈ `active | archived`。
- `active_run_id` 仅在存在活动运行时非空；活动状态为 `queued | running | awaiting_approval | paused`。
- `counts` 由项目记忆和草稿写入后同步更新，供列表/详情快速展示；读取项目时可重新计算并纠正。
- 项目上限 20 个；每个项目运行上限 20；全局运行上限 40。

### 5.2 Project Memory

`data/projects/<project_id>/memory.json`：

```json
{
  "version": 1,
  "project_id": "prj-0123456789",
  "evidence": [
    {
      "id": "pev-1a2b3c4d5e",
      "paper_id": "arxiv-2401.00001",
      "title": "A Low-Power SAR ADC for CIM",
      "section": "Results",
      "page": 5,
      "quote": "The prototype achieves 4.2 fJ/conversion-step.",
      "claim": "原型达到 4.2 fJ/conversion-step",
      "verified": true,
      "kind": "text",
      "origin": {
        "run_id": "run-abc1234567",
        "action_id": "act-004",
        "tool": "read"
      },
      "created_at": "2026-10-21 10:04:00",
      "updated_at": "2026-10-21 10:04:00"
    }
  ],
  "open_questions": [
    {
      "id": "pq-0a1b2c3d4e",
      "question": "在 28nm 以下工艺，校准开销是否抵消功耗收益？",
      "status": "open",
      "priority": "high",
      "kind": "evidence",
      "origin": {
        "run_id": "run-abc1234567",
        "action_id": "act-006",
        "tool": "critic"
      },
      "recurrences": 1,
      "resolution": "",
      "history": [],
      "created_at": "2026-10-21 10:06:00",
      "updated_at": "2026-10-21 10:06:00"
    }
  ],
  "decisions": [
    {
      "id": "pd-9f8e7d6c5b",
      "kind": "topic",
      "choice": "异步 SAR ADC 的校准开销",
      "note": "用户在第 2 个检查点选择",
      "origin": {
        "run_id": "run-abc1234567",
        "action_id": "act-007",
        "tool": "topic"
      },
      "at": "2026-10-21 10:07:00"
    }
  ],
  "papers": {
    "arxiv-2401.00001": {
      "id": "arxiv-2401.00001",
      "title": "A Low-Power SAR ADC for CIM",
      "authors": ["A. Author", "B. Author"],
      "year": 2024,
      "venue": "arXiv",
      "doi": "",
      "url": "https://arxiv.org/abs/2401.00001",
      "source": "demo"
    }
  }
}
```

**确定性 ID**

- 项目证据：`pev-` + `sha1(paper_id | section | page | 规范化引文)[:10]`。
- 项目问题：`pq-` + `sha1(规范化问题)[:10]`。
- 项目决策：`pd-` + `sha1(run_id | kind | JSON(choice) | note)[:10]`。
- 同一运行重复提升、对账或重试时，ID 稳定，写入幂等。

**提升规则**

- 在每次动作成功保存后，以及读取项目详情时，从项目关联运行中提升：
  - 非空 `quote` 的运行证据；
  - 全部运行开放问题及其解决状态；
  - 运行决策；
  - 证据/论文快照中的论文元数据。
- 证据去重键：`(paper_id, section, page, 规范化引文)`；后写入的非空 `claim/title/verified` 可以补全已有条目，但不会生成新 ID。
- 问题按规范化问题文本去重；最新运行的解决状态生效。若已解决问题在新运行中再次以 open 出现，则重新打开，`recurrences += 1`，保留最近 10 条 `history`。
- 决策按确定性 ID 去重，重复提升不重复追加。
- 论文按 `paper_id` 合并，非空字段优先；不覆盖已有非空标题/作者。

**上限与裁剪**

- 证据最多 300 条；优先保留 `verified=true`、最近更新、来源运行未删除的条目。
- 开放问题最多 80 条；优先保留 open、high/medium 优先级、最近更新。
- 决策最多 120 条；按时间保留最近条目。
- 论文最多 100 条；优先保留仍被证据或草稿引用的条目。
- 裁剪只影响项目记忆，不修改运行账本。

**阶段推导**

- `stage` 以存储值为准，允许用户手动修改。
- 自动前进规则（仅向前）：
  - 项目证据 ≥ 3 或已读论文 ≥ 1：`exploring → reading`。
  - 草稿存在至少 1 个非空块：`reading → writing`。
  - 草稿核查无 high 级问题且至少 1 个章节：`writing → review`。
  - 用户显式标记完成：`review → done`。
- 自动前进不会回退；用户可手动回退，并记录项目决策。

### 5.3 Draft

`data/projects/<project_id>/draft.json`：

```json
{
  "version": 1,
  "project_id": "prj-0123456789",
  "revision": 3,
  "title": "面向存算一体的低功耗 ADC 设计：研究草稿",
  "citation_style": "numeric",
  "sections": [
    {
      "id": "sec-001",
      "title": "引言",
      "blocks": [
        {
          "id": "blk-001",
          "type": "paragraph",
          "text": "存内计算通过把计算搬到存储阵列附近降低数据搬运开销。",
          "evidence_ids": ["pev-1a2b3c4d5e"],
          "citation_keys": ["author2024"],
          "status": "draft",
          "updated_at": "2026-10-21 10:10:00"
        }
      ]
    }
  ],
  "citations": {
    "arxiv-2401.00001": {
      "key": "author2024",
      "title": "A Low-Power SAR ADC for CIM",
      "authors": ["A. Author", "B. Author"],
      "year": 2024,
      "venue": "arXiv",
      "doi": "",
      "url": "https://arxiv.org/abs/2401.00001"
    }
  },
  "versions": [
    {
      "id": "ver-001",
      "label": "初始版本",
      "reason": "manual",
      "revision": 2,
      "created_at": "2026-10-21 10:09:00",
      "summary": "1 节 / 3 块",
      "path": "versions/ver-001.json"
    }
  ],
  "created_at": "2026-10-21 10:08:00",
  "updated_at": "2026-10-21 10:10:00"
}
```

字段约束：

- `sections` 数组顺序即章节顺序；最多 30 节。
- 每节最多 80 块；草稿总块数最多 400；单块文本最多 20,000 字符；草稿总文本最多 200,000 字符。
- 块 `type` ∈ `paragraph | bullet | quote | note`。
- 块 `status` ∈ `draft | needs_evidence | checked`。
- `evidence_ids` 必须存在于项目记忆；`citation_keys` 必须存在于 `citations` 的值中。
- 引用键由服务端生成：第一作者姓氏（ASCII 化）+ 年份，小写；冲突追加 `a`、`b`；缺少作者时使用 `paper_id` 的稳定短哈希。
- 用户插入引用时调用引用端点，由服务端从项目论文元数据生成/复用引用键；前端不自行编造 `citation_keys`。
- `revision` 每次保存递增；保存请求必须携带当前 `revision`，不匹配返回 409。
- 版本快照保留最近 20 个；`reason` ∈ `manual | before_agent_write | before_restore`。
- 版本快照是完整草稿副本，包含当时的 `sections/citations/title/citation_style`，不包含 `versions` 列表本身。

### 5.4 AgentRun v3

在 v2 运行结构上新增字段：

```json
{
  "version": 3,
  "project_id": "prj-0123456789",
  "seed_evidence_ids": ["pev-1a2b3c4d5e"],
  "seed_paper_ids": ["arxiv-2401.00001"],
  "seed_summary": {"evidence": 1, "questions": 2, "papers": 1},
  "constraint": "优先开放获取论文",
  "steering": [
    {
      "id": "ctl-001",
      "kind": "add_constraint",
      "payload": {"constraint": "关注 28nm 及以下工艺"},
      "status": "pending",
      "requested_at": "2026-10-21 10:12:00",
      "applied_at": "",
      "note": ""
    }
  ],
  "pause_requested": false,
  "replan_requested": false,
  "stream_seq": 12
}
```

- `project_id` 可为空；为空表示独立任务。
- 新运行在项目中创建时，默认 `seed_memory=true`；独立任务为 false。
- 记忆种子最多带入 40 条已验证证据、20 条未解决问题、`max_papers` 篇论文和最近一次选题；带入的证据在运行账本中标记 `origin="project"` 与 `project_evidence_id`，带入的问题标记 `origin="project"` 与 `project_question_id`。
- 带入的论文写入 `seed_paper_ids`；`seed_paper_ids` 与运行已读论文共同构成 Planner 的已知论文集合。
- `constraint` 继续使用单一字符串，新约束以“；”追加，最多 600 字符。
- `steering` 最多 50 条，保留 pending/applied/rejected；已应用条目保留最近 30 条。
- `stream_seq` 每次保存递增，用于 SSE 事件 ID。
- 运行状态新增 `paused`。
- v1.0 的 `version=2` 运行在读取时补齐新字段并升级为 `version=3`；v0.9 迁移路径继续保留。

### 5.5 存储布局与一致性

- 项目索引 `data/projects.json`；项目数据目录 `data/projects/<project_id>/`。
- 运行仍存 `data/agent_runs.json`；报告仍存 `data/agent_runs/<run_id>/report.md`。
- 测试用环境变量覆盖：
  - `PAPERPILOT_PROJECT_FILE`：项目索引路径。
  - `PAPERPILOT_PROJECT_DIR`：项目数据目录。
  - 现有 `PAPERPILOT_AGENT_FILE`、`PAPERPILOT_AGENT_DIR` 保持不变。
- 所有 JSON 写入使用临时文件 + `replace` 的原子写入。
- 项目状态变更在 `project_state._LOCK` 内完成读改写；Worker 与 API 线程共享该锁。
- 记忆提升为幂等追加；运行保存成功而项目记忆写入失败时，读取项目或下次动作会触发对账补齐。
- 项目 `counts` 在记忆提升、草稿保存、运行归属变化后同步；读取项目详情时以记忆/草稿实际数据重算一次，避免计数漂移。
- 删除项目时默认保留运行并解除归属；删除运行时必须从项目 `run_ids` 和 `active_run_id` 中移除。
- 全局运行上限 40：裁剪顺序为最旧独立任务 → 已归档项目中最旧的终态运行 → 活动项目中超过 20 条后最旧的终态运行；永不裁剪活动状态运行。
- 删除项目或裁剪运行时，报告目录、项目归属和计数同步清理。

## 6. 项目生命周期

### 6.1 创建与更新

- 创建项目必须提供 `title` 与 `question`；其余字段可空。
- 更新项目可修改 `title/brief/tags/stage`；`id/created_at` 不可修改。
- 用户手动修改 `stage` 时写入项目决策 `kind="stage"`。
- 项目详情返回：项目元数据、记忆计数、最近 5 次运行摘要、草稿摘要（节数/块数/最后保存时间/最近核查问题数）。

### 6.2 运行归属与互斥

- 创建运行时若 `project_id` 非空，校验项目存在且未归档。
- 同一项目同时最多一个活动运行；冲突返回 409，响应包含 `active_run_id`。
- 项目内新运行默认带入项目记忆；可通过 `seed_memory=false` 关闭。
- 运行进入 `queued/running/awaiting_approval/paused` 时设置项目 `active_run_id`；进入 `completed/cancelled/failed/interrupted` 时清空。
- 失败/中断运行恢复或重试前，若项目已有其他活动运行，返回 409，避免两个运行并发改写同一项目记忆。
- 运行完成后提升记忆，并尝试自动推进阶段。
- 运行删除后从项目 `run_ids` 移除；若删除的是活动运行，先取消/停止。

### 6.3 v1.0 任务归档

- v1.0 运行默认保持“独立任务”，不自动创建项目。
- 归档接口 `POST /api/projects/{id}/runs/{run_id}/attach`：
  - 仅允许终态运行（completed/failed/cancelled），interrupted 需先恢复或删除；
  - 运行不得已属于其他项目；
  - 归档后写入 `project_id`、追加 `run_ids`、提升记忆。
- 已归档运行可 `POST /api/projects/{id}/runs/{run_id}/detach`，恢复为独立任务；项目记忆保留已提升证据，不删除。
- 只有在项目没有其他活动运行时，attach 才允许把 failed 运行带入；否则返回 409。

### 6.4 归档与删除

- 归档项目：`POST /api/projects/{id}/archive`；要求无活动运行，否则 409。
- 恢复项目：`POST /api/projects/{id}/restore`。
- 永久删除：`DELETE /api/projects/{id}?keep_runs=true`，仅允许已归档项目；`keep_runs` 缺省为 `true`。
  - `keep_runs=true`：删除项目记忆、草稿、版本，运行变为独立任务。
  - `keep_runs=false`：同时删除项目内运行及报告。
- 删除不可恢复；前端必须二次确认并显示将保留/删除的运行数量。

## 7. 实时运行与干预

### 7.1 SSE 快照流

端点：`GET /api/agent/runs/{run_id}/stream`

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

事件格式：

```text
id: 12
event: snapshot
data: {"seq":12,"run":{...}}

```

快照只包含紧凑字段：

```json
{
  "seq": 12,
  "run": {
    "id": "run-abc1234567",
    "project_id": "prj-0123456789",
    "goal": "……",
    "mode": "checkpoint",
    "status": "running",
    "updated_at": "2026-10-21 10:12:00",
    "plan": {"tool": "read", "title": "精读论文", "rationale": "……", "requires_approval": false},
    "steps": [
      {
        "id": "act-004",
        "tool": "read",
        "title": "精读论文",
        "status": "done",
        "started_at": "……",
        "finished_at": "……",
        "observation": {"summary": "……"},
        "error": "",
        "checkpoint": false
      }
    ],
    "evidence_count": 8,
    "latest_evidence": [],
    "open_questions": [],
    "decisions": [],
    "budget": {},
    "critic": {},
    "report": {},
    "pause_requested": false,
    "replan_requested": false
  }
}
```

约束：

- `steps` 不含 artifact；`latest_evidence` 最多 3 条；`open_questions` 与 `decisions` 各最多 10 条。
- 每个事件是完整快照，客户端可直接替换状态；重连时服务端立即发送当前快照，不重放旧快照，避免界面状态倒退。
- 心跳事件每 15 秒发送一次：

```text
event: heartbeat
data: {"at":"2026-10-21 10:12:15"}

```

- 订阅队列上限 8；队列满时丢弃最旧快照。慢客户端不会阻塞 Worker。
- 客户端断开只移除订阅，不取消运行。
- SSE 失败时前端关闭 EventSource，回退到现有 1.5 秒轮询；轮询成功后可保留降级状态直到页面刷新。

### 7.2 运行中控制

端点：`POST /api/agent/runs/{run_id}/control`

请求体：

```json
{
  "action": "pause | resume | replan | set_mode | add_constraint | add_papers | inject_question",
  "mode": "auto | checkpoint | step",
  "constraint": "关注 28nm 及以下工艺",
  "paper_ids": ["arxiv-2401.00001"],
  "question": "校准开销是否抵消功耗收益？",
  "priority": "high | medium | low"
}
```

语义矩阵：

| 动作 | 允许状态 | 立即效果 | 边界效果 |
|---|---|---|---|
| `pause` | queued / running | 写入 `pause_requested`；queued 直接转 `paused` | 当前动作结束后转 `paused`，保留计划 |
| `resume` | paused | 转 `queued` 并重新入队 | Worker 从当前计划继续 |
| `replan` | running / paused / awaiting_approval | running 写 `replan_requested`；其余立即清空计划 | 下一个动作前由 Planner 重新选择 |
| `set_mode` | 非终态 | 更新 `mode` | 不自动批准当前检查点；只影响后续检查点 |
| `add_constraint` | 非终态 | 追加约束并写入决策 | Planner 下一步读取 |
| `add_papers` | 非终态 | 校验并选择论文 | 下一步优先精读新论文 |
| `inject_question` | 非终态 | 写入运行；有项目时同步写入项目开放问题 | Planner 下一步优先追问 |

- 所有控制指令写入 `steering`，`kind` 为动作名，`status` 从 `pending` 开始。
- 当前动作结束后，Worker 依次应用 pending 指令；成功为 `applied`，校验失败为 `rejected` 并记录 `note`。
- `pause` 优先于其他指令：若同一批存在 pause，先进入 paused，其余指令保留 pending，恢复后继续应用。
- 对 `queued` 运行执行 pause 时，状态立即转 `paused`；Worker 取出该 id 后看到非执行状态直接返回，不执行动作。
- 对 `paused/awaiting_approval` 执行 replan 时，立即重新计算计划并保存；`paused` 保持暂停，`awaiting_approval` 保持等待确认，不自动放行。
- `set_mode` 不绕过已存在的 `awaiting_approval`；用户仍需 approve/resume。
- 终态运行返回 409；未知运行返回 404；参数非法返回 422。
- 每次成功控制写入运行动作事件，并由 SSE 广播新快照。
- 端点分工：现有 `/action` 继续处理 approve/retry/skip/cancel/stop 以及失败/中断/等待确认后的 resume；`/control` 只处理运行中控制和 `paused` 状态的 resume。两者都拒绝非法状态，避免重复语义；`/action` 对 `queued` 的现有幂等 resume 保持兼容。

### 7.3 Worker 边界

- Worker 在每次动作完成后、选择下一步前执行 `apply_controls(run)`。
- Worker 在开始执行一个 queued 运行前也执行一次 `apply_controls(run)`，确保排队期间提交的约束、论文和问题在第一个动作前生效。
- 若 `pause_requested` 为真：清除标志，状态置 `paused`，保存并广播，结束本轮执行。
- 若 `replan_requested` 为真：清除标志，`plan=None`，记录决策，继续循环重新规划。
- 其他 pending 指令按顺序应用；`add_papers` 通过 `agent_actions.select_papers` 校验并快照论文。
- 恢复运行前清理过期的 `pause_requested/replan_requested`，避免重启后误暂停。
- 一个项目只有一个活动运行；不同项目仍按单 Worker 顺序排队。

### 7.4 运行对比

端点：`GET /api/projects/{id}/compare?run_ids=run-a,run-b`

- 允许 2–3 个属于该项目的运行，否则 422。
- 返回：

```json
{
  "runs": [
    {
      "id": "run-a",
      "goal": "……",
      "mode": "auto",
      "status": "completed",
      "action_count": 12,
      "tool_counts": {"search": 1, "read": 4, "compare": 1, "topic": 1, "ask": 2, "draft": 1, "review": 1, "report": 1},
      "evidence_count": 16,
      "verified_count": 12,
      "open_question_count": 0,
      "resolved_question_count": 2,
      "decision_count": 3,
      "budget": {},
      "report_chars": 15136
    }
  ],
  "tool_sequences": {"run-a": ["search", "read", "read"]},
  "evidence": {
    "keys": {"run-a": ["evkey-1"], "run-b": ["evkey-2"]},
    "shared": ["evkey-1"],
    "unique": {"run-a": ["evkey-2"], "run-b": []}
  },
  "questions": {"run-a": [], "run-b": [], "shared": []},
  "decisions": {"run-a": [], "run-b": [], "shared": []}
}
```

- 证据键为 `sha1(paper_id | section | page | 规范化引文)[:12]`，不返回完整引文，避免对比响应过大。
- 对比只陈述差异，不输出“哪一轮更好”的自动结论。
- 对比结果完全由运行数据派生，不写入新事实。

## 8. 写作工作台

### 8.1 草稿编辑

- 前端维护完整草稿对象，保存时 `PUT` 全量 `sections`；服务端规范化并分配缺失 ID。
- 服务端保留客户端提供的合法 ID；重复 ID 返回 422；不合法 ID 重新生成。
- 保存时校验 `revision`；成功后 `revision += 1`，返回完整草稿。
- 块 `status` 可省略：新增块缺省为 `draft`；已有块未提供状态时保留原状态，避免保存操作重置核查结果。
- 文本按 Markdown 行内语法存储；预览只做 HTML 转义与换行保留，不解析执行 HTML；Markdown/LaTeX 导出使用存储的文本。
- 引用样式 `numeric` 按首次出现顺序生成 `[1]`，`author-year` 生成 `(Author, 2024)`；LaTeX 统一使用 `\cite{key}` 并附带 `.bib`。
- 编辑状态（浏览器本地）只保留未保存草稿与 dirty 标志；不以 localStorage 作为事实来源。

### 8.2 Agent 写入

新增 Agent 工具 `write`（标题“撰写章节”，`TOOL_DEFS.checkpoint=true`）：

- 仅当运行属于项目且项目存在草稿/写作骨架时可用；独立任务跳过。
- 若项目尚无结构化草稿，先从最近一次 `draft` artifact 的 Markdown 按 `##/###` 标题层级导入章节；无法识别的非空行作为 `paragraph` 块，不自动绑定证据。
- 选择规则：
  1. 优先选择没有非空块的章节；
  2. 其次选择首个存在空块的章节；
  3. 若所有章节都有内容，则不再选择 `write`，转入 `review`。
- 单次 `write` 最多写入 3 个块；每节最多自动写入一次（以已完成 `write` 动作 artifact 中的 `section_id` 判定），避免无限扩写。
- 输入：`{"section_id": "sec-001", "max_blocks": 3}`；输出：`{"section_id":"sec-001","blocks_added":2,"evidence_ids":["pev-..."],"mode":"offline|llm","draft_revision":4}`。
- `write` 通过引用服务从项目论文生成/复用引用键，不自行拼接引用字符串。
- 在 checkpoint 模式下，`write` 设置为 `requires_approval=true`；auto 模式继续执行，除非 Critic 请求人工确认；step 模式仍每步确认。
- 离线模式：
  - 从项目证据中选择与章节最相关的引文；
  - 生成证据锚定的保守草稿，明确加入“待结合研究问题改写”标记；
  - 不编造数据、不编造引用、不输出确定性结论；
  - 不消耗 LLM 预算。
- 若章节没有可用证据，`write` 不生成正文，转为新增一条开放问题并结束本次动作；后续由 Planner 选择检索/追问。
- 在线模式：
  - 使用现有 LLM 客户端，提示词严格限制“只能使用给定证据”；
  - 每个块必须带至少一个证据 ID；无法支撑时输出 `needs_evidence` 块而不是编造；
  - 每次调用计入 `max_llm_calls`；失败回退离线。
- 写入前自动创建版本快照 `reason="before_agent_write"`。
- Agent 写入使用执行时的最新草稿 revision 保存；若用户在此期间保存导致 409，本次动作失败并提示“草稿已被修改”，不覆盖用户内容；用户可从当前草稿重新规划。
- Planner 的已知论文集合为 `read_paper_ids ∪ seed_paper_ids`；种子证据不重复标记为已读动作。

`review` 扩展：

- 若项目存在草稿，`review` 在原有论证核查之外执行草稿核查；
- artifact 增加 `draft_audit`：问题列表、按严重度计数、块状态映射；
- 不自动改写正文；将块状态更新为 `checked` 或 `needs_evidence`；
- 核查结果同时写入项目决策 `kind="audit"`（仅摘要，不写全文）。
- 项目运行的 `report` 增加“沿用的项目证据”和“草稿进度”小节；独立任务报告保持 v1.0 结构，不出现空的项目章节。

### 8.3 草稿核查

端点：`POST /api/projects/{id}/draft/audit`

规则：

| 类型 | 严重度 | 条件 |
|---|---|---|
| `missing_evidence` | high | 非空段落无 `evidence_ids` 且文本长度 > 40 |
| `unknown_evidence` | high | `evidence_ids` 中有项目记忆中不存在的 ID |
| `unknown_citation` | high | `citation_keys` 不在草稿 `citations` 中 |
| `unsupported_claim` | medium | 段落与绑定证据的词重合度低于阈值 |
| `numeric_mismatch` | high | 段落中的数值不在绑定证据中出现 |
| `duplicate_block` | low | 规范化文本与另一块完全相同 |
| `empty_section` | medium | 章节没有任何非空块 |
| `missing_title` | low | 草稿标题为空 |

- `unsupported_claim` 的判定：规范化并去除停用词后，块文本与绑定证据的词重合度低于 0.35，且绑定证据中没有完整包含块文本的连续片段。
- `numeric_mismatch` 的判定：块中出现的阿拉伯数字/百分数/小数 token 未在任何绑定证据中出现；单位不同但数值相同的视为通过。
- 审计规则复用现有主张核查/数值校验中的规范化函数，避免两套判定。
- 返回 `{issues, counts, status_map}`；`status_map` 将块映射为 `checked` 或 `needs_evidence`。
- 手动审计与 Agent `review` 都写入项目决策 `kind="audit"` 的摘要（问题计数，不含全文），供阶段推进和项目概览使用。
- Agent `review` 将草稿核查中的 high 级问题转为运行开放问题（最多 5 条），供 Planner 在后续动作中补证据或追问；手动审计不自动新增运行问题。
- Planner 在 `review` 后若草稿 high 问题 > 0，优先选择 `ask`（最多 3 次）或扩展检索；仍无法解决则保留问题并在报告中标注。
- 有 high 级问题的块标记 `needs_evidence`；否则标记 `checked`。
- 只更新状态，不修改文本。

### 8.4 版本与差异

- `GET /api/projects/{id}/draft/versions` 返回版本摘要。
- `GET /api/projects/{id}/draft/versions/{version_id}` 返回完整快照。
- `POST /api/projects/{id}/draft/versions/{version_id}/restore`：
  - 恢复前自动创建 `before_restore` 快照；
  - 恢复后 `revision += 1`；
  - 版本列表保留最近 20 个。
- 差异由前端按块 ID 与文本比较，展示新增、删除、修改；服务端不生成差异文本。

### 8.5 导出

端点：`GET /api/projects/{id}/draft/export?format=markdown|latex|bibtex|json`

- `markdown`：`.md`，正文 + 引用列表；查询参数 `appendix=1` 时追加证据附录。
- `latex`：`.zip`，包含 `main.tex` 与 `refs.bib`；使用 `article` 模板，引用转 `\cite{key}`。
- `bibtex`：`.bib`，只导出草稿实际引用的条目。
- `json`：`.json`，包含草稿、证据、项目元数据与导出时间。
- LaTeX 转义 `\ { } $ & # ^ _ % ~`；引用键只允许 `[A-Za-z0-9:_-]`，不合法时回退稳定哈希。
- 下载文件名：标题规范化后截断 60 字符，替换非 `[\w\u4e00-\u9fff.-]` 字符为 `_`；空标题回退 `paperpilot-draft`。
- 导出不修改草稿，不触发版本。

## 9. API 契约

### 9.1 项目

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects?status=active&limit=20` | 项目列表；`status` 缺省为 active，`limit` 1–50 |
| GET | `/api/projects/{id}` | 项目详情（含计数、最近运行、草稿摘要） |
| PATCH | `/api/projects/{id}` | 更新标题/简介/标签/阶段 |
| POST | `/api/projects/{id}/archive` | 归档 |
| POST | `/api/projects/{id}/restore` | 恢复 |
| DELETE | `/api/projects/{id}?keep_runs=true` | 永久删除（仅归档项目） |
| GET | `/api/projects/{id}/memory?kind=evidence&limit=50&offset=0` | 读取项目记忆；`kind` 缺省 evidence，`limit` 1–100，`offset` ≥0 |
| GET | `/api/projects/{id}/runs` | 项目运行列表 |
| POST | `/api/projects/{id}/runs/{run_id}/attach` | 归档 v1.0 独立任务 |
| POST | `/api/projects/{id}/runs/{run_id}/detach` | 恢复为独立任务 |
| GET | `/api/projects/{id}/compare?run_ids=a,b` | 运行对比 |

创建项目请求：

```json
{
  "title": "面向存算一体的低功耗 ADC 设计",
  "question": "如何在保持精度的同时降低 ADC 功耗？",
  "scope": "近五年 CIM/ADC 架构与电路实现",
  "constraints": ["优先开放获取论文"],
  "success_criteria": "形成可辩护的候选方向与证据链",
  "tags": ["CIM", "ADC"]
}
```

### 9.2 运行

- 现有 `POST /api/agent/runs` 请求增加：

```json
{
  "project_id": "prj-0123456789",
  "seed_memory": true
}
```

- 现有 `GET /api/agent/runs`、`GET /api/agent/runs/{id}`、`POST /api/agent/runs/{id}/action`、`GET /api/agent/runs/{id}/report`、`DELETE /api/agent/runs/{id}` 保持兼容。
- 新增：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agent/runs/{id}/stream` | SSE 快照流 |
| POST | `/api/agent/runs/{id}/control` | 运行中控制 |

创建运行时项目冲突返回 409：

```json
{
  "detail": "项目已有活动运行",
  "active_run_id": "run-abc1234567"
}
```

### 9.3 草稿

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{id}/draft` | 读取草稿；不存在时返回空草稿 |
| PUT | `/api/projects/{id}/draft` | 全量保存（乐观锁） |
| POST | `/api/projects/{id}/draft/citations` | 从项目论文添加/复用引用键（乐观锁） |
| POST | `/api/projects/{id}/draft/versions` | 手动创建版本 |
| GET | `/api/projects/{id}/draft/versions` | 版本列表 |
| GET | `/api/projects/{id}/draft/versions/{version_id}` | 版本快照 |
| POST | `/api/projects/{id}/draft/versions/{version_id}/restore` | 恢复版本 |
| POST | `/api/projects/{id}/draft/audit` | 草稿核查 |
| GET | `/api/projects/{id}/draft/export?format=...` | 导出 |

创建版本请求：`{"label": "投稿前版本"}`；`label` 1–40 字符，缺省为“手动保存”。

添加引用请求：`{"paper_id": "arxiv-2401.00001", "revision": 3}`；服务端从项目 `papers` 读取元数据，返回 `{"key": "author2024", "citation": {...}, "revision": 4}`。论文不在项目记忆中返回 404；revision 不匹配返回 409。

保存请求：

```json
{
  "revision": 3,
  "title": "……",
  "citation_style": "numeric",
  "sections": [
    {
      "id": "sec-001",
      "title": "引言",
      "blocks": [
        {
          "id": "blk-001",
          "type": "paragraph",
          "text": "……",
          "evidence_ids": ["pev-1a2b3c4d5e"],
          "citation_keys": ["author2024"]
        }
      ]
    }
  ]
}
```

### 9.4 元信息

`/api/meta` 的 `features` 增加：

```json
{
  "projects": true,
  "draft_studio": true,
  "agent_stream": true,
  "agent_control": true,
  "run_compare": true
}
```

`/api/health` 版本返回 `2.0.0`。

## 10. 界面设计

### 10.1 信息架构

- 主入口新增“研究项目”；保留“研究任务”用于独立任务和 v1.0 历史任务。
- 项目列表：卡片显示标题、阶段、研究问题摘要、运行数、证据数、草稿进度、最近活动、活动运行状态。
- 项目详情标签页：
  - **概览**：研究简介、阶段、活动运行、证据亮点、开放问题、最近决策、下一步建议。
  - **运行**：运行列表、启动新运行、打开运行详情、选择 2–3 个运行对比。
  - **证据**：按论文/核验状态/来源运行筛选，显示引文与来源。
  - **草稿**：三栏写作工作台。
  - **对比**：运行对比指标与证据差异。

### 10.2 运行详情

- 保留 v1.0 自适应工作台的计划卡、动作时间线、证据账本、开放问题、决策和预算。
- 新增实时状态条：连接方式（实时/轮询降级）、当前动作、暂停/重规划请求状态。
- 控制栏：暂停、恢复、重新规划、切换自主程度、补充约束、加入论文、注入问题。
- 干预提交后显示“将在当前动作结束后生效”，并转为 pending 列表；应用后更新为已生效。
- 检查点仍支持改选论文和选择候选方向。

### 10.3 草稿工作台

- 左栏：章节大纲，支持新增/重命名/删除/拖拽排序。
- 中栏：块编辑器，支持段落/列表/引文/批注，块状态徽标、证据与引用计数。
- 右栏：证据选择器、引用列表、核查问题、版本历史。
- 工具栏：保存、保存版本、插入证据、插入引用、运行核查、预览、导出。
- 保存状态显示“已保存/未保存/冲突”；409 时提示刷新或另存版本。
- 预览使用转义后的纯文本并保留换行，不解析或执行 HTML。
- 移动端：章节与证据面板折叠为抽屉，中栏全宽。

### 10.4 项目对比

- 选择 2–3 个运行，显示指标表、工具序列、证据重叠、问题与决策差异。
- 证据差异按“共有 / 仅 A / 仅 B”展示键与论文标题，不展示完整引文。
- 空状态说明“至少需要两次运行”。

### 10.5 前端文件

- 新增 `static/projects.js`：项目列表/详情/记忆/对比。
- 新增 `static/draft.js`：草稿编辑、版本、核查、导出。
- 新增 `static/agent-live.js`：SSE 连接、降级、控制栏。
- 现有 `static/app.js` 保留旧工作区逻辑，通过 `window.PaperPilot` 暴露 `api/esc/state/render` 等必要接口。
- 新增 `static/projects.css`；现有 `agent.css` 只追加运行详情控件样式。
- 静态资源版本参数统一为 `v=2.0.0`。

## 11. 错误处理、安全与性能

### 11.1 错误语义

- 404：项目/运行/版本不存在。
- 409：项目名称冲突不适用；用于活动运行冲突、草稿 revision 冲突、归档项目仍有活动运行、向已有项目的运行重复归档。
- 422：参数校验、未知证据/引用、未知论文、控制指令参数缺失。
- 500：未预期错误；不把堆栈返回给前端。
- SSE 错误只关闭连接并触发降级，不改变运行状态。

### 11.2 安全

- 项目 ID、运行 ID、版本 ID 均由服务端生成；路径拼接前用正则校验，拒绝路径分隔符和 `..`。
- 草稿文本、证据引文、项目标题在 HTML 中统一转义；预览不执行原始 HTML。
- 导出文件名规范化，禁止路径穿越；LaTeX 内容转义，引用键白名单。
- 项目/草稿/版本上限强制执行，防止超大 JSON 拖垮单进程。
- 不把 API key、来源 token、术语表、检索历史、上传论文快照写入项目、草稿或导出包。
- 控制与草稿端点沿用现有同源策略；不新增跨域开放。

### 11.3 性能

- 项目列表最多返回 20 条摘要，不返回完整记忆或草稿。
- 项目详情只返回最近 5 次运行摘要与记忆计数；完整记忆分页读取。
- SSE 快照目标 < 100KB；`steps` 不含 artifact，长列表截断。
- 草稿总文本上限 200,000 字符；导出在请求线程内完成，目标 < 1 秒（上限数据量）。
- 项目记忆提升按运行增量执行；读取项目时只对账最近 5 次运行，完整对账在后台动作后触发。

## 12. 测试与验收

### 12.1 回归基线

- 现有 **124 条专项测试**与 **41 条主回归评测**必须全部通过。
- 现有 `/api/agent/*` 行为保持兼容；独立任务不因新字段改变计划与报告。
- v0.9 → v1.0 的迁移测试继续通过；新增 v1.0 → v2.0 迁移测试。

### 12.2 新增测试

**项目与记忆**

- 项目创建/更新/列表/归档/恢复/删除。
- 活动运行互斥；跨项目排队不受影响。
- 记忆提升幂等；证据/问题/决策确定性 ID；论文元数据合并。
- 上限裁剪；对账补齐；运行删除后的项目计数。
- v1.0 运行归档/解除归档；归档后记忆保留。
- 阶段自动前进与手动回退。

**运行与实时**

- `version=2 → 3` 规范化；独立任务字段默认值。
- 项目运行种子：证据/问题/论文/选题带入，`seed_summary` 正确。
- SSE 初始快照、动作后快照、心跳、断开清理、慢消费者丢弃旧快照。
- 七种控制指令的状态矩阵、边界生效、非法状态、暂停优先、恢复后继续。
- `set_mode` 不绕过检查点；`replan` 改变下一步计划。
- 运行对比指标、证据键重叠、2–3 个运行限制、跨项目运行拒绝。

**草稿与写作**

- 草稿创建/保存/乐观锁冲突；ID 规范化；上限校验。
- 证据 ID/引用键校验；未知证据/引用拒绝。
- 引用端点：项目论文生成稳定键、重复添加复用、乐观锁冲突、未知论文拒绝。
- 版本创建/列表/恢复/保留上限；恢复前自动快照。
- `write` 离线生成带证据锚点的块；无项目时跳过；每节最多一次；预算计入。
- `review` 草稿核查规则；块状态只在 `checked/needs_evidence` 间变化。
- 四种导出：Markdown 标题/引用、LaTeX 转义与 zip 内容、BibTeX 条目、JSON 完整性。
- XSS 转义与下载文件名规范化。

**API 与 UI**

- 项目/草稿/控制/对比 API 的成功与错误响应。
- `/api/meta` 新特性标记与版本号。
- 前端项目列表、详情、草稿编辑、证据插入、核查、导出、对比的浏览器冒烟测试。
- 0 console error；390px 宽横向溢出 0；SSE 降级后功能可用。

### 12.3 评测场景

新增 `scripts/eval_workspace.py`（或扩展现有评测脚本），至少覆盖：

1. 创建项目 → 第一轮 auto 运行 → 证据提升 → 第二轮运行复用记忆 → 报告包含沿用证据。
2. checkpoint 运行中暂停/恢复/补充约束/重新规划，动作序列发生预期变化。
3. 草稿生成 → 插入证据 → 核查发现问题 → 修改后复检 → 导出四种格式并校验内容。
4. 两次运行对比：工具序列、证据重叠、问题差异、预算差异。
5. 预算耗尽：停止写入/探索，仍生成报告并保留草稿。

### 12.4 验收清单

- [ ] 项目可创建、运行、归档、删除；项目记忆跨运行复用。
- [ ] v1.0 任务可读、可继续、可归档，不丢证据与报告。
- [ ] SSE 实时更新；断开后轮询降级；暂停在当前动作后生效。
- [ ] 七种控制指令可用且不绕过检查点。
- [ ] 草稿可编辑、绑定证据、核查、版本恢复、四种导出。
- [ ] 运行对比显示 2–3 次运行的指标与证据差异。
- [ ] 现有 124 + 41 全部通过；新增测试全部通过。
- [ ] 浏览器 0 console error；390px 横向溢出 0。
- [ ] 打包不含运行/项目数据、上传快照、来源配置、密钥、术语表、历史与缓存。
- [ ] ModelScope / GitHub Pages / Cloudflare 三端版本为 2.0.0，健康检查与静态页验证通过。

## 13. 交付与部署

- 版本号：`2.0.0`；`APP_VERSION`、`/api/health`、静态资源版本、页脚同步。
- 文档：本规格、实施计划、README、CHANGELOG、真机能力报告、评测方案、交付说明。
- 截图：项目列表、项目概览、实时运行、控制栏、草稿工作台、核查问题、运行对比。
- 演示视频：本版不强制重录；若录屏，沿用现有演示视频结构。
- 打包：`PaperPilot_Project.zip`，排除 `data/projects*`、`data/agent_runs*`、`data/uploads*`、`data/library*`、`data/custom_sources*`、`data/glossary*`、`data/history*`、`.secrets/`、缓存与日志。
- 部署：ModelScope、GitHub main/gh-pages、GitHub Pages、Cloudflare Pages；部署后验证 `/api/health`、`/api/meta`、项目页与 SSE/轮询。
- ModelScope 若缓冲 SSE，保留轮询降级并在能力报告中记录实测结果。

## 14. 风险与限制

| 风险 | 影响 | 缓解 |
|---|---|---|
| 三块能力同版交付，范围大 | 实现周期长、回归面广 | 四个内部里程碑，每个里程碑独立测试与可运行 |
| ModelScope 代理不支持 SSE 或缓冲 | 实时体验下降 | 自动轮询降级；真机验证并记录 |
| 单进程单 Worker | 多项目并发运行排队 | 项目级互斥 + 明确排队状态；文档说明 |
| JSON 存储与项目记忆膨胀 | 写入变慢、文件过大 | 上限、裁剪、紧凑快照、分页读取 |
| 草稿编辑冲突 | 用户覆盖内容 | revision 乐观锁、版本快照、409 提示 |
| 离线写作质量有限 | 草稿需要人工改写 | 明确标注“待改写”，只做证据锚定草稿，不伪装成终稿 |
| 规则核查误报/漏报 | 用户误判 | 显示规则与证据，不给出事实真伪结论；保留人工判断 |

## 15. 已确认决策

- 下一版主线为用户选择的前三项：研究工作区、论文写作工作台、实时可干预 Agent。
- 实现路线：项目为中心的分层重构。
- 写作深度：结构化写作 + 证据锚点 + 核查 + Markdown/LaTeX/BibTeX/JSON 导出；不做 DOCX/PDF 排版。
- 版本号：2.0.0。
- 继续单进程单 Worker、JSON 存储、无账号体系。
- 演示视频内容不改动；不做有损素材压缩。
