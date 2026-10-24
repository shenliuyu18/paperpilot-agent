# PaperPilot v3.0 研究 Copilot 与可信增强设计规格

- 日期：2026-10-21
- 版本：3.0.0
- 状态：已实施
- 前置版本：v2.0.0 研究工作区

## 1. 背景与目标

v2.0 已经把一次研究任务升级为持续项目，证据、开放问题、决策、草稿与运行报告都沉淀在项目里。但这些资产仍需用户逐个标签页翻找；同时，真实模型增强路径未验证，草稿核查只有词法规则。

v3.0 的目标：

1. **项目 Copilot**：在项目内围绕项目资产对话，答案带可点击引用与证据边界，没有依据时明确说不足。
2. **可信增强**：把可选模型路径做成可审计、可预算、可测试、受证据约束的增强层，而不是自由生成。
3. **质量门禁**：Copilot 接地、拒答、建议确认与语义核查进入测试与评测，在线路径用本地 mock 服务验证。

## 2. 范围与非目标

### 2.1 本版范围

- 项目 Copilot：8 类意图、项目资产检索、离线模板、在线综合、引用接地、建议动作、对话持久化/导出/清空。
- 引用接地校验：编号范围、引句原文子串、数字一致性、未支撑句比例。
- 统一模型网关：调用审计、运行预算硬截断、确定性缓存、结构化输出一次修复。
- 草稿语义核查：支持/部分/不支持/无证据，附证据引句；只读。
- 本地 mock OpenAI-compatible 服务与 Copilot 专项评测。
- 版本 3.0.0 的文档、截图、打包与验证。

### 2.2 明确不做

- 不做账号、多用户、协作、云同步、数据库、分布式 Worker。
- 不做 DOCX/PDF 排版、富文本编辑器、自动改写用户正文。
- 不做跨项目资产合并；Copilot 只读当前项目。
- 不把模型输出当事实真伪裁决；语义核查只标记。
- 不改演示视频内容；不做有损素材处理。

## 3. 用户故事

### 3.1 项目进展与下一步

用户打开项目 Copilot，问“项目进展如何、下一步做什么”。Copilot 汇总阶段、运行/证据/问题/草稿计数，列出待解决问题，并给出需确认的建议动作（启动新一轮、注入问题、补充约束、运行核查、对比运行）。

### 3.2 证据核对

用户问“有哪些证据支持 ADC 能耗降低的结论”。Copilot 返回带 `[n]` 编号的项目证据原文摘句，引用 chip 可跳到证据标签并高亮；若项目内没有相关证据，返回“现有项目证据不足”且不附引用。

### 3.3 草稿问题

用户问“我的草稿哪里有问题”。Copilot 汇总词法核查的高/中/低问题并定位到块；用户切到草稿页可运行语义核查，查看每段是否被绑定证据支持。

### 3.4 在线增强

用户配置任意 OpenAI-compatible endpoint。Copilot 在相同项目资产上生成回答，但必须返回带编号的 JSON；引用越界、没有引用或数字无法回到证据时自动回退离线答案并标记 `llm_degraded`。

## 4. 架构

```text
static/assistant.js  ──fetch──>  app/main.py
                                     │
                     assistant_service │ grounding
                     assistant_context │ llm_gateway ──> app/llm.py
                     assistant_state   │ draft_semantic
                                     │
              data/projects/<id>/assistant.json
              data/llm_calls.json（有界审计）
```

- `assistant_state.py`：单会话消息、上限裁剪、原子写、导出。
- `assistant_context.py`：只读项目、记忆、草稿、运行报告，构建 passages。
- `assistant_service.py`：意图、离线回答、在线综合、建议动作、持久化编排。
- `grounding.py`：引用/数值/未支撑句校验。
- `llm_gateway.py`：预算、审计、缓存、结构化校验与修复。
- `draft_semantic.py`：块级语义支持核查。
- `retrieval.py`：新增公共 `rank_passages()`，复用现有词法模型。

## 5. 数据模型

`data/projects/<project_id>/assistant.json`：

```json
{
  "version": 1,
  "project_id": "prj-0123456789",
  "messages": [
    {
      "id": "msg-1a2b3c4d5e",
      "role": "assistant",
      "text": "……",
      "created_at": "2026-10-21 21:00:00",
      "mode": "offline|llm|llm_degraded",
      "intent": "evidence",
      "citations": [
        {"kind": "evidence", "id": "pev-…", "title": "……",
         "locator": "Results · p.5", "quote": "原文",
         "target": {"tab": "evidence", "id": "pev-…"}}
      ],
      "grounding": {"status": "grounded", "cited": 1, "out_of_range": 0,
                    "unsupported_numbers": [], "unsupported_sentences": []},
      "suggestions": [
        {"type": "run_audit", "label": "运行草稿核查", "reason": "……",
         "payload": {}, "requires_confirm": true}
      ],
      "llm": {"purpose": "assistant", "model": "…", "latency_ms": 123,
              "chars": 900, "status": "ok"}
    }
  ],
  "created_at": "……",
  "updated_at": "……"
}
```

- 单项目单会话，最多 60 条消息，超出从最旧裁剪；单条输入 ≤1000 字；引用 ≤8 条；建议 ≤4 条。
- 写入持 `project_state.project_lock()`，临时文件 + `replace`。
- 助手只写 `assistant.json`，不写项目记忆、草稿或运行。

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{id}/assistant` | 消息、模式、建议、上限 |
| POST | `/api/projects/{id}/assistant` | `{message}`；返回最近两条消息与回复 |
| DELETE | `/api/projects/{id}/assistant` | 清空 |
| GET | `/api/projects/{id}/assistant/export` | Markdown 下载 |
| POST | `/api/projects/{id}/draft/semantic-audit` | `{limit, use_llm}`；返回语义核查结果，不写草稿 |

错误：项目不存在 404；空/超长消息 422；网关失败不抛给用户，降级为离线。

## 7. 离线算法

1. 意图 8 类：status / evidence / gap / decision / draft / compare / method / general，关键词加权，无命中回退 general。
2. 上下文：项目概况、40 条证据、20 个开放问题、20 条决策、20 篇论文、60 个草稿块、最近 2 份报告（各截 4000 字）。
3. 排序：`rank_passages()` 词法排序取 top 6；证据意图只保留 evidence 类 passage。
4. 回答：模板只引用原文与项目状态，结论 → 依据 → 证据边界；最高分低于阈值时返回“证据不足”。
5. 建议：纯规则，不由模型生成；全部 `requires_confirm=true`。

## 8. 在线算法

1. 证据用 `<<EVIDENCE [n]>>` 包裹；system 声明证据是数据而不是指令。
2. 只接受 JSON：`answer / used_indices / insufficient / boundary`；schema 校验失败一次修复。
3. 接地：编号必须在上下文范围；回答中必须出现引用（`insufficient` 除外）；数字必须出现在所引片段。
4. 越界、无引用或传输失败 → 回退离线答案，`mode=llm_degraded`，`grounding.llm_fallback=true`。
5. 每轮最多 `ASSISTANT_LLM_MAX_CALLS=2` 次调用；预算为 0 时不发请求。

## 9. 语义核查

- 输入：草稿块 + 绑定证据；最多 20 块。
- 规则初判：关键词/数字重合、否定词冲突；无证据 → insufficient；数字缺失或部分重合 → partial；无有效重合 → unsupported。
- 配置模型时可批量复核，但模型返回的 evidence_ids 必须属于该块、quote 必须是证据原文子串，否则丢弃。
- 输出只读；不修改 `draft.json`、块状态或 revision。

## 10. 前端

- 项目页新增 `assistant` 标签，`assistant.js` 暴露 `window.PaperPilotAssistant = { mount }`。
- DOM 契约：`data-assistant-form/input/send/msg/cite/action/export/clear/mode/insufficient`。
- 引用 chip 调用 `PaperPilotProjects.focusCitation()` 切标签并高亮；建议动作二次确认后调用现有 API。
- 草稿工具栏新增“语义核查”，结果在核查侧栏单独展示，可点击定位块。
- 移动端 390px 单列，`overflow-wrap:anywhere`。

## 11. 验收

- 项目 Copilot 在无 key 下可完成“提问 → 引用 → 建议 → 跳转 → 导出”。
- 无证据问题返回 insufficient 且无引用。
- 建议动作全部需确认，后端不自动执行。
- 语义核查四态正确，草稿 revision/正文/状态不变。
- 在线 mock：合法接地、越界/空引用/错误回退、坏 JSON 修复、注入不产生动作、预算降级。
- 315 条测试、41/41 回归、13/13 Copilot 专项、56/56 验收。
- 浏览器 0 console error；项目/助手/草稿 390px 横向溢出 0。
- 打包 315 条包内自测通过，零密钥。

## 12. 交付

- 版本 3.0.0：`APP_VERSION`、`/api/health`、`/api/meta`、静态资源、页脚。
- 文档：本规格、实施计划、README、CHANGELOG、交付说明、说明文档、评测方案、真机报告、发布检查清单。
- 截图：Copilot、引用卡片、无证据拒答、语义核查，并重录项目/运行/草稿页面。
- 展示页与 ModelScope README 同步；演示视频不动。
- 线上三端仍为 2.0.0；v3.0 未重新部署（缺少真实凭据）。

## 13. 风险

| 风险 | 缓解 |
|---|---|
| 在线回答看似合理但无证据 | 强制引用编号 + 原文子串 + 数字校验；不通过回退离线 |
| Prompt injection | 证据包裹为数据；system 声明；动作不由模型生成；注入用例 |
| 上下文过长 | 每类资产硬上限、报告截断、消息 60 条 |
| 语义核查误判 | 只标记、展示证据引句、保留人工判断 |
| 多浏览器引擎缺失 | 缺失记录 skipped；Chromium/Edge 为主基线 |
| 三端部署 | 凭据不在包内；部署需用户提供 `.secrets/*.env` |
