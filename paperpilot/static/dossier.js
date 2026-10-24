(function () {
  "use strict";

  const state = {
    projects: [],
    selected: "",
    pendingFile: null,
    preview: null,
    busy: false,
  };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(message, isError) {
    if (window.PaperPilot && window.PaperPilot.toast) {
      window.PaperPilot.toast(message, Boolean(isError));
      return;
    }
    if (isError) window.alert(message);
  }

  async function errorDetail(response) {
    try {
      const data = await response.json();
      return (data && data.detail) || `请求失败（${response.status}）`;
    } catch (_) {
      return `请求失败（${response.status}）`;
    }
  }

  function formatDate(value) {
    return String(value || "—").replace("T", " ");
  }

  function summaryRows(summary) {
    const item = summary || {};
    return [
      ["运行", item.runs || 0],
      ["证据", item.evidence || 0],
      ["草稿章节", item.draft_sections || 0],
      ["草稿段落", item.draft_blocks || 0],
      ["Copilot 消息", item.assistant_messages || 0],
      ["模型调用", item.llm_calls || 0],
      ["草稿版本", item.draft_versions || 0],
    ].map(([label, value]) => `<div class="dossier-stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join("");
  }

  function previewMarkup(preview) {
    if (!preview) return "";
    const project = preview.project || {};
    const warnings = preview.warnings || [];
    return `
      <div class="dossier-preview" data-dossier-preview>
        <div class="dossier-preview-head">
          <div>
            <span class="dossier-kicker">校验通过</span>
            <h3>${esc(project.title || "未命名项目")}</h3>
            <p>${esc(project.question || "")}</p>
          </div>
          <span class="dossier-badge">${esc(preview.app_version || "—")}</span>
        </div>
        <div class="dossier-stats">${summaryRows(preview.summary)}</div>
        <dl class="dossier-meta">
          <div><dt>导出时间</dt><dd>${esc(formatDate(preview.exported_at))}</dd></div>
          <div><dt>根哈希</dt><dd class="mono" title="${esc(preview.root_sha256 || "")}">${esc(String(preview.root_sha256 || "").slice(0, 24))}…</dd></div>
        </dl>
        ${warnings.length ? `<ul class="dossier-warnings">${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}
        <button class="btn btn-primary btn-small" type="button" data-dossier-import>导入为新项目</button>
        <p class="dossier-note">导入不会覆盖现有项目；相同的档案可以重复导入。</p>
      </div>`;
  }

  function renderShell(panel) {
    const options = state.projects.map((project) => `
      <option value="${esc(project.id)}" ${project.id === state.selected ? "selected" : ""}>
        ${esc(project.title)} · ${esc(project.status === "archived" ? "已归档" : "进行中")}
      </option>`).join("");
    const current = state.projects.find((project) => project.id === state.selected);
    panel.innerHTML = `
      <section class="dossier-shell">
        <header class="dossier-head">
          <div>
            <span class="dossier-kicker">可复现研究档案</span>
            <h2>导出、校验、导入</h2>
            <p>把项目、运行、证据、草稿、稿件与 Copilot 历史装进一个可校验的档案；不包含 PDF 原件、密钥或其他项目数据。</p>
          </div>
          <span class="dossier-seal">SHA-256</span>
        </header>
        <div class="dossier-grid">
          <article class="dossier-card">
            <h3>导出当前项目</h3>
            <label class="dossier-field">
              <span>研究项目</span>
              <select data-dossier-project ${state.projects.length ? "" : "disabled"}>
                ${options || `<option value="">还没有研究项目</option>`}
              </select>
            </label>
            <button class="btn btn-primary" type="button" data-dossier-export ${state.projects.length ? "" : "disabled"}>
              导出 .paperpilot.zip
            </button>
            <p class="dossier-note">${current ? `当前选择：${esc(current.title)}` : "先在“研究项目”中创建或导入一个项目。"}</p>
          </article>
          <article class="dossier-card">
            <h3>校验档案</h3>
            <label class="dossier-field dossier-file-field">
              <span>选择 .paperpilot.zip</span>
              <input type="file" accept=".zip,application/zip" data-dossier-file />
            </label>
            <p class="dossier-note">校验只读，不会创建项目；逐文件哈希与根哈希都一致才可导入。</p>
            <div data-dossier-verify-preview></div>
          </article>
        </div>
        <div data-dossier-result></div>
      </section>`;
    bind(panel);
  }

  function bind(panel) {
    const select = panel.querySelector("[data-dossier-project]");
    if (select) {
      select.addEventListener("change", () => {
        state.selected = select.value;
        renderShell(panel);
      });
    }
    const exportButton = panel.querySelector("[data-dossier-export]");
    if (exportButton && state.selected) {
      exportButton.addEventListener("click", () => exportDossier(exportButton));
    }
    const input = panel.querySelector("[data-dossier-file]");
    if (input) {
      input.addEventListener("change", () => {
        const file = (input.files || [])[0];
        if (file) verifyFile(file, panel);
      });
    }
    const importButton = panel.querySelector("[data-dossier-import]");
    if (importButton) {
      importButton.addEventListener("click", () => importFile(importButton, panel));
    }
    const openProjects = panel.querySelector("[data-dossier-open-projects]");
    if (openProjects) {
      openProjects.addEventListener("click", () => {
        if (window.PaperPilot && window.PaperPilot.switchTab) window.PaperPilot.switchTab("projects");
      });
    }
  }

  async function exportDossier(button) {
    if (!state.selected || state.busy) return;
    state.busy = true;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "正在导出…";
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(state.selected)}/dossier`);
      if (!response.ok) throw new Error(await errorDetail(response));
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/);
      const name = (match && match[1]) || `paperpilot-dossier-${state.selected}.zip`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast("研究档案已导出。");
    } catch (error) {
      toast(error.message || "导出失败。", true);
    } finally {
      state.busy = false;
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function verifyFile(file, panel) {
    if (state.busy) return;
    state.pendingFile = file;
    state.preview = null;
    const target = panel.querySelector("[data-dossier-verify-preview]");
    if (target) target.innerHTML = `<p class="dossier-note">正在校验 ${esc(file.name)}…</p>`;
    state.busy = true;
    try {
      const body = new FormData();
      body.append("file", file, file.name);
      const response = await fetch("/api/dossiers/verify", { method: "POST", body });
      if (!response.ok) throw new Error(await errorDetail(response));
      state.preview = await response.json();
      if (target) target.innerHTML = previewMarkup(state.preview);
      bind(panel);
    } catch (error) {
      state.pendingFile = null;
      if (target) target.innerHTML = `<p class="dossier-error">${esc(error.message || "档案校验失败。")}</p>`;
      toast(error.message || "档案校验失败。", true);
    } finally {
      state.busy = false;
    }
  }

  async function importFile(button, panel) {
    if (!state.pendingFile || state.busy) return;
    state.busy = true;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "正在导入…";
    try {
      const body = new FormData();
      body.append("file", state.pendingFile, state.pendingFile.name);
      const response = await fetch("/api/dossiers/import", { method: "POST", body });
      if (!response.ok) throw new Error(await errorDetail(response));
      const result = await response.json();
      const project = result.project || {};
      state.pendingFile = null;
      state.preview = null;
      await loadProjects();
      renderShell(panel);
      const resultNode = panel.querySelector("[data-dossier-result]");
      if (resultNode) {
        resultNode.innerHTML = `
          <article class="dossier-result">
            <span class="dossier-kicker">导入完成</span>
            <h3>${esc(project.title || "新项目")}</h3>
            <p>新项目 ID：<code>${esc(project.id || "")}</code> · 运行 ${esc((result.summary || {}).runs || 0)} · 证据 ${esc((result.summary || {}).evidence || 0)}</p>
            ${(result.warnings || []).length ? `<p class="dossier-note">${esc(result.warnings.join(" "))}</p>` : ""}
            <button class="btn btn-primary btn-small" type="button" data-dossier-open-projects>打开研究项目</button>
          </article>`;
        bind(panel);
      }
      toast("档案已导入为新项目。");
    } catch (error) {
      toast(error.message || "导入失败。", true);
    } finally {
      state.busy = false;
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function loadProjects() {
    try {
      const response = await fetch("/api/projects?status=&limit=50");
      if (!response.ok) throw new Error(await errorDetail(response));
      const data = await response.json();
      state.projects = Array.isArray(data.projects) ? data.projects : [];
      if (!state.selected || !state.projects.some((project) => project.id === state.selected)) {
        state.selected = state.projects.length ? state.projects[0].id : "";
      }
    } catch (error) {
      state.projects = [];
      state.selected = "";
      toast(error.message || "项目列表加载失败。", true);
    }
  }

  async function render(panel) {
    panel.innerHTML = `<section class="dossier-shell"><div class="dossier-loading">正在读取研究档案…</div></section>`;
    await loadProjects();
    renderShell(panel);
  }

  window.PaperPilotDossier = { render };
})();
