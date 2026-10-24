# PaperPilot v7.4 设计：原文页预览与证据定位

日期：2026-10-23

## 目标

上传 PDF 已有文本/证据/OCR，但读者无法直接看到原始页面。本版让本地 PDF 的原文页在阅读面板可查看，并让章节/证据的页码可点击跳转，便于用原图核对 OCR 与规则抽取。

## 设计

- `pdf.local_pdf_path(paper_id)`：只接受安全 ID，检查 `data/uploads/<id>.pdf` 与 `data/demo_pdfs/<id>.pdf`。
- `pdf.render_page(paper_id, page, dpi=150)`：PyMuPDF 渲染单页 PNG；缺文件/越界/损坏返回 None。
- API：`GET /api/paper/{id}/pages/{page}` 返回 `image/png`，404 处理未知论文/没有本地 PDF/越界；`/api/paper` 与 `/api/read` 返回 `local_pdf`；`/api/meta` 新增 `pdf_pages` 特性位（51）。
- UI：阅读页“原文页”按钮；章节页码/证据页码旁“查看”；模态查看器有上一页/下一页；加载失败显示明确提示。图片只显示本地 PDF 原页，不叠加 OCR 框。
- 安全：路径白名单 + 页码边界；不暴露文件系统路径；页面不打包进档案。

## 测试

- 上传 PDF：`local_pdf=true`；page 1 是合法 PNG；0/999/未知论文 404。
- 前端契约：按钮、`/pages/`、上一页/下一页、CSS。
- 浏览器：上传 4 页 PDF → p.1/4 → p.2/4，0 console error、0 失败请求。

## 边界

- 只预览本地 PDF；在线检索且没有本地文件的论文不显示入口。
- 页面是图片，不定位到词，不叠加 OCR 文本框或高亮坐标。
- 预览不进入研究档案；人工核对应同时看原图与证据引句。