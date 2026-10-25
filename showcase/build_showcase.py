from __future__ import annotations

import base64
import io
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEMO = PROJECT / "demo" / "out" / "paperpilot_demo.mp4"
COVER = PROJECT / "demo" / "out" / "cover.jpg"
GITHUB = "https://github.com/shenliuyu18/paperpilot-agent"
SHOTS = {
    "home": PROJECT / "paperpilot" / "docs" / "screenshot-home.png",
    "compare": PROJECT / "paperpilot" / "docs" / "screenshot-compare.png",
    "graph": PROJECT / "paperpilot" / "docs" / "screenshot-graph.png",
    "innovate": PROJECT / "paperpilot" / "docs" / "screenshot-innovate.png",
    "transform": PROJECT / "paperpilot" / "docs" / "screenshot-transform.png",
    "ask": PROJECT / "paperpilot" / "docs" / "screenshot-ask-retrieval.png",
    "library": PROJECT / "paperpilot" / "docs" / "screenshot-library.png",
    "topic": PROJECT / "paperpilot" / "docs" / "screenshot-topic-questions.png",
    "writing": PROJECT / "paperpilot" / "docs" / "screenshot-writing.png",
    "check": PROJECT / "paperpilot" / "docs" / "screenshot-check.png",
    "translation": PROJECT / "paperpilot" / "docs" / "screenshot-translation.png",
    "citations": PROJECT / "paperpilot" / "docs" / "screenshot-citations.png",
    "glossary": PROJECT / "paperpilot" / "docs" / "screenshot-glossary.png",
    "plan": PROJECT / "paperpilot" / "docs" / "screenshot-plan.png",
    "layers": PROJECT / "paperpilot" / "docs" / "screenshot-layers.png",
    "health": PROJECT / "paperpilot" / "docs" / "screenshot-library-health.png",
    "advisor": PROJECT / "paperpilot" / "docs" / "screenshot-topic-advisor.png",
    "argument": PROJECT / "paperpilot" / "docs" / "screenshot-argument.png",
    "sources": PROJECT / "paperpilot" / "docs" / "screenshot-sources.png",
    "custom_source": PROJECT / "paperpilot" / "docs" / "screenshot-custom-source.png",
    "history": PROJECT / "paperpilot" / "docs" / "screenshot-history.png",
    "glossary_packs": PROJECT / "paperpilot" / "docs" / "screenshot-glossary-packs.png",
    "agent": PROJECT / "paperpilot" / "docs" / "screenshot-agent.png",
    "agent_checkpoint": PROJECT / "paperpilot" / "docs" / "screenshot-agent-checkpoint.png",
    "agent_completed": PROJECT / "paperpilot" / "docs" / "screenshot-agent-completed.png",
    "agent_topic": PROJECT / "paperpilot" / "docs" / "screenshot-agent-topic.png",
    "agent_ledger": PROJECT / "paperpilot" / "docs" / "screenshot-agent-ledger.png",
    "projects": PROJECT / "paperpilot" / "docs" / "screenshot-projects.png",
    "project_overview": PROJECT / "paperpilot" / "docs" / "screenshot-project-overview.png",
    "agent_live": PROJECT / "paperpilot" / "docs" / "screenshot-agent-live.png",
    "run_compare": PROJECT / "paperpilot" / "docs" / "screenshot-run-compare.png",
    "draft_studio": PROJECT / "paperpilot" / "docs" / "screenshot-draft-studio.png",
    "draft_audit": PROJECT / "paperpilot" / "docs" / "screenshot-draft-audit.png",
    "manuscript": PROJECT / "paperpilot" / "docs" / "screenshot-manuscript.png",
    "tracking": PROJECT / "paperpilot" / "docs" / "screenshot-tracking.png",
    "draft_v51": PROJECT / "paperpilot" / "docs" / "screenshot-draft-v51.png",
    "submission": PROJECT / "paperpilot" / "docs" / "screenshot-submission.png",
    "model": PROJECT / "paperpilot" / "docs" / "screenshot-model.png",
    "model_usage": PROJECT / "paperpilot" / "docs" / "screenshot-model-usage.png",
    "ask_retrieval": PROJECT / "paperpilot" / "docs" / "screenshot-ask-retrieval.png",
    "copilot_nav": PROJECT / "paperpilot" / "docs" / "screenshot-copilot-nav.png",
    "assistant_insufficient": PROJECT / "paperpilot" / "docs" / "screenshot-assistant-insufficient.png",
    "draft_semantic": PROJECT / "paperpilot" / "docs" / "screenshot-draft-semantic.png",
}


def data_uri(path: Path, width: int = 1100) -> str:
    image = Image.open(path).convert("RGB")
    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def main() -> None:
    if not COVER.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "3", "-i", str(DEMO), "-frames:v", "1", "-q:v", "2", str(COVER)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if DEMO.exists():
        target = ROOT / "paperpilot_demo.mp4"
        if not target.exists() or target.stat().st_size != DEMO.stat().st_size:
            target.write_bytes(DEMO.read_bytes())
    if COVER.exists():
        cover_target = ROOT / "cover.jpg"
        if not cover_target.exists() or cover_target.stat().st_size != COVER.stat().st_size:
            cover_target.write_bytes(COVER.read_bytes())

    images = {key: data_uri(path) for key, path in SHOTS.items()}
    html = TEMPLATE
    for key, uri in images.items():
        html = html.replace(f"{{{{{key.upper()}}}}}", uri)
    html = html.replace("{{GITHUB}}", GITHUB)
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    print("showcase/index.html", round(len(html.encode("utf-8")) / 1024, 1), "KB")
    print("video", "present" if (ROOT / "paperpilot_demo.mp4").exists() else "missing")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>PaperPilot · 论文领航员</title>
<meta name="description" content="PaperPilot v8.2.0：支持证据高亮定位、原文页、OCR 与可复现研究档案的智能体。本版把边界收紧——标识符统一净化、CORS 默认同源、自建镜像不含用户数据与密钥，多源聚合检索逐源交代状态，读过的论文可进项目引用池并被引用，草稿正文不再被静默丢弃。顶栏即可接入你自己的大模型（OpenAI、DeepSeek、Kimi、通义千问、GLM 或任意 OpenAI 兼容网关），密钥只留在当前实例、接口只回掩码，五类任务可分用途指定模型。保留项目 Copilot、引用接地与语义核查、跨轮项目记忆、实时干预与 SSE、结构化草稿工作台与证据锚点核查、DOCX/PDF 等六种导出、矛盾模型复核、跨进程订阅锁、线上 PDF、投稿包与审稿视角，以及自适应研究 Agent、混合检索与引用核验。" />
<style>
  :root{
    --bg:#050b16;--bg2:#081525;--panel:rgba(13,29,50,.78);--line:rgba(137,181,230,.16);
    --text:#eaf3ff;--muted:#93a9c6;--muted2:#6f88a8;--cyan:#4ce3f7;--pink:#ff6aa9;
    --amber:#ffca6b;--green:#6ee7b7;--radius:18px;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{
    margin:0;background:
      radial-gradient(900px 620px at 8% -10%,rgba(76,227,247,.10),transparent 60%),
      radial-gradient(800px 600px at 96% 8%,rgba(255,106,169,.08),transparent 60%),
      linear-gradient(180deg,#050b16,#06101e 55%,#040912);
    color:var(--text);font-family:"Microsoft YaHei","Segoe UI","PingFang SC",Arial,sans-serif;
    line-height:1.7;overflow-x:hidden;
  }
  body::before{
    content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
    background-image:linear-gradient(rgba(137,181,230,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(137,181,230,.035) 1px,transparent 1px);
    background-size:56px 56px;mask-image:radial-gradient(circle at 55% 12%,black,transparent 78%);
  }
  a{color:var(--cyan);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{position:relative;z-index:1;max-width:1280px;margin:0 auto;padding:0 24px}
  nav{position:sticky;top:0;z-index:20;backdrop-filter:blur(16px);background:rgba(5,11,22,.72);border-bottom:1px solid var(--line)}
  .nav-inner{max-width:1280px;margin:0 auto;padding:13px 24px;display:flex;align-items:center;justify-content:space-between;gap:18px}
  .brand{display:flex;align-items:center;gap:10px;font-weight:750;letter-spacing:.2px}
  .brand-mark{width:34px;height:34px;border:1px solid rgba(76,227,247,.4);border-radius:10px;display:grid;place-items:center;color:var(--cyan);background:rgba(76,227,247,.08)}
  .brand-mark svg{width:22px;height:22px}
  .nav-links{display:flex;gap:20px;align-items:center;color:var(--muted);font-size:13px}
  .nav-links a{color:var(--muted)}
  .nav-links a:hover{color:var(--text)}
  .nav-cta{border:1px solid rgba(76,227,247,.4);background:rgba(76,227,247,.12);color:#dffbff;padding:7px 13px;border-radius:9px;font-size:12.5px}
  .hero{padding:86px 0 54px;display:grid;grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr);gap:38px;align-items:center}
  .eyebrow{color:var(--cyan);font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase}
  h1{font-size:clamp(38px,5.2vw,68px);line-height:1.12;letter-spacing:-1.5px;margin:14px 0 18px}
  .grad{background:linear-gradient(110deg,#fff 12%,#9df4ff 52%,#ffb3d3 100%);-webkit-background-clip:text;background-clip:text;color:transparent}
  .lead{color:var(--muted);font-size:16px;max-width:760px;margin:0 0 24px}
  .cta{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:30px}
  .btn{border:1px solid var(--line);background:rgba(137,181,230,.06);color:var(--text);padding:10px 16px;border-radius:10px;font-size:13px;font-weight:650;transition:.18s}
  .btn:hover{transform:translateY(-1px);border-color:rgba(76,227,247,.45);background:rgba(76,227,247,.1);text-decoration:none}
  .btn-primary{border-color:rgba(76,227,247,.5);background:linear-gradient(135deg,rgba(76,227,247,.26),rgba(76,227,247,.1));color:#e7fdff;box-shadow:0 12px 34px rgba(76,227,247,.12)}
  .stats{display:flex;gap:30px;flex-wrap:wrap}
  .stat strong{display:block;font-family:Consolas,monospace;font-size:28px;color:var(--cyan);line-height:1.2}
  .stat span{color:var(--muted2);font-size:11px}
  .hero-card{border:1px solid var(--line);border-radius:22px;padding:20px;background:linear-gradient(155deg,rgba(16,35,58,.82),rgba(7,16,30,.76));box-shadow:0 24px 70px rgba(0,0,0,.35);position:relative;overflow:hidden}
  .hero-card::after{content:"";position:absolute;width:220px;height:220px;right:-90px;top:-90px;border:1px solid rgba(76,227,247,.16);border-radius:50%;box-shadow:0 0 0 24px rgba(76,227,247,.03),0 0 0 50px rgba(76,227,247,.02)}
  .card-label{color:var(--muted2);font-size:10px;letter-spacing:1.3px;text-transform:uppercase}
  .card-title{font-size:17px;font-weight:700;margin:6px 0 16px;line-height:1.5}
  .trace{display:flex;flex-direction:column;gap:9px}
  .trace-row{display:grid;grid-template-columns:26px 58px 1fr auto;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
  .trace-row b{color:var(--cyan);font-size:11px}
  .trace-row .desc{color:#c6d9ee}
  .pill{border:1px solid var(--line);border-radius:999px;padding:2px 7px;font-size:9px;color:var(--green)}
  .flow{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:18px;color:var(--muted);font-size:11px}
  .flow span{border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:rgba(137,181,230,.04)}
  .flow i{width:14px;height:1px;background:var(--line)}
  section{padding:58px 0;border-top:1px solid rgba(137,181,230,.08)}
  .section-head{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:24px}
  h2{font-size:clamp(24px,3vw,36px);line-height:1.25;margin:7px 0 0;letter-spacing:-.5px}
  .section-head p{color:var(--muted);font-size:13px;max-width:520px;margin:0}
  .grid{display:grid;gap:12px}
  .problems{grid-template-columns:repeat(3,minmax(0,1fr))}
  .problem{border:1px solid var(--line);border-radius:14px;padding:15px;background:rgba(9,21,38,.6)}
  .problem .num{font-family:Consolas,monospace;color:var(--pink);font-size:11px}
  .problem h3{font-size:14px;margin:7px 0 5px}
  .problem p{color:var(--muted);font-size:12px;margin:0;line-height:1.65}
  .features{grid-template-columns:repeat(3,minmax(0,1fr))}
  .feature{border:1px solid var(--line);border-radius:16px;padding:18px;background:linear-gradient(150deg,rgba(13,29,50,.72),rgba(7,16,30,.62))}
  .feature .tag{font-size:10px;color:var(--cyan);letter-spacing:.8px}
  .feature h3{font-size:16px;margin:7px 0 6px}
  .feature p{color:var(--muted);font-size:12.5px;margin:0;line-height:1.7}
  .feature.pink .tag{color:var(--pink)}
  .feature.amber .tag{color:var(--amber)}
  .shots{grid-template-columns:repeat(2,minmax(0,1fr))}
  .research-shots{grid-template-columns:repeat(3,minmax(0,1fr))}
  .shot{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#081525}
  .shot img{display:block;width:100%;height:auto}
  .shot .cap{padding:10px 13px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:10px}
  .shot .cap b{color:var(--text);font-weight:650}
  .arch{border:1px solid var(--line);border-radius:16px;background:#050d19;padding:18px;overflow:auto;color:#b9e8f5;font-family:Consolas,"Courier New",monospace;font-size:12px;line-height:1.7;white-space:pre}
  .metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:18px}
  .metric{border:1px solid var(--line);border-radius:14px;padding:14px;background:rgba(9,21,38,.58)}
  .metric strong{display:block;font-family:Consolas,monospace;font-size:26px;color:var(--cyan)}
  .metric span{color:var(--muted2);font-size:10.5px}
  .video-card{border:1px solid var(--line);border-radius:20px;padding:14px;background:linear-gradient(150deg,rgba(13,29,50,.72),rgba(7,16,30,.62))}
  video{display:block;width:100%;height:auto;border-radius:12px;background:#000}
  .video-note{color:var(--muted);font-size:12px;margin:10px 3px 0;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
  .run-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .run-card{border:1px solid var(--line);border-radius:16px;padding:17px;background:rgba(9,21,38,.58)}
  .run-card h3{font-size:15px;margin:0 0 8px}
  .run-card p{color:var(--muted);font-size:12.5px;margin:0 0 12px}
  pre{border:1px solid var(--line);border-radius:10px;background:#050d19;padding:12px;color:#b9e8f5;font-family:Consolas,monospace;font-size:11.5px;line-height:1.65;overflow:auto;white-space:pre;margin:0}
  .note{border-left:2px solid var(--amber);background:rgba(255,202,107,.06);border-radius:0 10px 10px 0;padding:10px 13px;color:#f3d89f;font-size:12px;line-height:1.7;margin-top:14px}
  footer{border-top:1px solid var(--line);padding:26px 0 40px;color:var(--muted2);font-size:11px;display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap}
  @media(max-width:980px){.hero{grid-template-columns:1fr;padding-top:56px}.problems{grid-template-columns:repeat(2,1fr)}.features{grid-template-columns:repeat(2,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}.run-grid{grid-template-columns:1fr}.research-shots{grid-template-columns:1fr}}
  @media(max-width:640px){.nav-links{display:none}.problems,.features,.shots{grid-template-columns:1fr}.section-head{display:block}.section-head p{margin-top:8px}.wrap{padding:0 16px}nav .nav-inner{padding:11px 16px}}
</style>
</head>
<body>
<nav>
  <div class="nav-inner">
    <a class="brand" href="#top" style="color:var(--text);text-decoration:none">
      <span class="brand-mark">
        <svg viewBox="0 0 40 40" fill="none"><path d="M8 11.5 20 5l12 6.5v17L20 35 8 28.5v-17Z" stroke="currentColor" stroke-width="1.6"/><path d="M8 11.5 20 18l12-6.5M20 18v17" stroke="currentColor" stroke-width="1.2" opacity=".65"/><circle cx="20" cy="18" r="2.6" fill="currentColor"/></svg>
      </span>
      <span>PaperPilot <span style="color:var(--muted2);font-weight:400;font-size:12px">论文领航员</span></span>
    </a>
    <div class="nav-links">
      <a href="#problems">问题</a>
      <a href="#features">功能</a>
      <a href="#agent">任务</a>
      <a href="#workspace">工作区</a>
      <a href="#copilot">Copilot</a>
      <a href="#shots">界面</a>
      <a href="#eval">评测</a>
      <a href="#demo">演示</a>
      <a href="#run">运行</a>
      <a href="https://modelscope.cn/studios/shenliuyu/paperpilot/" target="_blank" rel="noopener">在线 Agent</a>
    </div>
    <a class="nav-cta" href="{{GITHUB}}" target="_blank" rel="noopener">GitHub 源码</a>
  </div>
</nav>

<main id="top">
  <div class="wrap">
    <header class="hero">
      <div>
        <div class="eyebrow">科研学习智能体</div>
        <h1>从一个方向，<br><span class="grad">到可辩护的研究</span></h1>
        <p class="lead">PaperPilot 把检索、精读、翻译、选题、写作与核查串成一条可回退的研究流程；v2.0 把它收进<b>研究项目</b>，v3.0 让项目资产会答话，v3.1 再把<b>对比</b>做实：不再套用内置 ADC 语料，而是从你选中的论文里抽取研究问题、方法、结果与局限，再生成共识、分歧与后续空白。<b>项目 Copilot</b> 只基于项目证据、草稿与运行报告回答，每条结论都带可回到原文的引用。</p>
        <div class="cta">
          <a class="btn btn-primary" href="#demo">观看 3 分 8 秒演示</a>
          <a class="btn" href="{{GITHUB}}" target="_blank" rel="noopener">查看源码</a>
          <a class="btn" href="https://modelscope.cn/studios/shenliuyu/paperpilot/" target="_blank" rel="noopener">打开在线 Agent</a>
          <a class="btn" href="#run">运行完整版</a>
        </div>
        <div class="stats">
          <div class="stat"><strong>5</strong><span>篇真实 arXiv 论文</span></div>
          <div class="stat"><strong>10/10</strong><span>真机能力检查</span></div>
          <div class="stat"><strong>41/41</strong><span>回归用例通过</span></div>
          <div class="stat"><strong>337/337</strong><span>测试通过</span></div>
          <div class="stat"><strong>13/13</strong><span>Copilot 评测</span></div>
        </div>
      </div>
      <aside class="hero-card">
        <div class="card-label">项目运行轨迹</div>
        <div class="card-title">一个项目，多轮运行，一份会累积的记忆</div>
        <div class="trace">
          <div class="trace-row"><b>00</b><b style="color:var(--cyan)">Copilot</b><span class="desc">项目资产对话 · 引用接地 · 建议动作</span><span class="pill">new</span></div>
          <div class="trace-row"><b>01</b><b style="color:var(--cyan)">Project</b><span class="desc">研究问题 · 约束 · 阶段 · 多轮运行</span><span class="pill">new</span></div>
          <div class="trace-row"><b>02</b><b style="color:var(--cyan)">Memory</b><span class="desc">证据提升 · 开放问题 · 下一轮播种</span><span class="pill">new</span></div>
          <div class="trace-row"><b>03</b><b style="color:var(--cyan)">Live</b><span class="desc">SSE 进度 · 暂停/恢复 · 补充约束</span><span class="pill">new</span></div>
          <div class="trace-row"><b>04</b><b style="color:var(--cyan)">Draft</b><span class="desc">段落证据锚点 · 版本 · DOCX / PDF</span><span class="pill">new</span></div>
          <div class="trace-row"><b>05</b><b style="color:var(--cyan)">Planner/Critic</b><span class="desc">证据覆盖 · 开放问题 · 预算 · 重规划</span><span class="pill">done</span></div>
          <div class="trace-row"><b>06</b><b style="color:var(--cyan)">Scout</b><span class="desc">检索计划 · 分层 · 多源并发</span><span class="pill">done</span></div>
          <div class="trace-row"><b>07</b><b style="color:var(--cyan)">Reader</b><span class="desc">PDF 解析 · 证据定位 · 精读笔记</span><span class="pill">done</span></div>
          <div class="trace-row"><b>08</b><b style="color:var(--cyan)">Planner</b><span class="desc">选题版图 · 可行性 · 导师沟通</span><span class="pill">done</span></div>
          <div class="trace-row"><b>09</b><b style="color:var(--cyan)">Critic</b><span class="desc">证据校验 · 引用体检 · 论证体检</span><span class="pill">done</span></div>
          <div class="trace-row"><b>10</b><b style="color:var(--cyan)">Librarian</b><span class="desc">研究库 · 笔记 · 证据检索</span><span class="pill">done</span></div>
        </div>
        <div class="flow"><span>Copilot</span><i></i><span>项目</span><i></i><span>运行</span><i></i><span>记忆</span><i></i><span>草稿</span><i></i><span>核查</span><i></i><span>导出</span></div>
      </aside>
    </header>
  </div>

  <section id="problems">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">为什么需要它</div><h2>读论文的六个断点</h2></div>
        <p>不是学生不努力，而是“找到、读懂、比清、提炼、落地、证成”之间缺少一条可回退的证据链。</p>
      </div>
      <div class="grid problems">
        <div class="problem"><div class="num">01</div><h3>搜不准</h3><p>关键词不会写、同义词不知道、数据库分散，容易漏掉关键工作。</p></div>
        <div class="problem"><div class="num">02</div><h3>读不完</h3><p>英文长文、术语密集、公式图表难懂，最后只读摘要。</p></div>
        <div class="problem"><div class="num">03</div><h3>比不清</h3><p>工艺、数据集、测试条件不一致，却把数字直接排序。</p></div>
        <div class="problem"><div class="num">04</div><h3>提不出</h3><p>分不清作者声称与实验证据，把 future work 当成创新点。</p></div>
        <div class="problem"><div class="num">05</div><h3>做不出</h3><p>代码跑不起来、超参数缺失，读完就忘，无法复现或教学转化。</p></div>
        <div class="problem"><div class="num">06</div><h3>证不成</h3><p>选题宽窄失当、引用缺失、论证结构断裂，最后只能堆砌材料。</p></div>
      </div>
    </div>
  </section>

  <section id="features">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">功能地图</div><h2>十四个工作区，一条可回退的研究流程</h2></div>
        <p>不是“总结论文”，而是把论文变成可核查的知识资产、可辩护的选题和可执行的下一步。</p>
      </div>
      <div class="grid features">
        <div class="feature"><div class="tag">TASK</div><h3>自适应研究 Agent</h3><p>Planner 按证据覆盖、引用支持、饱和度与开放问题动态选择检索/精读/追问/对比/选题/写作/撰写章节/核查/收束；三档自主程度、四类预算与证据账本。</p></div>
        <div class="feature"><div class="tag">PROJECT</div><h3>研究项目与记忆</h3><p>一个项目下多轮运行共享记忆：运行结束自动提升已核对证据、开放问题、决策与论文快照，下一轮带着记忆继续，并可对比 2–3 轮运行的动作与证据差异。</p></div>
        <div class="feature pink"><div class="tag">LIVE</div><h3>运行中实时干预</h3><p>暂停、恢复、重新规划、切换自主程度、补充约束、改选论文、注入问题都在动作边界生效；进度经 SSE 快照流推送，代理不支持时自动降级轮询。</p></div>
        <div class="feature amber"><div class="tag">DRAFT</div><h3>结构化草稿工作台</h3><p>章节与段落块、段落级证据锚点与引用键、版本快照与恢复、8 条核查规则分级定位、主张三档判定与矛盾复核，导出 Markdown/LaTeX/BibTeX/JSON/DOCX/PDF。</p></div>
        <div class="feature"><div class="tag">FIND</div><h3>检索计划与分层</h3><p>先拆概念块、中英同义词与缩写全称，再生成各库检索式；结果按直接相关/方法参考/背景脉络分层，并标注来源类型与题录可信度。</p></div>
        <div class="feature"><div class="tag">READ</div><h3>分层精读</h3><p>TL;DR、问题/方法/创新点、关键指标、图表/数值/局限抽取与页码级证据链；可导出五栏精读笔记。</p></div>
        <div class="feature"><div class="tag">TRANSLATE</div><h3>论文翻译工作台</h3><p>摘要/章节/整篇翻译、中英对照、311 条分类术语表、公式/引用/数字保护、缓存与双语 Markdown 导出；公共接口与模型双通道。</p></div>
        <div class="feature"><div class="tag">GRAPH</div><h3>概念图谱</h3><p>论文、概念/方法、数据/任务、指标、局限五类节点，点击查看来源论文与原文摘句；类型筛选、Markdown/JSON 导出，同名节点只代表共现。</p></div>
        <div class="feature pink"><div class="tag">COMPARE 2.0</div><h3>多篇对比</h3><p>逐对判定可比性，列共享指标、任务与差异；共识引用共同指标与各篇数值，分歧引用局限从句，不可比时明确建议分开阅读。</p></div>
        <div class="feature pink"><div class="tag">EXTRACT</div><h3>创新点提炼</h3><p>作者声称 / 实验证据 / 模型推断三栏分离，附审稿人问题与启发式评分。</p></div>
        <div class="feature pink"><div class="tag">BUILD</div><h3>论文 → 成果</h3><p>复现计划、代码骨架、实验清单、组会 Slides、课程案例与 Quiz。</p></div>
        <div class="feature amber"><div class="tag">GAP</div><h3>研究空白雷达</h3><p>从证据缺口生成研究问题，附新颖性、可行性与最小实验。</p></div>
        <div class="feature amber"><div class="tag">ASK</div><h3>证据问答</h3><p>问题拆解、章节意图、字段证据、支持度矩阵与可核对数值；答案附页码。</p></div>
        <div class="feature"><div class="tag">CITE</div><h3>引用网络</h3><p>OpenCitations 参考/被引关系与 Crossref 题录补全；可收藏并导出引用清单。</p></div>
        <div class="feature"><div class="tag">AUDIT</div><h3>引用体检</h3><p>研究库检查缺作者/年份/来源/DOI/链接、重复题名与异常年份；可选 Crossref DOI 核验，只给修复动作。</p></div>
        <div class="feature amber"><div class="tag">LIBRARY</div><h3>我的研究库</h3><p>收藏、待读/在读/已读、标签、笔记、引用体检，Markdown/JSON/BibTeX 导出与 JSON 导入。</p></div>
        <div class="feature"><div class="tag">TOPIC</div><h3>选题工作台</h3><p>方法簇与年份版图、证据缺口、候选研究问题、四维可行性、论证卡、三阶段阅读路径与最小实验计划。</p></div>
        <div class="feature amber"><div class="tag">MENTOR</div><h3>导师沟通页</h3><p>把不成熟的想法整理成一页纸、带选项的提问和追问预演，先自己判断，再占用导师时间。</p></div>
        <div class="feature pink"><div class="tag">WRITE</div><h3>写作草稿</h3><p>相关工作、论文提纲、摘要骨架；保留引用键与参考文献，所有数字要求回原文核对。</p></div>
        <div class="feature pink"><div class="tag">CHECK</div><h3>主张核查</h3><p>逐句检查文本与证据的重合度，标出未在证据中出现的数值，给出缩小论断/补引用建议。</p></div>
        <div class="feature pink"><div class="tag">ARGUE</div><h3>论证体检</h3><p>检查问题、缺口、方法、证据、讨论、局限、结论；统计引用标记与证据线索，列出未支撑句和优先修改动作。</p></div>
        <div class="feature"><div class="tag">SOURCE</div><h3>自定义论文来源</h3><p>Semantic Scholar、CORE 或任意 JSON API；地址、字段映射、账号/token、启停与测试连接都可配置，密钥只回显掩码。</p></div>
        <div class="feature"><div class="tag">MODEL</div><h3>接入你自己的大模型</h3><p>顶栏「模型设置」选 OpenAI / DeepSeek / Kimi / 通义千问 / GLM 或任意 OpenAI 兼容网关，填自己的 Key 与模型名，测试连接后保存即生效；五类任务可分用途指定模型，密钥只留当前实例、接口只回掩码。</p></div>
        <div class="feature"><div class="tag">GLOSSARY</div><h3>在线/本地术语库</h3><p>内置 311 条之外，可下载机器之心 AI 术语库（约 2442 条）或导入本地 JSON/CSV/TSV/Markdown；来源、许可与启停可见。</p></div>
        <div class="feature amber"><div class="tag">LOG</div><h3>检索留痕</h3><p>自动记录查询、来源、结果数与分层，支持 Markdown/CSV 导出和清空，方便复现检索过程与写检索日志。</p></div>
        <div class="feature"><div class="tag">SCALE</div><h3>多源并发与降级</h3><p>Crossref/arXiv/Europe PMC 并发聚合、RRF 融合与去重；8 秒响应上限，慢源冷却后跳过，不让外部 API 拖垮流程。</p></div>
        <div class="feature amber"><div class="tag">BOUNDARY</div><h3>证据与使用边界</h3><p>分层、可信度、可行性与论证体检都保留不确定性；不伪造引用、不代写终稿，关键判断留给用户。</p></div>
      </div>
    </div>
  </section>

  <section id="shots">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">界面</div><h2>从检索到产出的完整工作台</h2></div>
        <p>左侧检索，右侧十四个工作区；离线模式也能完整演示。</p>
      </div>
      <div class="grid shots">
        <div class="shot"><img src="{{PLAN}}" alt="PaperPilot 检索计划" loading="lazy"><div class="cap"><b>检索计划</b><span>概念块 · 中英扩展 · 各库检索式</span></div></div>
        <div class="shot"><img src="{{LAYERS}}" alt="PaperPilot 证据分层" loading="lazy"><div class="cap"><b>分层结果</b><span>直接相关 / 方法参考 / 背景脉络 · 可信度</span></div></div>
        <div class="shot"><img src="{{SOURCES}}" alt="PaperPilot 自定义来源" loading="lazy"><div class="cap"><b>来源设置</b><span>API · 字段映射 · token 掩码 · 测试</span></div></div>
        <div class="shot"><img src="{{CUSTOM_SOURCE}}" alt="PaperPilot 自定义来源检索" loading="lazy"><div class="cap"><b>自定义检索</b><span>Crossref API · 分层 · 收藏/精读</span></div></div>
        <div class="shot"><img src="{{HOME}}" alt="PaperPilot 首页" loading="lazy"><div class="cap"><b>精读</b><span>TL;DR · 指标 · 证据链 · 精读笔记</span></div></div>
        <div class="shot"><img src="{{GRAPH}}" alt="PaperPilot 概念图谱" loading="lazy"><div class="cap"><b>概念图谱</b><span>论文/概念/任务/指标/局限 · 节点证据</span></div></div>
        <div class="shot"><img src="{{COMPARE}}" alt="PaperPilot 对比 2.0" loading="lazy"><div class="cap"><b>对比 2.0</b><span>可比性判定 · 结构化共识 · 局限分歧</span></div></div>
        <div class="shot"><img src="{{INNOVATE}}" alt="PaperPilot 创新点" loading="lazy"><div class="cap"><b>创新点</b><span>作者声称 / 实验证据 / 模型推断</span></div></div>
        <div class="shot"><img src="{{TRANSFORM}}" alt="PaperPilot 成果" loading="lazy"><div class="cap"><b>成果</b><span>复现计划 · 代码骨架 · 课程案例</span></div></div>
        <div class="shot"><img src="{{ASK}}" alt="PaperPilot 证据问答" loading="lazy"><div class="cap"><b>证据问答</b><span>跨论文检索 · 页码引用 · 覆盖提示</span></div></div>
        <div class="shot"><img src="{{LIBRARY}}" alt="PaperPilot 我的库" loading="lazy"><div class="cap"><b>我的库</b><span>收藏 · 状态 · 标签 · 研究笔记</span></div></div>
      </div>
    </div>
  </section>

  <section id="agent">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">研究任务</div><h2>Agent 自己决定下一步，检查点由你定</h2></div>
        <p>检索检查点可改选论文，选题检查点可确认方向；证据不足时 Agent 会追问或扩展检索；动作、证据、开放问题、预算与人工决策全部落盘。完成后生成带证据账本与未解决问题的 Markdown 简报。</p>
      </div>
      <div class="grid shots research-shots">
        <div class="shot"><img src="{{AGENT}}" alt="PaperPilot 自适应研究启动" loading="lazy"><div class="cap"><b>启动任务</b><span>目标 · 来源 · 三档自主 · 预算</span></div></div>
        <div class="shot"><img src="{{AGENT_CHECKPOINT}}" alt="PaperPilot 检索检查点" loading="lazy"><div class="cap"><b>检索检查点</b><span>分层候选 · 改选论文 · 下一步计划</span></div></div>
        <div class="shot"><img src="{{AGENT_TOPIC}}" alt="PaperPilot 选题检查点" loading="lazy"><div class="cap"><b>选题与追问</b><span>候选方向 · 开放问题 · 人工选择</span></div></div>
        <div class="shot"><img src="{{AGENT_COMPLETED}}" alt="PaperPilot 自适应任务完成" loading="lazy"><div class="cap"><b>动态动作序列</b><span>两次追问 · 写作 · 核查 · 简报</span></div></div>
        <div class="shot"><img src="{{AGENT_LEDGER}}" alt="PaperPilot 证据账本" loading="lazy"><div class="cap"><b>证据账本</b><span>核对状态 · 预算 · 决策</span></div></div>
        <div class="shot"><img src="{{ASK_RETRIEVAL}}" alt="PaperPilot 混合检索与引用核验" loading="lazy"><div class="cap"><b>混合检索</b><span>检索模式 · 引用核对 6/6 · 原文已核对</span></div></div>
      </div>
      <div class="note">离线 demo（未配置 LLM）：v1.0 单轮 auto 12 个动作（含 2 次追问）/16 条证据/15,138 字符；checkpoint 11 个动作/14 条证据/13,176 字符；预算收缩 3 个动作/3,742 字符。v2.0 同一项目第二轮带着 14 条项目记忆启动，动作数降到 11 即收束；工作区评测 0.7 秒跑完两轮运行、草稿写入、核查与六种导出。worker 为单进程单线程，项目内运行互斥，订阅并发锁只在单进程内生效。</div>
    </div>
  </section>

  <section id="workspace">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">研究工作区</div><h2>一个项目，多轮运行，一份会累积的记忆</h2></div>
        <p>v2.0 把研究任务收进项目：运行结束把已核对证据、开放问题、决策与论文快照提升到项目记忆，下一轮直接带着这些记忆继续。运行中可以暂停、改约束、换自主程度或注入新问题，进度实时推送；写完的段落绑定证据，核查会指出哪一句还没有依据。</p>
      </div>
      <div class="grid shots research-shots">
        <div class="shot"><img src="{{PROJECTS}}" alt="PaperPilot 研究项目列表" loading="lazy"><div class="cap"><b>研究项目</b><span>阶段 · 运行数 · 证据数 · 草稿块</span></div></div>
        <div class="shot"><img src="{{PROJECT_OVERVIEW}}" alt="PaperPilot 项目概览" loading="lazy"><div class="cap"><b>项目概览</b><span>研究问题 · 约束 · 最近运行 · 计数</span></div></div>
        <div class="shot"><img src="{{AGENT_LIVE}}" alt="PaperPilot 实时运行与控制栏" loading="lazy"><div class="cap"><b>实时干预</b><span>SSE 进度 · 暂停/恢复 · 补充约束 · 注入问题</span></div></div>
        <div class="shot"><img src="{{RUN_COMPARE}}" alt="PaperPilot 运行对比" loading="lazy"><div class="cap"><b>运行对比</b><span>动作序列 · 指标 · 共有/独有证据</span></div></div>
        <div class="shot"><img src="{{DRAFT_STUDIO}}" alt="PaperPilot 草稿工作台" loading="lazy"><div class="cap"><b>草稿工作台</b><span>章节段落 · 证据锚点 · 引用键 · 版本</span></div></div>
        <div class="shot"><img src="{{DRAFT_AUDIT}}" alt="PaperPilot 草稿核查" loading="lazy"><div class="cap"><b>草稿核查</b><span>缺证据 · 数值不一致 · 分级定位</span></div></div>
        <div class="shot"><img src="{{MANUSCRIPT}}" alt="PaperPilot 稿件设置与参考文献预览" loading="lazy"><div class="cap"><b>稿件设置</b><span>GB/T 7714 · APA · IEEE · DOCX · 投稿前检查</span></div></div>
        <div class="shot"><img src="{{TRACKING}}" alt="PaperPilot 检索订阅与增量索引" loading="lazy"><div class="cap"><b>订阅跟踪</b><span>新增 / 冲突 / 方法 / 无关 · 游标去重</span></div></div>
        <div class="shot"><img src="{{DRAFT_V51}}" alt="PaperPilot 草稿复核与 PDF 导出" loading="lazy"><div class="cap"><b>复核与 PDF</b><span>矛盾复核 · 线上 PDF · 订阅锁</span></div></div>
        <div class="shot"><img src="{{SUBMISSION}}" alt="PaperPilot 投稿包与审稿视角" loading="lazy"><div class="cap"><b>投稿包</b><span>四套档案 · 盲审去致谢 · 审稿映射</span></div></div>
        <div class="shot"><img src="{{MODEL}}" alt="PaperPilot 自定义大模型设置" loading="lazy"><div class="cap"><b>模型设置</b><span>常见大模型预设 · 自己的 API Key · 测试连接</span></div></div>
        <div class="shot"><img src="{{MODEL_USAGE}}" alt="PaperPilot 分用途模型与调用统计" loading="lazy"><div class="cap"><b>按用途调用</b><span>五类任务各自模型 · 调用统计 · 密钥掩码</span></div></div>
      </div>
      <div class="note">v2.0 工作区评测 <code>scripts/eval_workspace.py</code>：首轮 16 动作/16 证据，次轮带入 14 条项目证据/11 动作，草稿写入 3 块，核查 13 条高危，两轮共有证据 15 条，0.61 秒完成。导出支持 Markdown、LaTeX（zip，含 ctex）、BibTeX、JSON、DOCX 与 PDF（PDF 需本机 XeLaTeX）。</div>
    </div>
  </section>

  <section id="copilot">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">研究 Copilot</div><h2>项目资产会答话，引用能回到原文</h2></div>
        <p>v3.0 新增项目 Copilot，v3.1 把它放到主导航：只读取当前项目的证据、开放问题、决策、草稿与运行报告，每条结论带可点击引用；没有依据时明确说“证据不足”。建议动作由规则生成，必须你确认后才会调用现有接口执行。配置模型时可走增强路径，答案需通过引用接地校验，否则自动回退离线。</p>
      </div>
      <div class="grid shots research-shots">
        <div class="shot"><img src="{{COPILOT_NAV}}" alt="PaperPilot 主导航 Copilot 入口" loading="lazy"><div class="cap"><b>主导航 Copilot 入口</b><span>一键进入项目 Copilot · 证据引用 · 可执行建议</span></div></div>
        <div class="shot"><img src="{{ASSISTANT_INSUFFICIENT}}" alt="PaperPilot 无证据拒答" loading="lazy"><div class="cap"><b>无证据拒答</b><span>没有依据就明确说不足</span></div></div>
        <div class="shot"><img src="{{DRAFT_SEMANTIC}}" alt="PaperPilot 草稿语义核查" loading="lazy"><div class="cap"><b>语义核查</b><span>支持 / 部分 / 不支持 · 不改写正文</span></div></div>
      </div>
      <div class="note">v4.0 评测：337 条测试、41/41 回归、13/13 Copilot 专项；新增概念图谱 6 条与前端契约 1 条。3 篇论文实测 27 节点 / 96 边，点击节点显示来源论文与原文摘句；非 ADC 论文图谱不出现 ADC/CIM/存算。浏览器 0 console error，390px 横向溢出 0。</div>
    </div>
  </section>

  <section id="research">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">研究与写作</div><h2>从选题到导师沟通，再到论证体检</h2></div>
        <p>不只帮学生读论文，也帮学生把证据组织成可辩护的问题、可执行的阅读计划、可审查的论证结构。</p>
      </div>
      <div class="grid shots research-shots">
        <div class="shot"><img src="{{TOPIC}}" alt="PaperPilot 选题工作台" loading="lazy"><div class="cap"><b>选题</b><span>方法簇 · 候选问题 · 阅读路径</span></div></div>
        <div class="shot"><img src="{{ADVISOR}}" alt="PaperPilot 导师沟通页" loading="lazy"><div class="cap"><b>导师沟通</b><span>一页纸 · 带选项提问 · 追问预演</span></div></div>
        <div class="shot"><img src="{{ARGUMENT}}" alt="PaperPilot 论证体检" loading="lazy"><div class="cap"><b>论证体检</b><span>结构缺口 · 证据线索 · 修改动作</span></div></div>
        <div class="shot"><img src="{{HEALTH}}" alt="PaperPilot 引用体检" loading="lazy"><div class="cap"><b>引用体检</b><span>题录完整度 · 重复项 · DOI 核验</span></div></div>
        <div class="shot"><img src="{{GLOSSARY_PACKS}}" alt="PaperPilot 术语库管理" loading="lazy"><div class="cap"><b>术语库</b><span>在线 2442 条 · 本地导入 · 许可</span></div></div>
        <div class="shot"><img src="{{HISTORY}}" alt="PaperPilot 检索留痕" loading="lazy"><div class="cap"><b>检索留痕</b><span>查询 · 来源 · 分层 · 导出</span></div></div>
        <div class="shot"><img src="{{WRITING}}" alt="PaperPilot 写作草稿" loading="lazy"><div class="cap"><b>写作</b><span>相关工作 · 提纲 · 引用键</span></div></div>
        <div class="shot"><img src="{{CHECK}}" alt="PaperPilot 主张核查" loading="lazy"><div class="cap"><b>逐句核查</b><span>支持度 · 数值校验</span></div></div>
        <div class="shot"><img src="{{TRANSLATION}}" alt="PaperPilot 论文翻译工作台" loading="lazy"><div class="cap"><b>翻译</b><span>整篇/章节 · 中英对照 · 术语与缓存</span></div></div>
        <div class="shot"><img src="{{CITATIONS}}" alt="PaperPilot 引用网络" loading="lazy"><div class="cap"><b>引用网络</b><span>参考/被引 · Crossref 题录 · 收藏</span></div></div>
        <div class="shot"><img src="{{GLOSSARY}}" alt="PaperPilot 术语表" loading="lazy"><div class="cap"><b>术语</b><span>311 条 · 7 类 · 搜索浏览</span></div></div>
      </div>
    </div>
  </section>

  <section id="arch">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">架构</div><h2>可运行的最小闭环</h2></div>
        <p>多智能体编排 + 工具层 + 证据链；离线规则模式与在线模型增强共用同一套接口。</p>
      </div>
      <pre class="arch">用户
  ↓
Orchestrator（意图识别 / 任务规划 / 人工确认）
  ├─ Adaptive Planner/Critic  证据覆盖 · 开放问题 · 预算 · 重规划
  ├─ Action Runner  search/read/ask/compare/topic/draft/review/report
  ├─ Evidence Ledger  证据 · 开放问题 · 决策 · 动作轨迹
  ├─ Retrieval     BM25 候选 + 术语/章节扩展 + 可选 embedding + 引用核验
  ├─ Scout       检索计划、概念扩展、多源并发、去重分层
  ├─ Reader      PDF 解析、章节/页码、证据定位、精读笔记
  ├─ Planner     研究版图、四维可行性、论证卡、导师沟通
  ├─ Critic      证据校验、可比性、研究空白、引用体检
  ├─ Writer      相关工作/提纲/摘要骨架、主张核查、论证体检
  ├─ Source      自定义 API、字段映射、token 隔离与测试连接
  ├─ Glossary    在线/本地术语包、合并检索与许可
  └─ Librarian   研究库、检索留痕、问题拆解与证据检索

工具层：arXiv / Crossref / Europe PMC / DOAJ / HAL / 自定义 JSON API / GitHub 术语库 / OpenCitations / PDF Parser / 任务 Runner / 混合检索与引用核验 / Markdown · JSON · BibTeX Export
存储：本地 JSON（研究库、任务与步骤、精选论文快照、来源配置、术语包、留痕、检索/翻译/引用/DOI/embedding 缓存）+ 上传 PDF

当前实现：FastAPI + 原生前端 + PyMuPDF + 章节/字段感知 BM25
离线：规则抽取 / 图表数值抽取 / 人工校订语料 / 抽取式问答
在线：任意 OpenAI-compatible 模型综合问答、上传 PDF 抽取与完整翻译</pre>
    </div>
  </section>

  <section id="eval">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">评测</div><h2>41 条回归 + 337 条测试 + 13 项 Copilot 专项</h2></div>
        <p>检索与 Crossref、PDF 抽取、字段抽取、证据数量、对比告警、术语、转化、研究空白、选题、写作、逐句核查、问答支持度、研究库备份、检索计划/分层、引用体检、选题论证卡、论证体检、精读缓存、自定义来源、术语包、检索留痕，v1.0 状态/迁移、Planner/Critic、动态动作、证据账本、三档 worker，v2.0 项目状态/生命周期/API、记忆提升与播种、运行控制、SSE、运行对比、草稿状态/服务/核查/导出、Agent 写作闭环，v3.0 Copilot/引用接地/模型网关/语义核查/前端契约，v5.0 三档判定、跨论文矛盾、引用样式、稿件与 DOCX、投稿前检查、检索订阅与增量索引，v5.1 PDF 导出、矛盾模型复核、订阅并发与金标集工作台，v5.2 跨进程订阅锁、4 主题 × 6 篇金标语料与线上 PDF，v6.0 投稿档案、投稿包、双盲导出、审稿视角与 RIS/EndNote 互操作，v6.1 ORCID/通讯作者、盲审去致谢与审稿意见映射，v6.2 自定义大模型预设、密钥隔离与掩码、运行时生效、地址 SSRF 校验与分用途路由；v6.2.1 真实密钥实测（ModelScope 推理 API · GLM-5.2）Agent/Copilot/语义核查 13/13，v6.3 跨部件一致性审计 42/42；当前 41/41 + 586/586 + 90/90 + 13/13。5 篇真实开放获取 PDF 真机检查 10/10；工作区评测 0.61 秒完成两轮运行、记忆播种、草稿写入、核查、六种导出与运行对比；浏览器 0 console error、390px 横向溢出 0；证据问答引用核验 6/6。</p>
      </div>
      <div class="metrics">
        <div class="metric"><strong>41/41</strong><span>回归用例通过</span></div>
        <div class="metric"><strong>586/586</strong><span>测试通过</span></div>
        <div class="metric"><strong>90/90</strong><span>端到端验收</span></div>
        <div class="metric"><strong>13/13</strong><span>Copilot 专项</span></div>
        <div class="metric"><strong>10/10</strong><span>真机能力检查</span></div>
        <div class="metric"><strong>0</strong><span>内置 API Key</span></div>
      </div>
      <div class="note">这是流程回归集、专项测试与真机能力检查，不是人工质量评测；它证明链路按预期工作，不能证明模型达到专家水平。同一查询缓存复跑约 2–3 ms；检索计划 0.06 ms、论证体检 4.2 ms、引用体检 0.33 ms。完整人工评测方案见源码中的 <code>docs/评测方案.md</code>。</div>
    </div>
  </section>

  <section id="demo">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">演示</div><h2>3 分 8 秒看完整流程</h2></div>
        <p>演示视频为 v0.8 基线：检索计划 → 精读 → 翻译 → 对比 → 创新点 → 成果 → 空白 → 证据问答 → 研究库 → 批量 PDF 解析 → 选题/导师沟通 → 论证体检 → 评测；v1.0 自适应 Agent 与证据账本见上方截图。</p>
      </div>
      <div class="video-card">
        <video controls preload="metadata" poster="cover.jpg">
          <source src="paperpilot_demo.mp4" type="video/mp4" />
          你的浏览器不支持 HTML5 视频。演示视频文件为 <code>paperpilot_demo.mp4</code>。
        </video>
        <div class="video-note"><span>演示视频：1600×900 · 中文旁白与字幕</span><span>静态展示页不运行 FastAPI API；完整 Agent 请按下方运行。</span></div>
      </div>
    </div>
  </section>

  <section id="run">
    <div class="wrap">
      <div class="section-head">
        <div><div class="eyebrow">运行完整版</div><h2>本地 / Docker 两种方式</h2></div>
        <p>无需任何 API key 即可运行离线演示；配置任意 OpenAI-compatible 模型后可增强上传 PDF 抽取。</p>
      </div>
      <div class="run-grid">
        <div class="run-card">
          <h3>本地启动</h3>
          <p>Windows 可双击 <code>start.bat</code>；默认地址 <code>http://127.0.0.1:8765</code>。</p>
          <pre>cd paperpilot
pip install -r requirements.txt
python run.py</pre>
        </div>
        <div class="run-card">
          <h3>Docker</h3>
          <p>适合部署到云服务器；发布包不含任何密钥。</p>
          <pre>cd paperpilot
docker compose up --build</pre>
        </div>
      </div>
      <div class="note">静态展示页（本页）用于快速了解作品；真实的检索、上传 PDF、证据问答、研究库与转化能力需要运行 FastAPI 服务。研究库保存在当前实例的 <code>data/library.json</code>，适合单机/演示使用。</div>
    </div>
  </section>
</main>

<footer>
  <div class="wrap" style="display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;width:100%">
    <span>PaperPilot 8.2.0 · 可交付稿 · 投稿包 · 自配模型 · 可信核查</span>
    <span><a href="{{GITHUB}}" target="_blank" rel="noopener">GitHub 源码</a> · 静态展示页</span>
  </div>
</footer>
</body>
</html>
"""


if __name__ == "__main__":
    main()

