# PaperPilot v7.3 设计：OCR 多页与截断边界

日期：2026-10-23

## 目标

7.2 的 OCR 默认只看前 12 页，超出部分静默省略。本版把“页数上限”和“是否截断”变成显式、可配置、可观察的行为，同时改善扫描件标题。

## 变更

- `ocr.DEFAULT_MAX_PAGES` 12 → 80，与上传 PDF 的 80 页上限一致；`PAPERPILOT_OCR_MAX_PAGES` 可调低/调高。
- `parse_pdf_bytes` 记录 `ocr_attempted`；`ocr_truncated = attempted < doc.page_count`；论文与上传响应带 `ocr_truncated`、`ocr_warning`。
- 上传 UI：结果栏显示“OCR N 页（其余页未识别）”，并弹出 `ocr_warning`。
- OCR 标题：`_title_from_ocr` 只取第一页前几行，遇空行、作者/机构、Abstract/Keywords/DOI/arXiv 停止；长度上限 220 字符/4 行。
- 测试：3 页图像型 PDF 全量 OCR；`PAPERPILOT_OCR_MAX_PAGES=1` 的截断警告；OCR 专项 6/6。

## 验证

- 621/621 测试、94/94 验收、42/42 一致性、24/24 离线 PDF、12/12 真实模型 PDF、10/10 档案、41/41 回归。

## 边界

- 80 页是页数上限，不保证 80 页扫描件的处理时延；可用 `PAPERPILOT_OCR_MAX_PAGES` 限制。
- 复杂表格、公式、手写体识别质量仍不保证；截断警告只是可见性，不代表已识别全部内容。
- 人工金标集与人工质量评分仍未完成。