/* Subscription tracking drawer (v5.1): saved searches, manual run, last diff. */
(() => {
  const state = { items: [], index: null, scheduler: null, projects: [] };

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = null; }
    if (!response.ok) throw new Error((body && (body.detail || body.error)) || `HTTP ${response.status}`);
    return body;
  }

  const INTERVALS = [[24, "每天"], [168, "每周"], [720, "每月"]];
  const SOURCES = [["arxiv", "arXiv"], ["crossref", "Crossref"], ["europepmc", "Europe PMC"],
                   ["doaj", "DOAJ"], ["hal", "HAL"], ["demo", "内置演示语料"]];

  function resultLine(item) {
    const result = item.last_result || {};
    if (!result.new && !result.at) return `<span class="muted small">尚未执行</span>`;
    return `<span class="tk-pills">
      <span class="tk-pill">新增 ${result.new ?? 0}</span>
      <span class="tk-pill tk-conflict">冲突 ${result.conflict ?? 0}</span>
      <span class="tk-pill">支持 ${result.support ?? 0}</span>
      <span class="tk-pill">方法 ${result.method ?? 0}</span>
      <span class="tk-pill muted">无关 ${result.irrelevant ?? 0}</span>
    </span>`;
  }

  function render(container) {
    const items = state.items || [];
    const index = state.index || {};
    const scheduler = state.scheduler || {};
    container.innerHTML = `
      <div class="ms-backdrop" data-tracking-close></div>
      <aside class="ms-drawer" data-tracking-drawer role="dialog" aria-label="订阅跟踪" aria-modal="true">
        <header class="ms-head">
          <div>
            <p class="eyebrow">持续研究</p>
            <h2>检索订阅 · 增量索引 · 差异定位</h2>
          </div>
          <button class="btn btn-ghost btn-small" type="button" data-tracking-close>关闭</button>
        </header>

        <section class="ms-section">
          <h3>新建订阅</h3>
          <div class="tk-form">
            <input class="ms-input" data-tracking-query placeholder="检索关键词，例如 efficient LLM inference quantization" maxlength="160" />
            <div class="ms-inline">
              <select class="ms-input" data-tracking-source>
                ${SOURCES.map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}
              </select>
              <select class="ms-input" data-tracking-interval>
                ${INTERVALS.map(([hours, label]) => `<option value="${hours}">${label}</option>`).join("")}
              </select>
            </div>
            <select class="ms-input" data-tracking-project>
              <option value="">不关联项目</option>
              ${(state.projects || []).map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}
            </select>
            <button class="btn btn-primary btn-small" type="button" data-tracking-create>创建订阅</button>
          </div>
          <p class="muted small" data-tracking-status></p>
        </section>

        <section class="ms-section">
          <h3>已有订阅</h3>
          <div data-tracking-list>
            ${items.length ? items.map((item) => `
              <article class="tk-item" data-tracking-item="${esc(item.id)}">
                <div class="tk-item-head">
                  <b>${esc(item.label || item.query)}</b>
                  <span class="muted small">${esc(item.query)} · ${esc(item.source)}</span>
                  <span class="muted small">下次 ${esc(item.next_run_at || "—")}</span>
                </div>
                <div class="tk-item-body">${resultLine(item)}</div>
                ${item.last_error ? `<p class="tk-error small">${esc(item.last_error)}</p>` : ""}
                <div class="tk-actions">
                  <button class="btn btn-ghost btn-small" type="button" data-tracking-run="${esc(item.id)}">立即执行</button>
                  <button class="btn btn-ghost btn-small" type="button" data-tracking-toggle="${esc(item.id)}">${item.enabled ? "停用" : "启用"}</button>
                  <button class="link-btn" type="button" data-tracking-delete="${esc(item.id)}">删除</button>
                </div>
              </article>`).join("") : `<p class="muted small">还没有订阅。上面的表单可以创建一个。</p>`}
          </div>
        </section>

        <section class="ms-section">
          <h3>增量索引</h3>
          <p class="muted small">
            已索引 ${index.total || 0} 篇（上限 ${index.max || 0}）· 关联项目 ${index.linked_projects || 0} 篇
            · 最近同步 ${esc(index.updated_at || "—")}
          </p>
          <p class="muted small">
            后台调度：${scheduler.running ? "运行中" : (scheduler.enabled ? "已启用未运行" : "未启用")}
            · 间隔 ${scheduler.tick_seconds || 60}s
            ${scheduler.last_tick ? `· 上次 ${esc(scheduler.last_tick)}` : ""}
            ${scheduler.last_error ? `· <span class="tk-error">${esc(scheduler.last_error)}</span>` : ""}
          </p>
          <p class="muted small">默认关闭自动执行（环境变量 PAPERPILOT_SCHEDULER=1 开启）；手动"立即执行"随时可用。</p>
        </section>
      </aside>`;
  }

  async function load(container) {
    try {
      const [data, projects] = await Promise.all([
        api("/api/subscriptions"),
        api("/api/projects").catch(() => ({ projects: [] })),
      ]);
      state.items = data.items || [];
      state.index = data.index || {};
      state.scheduler = data.scheduler || {};
      state.projects = projects.projects || [];
      render(container);
    } catch (error) {
      const node = container.querySelector("[data-tracking-status]");
      if (node) node.textContent = `加载失败：${error.message}`;
    }
  }

  function status(container, text) {
    const node = container.querySelector("[data-tracking-status]");
    if (node) node.textContent = text;
  }

  async function open() {
    const container = document.createElement("div");
    container.className = "ms-root";
    container.dataset.trackingRoot = "1";
    document.body.appendChild(container);
    container.innerHTML = `<div class="ms-backdrop"></div><aside class="ms-drawer"><p class="muted">加载订阅…</p></aside>`;

    container.addEventListener("click", async (event) => {
      const target = event.target;
      if (target.closest("[data-tracking-close]")) { close(); return; }
      if (target.closest("[data-tracking-create]")) {
        const query = (container.querySelector("[data-tracking-query]")?.value || "").trim();
        if (!query) { status(container, "请填写检索关键词。"); return; }
        status(container, "创建中…");
        try {
          await api("/api/subscriptions", { method: "POST", body: JSON.stringify({
            query,
            source: container.querySelector("[data-tracking-source]")?.value || "arxiv",
            interval_hours: Number(container.querySelector("[data-tracking-interval]")?.value || 168),
            project_id: container.querySelector("[data-tracking-project]")?.value || "",
          }) });
          await load(container);
          status(container, "已创建");
        } catch (error) { status(container, `创建失败：${error.message}`); }
        return;
      }
      const run = target.closest("[data-tracking-run]");
      if (run) {
        status(container, "执行中…");
        try {
          const result = await api(`/api/subscriptions/${encodeURIComponent(run.dataset.trackingRun)}/run`,
                                   { method: "POST" });
          await load(container);
          status(container, `本次新增 ${result.result?.new ?? 0} 篇，冲突 ${result.result?.conflict ?? 0} 篇`);
        } catch (error) { status(container, `执行失败：${error.message}`); }
        return;
      }
      const toggle = target.closest("[data-tracking-toggle]");
      if (toggle) {
        const item = state.items.find((row) => row.id === toggle.dataset.trackingToggle);
        try {
          await api(`/api/subscriptions/${encodeURIComponent(item.id)}`,
                    { method: "PATCH", body: JSON.stringify({ enabled: !item.enabled }) });
          await load(container);
        } catch (error) { status(container, `操作失败：${error.message}`); }
        return;
      }
      const remove = target.closest("[data-tracking-delete]");
      if (remove) {
        try {
          await api(`/api/subscriptions/${encodeURIComponent(remove.dataset.trackingDelete)}`,
                    { method: "DELETE" });
          await load(container);
        } catch (error) { status(container, `删除失败：${error.message}`); }
      }
    });

    await load(container);
  }

  function close() {
    document.querySelectorAll("[data-tracking-root]").forEach((node) => node.remove());
  }

  window.PaperPilotTracking = { open, close };
})();
