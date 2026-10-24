# PaperPilot v6.0 设计规格：投稿包与审稿闭环

- 日期：2026-10-22
- 版本：6.0.0
- 状态：已实施
- 前置版本：v5.2.0

## 1. 背景

v5 已经能把项目做成一份可交付稿（DOCX/PDF、引用样式、投稿前检查）。剩下的一段路是“把它投出去、被审、再回应”：

- 不同投稿档案对标题、摘要、关键词、亮点、盲审有不同要求，用户要自己对照；
- 双盲投稿需要一份不含作者信息的正文，同时保留单独的标题页；
- 投稿信、亮点、回复信通常从零写；
- 参考文献在 Zotero/EndNote 与项目之间来回搬；
- 作者想知道“如果我是审稿人，会先挑哪些问题”，但现有检查分散在核查、preflight、矛盾记录里。

v6.0 把这些拼成闭环，同时明确边界：**档案是参考要求，不是期刊官方模板；审稿视角是规则自查，不是同行评审。**

## 2. 范围

### 2.1 本版范围

- 投稿档案：课程论文、通用期刊、IEEE 会议、双盲期刊；各自标题/摘要/关键词/亮点上限与引用样式。
- 投稿检查：在现有 `preflight` 上叠加档案规则，返回分级清单、亮点与是否阻断。
- 投稿包 ZIP：正文 DOCX、PDF（有 TeX 时）、投稿信、亮点、标题页、检查清单、RIS、BibTeX、回复模板、README。
- 双盲渲染：DOCX/PDF 去除作者、单位、基金；标题页单独保留作者信息。
- 审稿视角：规则报告（优点、主要/次要问题、想问作者的问题、推荐等级），只读。
- 回复模板：逐条自查表 + 审稿人意见占位表。
- 参考文献互操作：RIS / EndNote XML 导出与导入；按 DOI/标题去重，遵守 draft revision 乐观锁。
- 前端“投稿包”抽屉：档案选择、检查、打包、审稿视角、回复模板、文献导入/导出。

### 2.2 明确不做

- 不冒充期刊官方模板；不校验期刊内部投稿系统字段。
- 不做真正的同行评审、不预测编辑决定、不生成审稿意见代替人。
- 不新增 ORCID/CRediT/伦理声明的专门表单；标题页会读取已有 ORCID（如有）。
- 不把生成的投稿信/回复信当作可直接发送的最终文本；界面与文件都标注“模板”。
- 不做多用户协作、投稿系统 API 对接。

## 3. 投稿档案

| id | 标签 | 标题上限 | 摘要词数 | 关键词 | 亮点 | 盲审 | 样式 |
|---|---|---:|---|---|---|---|---|
| `course` | 课程论文 | 200 | 100–500 | 3–8 | 3–5×85 | 否 | gb7714 |
| `generic-journal` | 通用期刊（参考） | 180 | 150–250 | 4–6 | 3–5×85 | 否 | numeric |
| `ieee-conference` | IEEE 会议（参考） | 150 | 150–250 | 3–5 | 不要求 | 否 | ieee |
| `double-blind-journal` | 双盲期刊（参考） | 180 | 150–300 | 3–8 | 3–5×85 | 是 | numeric |

档案只影响检查与导出，不改草稿。错误级问题阻断导出（`blocking=true`）；提醒/提示不阻断。

## 4. 投稿包

ZIP 内容：

| 文件 | 说明 |
|---|---|
| `manuscript.docx` | 按档案样式；双盲时无作者/单位/基金，核心属性作者清空 |
| `manuscript.pdf` | 有 TeX 时生成；失败则写 `PDF-NOTE.txt`，其余文件照常 |
| `cover-letter.md` | 标题、摘要摘录、亮点、原创/未重复投稿声明、通讯作者；标注模板 |
| `highlights.md` | 只取 `stance=supported` 的 claim，按档案上限截断 |
| `title-page.md` | 标题、作者、单位、邮箱、ORCID（如有）、关键词、基金 |
| `checklist.md` | 档案检查结果 |
| `references.ris` / `references.bib` | 参考文献导出 |
| `response-template.md` | 回复信模板 + 自查问题 |
| `README.txt` | 档案、是否盲审、文件清单、免责声明 |

亮点来源只有被证据支持的主张；没有就返回空并给出提示，不用摘要句子冒充亮点。

## 5. 审稿视角

输入：草稿、项目记忆、`preflight`、`draft_audit`、项目矛盾记录。

输出：

```json
{
  "stats": {"paragraphs": 1, "bound_paragraphs": 1, "coverage": 1.0,
            "supported": 1, "partial": 1, "unsupported": 0},
  "strengths": ["1 条主张被所绑证据直接支持。"],
  "major": [{"kind": "...", "message": "...", "target": {...}}],
  "minor": [...],
  "questions": ["..."],
  "recommendation": "major_revision|minor_revision|ready_for_review",
  "recommendation_label": "需要大修（规则自查）",
  "disclaimer": "这是规则自查，不是同行评审……"
}
```

推荐等级只由规则问题数量决定；`major` 非空即 `major_revision`，否则 `minor` 非空为 `minor_revision`，否则 `ready_for_review`。不写任何东西到项目。

## 6. 参考文献互操作

- RIS：`TY/AU/TI/JO|T2/PY/VL/IS/SP/EP/DO/UR/ER`；期刊/会议/图书/网页/通用映射。
- EndNote XML：`record/ref-type/contributors/titles/dates/volume/number/pages/electronic-resource-num/urls`。
- 导入：最多 200 条/次、总计 500 条；无题名记录丢弃；DOI 或规范化标题重复则跳过；revision 不匹配返回 409。
- XML 解析拒绝 `<!DOCTYPE` / `<!ENTITY`，避免实体扩展。

## 7. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/submission/profiles` | 4 个档案 |
| POST | `/api/projects/{id}/submission/check` | `{profile}`，只读检查 |
| GET | `/api/projects/{id}/submission/cover-letter?profile=` | Markdown 模板 |
| GET | `/api/projects/{id}/submission/package?profile=` | ZIP；响应头 `X-PaperPilot-PDF-Included` / `X-PaperPilot-Blinded` |
| POST | `/api/projects/{id}/reviewer/report` | 规则审稿报告 |
| GET | `/api/projects/{id}/reviewer/response-template` | 回复模板 Markdown |
| GET | `/api/projects/{id}/draft/references/export?format=ris|endnote` | 导出 |
| POST | `/api/projects/{id}/draft/references/import` | `{format,text,revision}` |

`/api/meta` 特性位 42 → **45**：`submission`、`reviewer_report`、`reference_io`。

## 8. 前端

- 草稿工具栏新增“投稿包”，打开 `static/submission.js` 抽屉。
- 数据钩子：`data-submission-open/profile/check/package/review/response/import/export/checklist/highlights/cover/review-report/status`。
- 导入文献成功后派发 `paperpilot:draft-changed`，草稿页重新 `mount`。
- 390px 全宽抽屉，正文 `overflow-wrap:anywhere`；服务端数据一律 `esc()`。

## 9. 验收

- 全量测试 **523/523**（v5.2 为 502，新增 21：投稿 21）。
- 端到端验收 **78/78**（新增档案、检查、投稿信、审稿、投稿包、RIS 闭环、特性位）。
- 浏览器：投稿抽屉 4 个档案、检查、ZIP（10 文件含 PDF）、审稿视角、导入 1 条、390px 溢出 0、0 console error、0 失败请求。
- 本机 PDF 在投稿包内；无 TeX 时写 `PDF-NOTE.txt` 而不是失败。

## 10. 风险与边界

| 风险 | 缓解 |
|---|---|
| 用户把“参考要求”当期刊官方规则 | 档案 label/描述/文档都写“参考要求”；不宣称官方 |
| 投稿信含未经核实的原创声明 | 文件头标注模板，发送前人工核对 |
| 审稿视角被当成真实评审 | 报告含 disclaimer，推荐等级写“规则自查” |
| 亮点从摘要臆造 | 只取 supported claims；没有就返回空 |
| 双盲泄漏作者 | DOCX 作者/单位/基金移除、核心属性清空；标题页单独文件 |
| XML 实体攻击 | 拒绝 DTD/实体声明；限制 2 MB / 200 条 |
| 导入覆盖草稿 | revision 乐观锁；重复 DOI/标题跳过 |
