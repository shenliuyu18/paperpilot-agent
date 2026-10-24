# PaperPilot v5.0 可信成稿与持续研究设计规格

- 日期：2026-10-22
- 版本：5.0.0（本版规格覆盖 v5.0.0 / v5.1.0 / v5.2.0 三次发布，见 §14）
- 状态：待实施
- 前置版本：v4.2.0
- 基线实测（2026-10-22，同步 `5b952c17` 后复跑）：全量测试 **350/350**、主回归 **41/41**、
  `eval_agent` PASS、`eval_workspace` PASS、`eval_assistant` **13/13**

## 1. 背景与目标

v4.2.0 已经把链路做到"能跑通真实论文的完整研究流程"：项目与跨轮记忆、实时干预与 SSE、草稿工作台、
项目 Copilot 与引用接地、统一模型网关、概念图谱、逐对可比性。但它停在三个缺口上：

1. **交不出去**：导出只有 Markdown / LaTeX / BibTeX / JSON。中文课程论文与毕设要 Word；参考文献没有
   GB/T 7714 等样式；图表编号、投稿前检查全靠人工。v2/v3 规格把 DOCX/PDF 列为"不做"，本版按用户决策**解禁**。
2. **证明不了**：现有 grounding 是"编号范围 + 原文子串 + 数字命中"级别的校验；语义核查是词法规则 + 可选模型，
   但没有**内容质量基线**。任何"增强"都无法证明自己没让结果变差。
3. **跟不动**：每次调研都要人肉重跑。没有订阅、没有增量索引、没有"新论文来了，哪些已有结论要改"的差异定位。

v5.0 的目标：

1. **可信成稿**：稿件元数据 + 三种引用样式 + 一致性校验 + DOCX/LaTeX/PDF 导出 + 投稿前检查，
   让草稿变成能交的稿子；所有输出都建立在同一份内容模型上。
2. **可证质量**：建立金标集与内容质量指标（引用精度、主张支持率、未支撑句率、数值一致率、证据覆盖率、
   矛盾检出率），把它们变成每次迭代必须通过的门禁；真实模型路径首次进入评测。
3. **持续研究**：检索订阅 + 增量索引 + 新论文差异定位，把工具从"一次性调研"变成"长期跟踪"。
4. **证据语义升级**：把"支持关系"从二值改成三档并给出理由与反例；新增跨论文矛盾检测，冲突直接进草稿核查。

## 2. 范围与非目标

### 2.1 本版范围

**A. 质量基线与证据语义（地基）**

- 金标集：4 主题 × 6 篇真实开放获取论文，200+ 主张-证据对人工标注（含分歧记录）。
- `scripts/eval_content.py`：六项指标、可对比报告、阈值门禁；先量出 4.2.0 基线。
- 三档支持判定（`supported / partial / unsupported`）+ 判定理由 + 最相近反例摘句；规则初判 + 可选模型复核。
- 跨论文矛盾检测（同指标不同结论、同方法不同前提），产出为一等公民数据。
- 引用双向追溯：文内编号 → 引用键 → 证据 → 原文页码与摘句。

**B. 可交付稿（主线）**

- 引用样式引擎：GB/T 7714-2015、APA 7、IEEE；文内标记与参考文献表由同一引擎生成。
- 一致性校验：缺项、重复、编号顺序、未被引用条目、字段完整度（按样式要求分级提示）。
- 稿件元数据：标题、作者、单位、摘要、关键词、基金；随项目保存为 `manuscript.json`。
- 导出：Markdown（既有）、LaTeX（加样式渲染的参考文献清单）、**DOCX**；PDF 经 LaTeX 工具链，属后续实现
  （当前导出枚举不含 `pdf`，调用返回 422，不做静默降级）。
- 投稿前检查：字数、图表编号与交叉引用、引用完整性、致谢/基金、疑似未支撑句清单一键定位。

**C. 持续研究（延展）**

- 检索订阅：保存查询（关键词/来源/结果上限），单进程内定时执行，持久化游标。
- 增量索引：论文指纹（DOI → arXiv ID → 标题+年份哈希）、首次入库时间、关联项目。
- 差异定位：新论文相对项目已有证据 → `新增支持 / 潜在冲突 / 方法补充 / 无关`，冲突项进草稿核查。
- 可选向量召回：默认关闭、需模型 key，只用于扩候选，BM25 仍是第一层。

**D. 真实模型治理（B 的后半）**

- 真实模型在金标集上的评测；在线/离线支持关系一致率与降级率指标。
- 模型注册与用途路由（写作建议 / 核查复核 / 摘要润色）。
- 成本与缓存命中报表；降级可解释（为什么降级、缺什么校验）。

**E. 收口**：版本号、文档、截图、打包、三端部署与线上真机验证。

### 2.2 明确不做

沿用既有非目标，**仅 DOCX 一项按用户决策解禁**：

- 不做账号、多用户、协作、云同步、数据库、分布式 Worker；订阅调度为单进程内线程 + 持久化游标。
- **DOCX 只做课程论文/毕设级模板**：不做期刊模板、不做分页预览、不做所见即所得编辑器。
- **DOCX 不排版数学公式**：公式以 LaTeX/Unicode 源码保留并在导出摘要中计数提示；OMML 原生公式列入后续评估。
- 不改写用户正文；不把模型输出当事实真伪裁决；三档判定与矛盾检测都只标记并给理由。
- 不做跨项目资产合并；不改演示视频内容；不做有损素材处理。

## 3. 用户故事

### 3.1 交一份能交的稿

用户写完草稿，在稿件设置里填标题/作者/单位/摘要/关键词，选 GB/T 7714-2015，导出 DOCX。
导出前系统提示：「3 条引用缺卷期页码」「2 条参考文献未被正文引用」「4 段正文未绑定证据」，
用户逐条修完再导出；Word 打开无修复提示，参考文献表与文内编号一致。

### 3.2 发现跨论文矛盾

两篇论文都报告了同一指标，结论方向相反。草稿里引用了其中一篇并据此下结论。
核查面板出现「潜在矛盾」：列出另一篇的原文摘句与指标口径差异，建议改为条件化表述或补充引用。
系统不替用户改正文，只给出可点击定位。

### 3.3 跟踪一个方向

用户保存订阅「efficient LLM inference quantization / arXiv / 每周」。
一周后新增 5 篇，系统给出：2 篇与现有证据冲突、2 篇补充方法、1 篇无关；
冲突项直接出现在项目核查清单里，并标注对应草稿块。

### 3.4 知道是否变好了

用户（或维护者）运行 `scripts/eval_content.py`，得到六项分数与上次对比：
引用精度 0.91 → 0.93、未支撑句率 7% → 4%。分数低于门槛时脚本以非零退出码失败。

## 4. 架构

```text
static/manuscript.js  ──fetch──>  app/main.py
static/tracking.js    ──fetch──>     │
                                     ├─ manuscript ── citation_styles ── docx_export
                                     ├─ contradictions ── grounding(三档)
                                     ├─ subscriptions ── paper_index ── search/discovery
                                     ├─ draft_audit / draft_semantic（复用并升级）
                                     └─ llm_gateway ── app/llm.py
                                     │
        data/projects/<id>/manuscript.json, contradictions.json, draft.json(claims)
        data/subscriptions.json, data/index.json, data/eval/golden/*.json
```

新增模块（`app/services/`）：

| 模块 | 职责 |
|---|---|
| `citation_styles.py` | 样式定义与格式化：文内标记、参考文献条目、字段完整度、排序与一致性校验 |
| `manuscript.py` | 稿件元数据读写、导出编排、投稿前检查汇总 |
| `docx_export.py` | 统一内容模型 → DOCX（`python-docx`），模板化标题/图表题/参考文献/页眉页脚 |
| `contradictions.py` | 矛盾检测（指标/结论方向/前提差异）与记录读写 |
| `paper_index.py` | 论文指纹、增量比对、与项目关联、差异分类 |
| `subscription_state.py` / `subscription_service.py` | 订阅持久化、调度、执行、结果落库 |
| `content_eval.py` | 六项指标计算（被脚本与测试共用） |

复用与升级：`draft_state.py`（块新增 `claims`）、`draft_export.py`（改用 `citation_styles` 渲染标记与条目）、
`draft_audit.py`（接入矛盾与三档判定）、`grounding.py`（二值 → 三档 + 理由 + 反例）、
`draft_semantic.py`（与 `grounding` 共用判定核心，避免两套口径）。

## 5. 数据模型

### 5.1 草稿块扩展（向后兼容）

```json
{
  "id": "blk-003", "type": "paragraph", "text": "……[3]",
  "evidence_ids": ["pev-…"], "citation_keys": ["qin2024"],
  "status": "checked",
  "claims": [
    {"id": "clm-001", "text": "……降低 38% 功耗", "citation_keys": ["qin2024"],
     "evidence_ids": ["pev-…"],
     "stance": "supported", "reason": "原文同时给出 38% 与测试条件",
     "counter_quote": "", "checked_at": "2026-10-22 10:00:00"}
  ]
}
```

- `claims` 缺省为 `[]`；读取旧草稿时补默认值，不升 `draft.version`（保持 `1`）。
- `stance` 只由核查写入，`write_section` 不写；用户可直接编辑 `text`，编辑后该块 `stance` 置 `stale`。

### 5.2 `data/projects/<id>/manuscript.json`

```json
{
  "version": 1,
  "project_id": "prj-…",
  "title": "……", "subtitle": "",
  "authors": [{"name": "……", "affiliation": "……", "email": ""}],
  "abstract": "……", "keywords": ["……"], "funding": "……",
  "citation_style": "gb7714", "language": "zh",
  "updated_at": "……"
}
```

- 上限：标题 200 字、摘要 2000 字、作者 20 人、关键词 12 个、基金 300 字。
- `citation_style ∈ {gb7714, apa, ieee, numeric, author-year}`；后两个为兼容 v2 的别名。

### 5.3 `data/projects/<id>/contradictions.json`

```json
{
  "version": 1, "project_id": "prj-…", "generated_at": "……",
  "items": [{
    "id": "ctr-001", "kind": "metric_conflict",
    "metric": "功耗（mW）", "paper_a": "2609.24288v1", "paper_b": "2211.10438",
    "quote_a": "……", "quote_b": "……", "locator_a": "Results · p.4", "locator_b": "Results · p.6",
    "why": "指标同名但测试条件不同（55nm 仿真 vs 28nm 流片）",
    "severity": "high", "draft_blocks": ["blk-003"], "status": "open"
  }]
}
```

### 5.4 `data/subscriptions.json`

```json
{
  "version": 1,
  "items": [{
    "id": "sub-1a2b3c4d5e", "label": "量化推理跟踪",
    "query": "efficient LLM inference quantization", "source": "arxiv",
    "limit": 20, "interval_hours": 168, "enabled": true,
    "project_id": "prj-…",
    "last_run_at": "2026-10-22 09:00:00", "next_run_at": "2026-10-29 09:00:00",
    "seen_fingerprints": ["doi:10.…", "arxiv:2501.01234"],
    "last_result": {"new": 5, "conflict": 2, "method": 2, "irrelevant": 1}
  }]
}
```

- 单文件上限 50 条订阅；`seen_fingerprints` 每条订阅最多 500 个（超出按时间淘汰最旧）。
- 写入持 `project_state.project_lock()`（与应用内其他 JSON 存储同一把锁）。

### 5.5 `data/index.json`

```json
{
  "version": 1,
  "papers": {
    "2609.24288v1": {
      "fingerprint": "arxiv:2609.24288v1", "content_hash": "sha1:…",
      "title": "……", "year": "2026", "source": "arxiv",
      "first_seen": "2026-10-22 09:00:00", "last_seen": "2026-10-22 09:00:00",
      "projects": ["prj-…"], "subscriptions": ["sub-…"]
    }
  },
  "updated_at": "……"
}
```

- 指纹优先级：`doi:` → `arxiv:`（从 `url`/`id` 解析）→ `title:` + 年份的 sha1。
- 索引不存全文，只存指纹与定位；全文仍在既有 `data/parsed/` 与 `search` 缓存中。
- 上限 5000 篇，超出按 `last_seen` 淘汰未关联项目的条目。

### 5.6 金标集 `data/eval/golden/`

```json
{
  "version": 1, "topic": "llm-inference-quantization",
  "papers": [{"id": "2205.14135", "title": "……", "source": "arxiv"}],
  "claims": [{
    "id": "g-001", "paper_id": "2205.14135",
    "claim": "GPTQ 在 175B 模型上把精度损失控制在 1% 以内",
    "evidence_quote": "……", "locator": "Table 1 / p.6",
    "label": "supported", "annotator": "A", "note": ""
  }],
  "disagreements": [{"claim_id": "g-017", "labels": ["partial", "unsupported"], "note": "……"}]
}
```

- 只存短摘句（≤200 字符）与定位，不存全文；语料本身由 `scripts/eval_content.py` 拉取或复用 `.bench/real_papers`。
- 4 个主题各一个文件，合计 ≥200 条 claim；分歧单独记录，指标计算时按多数标签、分歧计入报告。

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/api/projects/{id}/manuscript` | 稿件元数据；PUT 校验字段与长度 |
| GET | `/api/citation-styles` | 可用样式与各自必填字段说明 |
| POST | `/api/projects/{id}/draft/references/preview` | `{style}`；返回文内标记样例、参考文献条目、完整度问题 |
| GET | `/api/projects/{id}/draft/export` | 既有，`format` 新增 `docx`；`style` 可选覆盖 |
| POST | `/api/projects/{id}/draft/preflight` | 投稿前检查：字数、图表号、引用完整性、未支撑句、公式计数 |
| GET | `/api/projects/{id}/contradictions` | 读取矛盾记录 |
| POST | `/api/projects/{id}/contradictions/scan` | 重新检测（可指定 paper_ids） |
| GET/POST | `/api/subscriptions` | 列表 / 新建 |
| PATCH/DELETE | `/api/subscriptions/{id}` | 启停、改间隔 / 删除 |
| POST | `/api/subscriptions/{id}/run` | 立即执行一次，返回差异分类结果 |
| GET | `/api/index/status` | 索引规模、最近同步、按项目关联计数 |
| POST | `/api/index/sync` | 手动同步（接受 paper_ids 或走订阅） |

错误语义：项目/订阅不存在 404；元数据超长或样式未知 422；DOCX 生成失败 500 并给出可读原因；
LaTeX/PDF 工具链缺失时返回 409 并提示改用 DOCX 或安装工具链（不假装成功）。

## 7. 离线算法

### 7.1 三档支持判定（`grounding.classify_claim`）

输入：claim 文本、其绑定证据的原文摘句。输出：`stance / reason / counter_quote / signals`。
规则**按下列顺序**求值（顺序本身是规格的一部分，早先版本把"缺数字"放在重合度之前，
导致跨论文句子被判成 partial，已修正）：

1. **无证据** → `unsupported`，理由「未绑定证据」。
2. **重合度过低**（`overlap < CLAIM_PARTIAL_OVERLAP`，默认 0.20）→ `unsupported`。
   证据讲的是另一件事，不应因为主张里恰好有证据没有的数字而被"救"成 partial。
3. **主张明显长于证据**（`claim_tokens / evidence_tokens > CLAIM_LENGTH_RATIO`，默认 1.35）
   → `partial`，理由「证据只能覆盖其中一部分」。即使共享词全部命中，断言范围已超出证据。
4. **数字缺失**（claim 的数字不在证据中）→ `partial`，理由列出缺失数字。
5. **重合度足够**（`overlap ≥ CLAIM_SUPPORT_OVERLAP`，默认 0.45）→ `supported`。
6. 其余（介于两个阈值之间）→ `partial`。
7. **极性冲突**：claim 与证据的否定极性相反（中英文否定词均识别）→ 降一档，
   `counter_quote` 取证据中最不相关的一句。
8. **方向冲突**：claim 与证据的数值方向相反（提升/降低，中英双语）→ `unsupported` + `counter_quote`。

阈值与词表进 `config`，可被调用方覆盖（草稿块级核查按块粒度传入自己的阈值，
但分词、重合、否定、方向原语与 claim 级共用同一实现，避免两套口径）。
判定无随机项，同一输入必须给出相同结果。

### 7.2 矛盾检测（`contradictions`）

1. 候选对：同一项目内、被引用或精读过的论文两两组合（上限 40 篇 → 最多 780 对，实际按指标索引裁剪）。
2. 指标归一：把 `results` 与 `metrics` 的键归一（大小写、复数、中英同义词表），得到可比指标。
3. 只对**同名指标**比较；抽取各自数值与方向词。
4. 判为矛盾的条件（满足其一）：
   - 数值方向相反（一升一降）；
   - 同一结论的极性相反且指标同名；
   - 同一方法在 A 中被报告为有效、在 B 中被报告为失效（含 not / fail / degrade 等）。
5. 生成 `why`：写明「指标同名但测试条件/口径差异」，并从两篇的局限句中取证据；能判断口径不同时降级为 `medium`。
6. `draft_blocks`：把引用了 A 或 B 的草稿块关联进来，供核查面板定位。
7. 结果写入 `contradictions.json`；不自动改草稿。

### 7.3 引用样式引擎（`citation_styles`）

| 样式 | 文内 | 参考文献要点 |
|---|---|---|
| `gb7714` | `[1]` 上标位（Markdown 用 `[1]`） | `[1] 作者. 题名[J]. 刊名, 年, 卷(期): 页码.`；文献类型标识 `[J]/[C]/[M]/[EB/OL]` |
| `apa` | `(Qin et al., 2024)` | `Qin, Y., et al. (2024). Title. Venue, vol(issue), pages.` |
| `ieee` | `[1]` | `[1] Y. Qin et al., "Title," Venue, vol. x, no. y, pp. a–b, 2024.` |
| `numeric` / `author-year` | 兼容 v2 行为 | 与既有实现保持一致，便于旧草稿 |

- 排序：`gb7714`/`ieee` 按正文首次出现顺序；`apa` 按作者姓氏字母序，文内用作者-年。
- 字段完整度按样式分级：缺卷期页码为 `warn`（不阻塞），缺作者/题名/年份为 `error`（阻塞导出并提示）。
- 一致性校验：文内键集合与 `draft.citations` 键集合的双向差集、重复键、编号跳号。
- 所有格式化函数是纯函数，输入 `(citations, style)` 输出字符串，便于逐条测试。

### 7.4 DOCX 导出（`docx_export`）

内容模型（与 Markdown/LaTeX 共用）：`title / authors / abstract / keywords / sections[{heading, blocks[]}] / references / figures[] / tables[]`。

- 用 `python-docx` 生成；内置模板定义标题 1–3、正文、图题、表题、参考文献样式。
- 图表编号：草稿没有独立的 figure/table 块类型，因此**按题注文本识别**——以「图 N / 表 N / Figure N / Table N」
  开头的段落视为题注，按出现顺序校验编号连续性；正文中的「见图 X / 见表 X」做交叉引用存在性校验。
  本版不新增块类型，避免动编辑器与既有草稿。
- 页眉（短标题）与页脚（页码）；不生成目录页码（只留目录占位与提示）。
- 公式：保留 LaTeX 源码文本，导出后在 `preflight` 报告中提示公式数量与建议（OMML 不在本版）。
- 不修改 `draft.json`；导出为只读操作。

### 7.5 投稿前检查（`manuscript` + `draft_audit`）

汇总为一份可定位清单：字数（正文/摘要）、图表编号连续性、交叉引用有效性、引用完整性（按样式）、
未绑定证据的正文块、`unsupported` 主张、`high` 级词法问题、跨论文矛盾、公式计数。
每项含 `level ∈ {info, warn, error}` 与 `target {kind, id}`，前端点击定位。

### 7.6 订阅与增量（`subscription_service` / `paper_index`）

1. 调度：单线程轮询（默认 60 秒一次），只处理 `enabled && next_run_at <= now` 的订阅；
   每次最多执行 3 条，避免长任务阻塞；执行持订阅级锁。
2. 执行：调用既有 `search.search(query, limit, source)`；失败按 `_is_transient_failure` 重试一次，
   仍失败则记录 `last_error` 并把 `next_run_at` 推后（退避 1×/2×/4×，最多 24 小时）。
3. 去重：计算指纹；已在 `seen_fingerprints` 的直接跳过。
4. 关联：对每篇新论文，按标题/摘要与项目记忆的实词重合、指标与数据集命中，分类为：
   - `conflict`：与项目内某条证据在某指标上判定矛盾（复用 §7.2 判定，单对比较）；
   - `method`：方法/数据集高度重合但结论不冲突；
   - `support`：指标方向一致且结论相近；
   - `irrelevant`：三项都不命中。
5. 落库：写 `index.json`、`subscriptions.json` 的 `last_result`；`conflict` 项追加进 `contradictions.json`。
6. 不自动抓全文、不自动跑 Agent 运行；用户确认后再进项目（与 Copilot 建议同一原则）。

## 8. 在线算法（真实模型治理）

1. **三档复核**：规则判定后，若配置模型且预算允许，把 claim + 摘句发给模型，要求返回
   `{stance, reason, counter_quote}`；`counter_quote` 必须是摘句原文子串，否则丢弃并保留规则结果。
2. **矛盾复核**：模型可对候选对给出 `{is_conflict, why, severity}`；不改变规则已定的 `paper_a/b` 与指标。
3. **接受门槛**：规则与模型不一致时以规则为准并标记 `model_disagreed=true`，写入评测用于校准模型。
4. **一致率指标**：同一金标集上，在线路径与离线路径的 `stance` 一致率 ≥0.85；降级率 ≤20%。
5. **成本**：全部模型调用经 `llm_gateway`（预算、缓存、审计）；`eval_content.py` 默认离线，
   传 `--online` 才启用模型，并在报告中单列在线指标与调用成本。

## 9. 关键机制与迁移

- **版本迁移**：`draft.json` 不升 version（`claims` 为可选新增）；`manuscript.json` / `contradictions.json` /
  `subscriptions.json` / `index.json` 均为新文件，缺失视为空。
- **并发**：所有 JSON 写入持同一把 `project_state.project_lock()`；订阅执行与草稿保存不会互相覆盖。
- **性能预算**：矛盾检测 ≤1.5 s（40 篇）；DOCX 生成 ≤2 s（100 块）；增量同步单订阅 ≤8 s（20 篇，命中缓存时更快）。
- **失败可见**：导出、订阅、矛盾检测的失败都要有可读原因与定位，不静默吞掉。

## 10. 前端

- 主导航新增「跟踪」工作区（`static/tracking.js`）：订阅列表、上次结果、立即执行、新建/启停/删除。
- 草稿工作台新增「稿件」面板（`static/manuscript.js`）：元数据表单、样式选择、参考文献预览、
  投稿前检查清单；导出按钮统一到该面板。
- 核查侧栏升级：三档判定（含理由与反例摘句）、跨论文矛盾（可跳转两侧论文与草稿块）。
- 视觉与交互沿用 v2–v4：`data-*` 契约、390px 单列、`overflow-wrap:anywhere`、无构建原生 JS。

## 11. 验收

**功能**

- 三种样式在同一草稿上的文内标记与参考文献条目与手工样例逐条一致；字段缺失分级正确。
- DOCX 在 Word 与 WPS 打开无修复提示；图表编号连续、页脚页码正确、参考文献表与文内一致。
- 投稿前检查能定位到块/条目，且错误级问题会阻塞导出并说明原因。
- 三档判定在 40 条构造用例上标签正确；否定与方向冲突能给出 `counter_quote`。
- 矛盾检测在构造用例上召回 ≥0.8、误报 ≤0.15；真实论文上无 ADC/CIM 预设词泄漏。
- 订阅执行产生正确分类；重复执行不重复入库；失败可见并可重试。
- 索引指纹去重生效：同一论文不同来源（DOI 与 arXiv）合并为一条。

**量化门禁**（`scripts/eval_content.py`）

| 指标 | 阈值 |
|---|---|
| 引用精度 | ≥ 0.90 |
| 主张支持率（supported 占比） | ≥ 0.75 |
| 未支撑句率 | ≤ 5% |
| 数值一致率 | ≥ 0.95 |
| 证据覆盖率（正文块） | ≥ 0.80 |
| 矛盾召回 / 误报 | ≥ 0.80 / ≤ 0.15 |
| 在线-离线一致率（`--online`） | ≥ 0.85 |

**回归与体例**

- 全量测试 **≥400**（当前 350）；主回归 41/41 不降；Copilot 13/13 不降；验收脚本 ≥56 项不降（需先补入库，见 §13）。
- 浏览器矩阵 Edge/Firefox/WebKit：0 console error；390px 横向溢出 0。
- 打包：包内自测通过、零密钥、`python-docx` 只作为运行依赖出现在 requirements。
- 三端：5.0.0 上线，线上真机跑通「项目 → 运行 → 草稿 → 稿件设置 → 导出 DOCX → 投稿前检查」。

## 12. 交付

- 版本 5.0.0：`APP_VERSION`、`/api/health`、`/api/meta`（新增 `manuscript / docx / subscriptions / contradictions`
  特性位）、静态资源与页脚。
- 依赖：`paperpilot/requirements.txt` 增加 `python-docx>=1.1,<2`；ModelScope 镜像与 Dockerfile 同步。
- 文档：本规格、实施计划、README、CHANGELOG、交付说明、说明文档、评测方案、真机能力报告、发布检查清单。
- 截图：跟踪工作区、稿件设置、参考文献预览、投稿前检查、DOCX 成品页、矛盾卡片。
- 展示页与 ModelScope README 同步；演示视频不动。

## 13. 风险

| 风险 | 缓解 |
|---|---|
| 三块能力同版，范围过大 | 分三次发布（§14）；每里程碑独立测试与可运行 |
| 金标集标注主观、样本小 | 双次独立标注 + 分歧记录；只作趋势指标，不作绝对真值 |
| DOCX 保真（公式/图表/参考文献） | 单一内容模型 + 固定模板；公式明确不在本版排版；先支持课程论文级 |
| 中文引用样式细节多 | 以 GB/T 7714-2015 为唯一权威；按文献类型写用例；缺字段分级而非静默 |
| 真实模型成本与不稳定 | 网关预算硬上限 + 缓存；默认离线评测；仅金标集与演示路径用真实模型 |
| 订阅调度影响稳定性 | 单进程线程 + 持久化游标 + 退避重试；每轮最多 3 条；不自动跑 Agent |
| 矛盾检测误报 | 只对同名指标比较；口径不同降级；展示双方原文，保留人工判断 |
| **验收脚本未入库** | v5 起把 `out/task15-qa/acceptance.py` 迁到 `paperpilot/scripts/acceptance.py` 并纳入 CI 口径，避免关键门禁只存在于本地 |
| 本地与远端版本漂移 | 每次迭代先 `git`/tarball 同步并以 350/41/13 复跑确认基线（本版已实测） |

## 14. 发布节奏

| 发布 | 内容 | 退出条件 |
|---|---|---|
| **v5.0.0** | M1 质量基线 + M2 证据语义 + M3 可交付稿 | 六项指标首次达标、DOCX 三样式导出可用、≥400 测试 |
| **v5.1.0** | M4 持续研究 | 订阅/增量/差异定位可用，索引去重与失败重试验证通过 |
| **v5.2.0** | M5 真实模型治理 | 在线一致率与降级率达标，成本报表可复现 |

三次发布共用同一套门禁；每次发布都更新版本号、文档、截图与三端线上。
