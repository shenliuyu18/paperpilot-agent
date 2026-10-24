/* Manuscript panel (v5.0): metadata, reference style, preview, preflight, export. */
(() => {
  const state = { projectId: "", meta: null, styles: [], preview: null, preflight: null, open: false };

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
    if (!response.ok) {
      const detail = (body && (body.detail || body.error)) || `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return body;
  }

  const base = (suffix = "") => `/api/projects/${encodeURIComponent(state.projectId)}${suffix}`;

  function authorRow(author = {}, index = 0) {
    return `
      <div class="ms-author" data-manuscript-author>
        <input class="ms-input" data-author-name placeholder="姓名" maxlength="60" value="${esc(author.name || "")}" aria-label="作者 ${index + 1} 姓名" />
        <input class="ms-input" data-author-affiliation placeholder="单位" maxlength="120" value="${esc(author.affiliation || "")}" aria-label="作者 ${index + 1} 单位" />
        <input class="ms-input" data-author-email placeholder="邮箱（可选）" maxlength="120" value="${esc(author.email || "")}" aria-label="作者 ${index + 1} 邮箱" />
        <input class="ms-input" data-author-orcid placeholder="ORCID（可选）" maxlength="19" value="${esc(author.orcid || "")}" aria-label="作者 ${index + 1} ORCID" />
        <label class="ms-check"><input type="checkbox" data-author-corresponding${author.corresponding ? " checked" : ""} /> 通讯</label>
        <button class="link-btn ms-remove" type="button" data-author-remove title="删除该作者">删除</button>
      </div>`;
  }

  function issueList(items, empty) {
    if (!items || !items.length) return `<p class="muted small">${esc(empty)}</p>`;
    return `<ul class="ms-list">${items.map((item) => `
      <li class="ms-issue ms-${esc(item.level || "info")}">
        <span class="ms-level">${esc(item.level || "info")}</span>
        <span>${esc(item.message)}</span>
      </li>`).join("")}</ul>`;
  }

  function render(container) {
    const meta = state.meta || {};
    const preview = state.preview || { markers: [], references: [], issues: [] };
    const preflight = state.preflight || { items: [], counts: {} };
    const styleOptions = state.styles.map((item) =>
      `<option value="${esc(item.id)}"${item.id === meta.citation_style ? " selected" : ""}>${esc(item.label)}</option>`
    ).join("");
    container.innerHTML = `
      <div class="ms-backdrop" data-manuscript-close></div>
      <aside class="ms-drawer" data-manuscript-drawer role="dialog" aria-label="稿件设置" aria-modal="true">
        <header class="ms-head">
          <div>
            <p class="eyebrow">稿件</p>
            <h2>元数据 · 引用样式 · 投稿前检查</h2>
          </div>
          <button class="btn btn-ghost btn-small" type="button" data-manuscript-close>关闭</button>
        </header>

        <section class="ms-section">
          <h3>基本信息</h3>
          <label class="ms-label">标题 <input class="ms-input" data-manuscript-field="title" maxlength="200" value="${esc(meta.title || "")}" /></label>
          <label class="ms-label">副标题 <input class="ms-input" data-manuscript-field="subtitle" maxlength="200" value="${esc(meta.subtitle || "")}" /></label>
          <label class="ms-label">摘要
            <textarea class="ms-input ms-textarea" data-manuscript-field="abstract" maxlength="2000" rows="5">${esc(meta.abstract || "")}</textarea>
          </label>
          <label class="ms-label">关键词（用逗号分隔） <input class="ms-input" data-manuscript-keywords maxlength="400" value="${esc((meta.keywords || []).join("，"))}" /></label>
          <label class="ms-label">基金（可选） <input class="ms-input" data-manuscript-field="funding" maxlength="300" value="${esc(meta.funding || "")}" /></label>
          <div class="ms-inline">
            <label class="ms-label">参考文献样式
              <select class="ms-input" data-manuscript-field="style">${styleOptions}</select>
            </label>
            <label class="ms-label">语言
              <select class="ms-input" data-manuscript-field="language">
                <option value="zh"${meta.language === "zh" ? " selected" : ""}>中文</option>
                <option value="en"${meta.language === "en" ? " selected" : ""}>English</option>
              </select>
            </label>
          </div>
        </section>

        <section class="ms-section">
          <div class="ms-section-head">
            <h3>作者</h3>
            <button class="link-btn" type="button" data-manuscript-add-author>+ 添加作者</button>
          </div>
          <div data-manuscript-authors>${(meta.authors || []).map(authorRow).join("")}</div>
        </section>

        <section class="ms-section">
          <h3>投稿信息</h3>
          <label class="ms-label">作者贡献（CRediT 或自由文本）
            <textarea class="ms-input ms-textarea" data-manuscript-field="contributions" maxlength="300" rows="2">${esc(meta.contributions || "")}</textarea>
          </label>
          <label class="ms-label">数据可用性
            <textarea class="ms-input ms-textarea" data-manuscript-field="data_availability" maxlength="500" rows="2">${esc(meta.data_availability || "")}</textarea>
          </label>
          <label class="ms-label">代码可用性
            <textarea class="ms-input ms-textarea" data-manuscript-field="code_availability" maxlength="500" rows="2">${esc(meta.code_availability || "")}</textarea>
          </label>
          <label class="ms-label">利益冲突
            <textarea class="ms-input ms-textarea" data-manuscript-field="competing_interests" maxlength="300" rows="2">${esc(meta.competing_interests || "")}</textarea>
          </label>
          <label class="ms-label">致谢（双盲导出会自动移除）
            <textarea class="ms-input ms-textarea" data-manuscript-field="acknowledgments" maxlength="1000" rows="3">${esc(meta.acknowledgments || "")}</textarea>
          </label>
        </section>

        <section class="ms-section">
          <h3>参考文献预览</h3>
          <p class="muted small">文内标记与条目由同一样式引擎生成；共 ${preview.references.length} 条，${preview.markers.length} 处文内引用。</p>
          <div class="ms-refs" data-manuscript-refs>
            ${preview.references.map((row) => `<div class="ms-ref"><span>${esc(row.text)}</span></div>`).join("") || `<p class="muted small">正文还没有引用。先在草稿里绑定论文。</p>`}
          </div>
          ${issueList(preview.issues, "引用字段完整。")}
        </section>

        <section class="ms-section">
          <h3>投稿前检查</h3>
          <p class="muted small">正文 ${preflight.body_words || 0} 词 · 摘要 ${preflight.abstract_words || 0} 词 · 图 ${(preflight.figures || []).length} · 表 ${(preflight.tables || []).length} · 公式 ${preflight.formulas || 0}</p>
          <div data-manuscript-preflight>
            ${issueList(preflight.items, "没有发现待处理项。")}
          </div>
        </section>

        <footer class="ms-foot">
          <button class="btn btn-primary" type="button" data-manuscript-save>保存稿件信息</button>
          <span class="ms-foot-group">
            <button class="btn btn-ghost btn-small" type="button" data-manuscript-export="docx">导出 DOCX</button>
            <button class="btn btn-ghost btn-small" type="button" data-manuscript-export="pdf">导出 PDF</button>
            <button class="btn btn-ghost btn-small" type="button" data-manuscript-export="markdown">导出 Markdown</button>
            <button class="btn btn-ghost btn-small" type="button" data-manuscript-export="latex">导出 LaTeX</button>
          </span>
          <span class="ms-status muted small" data-manuscript-status></span>
        </footer>
      </aside>`;
  }

  function collect(container) {
    const value = (name) => container.querySelector(`[data-manuscript-field="${name}"]`)?.value ?? "";
    const authors = [...container.querySelectorAll("[data-manuscript-author]")].map((row) => ({
      name: (row.querySelector("[data-author-name]")?.value || "").trim(),
      affiliation: (row.querySelector("[data-author-affiliation]")?.value || "").trim(),
      email: (row.querySelector("[data-author-email]")?.value || "").trim(),
      orcid: (row.querySelector("[data-author-orcid]")?.value || "").trim(),
      corresponding: Boolean(row.querySelector("[data-author-corresponding]")?.checked),
    })).filter((item) => item.name);
    const keywords = (container.querySelector("[data-manuscript-keywords]")?.value || "")
      .split(/[,，;；\n]/).map((item) => item.trim()).filter(Boolean);
    return {
      title: value("title"), subtitle: value("subtitle"), abstract: value("abstract"),
      funding: value("funding"), language: value("language") || "zh",
      citation_style: value("style") || "gb7714",
      contributions: value("contributions"), data_availability: value("data_availability"),
      code_availability: value("code_availability"), competing_interests: value("competing_interests"),
      acknowledgments: value("acknowledgments"),
      authors, keywords,
    };
  }

  function setStatus(container, text) {
    const node = container.querySelector("[data-manuscript-status]");
    if (node) node.textContent = text;
  }

  async function refresh(container) {
    try {
      // Load the metadata first: the preview must use the style that is on disk
      // now, not the one cached from the previous open (that lagged one change).
      const [meta, styles] = await Promise.all([
        api(base("/manuscript")),
        state.styles.length ? Promise.resolve({ styles: state.styles }) : api("/api/citation-styles"),
      ]);
      state.meta = meta;
      state.styles = styles.styles || state.styles;
      const style = meta.citation_style || "";
      const [preview, preflight] = await Promise.all([
        api(base("/draft/references/preview"), {
          method: "POST",
          body: JSON.stringify({ style }),
        }),
        api(base("/draft/preflight"), {
          method: "POST",
          body: JSON.stringify({ style }),
        }),
      ]);
      state.preview = preview;
      state.preflight = preflight;
      render(container);
    } catch (error) {
      setStatus(container, `加载失败：${error.message}`);
    }
  }

  async function save(container) {
    const payload = collect(container);
    setStatus(container, "保存中…");
    try {
      state.meta = await api(base("/manuscript"), { method: "PUT", body: JSON.stringify(payload) });
      // The API mirrors the style into the draft (no revision bump), so the
      // editor's selector and every export agree without a second draft save.
      await refresh(container);
      setStatus(container, "已保存");
      // Remount the draft studio so its style selector shows the mirrored value.
      window.dispatchEvent(new CustomEvent("paperpilot:draft-changed"));
    } catch (error) {
      setStatus(container, `保存失败：${error.message}`);
    }
  }

  async function exportDraft(format) {
    const response = await fetch(base(`/draft/export?format=${encodeURIComponent(format)}`), { method: "GET" });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { const body = await response.json(); detail = body.detail || detail; } catch { /* binary error */ }
      throw new Error(detail);
    }
    const blob = await response.blob();
    const matches = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get("content-disposition") || "");
    const fallback = `paperpilot-draft.${format === "latex" ? "zip" : format === "markdown" ? "md" : format}`;
    const name = matches ? decodeURIComponent(matches[1]) : fallback;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  async function open(projectId) {
    if (!projectId) return;
    state.projectId = String(projectId);
    state.open = true;
    const container = document.createElement("div");
    container.className = "ms-root";
    container.dataset.manuscriptRoot = "1";
    document.body.appendChild(container);
    container.innerHTML = `<div class="ms-backdrop"></div><aside class="ms-drawer"><p class="muted">加载稿件信息…</p></aside>`;

    container.addEventListener("click", async (event) => {
      const target = event.target;
      if (target.closest("[data-manuscript-close]")) { close(); return; }
      if (target.closest("[data-manuscript-add-author]")) {
        const host = container.querySelector("[data-manuscript-authors]");
        host.insertAdjacentHTML("beforeend", authorRow({}, host.children.length));
        return;
      }
      if (target.closest("[data-author-remove]")) {
        target.closest("[data-manuscript-author]")?.remove();
        return;
      }
      if (target.closest("[data-manuscript-save]")) { await save(container); return; }
      const exportButton = target.closest("[data-manuscript-export]");
      if (exportButton) {
        setStatus(container, "导出中…");
        try { await exportDraft(exportButton.dataset.manuscriptExport); setStatus(container, "已导出"); }
        catch (error) { setStatus(container, `导出失败：${error.message}`); }
      }
    });

    await refresh(container);
  }

  function close() {
    document.querySelectorAll("[data-manuscript-root]").forEach((node) => node.remove());
    state.open = false;
  }

  window.PaperPilotManuscript = { open, close };
})();
