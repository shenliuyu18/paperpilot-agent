# PaperPilot v7.2 设计：本地扫描件 OCR

日期：2026-10-23

## 目标

7.1 对无文字 PDF 只返回“请先 OCR”。本版让扫描件可以在本机完成 OCR，并继续走既有的章节/证据/检索/问答/Agent 链路；OCR 仍是可选项，不把云端服务变成依赖。

## 设计

- 新模块 `app/services/ocr.py`：`enabled()`、`status()`、`extract_pages(doc)`。
- 触发：`pdf.parse_pdf_bytes` 的文本层少于 80 字符，且 OCR 可用。
- 渲染：PyMuPDF 以 200 dpi 渲染前 N 页（默认 12）；临时 PNG 在 `TemporaryDirectory` 中，结束即删除。
- 识别：调用系统 `tesseract` CLI，默认 `eng+chi_sim`，单页 45 秒超时；语言包不可用时退回 `eng`。
- 结果：替换页面文本与全文，重新走 `_split_sections`、标题/作者/图表/结果/局限抽取；`ocr_used`、`ocr_pages`、`extraction_level` 写回论文与上传响应。
- 降级：OCR 关闭/未安装时返回 400，提示上传可搜索 PDF 或先 OCR；OCR 无可识别文字同样 400，不产生 `Untitled paper`。
- 状态：`/api/meta` 暴露 `ocr` 状态；特性位 49 → 50。
- 镜像：`paperpilot/Dockerfile` 与 `modelscope_space/Dockerfile` 安装 `tesseract-ocr`、`tesseract-ocr-eng`、`tesseract-ocr-chi-sim`。
- UI：上传成功后在结果栏与 toast 显示 OCR 页数。

## 验证

- `tests/test_ocr.py` 5/5（合成图像型 PDF、上传响应、meta、关闭 OCR 降级）。
- 全量 620/620；验收 94/94；离线一致性 42/42；档案 10/10；离线 PDF 24/24；真实模型 PDF 12/12。
- 浏览器上传流程仍需人工/自动化确认 OCR 提示；本地测试覆盖 API 与解析层。

## 边界

- 只 OCR 前 12 页；25 MB / 80 页总上限不变。
- 识别质量取决于扫描清晰度、版式与语言；复杂表格、公式、手写体未评估。
- 不上传云端；不保存 OCR 图像；不替代人工校对。