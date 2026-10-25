(() => {
  "use strict";

  const state = {
    projects: [],
    project: null,
    tab: "overview",
    compare: null,
    focus: null,
    autoAudit: false,
    comparePreselect: [],
    autoCompare: false,
  };
  const boundPanels = new WeakSet();

  const stageLabels = {
    exploring: "探索",
    reading: "精读",
    writing: "写作",
    review: "核查",
    done: "完成",
  };
  const runStatusLabels = {
    queued: ["排队", "soft"],
    running: ["执行中", "amber"],
    paused: ["已暂停", "amber"],
    awaiting_approval: ["等待确认", "green"],
    completed: ["完成", "green"],
    cancelled: ["已取消", "muted"],
    failed: ["失败", "pink"],
    interrupted: ["中断", "pink"],
  };
  const statusLabels = { active: "进行中", archived: "已归档" };
  const tabs = [
    ["overview", "概览"],
    ["assistant", "Copilot"],
    ["runs", "运行"],
    ["evidence", "证据"],
    ["papers", "引用池"],
    ["draft", "草稿"],
    ["compare", "对比"],
  ];

  function api(path, options = {}) {
    return window.PaperPilot.api(path, options);
  }
  function esc(value) {
    return window.PaperPilot.esc(value);
  }
  function toast(message, isError = false) {
    window.PaperPilot.toast(message, isError);
  }
  function badge(text, kind = "soft") {
    return `<span class="badge badge-${kind}">${esc(text)}</span>`;
  }
  function runBadge(status) {
    const [label, kind] = runStatusLabels[status] || [status || "未知", "muted"];
    return badge(label, kind);
  }
  function stageLabel(stage) {
    return stageLabels[stage] || stage || "探索";
  }
  function statusLabel(status) {
    return statusLabels[status] || status || "进行中";
  }
  function panelOf(panel) {
    return panel || window.PaperPilot.panel;
  }

  async function flushDraft() {
    if (!window.PaperPilotDraft?.isDirty?.()) return true;
    return window.PaperPilotDraft.flush();
  }

  async function render(panel, projectId = "") {
    panel = panelOf(panel);
    if (!panel) return;
    if (projectId) return open(panel, projectId);
    if (!(await flushDraft())) return;
    try {
      const data = await api("/api/projects");
      state.projects = data.projects || [];
    } catch (error) {
      panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
      return;
    }
    panel.innerHTML = `
      <div class="projects-shell">
        <section class="projects-head card">
          <div>
            <div class="eyebrow">研究工作区</div>
            <h2>研究项目</h2>
            <p class="muted">把多轮研究、证据、决策与草稿放在一个持续空间里。</p>
          </div>
          <div class="projects-head-actions">
            <button class="btn btn-ghost" type="button" data-tracking-open>订阅跟踪</button>
            <button class="btn btn-primary" type="button" data-project-new>新建项目</button>
          </div>
        </section>
        <div class="project-grid">
          ${state.projects.length ? state.projects.map((project) => `
            <button class="project-card card" type="button" data-project-open="${esc(project.id)}">
              <div class="project-card-top">
                <span class="eyebrow">${esc(stageLabel(project.stage))}</span>
                ${project.active_run_id ? badge("运行中", "green") : badge("空闲", "soft")}
              </div>
              <h3>${esc(project.title)}</h3>
              <p>${esc(project.brief?.question || "")}</p>
              <div class="project-counts">
                <span>${project.counts?.runs || 0} 轮</span>
                <span>${project.counts?.evidence || 0} 证据</span>
                <span>${project.counts?.papers || 0} 引用池</span>
                <span>${project.counts?.draft_blocks || 0} 草稿块</span>
              </div>
            </button>
          `).join("") : `<div class="empty-state"><h2>还没有项目</h2><p>新建一个项目，从第一轮研究开始。</p></div>`}
        </div>
      </div>
    `;
  }

  async function open(panel, projectId, resetTab = true) {
    if (typeof panel === "string") {
      projectId = panel;
      panel = window.PaperPilot.panel;
    }
    panel = panelOf(panel);
    if (!panel || !projectId) return;
    if (!(await flushDraft())) return;
    try {
      const data = await api(`/api/projects/${encodeURIComponent(projectId)}`);
      state.project = data.project;
    } catch (error) {
      toast(error.message, true);
      state.project = null;
      return render(panel);
    }
    if (resetTab) {
      state.tab = "overview";
      state.focus = null;
      state.comparePreselect = [];
      state.autoCompare = false;
    }
    await renderDetail(panel);
  }

  /**
   * Rebuild the project detail view.
   *
   * The draft workbench lives inside this view, so it is destroyed on every
   * re-render. The workbench therefore gets a chance to save pending text
   * first; when it cannot, the view is left alone instead of dropping the
   * text. Returns false when the re-render was aborted.
   */
  async function renderDetail(panel) {
    if (!(await flushDraft())) return false;
    const project = state.project;
    if (!project) return render(panel);
    panel.innerHTML = `
      <div class="project-detail">
        <section class="project-hero card">
          <div class="project-hero-main">
            <div class="eyebrow">${esc(stageLabel(project.stage))} · ${esc(statusLabel(project.status))}</div>
            <h2>${esc(project.title)}</h2>
            <p>${esc(project.brief?.question || "")}</p>
            <div class="project-meta">
              ${badge(`${project.counts?.runs || 0} 轮运行`, "soft")}
              ${badge(`${project.counts?.evidence || 0} 条证据`, "muted")}
              ${badge(`${project.counts?.papers || 0} 篇引用池`, project.counts?.papers ? "green" : "muted")}
              ${project.active_run_id ? badge("有运行正在进行", "green") : badge("空闲", "muted")}
              ${project.updated_at ? `<span class="muted small">更新 ${esc(project.updated_at)}</span>` : ""}
            </div>
          </div>
          <div class="project-actions">
            <button class="btn btn-primary" type="button" data-project-run>启动新一轮</button>
            <button class="btn btn-ghost" type="button" data-project-copilot>Copilot</button>
            <button class="btn btn-ghost" type="button" data-project-refresh>刷新</button>
            <button class="btn btn-ghost" type="button" data-project-back>返回列表</button>
          </div>
        </section>
        <nav class="project-tabs" aria-label="项目视图">
          ${tabs
            .map(([key, label]) => `<button class="tab ${state.tab === key ? "active" : ""}" type="button" data-project-tab="${key}">${label}</button>`)
            .join("")}
        </nav>
        <div id="projectTabBody"></div>
      </div>
    `;
    renderTab(panel);
    return true;
  }

  async function renderTab(panel) {
    const body = panel.querySelector("#projectTabBody");
    const project = state.project;
    if (!body || !project) return;
    if (state.tab === "overview") {
      renderOverview(body, project);
      return;
    }
    if (state.tab === "assistant") {
      await renderAssistant(body, project);
      return;
    }
    if (state.tab === "runs") {
      await renderRuns(body, project);
      return;
    }
    if (state.tab === "evidence") {
      await renderEvidence(body, project);
      return;
    }
    if (state.tab === "papers") {
      await renderPapers(body, project);
      return;
    }
    if (state.tab === "draft") {
      await renderDraft(body, project);
      return;
    }
    if (state.tab === "compare") {
      renderCompare(body, project);
    }
  }

  async function renderAssistant(body, project) {
    body.innerHTML = `<section class="card assistant-shell"><div id="assistantMount"></div></section>`;
    const mount = body.querySelector("#assistantMount");
    if (window.PaperPilotAssistant) {
      await window.PaperPilotAssistant.mount(mount, project.id);
      return;
    }
    mount.innerHTML = `<p class="muted">助手模块未加载，请刷新页面。</p>`;
  }

  function flash(element) {
    if (!element) return;
    element.scrollIntoView({ block: "center", behavior: "smooth" });
    element.classList.add("focus-flash");
    setTimeout(() => element.classList.remove("focus-flash"), 1800);
  }

  function findRunRow(body, runId) {
    return Array.from(body.querySelectorAll("[data-open-run]"))
      .find((element) => element.dataset.openRun === runId) || null;
  }

  function consumeFocus(body, tab) {
    if (!state.focus || state.focus.tab !== tab) return;
    const id = state.focus.id;
    if (tab === "overview") {
      flash(body.querySelector(".project-overview") || body);
    } else if (tab === "evidence") {
      flash(Array.from(body.querySelectorAll("[data-evidence-id]"))
        .find((element) => element.dataset.evidenceId === id));
    } else if (tab === "runs") {
      flash(findRunRow(body, id));
    }
    state.focus = null;
  }

  function renderOverview(body, project) {
    const brief = project.brief || {};
    const constraints = (brief.constraints || []).filter(Boolean);
    const recent = project.recent_runs || [];
    body.innerHTML = `
      <section class="card project-overview">
        <div class="card-head"><h3>研究简报</h3><span class="muted small">${project.updated_at ? `更新 ${esc(project.updated_at)}` : ""}</span></div>
        <p>${esc(brief.scope || "尚未填写范围。")}</p>
        ${constraints.length ? `<div class="project-constraints">${constraints.map((item) => badge(item, "amber")).join("")}</div>` : ""}
        ${brief.success_criteria ? `<p class="muted small">成功标准：${esc(brief.success_criteria)}</p>` : ""}
        <div class="project-counts">
          <span>${project.counts?.runs || 0} 轮运行</span>
          <span>${project.counts?.evidence || 0} 条证据</span>
          <span>${project.counts?.papers || 0} 篇引用池</span>
          <span>${project.counts?.open_questions || 0} 个开放问题</span>
          <span>${project.draft_summary?.blocks || 0} 个草稿块</span>
        </div>
        <h3>最近运行</h3>
        ${recent.length ? recent.map((run) => `
          <div class="project-run-row">
            <button class="link-btn" type="button" data-open-run="${esc(run.id)}">${esc(run.goal)}</button>
            <div class="project-run-meta">
              ${runBadge(run.status)}
              <span class="muted small">${run.action_count || 0} 动作 · ${run.evidence_count || 0} 证据${run.updated_at ? ` · ${esc(run.updated_at)}` : ""}</span>
            </div>
          </div>
        `).join("") : "<p class='muted'>还没有运行。</p>"}
      </section>
    `;
    consumeFocus(body, "overview");
  }

  async function renderRuns(body, project) {
    body.innerHTML = `<section class="card"><div class="loading-line"></div><p class="muted">正在读取运行…</p></section>`;
    let runs = [];
    try {
      const data = await api(`/api/projects/${encodeURIComponent(project.id)}/runs`);
      runs = data.runs || [];
    } catch (error) {
      body.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      return;
    }
    body.innerHTML = `
      <section class="card">
        <div class="card-head"><h3>运行</h3><span class="muted small">${runs.length} 次</span></div>
        ${runs.length ? runs.map((run) => `
          <div class="project-run-row">
            <button class="link-btn" type="button" data-open-run="${esc(run.id)}">${esc(run.goal)}</button>
            <div class="project-run-meta">
              ${runBadge(run.status)}
              <span class="muted small">${run.action_count || 0} 动作 · ${run.evidence_count || 0} 证据 · ${esc(run.mode || "checkpoint")}${run.updated_at ? ` · ${esc(run.updated_at)}` : ""}</span>
              <label class="project-compare-pick"><input type="checkbox" data-compare-run="${esc(run.id)}" /> 对比</label>
            </div>
          </div>
        `).join("") : "<p class='muted'>还没有运行。</p>"}
        <div class="project-compare-bar">
          <span class="muted small" id="projectCompareHint">已选 0 / 3</span>
          <button class="btn btn-ghost" type="button" data-project-compare>对比已选运行</button>
        </div>
        <p class="muted small">勾选 2–3 个运行后对比动作、证据与问题差异。</p>
      </section>
    `;
    if (state.comparePreselect.length) {
      state.comparePreselect.forEach((runId) => {
        const box = Array.from(body.querySelectorAll("[data-compare-run]"))
          .find((element) => element.dataset.compareRun === runId);
        if (box) box.checked = true;
      });
      state.comparePreselect = [];
      updateCompareHint(body);
      if (state.autoCompare) {
        state.autoCompare = false;
        setTimeout(() => body.querySelector("[data-project-compare]")?.click(), 120);
      }
    }
    consumeFocus(body, "runs");
  }

  async function renderEvidence(body, project) {
    body.innerHTML = `<section class="card"><div class="loading-line"></div><p class="muted">正在读取项目证据…</p></section>`;
    let items = [];
    try {
      const data = await api(`/api/projects/${encodeURIComponent(project.id)}/memory?kind=evidence`);
      items = data.items || [];
    } catch (error) {
      body.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      return;
    }
    body.innerHTML = `
      <section class="card">
        <div class="card-head"><h3>项目证据</h3><span class="muted small">${items.length} 条</span></div>
        ${items.length ? items.map((item) => `
          <div class="project-evidence" data-evidence-id="${esc(item.id || "")}">
            <strong>${esc(item.title || item.paper_id || "证据")}</strong>
            ${badge(item.verified ? "已核对" : "待核对", item.verified ? "green" : "amber")}
            <blockquote>${esc(item.quote)}</blockquote>
            <div class="muted small">${esc(item.section || "原文")} · p.${esc(item.page ?? "—")}${item.claim ? ` · ${esc(item.claim)}` : ""}</div>
          </div>
        `).join("") : "<p class='muted'>还没有证据。启动一轮运行后，证据会自动进入项目记忆。</p>"}
      </section>
    `;
    consumeFocus(body, "evidence");
  }

  async function renderPapers(body, project) {
    body.innerHTML = `<section class="card"><div class="loading-line"></div><p class="muted">正在读取引用池…</p></section>`;
    let items = [];
    try {
      const data = await api(`/api/projects/${encodeURIComponent(project.id)}/memory?kind=papers&limit=100`);
      items = data.items || [];
    } catch (error) {
      body.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      return;
    }
    body.innerHTML = `
      <section class="card">
        <div class="card-head"><h3>引用池</h3><span class="muted small">${items.length} 篇</span></div>
        <p class="muted small">引用池里的论文可以直接在草稿里插入引用。检索结果、我的库和精读页都有“加入项目”按钮。</p>
        ${items.length ? `<div class="project-paper-list">${items.map((paper) => {
          const id = String(paper.id || "");
          const authors = Array.isArray(paper.authors) ? paper.authors : [];
          const meta = [
            authors.slice(0, 2).join(", ") + (authors.length > 2 ? " 等" : ""),
            paper.year || "",
            paper.venue || "",
          ].filter(Boolean).join(" · ");
          return `
            <div class="project-paper-row" data-paper-id="${esc(id)}">
              <div style="min-width:0">
                <strong>${esc(paper.title || id || "未命名论文")}</strong>
                <div class="muted small">${esc(meta || "元数据待补全")}</div>
              </div>
              <button class="btn btn-small btn-ghost" type="button" data-paper-remove="${esc(id)}">移除</button>
            </div>
          `;
        }).join("")}</div>` : `<p class="muted">引用池还是空的。在检索结果、我的库或精读页点击“加入项目”，论文就会出现在这里。</p>`}
      </section>
    `;
  }

  async function removePaperFromPool(projectId, paperId) {
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}`, {
        method: "DELETE",
      });
      toast("已从引用池移除");
      await open(window.PaperPilot.panel, projectId, false);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function renderDraft(body, project) {
    body.innerHTML = `<section class="card project-draft"><div id="draftStudio"></div></section>`;
    const studio = body.querySelector("#draftStudio");
    if (window.PaperPilotDraft) {
      await window.PaperPilotDraft.mount(studio, project.id);
      if (state.focus?.tab === "draft") {
        const blockId = state.focus.id;
        state.focus = null;
        setTimeout(() => {
          if (window.PaperPilotDraft.focus) window.PaperPilotDraft.focus(blockId);
        }, 160);
      }
      if (state.autoAudit) {
        state.autoAudit = false;
        setTimeout(() => studio.querySelector("[data-draft-audit]")?.click(), 240);
      }
      return;
    }
    studio.innerHTML = `
      <div class="card-head"><h3>项目草稿</h3><span class="muted small">${project.draft_summary?.blocks || 0} 个块</span></div>
      <div class="agent-empty">草稿工作台将在下一版本接入；当前项目的草稿数据已经保存在后端。</div>
    `;
  }

  function renderCompare(body, project) {
    if (!state.compare) {
      body.innerHTML = `<section class="card"><h3>运行对比</h3><p class="muted">在“运行”页勾选 2–3 个运行，再点击对比。</p></section>`;
      return;
    }
    const result = state.compare;
    const runs = result.runs || [];
    const unique = result.evidence?.unique || {};
    body.innerHTML = `
      <section class="card project-compare">
        <div class="card-head"><h3>运行对比</h3><span class="muted small">${runs.length} 个运行</span></div>
        <div class="compare-table">
          ${runs.map((run) => `
            <div class="compare-column">
              <div class="compare-col-head"><h4>${esc(run.id)}</h4>${runBadge(run.status)}</div>
              <p>${esc(run.goal)}</p>
              <dl class="compare-metrics">
                <div><dt>动作</dt><dd>${run.action_count || 0}</dd></div>
                <div><dt>证据</dt><dd>${run.evidence_count || 0}</dd></div>
                <div><dt>已核对</dt><dd>${run.verified_count || 0}</dd></div>
                <div><dt>已解决问题</dt><dd>${run.resolved_question_count || 0}</dd></div>
                <div><dt>开放问题</dt><dd>${run.open_question_count || 0}</dd></div>
                <div><dt>报告字符</dt><dd>${run.report_chars || 0}</dd></div>
              </dl>
            </div>
          `).join("")}
        </div>
        <h3>证据差异</h3>
        <p>共有 ${result.evidence?.shared?.length || 0} 条；${Object.entries(unique).map(([id, keys]) => `${esc(id)} 独有 ${keys.length} 条`).join("；") || "没有独有证据。"}</p>
        <h3>动作序列</h3>
        <div class="compare-sequences">
          ${runs.map((run) => `<div><strong>${esc(run.id)}</strong><span>${esc((result.tool_sequences?.[run.id] || []).join(" → ") || "暂无动作")}</span></div>`).join("")}
        </div>
      </section>
    `;
  }

  async function createProject(panel) {
    const title = (window.prompt("项目标题", "") || "").trim();
    if (!title) return;
    if (title.length < 2) {
      toast("项目标题至少 2 个字符。", true);
      return;
    }
    const question = (window.prompt("研究问题", "") || "").trim();
    if (!question) return;
    if (question.length < 2) {
      toast("研究问题至少 2 个字符。", true);
      return;
    }
    try {
      const data = await api("/api/projects", {
        method: "POST",
        body: JSON.stringify({ title, question }),
      });
      await open(panel, data.project.id);
      toast("项目已创建");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function startRun(projectId, goalDefault = "") {
    const project = state.project;
    const goal = (window.prompt("这一轮的研究目标", goalDefault || project?.brief?.question || "") || "").trim();
    if (!goal) return;
    if (goal.length < 2) {
      toast("研究目标至少 2 个字符。", true);
      return;
    }
    const modeInput = (window.prompt("自主程度：auto / checkpoint / step", "checkpoint") || "").trim().toLowerCase();
    if (modeInput && !["auto", "checkpoint", "step"].includes(modeInput)) {
      toast("自主程度只能是 auto / checkpoint / step。", true);
      return;
    }
    try {
      const data = await api("/api/agent/runs", {
        method: "POST",
        body: JSON.stringify({
          goal,
          mode: modeInput || "checkpoint",
          project_id: projectId,
          seed_memory: true,
        }),
      });
      toast(`已启动运行 ${data.run.id}`);
      await window.PaperPilot.openAgentRun(data.run.id);
      await window.PaperPilot.switchTab("agent");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function openCopilot(panel) {
    panel = panelOf(panel);
    if (!panel) return;
    try {
      const data = await api("/api/projects");
      const projects = data.projects || [];
      if (!projects.length) {
        await render(panel);
        toast("先创建一个研究项目，Copilot 会在项目内出现。");
        return;
      }
      const active = projects.find((project) => project.active_run_id) || projects[0];
      state.tab = "assistant";
      state.focus = null;
      state.comparePreselect = [];
      state.autoCompare = false;
      await open(panel, active.id, false);
    } catch (error) {
      toast(error.message, true);
      await render(panel);
    }
  }

  async function focusCitation(target = {}) {
    const panel = window.PaperPilot.panel;
    if (!panel) return;
    const tab = target.tab || "overview";
    state.focus = { tab, id: String(target.id || "") };
    state.tab = tab;
    state.compare = null;
    await renderDetail(panel);
  }

  async function handleAssistantAction(type, payload = {}) {
    const panel = window.PaperPilot.panel;
    const project = state.project;
    if (!panel || !project) return;
    if (type === "new_run") {
      await startRun(project.id, payload.goal || "");
      return;
    }
    if (type === "inject_question" || type === "add_constraint") {
      const runId = payload.run_id || project.active_run_id;
      if (!runId) {
        toast("当前没有活动运行，请先启动新一轮。", true);
        return;
      }
      const body = type === "inject_question"
        ? { question: payload.question || "", priority: payload.priority || "high" }
        : { constraint: payload.constraint || "" };
      if (!body.question && !body.constraint) {
        toast("建议内容为空，请手动填写。", true);
        return;
      }
      try {
        await window.PaperPilotLive.control(runId, type, body);
        toast("控制指令已提交，将在当前动作结束后生效");
        await open(panel, project.id, false);
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    if (type === "open_draft" || type === "run_audit") {
      state.focus = { tab: "draft", id: String(payload.block_id || "") };
      state.autoAudit = type === "run_audit";
      state.tab = "draft";
      state.compare = null;
      await renderDetail(panel);
      return;
    }
    if (type === "compare_runs") {
      state.comparePreselect = Array.isArray(payload.run_ids) ? payload.run_ids : [];
      state.autoCompare = true;
      state.tab = "runs";
      state.compare = null;
      await renderDetail(panel);
      return;
    }
    if (type === "open_evidence") {
      focusCitation({ tab: "evidence", id: payload.evidence_id });
    }
  }

  async function switchProjectTab(panel, nextTab) {
    const previous = state.tab;
    state.tab = nextTab;
    const rendered = await renderDetail(panel);
    if (!rendered) {
      // The draft workbench could not save; keep the student where they were
      // instead of moving them away from the text they are writing.
      state.tab = previous;
    }
    return rendered;
  }

  function updateCompareHint(panel) {
    const hint = panel.querySelector("#projectCompareHint");
    if (!hint) return;
    const count = panel.querySelectorAll("[data-compare-run]:checked").length;
    hint.textContent = `已选 ${count} / 3`;
  }

  async function handleClick(panel, event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest("[data-tracking-open]")) {
      window.PaperPilotTracking?.open();
      return;
    }
    const newProject = target.closest("[data-project-new]");
    if (newProject) {
      await createProject(panel);
      return;
    }
    const openProject = target.closest("[data-project-open]");
    if (openProject) {
      await open(panel, openProject.dataset.projectOpen);
      return;
    }
    const back = target.closest("[data-project-back]");
    if (back) {
      state.project = null;
      state.compare = null;
      await render(panel);
      return;
    }
    const refresh = target.closest("[data-project-refresh]");
    if (refresh && state.project) {
      await open(panel, state.project.id, false);
      return;
    }
    const tabButton = target.closest("[data-project-tab]");
    if (tabButton) {
      state.compare = null;
      await switchProjectTab(panel, tabButton.dataset.projectTab || "overview");
      return;
    }
    const runButton = target.closest("[data-project-run]");
    if (runButton && state.project) {
      await startRun(state.project.id);
      return;
    }
    const copilotButton = target.closest("[data-project-copilot]");
    if (copilotButton && state.project) {
      await switchProjectTab(panel, "assistant");
      return;
    }
    const openRun = target.closest("[data-open-run]");
    if (openRun) {
      try {
        await window.PaperPilot.openAgentRun(openRun.dataset.openRun);
        await window.PaperPilot.switchTab("agent");
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    const removePaper = target.closest("[data-paper-remove]");
    if (removePaper && state.project) {
      await removePaperFromPool(state.project.id, removePaper.dataset.paperRemove);
      return;
    }
    const compareButton = target.closest("[data-project-compare]");
    if (compareButton && state.project) {
      const ids = Array.from(panel.querySelectorAll("[data-compare-run]:checked")).map((item) => item.dataset.compareRun);
      if (ids.length < 2 || ids.length > 3) {
        toast("请选择 2–3 个运行。", true);
        return;
      }
      try {
        state.compare = await api(`/api/projects/${encodeURIComponent(state.project.id)}/compare?run_ids=${ids.join(",")}`);
        await switchProjectTab(panel, "compare");
      } catch (error) {
        toast(error.message, true);
      }
    }
  }

  function bind(panel) {
    panel = panelOf(panel);
    if (!panel || boundPanels.has(panel)) return;
    boundPanels.add(panel);
    panel.addEventListener("click", (event) => {
      handleClick(panel, event).catch((error) => toast(error.message, true));
    });
    panel.addEventListener("change", (event) => {
      const box = event.target instanceof Element ? event.target.closest("[data-compare-run]") : null;
      if (!box) return;
      if (!box.checked) {
        updateCompareHint(panel);
        return;
      }
      const checked = panel.querySelectorAll("[data-compare-run]:checked");
      if (checked.length > 3) {
        box.checked = false;
        toast("最多选择 3 个运行。", true);
      }
      updateCompareHint(panel);
    });
  }

  window.PaperPilotProjects = {
    render,
    open,
    bind,
    state,
    openCopilot,
    focusCitation,
    startRun,
    handleAssistantAction,
  };
})();
