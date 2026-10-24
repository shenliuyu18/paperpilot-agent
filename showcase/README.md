# PaperPilot 静态展示站

这是 PaperPilot 的静态展示页，展示产品定位、功能、架构、截图与演示视频。

**重要：** 静态页不运行 FastAPI API。真实的检索、PDF 上传、对比、证据问答、研究库与转化能力需要运行 `paperpilot` 后端。

## 本地预览

```bash
cd showcase
python -m http.server 8080
# 打开 http://127.0.0.1:8080
```

## 重新生成

需要先准备：

- `paperpilot/docs/screenshot-*.png`（含 ask-retrieval / library / agent / agent-checkpoint / agent-topic / agent-completed / agent-ledger）
- `demo/out/paperpilot_demo.mp4`
- `demo/out/cover.jpg`

```bash
python showcase/build_showcase.py
```

会生成自包含的 `showcase/index.html`（截图以 base64 内嵌），并复制演示视频与封面到 `showcase/`。

## Cloudflare Pages 部署

```bash
# 方式一：浏览器授权
npx wrangler login
npx wrangler pages deploy showcase --project-name paperpilot-showcase

# 方式二：API Token（需要 Pages:Edit 权限）
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."
npx wrangler pages deploy showcase --project-name paperpilot-showcase
```

部署完成后把返回的 `*.pages.dev` 地址填到仓库根 README 的 "Live showcase" 处。

## 文件

- `index.html`：自包含展示页（截图内嵌）
- `paperpilot_demo.mp4`：演示视频
- `cover.jpg`：视频封面
- `build_showcase.py`：生成脚本
