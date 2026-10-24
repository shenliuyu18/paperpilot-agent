# PaperPilot v7.1 设计：本地 PDF 全链路自查

日期：2026-10-23

## 背景

7.0 的研究档案与既有阅读/问答/Agent 功能都假设论文来自演示语料或在线检索。上传本地 PDF 后，部分入口没有把它当成一等来源：搜索来源 `upload` 返回 400，Agent 静默回退到演示论文；损坏/扫描件也能上传成空论文；在线精读绕过审计网关；规则抽取会把准确率和能效混在同一行。本版以“上传 PDF 后每个功能都能正确使用它”为目标。

## 修复

### 来源

- `search.search_upload()` 按标题、摘要和章节文本检索 `store.UPLOAD_PAPERS`。
- `planner.SOURCE_LABELS/SOURCE_NOTES`、搜索下拉、Agent 来源下拉加入 `upload`。
- Agent 的 `_search_with_fallback` 对 `source=upload` 不再回退到 `auto`；没有上传时返回明确的中文失败原因。

### 上传边界

- `pymupdf.open` 的损坏文件异常转为 `ValueError` → 400。
- 全文少于 80 字符视为扫描件/无文字 PDF → 400，并提示 OCR。
- 既有的 25 MB、80 页、非 PDF 415 边界保留。

### 规则抽取

- `_metric_quantities` 按指标保留单位族：能效/能耗/ADC 开销保留 pJ/fJ/TOPS/W/× 等，精度保留 %，面积保留面积/百分比。
- 结果句中“某文献 reports that…/according to…”且带引用编号的句子不作为本文结果。
- 局限性提取忽略图表标题句，移除过宽的 `trade-off` 关键词。

### 在线模型

- 在线精读 `_llm_read` 与翻译 `analysis.translate` 改走 `llm_gateway.audited_chat_json`，审计 purpose 分别为 `read` 与 `translation`。
- 精读 `max_tokens=16384`，避免推理模型把 1800 token 全用在思考上。
- `llm.extract_json` 使用 `strict=False` 接受模型字符串内的原始控制字符。
- 精读 schema 只要求 JSON 对象；`problem/method/tldr` 可为对象或列表，由 `_coerce_text` 归一化；数组字段可超过 6 项，截断到 6 项。
- 上传 PDF 的离线翻译提示不再套用“演示论文有双语对照”。

### 档案

- 档案导入时，从运行快照中恢复 `source=upload` 论文的元数据到上传存储；返回 `restored_uploads`。不恢复 PDF 原件。

## 验证

- 离线 `scripts/pdf_audit.py`：24/24。
- 真实模型 `scripts/live_pdf_check.py`：12/12，7 次调用零降级。
- 615/615 测试、94/94 验收、42/42 离线一致性、10/10 档案审计、41/41 回归、`eval_content --strict` 0。
- 浏览器：上传、读取、本地 PDF 搜索、档案导出/校验/导入；0 console error、0 失败请求、390px 溢出 0。

## 边界

- 不做内置 OCR；扫描件需用户先 OCR。
- 在线精读/翻译的模型输出仍需人工核对原文与数值。
- 档案恢复上传 PDF 的元数据，不恢复 PDF 文件本身。