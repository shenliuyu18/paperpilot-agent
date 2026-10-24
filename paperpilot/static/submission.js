/* Submission drawer (v6.0): profile check, package, reviewer view, reference I/O. */
(() => {
  const state = {
    projectId: "", profile: "course", profiles: [], check: null,
    review: null, cover: "", busy: false, open: false,
    comments: "", mapped: null,
  };

  const esc = (value) => window.PaperPilot.esc(value);
  const toast = (message, error = false) => window.PaperPilot.toast(message, error);
  const base = (suffix = "") => `/api/projects/${encodeURIComponent(state.projectId)}${suffix}`;
  const profileOf = (id) => state.profiles.find((item) => item.id === id) || state.profiles[0] || {};

  async function api(path, options = {}) {
    return window.PaperPilot.api(path, options);
  }

  function issueRow(item) {
    const level = item.level || item.severity || "info";
    return `<li class="sb-issue sb-${esc(level)}"><span class="sb-level">${esc(level)}</span><span>${esc(item.message || "")}</span></li>`;
  }

  function renderCheck() {
    const result = state.check;
    if (!result) return `<p class="muted small">还没有运行检查。</p>`;
    const counts = result.counts || {};
    const rows = (result.items || []).map(issueRow).join("") || `<li class="muted small">没有发现问题。</li>`;
    return `
      <div class="sb-counts">
        <span class="sb-pill sb-error">错误 ${esc(counts.error || 0)}</span>
        <span class="sb-pill sb-warn">提醒 ${esc(counts.warn || 0)}</span>
        <span class="sb-pill sb-info">提示 ${esc(counts.info || 0)}</span>
        <span class="sb-pill">${result.blocking ? "会阻断导出" : "不阻断导出"}</span>
      </div>
      <ul class="sb-list">${rows}</ul>`;
  }

  function renderHighlights() {
    const rows = (state.check && state.check.highlights) || [];
    if (!rows.length) return `<p class="muted small">还没有可用的亮点：先把主张绑定到证据并跑语义核查。</p>`;
    return `<ul class="sb-highlights">${rows.map((row) => `
      <li><span>${esc(row.text)}</span><small>${esc(row.section || "")}</small></li>`).join("")}</ul>`;
  }

  function renderMap() {
    const data = state.mapped;
    if (!data) return `<p class="muted small">粘贴审稿意见后点“映射到草稿”。</p>`;
    const rows = (data.items || []).map((item) => {
      const first = (item.matches || [])[0];
      return `<tr>
        <td>${esc(item.comment)}</td>
        <td>${esc(first ? (first.section || "") : "未匹配")}</td>
        <td>${esc(first ? (first.block_id || "") : "")}</td>
        <td>${esc(first ? String(first.score) : "")}</td>
      </tr>`;
    }).join("");
    return `<table class="sb-table"><thead><tr><th>审稿意见</th><th>章节</th><th>段落</th><th>重合</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small">${esc(data.disclaimer || "")}</p>`;
  }

  function renderReview() {
    const report = state.review;
    if (!report) return `<p class="muted small">还没有生成审稿视角。</p>`;
    const stats = report.stats || {};
    const major = (report.major || []).map(issueRow).join("") || `<li class="muted small">没有主要问题。</li>`;
    const minor = (report.minor || []).map(issueRow).join("") || `<li class="muted small">没有次要问题。</li>`;
    const strengths = (report.strengths || []).map((item) => `<li>${esc(item)}</li>`).join("");
    const questions = (report.questions || []).map((item) => `<li>${esc(item)}</li>`).join("");
    return `
      <div class="sb-recommend">${esc(report.recommendation_label || report.recommendation || "")}</div>
      <p class="muted small">${esc(report.disclaimer || "")}</p>
      <div class="sb-counts">
        <span class="sb-pill">段落 ${esc(stats.paragraphs || 0)}</span>
        <span class="sb-pill">覆盖 ${esc(Math.round((stats.coverage || 0) * 100))}%</span>
        <span class="sb-pill">支持 ${esc(stats.supported || 0)}</span>
        <span class="sb-pill">部分 ${esc(stats.partial || 0)}</span>
        <span class="sb-pill">不支持 ${esc(stats.unsupported || 0)}</span>
      </div>
      <h4>优点</h4><ul class="sb-list">${strengths || "<li class='muted small'>暂无</li>"}</ul>
      <h4>主要问题</h4><ul class="sb-list">${major}</ul>
      <h4>次要问题</h4><ul class="sb-list">${minor}</ul>
      <h4>想请作者回答</h4><ul class="sb-list">${questions || "<li class='muted small'>暂无</li>"}</ul>`;
  }

  function render(container) {
    const options = state.profiles.map((item) =>
      `<option value="${esc(item.id)}"${item.id === state.profile ? " selected" : ""}>${esc(item.label)}</option>`
    ).join("");
    const profile = profileOf(state.profile);
    container.innerHTML = `
      <div class="sb-backdrop" data-submission-close></div>
      <aside class="sb-drawer" data-submission-drawer role="dialog" aria-label="投稿包与审稿" aria-modal="true">
        <header class="sb-head">
          <div><p class="eyebrow">投稿</p><h2>投稿包 · 审稿视角</h2></div>
          <button class="btn btn-ghost btn-small" type="button" data-submission-close>关闭</button>
        </header>
        <div class="sb-body">
          <label class="sb-label">投稿档案
            <select class="sb-input" data-submission-profile>${options}</select>
          </label>
          <p class="muted small" data-submission-profile-desc>${esc(profile.description || "")}</p>
          <div class="sb-actions">
            <button class="btn btn-primary btn-small" type="button" data-submission-check>运行检查</button>
            <button class="btn btn-ghost btn-small" type="button" data-submission-package>导出投稿包</button>
            <button class="btn btn-ghost btn-small" type="button" data-submission-review>审稿视角</button>
            <button class="btn btn-ghost btn-small" type="button" data-submission-response>回复模板</button>
          </div>
          <div class="sb-status muted small" data-submission-status></div>

          <section class="sb-section">
            <h3>检查清单</h3>
            <div data-submission-checklist>${renderCheck()}</div>
          </section>
          <section class="sb-section">
            <h3>亮点（≤${esc(profile.highlight_max_chars || 85)} 字符）</h3>
            <div data-submission-highlights>${renderHighlights()}</div>
          </section>
          <details class="sb-section" ${state.cover ? "open" : ""}>
            <summary>投稿信（模板）</summary>
            <pre class="sb-pre" data-submission-cover>${esc(state.cover)}</pre>
          </details>
          <section class="sb-section" data-submission-review-section ${state.review ? "" : "hidden"}>
            <h3>审稿视角</h3>
            <div data-submission-review-report>${renderReview()}</div>
          </section>
          <section class="sb-section">
            <h3>审稿意见映射</h3>
            <textarea class="sb-input sb-textarea" data-submission-comments rows="4" placeholder="每行一条审稿意见；用于定位草稿段落并生成回复模板。">${esc(state.comments)}</textarea>
            <div class="sb-actions">
              <button class="btn btn-ghost btn-small" type="button" data-submission-map>映射到草稿</button>
            </div>
            <div data-submission-map-report>${renderMap()}</div>
          </section>
          <section class="sb-section">
            <h3>参考文献</h3>
            <div class="sb-actions">
              <label class="btn btn-ghost btn-small sb-file">导入 RIS/EndNote
                <input type="file" accept=".ris,.xml,.txt" data-submission-import hidden />
              </label>
              <button class="btn btn-ghost btn-small" type="button" data-submission-export="ris">导出 RIS</button>
              <button class="btn btn-ghost btn-small" type="button" data-submission-export="endnote">导出 EndNote XML</button>
            </div>
            <p class="muted small">导入会跳过 DOI 或标题已存在的条目，并把草稿 revision 加一。</p>
          </section>
        </div>
      </aside>`;
  }

  function setStatus(container, text, error = false) {
    const node = container.querySelector("[data-submission-status]");
    if (!node) return;
    node.textContent = text;
    node.classList.toggle("error", Boolean(error));
  }

  async function loadProfiles() {
    if (state.profiles.length) return;
    const data = await api("/api/submission/profiles");
    state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
    if (!state.profiles.some((item) => item.id === state.profile)) {
      state.profile = state.profiles[0]?.id || "course";
    }
  }

  async function refresh(container) {
    await loadProfiles();
    render(container);
    await runCheck(container);
  }

  async function runCheck(container) {
    state.busy = true;
    setStatus(container, "检查中…");
    try {
      state.check = await api(base("/submission/check"), {
        method: "POST", body: JSON.stringify({ profile: state.profile }),
      });
      state.cover = await (await fetch(base("/submission/cover-letter?profile=" + encodeURIComponent(state.profile)))).text();
      render(container);
      const counts = state.check.counts || {};
      setStatus(container, `检查完成：${counts.error || 0} 错误 / ${counts.warn || 0} 提醒`);
    } catch (error) {
      setStatus(container, error.message, true);
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = name; link.style.display = "none";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  async function exportPackage(container) {
    if (state.busy) return;
    state.busy = true;
    setStatus(container, "正在打包…");
    try {
      const response = await fetch(base("/submission/package?profile=" + encodeURIComponent(state.profile)));
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { detail = (await response.json()).detail || detail; } catch { /* binary */ }
        throw new Error(detail);
      }
      const blob = await response.blob();
      const match = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get("content-disposition") || "");
      download(blob, match ? decodeURIComponent(match[1]) : "submission.zip");
      const pdf = response.headers.get("x-paperpilot-pdf-included") === "1";
      const blind = response.headers.get("x-paperpilot-blinded") === "1";
      setStatus(container, `已导出投稿包${pdf ? "（含 PDF）" : "（无 PDF，见 ZIP 内说明）"}${blind ? "，正文已匿名" : ""}`);
    } catch (error) {
      setStatus(container, error.message, true); toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function runReview(container) {
    state.busy = true;
    setStatus(container, "生成审稿视角…");
    try {
      state.review = await api(base("/reviewer/report"), { method: "POST" });
      render(container);
      setStatus(container, state.review.recommendation_label || "已生成");
    } catch (error) {
      setStatus(container, error.message, true); toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function mapComments(container) {
    const textarea = container.querySelector("[data-submission-comments]");
    state.comments = textarea ? textarea.value : state.comments;
    const comments = state.comments.split(/\n+/).map((line) => line.trim()).filter(Boolean);
    if (!comments.length) { setStatus(container, "先粘贴至少一条审稿意见。", true); return; }
    setStatus(container, "正在映射…");
    try {
      state.mapped = await api(base("/reviewer/map-comments"), {
        method: "POST", body: JSON.stringify({ comments }),
      });
      render(container);
      setStatus(container, `已映射 ${state.mapped.comments || 0} 条意见`);
    } catch (error) {
      setStatus(container, error.message, true); toast(error.message, true);
    }
  }

  async function downloadResponse(container) {
    setStatus(container, "正在生成回复模板…");
    try {
      const comments = state.comments.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      const response = comments.length
        ? await fetch(base("/reviewer/response-template"), {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ comments }),
          })
        : await fetch(base("/reviewer/response-template"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const match = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get("content-disposition") || "");
      download(blob, match ? decodeURIComponent(match[1]) : "response-template.md");
      setStatus(container, "已下载回复模板");
    } catch (error) {
      setStatus(container, error.message, true); toast(error.message, true);
    }
  }

  async function importReferences(container, file) {
    if (!file) return;
    setStatus(container, "导入参考文献…");
    try {
      const text = await file.text();
      const draft = await api(base("/draft"));
      const fmt = /\.xml$/i.test(file.name) ? "endnote" : "ris";
      const result = await api(base("/draft/references/import"), {
        method: "POST",
        body: JSON.stringify({ format: fmt, text, revision: draft.draft.revision }),
      });
      setStatus(container, `已导入 ${result.added} 条，跳过 ${result.skipped} 条`);
      window.dispatchEvent(new CustomEvent("paperpilot:draft-changed"));
    } catch (error) {
      setStatus(container, error.message, true); toast(error.message, true);
    }
  }

  async function open(projectId) {
    if (!projectId) return;
    state.projectId = String(projectId);
    state.open = true;
    state.check = null; state.review = null; state.cover = ""; state.mapped = null;
    const container = document.createElement("div");
    container.className = "sb-root";
    container.dataset.submissionRoot = "1";
    document.body.appendChild(container);
    container.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-submission-close]")) { close(); return; }
      if (target.closest("[data-submission-check]")) { await runCheck(container); return; }
      if (target.closest("[data-submission-package]")) { await exportPackage(container); return; }
      if (target.closest("[data-submission-review]")) { await runReview(container); return; }
      if (target.closest("[data-submission-map]")) { await mapComments(container); return; }
      if (target.closest("[data-submission-response]")) { await downloadResponse(container); return; }
      const exportButton = target.closest("[data-submission-export]");
      if (exportButton) {
        const fmt = exportButton.dataset.submissionExport;
        window.location.href = base("/draft/references/export?format=" + encodeURIComponent(fmt));
        return;
      }
    });
    container.addEventListener("input", (event) => {
      if (event.target.matches("[data-submission-comments]")) {
        state.comments = event.target.value;
      }
    });
    container.addEventListener("change", async (event) => {
      const target = event.target;
      if (target.matches("[data-submission-profile]")) {
        state.profile = target.value;
        render(container);
        await runCheck(container);
      } else if (target.matches("[data-submission-import]")) {
        await importReferences(container, target.files?.[0]);
      }
    });
    render(container);
    await refresh(container);
  }

  function close() {
    document.querySelectorAll("[data-submission-root]").forEach((node) => node.remove());
    state.open = false;
  }

  window.PaperPilotSubmission = { open, close };
})();
