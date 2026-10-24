# PaperPilot v5.2 设计规格：跨进程并发、金标语料与线上 PDF

- 日期：2026-10-22
- 版本：5.2.0
- 状态：已实施
- 前置版本：v5.1.0

## 1. 背景

v5.1 关闭了 PDF、矛盾复核、单进程订阅并发与金标集管线，但交接时仍有三处被明确标为未完成：

1. 订阅锁只覆盖单进程，多 Worker/多实例会重复抓取。
2. 人工金标集的语料只覆盖两个主题，候选池 98 条，达不到 4 主题 × 6 篇 / ≥200 条。
3. 线上 ModelScope 镜像没有 TeX，PDF 返回 503。

v5.2 分别处理这三点。第三点要求线上实际可下载 PDF；第二点只能完成“语料、候选池、
可复核的构造集”，**人工签字仍然必须由人完成**，本版不伪造。

## 2. 跨进程订阅锁

### 2.1 机制

- 每订阅一个锁文件：`$PAPERPILOT_LOCK_DIR/<id>.lock`，默认 `DATA_DIR/locks/`。
- `os.open(O_CREAT|O_EXCL)` 原子抢占；内容为 `{pid, at, token}`。
- 线程锁先拿，文件锁后拿；任一失败返回 `{"skipped": "busy"}`，不抓取、不写游标。
- 释放时校验 token，只删除自己创建的锁；崩溃遗留的锁超过 `LOCK_TTL_SECONDS`（30 分钟）自动回收。
- `running()` 合并线程集合与文件锁；`due()` 跳过运行中订阅；`scheduler_status()["running_ids"]` 可见。
- 边界：文件锁依赖本地文件系统；NFS 等不保证 `O_EXCL` 语义的环境不在支持范围。

### 2.2 测试

- 子进程持锁 → 父进程 `run_once` 返回 busy 且不调用检索。
- 陈旧锁（mtime 超过 TTL）可回收。
- 错误 token 不能删除他人的锁。
- 原有双线程 busy、传输失败后释放锁的测试继续通过。

## 3. 金标语料与候选池

### 3.1 语料抓取

`scripts/fetch_golden_corpus.py` 从 Crossref（`filter=has-abstract:true`）与 Europe PMC
（`resultType=core`）按主题查询，清洗 JATS/HTML 标签，按标题去重，优先选取含数字句较多的摘要，
每个主题 6 篇，写入 `data/eval/golden/corpus/<topic>.json` 与 `all.json`。
语料文件随仓库提交，后续构建不需要联网。

主题查询：LLM 推理量化、检索增强生成、存内计算 ADC、推荐系统。

### 3.2 候选池

`golden_workbench.py build --corpus-dir data/eval/golden/corpus` 直接使用主题语料，
每篇最多 10 句，生成 4 个候选文件，`label` 为空、`provenance: human-required`。
本版实测 **225 条**（55/58/52/60），达到 ≥200 条，且 4 个主题各 6 篇。

### 3.3 多主题构造集

`scripts/build_golden_multitopic.py` 对每篇论文的可分词句（ASCII/CJK，至少 12 个 token）
构造四类探针：逐字 supported、追加从句 partial、改数字 partial（`probe=missing_number`）、
同主题另一篇 unsupported。输出 `data/eval/golden/multitopic-<topic>.json`，
`provenance: constructed-multitopic`。

本版实测 **351 条**（94/96/90/71）。构造规则：

- 追加从句使用多段不同文本直到唯一 token 数超过原句 1.5 倍（`tokens()` 是集合，重复同一句无效）。
- 改数字时避开原句已有的数字，避免“新数字恰好也在引句中”导致漏判。
- 非 ASCII/CJK 句子（如希腊文）不进入构造集，避免分词器不支持造成的假阴性。
- unsupported 只保留判定器确实判 unsupported 的跨论文对；标签仍由构造方式决定。

**诚实边界**：这 351 条仍是构造探针，不是人工标注。人工候选池已满 4×6/225，
但需要人逐条填写 label；`eval_content` 不会把未标注候选算进指标。

## 4. 线上 PDF

### 4.1 镜像

`modelscope_space/Dockerfile` 在 `python:3.12-slim` 上安装：

```dockerfile
texlive-xetex texlive-lang-chinese texlive-latex-recommended texlive-fonts-recommended fonts-noto-cjk
```

并设置 `PAPERPILOT_XELATEX=/usr/bin/xelatex`。选择 `--no-install-recommends` 控制体积；
`latex-recommended` 提供 `geometry`/`hyperref`，`lang-chinese` 提供 `ctex`，`fonts-noto-cjk` 提供 Linux 中文字体（`fontset=ubuntu`）。
LaTeX 模板移除未使用的 `enumitem`；Debian 的 `texlive-lang-chinese` 仍会把 `latex-extra` 作为硬依赖带入，镜像体积相应增加。

### 4.2 构建时踩到的坑

- Debian trixie 没有 `fonts-fandol` 包；改用 `fonts-noto-cjk` + `PAPERPILOT_CTEX_FONTSET=ubuntu`。
- 没有 `texlive-fonts-recommended` 时，`hyperref` 触发 METAFONT 生成 `pzdr`（Zapf Dingbats）失败，
  XeLaTeX 报 `I can't find file 'pzdr'`；补装 `texlive-fonts-recommended` 后通过。
- `texlive-lang-chinese` 会把 `latex-extra` 作为硬依赖带入，镜像体积约增加数百 MB。

### 4.3 运行时

- 工具链存在时 `format=pdf` 返回 200 与 `%PDF`；不存在时仍是 503（本地开发不受影响）。
- 首次请求需要两次 XeLaTeX 编译，已在本地测过约 5 秒；线上冷启动可能更慢，验收超时 180 秒。
- 不以 PDF 阻塞应用启动：`pdf_export` 只在请求时查 `xelatex`。

## 5. API / 特性位

- `/api/meta` 特性位 41 → **42**，新增 `cross_process_lock`。
- 无新增路由。

## 6. 验收

- 全量测试 **502/502**（v5.1 为 494，新增 8：跨进程锁 3、多主题金标 4、ctex fontset 1）。
- 端到端验收 **71/71**（新增金标语料 4×6、候选池 ≥200 且标签为空）。
- `eval_content --strict`：构造集 32 + 硬负例 37 + 多主题 351 = **420 条**，六项指标 1.000。
- 线上 ModelScope：`/api/health` 5.2.0；`format=pdf` 200 且 `%PDF`；DOCX 仍 200。
- 本地无 TeX 时 503 路径不变。

## 7. 非目标

- 不伪造人工标注；不把构造集指标称为人工质量结论。
- 不承诺 NFS/对象存储上的锁语义。
- 不在应用启动时安装或检测 TeX；不在 Docker 之外捆绑 TeX 发行版。
- 不主动安装完整 TeX Live；`latex-extra` 仅作为 `texlive-lang-chinese` 的依赖出现。
