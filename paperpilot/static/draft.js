(() => {
  "use strict";

  const TYPE_LABELS = {
    paragraph: "段落",
    bullet: "要点",
    quote: "引文",
    note: "备注",
  };
  const STATUS_LABELS = {
    draft: "草稿",
    needs_evidence: "需证据",
    checked: "已核对",
  };
  const SEVERITY_LABELS = { high: "高", medium: "中", low: "低" };
  const VERDICT_LABELS = {
    supported: "支持",
    partial: "部分",
    unsupported: "未支持",
    insufficient: "无证据",
  };
  const REASON_LABELS = {
    manual: "手动保存",
    before_restore: "恢复前自动保存",
    before_agent_write: "Agent 写入前",
  };
  const EXPORT_LABELS = {
    markdown: "Markdown",
    latex: "LaTeX",
    bibtex: "BibTeX",
    json: "JSON",
    docx: "DOCX",
    pdf: "PDF",
  };

  const CITATION_STYLES = [
    ["numeric", "数字引用"],
    ["author-year", "作者-年份"],
    ["gb7714", "GB/T 7714"],
    ["apa", "APA 7"],
    ["ieee", "IEEE"],
  ];

  const state = {
    projectId: "",
    draft: null,
    evidence: [],
    papers: [],
    versions: [],
    issues: [],
    counts: null,
    auditAt: "",
    semantic: null,
    semanticMode: "",
    semanticAt: "",
    container: null,
    selectedBlock: "",
    selectedSection: "",
    dirty: false,
    busy: false,
    evidenceFilter: "",
    paperFilter: "",
    mountToken: 0,
  };

  const api = (path, options = {}) => window.PaperPilot.api(path, options);
  const esc = (value) => window.PaperPilot.esc(value);
  const toast = (message, error = false) => window.PaperPilot.toast(message, error);
  const schedule = window.requestAnimationFrame
    ? (fn) => window.requestAnimationFrame(fn)
    : (fn) => window.setTimeout(fn, 0);

  function projectPath(suffix) {
    return `/api/projects/${encodeURIComponent(state.projectId)}${suffix}`;
  }

  /**
   * Mirrors PaperPilot.api but keeps the HTTP status so a 409 revision
   * conflict can reload the server draft instead of overwriting it.
   */
  async function send(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, { ...options, headers });
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = null;
    }
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : `请求失败（${response.status}）`;
      const error = new Error(typeof detail === "string" ? detail : "请求失败");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function draftOrDefault() {
    return state.draft || {
      revision: 0,
      title: "",
      citation_style: "numeric",
      sections: [],
      citations: {},
    };
  }

  function draftSections() {
    return Array.isArray(draftOrDefault().sections) ? draftOrDefault().sections : [];
  }

  function listOf(value) {
    return Array.isArray(value) ? value.map((item) => String(item)) : [];
  }

  function ensureList(block, key) {
    if (!Array.isArray(block[key])) block[key] = [];
    return block[key];
  }

  function indexById(items) {
    const index = {};
    for (const item of items || []) {
      if (item && item.id !== undefined && item.id !== null) index[String(item.id)] = item;
    }
    return index;
  }

  function blockMap() {
    const map = {};
    for (const section of draftSections()) {
      const blocks = Array.isArray(section.blocks) ? section.blocks : [];
      for (const block of blocks) {
        if (block && block.id !== undefined && block.id !== null) {
          map[String(block.id)] = { section, block };
        }
      }
    }
    return map;
  }

  function findBlock(blockId) {
    return blockMap()[String(blockId || "")] || null;
  }

  function selectedEntry() {
    return state.selectedBlock ? findBlock(state.selectedBlock) : null;
  }

  function selectionSummary() {
    const found = selectedEntry();
    if (!found) return "未选择段落";
    return [
      found.section.title || "未命名章节",
      `段落 ${found.block.id || "—"}`,
      `证据 ${listOf(found.block.evidence_ids).length}`,
      `引用 ${listOf(found.block.citation_keys).length}`,
    ].join(" · ");
  }

  function totalStats() {
    let blocks = 0;
    let chars = 0;
    for (const section of draftSections()) {
      for (const block of section.blocks || []) {
        blocks += 1;
        chars += String(block?.text || "").length;
      }
    }
    return { sections: draftSections().length, blocks, chars };
  }

  function statusText() {
    const draft = draftOrDefault();
    const stats = totalStats();
    return [
      state.dirty ? "未保存" : "已保存",
      `revision ${draft.revision}`,
      `${stats.sections} 节`,
      `${stats.blocks} 块`,
      `${stats.chars} 字`,
    ].join(" · ");
  }

  function ensureSelection() {
    const sections = draftSections();
    if (!sections.some((section) => section.id === state.selectedSection)) {
      state.selectedSection = sections[0]?.id || "";
    }
    const map = blockMap();
    if (!map[state.selectedBlock]) {
      state.selectedBlock = "";
      for (const section of sections) {
        const first = (section.blocks || [])[0];
        if (first) {
          state.selectedBlock = first.id || "";
          state.selectedSection = section.id || state.selectedSection;
          break;
        }
      }
    }
    if (state.selectedBlock) {
      const found = findBlock(state.selectedBlock);
      if (found) state.selectedSection = found.section.id || state.selectedSection;
    }
  }

  function nextId(prefix, used) {
    let index = 1;
    while (used.has(`${prefix}-${String(index).padStart(3, "0")}`)) index += 1;
    return `${prefix}-${String(index).padStart(3, "0")}`;
  }

  function sectionIds() {
    const ids = new Set();
    for (const section of draftSections()) {
      if (section.id) ids.add(String(section.id));
    }
    return ids;
  }

  function blockIds() {
    const ids = new Set();
    for (const section of draftSections()) {
      for (const block of section.blocks || []) {
        if (block?.id) ids.add(String(block.id));
      }
    }
    return ids;
  }

  function typeOptions(selected) {
    return Object.entries(TYPE_LABELS)
      .map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`)
      .join("");
  }

  function evidenceStateLabel(bound) {
    return bound ? "已绑定 · 点击移除" : "点击绑定";
  }

  function citationStateLabel(key, inserted) {
    if (!key) return "点击生成引用键";
    return inserted ? "已插入 · 点击移除" : "点击插入";
  }

  function renderEvidenceSide() {
    if (!state.evidence.length) {
      return `<p class="muted small draft-side-empty">没有可用证据。启动一轮研究运行后，证据会自动进入项目记忆。</p>`;
    }
    const selected = selectedEntry();
    const boundIds = new Set(selected ? listOf(selected.block.evidence_ids) : []);
    const rows = state.evidence
      .map((item) => {
        const id = String(item.id ?? "");
        const bound = boundIds.has(id);
        const title = String(item.title || item.paper_id || id || "证据");
        const quote = String(item.quote || "").slice(0, 96);
        const search = `${title} ${quote} ${item.paper_id || ""}`.toLowerCase();
        const page = item.page !== undefined && item.page !== null && item.page !== "" ? ` · p.${item.page}` : "";
        return `
          <button class="draft-evidence${bound ? " bound" : ""}" type="button" data-draft-evidence="${esc(id)}" data-search="${esc(search)}">
            <span class="draft-evidence-title">${esc(title)}</span>
            <span class="draft-evidence-quote">${esc(quote)}</span>
            <span class="draft-evidence-meta">${item.verified ? "已核对" : "待核对"} · <span data-evidence-state>${esc(evidenceStateLabel(bound))}</span>${esc(page)}</span>
          </button>
        `;
      })
      .join("");
    return `
      <input class="draft-filter" type="search" data-draft-filter-evidence value="${esc(state.evidenceFilter)}" placeholder="筛选证据…" aria-label="筛选证据" />
      <div class="draft-side-list">${rows}</div>
      <p class="muted small draft-filter-empty" data-draft-filter-empty="evidence" hidden>没有匹配的证据。</p>
    `;
  }

  function renderCitationSide() {
    if (!state.papers.length) {
      return `<p class="muted small draft-side-empty">项目记忆中没有论文。启动运行或导入论文后即可插入引用。</p>`;
    }
    const citations = draftOrDefault().citations || {};
    const selected = selectedEntry();
    const selectedKeys = new Set(selected ? listOf(selected.block.citation_keys) : []);
    const rows = state.papers
      .map((paper) => {
        const id = String(paper.id ?? "");
        const citation = citations[id];
        const key = citation && citation.key ? String(citation.key) : "";
        const inserted = Boolean(key && selectedKeys.has(key));
        const title = String(paper.title || id || "未命名论文");
        const authors = Array.isArray(paper.authors) ? paper.authors : [];
        const meta = authors.length
          ? `${authors.slice(0, 2).join(", ")}${authors.length > 2 ? " 等" : ""}`
          : "作者未知";
        const year = paper.year ? ` · ${paper.year}` : "";
        const venue = paper.venue ? ` · ${paper.venue}` : "";
        const search = `${title} ${authors.join(" ")} ${paper.year || ""} ${paper.venue || ""}`.toLowerCase();
        return `
          <button class="draft-evidence draft-cite${inserted ? " inserted" : ""}" type="button" data-draft-cite="${esc(id)}" data-citation-key="${esc(key)}" data-search="${esc(search)}">
            <span class="draft-evidence-title">${esc(title)}</span>
            <span class="draft-evidence-quote">${esc(meta + year + venue)}</span>
            <span class="draft-evidence-meta">${key ? `引用键 ${esc(key)} · ` : ""}<span data-citation-state>${esc(citationStateLabel(key, inserted))}</span></span>
          </button>
        `;
      })
      .join("");
    return `
      <input class="draft-filter" type="search" data-draft-filter-papers value="${esc(state.paperFilter)}" placeholder="筛选论文…" aria-label="筛选论文" />
      <div class="draft-side-list">${rows}</div>
      <p class="muted small draft-filter-empty" data-draft-filter-empty="papers" hidden>没有匹配的论文。</p>
    `;
  }

  function renderVersionsSide() {
    if (!state.versions.length) {
      return `<p class="muted small draft-side-empty">还没有版本。点击“保存版本”创建第一个快照。</p>`;
    }
    return `
      <div class="draft-side-list">
        ${state.versions
          .map((version) => {
            const reason = version.reason && version.reason !== "manual"
              ? (REASON_LABELS[version.reason] || String(version.reason))
              : "";
            return `
              <button class="draft-version" type="button" data-draft-restore="${esc(version.id)}" title="恢复版本 ${esc(version.id)}">
                <span class="draft-version-label">${esc(version.label || version.id || "版本")}</span>
                <span class="draft-version-meta">${esc(version.id || "")} · revision ${esc(version.revision ?? "—")} · ${esc(version.created_at || "")}</span>
                <span class="draft-version-meta">${esc(version.summary || "")}${reason ? ` · ${esc(reason)}` : ""}</span>
              </button>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function renderAuditSide() {
    const counts = state.counts;
    const summary = counts
      ? `
        <div class="draft-audit-counts">
          <span class="badge badge-pink">高 ${esc(counts.high || 0)}</span>
          <span class="badge badge-amber">中 ${esc(counts.medium || 0)}</span>
          <span class="badge badge-soft">低 ${esc(counts.low || 0)}</span>
          <span class="badge badge-muted">共 ${esc(counts.total || 0)}</span>
        </div>
        ${state.auditAt ? `<p class="muted small draft-audit-at">核查于 ${esc(state.auditAt)}</p>` : ""}
      `
      : `<p class="muted small draft-side-empty">尚未运行核查。核查会检查证据、引用、数值和重复文本。</p>`;
    let issues = "";
    if (state.issues.length) {
      issues = state.issues
        .map((issue) => {
          const blockId = String(issue.block_id || "");
          const sectionId = String(issue.section_id || "");
          return `
            <button class="draft-issue ${esc(issue.severity || "low")}" type="button" data-draft-issue-block="${esc(blockId)}" data-draft-issue-section="${esc(sectionId)}"${blockId || sectionId ? "" : " disabled"}>
              <span class="draft-issue-severity">${esc(SEVERITY_LABELS[issue.severity] || issue.severity || "提示")}</span>
              <span class="draft-issue-message">${esc(issue.message || "")}</span>
              <span class="draft-issue-block">${esc(blockId || sectionId || "全局")}</span>
            </button>
          `;
        })
        .join("");
    } else if (counts) {
      issues = `<p class="muted small draft-audit-clean">没有发现问题。</p>`;
    }
    return `${summary}${issues}${renderSemanticSide()}`;
  }

  function renderSemanticSide() {
    const data = state.semantic;
    const mode = state.semanticMode;
    const labels = {
      rule: ["规则初判", "soft"],
      llm: ["模型复核", "green"],
      llm_degraded: ["模型降级", "amber"],
    };
    const [modeLabel, modeKind] = labels[mode] || ["未运行", "muted"];
    const head = `
      <div class="draft-semantic-head">
        <h4>语义支持</h4>
        <span class="badge badge-${modeKind}">${esc(modeLabel)}</span>
      </div>
    `;
    if (!data) {
      return `${head}<p class="muted small">运行语义核查，判断每段是否被绑定证据支持。未配置模型时给出规则初判。</p>`;
    }
    const counts = data.counts || {};
    const items = (data.items || []).filter((item) => item.verdict !== "supported").slice(0, 6);
    const summary = `
      <p class="muted small draft-semantic-summary">
        支持 ${esc(counts.supported || 0)} · 部分 ${esc(counts.partial || 0)} ·
        未支持 ${esc(counts.unsupported || 0)} · 无证据 ${esc(counts.insufficient || 0)}
        ${state.semanticAt ? ` · ${esc(state.semanticAt)}` : ""}
      </p>
    `;
    if (!items.length) {
      return `${head}${summary}<p class="muted small draft-audit-clean">所有块均被绑定证据支持。</p>`;
    }
    return `${head}${summary}${items.map((item) => `
      <button class="draft-semantic-item ${esc(item.severity || "medium")}" type="button"
        data-draft-semantic-block="${esc(item.block_id || "")}">
        <span class="draft-semantic-verdict">${esc(VERDICT_LABELS[item.verdict] || item.verdict || "")}</span>
        <span class="draft-semantic-text">${esc(item.rationale || "")}</span>
        <small>${esc(item.block_id || "")}${item.quote ? ` · ${esc(item.quote)}` : ""}</small>
      </button>
    `).join("")}`;
  }

  function renderOutline(sections) {
    if (!sections.length) {
      return `<p class="muted small draft-side-empty">还没有章节。点击“+ 新章节”开始。</p>`;
    }
    return sections
      .map((section, index) => {
        const count = Array.isArray(section.blocks) ? section.blocks.length : 0;
        return `
        <button class="draft-section${section.id === state.selectedSection ? " active" : ""}" type="button" data-draft-section="${esc(section.id)}">
          <span class="draft-section-index">${index + 1}.</span>
          <span class="draft-section-name">${esc(section.title || "未命名章节")}</span>
          <span class="draft-section-count muted small">${count} 块</span>
        </button>
      `;
      })
      .join("");
  }

  function renderBlock(block, index, total, evidenceIndex) {
    const blockId = String(block.id ?? "");
    const type = TYPE_LABELS[block.type] ? block.type : "paragraph";
    const status = STATUS_LABELS[block.status] ? block.status : "draft";
    const evidenceIds = listOf(block.evidence_ids);
    const citationKeys = listOf(block.citation_keys);
    const evidenceChips = evidenceIds
      .map((evidenceId) => {
        const item = evidenceIndex[evidenceId] || {};
        const label = item.title || item.paper_id || evidenceId;
        return `<button class="draft-chip" type="button" data-draft-unbind="${esc(evidenceId)}" data-block-ref="${esc(blockId)}" title="移除证据 ${esc(evidenceId)}">${esc(label)} ×</button>`;
      })
      .join("");
    const citationChips = citationKeys
      .map((key) => `<button class="draft-chip draft-chip-cite" type="button" data-draft-uncite="${esc(key)}" data-block-ref="${esc(blockId)}" title="移除引用 ${esc(key)}">${esc(key)} ×</button>`)
      .join("");
    const emptyChips = evidenceIds.length || citationKeys.length
      ? ""
      : `<span class="draft-block-empty muted small">尚未绑定证据或引用。</span>`;
    return `
      <div class="draft-block${state.selectedBlock === blockId ? " selected" : ""}" data-block-row="${esc(blockId)}">
        <div class="draft-block-meta">
          <span class="draft-type-label">${esc(TYPE_LABELS[type])}</span>
          <span class="draft-status ${esc(status)}">${esc(STATUS_LABELS[status])}</span>
          <div class="draft-block-tools">
            <select class="draft-type-select" data-draft-block-type="${esc(blockId)}" aria-label="段落类型">${typeOptions(type)}</select>
            <button class="icon-btn draft-icon" type="button" data-draft-block-move="up" data-block-id="${esc(blockId)}" aria-label="上移段落" title="上移段落"${index === 0 ? " disabled" : ""}>↑</button>
            <button class="icon-btn draft-icon" type="button" data-draft-block-move="down" data-block-id="${esc(blockId)}" aria-label="下移段落" title="下移段落"${index === total - 1 ? " disabled" : ""}>↓</button>
            <button class="link-btn draft-danger" type="button" data-draft-block-delete="${esc(blockId)}">删除</button>
          </div>
        </div>
        <textarea data-draft-block="${esc(blockId)}" rows="4" placeholder="在这里写这一段的正文…" aria-label="段落正文">${esc(block.text || "")}</textarea>
        <div class="draft-block-foot">
          <span class="muted small">证据 ${evidenceIds.length} · 引用 ${citationKeys.length}</span>
          <span class="draft-chips">${evidenceChips}${citationChips}${emptyChips}</span>
        </div>
      </div>
    `;
  }

  function renderSection(section, index, count, evidenceIndex) {
    const blocks = Array.isArray(section.blocks) ? section.blocks : [];
    return `
      <div class="draft-section-block${section.id === state.selectedSection ? " active" : ""}" data-section-row="${esc(section.id)}">
        <div class="draft-section-head">
          <input class="draft-section-title" data-draft-section-title="${esc(section.id)}" value="${esc(section.title || "")}" placeholder="章节标题" maxlength="120" aria-label="章节 ${index + 1} 标题" />
          <div class="draft-section-tools">
            <button class="icon-btn draft-icon" type="button" data-draft-section-move="up" data-section-id="${esc(section.id)}" aria-label="上移章节" title="上移章节"${index === 0 ? " disabled" : ""}>↑</button>
            <button class="icon-btn draft-icon" type="button" data-draft-section-move="down" data-section-id="${esc(section.id)}" aria-label="下移章节" title="下移章节"${index === count - 1 ? " disabled" : ""}>↓</button>
            <button class="link-btn draft-danger" type="button" data-draft-section-delete="${esc(section.id)}">删除章节</button>
          </div>
        </div>
        ${blocks.map((block, blockIndex) => renderBlock(block, blockIndex, blocks.length, evidenceIndex)).join("")}
        <button class="link-btn draft-add-block" type="button" data-draft-add-block="${esc(section.id)}">+ 段落</button>
      </div>
    `;
  }

  function render(container, focus = {}) {
    ensureSelection();
    const draft = draftOrDefault();
    const sections = draftSections();
    const stats = totalStats();
    const style = CITATION_STYLES.some(([id]) => id === draft.citation_style)
      ? draft.citation_style
      : "numeric";
    const evidenceIndex = indexById(state.evidence);
    container.innerHTML = `
      <div class="draft-studio">
        <div class="draft-toolbar">
          <button class="btn btn-primary btn-small" type="button" data-draft-save>保存</button>
          <button class="btn btn-ghost btn-small" type="button" data-draft-version>保存版本</button>
          <button class="btn btn-ghost btn-small" type="button" data-draft-audit>运行核查</button>
          <button class="btn btn-ghost btn-small" type="button" data-draft-review>矛盾复核</button>
          <button class="btn btn-ghost btn-small" type="button" data-draft-semantic>语义核查</button>
          <button class="btn btn-ghost btn-small" type="button" data-manuscript-open>稿件设置</button>
          <button class="btn btn-ghost btn-small" type="button" data-submission-open>投稿包</button>
          <span class="draft-toolbar-group">
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="markdown">导出 Markdown</button>
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="latex">导出 LaTeX</button>
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="bibtex">导出 BibTeX</button>
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="json">导出 JSON</button>
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="docx">导出 DOCX</button>
            <button class="btn btn-ghost btn-small" type="button" data-draft-export="pdf">导出 PDF</button>
          </span>
          <span class="draft-status-line${state.dirty ? " dirty" : ""}" data-draft-status>${esc(statusText())}</span>
        </div>
        <div class="draft-selection-bar">
          <span class="eyebrow">当前段落</span>
          <span class="draft-selection-text" data-draft-selected>${esc(selectionSummary())}</span>
          <span class="muted small draft-selection-hint">点选证据绑定到当前段落，点选论文插入引用。</span>
        </div>
        <div class="draft-grid">
          <aside class="draft-outline card">
            <div class="draft-side-head">
              <h3>章节</h3>
              <button class="link-btn" type="button" data-draft-add-section>+ 新章节</button>
            </div>
            <div class="draft-outline-list">${renderOutline(sections)}</div>
            <div class="draft-outline-foot muted small">${esc(`${stats.sections} 节 · ${stats.blocks} 块`)}</div>
          </aside>
          <section class="draft-editor card">
            <div class="draft-editor-head">
              <input class="draft-title" data-draft-title value="${esc(draft.title || "")}" placeholder="草稿标题" maxlength="200" aria-label="草稿标题" />
              <select class="draft-style-select" data-draft-style aria-label="引用样式">
                ${CITATION_STYLES.map(([id, label]) =>
                  `<option value="${id}"${style === id ? " selected" : ""}>${label}</option>`).join("")}
              </select>
            </div>
            ${sections.length
              ? sections.map((section, index) => renderSection(section, index, sections.length, evidenceIndex)).join("")
              : `<div class="draft-empty">
                  <p class="muted">还没有章节。先添加一个章节，再添加段落，然后绑定证据并保存。</p>
                  <button class="btn btn-primary btn-small" type="button" data-draft-add-section>+ 新建章节</button>
                </div>`}
          </section>
          <aside class="draft-side">
            <div class="card">
              <div class="draft-side-head"><h3>证据</h3><span class="muted small">${state.evidence.length} 条</span></div>
              ${renderEvidenceSide()}
            </div>
            <div class="card">
              <div class="draft-side-head"><h3>引用</h3><span class="muted small">${state.papers.length} 篇</span></div>
              ${renderCitationSide()}
            </div>
            <div class="card">
              <div class="draft-side-head"><h3>版本</h3><span class="muted small">${state.versions.length} 个</span></div>
              ${renderVersionsSide()}
            </div>
            <div class="card">
              <div class="draft-side-head"><h3>核查</h3><span class="muted small">${state.counts ? "已运行" : "未运行"}</span></div>
              ${renderAuditSide()}
            </div>
          </aside>
        </div>
      </div>
    `;
    applyFilters(container);
    if (focus.blockId) {
      schedule(() => {
        focusBlockTextarea(container, focus.blockId);
        revealBlock(container, focus.blockId);
      });
    } else if (focus.sectionId) {
      schedule(() => {
        focusSectionTitle(container, focus.sectionId);
        revealSection(container, focus.sectionId);
      });
    }
  }

  function findByData(container, selector, key, value) {
    return Array.from(container.querySelectorAll(selector))
      .find((element) => element.dataset[key] === value) || null;
  }

  function focusBlockTextarea(container, blockId) {
    const area = findByData(container, "[data-draft-block]", "draftBlock", blockId);
    if (!area) return;
    area.focus();
    try {
      area.setSelectionRange(area.value.length, area.value.length);
    } catch (_) {
      /* Some browsers refuse selection on hidden fields; focus is enough. */
    }
  }

  function focusSectionTitle(container, sectionId) {
    const input = findByData(container, "[data-draft-section-title]", "draftSectionTitle", sectionId);
    if (!input) return;
    input.focus();
    input.select();
  }

  function revealBlock(container, blockId) {
    const row = findByData(container, "[data-block-row]", "blockRow", blockId);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function revealSection(container, sectionId) {
    const row = findByData(container, "[data-section-row]", "sectionRow", sectionId);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function applyFilters(container) {
    applyFilter(container, "[data-draft-evidence]", state.evidenceFilter, "[data-draft-filter-empty='evidence']");
    applyFilter(container, "[data-draft-cite]", state.paperFilter, "[data-draft-filter-empty='papers']");
  }

  function applyFilter(container, selector, query, emptySelector) {
    const needle = String(query || "").trim().toLowerCase();
    let shown = 0;
    container.querySelectorAll(selector).forEach((element) => {
      const match = !needle || String(element.dataset.search || "").includes(needle);
      element.style.display = match ? "" : "none";
      if (match) shown += 1;
    });
    const empty = container.querySelector(emptySelector);
    if (empty) empty.hidden = shown > 0;
  }

  function applySelectionDom(container) {
    const found = selectedEntry();
    const sectionId = found ? found.section.id : state.selectedSection;
    container.querySelectorAll("[data-draft-section]").forEach((element) => {
      element.classList.toggle("active", element.dataset.draftSection === state.selectedSection);
    });
    container.querySelectorAll("[data-section-row]").forEach((element) => {
      element.classList.toggle("active", element.dataset.sectionRow === state.selectedSection);
    });
    container.querySelectorAll("[data-block-row]").forEach((element) => {
      element.classList.toggle("selected", element.dataset.blockRow === state.selectedBlock);
    });
    const label = container.querySelector("[data-draft-selected]");
    if (label) label.textContent = selectionSummary();
    const evidenceIds = new Set(found ? listOf(found.block.evidence_ids) : []);
    container.querySelectorAll("[data-draft-evidence]").forEach((element) => {
      const bound = evidenceIds.has(element.dataset.draftEvidence);
      element.classList.toggle("bound", bound);
      const hint = element.querySelector("[data-evidence-state]");
      if (hint) hint.textContent = evidenceStateLabel(bound);
    });
    const keys = new Set(found ? listOf(found.block.citation_keys) : []);
    container.querySelectorAll("[data-draft-cite]").forEach((element) => {
      const key = element.dataset.citationKey || "";
      const inserted = Boolean(key && keys.has(key));
      element.classList.toggle("inserted", inserted);
      const hint = element.querySelector("[data-citation-state]");
      if (hint) hint.textContent = citationStateLabel(key, inserted);
    });
  }

  function selectBlock(container, blockId) {
    const found = findBlock(blockId);
    if (!found) return;
    state.selectedBlock = String(blockId);
    state.selectedSection = found.section.id || state.selectedSection;
    applySelectionDom(container);
  }

  function markDirty(container) {
    state.dirty = true;
    updateStatus(container);
  }

  function updateStatus(container) {
    const line = container.querySelector("[data-draft-status]");
    if (!line) return;
    line.textContent = statusText();
    line.classList.toggle("dirty", state.dirty);
  }

  async function loadVersions() {
    const data = await api(projectPath("/draft/versions"));
    state.versions = Array.isArray(data.versions) ? data.versions : [];
  }

  function resetAudit() {
    state.issues = [];
    state.counts = null;
    state.auditAt = "";
  }

  async function mount(container, projectId) {
    if (!container) return;
    if (!window.PaperPilot) {
      container.textContent = "草稿工作台初始化失败：缺少 PaperPilot 运行时。";
      return;
    }
    const token = (state.mountToken += 1);
    state.container = container;
    state.projectId = String(projectId || "");
    state.draft = null;
    state.semantic = null;
    state.semanticMode = "";
    state.semanticAt = "";
    state.evidence = [];
    state.papers = [];
    state.versions = [];
    state.selectedBlock = "";
    state.selectedSection = "";
    state.dirty = false;
    state.busy = false;
    state.evidenceFilter = "";
    state.paperFilter = "";
    resetAudit();
    bind(container);
    container.innerHTML = `<div class="draft-loading"><div class="loading-line"></div><p class="muted">正在读取草稿…</p></div>`;
    try {
      const [draftData, evidenceData, papersData] = await Promise.all([
        api(projectPath("/draft")),
        api(`/api/projects/${encodeURIComponent(state.projectId)}/memory?kind=evidence&limit=100`).catch(() => ({ items: [] })),
        api(`/api/projects/${encodeURIComponent(state.projectId)}/memory?kind=papers&limit=100`).catch(() => ({ items: [] })),
      ]);
      if (token !== state.mountToken) return;
      state.draft = draftData.draft || null;
      state.evidence = Array.isArray(evidenceData.items) ? evidenceData.items : [];
      state.papers = Array.isArray(papersData.items) ? papersData.items : [];
      try {
        await loadVersions();
      } catch (_) {
        state.versions = [];
      }
      if (token !== state.mountToken) return;
      ensureSelection();
      render(container);
    } catch (error) {
      if (token !== state.mountToken) return;
      container.innerHTML = `
        <div class="warn-box">${esc(error.message)}</div>
        <div class="draft-retry">
          <button class="btn btn-ghost btn-small" type="button" data-draft-retry>重新加载</button>
        </div>
      `;
      toast(error.message, true);
    }
  }

  async function saveDraft(container) {
    if (!state.draft) return false;
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return false;
    }
    state.busy = true;
    try {
      const draft = draftOrDefault();
      const data = await send(projectPath("/draft"), {
        method: "PUT",
        body: JSON.stringify({
          revision: state.draft.revision,
          title: draft.title,
          citation_style: draft.citation_style,
          sections: draft.sections,
        }),
      });
      state.draft = data.draft;
      state.dirty = false;
      ensureSelection();
      render(container);
      toast("草稿已保存");
      return true;
    } catch (error) {
      if (error.status === 409) {
        await handleConflict(container);
        return false;
      }
      toast(error.message, true);
      return false;
    } finally {
      state.busy = false;
    }
  }

  async function handleConflict(container) {
    try {
      const data = await api(projectPath("/draft"));
      state.draft = data.draft;
      state.dirty = false;
      resetAudit();
      try {
        await loadVersions();
      } catch (_) {
        state.versions = [];
      }
      ensureSelection();
      render(container);
      toast("草稿已在别处更新，已载入最新版本，请确认后重新编辑。", true);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function saveVersion(container) {
    if (!(await saveDraft(container))) return;
    if (state.busy) return;
    state.busy = true;
    try {
      const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      const data = await api(projectPath("/draft/versions"), {
        method: "POST",
        body: JSON.stringify({ label: `手动保存 ${time}` }),
      });
      await loadVersions();
      render(container);
      toast(`已保存版本 ${data.version?.id || ""}`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function runAudit(container) {
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    if (state.dirty) {
      const saved = await saveDraft(container);
      if (!saved) return;
    }
    state.busy = true;
    try {
      const data = await api(projectPath("/draft/audit"), { method: "POST" });
      state.issues = Array.isArray(data.issues) ? data.issues : [];
      state.counts = data.counts || { high: 0, medium: 0, low: 0, total: 0 };
      state.auditAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      const fresh = await api(projectPath("/draft"));
      state.draft = fresh.draft;
      state.dirty = false;
      ensureSelection();
      render(container);
      toast(`核查完成：${state.counts.total || 0} 个问题`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function reviewContradictions(container) {
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    state.busy = true;
    try {
      const data = await api(projectPath("/contradictions/review"), {
        method: "POST",
        body: JSON.stringify({ use_llm: true, limit: 8 }),
      });
      if (data.degraded) {
        const hint = data.degrade_reason === "no_key" ? "未配置模型，已跳过复核" : `复核未执行：${data.degrade_reason}`;
        toast(hint, true);
      } else {
        toast(`已复核 ${data.reviewed} 条：确认 ${data.confirmed}，排除 ${data.dismissed}，存疑 ${data.uncertain}`);
      }
      state.busy = false;
      await runAudit(container);
      return;
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function runSemantic(container) {
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    if (state.dirty) {
      const saved = await saveDraft(container);
      if (!saved) return;
    }
    state.busy = true;
    try {
      const data = await api(projectPath("/draft/semantic-audit"), {
        method: "POST",
        body: JSON.stringify({ limit: 20, use_llm: true }),
      });
      state.semantic = data;
      state.semanticMode = data.mode || "rule";
      state.semanticAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      render(container);
      const counts = data.counts || {};
      toast(`语义核查完成：未支持 ${counts.unsupported || 0}，部分 ${counts.partial || 0}`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  function focus(blockId) {
    if (!state.container || !blockId) return;
    focusBlockTextarea(state.container, blockId);
    revealBlock(state.container, blockId);
  }

  async function addEvidence(container, evidenceId) {
    if (!state.selectedBlock) {
      toast("先选择一个段落，再绑定证据。", true);
      return;
    }
    const found = findBlock(state.selectedBlock);
    if (!found) {
      toast("选中的段落已不存在，请重新选择。", true);
      return;
    }
    const ids = ensureList(found.block, "evidence_ids");
    const index = ids.indexOf(evidenceId);
    if (index >= 0) ids.splice(index, 1);
    else ids.push(evidenceId);
    markDirty(container);
    render(container);
    toast(index >= 0 ? "已移除证据" : "已绑定证据");
  }

  async function addCitation(container, paperId) {
    if (!state.selectedBlock) {
      toast("先选择一个段落，再插入引用。", true);
      return;
    }
    const found = findBlock(state.selectedBlock);
    if (!found) {
      toast("选中的段落已不存在，请重新选择。", true);
      return;
    }
    const citations = draftOrDefault().citations || (state.draft.citations = {});
    const current = citations[paperId];
    const keys = ensureList(found.block, "citation_keys");
    if (current && current.key && keys.includes(current.key)) {
      found.block.citation_keys = keys.filter((key) => key !== current.key);
      markDirty(container);
      render(container);
      toast("已移除引用");
      return;
    }
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    state.busy = true;
    try {
      const data = await send(projectPath("/draft/citations"), {
        method: "POST",
        body: JSON.stringify({ paper_id: paperId, revision: state.draft.revision }),
      });
      if (typeof data.revision === "number") state.draft.revision = data.revision;
      if (data.citation) citations[paperId] = data.citation;
      else if (!citations[paperId]) citations[paperId] = { key: data.key };
      const key = String(data.key || "");
      if (key && !keys.includes(key)) keys.push(key);
      markDirty(container);
      render(container);
      revealBlock(container, state.selectedBlock);
      toast(key ? `已插入引用 ${key}` : "已插入引用");
    } catch (error) {
      if (error.status === 409) await handleConflict(container);
      else toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function restoreVersion(container, versionId) {
    if (!window.confirm("恢复这个版本？当前内容会先自动保存为一个版本。")) return;
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    if (state.dirty) {
      const saved = await saveDraft(container);
      if (!saved) return;
    }
    state.busy = true;
    try {
      const data = await api(projectPath(`/draft/versions/${encodeURIComponent(versionId)}/restore`), { method: "POST" });
      state.draft = data.draft;
      state.dirty = false;
      resetAudit();
      await loadVersions();
      ensureSelection();
      render(container);
      toast("版本已恢复");
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function downloadExport(format) {
    const response = await fetch(projectPath("/draft/export?format=" + encodeURIComponent(format)), { method: "GET" });
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
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.rel = "noopener";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    toast(`已导出 ${EXPORT_LABELS[format]}`);
  }

  async function exportDraft(container, format) {
    if (!EXPORT_LABELS[format]) return;
    if (state.busy) {
      toast("正在处理上一个操作，请稍候。", true);
      return;
    }
    if (state.dirty) {
      const saved = await saveDraft(container);
      if (!saved) return;
    }
    try {
      await downloadExport(format);
    } catch (error) {
      toast(`导出失败：${error.message}`, true);
    }
  }

  function addSection(container) {
    if (!state.draft) return;
    const id = nextId("sec", sectionIds());
    if (!Array.isArray(state.draft.sections)) state.draft.sections = [];
    state.draft.sections.push({ id, title: "新章节", blocks: [] });
    state.selectedSection = id;
    state.selectedBlock = "";
    markDirty(container);
    render(container, { sectionId: id });
  }

  function addBlock(container, sectionId) {
    const section = draftSections().find((item) => item.id === sectionId);
    if (!section) return;
    const id = nextId("blk", blockIds());
    if (!Array.isArray(section.blocks)) section.blocks = [];
    section.blocks.push({
      id,
      type: "paragraph",
      text: "",
      evidence_ids: [],
      citation_keys: [],
      status: "draft",
    });
    state.selectedSection = section.id;
    state.selectedBlock = id;
    markDirty(container);
    render(container, { blockId: id });
  }

  function deleteBlock(container, blockId) {
    const found = findBlock(blockId);
    if (!found) return;
    if (!window.confirm("删除这个段落？")) return;
    found.section.blocks = (found.section.blocks || []).filter((block) => block.id !== blockId);
    ensureSelection();
    markDirty(container);
    render(container);
  }

  function deleteSection(container, sectionId) {
    const section = draftSections().find((item) => item.id === sectionId);
    if (!section) return;
    const count = (section.blocks || []).length;
    if (!window.confirm(`删除章节“${section.title || "未命名章节"}”及其 ${count} 个段落？`)) return;
    state.draft.sections = draftSections().filter((item) => item.id !== sectionId);
    ensureSelection();
    markDirty(container);
    render(container);
  }

  function moveItem(list, index, delta) {
    const target = index + delta;
    if (index < 0 || target < 0 || target >= list.length) return false;
    const [item] = list.splice(index, 1);
    list.splice(target, 0, item);
    return true;
  }

  function moveSection(container, sectionId, direction) {
    const sections = state.draft.sections || [];
    const index = sections.findIndex((item) => item.id === sectionId);
    if (!moveItem(sections, index, direction === "up" ? -1 : 1)) return;
    state.selectedSection = sectionId;
    markDirty(container);
    render(container);
    revealSection(container, sectionId);
  }

  function moveBlock(container, blockId, direction) {
    const found = findBlock(blockId);
    if (!found) return;
    const blocks = found.section.blocks || [];
    const index = blocks.findIndex((block) => block.id === blockId);
    if (!moveItem(blocks, index, direction === "up" ? -1 : 1)) return;
    state.selectedBlock = blockId;
    state.selectedSection = found.section.id;
    markDirty(container);
    render(container);
    revealBlock(container, blockId);
  }

  function handleInput(container, event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.hasAttribute("data-draft-block")) {
      const found = findBlock(target.dataset.draftBlock);
      if (found) {
        found.block.text = target.value;
        markDirty(container);
      }
      return;
    }
    if (target.hasAttribute("data-draft-title")) {
      state.draft.title = target.value;
      markDirty(container);
      return;
    }
    if (target.hasAttribute("data-draft-section-title")) {
      const section = draftSections().find((item) => item.id === target.dataset.draftSectionTitle);
      if (section) {
        section.title = target.value;
        const button = findByData(container, "[data-draft-section]", "draftSection", section.id);
        const name = button ? button.querySelector(".draft-section-name") : null;
        if (name) name.textContent = section.title || "未命名章节";
        markDirty(container);
      }
      return;
    }
    if (target.hasAttribute("data-draft-filter-evidence")) {
      state.evidenceFilter = target.value;
      applyFilters(container);
      return;
    }
    if (target.hasAttribute("data-draft-filter-papers")) {
      state.paperFilter = target.value;
      applyFilters(container);
    }
  }

  function handleChange(container, event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.hasAttribute("data-draft-block-type")) {
      const found = findBlock(target.dataset.draftBlockType);
      if (found) {
        found.block.type = TYPE_LABELS[target.value] ? target.value : "paragraph";
        markDirty(container);
        render(container);
      }
      return;
    }
    if (target.hasAttribute("data-draft-style")) {
      state.draft.citation_style = CITATION_STYLES.some(([id]) => id === target.value)
        ? target.value
        : "numeric";
      markDirty(container);
    }
  }

  async function handleClick(container, event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    if (target.closest("[data-draft-retry]")) {
      await mount(container, state.projectId);
      return;
    }
    if (target.closest("[data-draft-save]")) {
      await saveDraft(container);
      return;
    }
    if (target.closest("[data-draft-version]")) {
      await saveVersion(container);
      return;
    }
    if (target.closest("[data-manuscript-open]")) {
      window.PaperPilotManuscript?.open(state.projectId);
      return;
    }
    if (target.closest("[data-submission-open]")) {
      window.PaperPilotSubmission?.open(state.projectId);
      return;
    }
    if (target.closest("[data-draft-audit]")) {
      await runAudit(container);
      return;
    }
    if (target.closest("[data-draft-review]")) {
      await reviewContradictions(container);
      return;
    }
    if (target.closest("[data-draft-semantic]")) {
      await runSemantic(container);
      return;
    }
    const semanticBlock = target.closest("[data-draft-semantic-block]");
    if (semanticBlock) {
      focus(semanticBlock.dataset.draftSemanticBlock);
      return;
    }
    const exportButton = target.closest("[data-draft-export]");
    if (exportButton) {
      await exportDraft(container, exportButton.dataset.draftExport);
      return;
    }
    if (target.closest("[data-draft-add-section]")) {
      addSection(container);
      return;
    }
    const addBlockButton = target.closest("[data-draft-add-block]");
    if (addBlockButton) {
      addBlock(container, addBlockButton.dataset.draftAddBlock);
      return;
    }
    const sectionMove = target.closest("[data-draft-section-move]");
    if (sectionMove) {
      moveSection(container, sectionMove.dataset.sectionId, sectionMove.dataset.draftSectionMove);
      return;
    }
    const sectionDelete = target.closest("[data-draft-section-delete]");
    if (sectionDelete) {
      deleteSection(container, sectionDelete.dataset.draftSectionDelete);
      return;
    }
    const blockMove = target.closest("[data-draft-block-move]");
    if (blockMove) {
      moveBlock(container, blockMove.dataset.blockId, blockMove.dataset.draftBlockMove);
      return;
    }
    const blockDelete = target.closest("[data-draft-block-delete]");
    if (blockDelete) {
      deleteBlock(container, blockDelete.dataset.draftBlockDelete);
      return;
    }
    const unbind = target.closest("[data-draft-unbind]");
    if (unbind) {
      const found = findBlock(unbind.dataset.blockRef || "");
      if (found) {
        found.block.evidence_ids = listOf(found.block.evidence_ids)
          .filter((id) => id !== unbind.dataset.draftUnbind);
        markDirty(container);
        render(container);
      }
      return;
    }
    const uncite = target.closest("[data-draft-uncite]");
    if (uncite) {
      const found = findBlock(uncite.dataset.blockRef || "");
      if (found) {
        found.block.citation_keys = listOf(found.block.citation_keys)
          .filter((key) => key !== uncite.dataset.draftUncite);
        markDirty(container);
        render(container);
      }
      return;
    }
    const evidenceButton = target.closest("[data-draft-evidence]");
    if (evidenceButton) {
      await addEvidence(container, evidenceButton.dataset.draftEvidence);
      return;
    }
    const citeButton = target.closest("[data-draft-cite]");
    if (citeButton) {
      await addCitation(container, citeButton.dataset.draftCite);
      return;
    }
    const restoreButton = target.closest("[data-draft-restore]");
    if (restoreButton) {
      await restoreVersion(container, restoreButton.dataset.draftRestore);
      return;
    }
    const issueButton = target.closest("[data-draft-issue-block]");
    if (issueButton) {
      const blockId = issueButton.dataset.draftIssueBlock || "";
      const sectionId = issueButton.dataset.draftIssueSection || "";
      if (blockId && findBlock(blockId)) {
        selectBlock(container, blockId);
        revealBlock(container, blockId);
      } else if (sectionId) {
        const section = draftSections().find((item) => item.id === sectionId);
        state.selectedSection = sectionId;
        state.selectedBlock = section?.blocks?.[0]?.id || "";
        applySelectionDom(container);
        revealSection(container, sectionId);
      }
      return;
    }
    const sectionButton = target.closest("[data-draft-section]");
    if (sectionButton) {
      const section = draftSections().find((item) => item.id === sectionButton.dataset.draftSection);
      if (!section) return;
      state.selectedSection = section.id;
      state.selectedBlock = section.blocks?.[0]?.id || "";
      applySelectionDom(container);
      revealSection(container, section.id);
      return;
    }
    const blockRow = target.closest("[data-block-row]");
    if (blockRow && !target.closest("button, input, textarea, select, a")) {
      selectBlock(container, blockRow.dataset.blockRow);
    }
  }

  function bind(container) {
    container.oninput = (event) => handleInput(container, event);
    container.onchange = (event) => handleChange(container, event);
    container.onfocusin = (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const area = target.closest("[data-draft-block]");
      if (area) selectBlock(container, area.dataset.draftBlock);
    };
    container.onclick = (event) => {
      handleClick(container, event).catch((error) => toast(error.message, true));
    };
  }

  window.addEventListener("paperpilot:draft-changed", () => {
    if (state.projectId && state.container) {
      mount(state.container, state.projectId);
    }
  });

  window.PaperPilotDraft = { mount, focus };
})();
