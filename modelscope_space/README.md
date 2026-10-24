---
license: Apache License 2.0
---

# PaperPilot · 论文领航员

从一个研究方向，到检索计划、证据阅读、可辩护选题与论证体检的科研学习智能体。

这是 PaperPilot 的 **FastAPI Agent**。默认离线规则模式，无需任何 API key；项目 Copilot、研究项目工作区、跨轮项目记忆、运行中实时干预、运行对比、结构化草稿工作台与核查/导出、自适应研究 Agent、证据账本、检索（arXiv/Crossref/Europe PMC/DOAJ/HAL/多源聚合/自定义 API）、精读、翻译、对比、创新点、成果、研究空白、选题、写作（草稿/核查/论证体检）、证据问答（支持度矩阵/数值/引用核验）、研究库引用体检、在线/本地术语库与检索留痕均可使用。

当前 6.3.0 支持在界面上接入自己的大模型：顶栏「模型设置」选择 OpenAI / DeepSeek / 月之暗面 Kimi / 阿里通义千问 / 智谱 GLM 或任意 OpenAI 兼容网关，填入自己的接口地址、模型名与 API Key，「测试连接」通过后「保存并启用」，本实例立即从离线规则模式切到在线增强模式；五类任务（研究报告、Copilot、草稿写作、语义核查、矛盾复核）可分别指定模型。**本创空间为共享实例，密钥只驻留进程内存、不写入磁盘、接口也不回显掩码，重启即失效——请不要在此粘贴生产密钥。** 6.1 的作者元数据与审稿映射、6.0 的四套投稿档案/投稿包 ZIP/盲审/审稿视角/RIS 与 EndNote 互操作，5.2 的跨进程锁、4×6 金标语料与线上 PDF，以及 2.0–5.1 的项目记忆、Copilot、稿件、DOCX/PDF、矛盾复核与金标集工作台继续保留。5 篇真实开放获取 PDF 真机检查 10/10，41 条回归 + 582 条全量测试 + 13 项 Copilot 专项 + 90 项验收通过。投稿档案是参考要求、审稿映射只定位段落；模型配置链路已用真实密钥在 ModelScope 推理 API（GLM-5.2）跑通 Agent/Copilot/语义核查（`scripts/live_agent_check.py`，13/13），人工金标签字仍未完成。

- 开源仓库：https://github.com/shenliuyu18/paperpilot-agent
- Cloudflare Pages 展示页：https://paperpilot-showcase.pages.dev/
- GitHub Pages 项目页：https://shenliuyu18.github.io/paperpilot-agent/

## 说明

- Docker 容器监听 `0.0.0.0:7860`。
- ModelScope 创空间磁盘为临时存储，上传 PDF、自适应任务/证据账本、研究项目/记忆/草稿与研究库不跨实例/重启持久化；导出文件是可持久带走的产物。
- SSE 进度流经托管网关时可能被缓冲，页面会自动回退到轮询，功能不受影响。
- 本项目不包含任何 API key；界面里填写的密钥在本实例只存内存（`PAPERPILOT_LLM_PERSIST_KEY=0`），既不落盘也不回显，配置本身同样不跨重启保留。
- 配置的接口地址默认只允许公网 HTTPS，无法指向内网或元数据地址。

