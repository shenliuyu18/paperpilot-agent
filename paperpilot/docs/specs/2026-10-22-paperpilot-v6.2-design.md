# PaperPilot v6.2 设计规格：自定义大模型设置

- 日期：2026-10-22
- 版本：6.2.0
- 状态：已实施
- 前置版本：v6.1.0

## 1. 背景

v6.1 之前，在线增强模式只能靠环境变量：`PAPERPILOT_LLM_API_KEY` / `_BASE_URL` / `_MODEL` / `_ROUTES`。
对课程用户与评审来说，这意味着要么用演示实例的离线规则模式，要么会改 `.env` 并重启容器。
需求是把选择权交回使用者：在界面上选一个常见大模型、粘贴自己的 API Key，立刻生效。

难点不在表单，而在三件事：密钥不能泄漏、地址不能变成 SSRF 跳板、配置要能覆盖运行时而不打乱既有网关。

## 2. 范围

1. 服务 `app/services/llm_settings.py`：供应商预设、校验、存储、运行时写回、测试连接。
2. API：`GET|POST /api/llm/settings`、`POST /api/llm/settings/test`、`POST /api/llm/settings/clear`。
3. 前端：顶栏常驻入口 + 离线提示条 + 模式徽章文案 + `static/model.js` / `model.css` 抽屉。
4. 元数据：`/api/meta` 暴露 `llm_settings`，特性位 47 → **48**（`custom_llm`）。
5. 部署：`modelscope_space/Dockerfile` 设 `PAPERPILOT_LLM_PERSIST_KEY=0`；打包与 `.gitignore` 排除两份新文件。

不在范围：多用户密钥隔离、密钥托管/加密存储、模型能力探测（上下文长度、是否支持工具调用）。

## 3. 供应商预设

| id | 标签 | base_url | 默认模型 |
|---|---|---|---|
| openai | OpenAI | `https://api.openai.com/v1` | gpt-4o-mini |
| deepseek | DeepSeek | `https://api.deepseek.com/v1` | deepseek-chat |
| moonshot | 月之暗面 Kimi | `https://api.moonshot.cn/v1` | moonshot-v1-32k |
| dashscope | 阿里通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-plus |
| zhipu | 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | glm-4-air |
| custom | 自定义 OpenAI 兼容 | 用户填写 | 用户填写 |

模型名是可自由编辑的输入框 + `datalist` 建议，而不是封闭下拉：供应商会随时上新模型，硬编码清单必然过期。
`custom` 没有默认地址与模型，缺任一项直接报错，避免静默落回 OpenAI 默认值。

## 4. 规则

- **优先级**：界面设置 > 环境变量 > 内置默认。`_from_env()` 每次从 `os.environ` 重新解析，因为
  `config.LLM_*` 会被 `apply()` 改写，回读会拿到自己写进去的值。
- **运行时写回**：`apply()` 把生效值写进 `config.LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_ROUTES /
  LLM_TIMEOUT / LLM_RETRIES / EMBEDDING_MODEL / EMBEDDING_ENABLED / LLM_ENABLED / APP_MODE`。
  `app/llm.py` 与 `llm_gateway` 都是按属性即时读取，因此保存后无需重启即生效；`main.py` 导入时调用一次。
- **存储**：`data/llm_settings.json`（provider/base_url/model/routes/timeout/retries/embeddings/
  embedding_model/updated_at）与 `data/llm_settings.secrets.json`（api_key）分文件；环境变量
  `PAPERPILOT_LLM_SETTINGS_FILE` / `..._SECRETS_FILE` 可覆盖路径，且每次调用解析（与既有 store 一致）。
- **密钥不回显**：任何响应只给 `masked_key = "••••" + 后 4 位`，且仅当本实例落盘密钥时才给掩码；
  `PAPERPILOT_LLM_PERSIST_KEY=0` 时密钥只存进程内存、掩码也不返回（公共实例的默认）。
- **留空即保持**：`api_key` 为空表示不改动已存密钥；`clear_key=true` 才删除；`test` 接受未保存的草稿值，
  试完不落盘。
- **地址安全**：`scheme ∈ {http, https}`、无 userinfo，然后走 `netguard.validate_url`（公网 HTTPS、443、
  非回环/内网/链路本地）。本机网关需 `PAPERPILOT_ALLOW_PRIVATE_LLM=1` 显式放开，错误信息里直接写明这一点。
- **分用途路由**：`routes` 只接受 `agent_report / assistant / draft_write / semantic_audit / contradiction`，
  未知用途 400（宁可报错也不静默丢弃）；`provider` 变化时清空旧路由。
- **限额**：key ≤300 字符且不含空白/控制字符；model ≤120；base_url ≤300；timeout 1–300 秒；retries 0–3；
  测试补全 `max_tokens=32`、超时 20 秒。
- **清除**：删除两份文件 + 清内存密钥 + `apply()` 回到环境变量基线。

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/llm/settings` | `{settings, calls}`；settings 含 providers/purposes/掩码/统计 |
| POST | `/api/llm/settings` | 校验并保存，返回同一结构；400 带中文原因 |
| POST | `/api/llm/settings/test` | 一次最小补全；400 分类提示，502 兜底 |
| POST | `/api/llm/settings/clear` | 回到环境变量配置 |

`/api/health` 的 `mode`、`/api/meta` 的 `llm_enabled/llm_model/llm` 全部来自同一份运行时配置，因此
界面保存后这些端点与徽章会一致变化。

## 6. 前端

- 顶栏 `#modelBtn`（状态点 + 当前模型名）、侧栏 `#modelSideBtn`、离线提示条 `#modelCallout` 三处入口。
- 抽屉：状态胶囊 → 供应商芯片 → 表单（地址/模型/密钥/超时/重试/向量模型）→ 复选项 → 动作 →
  分用途路由表 → 调用统计 → 安全说明。
- 保存/清除后 `window.dispatchEvent(new CustomEvent("paperpilot:llm-changed"))`，`app.js` 重新拉
  `/api/meta` 并刷新徽章、状态点与提示条。
- 密钥输入框是 `type=password` 且**永远不回填 value**；控制台链接仅在 `key_url` 以 `https://` 开头时渲染。

## 7. 验收

- 全量测试 **563/563**（新增 `tests/test_llm_settings.py` 28 条 + 前端契约 3 条）。
- 端到端验收 **90/90**（新增 17.1–17.9，含真实命中本地 OpenAI 兼容桩）。
- 浏览器 `scripts/capture_screenshots_v62.py`：配置 → 测试连接 → 保存启用 → 徽章切换 → 语义核查与
  Copilot 分别命中默认模型与路由模型（桩侧核对 Bearer/路径/model）→ 清除回到离线；
  DOM 检索不到原始密钥；0 console error、0 失败请求、390px 溢出 0。

## 8. 已知边界

- 未使用任何真实供应商 key：端到端证据来自本地桩，各家鉴权细节与模型可用性需使用者自行验证。
- 界面配置只影响当前进程；多 worker 部署需逐进程配置（本项目按单进程设计）。
- 公共创空间为内存模式：重启失效，且同一实例的访问者共享该配置；生产密钥应放在私有实例。
- 路由不校验模型是否存在，错误模型在调用时以 4xx 暴露。

## 9. 6.2.1 修订（2026-10-23，真实密钥实测驱动）

真实模型（ModelScope 推理 API · `ZhipuAI/GLM-5.2`）实测暴露了五个缺陷，全部是链路问题而非外观问题：

1. **环境变量密钥不可用于探活**：`test_connection` 用 `_stored_key()` 取密钥，忽略了 `effective()` 里由环境
   变量提供的密钥，导致 `PAPERPILOT_LLM_API_KEY` 驱动的实例误报“先填写 API Key”。改为优先使用生效值。
2. **截断回复的形状**：网关在 `max_tokens` 处返回 `choices[0].delta`（无 `message`）。新增
   `llm.extract_content()` 同时接受两种形状；空内容抛 `LLMBadResponse`，审计映射为 `schema` 降级。
3. **空回复重试**：`audited_chat` 对 `LLMBadResponse` 重试一次（0.6 s 退避），JSON schema 失败仍走修复路径。
   实测有一次语义核查收到空回复，修复后复测通过。
4. **探活预算**：32 → 512 token。推理型模型会把小额度耗在推理片段上（32 token 稳定空回复，
   96 token 时好时坏，512 token 稳定）。
5. **Agent 简报**：模型回答过短（<10 字符）时用 3200 token 重试一次；长但缺引用标记的回答不重试，
   直接回退规则简报——引用标记是产品约束，不靠重试刷出来。
6. **4xx 详情**：附供应商原文（截断 160 字，掩码 `Bearer …` / `sk-…`），例如
   `invalid_parameter`、`no provider supported`、速率限制。

新增 `scripts/live_agent_check.py`（无 key 退出 2、不写报告），把“真实模型能不能跑通 Agent”变成一条可复现命令。
门禁变为 **577/577** 测试、90/90 验收；真实模型 13/13。边界：只测一个供应商、一个模型；回答质量无人评分。
