# PaperPilot v8.0 设计：证据浏览器与词级高亮

日期：2026-10-23

## 目标

7.4 能打开原文页，但只到页级。8.0 把证据引句定位到 PDF 页面上的具体矩形，用高亮让读者直接核对引句与原文；扫描/OCR 页没有文本层时必须明确失败，而不是伪造坐标。

## 设计

- `pdf.locate_quote(paper_id, page, quote)`：PyMuPDF `page.search_for` 在本地 PDF 文本层检索；按整句、句子片段、前缀依次尝试；返回归一化 `x0/y0/x1/y1`（0–1），最多 12 个框。
- API `GET /api/paper/{id}/locate?page=&quote=`：未知论文/无本地 PDF/页码越界 404；未命中 200 + `matched=false` + 说明。
- UI：证据“查看原文页”带 `data-open-quote`；页面查看器在图片上叠加 `.page-highlight`，显示“已定位证据 · N 处”；翻页重新定位；无文本层显示“未定位到文本层”。
- 特性位：`/api/meta` 增加 `pdf_evidence`（52）。
- 安全/边界：只读本地 PDF；不写回 PDF；高亮不持久化；不声称对 OCR/扫描页做词级定位。

## 测试

- 定位坐标归一化；未命中说明；page 0/999/未知论文 404。
- 前端契约：`data-open-quote`、`/locate?page=`、`.page-highlight`。
- 浏览器：上传 PDF → 证据“查看原文页” → 已定位 6 处、6 个高亮框、0 console/request error。

## 边界

- 文本层缺失（扫描件/OCR 页）不能词级高亮，只能查看原图。
- 引句跨行、断词、改写或含特殊字符时可能未命中；页面仍可打开。
- 高亮是临时查看层，不写入 PDF、不进入研究档案。