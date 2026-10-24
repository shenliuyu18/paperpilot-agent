# PaperPilot v7.0 设计：可复现研究档案

日期：2026-10-23

## 目标

让一个研究项目可以脱离当前实例被保存、审阅、校验和恢复：导出 `.paperpilot.zip`，其中包含项目状态、运行轨迹、证据、草稿、稿件、Copilot 历史与逐文件 SHA-256；任何拿到档案的人都能先校验完整性，再导入为一个新项目继续工作。

不引入外部存储、账号或协作服务；档案完全由本地文件构成。

## 档案格式

```text
paperpilot-dossier-<project-id>.zip
├── manifest.json     格式版本、条目大小/哈希、root_sha256
├── dossier.json      完整结构化数据
└── DOSSIER.md        可读摘要（研究问题、证据账本、运行、草稿、稿件、边界）
```

- `FORMAT = "paperpilot-dossier/1"`；`app_version` 记录导出时的版本。
- `dossier.json` 使用 UTF-8、排序键、紧凑分隔符，便于复核。
- `manifest.json` 的 `entries` 逐条记录 `path / bytes / sha256`；`root_sha256` 是排序后的 `sha256  path` 行的 SHA-256。
- ZIP 使用固定时间戳（1980-01-01）与固定条目顺序，避免无意义的字节漂移。

### 包含

- 项目元数据、阶段、标签、运行 ID 列表、计数。
- 项目记忆：论文题录、证据（原文引句、核对状态、来源动作）、开放问题、决策、reconciled_runs。
- 全部关联运行：步骤、证据、问题、决策、预算、报告、`papers.json` 快照与 `report.md`。
- 草稿当前版本、草稿版本快照（最多 20 个）。
- 稿件元数据与引用样式。
- 项目 Copilot 对话与引用。
- 属于这些运行的模型调用审计（无 prompt、无密钥）。

### 不包含

- PDF 原件、上传文件、解析缓存、其他项目数据。
- 模型 API Key、`llm_settings*.json`、环境变量与任何凭据。
- 全局检索缓存、术语库、订阅、评测语料。

`DOSSIER.md` 开头写明上述边界；结构化完整数据只在 `dossier.json`。

## 校验

`verify_archive(blob)` 只读，不写任何状态：

1. 限制压缩包 ≤ 25 MB、解压总量 ≤ 100 MB、单条目 ≤ 25 MB。
2. ZIP 条目必须是 `manifest.json`、`dossier.json`、`DOSSIER.md` 三个精确路径；拒绝目录、绝对路径、`..`、重复条目与额外文件。
3. 校验 manifest 格式、条目大小、逐文件 SHA-256 与 `root_sha256`。
4. 解析 `dossier.json`，校验项目/运行/草稿/稿件的基本类型与运行 ID 唯一性。
5. 返回项目标题、计数摘要、导出时间、根哈希；非终态运行给出“导入后将冻结”的警告。

任何一步失败抛 `DossierError`，API 返回 422；导入遇到项目数/运行数上限返回 409。

## 导入

`import_archive(blob)` 先执行完整校验，再在项目锁内：

1. 用 `project_state.create_project` 创建新项目；标签追加“档案导入”。
2. 为每个运行生成新 `run_id`，并建立旧→新映射。
3. 重写项目 `run_ids` / `active_run_id`、记忆中的 `origin.run_id`、问题历史、`reconciled_runs`、决策 origin、Copilot 引用的 run target；`project_id` 全部改为新项目 ID。
4. 非终态运行（queued/running/awaiting_approval/paused）改为 `interrupted`，追加“档案导入时冻结”事件；`active_run_id` 置空。
5. 写入运行存储、`papers.json`、`report.md`，并把 `report.path` 指向新运行目录。
6. 写入记忆、草稿与版本快照、稿件、Copilot 历史；重算项目计数。
7. 失败时尽力清理新建项目目录与运行，避免半导入状态。

默认导入为新项目，不覆盖现有项目；同一档案可重复导入，得到不同 ID。

## API

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/projects/{project_id}/dossier` | 导出 ZIP（`application/zip`） |
| `POST` | `/api/dossiers/verify` | 上传 ZIP，只校验并返回摘要 |
| `POST` | `/api/dossiers/import` | 上传 ZIP，校验后导入为新项目 |

`/api/meta` 特性位新增 `dossier`（48 → 49）。

## UI

主导航新增“研究档案”标签：

- 项目选择器（含进行中与已归档项目）。
- “导出档案”按钮：下载 `.paperpilot.zip`。
- “选择档案校验”文件框：显示标题、版本、导出时间、根哈希、运行/证据/草稿计数与警告。
- 校验通过后显示“导入为新项目”；成功后显示新项目 ID 与“打开研究项目”入口。
- 390px 下按钮纵向排列，无横向溢出。

## CLI

`scripts/dossier.py`：

```bash
python scripts/dossier.py export --project-id prj-xxxx --out project.paperpilot.zip
python scripts/dossier.py verify project.paperpilot.zip --json
python scripts/dossier.py import project.paperpilot.zip --json
```

使用与 API 相同的服务函数；`--data-dir` 可指定隔离数据目录。

## 测试

- 导出：三个条目、manifest 哈希、root 哈希、固定时间戳、无密钥/PDF。
- 校验：合法档案通过；篡改 `dossier.json`、改大小、缺失条目、额外条目、zip 路径穿越、重复条目均失败。
- 导入：新项目 ID、运行 ID 全部重映射、证据/问题/决策 origin 一致、草稿版本可恢复、稿件与 Copilot 历史保留、报告路径指向新目录。
- 非终态运行冻结为 `interrupted`；重复导入得到不同项目与运行 ID；超限返回 409。
- API：导出 content-type/zip、verify/import multipart、错误状态码。
- CLI：export → verify → import 闭环。
- 前端契约：标签、脚本、CSS、三个接口字符串；浏览器冒烟检查导出按钮、校验区与 390px。

## 边界

- 档案是项目快照，不包含 PDF 原件；证据引句仍在结构化数据中，但无法仅凭档案重新解析 PDF。
- 导入不会恢复运行中的后台线程；非终态运行会被冻结，需要用户新建运行继续。
- 哈希校验证明“文件未被修改”，不证明研究结论正确；人工金标集与人工质量评分仍是独立事项。
