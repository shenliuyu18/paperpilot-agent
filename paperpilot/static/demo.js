/* One-click demo: a complete offline research run, orchestrated from the UI.
 *
 * Everything here goes through the public API; no privileged endpoint, no
 * hidden fixtures. If a step fails the tour says so and stops — it never
 * renders a success state it did not observe.
 */
(function () {
  "use strict";

  const TOPIC = "面向存算一体的低功耗 ADC 设计";
  const PAPER_LIMIT = 3;
  const POLL_MS = 400;
  const TIMEOUT_MS = 120000;

  const STEPS = [
    { key: "search", tools: ["search"], label: "检索与分层", detail: "从演示语料里选出直接相关的论文" },
    { key: "read", tools: ["read"], label: "精读与抽取", detail: "章节、指标与逐字证据引句" },
    { key: "compare", tools: ["compare"], label: "横向对比", detail: "可比性判定与共识/分歧" },
    { key: "topic", tools: ["topic"], label: "选题", detail: "带可行性的候选问题" },
    { key: "write", tools: ["draft", "write"], label: "写作", detail: "按缺口写草稿块并绑定证据" },
    { key: "review", tools: ["review"], label: "核查", detail: "逐块三档支持判定" },
    { key: "report", tools: ["report"], label: "研究简报", detail: "可下载的完整简报" },
  ];

  let busy = false;
  let timer = null;

  function pp() {
    return window.PaperPilot || null;
  }

  function esc(value) {
    const helper = pp()?.esc;
    if (typeof helper === "function") return helper(value);
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function toast(message, isError = false) {
    const helper = pp()?.toast;
    if (typeof helper === "function") helper(message, isError);
  }

  async function call(path, options) {
    const api = pp()?.api;
    if (typeof api !== "function") throw new Error("页面尚未就绪，请刷新后重试。");
    return api(path, options);
  }

  function tourRoot() {
    return document.getElementById("demoTour");
  }

  function renderTour(state) {
    const root = tourRoot();
    if (!root) return;
    const rows = STEPS.map((step) => {
      const row = state.steps[step.key] || "waiting";
      const mark = row === "done" ? "✓" : row === "active" ? "•" : row === "error" ? "!" : row === "skipped" ? "–" : "○";
      const suffix = row === "skipped" ? "<i class=\"tour-skip\">本次未触发</i>" : "";
      return `<li class="tour-step ${row}"><span class="tour-mark">${mark}</span>
        <span class="tour-copy"><strong>${esc(step.label)}${suffix}</strong><em>${esc(step.detail)}</em></span></li>`;
    }).join("");
    root.hidden = false;
    root.innerHTML = `
      <div class="tour-head">
        <div>
          <div class="eyebrow">一键演示 · 离线规则模式</div>
          <strong class="tour-title">${esc(state.title || "正在准备…")}</strong>
        </div>
        <button class="icon-btn" type="button" data-tour-close aria-label="关闭演示">关闭</button>
      </div>
      <ol class="tour-steps">${rows}</ol>
      <div class="tour-foot">${state.foot || ""}</div>
    `;
    const close = root.querySelector("[data-tour-close]");
    if (close) close.addEventListener("click", () => { root.hidden = true; });
  }

  function setFoot(text, tone = "") {
    const foot = tourRoot()?.querySelector(".tour-foot");
    if (!foot) return;
    foot.className = `tour-foot ${tone}`;
    foot.innerHTML = text;
  }

  function allWaiting() {
    return Object.fromEntries(STEPS.map((step) => [step.key, "waiting"]));
  }

  function markSteps(steps, tools) {
    let activeAssigned = false;
    return STEPS.reduce((acc, step) => {
      if (step.tools.some((tool) => tools.includes(tool))) {
        acc[step.key] = "done";
      } else if (!activeAssigned) {
        acc[step.key] = "active";
        activeAssigned = true;
      } else {
        acc[step.key] = "waiting";
      }
      return acc;
    }, { ...steps });
  }

  function doneTools(run) {
    return (run.steps || [])
      .filter((step) => step && step.status === "done")
      .map((step) => String(step.tool || ""));
  }

  async function runDemo() {
    if (busy) return;
    const button = document.getElementById("demoStartBtn");
    busy = true;
    if (button) button.disabled = true;
    const state = { title: "正在准备演示项目…", steps: allWaiting(), foot: "" };
    renderTour(state);
    try {
      state.steps.search = "active";
      state.foot = "第 1/4 步：检索演示语料";
      renderTour(state);

      const search = await call("/api/search", {
        method: "POST",
        body: JSON.stringify({ query: TOPIC, limit: 5, source: "demo" }),
      });
      const papers = (search.papers || []).slice(0, PAPER_LIMIT);
      if (!papers.length) throw new Error("演示语料没有返回结果，已停止（不会伪造产出）。");

      state.title = "正在创建研究项目…";
      state.foot = "第 2/4 步：创建项目并放入引用池";
      renderTour(state);
      const created = await call("/api/projects", {
        method: "POST",
        body: JSON.stringify({ title: `演示：${TOPIC}`, question: TOPIC }),
      });
      const projectId = created.project?.id;
      if (!projectId) throw new Error("创建项目失败：服务端没有返回项目 ID。");

      const pool = await call(`/api/projects/${encodeURIComponent(projectId)}/papers`, {
        method: "POST",
        body: JSON.stringify({ paper_ids: papers.map((paper) => paper.id) }),
      });
      const pooled = Number(pool.added || 0);
      if (!pooled) throw new Error("论文没有进入引用池，已停止。");

      state.title = "正在运行自适应研究 Agent…";
      state.foot = `第 3/4 步：${pooled} 篇论文已入池，启动 auto 运行（模型预算 0）`;
      renderTour(state);
      const started = await call("/api/agent/runs", {
        method: "POST",
        body: JSON.stringify({
          goal: TOPIC,
          source: "demo",
          mode: "auto",
          project_id: projectId,
          seed_memory: true,
          max_llm_calls: 0,
          max_actions: 14,
        }),
      });
      const runId = started.run?.id;
      if (!runId) throw new Error("运行没有启动成功。");

      const run = await waitForRun(runId, (snapshot) => {
        const tools = doneTools(snapshot);
        state.steps = markSteps(allWaiting(), tools);
        state.foot = `第 4/4 步：已执行 ${snapshot.action_count || (snapshot.steps || []).length} 个动作`;
        renderTour(state);
      });

      const observed = doneTools(run);
      state.steps = STEPS.reduce((acc, step) => {
        acc[step.key] = step.tools.some((tool) => observed.includes(tool)) ? "done" : "skipped";
        return acc;
      }, {});
      const project = await call(`/api/projects/${encodeURIComponent(projectId)}`);
      const counts = project.project?.counts || {};
      const evidence = (run.evidence || []).length || counts.evidence || 0;
      state.title = "演示完成";
      state.foot = `
        <div class="tour-result">
          <div><span>动作</span><strong>${esc(run.action_count || (run.steps || []).length)}</strong></div>
          <div><span>证据</span><strong>${esc(evidence)}</strong></div>
          <div><span>草稿块</span><strong>${esc(counts.draft_blocks || 0)}</strong></div>
          <div><span>引用池</span><strong>${esc(counts.papers || pooled)}</strong></div>
        </div>
        <button class="btn btn-primary btn-small" type="button" data-tour-open>打开这个项目</button>
        <span class="tour-hint">简报、证据账本与草稿都在项目里；草稿块可回原文核对。</span>`;
      renderTour(state);
      const openBtn = tourRoot()?.querySelector("[data-tour-open]");
      if (openBtn) openBtn.addEventListener("click", () => openProject(projectId));
      await openProject(projectId);
      tourRoot()?.classList.add("compact");
      markLastDemo(projectId);
    } catch (error) {
      const message = error?.message || String(error);
      const steps = { ...state.steps };
      const activeKey = STEPS.find((step) => steps[step.key] === "active")?.key;
      steps[activeKey || "search"] = "error";
      renderTour({ title: "演示未完成", steps, foot: "" });
      setFoot(`<strong>已停止：</strong>${esc(message)}<br><span class="tour-hint">没有生成任何“成功”状态；可以修正后重试。</span>`, "error");
      toast(message, true);
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  function waitForRun(runId, onTick) {
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const tick = async () => {
        try {
          const data = await call(`/api/agent/runs/${encodeURIComponent(runId)}`);
          const run = data.run || {};
          if (typeof onTick === "function") onTick(run);
          if (run.status === "completed") {
            resolve(run);
            return;
          }
          if (["failed", "cancelled", "interrupted"].includes(run.status)) {
            reject(new Error(`运行${run.status === "failed" ? "失败" : run.status === "cancelled" ? "被取消" : "被中断"}，没有产出简报。`));
            return;
          }
          if (Date.now() - started > TIMEOUT_MS) {
            reject(new Error("运行超时（120 秒），已停止等待。"));
            return;
          }
          timer = window.setTimeout(tick, POLL_MS);
        } catch (error) {
          reject(error);
        }
      };
      tick();
    });
  }

  async function openProject(projectId) {
    const helper = pp();
    if (!helper) return;
    try {
      await helper.switchTab("projects");
      if (window.PaperPilotProjects?.open) {
        await window.PaperPilotProjects.open(projectId);
      }
      markLastDemo(projectId);
      const tabs = document.getElementById("tabs");
      if (tabs && typeof tabs.scrollIntoView === "function") {
        tabs.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  function markLastDemo(projectId) {
    try {
      window.localStorage.setItem("paperpilot.demoProject", projectId);
    } catch (error) {
      /* private mode: the "open last demo" button simply stays unavailable */
    }
  }

  function lastDemo() {
    try {
      return window.localStorage.getItem("paperpilot.demoProject") || "";
    } catch (error) {
      return "";
    }
  }

  async function openLastDemo() {
    const projectId = lastDemo();
    if (!projectId) {
      toast("还没有演示项目，先点「一键跑完一次完整研究」。");
      return;
    }
    try {
      await call(`/api/projects/${encodeURIComponent(projectId)}`);
      await openProject(projectId);
    } catch (error) {
      try { window.localStorage.removeItem("paperpilot.demoProject"); } catch (ignored) { /* ignore */ }
      toast("上次的演示项目已不存在，先重新跑一次。", true);
    }
  }

  function openModelSettings() {
    const button = document.getElementById("modelBtn");
    if (button) button.click();
  }

  function bind() {
    const start = document.getElementById("demoStartBtn");
    if (start) start.addEventListener("click", runDemo);
    const last = document.getElementById("demoOpenBtn");
    if (last) last.addEventListener("click", openLastDemo);
    const model = document.getElementById("demoModelBtn");
    if (model) model.addEventListener("click", openModelSettings);
    if (lastDemo() && last) last.classList.remove("is-empty");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.PaperPilotDemo = { run: runDemo, openLast: openLastDemo, steps: STEPS };
})();