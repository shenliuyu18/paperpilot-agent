(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    meta: null,
    papers: [],
    selected: new Set(),
    activePaper: null,
    read: null,
    translation: null,
    translationTarget: "zh",
    translationProvider: "auto",
    translationBusy: false,
    citations: null,
    compare: null,
    graph: null,
    graphFocus: "",
    graphType: "all",
    innovate: null,
    transform: null,
    gaps: null,
    ask: null,
    askQuestion: "",
    askScope: "all",
    topic: null,
    topicQuery: "面向存算一体的低功耗 ADC",
    topicScope: "all",
    draft: null,
    check: null,
    argument: null,
    writingTopic: "面向存算一体的低功耗 ADC",
    writingKind: "related_work",
    writingScope: "all",
    checkText: "",
    library: [],
    libraryStats: null,
    libraryHealth: null,
    libraryFilter: { status: "", query: "" },
    searchPlan: null,
    layerFilter: "",
    sourcePresets: [],
    eval: null,
    agentRuns: [],
    agentRun: null,
    agentSelection: new Set(),
    agentSelectionRun: "",
    agentTimer: null,
    agentGoal: "面向存算一体的低功耗 ADC 设计",
    agentSource: "demo",
    agentMode: "checkpoint",
    agentMaxActions: 14,
    agentMaxPapers: 6,
    agentTopic: null,
    agentTopicRun: "",
    tab: "read",
  };

  const el = {
    modeBadge: $("#modeBadge"),
    modeDetail: $("#modeDetail"),
    resultMeta: $("#resultMeta"),
    resultsList: $("#resultsList"),
    sourceNotice: $("#sourceNotice"),
    resultsHead: $(".results-head"),
    queryInput: $("#queryInput"),
    sourceSelect: $("#sourceSelect"),
    limitSelect: $("#limitSelect"),
    searchBtn: $("#searchBtn"),
    searchForm: $("#searchForm"),
    planBtn: $("#planBtn"),
    planPanel: $("#planPanel"),
    layerFilter: $("#layerFilter"),
    selectAll: $("#selectAll"),
    panel: $("#panel"),
    tabs: $("#tabs"),
    compareCount: $("#compareCount"),
    crumb: $("#crumb"),
    toast: $("#toast"),
    modal: $("#modal"),
    modalTitle: $("#modalTitle"),
    modalBody: $("#modalBody"),
    pdfInput: $("#pdfInput"),
    uploadBtn: $("#uploadBtn"),
    exportBtn: $("#exportBtn"),
    statLibrary: $("#statLibrary"),
    libraryCount: $("#libraryCount"),
  };

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  // The "/" shortcut must never eat a keystroke meant for a form field: the model
  // drawer alone needs "/" for base URLs, API keys and org/model route names.
  function isTextEntry(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest(
      "input, textarea, select, [contenteditable=''], [contenteditable='true']"
    ));
  }

  function drawerOpen() {
    return Boolean(document.querySelector(
      "[data-model-root], [data-submission-root], [data-manuscript-root], [data-tracking-root]"
    ));
  }

  function safeUrl(value) {
    const text = String(value || "");
    return /^https?:\/\//i.test(text) ? text : "";
  }

  function authorsText(authors = []) {
    if (!authors.length) return "作者信息未提供";
    if (authors.length <= 3) return authors.join(", ");
    return authors.slice(0, 3).join(", ") + " 等";
  }

  function shortTitle(title, max = 54) {
    const text = String(title || "未命名论文");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  function badge(text, kind = "muted") {
    return `<span class="badge badge-${kind}">${esc(text)}</span>`;
  }

  function toast(message, isError = false) {
    el.toast.textContent = message;
    el.toast.classList.toggle("error", isError);
    el.toast.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.toast.classList.remove("show"), 2600);
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, { ...options, headers });
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
      const detail = data && data.detail ? data.detail : `请求失败（${response.status}）`;
      throw new Error(detail);
    }
    return data;
  }

  function setLoadingText(button, text) {
    button.dataset.original = button.dataset.original || button.textContent;
    button.textContent = text;
    button.disabled = true;
  }

  function restoreButton(button) {
    button.textContent = button.dataset.original || button.textContent;
    button.disabled = false;
  }

  function skeleton() {
    el.resultsList.innerHTML = `<div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>`;
  }

  function emptyState(title, text) {
    return `<div class="empty-state"><div class="empty-orb"></div><h2>${esc(title)}</h2><p>${esc(text)}</p></div>`;
  }

  async function init() {
    const initialHash = location.hash;
    bindEvents();
    try {
      state.meta = await api("/api/meta");
      renderMode();
      if (state.meta.library) updateLibraryStats(state.meta.library);
      refreshCustomSourceOptions();
    } catch (error) {
      el.modeBadge.textContent = "连接失败";
      el.modeBadge.className = "badge badge-pink";
      el.modeDetail.textContent = "后端未启动？";
    }
    await refreshLibrary({ render: false });
    await doSearch();
    await handleDeepLink(initialHash);
  }

  async function handleDeepLink(rawHash = location.hash) {
    const hash = (rawHash || "").replace("#", "");
    const allowed = ["read", "translate", "citations", "compare", "innovate", "transform", "gaps", "topic", "writing", "ask", "agent", "projects", "copilot", "dossier", "library", "eval"];
    if (!allowed.includes(hash)) return;
    if (hash === "compare" && state.papers.length >= 3) {
      state.selected = new Set(state.papers.slice(0, 3).map((paper) => paper.id));
      el.selectAll.checked = false;
      renderResults();
      updateCompareCount();
    }
    await switchTab(hash);
  }

  function bindEvents() {
    el.searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      doSearch();
    });

    $("#queryChips").addEventListener("click", (event) => {
      const chip = event.target.closest(".chip");
      if (!chip) return;
      el.queryInput.value = chip.dataset.query || "";
      doSearch();
    });

    if (el.planBtn) {
      el.planBtn.addEventListener("click", () => loadSearchPlan());
    }
    const sourcesButton = $("#sourcesBtn");
    if (sourcesButton) sourcesButton.addEventListener("click", () => openSourcesManager());
    const historyButton = $("#historyBtn");
    if (historyButton) historyButton.addEventListener("click", () => openSearchHistory());

    const openModelSettings = () => {
      if (window.PaperPilotModel) window.PaperPilotModel.open();
    };
    ["#modelBtn", "#modelSideBtn", "#modelCalloutBtn"].forEach((selector) => {
      const node = $(selector);
      if (node) node.addEventListener("click", openModelSettings);
    });
    window.addEventListener("paperpilot:llm-changed", () => refreshLlmMode());

    if (el.planPanel) {
      el.planPanel.addEventListener("click", (event) => {
        const close = event.target.closest("[data-plan-close]");
        if (close) {
          el.planPanel.hidden = true;
          return;
        }
        const copy = event.target.closest("[data-plan-copy]");
        if (copy) {
          copyText(copy.dataset.planCopy || "");
          return;
        }
        const use = event.target.closest("[data-plan-use]");
        if (use) {
          el.sourceSelect.value = use.dataset.planUse;
          el.planPanel.hidden = true;
          toast(`已切换检索来源：${use.dataset.planLabel || use.dataset.planUse}`);
        }
      });
    }

    if (el.layerFilter) {
      el.layerFilter.addEventListener("click", (event) => {
        const button = event.target.closest("[data-layer]");
        if (!button) return;
        state.layerFilter = button.dataset.layer || "";
        renderLayerFilter();
        renderResults();
      });
    }

    el.resultsList.addEventListener("click", (event) => {
      const saveButton = event.target.closest(".result-save");
      if (saveButton) {
        event.stopPropagation();
        toggleResultSave(saveButton.dataset.saveId);
        return;
      }
      const addButton = event.target.closest(".result-add");
      if (addButton) {
        event.stopPropagation();
        addPaperToProject(addButton.dataset.addProject);
        return;
      }
      const checkbox = event.target.closest(".result-check");
      if (checkbox) {
        event.stopPropagation();
        toggleSelect(checkbox.dataset.id, checkbox.checked);
        return;
      }
      const card = event.target.closest(".result-card");
      if (card) openPaper(card.dataset.id);
    });

    el.selectAll.addEventListener("change", () => {
      const ids = visiblePapers().map((paper) => paper.id);
      if (el.selectAll.checked) {
        ids.forEach((id) => state.selected.add(id));
      } else {
        ids.forEach((id) => state.selected.delete(id));
      }
      renderResults();
      updateCompareCount();
    });

    el.tabs.addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) switchTab(tab.dataset.tab);
    });

    el.exportBtn.addEventListener("click", exportMarkdown);
    el.uploadBtn.addEventListener("click", () => el.pdfInput.click());
    el.pdfInput.addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length) uploadPdfs(files);
      event.target.value = "";
    });

    el.modal.addEventListener("click", (event) => {
      if (event.target.closest("[data-close-modal]")) closeModal();
    });

    el.panel.addEventListener("click", (event) => handleAgentClick(event));
    el.panel.addEventListener("change", (event) => handleAgentChange(event));
    if (window.PaperPilotProjects) window.PaperPilotProjects.bind(el.panel);
    if (window.PaperPilotLive) window.PaperPilotLive.bind(el.panel);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModal();
      if (event.key === "/" && !isTextEntry(event.target) && !drawerOpen()) {
        event.preventDefault();
        el.queryInput.focus();
      }
    });
  }

  function renderMode() {
    if (!state.meta) return;
    const online = state.meta.llm_enabled;
    const settings = state.meta.llm_settings || {};
    el.modeBadge.textContent = online ? "在线增强模式" : "离线规则模式";
    el.modeBadge.className = "badge " + (online ? "badge-green" : "badge-soft");
    el.modeDetail.textContent = online
      ? `模型 ${state.meta.llm_model} · 可抽取上传 PDF · 完整翻译`
      : "未配置模型 · 点“模型设置”接入你自己的大模型";
    const dot = $("#modelDot");
    if (dot) dot.classList.toggle("online", online);
    const btnState = $("#modelBtnState");
    if (btnState) btnState.textContent = online ? ` · ${settings.model || state.meta.llm_model}` : "";
    const callout = $("#modelCallout");
    if (callout) callout.classList.toggle("hidden", online);
  }

  async function refreshLlmMode() {
    try {
      state.meta = await api("/api/meta");
      renderMode();
    } catch (error) {
      toast("模型状态刷新失败，请重新打开页面", true);
    }
  }

  async function loadSearchPlan() {
    const query = (el.queryInput.value || "").trim();
    if (!query) {
      toast("先输入一个研究方向", true);
      return;
    }
    const source = el.sourceSelect.value;
    if (el.planPanel && !el.planPanel.hidden && state.searchPlan
      && state.searchPlan.query === query && state.searchPlan.source === source) {
      el.planPanel.hidden = true;
      return;
    }
    if (el.planBtn) setLoadingText(el.planBtn, "拆解中…");
    try {
      const data = await api("/api/search/plan", {
        method: "POST",
        body: JSON.stringify({ query, source }),
      });
      state.searchPlan = data;
      renderSearchPlan(data);
      if (el.planPanel) el.planPanel.hidden = false;
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (el.planBtn) restoreButton(el.planBtn);
    }
  }

  function refreshCustomSourceOptions() {
    if (!el.sourceSelect) return;
    const builtins = [
      ["demo", "内置演示语料"],
      ["auto", "多源并发聚合"],
      ["arxiv", "arXiv 在线"],
      ["crossref", "Crossref 在线（摘要级）"],
      ["europepmc", "Europe PMC（生命科学/预印本）"],
      ["doaj", "DOAJ 开放获取期刊"],
      ["hal", "HAL 开放获取"],
      ["upload", "本地 PDF"],
    ];
    const custom = (state.meta && state.meta.custom_sources) || [];
    const current = el.sourceSelect.value || "demo";
    el.sourceSelect.innerHTML = `
      <optgroup label="内置来源">
        ${builtins.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("")}
      </optgroup>
      ${custom.length ? `<optgroup label="自定义来源">${custom.map((item) => `<option value="custom:${esc(item.id)}">${esc(item.name)}${item.has_token ? " · 已配置密钥" : ""}</option>`).join("")}</optgroup>` : ""}
    `;
    const values = Array.from(el.sourceSelect.options).map((option) => option.value);
    el.sourceSelect.value = values.includes(current) ? current : "demo";
  }

  function sourceLabelFor(source) {
    const labels = {
      demo: "内置演示语料",
      auto: "多源并发聚合",
      arxiv: "arXiv 在线",
      crossref: "Crossref 在线（摘要级）",
      europepmc: "Europe PMC 在线",
      doaj: "DOAJ 开放获取",
      hal: "HAL 开放获取",
      upload: "本地 PDF",
    };
    if (labels[source]) return labels[source];
    if (source && source.startsWith("custom:")) {
      const item = ((state.meta && state.meta.custom_sources) || []).find(
        (entry) => "custom:" + entry.id === source
      );
      return item ? item.name : "自定义来源";
    }
    return "检索";
  }

  function refreshMetaSources(sources, presets) {
    if (!state.meta) state.meta = {};
    state.meta.custom_sources = (sources || []).filter((item) => item.enabled !== false);
    if (presets) state.sourcePresets = presets;
    refreshCustomSourceOptions();
  }

  function renderSearchPlan(data) {
    if (!el.planPanel) return;
    const concepts = data.concepts || [];
    const sources = data.sources || [];
    const warnings = data.warnings || [];
    el.planPanel.innerHTML = `
      <div class="plan-head">
        <div>
          <div class="eyebrow">Search Plan</div>
          <div class="plan-title">先看检索式，再看结果</div>
        </div>
        <button class="icon-btn" type="button" data-plan-close aria-label="关闭检索计划">关闭</button>
      </div>
      <div class="plan-concepts">
        ${concepts.map((concept) => `
          <div class="plan-concept">
            <div class="plan-concept-head"><strong>${esc(concept.label)}</strong><span>${(concept.terms || []).length} 个词</span></div>
            <div class="chips">${(concept.terms || []).slice(0, 5).map((term) => `<span class="chip">${esc(term)}</span>`).join("")}</div>
            <div class="section-note">${esc(concept.rationale)}</div>
          </div>
        `).join("")}
      </div>
      <div class="plan-strategy">
        <div class="section-note">检索策略</div>
        <ol class="step-list">${(data.strategy || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ol>
      </div>
      <div class="plan-sources">
        <div class="section-note">各来源检索式</div>
        ${sources.map((item) => `
          <div class="plan-source ${item.id === data.source ? "active" : ""}">
            <div class="plan-source-head"><strong>${esc(item.label)}</strong><span>${esc(item.note)}</span></div>
            <code class="plan-query">${esc(item.query || "—")}</code>
            <div class="plan-source-actions">
              <button class="btn btn-small btn-ghost" type="button" data-plan-copy="${esc(item.query || "")}">复制</button>
              <button class="btn btn-small btn-ghost" type="button" data-plan-use="${esc(item.id)}" data-plan-label="${esc(item.label)}">用此来源</button>
            </div>
          </div>
        `).join("")}
      </div>
      ${warnings.length ? `<div class="plan-warnings">${warnings.map((item) => `<div>${esc(item)}</div>`).join("")}</div>` : ""}
      <div class="section-note">检索完成后，每条结果会标注“直接相关 / 方法参考 / 背景脉络”和题录可信度；分层是筛选提示，不是对论文质量的最终裁决。</div>
    `;
  }

  function visiblePapers() {
    if (!state.layerFilter) return state.papers;
    return state.papers.filter((paper) => paper.layer === state.layerFilter);
  }

  function renderLayerFilter() {
    if (!el.layerFilter) return;
    if (!state.papers.length) {
      el.layerFilter.innerHTML = "";
      return;
    }
    const counts = { "": state.papers.length, "直接相关": 0, "方法参考": 0, "背景脉络": 0 };
    state.papers.forEach((paper) => {
      if (paper.layer && counts[paper.layer] !== undefined) counts[paper.layer] += 1;
    });
    el.layerFilter.innerHTML = [
      ["", "全部"],
      ["直接相关", "直接相关"],
      ["方法参考", "方法参考"],
      ["背景脉络", "背景脉络"],
    ].map(([value, label]) => `
      <button class="layer-filter-btn ${state.layerFilter === value ? "active" : ""}" type="button" data-layer="${esc(value)}">${esc(label)} <span>${counts[value]}</span></button>
    `).join("");
  }

  function renderSourceNotice(status) {
    if (!el.sourceNotice) return;
    const sources = (status && status.aggregate && status.sources) || [];
    if (!sources.length) {
      el.sourceNotice.innerHTML = "";
      el.sourceNotice.hidden = true;
      return;
    }
    const rows = sources.map((item) => {
      const label = item.label || item.source || "来源";
      const outcome = item.status === "ok"
        ? `${item.count || 0} 篇`
        : item.status === "timeout" ? "未返回（超时）" : item.status === "skipped" ? "未参与" : "未返回（失败）";
      const detail = item.detail ? `：${item.detail}` : "";
      return `<li>${esc(label)} — ${esc(outcome)}${esc(detail)}</li>`;
    }).join("");
    const headline = status.fallback
      ? "在线来源本轮没有取到结果，下面显示的是内置演示语料，不是实时检索结果。"
      : "多源聚合检索的各来源本轮状态：";
    el.sourceNotice.hidden = false;
    el.sourceNotice.innerHTML = `<div class="warn-box">${esc(headline)}<ul class="source-notice-list">${rows}</ul></div>`;
  }

  async function doSearch() {
    const query = (el.queryInput.value || "").trim();
    if (!query) {
      toast("先输入一个研究方向", true);
      return;
    }
    setLoadingText(el.searchBtn, "检索中…");
    skeleton();
    el.resultMeta.textContent = "正在检索、分层与评估题录…";
    try {
      const data = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({
          query,
          limit: Number(el.limitSelect.value),
          source: el.sourceSelect.value,
        }),
      });
      state.papers = data.papers || [];
      state.searchPlan = data.plan || null;
      state.layerFilter = "";
      state.selected.clear();
      el.selectAll.checked = false;
      updateCompareCount();
      const cached = (data.papers || []).some((paper) => paper.cached);
      const layers = data.layers || {};
      el.resultMeta.textContent = `${data.count} 篇 · ${sourceLabelFor(el.sourceSelect.value)}${cached ? " · 缓存结果" : ""}${data.count ? ` · 直接 ${layers["直接相关"] || 0} / 方法 ${layers["方法参考"] || 0} / 背景 ${layers["背景脉络"] || 0}` : ""}`;
      renderSourceNotice(data.source_status);
      renderLayerFilter();
      renderResults();
      if (state.papers.length) {
        const first = visiblePapers()[0] || state.papers[0];
        await openPaper(first.id, true);
        document.querySelector(".main").scrollTo({ top: 0, behavior: "smooth" });
      } else {
        el.panel.innerHTML = emptyState("没有找到匹配论文", "换一个关键词，或切换到多源并发聚合/arXiv 在线检索。");
      }
    } catch (error) {
      el.resultsList.innerHTML = `<div class="warn-box">检索失败：${esc(error.message)}</div>`;
      el.resultMeta.textContent = "检索失败";
      renderSourceNotice(null);
      toast(error.message, true);
    } finally {
      restoreButton(el.searchBtn);
    }
  }

  function renderResults() {
    if (!state.papers.length) {
      el.resultsList.innerHTML = `<div class="warn-box">暂无结果。</div>`;
      return;
    }
    const papers = visiblePapers();
    if (!papers.length) {
      el.resultsList.innerHTML = `<div class="warn-box">当前分层下没有结果；切换到“全部”或换一个检索词。</div>`;
      return;
    }
    el.selectAll.checked = papers.length > 0 && papers.every((paper) => state.selected.has(paper.id));
    el.resultsList.innerHTML = papers.map((paper, index) => {
      const checked = state.selected.has(paper.id) ? "checked" : "";
      const active = state.activePaper === paper.id ? "active" : "";
      const selectedClass = state.selected.has(paper.id) ? "selected" : "";
      const sourceBadge = paper.source === "upload"
        ? badge("上传", "pink")
        : paper.source && paper.source.startsWith("custom:")
          ? badge(paper.source_label || "自定义来源", "pink")
        : paper.source === "crossref"
          ? badge("Crossref", "green")
        : paper.source === "europepmc"
          ? badge("Europe PMC", "green")
        : paper.source === "doaj"
          ? badge("DOAJ", "green")
        : paper.source === "hal"
          ? badge("HAL", "green")
          : paper.demo ? badge("演示", "amber") : badge("arXiv", "green");
      const layerKind = paper.layer === "直接相关" ? "green" : paper.layer === "方法参考" ? "amber" : "muted";
      const layerBadge = paper.layer ? badge(paper.layer, layerKind) : "";
      const cred = paper.credibility || {};
      const credKind = cred.level === "高" ? "green" : cred.level === "中" ? "amber" : "pink";
      const credBadge = cred.score !== undefined ? badge(`可信度 ${cred.score}`, credKind) : "";
      const sourceTypeBadge = paper.source_type ? badge(paper.source_type, "soft") : "";
      const textBadge = paper.full_text ? badge("全文", "soft") : badge("摘要级", "muted");
      const cacheBadge = paper.cached ? badge("缓存", "amber") : "";
      const aggregateBadge = paper.aggregate && (paper.aggregate_sources || []).length > 1
        ? badge(`多源 ${paper.aggregate_sources.length}`, "pink")
        : "";
      const fallbackBadge = paper.aggregate_fallback ? badge("回退演示语料", "amber") : "";
      const highlight = (paper.highlights || []).slice(0, 1).join(" · ");
      const metricLine = (paper.metrics_preview || []).slice(0, 2).map((item) => `${item.key} ${item.value}`).join(" · ");
      const saved = isSaved(paper.id);
      return `
        <article class="result-card ${active} ${selectedClass}" data-id="${esc(paper.id)}">
          <button class="result-save ${saved ? "saved" : ""}" type="button" data-save-id="${esc(paper.id)}" title="${saved ? "已收藏，点击移除" : "收藏到我的库"}" aria-label="${saved ? "已收藏，点击移除" : "收藏到我的库"}">${saved ? "★" : "☆"}</button>
          <button class="result-add" type="button" data-add-project="${esc(paper.id)}" title="加入项目引用池" aria-label="加入项目引用池">加入项目</button>
          <div class="result-top">
            <input class="result-check" type="checkbox" data-id="${esc(paper.id)}" ${checked} aria-label="选择 ${esc(shortTitle(paper.title, 30))}" />
            <div class="result-index">${String(index + 1).padStart(2, "0")}</div>
            <div style="min-width:0;flex:1">
              <h3 class="result-title">${esc(paper.title)}</h3>
              <div class="result-meta">
                <span>${esc(paper.year || "—")}</span>
                <span>${esc(paper.venue || "—")}</span>
                <span>${esc(authorsText(paper.authors))}</span>
              </div>
              <p class="result-tldr">${esc(paper.tldr || "摘要待生成。")}</p>
              <div class="result-badges">${layerBadge}${credBadge}${sourceTypeBadge}${sourceBadge}${textBadge}${cacheBadge}${aggregateBadge}${fallbackBadge}</div>
              ${paper.layer_reason ? `<div class="result-layer-note">${esc(paper.layer_reason)}</div>` : ""}
              ${highlight || metricLine ? `<div class="result-highlight">${esc(highlight || metricLine)}</div>` : ""}
            </div>
          </div>
        </article>`;
    }).join("");
  }

  function toggleSelect(id, checked) {
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    renderResults();
    updateCompareCount();
  }

  function updateCompareCount() {
    el.compareCount.textContent = String(state.selected.size);
    if (state.tab === "compare") loadTab("compare");
  }

  function isSaved(id) {
    return state.library.some((item) => item.paper_id === id);
  }

  function updateLibraryStats(stats) {
    if (stats) state.libraryStats = stats;
    const total = state.libraryStats ? state.libraryStats.total : state.library.length;
    if (el.statLibrary) el.statLibrary.textContent = String(total);
    if (el.libraryCount) el.libraryCount.textContent = String(total);
  }

  async function refreshLibrary({ render = true } = {}) {
    try {
      const data = await api("/api/library");
      state.library = data.items || [];
      updateLibraryStats(data.stats);
      if (state.papers.length) renderResults();
      if (render && state.tab === "library") renderLibrary();
    } catch (_) {
      // Keep the current UI usable if the library endpoint is briefly unavailable.
    }
  }

  async function addToLibrary(id) {
    try {
      const data = await api("/api/library", {
        method: "POST",
        body: JSON.stringify({ paper_id: id }),
      });
      state.library = [data.item, ...state.library.filter((item) => item.paper_id !== id)];
      state.libraryHealth = null;
      updateLibraryStats(data.stats);
      if (state.papers.length) renderResults();
      if (state.tab === "library") renderLibrary();
      toast("已收藏到我的库");
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function removeFromLibrary(id) {
    try {
      const data = await api(`/api/library/${encodeURIComponent(id)}/delete`, { method: "POST" });
      state.library = state.library.filter((item) => item.paper_id !== id);
      state.libraryHealth = null;
      updateLibraryStats(data.stats);
      if (state.papers.length) renderResults();
      if (state.tab === "library") renderLibrary();
      toast("已从研究库移除");
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function pickProject() {
    const data = await api("/api/projects");
    const projects = data.projects || [];
    if (!projects.length) {
      toast("还没有研究项目，请先在“研究项目”里新建一个。", true);
      return "";
    }
    const lines = projects.map((project, index) => `${index + 1}. ${project.title}`).join("\n");
    const answer = (window.prompt(`加入哪个项目的引用池？\n${lines}\n输入序号：`, "1") || "").trim();
    const index = Number(answer) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= projects.length) {
      toast("未选择项目。", true);
      return "";
    }
    return projects[index].id;
  }

  async function addPaperToProject(paperId, projectId = "") {
    try {
      const pid = projectId || (await pickProject());
      if (!pid) return false;
      const data = await api(`/api/projects/${encodeURIComponent(pid)}/papers`, {
        method: "POST",
        body: JSON.stringify({ paper_ids: [paperId] }),
      });
      const missing = data.missing || [];
      if (missing.length) {
        toast(`论文未找到：${missing.join("、")}`, true);
        return false;
      }
      toast("已加入项目引用池");
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function toggleResultSave(id) {
    if (isSaved(id)) {
      await removeFromLibrary(id);
      if (state.read && state.activePaper === id) renderRead(state.read);
      return;
    }
    const saved = await addToLibrary(id);
    if (saved && state.read && state.activePaper === id) renderRead(state.read);
  }

  async function saveCurrentPaper() {
    if (!state.activePaper) return;
    if (isSaved(state.activePaper)) {
      switchTab("library");
      return;
    }
    const saved = await addToLibrary(state.activePaper);
    if (saved && state.read) renderRead(state.read);
  }

  function filteredLibrary() {
    const { status, query } = state.libraryFilter;
    const needle = query.trim().toLowerCase();
    return state.library.filter((item) => {
      if (status && item.status !== status) return false;
      if (!needle) return true;
      return [item.title, item.tldr, item.note, (item.tags || []).join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }

  function healthItemMap() {
    const map = {};
    ((state.libraryHealth && state.libraryHealth.items) || []).forEach((item) => {
      map[item.paper_id] = item;
    });
    return map;
  }

  function healthKind(score) {
    return score >= 80 ? "green" : score >= 55 ? "amber" : "pink";
  }

  function healthPanelHtml() {
    const health = state.libraryHealth;
    if (!health) return "";
    const items = (health.items || []).filter((item) => (item.issues || []).length).slice(0, 6);
    const groups = health.groups || [];
    return `
      <div class="health-panel card">
        <div class="health-head">
          <div>
            <h3 class="card-title">引用体检 <span class="badge badge-${healthKind(health.average_score)}">${esc(health.level || "—")}</span></h3>
            <div class="section-note">先补题录与重复项，再决定哪些文献进入写作引用池。</div>
          </div>
          <div class="chips">
            <button class="btn btn-small" type="button" data-health-rerun>重新体检</button>
            <button class="btn btn-small btn-primary" type="button" data-health-verify>核验 DOI（联网）</button>
          </div>
        </div>
        <div class="health-stats">
          <div><strong>${health.average_score}</strong><span>平均题录分</span></div>
          <div><strong>${health.ready}</strong><span>可引用</span></div>
          <div><strong>${health.needs_fix}</strong><span>待补项</span></div>
          <div><strong>${health.duplicate_groups}</strong><span>重复组</span></div>
          <div><strong>${health.doi_verified}/${health.doi_checked}</strong><span>DOI 核验</span></div>
        </div>
        <div class="health-actions">
          <div class="section-note">优先修复</div>
          <ol class="step-list">${(health.actions || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ol>
        </div>
        ${items.length ? `
          <div class="health-items">
            <div class="section-note">待补题录</div>
            ${items.map((item) => `
              <div class="health-item">
                <div class="health-item-main">
                  <strong>${esc(shortTitle(item.title, 52))}</strong>
                  <span class="badge badge-${healthKind(item.score)}">${item.score} 分</span>
                  ${item.doi_status === "verified" ? badge("DOI 已核", "green") : ""}
                  ${item.doi_status === "mismatch" ? badge("DOI 不一致", "pink") : ""}
                </div>
                <div class="health-issues">${(item.issues || []).slice(0, 4).map((issue) => `<span class="health-issue ${esc(issue.severity)}">${esc(issue.label)}</span>`).join("")}</div>
                <button class="btn btn-small btn-ghost" type="button" data-health-open="${esc(item.paper_id)}">打开记录</button>
              </div>
            `).join("")}
          </div>
        ` : `<div class="ok-box">题录没有明显缺口；写作前仍建议抽查 DOI 与作者顺序。</div>`}
        ${groups.length ? `
          <div class="health-groups">
            <div class="section-note">重复条目</div>
            ${groups.map((group) => `<div class="health-group"><strong>${esc(shortTitle(group.title, 64))}</strong><span>${esc((group.paper_ids || []).join(" / "))}</span><span class="section-note">${esc(group.suggestion)}</span></div>`).join("")}
          </div>
        ` : ""}
        <div class="section-note" style="margin-top:8px">${esc(health.note || "")}</div>
      </div>
    `;
  }

  async function runLibraryHealth(verify = false, button = null) {
    if (button) setLoadingText(button, verify ? "核验 DOI…" : "体检中…");
    try {
      const data = await api(`/api/library/health?verify=${verify ? "true" : "false"}`);
      state.libraryHealth = data;
      if (state.tab !== "library") switchTab("library");
      else renderLibrary();
      toast(verify
        ? `DOI 核验完成：${data.doi_verified}/${data.doi_checked} 通过`
        : "引用体检完成");
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) restoreButton(button);
    }
  }

  function renderLibrary() {
    const stats = state.libraryStats || { total: 0, statuses: {}, tags: [] };
    const statusCounts = stats.statuses || {};
    const items = filteredLibrary();
    const healthMap = healthItemMap();
    const statusButtons = [
      { id: "", label: "全部", count: stats.total || 0 },
      { id: "todo", label: "待读", count: statusCounts.todo || 0 },
      { id: "reading", label: "在读", count: statusCounts.reading || 0 },
      { id: "done", label: "已读", count: statusCounts.done || 0 },
    ];
    const statusOptions = (current) => [
      ["todo", "待读"], ["reading", "在读"], ["done", "已读"],
    ].map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");

    el.panel.innerHTML = `
      <div class="library-toolbar">
        <div class="library-stats">
          <div class="library-stat"><strong>${stats.total || 0}</strong><span>总收藏</span></div>
          <div class="library-stat"><strong>${statusCounts.todo || 0}</strong><span>待读</span></div>
          <div class="library-stat"><strong>${statusCounts.reading || 0}</strong><span>在读</span></div>
          <div class="library-stat"><strong>${statusCounts.done || 0}</strong><span>已读</span></div>
        </div>
        <div class="library-tools">
          <input id="librarySearch" type="search" placeholder="搜索标题、标签、笔记" value="${esc(state.libraryFilter.query)}" />
          <button class="btn btn-small" type="button" id="libraryHealthBtn">引用体检</button>
          <button class="btn btn-small" type="button" data-library-export="markdown">Markdown</button>
          <button class="btn btn-small" type="button" data-library-export="bibtex">BibTeX</button>
          <button class="btn btn-small" type="button" data-library-export="json">JSON 备份</button>
          <button class="btn btn-small" type="button" id="libraryImportBtn">导入</button>
          <input type="file" id="libraryImportInput" accept="application/json,.json" hidden />
        </div>
      </div>
      <div class="status-filter">
        ${statusButtons.map((item) => `<button class="status-filter-btn ${state.libraryFilter.status === item.id ? "active" : ""}" type="button" data-lib-status="${item.id}">${item.label} <span>${item.count}</span></button>`).join("")}
        ${(stats.tags || []).slice(0, 5).map((tag) => `<button class="chip" type="button" data-lib-tag="${esc(tag.name)}">#${esc(tag.name)} ${tag.count}</button>`).join("")}
      </div>
      ${healthPanelHtml()}
      ${items.length ? `<div class="library-grid">${items.map((item) => {
        const health = healthMap[item.paper_id];
        return `
        <article class="library-card" data-library-id="${esc(item.paper_id)}">
          <div class="library-head">
            <div style="min-width:0">
              <div class="library-title">${esc(item.title)}</div>
              <div class="library-meta">${esc(item.year || "—")} · ${esc(item.venue || "未注明来源")} · 收藏于 ${esc(item.added_at || "—")}</div>
            </div>
            <select class="status-select" data-library-status="${esc(item.paper_id)}" aria-label="阅读状态">${statusOptions(item.status)}</select>
          </div>
          <p class="library-tldr">${esc(item.tldr || item.abstract || "暂无摘要。")}</p>
          ${health ? `
            <div class="health-inline">
              ${badge(`题录 ${health.score}`, healthKind(health.score))}
              ${health.doi_status === "verified" ? badge("DOI 已核", "green") : ""}
              ${health.doi_status === "mismatch" ? badge("DOI 不一致", "pink") : ""}
              ${(health.issues || []).length ? `<span class="health-issue-count">${health.issues.length} 项待补</span>` : `<span class="health-ok">题录完整</span>`}
            </div>
            ${(health.issues || []).length ? `<details class="health-card-details"><summary>查看题录问题</summary><div class="health-card-issues">${health.issues.map((issue) => `<div><span class="health-issue ${esc(issue.severity)}">${esc(issue.label)}</span>${esc(issue.action)}</div>`).join("")}</div></details>` : ""}
          ` : ""}
          <div class="chips" style="margin-top:2px">
            ${(item.tags || []).length ? item.tags.map((tag) => `<button class="chip" type="button" data-lib-tag="${esc(tag)}">#${esc(tag)}</button>`).join("") : `<span class="section-note">还没有标签</span>`}
          </div>
          <div class="library-editor">
            <label class="library-field">标签（逗号分隔）
              <input data-library-tags="${esc(item.paper_id)}" value="${esc((item.tags || []).join(", "))}" placeholder="例如：ADC, 读出, 待复现" />
            </label>
            <label class="library-field">研究笔记
              <textarea data-library-note="${esc(item.paper_id)}" rows="3" placeholder="记录判断、疑问、可复现点…">${esc(item.note || "")}</textarea>
            </label>
          </div>
          <div class="library-actions">
            <button class="btn btn-small btn-primary" type="button" data-library-open="${esc(item.paper_id)}">打开精读</button>
            <button class="btn btn-small" type="button" data-library-add-project="${esc(item.paper_id)}">加入项目</button>
            <button class="btn btn-small" type="button" data-library-save="${esc(item.paper_id)}">保存笔记</button>
            <button class="btn btn-small btn-ghost" type="button" data-library-remove="${esc(item.paper_id)}">移除</button>
          </div>
        </article>
      `; }).join("")}</div>` : emptyState("研究库还是空的", "在检索结果或论文页点击星标收藏；之后可以打标签、写笔记、按状态筛选，并导出 Markdown。")}
    `;

    const search = $("#librarySearch");
    if (search) {
      search.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          state.libraryFilter.query = search.value;
          renderLibrary();
        }
      });
    }
    const healthButton = $("#libraryHealthBtn");
    if (healthButton) {
      healthButton.addEventListener("click", () => runLibraryHealth(false, healthButton));
    }
    $$("[data-health-rerun]", el.panel).forEach((button) => {
      button.addEventListener("click", () => runLibraryHealth(false, button));
    });
    $$("[data-health-verify]", el.panel).forEach((button) => {
      button.addEventListener("click", () => runLibraryHealth(true, button));
    });
    $$("[data-health-open]", el.panel).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.healthOpen, true));
    });
    $$("[data-library-export]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const format = button.dataset.libraryExport || "markdown";
        const link = document.createElement("a");
        link.href = `/api/library/export?format=${encodeURIComponent(format)}&t=${Date.now()}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        toast(`研究库 ${format === "json" ? "JSON" : format === "bibtex" ? "BibTeX" : "Markdown"} 已导出`);
      });
    });
    const importButton = $("#libraryImportBtn");
    const importInput = $("#libraryImportInput");
    if (importButton && importInput) {
      importButton.addEventListener("click", () => importInput.click());
      importInput.addEventListener("change", async () => {
        const file = importInput.files && importInput.files[0];
        importInput.value = "";
        if (!file) return;
        try {
          const payload = JSON.parse(await file.text());
          const data = await api("/api/library/import", {
            method: "POST",
            body: JSON.stringify(payload),
          });
          state.libraryHealth = null;
          await refreshLibrary({ render: false });
          renderLibrary();
          toast(`导入 ${data.imported} 条，跳过 ${data.skipped} 条`);
        } catch (error) {
          toast(error.message || "备份文件无法解析", true);
        }
      });
    }
    $$("[data-lib-status]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        state.libraryFilter.status = button.dataset.libStatus || "";
        renderLibrary();
      });
    });
    $$("[data-lib-tag]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        state.libraryFilter.query = button.dataset.libTag || "";
        state.libraryFilter.status = "";
        renderLibrary();
      });
    });
    $$("[data-library-open]", el.panel).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.libraryOpen, true));
    });
    $$("[data-library-status]", el.panel).forEach((select) => {
      select.addEventListener("change", async () => {
        try {
          const data = await api(`/api/library/${encodeURIComponent(select.dataset.libraryStatus)}/update`, {
            method: "POST",
            body: JSON.stringify({ status: select.value }),
          });
          state.library = state.library.map((item) => item.paper_id === data.item.paper_id ? data.item : item);
          updateLibraryStats(data.stats);
          renderLibrary();
          toast("阅读状态已更新");
        } catch (error) {
          toast(error.message, true);
        }
      });
    });
    $$("[data-library-save]", el.panel).forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.dataset.librarySave;
        const tagsInput = $(`[data-library-tags="${CSS.escape(id)}"]`, el.panel);
        const noteInput = $(`[data-library-note="${CSS.escape(id)}"]`, el.panel);
        const tags = (tagsInput ? tagsInput.value : "").split(/[,，;；]/).map((tag) => tag.trim()).filter(Boolean);
        try {
          const data = await api(`/api/library/${encodeURIComponent(id)}/update`, {
            method: "POST",
            body: JSON.stringify({ tags, note: noteInput ? noteInput.value : "" }),
          });
          state.library = state.library.map((item) => item.paper_id === data.item.paper_id ? data.item : item);
          updateLibraryStats(data.stats);
          renderLibrary();
          toast("标签与笔记已保存");
        } catch (error) {
          toast(error.message, true);
        }
      });
    });
    $$("[data-library-remove]", el.panel).forEach((button) => {
      button.addEventListener("click", () => removeFromLibrary(button.dataset.libraryRemove));
    });
    $$("[data-library-add-project]", el.panel).forEach((button) => {
      button.addEventListener("click", () => addPaperToProject(button.dataset.libraryAddProject));
    });
  }

  async function loadLibrary() {
    await refreshLibrary({ render: false });
    renderLibrary();
  }

  const AGENT_STATUS = {
    queued: ["排队中", "soft"],
    running: ["执行中", "amber"],
    paused: ["已暂停", "amber"],
    awaiting_approval: ["等待确认", "green"],
    completed: ["已完成", "green"],
    failed: ["失败", "pink"],
    cancelled: ["已取消", "muted"],
    interrupted: ["已中断", "amber"],
  };

  const AGENT_STEP_STATUS = {
    pending: ["待执行", "muted"],
    running: ["执行中", "amber"],
    done: ["完成", "green"],
    failed: ["失败", "pink"],
    skipped: ["已跳过", "muted"],
  };

  const AGENT_TOOL_LABELS = {
    search: "检索",
    read: "精读",
    ask: "追问",
    compare: "对比",
    topic: "选题",
    draft: "写作骨架",
    review: "核查",
    report: "简报",
  };

  function agentToolLabel(tool) {
    return AGENT_TOOL_LABELS[tool] || tool || "动作";
  }

  function stopAgentPolling() {
    if (state.agentTimer) {
      clearInterval(state.agentTimer);
      state.agentTimer = null;
    }
  }

  function startAgentPolling(runId) {
    stopAgentPolling();
    state.agentTimer = setInterval(async () => {
      if (state.tab !== "agent" || !state.agentRun || state.agentRun.id !== runId) {
        stopAgentPolling();
        return;
      }
      try {
        const data = await api(`/api/agent/runs/${encodeURIComponent(runId)}`);
        state.agentRun = data.run;
        if (!["queued", "running"].includes(data.run.status)) {
          stopAgentPolling();
          refreshAgentList();
        }
        renderAgentRun(data.run);
      } catch (_) {
        stopAgentPolling();
      }
    }, 1500);
  }

  function agentSourceOptions() {
    const options = [
      ["demo", "内置演示语料"],
      ["auto", "多源并发聚合"],
      ["arxiv", "arXiv 在线"],
      ["crossref", "Crossref 在线"],
      ["europepmc", "Europe PMC"],
      ["doaj", "DOAJ 开放获取"],
      ["hal", "HAL 开放获取"],
      ["upload", "本地 PDF"],
    ];
    (state.meta?.custom_sources || []).forEach((item) => {
      if (item?.id) options.push([`custom:${item.id}`, item.name || item.id]);
    });
    return options;
  }

  function agentBudget(run) {
    const budget = run.budget || {};
    const used = run.budget_used || {};
    return {
      actions: [Number(used.actions || 0), Number(budget.max_actions || 14)],
      seconds: [Number(used.seconds || 0), Number(budget.max_seconds || 300)],
      papers: [(run.paper_ids || []).length, Number(budget.max_papers || 6)],
      llm: [Number(used.llm_calls || 0), Number(budget.max_llm_calls || 4)],
    };
  }

  function agentLatest(run, tool) {
    const steps = (run.steps || []).filter((step) => step.tool === tool && step.status === "done");
    return steps.length ? steps[steps.length - 1] : null;
  }

  function agentIsSearchCheckpoint(run) {
    if (run.status !== "awaiting_approval") return false;
    const plan = run.plan || {};
    const last = (run.steps || []).length ? run.steps[run.steps.length - 1] : null;
    return last?.tool === "search" && plan.tool === "read";
  }

  function agentIsTopicCheckpoint(run) {
    if (run.status !== "awaiting_approval") return false;
    const last = (run.steps || []).length ? run.steps[run.steps.length - 1] : null;
    return last?.tool === "topic" && ["draft", "ask", "report"].includes(run.plan?.tool || "");
  }

  function agentLaunchShell() {
    const options = agentSourceOptions();
    const searchSource = ($("#sourceSelect")?.value || "").trim();
    const agentSource = state.agentSourceCustom ? state.agentSource : (searchSource || state.agentSource);
    const queryGoal = ($("#queryInput")?.value || "").trim();
    const goalValue = state.agentGoal && state.agentGoal !== "面向存算一体的低功耗 ADC 设计"
      ? state.agentGoal
      : (queryGoal || state.agentGoal);
    const modes = [
      ["checkpoint", "关键点确认（推荐）"],
      ["auto", "全自动"],
      ["step", "逐步确认"],
    ];
    return `
      <section class="agent-launch card">
        <div class="agent-launch-head">
          <div>
            <div class="eyebrow">Adaptive Research Agent</div>
            <h2>给定目标，Agent 自己决定下一步</h2>
            <p>不是固定七步：Planner 根据证据缺口、饱和度和预算选择检索、精读、追问、对比、选题、写作或收束；每一步都可以审计。</p>
          </div>
          <span class="badge badge-soft">计划 · 执行 · 批判 · 重规划</span>
        </div>
        <div class="agent-form-grid">
          <label class="agent-field agent-field-wide">
            <span>研究目标</span>
            <textarea id="agentGoal" rows="2" maxlength="300" placeholder="例如：面向存算一体的低功耗 ADC 设计">${esc(goalValue)}</textarea>
          </label>
          <label class="agent-field">
            <span>检索来源</span>
            <select id="agentSource">
              ${options.map(([value, label]) => `<option value="${esc(value)}" ${agentSource === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
            </select>
          </label>
          <label class="agent-field">
            <span>自主程度</span>
            <select id="agentMode">
              ${modes.map(([value, label]) => `<option value="${value}" ${state.agentMode === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
            </select>
          </label>
          <label class="agent-field">
            <span>动作预算</span>
            <select id="agentMaxActions">
              ${[10, 14, 20].map((value) => `<option value="${value}" ${state.agentMaxActions === value ? "selected" : ""}>${value} 个动作</option>`).join("")}
            </select>
          </label>
          <label class="agent-field">
            <span>论文预算</span>
            <select id="agentMaxPapers">
              ${[3, 4, 5, 6, 8].map((value) => `<option value="${value}" ${state.agentMaxPapers === value ? "selected" : ""}>${value} 篇</option>`).join("")}
            </select>
          </label>
        </div>
        <div class="agent-launch-actions">
          <button class="btn btn-primary" type="button" data-agent-start>启动自适应研究</button>
          <span class="section-note">默认在检索、选题、证据不足与收束前暂停；离线可完整运行。</span>
        </div>
      </section>
    `;
  }

  function agentRunsList(runs) {
    if (!runs.length) {
      return `<section class="agent-list card"><div class="agent-list-head"><h3>历史任务</h3><span class="muted small">还没有任务</span></div><div class="agent-empty">启动一个任务后，Agent 会按证据自主规划，并留下证据账本与动作轨迹。</div></section>`;
    }
    return `
      <section class="agent-list card">
        <div class="agent-list-head"><h3>历史任务</h3><span class="muted small">${runs.length} 个</span></div>
        <div class="agent-run-list">
          ${runs.map((run) => {
            const status = AGENT_STATUS[run.status] || [run.status, "muted"];
            return `
              <button class="agent-run-card" type="button" data-agent-run="${esc(run.id)}">
                <div class="agent-run-main">
                  <strong>${esc(shortTitle(run.goal, 72))}</strong>
                  <div class="agent-run-meta">${esc(run.mode || "checkpoint")} · ${run.action_count || 0} 动作 · ${run.evidence_count || 0} 证据${run.plan_tool ? ` · 下一步 ${esc(agentToolLabel(run.plan_tool))}` : ""} · ${esc(run.updated_at || "")}</div>
                </div>
                ${run.legacy ? badge("v0.9", "muted") : ""}${badge(status[0], status[1])}
              </button>
            `;
          }).join("")}
        </div>
      </section>
    `;
  }

  async function loadAgent() {
    stopAgentPolling();
    try {
      const data = await api("/api/agent/runs");
      state.agentRuns = data.runs || [];
    } catch (error) {
      toast(error.message, true);
    }
    if (state.agentRun) {
      try {
        const data = await api(`/api/agent/runs/${encodeURIComponent(state.agentRun.id)}`);
        state.agentRun = data.run;
      } catch (_) {
        state.agentRun = null;
        window.PaperPilotLive?.disconnect();
        stopAgentPolling();
      }
    }
    renderAgent();
    if (state.agentRun && ["queued", "running"].includes(state.agentRun.status)) {
      startAgentPolling(state.agentRun.id);
    }
  }

  function renderAgent() {
    if (state.agentRun) {
      renderAgentRun(state.agentRun);
      return;
    }
    el.panel.innerHTML = `<div class="agent-shell">${agentLaunchShell()}${agentRunsList(state.agentRuns || [])}</div>`;
  }

  async function refreshAgentList() {
    try {
      const data = await api("/api/agent/runs");
      state.agentRuns = data.runs || [];
    } catch (_) {
      return;
    }
    if (!state.agentRun && state.tab === "agent") {
      el.panel.innerHTML = `<div class="agent-shell">${agentLaunchShell()}${agentRunsList(state.agentRuns)}</div>`;
    }
  }

  async function startAgentRun() {
    const goal = ($("#agentGoal")?.value || "").trim();
    if (goal.length < 2) {
      toast("先写一个研究目标", true);
      return;
    }
    const source = $("#agentSource")?.value || "demo";
    const mode = $("#agentMode")?.value || "checkpoint";
    const maxActions = Number($("#agentMaxActions")?.value || 14);
    const maxPapers = Number($("#agentMaxPapers")?.value || 6);
    state.agentGoal = goal;
    state.agentSource = source;
    state.agentMode = mode;
    state.agentMaxActions = maxActions;
    state.agentMaxPapers = maxPapers;
    const button = $("[data-agent-start]");
    if (button) setLoadingText(button, "规划中…");
    try {
      const data = await api("/api/agent/runs", {
        method: "POST",
        body: JSON.stringify({
          goal,
          source,
          mode,
          max_actions: maxActions,
          max_papers: maxPapers,
        }),
      });
      state.agentRun = data.run;
      state.agentSelectionRun = "";
      state.agentTopicRun = "";
      renderAgentRun(data.run);
      await refreshAgentList();
      if (["queued", "running"].includes(data.run.status)) startAgentPolling(data.run.id);
    } catch (error) {
      toast(error.message, true);
      if (button) restoreButton(button);
    }
  }

  async function openAgentRun(runId) {
    stopAgentPolling();
    try {
      const data = await api(`/api/agent/runs/${encodeURIComponent(runId)}`);
      state.agentRun = data.run;
      state.agentSelectionRun = "";
      state.agentTopicRun = "";
      renderAgentRun(data.run);
      if (["queued", "running"].includes(data.run.status)) startAgentPolling(data.run.id);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function agentAction(runId, action, extra = {}) {
    try {
      const data = await api(`/api/agent/runs/${encodeURIComponent(runId)}/action`, {
        method: "POST",
        body: JSON.stringify({ action, ...extra }),
      });
      state.agentRun = data.run;
      state.agentSelectionRun = "";
      state.agentTopicRun = "";
      renderAgentRun(data.run);
      await refreshAgentList();
      if (["queued", "running"].includes(data.run.status)) startAgentPolling(data.run.id);
      else stopAgentPolling();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteAgentRun(runId) {
    if (!window.confirm("删除这个研究任务及其报告？")) return;
    try {
      await api(`/api/agent/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
      state.agentRun = null;
      window.PaperPilotLive?.disconnect();
      stopAgentPolling();
      await loadAgent();
      toast("任务已删除");
    } catch (error) {
      toast(error.message, true);
    }
  }

  function agentBudgetRow(label, used, max) {
    const pct = Math.min(100, Math.round((used / Math.max(max, 1)) * 100));
    return `
      <div class="agent-budget-row">
        <div><span>${esc(label)}</span><strong>${used}/${max}</strong></div>
        <div class="agent-budget-track"><span style="width:${pct}%"></span></div>
      </div>
    `;
  }

  function agentSidePanels(run) {
    const budget = agentBudget(run);
    const evidence = run.evidence || [];
    const questions = run.open_questions || [];
    const open = questions.filter((item) => item.status === "open");
    const decisions = run.decisions || [];
    return `
      <div class="agent-side-card">
        <div class="agent-side-head"><h3>预算</h3>${badge(run.mode || "checkpoint", "soft")}</div>
        ${agentBudgetRow("动作", budget.actions[0], budget.actions[1])}
        ${agentBudgetRow("论文", budget.papers[0], budget.papers[1])}
        ${agentBudgetRow("时间（秒）", budget.seconds[0], budget.seconds[1])}
        ${agentBudgetRow("LLM", budget.llm[0], budget.llm[1])}
      </div>
      <div class="agent-side-card">
        <div class="agent-side-head"><h3>证据账本</h3><span class="muted small">${evidence.length} 条</span></div>
        ${evidence.length ? `<div class="agent-evidence-list">${evidence.slice(-10).reverse().map((item) => `
          <div class="agent-evidence-item">
            <div class="agent-evidence-head"><strong>${esc(shortTitle(item.title || item.paper_id || "证据", 40))}</strong>${item.verified ? badge("已核对", "green") : badge("待核对", "amber")}</div>
            <div class="agent-evidence-quote">“${esc(shortTitle(item.quote, 150))}”</div>
            <div class="agent-evidence-meta">${esc(item.section || "原文")} · p.${esc(item.page || "—")} · ${esc(item.kind || "text")}</div>
          </div>
        `).join("")}</div>` : `<div class="agent-empty">还没有证据入账。</div>`}
      </div>
      <div class="agent-side-card">
        <div class="agent-side-head"><h3>开放问题</h3><span class="muted small">${open.length} 个</span></div>
        ${open.length ? `<div class="agent-question-list">${open.slice(0, 8).map((item) => `
          <div class="agent-question-item">
            <div>${badge(item.priority || "medium", item.priority === "high" ? "pink" : "amber")} ${esc(item.question)}</div>
            <div class="agent-question-meta">${esc(item.kind || "evidence")} · ${esc(item.source || "critic")}${item.resolution ? ` · ${esc(item.resolution)}` : ""}</div>
          </div>
        `).join("")}</div>` : `<div class="agent-empty">没有未解决问题。</div>`}
      </div>
      <div class="agent-side-card">
        <div class="agent-side-head"><h3>人工决策</h3><span class="muted small">${decisions.length} 次</span></div>
        ${decisions.length ? `<div class="agent-decisions">${decisions.slice(-6).reverse().map((item) => `
          <div><b>${esc(item.kind)}</b>${item.choice ? `：${esc(shortTitle(Array.isArray(item.choice) ? item.choice.join(" / ") : item.choice, 90))}` : ""}${item.note ? `<span> · ${esc(item.note)}</span>` : ""}</div>
        `).join("")}</div>` : `<div class="agent-empty">还没有人工决策。</div>`}
      </div>
    `;
  }

  function agentPlanCard(run) {
    const plan = run.plan;
    if (run.status === "completed") {
      return `<section class="agent-plan-card card"><div class="eyebrow">任务完成</div><h3>研究简报已生成</h3><p>Agent 在 ${(run.steps || []).length} 个动作内完成当前证据边界内的探索；未解决问题保留在账本与简报中。</p></section>`;
    }
    if (run.status === "cancelled") {
      return `<section class="agent-plan-card card"><div class="eyebrow">任务已取消</div><h3>没有继续执行</h3><p>可以返回列表启动新任务。</p></section>`;
    }
    if (!plan) {
      return `<section class="agent-plan-card card"><div class="eyebrow">下一步计划</div><h3>等待 Planner 计算</h3><p>Agent 正在根据证据账本与预算决定下一步。</p></section>`;
    }
    return `
      <section class="agent-plan-card card">
        <div class="agent-plan-main">
          <div class="eyebrow">下一步计划</div>
          <h3>${esc(plan.title || agentToolLabel(plan.tool))}</h3>
          <p>${esc(plan.rationale || "")}</p>
          <div class="agent-plan-meta">
            ${badge(agentToolLabel(plan.tool), "soft")}
            ${plan.requires_approval ? badge("需要确认", "amber") : badge("自动执行", "muted")}
            ${run.critic?.next_hint ? badge(`Critic · ${run.critic.next_hint}`, "muted") : ""}
          </div>
        </div>
        <div class="agent-plan-side">
          <div><span>动作</span><strong>${agentBudget(run).actions[0]}/${agentBudget(run).actions[1]}</strong></div>
          <div><span>证据</span><strong>${(run.evidence || []).length}</strong></div>
          <div><span>问题</span><strong>${(run.open_questions || []).filter((item) => item.status === "open").length}</strong></div>
        </div>
        ${run.critic?.last_observation ? `<div class="agent-plan-observation">Critic：${esc(run.critic.last_observation)}</div>` : ""}
      </section>
    `;
  }

  function agentArtifact(step, run) {
    const tool = step.tool || step.id;
    const artifact = step.artifact || {};
    if (tool === "search") {
      const candidates = artifact.candidates || [];
      const awaiting = agentIsSearchCheckpoint(run) && agentLatest(run, "search")?.id === step.id;
      if (!candidates.length) return "";
      return `
        <details class="agent-artifact" ${awaiting ? "open" : ""}>
          <summary>检索候选与证据分层 <span>${candidates.length} 篇</span></summary>
          ${artifact.query ? `<div class="agent-note">查询：${esc(artifact.query)}${artifact.new_ids?.length ? ` · 新增 ${artifact.new_ids.length} 篇` : ""}</div>` : ""}
          <div class="agent-candidates">
            ${candidates.map((paper) => `
              <label class="agent-candidate ${awaiting ? "selectable" : ""}">
                ${awaiting ? `<input type="checkbox" data-agent-pick="${esc(paper.id)}" ${state.agentSelection.has(paper.id) ? "checked" : ""}>` : ""}
                <div class="agent-candidate-body">
                  <div class="agent-candidate-head">
                    <strong>${esc(shortTitle(paper.title, 74))}</strong>
                    ${badge(paper.layer || "待分层", paper.layer === "直接相关" ? "green" : paper.layer === "方法参考" ? "amber" : "soft")}
                  </div>
                  <div class="agent-candidate-meta">${esc(paper.year || "—")} · ${esc(paper.venue || "未提供 venue")} · ${esc(paper.source || "—")}</div>
                  ${paper.layer_reason ? `<div class="agent-candidate-reason">${esc(paper.layer_reason)}</div>` : ""}
                </div>
              </label>
            `).join("")}
          </div>
          ${awaiting ? `<div class="agent-note">勾选要进入精读的论文，然后点下方“批准并继续”。最多 ${run.budget?.max_papers || 6} 篇。</div>` : ""}
        </details>
      `;
    }
    if (tool === "read") {
      const paper = artifact.paper;
      if (paper) {
        return `
          <details class="agent-artifact">
            <summary>精读摘要 <span>${esc(paper.title || paper.id)}</span></summary>
            <div class="agent-read-card">
              <strong>${esc(paper.title || paper.id)}</strong>
              <p>${esc(artifact.tldr || "未生成摘要")}</p>
              ${artifact.problem ? `<div><b>问题：</b>${esc(artifact.problem)}</div>` : ""}
              ${artifact.method ? `<div><b>方法：</b>${esc(artifact.method)}</div>` : ""}
              ${(artifact.limitations || []).length ? `<div><b>局限：</b>${esc((artifact.limitations || []).join("；"))}</div>` : ""}
            </div>
          </details>
        `;
      }
      const papers = artifact.papers || [];
      if (!papers.length) return "";
      return `
        <details class="agent-artifact">
          <summary>精读摘要 <span>${papers.length} 篇</span></summary>
          <div class="agent-read-grid">
            ${papers.map((item) => `
              <div class="agent-read-card">
                <strong>${esc(shortTitle(item.title, 64))}</strong>
                <p>${esc(item.tldr || "未生成摘要")}</p>
              </div>
            `).join("")}
          </div>
        </details>
      `;
    }
    if (tool === "ask") {
      if (!artifact.answer && !(artifact.citations || []).length) return "";
      return `
        <details class="agent-artifact" open>
          <summary>追问结果 <span>${(artifact.citations || []).length} 条引用</span></summary>
          <div class="agent-answer">${esc(artifact.answer || "")}</div>
          <div class="agent-evidence-meta">${esc(artifact.question || "")}${artifact.verification ? ` · 核对 ${artifact.verification.verified || 0}/${artifact.verification.total || 0}` : ""}</div>
        </details>
      `;
    }
    if (tool === "compare") {
      const blocks = [
        ["共识", artifact.consensus || [], "green"],
        ["分歧", artifact.divergence || [], "pink"],
        ["注意", artifact.warnings || [], "amber"],
      ];
      if (!blocks.some(([, items]) => items.length)) return "";
      return `
        <details class="agent-artifact">
          <summary>对比结论 <span>${(artifact.rows || []).length} 行</span></summary>
          <div class="agent-insight-grid">
            ${blocks.map(([label, items, kind]) => `
              <div class="agent-insight">
                <div class="agent-insight-head">${badge(label, kind)}</div>
                ${(items || []).map((item) => `<div>${esc(item)}</div>`).join("") || `<span class="muted">无</span>`}
              </div>
            `).join("")}
          </div>
        </details>
      `;
    }
    if (tool === "topic") {
      const questions = artifact.questions || [];
      if (!questions.length) return "";
      const awaiting = agentIsTopicCheckpoint(run) && agentLatest(run, "topic")?.id === step.id;
      return `
        <details class="agent-artifact" ${awaiting ? "open" : ""}>
          <summary>候选问题与论证卡 <span>${questions.length} 个</span></summary>
          <div class="agent-question-list">
            ${questions.map((question, index) => {
              const card = question.argument_card || {};
              return `
                <label class="agent-question ${awaiting ? "selectable" : ""}">
                  <div class="agent-question-head">
                    <div><b>Q${index + 1}. ${esc(question.title || question.question || "候选问题")}</b>${badge(`可行性 ${question.feasibility ?? "—"}`, "soft")}</div>
                    ${awaiting ? `<input type="radio" name="agentTopic" data-agent-topic="${esc(question.id || index)}" ${String(state.agentTopic) === String(question.id || index) ? "checked" : ""}>` : ""}
                  </div>
                  ${card.gap ? `<div><b>缺口：</b>${esc(card.gap)}</div>` : ""}
                  ${card.method ? `<div><b>最小方法：</b>${esc(card.method)}</div>` : ""}
                  ${card.success_criterion ? `<div><b>成功判据：</b>${esc(card.success_criterion)}</div>` : ""}
                  ${card.failure_mode ? `<div><b>失败模式：</b>${esc(card.failure_mode)}</div>` : ""}
                </label>
              `;
            }).join("")}
          </div>
          ${awaiting ? `<div class="agent-note">选择一个方向后批准；Agent 会在该方向下生成写作骨架。</div>` : ""}
        </details>
      `;
    }
    if (tool === "draft") {
      if (!artifact.markdown) return "";
      return `
        <details class="agent-artifact">
          <summary>写作骨架 <span>${artifact.markdown.length} 字符</span></summary>
          <pre class="agent-markdown">${esc(artifact.markdown)}</pre>
        </details>
      `;
    }
    if (tool === "review") {
      const claim = artifact.claim_summary || {};
      const argument = artifact.argument || {};
      if (!Object.keys(claim).length && !Object.keys(argument).length) return "";
      return `
        <details class="agent-artifact" open>
          <summary>核查摘要 <span>${esc(argument.verdict || claim.overall_label || "")}</span></summary>
          <div class="agent-review">
            <div>逐句核查：${claim.total || 0} 句 · 支持 ${claim.supported || 0} · 部分 ${claim.partial || 0} · 不足 ${claim.unsupported || 0}</div>
            ${argument.score !== undefined ? `<div>论证体检：${esc(argument.score)} 分 · ${esc(argument.verdict || "—")}</div>` : ""}
            ${(argument.actions || []).length ? `<ul>${(argument.actions || []).map((action) => `<li>${esc(action)}</li>`).join("")}</ul>` : ""}
          </div>
        </details>
      `;
    }
    if (tool === "report") {
      if (!artifact.chars && !artifact.mode) return "";
      return `
        <div class="agent-report-line">
          ${badge(artifact.mode === "llm" ? "模型整理" : "离线模板", artifact.mode === "llm" ? "green" : "soft")}
          <span>${esc(artifact.chars || 0)} 字符</span>
          ${run.report?.path ? `<button class="btn btn-small" type="button" data-agent-report="${esc(run.id)}">下载 Markdown</button>` : ""}
        </div>
      `;
    }
    return "";
  }

  function agentStepCard(step, index, run) {
    const status = AGENT_STEP_STATUS[step.status] || [step.status, "muted"];
    const artifact = agentArtifact(step, run);
    const observation = step.observation || {};
    return `
      <article class="agent-step ${esc(step.status)}">
        <div class="agent-step-rail"><span class="agent-step-dot"></span></div>
        <div class="agent-step-body">
          <div class="agent-step-head">
            <div class="agent-step-title"><span class="agent-step-index">${String(index + 1).padStart(2, "0")}</span><h3>${esc(step.title || agentToolLabel(step.tool))}</h3>${badge(agentToolLabel(step.tool), "muted")}</div>
            ${badge(status[0], status[1])}
          </div>
          <div class="agent-step-meta">
            ${step.started_at ? `开始 ${esc(step.started_at)}` : "尚未开始"}
            ${step.finished_at ? ` · 完成 ${esc(step.finished_at)}` : ""}
            ${step.attempts > 1 ? ` · 第 ${step.attempts} 次尝试` : ""}
          </div>
          ${step.rationale ? `<p class="agent-step-rationale">为什么：${esc(step.rationale)}</p>` : ""}
          ${observation.summary ? `<p class="agent-step-observation">Critic：${esc(observation.summary)}</p>` : ""}
          ${step.error ? `<div class="agent-step-error">${esc(step.error)}</div>` : ""}
          ${artifact}
        </div>
      </article>
    `;
  }

  function agentActionBar(run) {
    if (run.status === "awaiting_approval") {
      const searchCheckpoint = agentIsSearchCheckpoint(run);
      const topicCheckpoint = agentIsTopicCheckpoint(run);
      const plan = run.plan || {};
      const title = searchCheckpoint ? "核对检索与选文" : topicCheckpoint ? "核对候选方向" : `下一步：${plan.title || agentToolLabel(plan.tool)}`;
      const text = searchCheckpoint
        ? "改选论文后批准，Agent 会用新的论文集合重新规划。"
        : topicCheckpoint
          ? "选择一个候选方向后批准，写作骨架会围绕该方向生成。"
          : (plan.rationale || "批准后执行当前计划。");
      const label = searchCheckpoint || topicCheckpoint || run.mode !== "step" ? "批准并继续" : "执行下一步";
      return `
        <div class="agent-actionbar">
          <div><strong>${esc(title)}</strong><span>${esc(text)}</span></div>
          <div class="agent-actions">
            <button class="btn btn-primary" type="button" data-agent-action="approve">${esc(label)}</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="stop">停止并生成简报</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="cancel">取消任务</button>
          </div>
        </div>
      `;
    }
    if (run.status === "failed" || run.status === "interrupted") {
      return `
        <div class="agent-actionbar">
          <div><strong>${run.status === "failed" ? "动作执行失败" : "上次进程中断"}</strong><span>可以恢复，让 Planner 重新规划；也可以停止并生成当前简报。</span></div>
          <div class="agent-actions">
            <button class="btn btn-primary" type="button" data-agent-action="resume">恢复执行</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="retry">重试</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="stop">停止并生成简报</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="cancel">取消任务</button>
          </div>
        </div>
      `;
    }
    if (["queued", "running"].includes(run.status)) {
      return `
        <div class="agent-actionbar">
          <div><strong>${run.status === "queued" ? "任务已排队" : "Agent 正在执行"}</strong><span>${esc(run.plan?.title || "等待下一步规划")} · 页面每 1.5 秒刷新；可以停止探索并立即收束。</span></div>
          <div class="agent-actions">
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="stop">停止并生成简报</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-action="cancel">取消任务</button>
          </div>
        </div>
      `;
    }
    if (run.status === "completed") {
      return `
        <div class="agent-actionbar">
          <div><strong>任务完成</strong><span>研究简报包含证据账本、未解决问题、预算与动作轨迹。</span></div>
          <div class="agent-actions">
            ${run.report?.path ? `<button class="btn btn-primary" type="button" data-agent-report="${esc(run.id)}">下载研究简报</button>` : ""}
            <button class="btn btn-ghost btn-small" type="button" data-agent-delete="${esc(run.id)}">删除任务</button>
          </div>
        </div>
      `;
    }
    return `
      <div class="agent-actionbar">
        <div><strong>任务已取消</strong><span>可以返回列表启动新任务。</span></div>
        <div class="agent-actions"><button class="btn btn-ghost btn-small" type="button" data-agent-delete="${esc(run.id)}">删除任务</button></div>
      </div>
    `;
  }

  function renderAgentRun(run) {
    stopAgentPolling();
    const searchStep = agentLatest(run, "search");
    const selected = (run.paper_ids || []).length ? run.paper_ids : (searchStep?.artifact?.selected || []);
    if (state.agentSelectionRun !== run.id || (selected.length && state.agentSelection.size === 0)) {
      state.agentSelection = new Set(selected);
      state.agentSelectionRun = run.id;
    }
    const topicStep = agentLatest(run, "topic");
    if (state.agentTopicRun !== run.id) {
      const topicQuestions = topicStep?.artifact?.questions || [];
      state.agentTopic = run.selected_topic?.id || topicQuestions[0]?.id || null;
      state.agentTopicRun = run.id;
    }
    const steps = run.steps || [];
    const status = AGENT_STATUS[run.status] || [run.status, "muted"];
    const budget = agentBudget(run);
    const openCount = (run.open_questions || []).filter((item) => item.status === "open").length;
    el.panel.innerHTML = `
      <div class="agent-detail">
        <section class="agent-head card">
          <div class="agent-head-main">
            <div class="eyebrow">Adaptive Research Agent</div>
            <h2 class="agent-title">${esc(run.goal)}</h2>
            <div class="agent-meta">
              ${badge(status[0], status[1])}
              ${badge(`模式 ${run.mode || "checkpoint"}`, "soft")}
              ${badge(`${steps.length} 个动作`, "muted")}
              ${badge(`${(run.evidence || []).length} 条证据`, "muted")}
              ${badge(`${openCount} 个开放问题`, openCount ? "amber" : "green")}
              ${badge(`更新 ${run.updated_at || "—"}`, "muted")}
              ${run.legacy ? badge("v0.9 迁移", "muted") : ""}
            </div>
          </div>
          <div class="agent-actions">
            <button class="btn btn-ghost btn-small" type="button" data-agent-back>返回任务列表</button>
            <button class="btn btn-ghost btn-small" type="button" data-agent-refresh>刷新</button>
            ${run.report?.path ? `<button class="btn btn-small" type="button" data-agent-report="${esc(run.id)}">下载简报</button>` : ""}
          </div>
        </section>
        ${agentPlanCard(run)}
        <div id="agent-live-bar" class="agent-live-bar"></div>
        <div class="agent-workbench">
          <div class="agent-main">
            <div class="agent-progress card">
              <div class="agent-progress-head"><span>动作预算</span><strong>${budget.actions[0]} / ${budget.actions[1]}</strong></div>
              <div class="agent-progress-track"><span style="width:${Math.min(100, Math.round(budget.actions[0] / Math.max(budget.actions[1], 1) * 100))}%"></span></div>
              <div class="section-note">证据 ${(run.evidence || []).length} 条 · 论文 ${budget.papers[0]}/${budget.papers[1]} · 开放问题 ${openCount}${run.selected_topic ? ` · 方向：${esc(run.selected_topic.title || run.selected_topic.id)}` : ""}</div>
            </div>
            <div class="agent-timeline">
              ${steps.length ? steps.map((step, index) => agentStepCard(step, index, run)).join("") : `<div class="agent-empty">Planner 正在建立第一个动作。</div>`}
            </div>
          </div>
          <aside class="agent-side">${agentSidePanels(run)}</aside>
        </div>
        ${agentActionBar(run)}
      </div>
    `;
    if (state.tab === "agent" && ["queued", "running"].includes(run.status)) {
      startAgentPolling(run.id);
    }
    if (window.PaperPilotLive) {
      window.PaperPilotLive.mount(el.panel, run, {
        onSnapshot: async (snapshot) => {
          const isCurrentRun = () => state.tab === "agent" && Boolean(state.agentRun) && snapshot.id === state.agentRun.id;
          if (!snapshot || !snapshot.id || !isCurrentRun()) return;
          let next = null;
          try {
            const data = await api(`/api/agent/runs/${encodeURIComponent(snapshot.id)}`);
            next = data.run;
          } catch (_) {
            next = state.agentRun ? { ...state.agentRun, ...snapshot } : null;
          }
          if (!isCurrentRun()) return;
          if (!next) return;
          state.agentRun = next;
          renderAgentRun(next);
        },
        onFallback: () => { if (state.agentRun) startAgentPolling(state.agentRun.id); },
      });
    }
  }

  function handleAgentChange(event) {
    if (event.target.id === "agentSource") {
      state.agentSource = event.target.value;
      state.agentSourceCustom = true;
      return;
    }
    const pick = event.target.closest("[data-agent-pick]");
    if (pick) {
      if (pick.checked) {
        const limit = state.agentRun?.budget?.max_papers || 6;
        if (state.agentSelection.size >= limit) {
          pick.checked = false;
          toast(`最多保留 ${limit} 篇论文`, true);
          return;
        }
        state.agentSelection.add(pick.dataset.agentPick);
      } else {
        state.agentSelection.delete(pick.dataset.agentPick);
      }
      return;
    }
    const topic = event.target.closest("[data-agent-topic]");
    if (topic && topic.checked) {
      state.agentTopic = topic.dataset.agentTopic;
    }
  }

  function handleAgentClick(event) {
    const start = event.target.closest("[data-agent-start]");
    if (start) { startAgentRun(); return; }
    const back = event.target.closest("[data-agent-back]");
    if (back) {
      state.agentRun = null;
      window.PaperPilotLive?.disconnect();
      stopAgentPolling();
      loadAgent();
      return;
    }
    const refresh = event.target.closest("[data-agent-refresh]");
    if (refresh && state.agentRun) {
      stopAgentPolling();
      openAgentRun(state.agentRun.id);
      return;
    }
    const report = event.target.closest("[data-agent-report]");
    if (report) {
      window.open(`/api/agent/runs/${encodeURIComponent(report.dataset.agentReport)}/report`, "_blank");
      return;
    }
    const remove = event.target.closest("[data-agent-delete]");
    if (remove) { deleteAgentRun(remove.dataset.agentDelete); return; }
    const runButton = event.target.closest("[data-agent-run]");
    if (runButton && !event.target.closest("[data-agent-action]")) {
      openAgentRun(runButton.dataset.agentRun);
      return;
    }
    const actionButton = event.target.closest("[data-agent-action]");
    if (actionButton && state.agentRun) {
      const action = actionButton.dataset.agentAction;
      const run = state.agentRun;
      const extra = {};
      if (action === "approve") {
        if (agentIsSearchCheckpoint(run)) {
          extra.paper_ids = Array.from(state.agentSelection);
          if (!extra.paper_ids.length) {
            toast("至少保留一篇论文", true);
            return;
          }
        }
        if (agentIsTopicCheckpoint(run)) {
          if (state.agentTopic === null || state.agentTopic === undefined || state.agentTopic === "") {
            toast("先选择一个候选方向", true);
            return;
          }
          extra.topic_id = String(state.agentTopic);
        }
      }
      agentAction(run.id, action, extra);
    }
  }

  function askShell() {
    const libraryTotal = state.libraryStats ? state.libraryStats.total : 0;
    const samples = [
      "五篇论文分别如何降低 ADC 开销？",
      "随机 1-bit 读出的代价是什么？",
      "SNN 的 Top-K 早停如何实现？",
      "哪些工作用了可重构阵列？",
    ];
    return `
      <section class="ask-head">
        <div>
          <div class="eyebrow">Evidence Q&amp;A</div>
          <h2>问一个问题，答案回到页码</h2>
          <p>在全部语料、我的研究库或当前勾选论文中检索证据。每条回答都附章节、页码和原文引文；离线模式下使用抽取式拼接，不编造原文没有的结论。</p>
        </div>
        <div class="ask-facts">
          <div><strong>${libraryTotal}</strong><span>篇在库</span></div>
          <div><strong>${state.selected.size}</strong><span>篇已勾选</span></div>
        </div>
      </section>
      <div class="ask-box card">
        <textarea id="askInput" rows="3" maxlength="400" placeholder="例如：这些工作如何降低 ADC 的能耗？各自的代价是什么？">${esc(state.askQuestion)}</textarea>
        <div class="ask-controls">
          <select id="askScope" aria-label="问答范围">
            <option value="all" ${state.askScope === "all" ? "selected" : ""}>全部语料（含研究库）</option>
            <option value="library" ${state.askScope === "library" ? "selected" : ""}>我的研究库</option>
            <option value="selected" ${state.askScope === "selected" ? "selected" : ""}>左侧勾选论文（${state.selected.size} 篇）</option>
          </select>
          <button class="btn btn-primary" type="button" id="askBtn">搜索证据并回答</button>
        </div>
        <div class="chips" id="askChips">
          ${samples.map((sample) => `<button class="chip" type="button" data-question="${esc(sample)}">${esc(sample)}</button>`).join("")}
        </div>
        <div class="section-note" style="margin-top:8px">快捷键：在输入框中按 Ctrl + Enter 直接提问。</div>
      </div>
      <div id="askResult" class="ask-result"></div>
    `;
  }

  function bindAskEvents() {
    const button = $("#askBtn");
    if (button) button.addEventListener("click", () => doAsk());
    const input = $("#askInput");
    if (input) input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        doAsk();
      }
    });
    const chips = $("#askChips");
    if (chips) chips.addEventListener("click", (event) => {
      const chip = event.target.closest(".chip");
      if (!chip) return;
      state.askQuestion = chip.dataset.question || "";
      if (input) input.value = state.askQuestion;
      doAsk(state.askQuestion);
    });
  }

  async function loadAsk() {
    el.panel.innerHTML = askShell();
    bindAskEvents();
    if (state.ask) {
      renderAskResult(state.ask);
    } else {
      const result = $("#askResult");
      if (result) result.innerHTML = emptyState("等待一个研究问题", "可以从上方示例开始；也可以先勾选论文，再选择“左侧勾选论文”。");
    }
  }

  function formatAnswer(text) {
    return esc(text || "")
      .replace(/\[(\d+)\]/g, '<span class="cite-ref">[$1]</span>')
      .replace(/\n/g, "<br>");
  }

  async function doAsk(questionOverride = null) {
    const input = $("#askInput");
    const question = String(questionOverride || (input ? input.value : "")).trim();
    const scope = $("#askScope") ? $("#askScope").value : "all";
    if (!question) {
      toast("先输入一个问题", true);
      return;
    }
    if (scope === "selected" && state.selected.size === 0) {
      toast("先在左侧勾选至少一篇论文", true);
      return;
    }
    state.askQuestion = question;
    state.askScope = scope;
    if (input) input.value = question;
    const button = $("#askBtn");
    if (button) setLoadingText(button, "检索证据中…");
    try {
      const data = await api("/api/ask", {
        method: "POST",
        body: JSON.stringify({
          question,
          scope,
          paper_ids: Array.from(state.selected),
          top_k: 6,
        }),
      });
      state.ask = data;
      renderAskResult(data);
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) restoreButton(button);
    }
  }

  function renderAskResult(data) {
    const result = $("#askResult");
    if (!result) return;
    const citations = data.citations || [];
    const support = data.support || [];
    const summary = data.support_summary || { direct: 0, partial: 0, none: 0 };
    const intent = data.intent || { label: "综合检索" };
    const confidence = data.confidence || { level: "low", label: "低", reason: "" };
    const plan = data.plan || [];
    const retrievalMeta = data.retrieval || null;
    const verification = data.citation_verification || null;
    const retrievalLabel = retrievalMeta
      ? (retrievalMeta.mode === "embedding" ? "向量" : retrievalMeta.mode === "hybrid" ? "混合" : "BM25")
      : "BM25";
    const confidenceKind = confidence.level === "high" ? "green" : confidence.level === "medium" ? "amber" : "pink";
    result.innerHTML = `
      <div class="answer-card">
        <div class="answer-head">
          <div style="min-width:0">
            <div class="eyebrow">证据回答</div>
            <h3>${esc(data.question)}</h3>
          </div>
          <div class="answer-badges">
            ${data.mode === "llm" ? badge("模型综合", "green") : badge("抽取式", "soft")}
            ${badge(`检索 ${retrievalLabel}`, "soft")}
            ${verification ? badge(`引用核对 ${verification.verified}/${verification.total}`, verification.verified === verification.total ? "green" : "amber") : ""}
            ${badge(intent.label, "muted")}
            ${badge(`检索置信度 ${confidence.label}`, confidenceKind)}
            ${badge(data.scope_label || "全部语料", "muted")}
          </div>
        </div>
        <div class="answer-text">${formatAnswer(data.answer)}</div>
        <div class="answer-meta">证据覆盖 ${data.matched_papers ?? "—"} / ${data.papers_considered} 篇 · 直接 ${summary.direct} / 部分 ${summary.partial} / 未命中 ${summary.none} · ${citations.length} 条引用 · ${data.quantity_count || 0} 个可核对数值</div>
        ${plan.length > 1 ? `<div class="ask-plan">${plan.map((item) => `<span>${esc(item.label)} <b>${item.citation_count}</b></span>`).join("")}</div>` : ""}
        ${support.length ? `<div class="support-grid">${support.map((item) => `
          <button class="support-chip ${esc(item.level)}" type="button" data-support-open="${esc(item.paper_id)}" title="${esc(item.title)}">
            <span>${esc(shortTitle(item.title, 42))}</span>
            <b>${esc(item.label)}</b>
          </button>
        `).join("")}</div>` : ""}
        <div class="ask-trace">
          ${(data.trace || []).map((step) => `<span><strong>${esc(step.step)}</strong>${esc(step.detail)}</span>`).join("")}
        </div>
        <div class="section-note">证据边界：${esc(confidence.reason)} ${esc(data.note || "")}</div>
      </div>
      ${citations.length ? `<div class="citation-grid">${citations.map((item) => `
        <article class="citation-card">
          <div class="citation-head">
            <span class="citation-index">${item.index}</span>
            <div style="min-width:0">
              <div class="citation-title">${esc(item.title)}</div>
              <div class="citation-source">${esc(item.section || "原文")} · p.${esc(item.page || "—")}${item.evidence_type === "field" ? " · 字段证据" : ""}</div>
              <div class="citation-verify">${item.quote_verified ? badge("原文已核对", "green") : badge("待核对", "amber")}</div>
              ${(item.aspects || []).length ? `<div class="citation-aspects">${(item.aspects || []).map((aspect) => `<span>${esc(aspect)}</span>`).join("")}</div>` : ""}
            </div>
          </div>
          <div class="citation-quote">“${esc(item.quote)}”</div>
          ${(item.quantities || []).length ? `<div class="quantity-row">${(item.quantities || []).map((quantity) => `<span class="quantity-chip" title="原文数值，需结合测试条件核对">${esc(quantity.text)}</span>`).join("")}</div>` : ""}
          <div class="citation-actions">
            <button class="btn btn-small" type="button" data-cite-open="${esc(item.paper_id)}">打开精读</button>
            ${isSaved(item.paper_id) ? badge("已在库中", "green") : `<button class="btn btn-small btn-ghost" type="button" data-cite-save="${esc(item.paper_id)}">收藏</button>`}
          </div>
        </article>
      `).join("")}</div>` : `<div class="warn-box">没有检索到可引用证据：试试更换关键词，或把范围扩大到“全部语料”。</div>`}
    `;
    $$("[data-support-open]", result).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.supportOpen, true));
    });
    $$("[data-cite-open]", result).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.citeOpen, true));
    });
    $$("[data-cite-save]", result).forEach((button) => {
      button.addEventListener("click", async () => {
        const saved = await addToLibrary(button.dataset.citeSave);
        if (saved) renderAskResult(data);
      });
    });
  }

  function scopeOptions(current) {
    return [
      ["all", "全部语料"],
      ["library", "我的研究库"],
      ["selected", `左侧勾选（${state.selected.size} 篇）`],
    ].map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
  }

  function topicShell() {
    const queryTopic = ($("#queryInput")?.value || "").trim();
    const topicValue = state.topicQuery && state.topicQuery !== "面向存算一体的低功耗 ADC"
      ? state.topicQuery
      : (queryTopic || state.topicQuery);
    const samples = queryTopic ? [queryTopic] : [];
    return `
      <section class="topic-head">
        <div>
          <div class="eyebrow">Research Topic Studio</div>
          <h2>从一个方向，走到可验证的选题</h2>
          <p>先看研究版图和方法簇，再从证据缺口生成候选问题、三阶段阅读路径与最小实验计划。所有候选都标出证据来源，不把“未来工作”直接当作创新点。</p>
        </div>
      </section>
      <div class="topic-controls card">
        <input id="topicQuery" type="text" maxlength="200" value="${esc(topicValue)}" placeholder="例如：面向存算一体的低功耗 ADC" />
        <select id="topicScope" aria-label="选题范围">${scopeOptions(state.topicScope)}</select>
        <button class="btn btn-primary" type="button" id="topicBtn">生成选题地图</button>
        <div class="chips">
          ${samples.map((item) => `<button class="chip" type="button" data-topic-sample="${esc(item)}">${esc(item)}</button>`).join("")}
        </div>
      </div>
      <div id="topicResult" class="topic-result"></div>
    `;
  }

  async function loadTopic() {
    el.panel.innerHTML = topicShell();
    bindTopicEvents();
    if (state.topic) renderTopic(state.topic);
    else {
      const result = $("#topicResult");
      if (result) result.innerHTML = emptyState("等待一个研究方向", "输入方向后，PaperPilot 会整理方法簇、证据缺口、候选选题和阅读路径。");
    }
  }

  function bindTopicEvents() {
    const button = $("#topicBtn");
    const input = $("#topicQuery");
    if (button) button.addEventListener("click", () => doTopic());
    if (input) input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        doTopic();
      }
    });
    $$("[data-topic-sample]", el.panel).forEach((chip) => {
      chip.addEventListener("click", () => {
        state.topicQuery = chip.dataset.topicSample;
        if (input) input.value = state.topicQuery;
        doTopic();
      });
    });
  }

  async function doTopic() {
    const input = $("#topicQuery");
    const topic = (input ? input.value : state.topicQuery).trim();
    const scope = $("#topicScope") ? $("#topicScope").value : "all";
    if (!topic) {
      toast("先输入一个研究方向", true);
      return;
    }
    if (scope === "selected" && state.selected.size === 0) {
      toast("先在左侧勾选至少一篇论文", true);
      return;
    }
    state.topicQuery = topic;
    state.topicScope = scope;
    const button = $("#topicBtn");
    if (button) setLoadingText(button, "整理版图…");
    const result = $("#topicResult");
    if (result) result.innerHTML = `<div class="loading-line"></div>${emptyState("正在生成选题地图", "聚类方法、扫描证据缺口、安排阅读路径…")}`;
    try {
      const data = await api("/api/topic", {
        method: "POST",
        body: JSON.stringify({ topic, scope, paper_ids: Array.from(state.selected) }),
      });
      state.topic = data;
      renderTopic(data);
    } catch (error) {
      toast(error.message, true);
      if (result) result.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    } finally {
      if (button) restoreButton(button);
    }
  }

  function feasibilityBars(detail) {
    if (!detail) return "";
    return `
      <div class="question-feas">
        <div class="question-feas-head"><span>四维可行性</span><span class="badge badge-${detail.verdict === "可开题" ? "green" : detail.verdict === "需收窄" ? "amber" : "pink"}">${esc(detail.verdict)} · ${detail.overall}/5</span></div>
        <div class="feas-bars">
          ${(detail.dimensions || []).map((item) => `
            <div class="feas-row">
              <span>${esc(item.label)}</span>
              <div class="feas-bar"><i class="${item.score >= 4 ? "good" : item.score >= 3 ? "mid" : "low"}" style="width:${item.score * 20}%"></i></div>
              <b>${item.score}</b>
            </div>
          `).join("")}
        </div>
        <div class="section-note">${esc(detail.summary)}</div>
      </div>
    `;
  }

  function argumentCardHtml(card) {
    if (!card) return "";
    return `
      <details class="argument-card">
        <summary>论证卡：缺口 → 证据 → 最小实验</summary>
        <div class="argument-grid">
          <div><span class="section-note">想回答的问题</span><p>${esc(card.claim)}</p></div>
          <div><span class="section-note">缺口</span><p>${esc(card.gap)}</p></div>
          <div><span class="section-note">最小方法</span><p>${esc(card.method)}</p></div>
          <div><span class="section-note">数据与指标</span><p>${esc((card.data || []).join("、") || "待定")}；${esc((card.metric || []).join("、") || "待定")}</p></div>
          <div><span class="section-note">成功判据</span><p>${esc(card.success_criterion)}</p></div>
          <div><span class="section-note">失败模式</span><p>${esc(card.failure_mode)}</p></div>
        </div>
        <div class="section-note">证据</div>
        <div class="insight-list">${(card.evidence || []).map((item) => `<div class="insight-item">${esc(item)}</div>`).join("") || `<div class="section-note">待补全文证据。</div>`}</div>
        <div class="section-note" style="margin-top:6px">${esc(card.scope || "")}</div>
      </details>
    `;
  }

  function advisorCardHtml(advisor) {
    if (!advisor || !(advisor.questions || []).length) return "";
    return `
      <div class="card advisor-card">
        <h3 class="card-title">导师沟通 <span class="badge badge-pink">先自己判断，再问导师</span></h3>
        <div class="advisor-grid">
          <div class="advisor-onepager">
            <div class="section-note">一页纸 brief</div>
            <pre>${esc(advisor.one_pager || "")}</pre>
            <div class="chips">
              <button class="btn btn-small" type="button" id="copyAdvisor">复制一页纸</button>
              <button class="btn btn-small" type="button" id="exportAdvisor">导出沟通稿</button>
            </div>
          </div>
          <div class="advisor-questions">
            <div class="section-note">带选项的提问</div>
            ${(advisor.questions || []).map((item, index) => `
              <div class="advisor-q">
                <div><strong>Q${index + 1}. ${esc(item.question)}</strong></div>
                <div class="chips">${(item.options || []).map((option) => `<span class="chip">${esc(option)}</span>`).join("")}</div>
                <div class="section-note">${esc(item.why)}</div>
              </div>
            `).join("")}
          </div>
        </div>
        <div class="advisor-anticipated">
          <div class="section-note">导师可能追问，先准备</div>
          ${(advisor.anticipated || []).map((item) => `
            <div class="advisor-qa">
              <strong>${esc(item.question)}</strong>
              <span>${esc(item.preparation)}</span>
              <span class="section-note">${esc(item.evidence)}</span>
            </div>
          `).join("")}
        </div>
        <div class="advisor-template">
          <div class="section-note">发给导师的话</div>
          <p>${esc(advisor.ask_template || "")}</p>
          <button class="btn btn-small btn-ghost" type="button" id="copyAskTemplate">复制这段话</button>
        </div>
      </div>
    `;
  }

  function advisorMarkdown(data) {
    const advisor = data.advisor || {};
    const lines = ["# 导师沟通稿", "", advisor.one_pager || "", ""];
    if (advisor.questions || []) {
      lines.push("## 带选项的提问", "");
      advisor.questions.forEach((item, index) => {
        lines.push(`### Q${index + 1}. ${item.question}`, "", ...(item.options || []).map((option) => `- ${option}`), "", `为什么先问这个：${item.why}`, "");
      });
    }
    if (advisor.anticipated || []) {
      lines.push("## 导师可能追问", "");
      advisor.anticipated.forEach((item) => {
        lines.push(`- **${item.question}** 准备：${item.preparation} 证据：${item.evidence}`);
      });
      lines.push("");
    }
    lines.push("## 发给导师的话", "", advisor.ask_template || "", "", `> ${data.note || ""}`);
    return lines.join("\n");
  }

  function topicMarkdown(data) {
    const lines = [
      `# 选题工作台：${data.topic}`,
      "",
      `> ${data.note}`,
      "",
      `范围：${data.scope_label} · ${data.stats.papers} 篇论文 · ${data.stats.clusters} 个方法簇`,
      "",
      "## 候选研究问题",
      "",
    ];
    (data.questions || []).forEach((item, index) => {
      const detail = item.feasibility_detail || {};
      const card = item.argument_card || {};
      lines.push(
        `### ${index + 1}. ${item.title}`,
        "",
        item.question,
        "",
        `- 为什么是空白：${item.why_gap}`,
        `- 评分：新颖性 ${item.novelty} / 可行性 ${item.feasibility} / 影响 ${item.impact}`,
        `- 四维可行性：${(detail.dimensions || []).map((dim) => `${dim.label} ${dim.score}/5`).join("；")}；结论：${detail.verdict}`,
        `- 最小实验：${item.experiment}`,
        `- 成功判据：${card.success_criterion || "待补充"}`,
        `- 失败模式：${card.failure_mode || "待补充"}`,
        "",
      );
    });
    lines.push("## 阅读路径", "");
    (data.reading_path || []).forEach((stage) => {
      lines.push(`### ${stage.label}`, "");
      (stage.papers || []).forEach((paper) => {
        lines.push(`- [${paper.cite}] ${paper.title}（${paper.year || "—"}）：${paper.why}`);
      });
      lines.push("");
    });
    lines.push(
      "## 实验计划",
      "",
      `- 数据集：${(data.experiment_plan.datasets || []).join("、")}`,
      `- 指标：${(data.experiment_plan.metrics || []).join("、")}`,
      `- baseline：${(data.experiment_plan.baselines || []).join("、")}`,
      `- 最小实验：${data.experiment_plan.minimal_experiment}`,
      "",
      "## 写作 brief",
      "",
      `**工作标题**：${data.brief.working_title}`,
      "",
      data.brief.thesis,
      "",
      ...(data.brief.contributions || []).map((item) => `- ${item}`),
      "",
    );
    const advisor = data.advisor || {};
    if (advisor.one_pager) {
      lines.push("## 导师沟通", "", advisor.one_pager, "");
      if (advisor.questions || []) {
        lines.push("### 带选项的提问", "");
        advisor.questions.forEach((item, index) => {
          lines.push(`#### Q${index + 1}. ${item.question}`, "", ...(item.options || []).map((option) => `- ${option}`), "", `为什么：${item.why}`, "");
        });
      }
      if (advisor.anticipated || []) {
        lines.push("### 导师可能追问", "");
        advisor.anticipated.forEach((item) => {
          lines.push(`- **${item.question}** 准备：${item.preparation} 证据：${item.evidence}`);
        });
      }
    }
    return lines.join("\n");
  }

  function renderTopic(data) {
    const result = $("#topicResult");
    if (!result) return;
    const clusters = data.clusters || [];
    const questions = data.questions || [];
    const reading = data.reading_path || [];
    const plan = data.experiment_plan || {};
    const brief = data.brief || {};
    result.innerHTML = `
      <div class="topic-stats">
        <div><strong>${data.stats.papers}</strong><span>篇论文</span></div>
        <div><strong>${data.stats.clusters}</strong><span>个方法簇</span></div>
        <div><strong>${questions.length}</strong><span>个候选问题</span></div>
        <div><strong>${esc(data.stats.year_range)}</strong><span>年份范围</span></div>
      </div>
      <div class="topic-grid">
        <div class="card">
          <h3 class="card-title">研究版图 <span class="badge badge-soft">${esc(data.scope_label)}</span></h3>
          ${clusters.map((cluster) => `
            <div class="cluster-row">
              <div class="cluster-head"><strong>${esc(cluster.label)}</strong><span>${cluster.count} 篇 · ${cluster.share}%</span></div>
              <div class="cluster-bar"><i style="width:${Math.max(4, cluster.share)}%"></i></div>
              <div class="chips">${(cluster.papers || []).map((paper) => `<button class="chip" type="button" data-topic-open="${esc(paper.paper_id)}">${esc(paper.cite)} · ${esc(shortTitle(paper.title, 30))}</button>`).join("")}</div>
            </div>
          `).join("") || `<div class="section-note">当前范围没有足够材料形成方法簇。</div>`}
          <div class="section-note" style="margin-top:10px">年份分布：${(data.timeline || []).map((item) => `${item.year}（${item.count}）`).join(" · ") || "—"}</div>
        </div>
        <div class="card">
          <h3 class="card-title">写作 brief <span class="badge badge-pink">待验证</span></h3>
          <div class="brief-title">${esc(brief.working_title || "")}</div>
          <p class="card-sub">${esc(brief.thesis || "")}</p>
          <div class="section-note">计划贡献</div>
          <div class="insight-list">${(brief.contributions || []).map((item) => `<div class="insight-item">${esc(item)}</div>`).join("")}</div>
          <div class="chips" style="margin-top:9px">${(brief.keywords || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div>
          <div class="section-note" style="margin-top:8px">${esc(brief.scope || "")}</div>
          <div class="topic-brief-actions">
            <button class="btn btn-small" type="button" id="copyTopicBrief">复制 brief</button>
            <button class="btn btn-small" type="button" id="exportTopic">导出选题 Markdown</button>
          </div>
        </div>
      </div>
      ${advisorCardHtml(data.advisor)}
      <div class="card" style="margin-top:12px">
        <h3 class="card-title">候选研究问题 <span class="badge badge-green">${questions.length} 个</span></h3>
        <div class="question-grid">
          ${questions.map((item, index) => `
            <article class="question-card">
              <div class="question-top"><span class="question-index">Q${index + 1}</span>${badge(item.source === "curated" ? "证据缺口" : "论文局限", item.source === "curated" ? "green" : "amber")}</div>
              <div class="question-title">${esc(item.title)}</div>
              <p class="question-text">${esc(item.question)}</p>
              <div class="section-note">${esc(item.why_gap)}</div>
              <div class="chips" style="margin-top:7px">${(item.evidence || []).slice(0, 2).map((evidence) => `<span class="chip">${esc(shortTitle(evidence, 46))}</span>`).join("")}</div>
              <div class="question-scores">
                <span>新颖性 <b>${esc(item.novelty)}</b></span><span>可行性 <b>${esc(item.feasibility)}</b></span><span>影响 <b>${esc(item.impact)}</b></span>
              </div>
              ${feasibilityBars(item.feasibility_detail)}
              ${argumentCardHtml(item.argument_card)}
              <div class="question-exp"><strong>最小实验：</strong>${esc(item.experiment)}</div>
              <button class="btn btn-small btn-primary" type="button" data-topic-write="${esc(item.title)}">用这个方向写作</button>
            </article>
          `).join("") || `<div class="section-note">暂无候选问题。</div>`}
        </div>
      </div>
      <div class="card" style="margin-top:12px">
        <h3 class="card-title">阅读路径 <span class="badge badge-soft">按阶段</span></h3>
        <div class="reading-grid">
          ${reading.map((stage) => `
            <div class="reading-stage">
              <div class="reading-stage-title">${esc(stage.label)}</div>
              ${(stage.papers || []).map((paper) => `
                <div class="reading-card">
                  <div class="reading-card-top"><span class="badge badge-muted">${esc(paper.cite)}</span><span class="section-page">${esc(paper.year || "—")}</span></div>
                  <button class="reading-title" type="button" data-topic-open="${esc(paper.paper_id)}">${esc(paper.title)}</button>
                  <div class="section-note">${esc(paper.why)}</div>
                </div>
              `).join("") || `<div class="section-note">该阶段暂无额外论文。</div>`}
            </div>
          `).join("")}
        </div>
      </div>
      <div class="card" style="margin-top:12px">
        <h3 class="card-title">实验计划 <span class="badge badge-amber">先最小验证</span></h3>
        <div class="plan-grid">
          <div class="plan-block"><div class="section-note">数据集</div><div class="chips">${(plan.datasets || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div></div>
          <div class="plan-block"><div class="section-note">指标</div><div class="chips">${(plan.metrics || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div></div>
          <div class="plan-block"><div class="section-note">baseline</div><div class="chips">${(plan.baselines || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div></div>
          <div class="plan-block"><div class="section-note">工艺 / 验证</div><div class="chips">${(plan.hardware || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div></div>
        </div>
        <div class="plan-steps">
          <div class="section-note">执行步骤</div>
          <ol class="step-list">${(plan.steps || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ol>
          <div class="section-note" style="margin-top:8px">最小实验</div>
          <p class="card-sub">${esc(plan.minimal_experiment || "待补充")}</p>
          <div class="section-note">风险</div>
          <div class="chips">${(plan.risks || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div>
        </div>
      </div>
      <div class="section-note" style="margin-top:9px">${esc(data.note || "")}</div>
    `;
    $$("[data-topic-open]", result).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.topicOpen, true));
    });
    $$("[data-topic-write]", result).forEach((button) => {
      button.addEventListener("click", () => {
        state.writingTopic = `${data.topic}：${button.dataset.topicWrite}`;
        state.draft = null;
        state.check = null;
        state.argument = null;
        switchTab("writing");
      });
    });
    const copyButton = $("#copyTopicBrief");
    if (copyButton) copyButton.addEventListener("click", () => copyText(topicMarkdown(data)));
    const exportButton = $("#exportTopic");
    if (exportButton) exportButton.addEventListener("click", () => downloadMarkdown(topicMarkdown(data), "paperpilot-topic.md"));
    const copyAdvisor = $("#copyAdvisor");
    if (copyAdvisor) copyAdvisor.addEventListener("click", () => copyText((data.advisor || {}).one_pager || ""));
    const exportAdvisor = $("#exportAdvisor");
    if (exportAdvisor) exportAdvisor.addEventListener("click", () => downloadMarkdown(advisorMarkdown(data), "paperpilot-advisor.md"));
    const copyAsk = $("#copyAskTemplate");
    if (copyAsk) copyAsk.addEventListener("click", () => copyText((data.advisor || {}).ask_template || ""));
  }

  function writingShell() {
    const queryTopic = ($("#queryInput")?.value || "").trim();
    const writingValue = state.writingTopic && state.writingTopic !== "面向存算一体的低功耗 ADC"
      ? state.writingTopic
      : (queryTopic || state.writingTopic);
    return `
      <section class="writing-head">
        <div>
          <div class="eyebrow">Writing Studio</div>
          <h2>把证据变成可改的草稿，再做两层体检</h2>
          <p>先看“论证结构”是否回答了研究问题，再逐句检查证据与数值。模板只负责搭结构，不替代你的论证；体检结果会给出优先修改动作。</p>
        </div>
      </section>
      <div class="writing-controls card">
        <input id="writingTopic" type="text" maxlength="200" value="${esc(writingValue)}" placeholder="论文主题" />
        <select id="writingKind" aria-label="草稿类型">
          <option value="related_work" ${state.writingKind === "related_work" ? "selected" : ""}>相关工作</option>
          <option value="outline" ${state.writingKind === "outline" ? "selected" : ""}>论文提纲</option>
          <option value="abstract" ${state.writingKind === "abstract" ? "selected" : ""}>摘要骨架</option>
        </select>
        <select id="writingScope" aria-label="写作范围">${scopeOptions(state.writingScope)}</select>
        <button class="btn btn-primary" type="button" id="draftBtn">生成草稿</button>
      </div>
      <div id="draftResult" class="draft-result"></div>
      <div class="check-box card">
        <div class="check-head">
          <div><h3 class="card-title" style="margin-bottom:3px">草稿体检 <span class="badge badge-pink">结构 + 逐句</span></h3><div class="section-note">粘贴摘要/引言/相关工作。先做“论证体检”看结构、引用与未支撑主张，再做“逐句核查”核对数值。</div></div>
          <div class="chips"><button class="chip" type="button" id="checkSample">填入示例</button><button class="btn btn-small" type="button" id="argumentBtn">论证体检</button><button class="btn btn-small" type="button" id="checkBtn">逐句核查</button></div>
        </div>
        <textarea id="checkText" rows="4" maxlength="12000" placeholder="例如：本文提出一种低功耗 ADC，将能耗降低了 91.7%...">${esc(state.checkText)}</textarea>
        <div class="section-note" style="margin-top:6px">核查只判断“文本与检索证据的重合度 + 数值一致性”，不是事实真伪判定；论证体检检查结构、引用可追溯性与未支撑句。</div>
      </div>
      <div id="checkResult" class="check-result"></div>
      <div id="argumentResult" class="argument-result"></div>
    `;
  }

  async function loadWriting() {
    el.panel.innerHTML = writingShell();
    bindWritingEvents();
    if (state.draft) renderDraft(state.draft);
    else {
      const result = $("#draftResult");
      if (result) result.innerHTML = emptyState("先生成一份草稿", "选择相关工作、提纲或摘要骨架；生成后可一键送入体检。");
    }
    if (state.check) renderCheck(state.check);
    if (state.argument) renderArgument(state.argument);
  }

  function bindWritingEvents() {
    const draftButton = $("#draftBtn");
    if (draftButton) draftButton.addEventListener("click", () => doDraft());
    const checkButton = $("#checkBtn");
    if (checkButton) checkButton.addEventListener("click", () => doCheck());
    const argumentButton = $("#argumentBtn");
    if (argumentButton) argumentButton.addEventListener("click", () => doArgument());
    const sample = $("#checkSample");
    if (sample) sample.addEventListener("click", () => {
      const text = "本文面向存算一体中的 ADC 能耗瓶颈，提出一种低功耗读出方案。该方案将 ADC 能耗降低了 91.7%，并在 ViT-Base 上保持 84.48% 的 Top-1 精度。该方案已在所有工艺角下得到硅验证，并在所有数据集上取得 99.9% 的能效提升。";
      const area = $("#checkText");
      if (area) area.value = text;
      state.checkText = text;
    });
  }

  async function doDraft() {
    const topicInput = $("#writingTopic");
    const topic = (topicInput ? topicInput.value : state.writingTopic).trim();
    const kind = $("#writingKind") ? $("#writingKind").value : "related_work";
    const scope = $("#writingScope") ? $("#writingScope").value : "all";
    if (!topic) {
      toast("先输入论文主题", true);
      return;
    }
    if (scope === "selected" && state.selected.size === 0) {
      toast("先在左侧勾选至少一篇论文", true);
      return;
    }
    state.writingTopic = topic;
    state.writingKind = kind;
    state.writingScope = scope;
    const button = $("#draftBtn");
    if (button) setLoadingText(button, "生成草稿…");
    const result = $("#draftResult");
    if (result) result.innerHTML = `<div class="loading-line"></div>${emptyState("正在搭草稿", "分组相关工作、整理证据引用与实验计划…")}`;
    try {
      const data = await api("/api/draft", {
        method: "POST",
        body: JSON.stringify({ topic, kind, scope, paper_ids: Array.from(state.selected) }),
      });
      state.draft = data;
      state.check = null;
      state.argument = null;
      const checkResult = $("#checkResult");
      if (checkResult) checkResult.innerHTML = "";
      const argumentResult = $("#argumentResult");
      if (argumentResult) argumentResult.innerHTML = "";
      renderDraft(data);
    } catch (error) {
      toast(error.message, true);
      if (result) result.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    } finally {
      if (button) restoreButton(button);
    }
  }

  function draftCheckText(data) {
    if (data.kind === "abstract") return data.body.text || "";
    if (data.kind === "outline") {
      return (data.body.sections || []).flatMap((section) => section.bullets || []).join("\n");
    }
    return (data.body.sections || []).flatMap((section) => section.paragraphs || []).join("\n");
  }

  function renderDraft(data) {
    const result = $("#draftResult");
    if (!result) return;
    let body = "";
    if (data.kind === "abstract") {
      body = `<div class="draft-abstract">${esc(data.body.text || "")}</div>`;
    } else if (data.kind === "outline") {
      body = (data.body.sections || []).map((section) => `
        <div class="draft-section">
          <h4>${esc(section.title)}</h4>
          <ul>${(section.bullets || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
        </div>
      `).join("");
    } else {
      body = (data.body.sections || []).map((section) => `
        <div class="draft-section">
          <h4>${esc(section.title)} <span class="badge badge-muted">${(section.citations || []).join(" ")}</span></h4>
          ${(section.paragraphs || []).map((paragraph) => `<p>${esc(paragraph)}</p>`).join("")}
        </div>
      `).join("") + `<div class="card-sub"><strong>对比小结：</strong>${(data.body.summary || []).map((item) => esc(item)).join(" ")}</div>`;
    }
    result.innerHTML = `
      <div class="draft-card">
        <div class="draft-head">
          <div><div class="eyebrow">${esc(data.kind_label)}</div><h3>${esc(data.title)}</h3><div class="section-note">${data.paper_count} 篇论文 · ${esc(data.scope_label)}</div></div>
          <div class="chips"><button class="btn btn-small" type="button" id="copyDraft">复制 Markdown</button><button class="btn btn-small btn-primary" type="button" id="draftToCheck">送入核查</button></div>
        </div>
        <div class="warn-box">${esc(data.disclaimer)}</div>
        <div class="draft-body">${body}</div>
        <details class="draft-refs" open><summary>参考文献（${(data.references || []).length}）</summary>
          <div class="insight-list">${(data.references || []).map((ref) => `<div class="insight-item"><strong>[${esc(ref.cite)}]</strong> ${esc(ref.title)} · ${esc((ref.authors || []).slice(0, 3).join(", "))} · ${esc(ref.year || "—")} · ${esc(ref.venue || "")}</div>`).join("")}</div>
        </details>
        <div class="section-note" style="margin-top:8px">Markdown 可直接导出；引用键 [P1] 对应下方参考文献。</div>
      </div>
    `;
    const copyButton = $("#copyDraft");
    if (copyButton) copyButton.addEventListener("click", () => copyText(data.markdown || ""));
    const checkButton = $("#draftToCheck");
    if (checkButton) checkButton.addEventListener("click", () => {
      const area = $("#checkText");
      const text = draftCheckText(data);
      if (area) area.value = text;
      state.checkText = text;
      const target = $("#checkText");
      if (target) target.focus();
      toast("草稿已填入核查区");
    });
  }

  async function doCheck() {
    const area = $("#checkText");
    const text = (area ? area.value : state.checkText).trim();
    const scope = $("#writingScope") ? $("#writingScope").value : "all";
    if (!text) {
      toast("先粘贴一段草稿", true);
      return;
    }
    if (scope === "selected" && state.selected.size === 0) {
      toast("先在左侧勾选至少一篇论文", true);
      return;
    }
    state.checkText = text;
    state.writingScope = scope;
    const button = $("#checkBtn");
    if (button) setLoadingText(button, "核查中…");
    try {
      const data = await api("/api/check", {
        method: "POST",
        body: JSON.stringify({ text, scope, paper_ids: Array.from(state.selected) }),
      });
      state.check = data;
      renderCheck(data);
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) restoreButton(button);
    }
  }

  async function doArgument() {
    const area = $("#checkText");
    const text = (area ? area.value : state.checkText).trim();
    const scope = $("#writingScope") ? $("#writingScope").value : "all";
    const topic = $("#writingTopic") ? $("#writingTopic").value.trim() : state.writingTopic;
    if (text.length < 5) {
      toast("先粘贴一段草稿", true);
      return;
    }
    if (scope === "selected" && state.selected.size === 0) {
      toast("先在左侧勾选至少一篇论文", true);
      return;
    }
    state.checkText = text;
    state.writingScope = scope;
    const button = $("#argumentBtn");
    if (button) setLoadingText(button, "体检中…");
    try {
      const data = await api("/api/argument", {
        method: "POST",
        body: JSON.stringify({
          text,
          topic,
          scope,
          paper_ids: Array.from(state.selected),
        }),
      });
      state.argument = data;
      renderArgument(data);
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) restoreButton(button);
    }
  }

  function argumentMarkdown(data) {
    const structure = data.structure || {};
    const summary = data.summary || {};
    const lines = [
      "# 论证体检",
      "",
      `总分：${data.score}/100 · ${data.verdict}`,
      `结构分：${summary.structure_score || 0}/100 · 证据分：${summary.evidence_score || 0}/100 · 主张：${summary.claims || 0} 句`,
      "",
      "## 结构检查",
      "",
    ];
    (structure.checks || []).forEach((item) => {
      lines.push(`- **${item.label}**：${{ present: "已具备", partial: "待展开", missing: "缺失" }[item.status] || item.status}${item.evidence ? ` · ${item.evidence}` : ""}`);
      if (item.status !== "present") lines.push(`  - 建议：${item.suggestion}`);
    });
    lines.push("", "## 章节论证映射", "");
    (data.argument_map || []).forEach((item) => {
      lines.push(`- ${item.title}：${item.claims} 句主张；直接支持 ${item.supported}；部分 ${item.partial}；未支撑 ${item.unsupported}；引用标记 ${item.citations}；证据线索 ${item.evidence_links}`);
    });
    lines.push("", "## 未充分支撑的句子", "");
    if ((data.unsupported || []).length) {
      data.unsupported.forEach((item) => {
        lines.push(`- ${item.level === "partial" ? "部分支撑" : "证据不足"}：${item.sentence}`);
        if (item.suggestion) lines.push(`  - ${item.suggestion}`);
      });
    } else {
      lines.push("- 未发现明显的未支撑句；仍需核对测试条件。");
    }
    lines.push("", "## 优先修改动作", "");
    (data.actions || []).forEach((item, index) => lines.push(`${index + 1}. ${item}`));
    lines.push("", `> ${data.note || ""}`);
    return lines.join("\n");
  }

  function renderArgument(data) {
    const result = $("#argumentResult");
    if (!result) return;
    const structure = data.structure || {};
    const summary = data.summary || {};
    const scoreKind = data.score >= 80 ? "green" : data.score >= 55 ? "amber" : "pink";
    const statusMap = {
      present: { label: "已具备", kind: "green" },
      partial: { label: "待展开", kind: "amber" },
      missing: { label: "缺失", kind: "pink" },
    };
    result.innerHTML = `
      <div class="card argument-card-result">
        <div class="check-summary-head">
          <h3 class="card-title">论证体检 <span class="badge badge-${scoreKind}">${esc(data.verdict || "—")}</span></h3>
          <div class="chips"><button class="btn btn-small" type="button" id="exportArgument">导出 Markdown</button></div>
        </div>
        <div class="argument-stats">
          <div><strong>${data.score}</strong><span>综合分</span></div>
          <div><strong>${summary.structure_score || 0}</strong><span>结构分</span></div>
          <div><strong>${summary.evidence_score || 0}</strong><span>证据分</span></div>
          <div><strong>${summary.claims || 0}</strong><span>主张句</span></div>
          <div><strong>${(data.unsupported || []).length}</strong><span>待补证据</span></div>
        </div>
        <div class="argument-columns">
          <div>
            <div class="section-note">结构检查</div>
            <div class="structure-grid">
              ${(structure.checks || []).map((item) => {
                const status = statusMap[item.status] || { label: item.status, kind: "muted" };
                return `
                  <div class="structure-item ${esc(item.status)}">
                    <div class="structure-top"><strong>${esc(item.label)}</strong>${badge(status.label, status.kind)}</div>
                    <div class="section-note">${esc(item.evidence || item.suggestion)}</div>
                  </div>
                `;
              }).join("")}
            </div>
          </div>
          <div>
            <div class="section-note">章节论证映射</div>
            <div class="argument-map">
              ${(data.argument_map || []).map((item) => `
                <div class="argument-map-row">
                  <strong>${esc(item.title)}</strong>
                  <span>${item.claims} 句</span>
                  <span class="ok">支持 ${item.supported}</span>
                  <span class="warn">部分 ${item.partial}</span>
                  <span class="bad">未支撑 ${item.unsupported}</span>
                  <span>引用 ${item.citations}</span>
                  <span>线索 ${item.evidence_links}</span>
                </div>
              `).join("") || `<div class="section-note">草稿还没有可映射的段落。</div>`}
            </div>
            <div class="section-note" style="margin-top:8px">优先修改动作</div>
            <ol class="argument-actions">${(data.actions || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ol>
          </div>
        </div>
        <div class="section-note" style="margin-top:10px">未充分支撑的句子</div>
        <div class="unsupported-list">
          ${(data.unsupported || []).map((item) => `
            <div class="unsupported-item">
              <span class="check-level ${item.level === "partial" ? "partial" : "unsupported"}">${esc(item.label || "需补证据")}</span>
              <div class="unsupported-sentence">${esc(item.sentence)}</div>
              ${(item.issues || []).length ? `<div class="check-issues">${item.issues.map((issue) => `<span>${esc(issue)}</span>`).join("")}</div>` : ""}
              <div class="section-note">${esc(item.suggestion || "")}</div>
            </div>
          `).join("") || `<div class="ok-box">没有发现明显未支撑句；仍请核对数值与测试条件。</div>`}
        </div>
        <div class="section-note" style="margin-top:9px">${esc(data.note || "")}</div>
      </div>
    `;
    const exportButton = $("#exportArgument");
    if (exportButton) exportButton.addEventListener("click", () => downloadMarkdown(argumentMarkdown(data), "paperpilot-argument.md"));
  }

  function checkMarkdown(data) {
    const lines = ["# 主张—证据核查", "", `范围：${data.scope_label} · ${data.summary.total} 句`, ""];
    (data.results || []).forEach((item, index) => {
      lines.push(`## ${index + 1}. ${item.label}（重合度 ${item.overlap}）`, "", item.sentence, "");
      if (item.suggestion) lines.push(`- 建议：${item.suggestion}`);
      (item.issues || []).forEach((issue) => lines.push(`- 问题：${issue}`));
      const citation = item.citation;
      if (citation) lines.push(`- 证据：${citation.title} · ${citation.section} p.${citation.page}`);
      lines.push("");
    });
    lines.push(`> ${data.note}`);
    return lines.join("\n");
  }

  function renderCheck(data) {
    const result = $("#checkResult");
    if (!result) return;
    const summary = data.summary || {};
    result.innerHTML = `
      <div class="card" style="margin-top:12px">
        <div class="check-summary-head">
          <h3 class="card-title">核查结果 <span class="badge ${summary.overall === "strong" ? "badge-green" : summary.overall === "mixed" ? "badge-amber" : "badge-pink"}">${esc(summary.overall_label || "—")}</span></h3>
          <div class="chips"><button class="btn btn-small" type="button" id="exportCheck">导出 Markdown</button></div>
        </div>
        <div class="check-stats">
          <div><strong>${summary.total || 0}</strong><span>句</span></div>
          <div><strong>${summary.supported || 0}</strong><span>直接支持</span></div>
          <div><strong>${summary.partial || 0}</strong><span>部分支持</span></div>
          <div><strong>${summary.unsupported || 0}</strong><span>证据不足</span></div>
          <div><strong>${summary.quantity_issues || 0}</strong><span>数值问题</span></div>
        </div>
        <div class="check-list">
          ${(data.results || []).map((item) => `
            <article class="check-item ${esc(item.level)}">
              <div class="check-item-head"><span class="check-level ${esc(item.level)}">${esc(item.label)}</span><span class="section-note">重合度 ${esc(item.overlap)}</span></div>
              <div class="check-sentence">${esc(item.sentence)}</div>
              ${(item.issues || []).length ? `<div class="check-issues">${item.issues.map((issue) => `<span>${esc(issue)}</span>`).join("")}</div>` : ""}
              ${(item.quantities || []).length ? `<div class="chips">${item.quantities.map((q) => `<span class="quantity-chip">${esc(q.text)}</span>`).join("")}</div>` : ""}
              ${(item.citations || []).slice(0, 2).map((citation) => `
                <div class="check-evidence">
                  <div class="citation-source">${esc(citation.title)} · ${esc(citation.section)} · p.${esc(citation.page)}</div>
                  <div class="citation-quote">“${esc(citation.quote)}”</div>
                  <button class="btn btn-small btn-ghost" type="button" data-check-open="${esc(citation.paper_id)}">打开精读</button>
                </div>
              `).join("")}
              <div class="check-suggestion">${esc(item.suggestion || "")}</div>
            </article>
          `).join("") || emptyState("没有可核查的句子", "粘贴一段包含结论的草稿后重试。")}
        </div>
        <div class="section-note" style="margin-top:9px">${esc(data.note || "")}</div>
      </div>
    `;
    $$("[data-check-open]", result).forEach((button) => {
      button.addEventListener("click", () => openPaper(button.dataset.checkOpen, true));
    });
    const exportButton = $("#exportCheck");
    if (exportButton) exportButton.addEventListener("click", () => downloadMarkdown(checkMarkdown(data), "paperpilot-check.md"));
  }

  function downloadMarkdown(content, filename) {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("Markdown 已导出");
  }

  async function openPaper(id, switchToRead = true) {
    state.activePaper = id;
    state.read = null;
    state.translation = null;
    state.citations = null;
    state.innovate = null;
    state.transform = null;
    const brief = state.papers.find((paper) => paper.id === id)
      || state.library.find((item) => item.paper_id === id);
    el.crumb.textContent = brief ? shortTitle(brief.title, 40) : "论文详情";
    renderResults();
    try {
      const data = await api("/api/read", {
        method: "POST",
        body: JSON.stringify({ paper_id: id, mode: "5min" }),
      });
      state.read = data;
      if (switchToRead) switchTab("read");
      else renderRead(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">加载失败：${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function readNoteMarkdown(data) {
    const paper = data.paper || {};
    const topic = state.topicQuery || state.writingTopic || "尚未设定";
    const findings = (data.results || []).slice(0, 6).map((item) => {
      const condition = item.condition || item.context ? `（${item.condition || item.context}）` : "";
      return `${item.name || "指标"} ${item.value || "未提及"}${condition}${item.baseline ? `；baseline：${item.baseline}` : ""}`;
    });
    const evidence = (data.evidence || []).slice(0, 8).map((item) => {
      return `- [${item.field || "evidence"}] ${item.section || ""} p.${item.page || "—"}：${item.quote || ""}`;
    });
    const lines = [
      `# 精读笔记：${paper.title || "未命名论文"}`,
      "",
      `- 作者：${(paper.authors || []).join(", ") || "未提供"}`,
      `- 年份 / 来源：${paper.year || "—"} / ${paper.venue || "—"}`,
      `- DOI / 链接：${paper.doi || paper.url || "未提供"}`,
      `- 抽取级别：${paper.extraction_level || "摘要级"}`,
      "",
      "## 1. 研究问题",
      "",
      data.problem || "未提及",
      "",
      "## 2. 数据与方法",
      "",
      data.method || "未提及",
      "",
      "## 3. 核心发现",
      "",
      ...(findings.length ? findings.map((item) => `- ${item}`) : ["- 未抽取到可核对数值，需回原文补充。"]),
      "",
      "## 4. 局限与边界",
      "",
      ...((data.limitations || []).length ? data.limitations.map((item) => `- ${item}`) : ["- 未抽取到明确局限。"]),
      "",
      "## 5. 与我的研究问题的关联",
      "",
      `当前选题：${topic}`,
      "",
      "- 可借鉴：记录本文的方法选择、评价指标与证据组织方式。",
      "- 需要核对：测试条件、baseline 口径、是否可获取全文/代码/数据。",
      "- 待形成的问题：本文的局限是否能转化为一个可证伪的最小实验？",
      "",
      "## 审稿人问题",
      "",
      ...((data.reviewer_questions || []).length ? data.reviewer_questions.map((item) => `- ${item}`) : ["- 待补充。"]),
      "",
      "## 证据摘录",
      "",
      ...(evidence.length ? evidence : ["- 暂无结构化证据，请回到原文核对。"]),
      "",
      "> 本笔记由结构化字段与原文证据拼接，需人工核对后再用于选题或写作。",
    ];
    return lines.join("\n");
  }

  function renderRead(data) {
    const paper = data.paper;
    const saved = isSaved(paper.id);
    const actions = [
      `<button class="btn btn-small ${saved ? "btn-pink" : ""}" data-read-action="save" type="button">${saved ? "★ 已收藏" : "☆ 收藏"}</button>`,
      `<button class="btn btn-small" data-read-action="add-project" type="button">加入项目</button>`,
      `<button class="btn btn-small" data-read-action="translate" type="button">翻译</button>`,
      `<button class="btn btn-small" data-read-action="notes" type="button">导出精读笔记</button>`,
      `<button class="btn btn-small" data-read-action="innovate" type="button">提炼创新点</button>`,
      `<button class="btn btn-small" data-read-action="transform" type="button">转成成果</button>`,
    ];
    if (paper.local_pdf) actions.push(`<button class="btn btn-small btn-ghost" data-read-action="pages" type="button">原文页</button>`);
    if (safeUrl(paper.url)) actions.push(`<a class="btn btn-small btn-ghost" href="${esc(safeUrl(paper.url))}" target="_blank" rel="noopener">原文</a>`);
    if (safeUrl(paper.pdf_url)) actions.push(`<a class="btn btn-small btn-ghost" href="${esc(safeUrl(paper.pdf_url))}" target="_blank" rel="noopener">PDF</a>`);

    const metrics = Object.entries(data.metrics || {});
    const evidence = data.evidence || [];
    const sections = data.sections || [];
    const translations = data.translation || [];
    const reviewQuestions = data.reviewer_questions || [];

    el.panel.innerHTML = `
      <section class="paper-head">
        <div class="paper-head-top">
          <div style="min-width:0">
            <h2 class="paper-title">${esc(paper.title)}</h2>
            <div class="paper-meta">
              <span>${esc(paper.year || "—")}</span>
              <span>·</span>
              <span>${esc(paper.venue || "—")}</span>
              <span>·</span>
              <span>${esc(authorsText(paper.authors))}</span>
              ${badge(paper.extraction_level || "摘要级", paper.full_text ? "soft" : "muted")}
              ${data.llm_enriched ? badge("模型增强", "green") : ""}
              ${data.extraction_mode === "heuristic" ? badge("规则抽取", "amber") : ""}
              ${paper.cached ? badge("缓存结果", "amber") : ""}
            </div>
          </div>
          <div class="paper-actions">${actions.join("")}</div>
        </div>
        <div class="paper-tldr">
          <div class="paper-tldr-label">TL;DR · 先看结论</div>
          ${esc(data.tldr)}
        </div>
      </section>

      <div class="read-grid">
        <div class="stack">
          <div class="card">
            <h3 class="card-title">问题与方法 <span class="badge badge-muted">结构化抽取</span></h3>
            <p class="card-sub"><strong>问题：</strong>${esc(data.problem)}</p>
            <p class="card-sub"><strong>方法：</strong>${esc(data.method)}</p>
            ${(data.novelty || []).length ? `
              <div class="card-title" style="margin-top:12px;font-size:12px">创新点</div>
              <div class="insight-list">${data.novelty.map((item) => `<div class="insight-item">${esc(item)}</div>`).join("")}</div>
            ` : ""}
          </div>

          <div class="card">
            <h3 class="card-title">全文节选 <span class="badge badge-soft">${sections.length} 段</span></h3>
            <div class="section-note" style="margin-bottom:9px">${esc(data.note || "")}</div>
            ${sections.length ? sections.map((section, index) => `
              <details class="section" ${index < 2 ? "open" : ""}>
                <summary>
                  <span>${esc(section.title)}</span>
                  <span class="section-page">p.${esc(section.page || "—")}${paper.local_pdf ? ` <button class="link-btn" type="button" data-open-page="${esc(section.page || 1)}">查看</button>` : ""}</span>
                </summary>
                <div class="section-body">${esc(section.text)}</div>
                <div class="section-tools">
                  <button class="btn btn-small btn-ghost" type="button" data-copy-section="${index}">复制原文</button>
                  ${evidence.some((item) => item.section === section.title) ? `<button class="btn btn-small btn-ghost" type="button" data-evidence-section="${index}">查看证据</button>` : ""}
                </div>
              </details>
            `).join("") : `<div class="warn-box">在线检索只返回摘要；上传 PDF 后可解锁全文节选。</div>`}
          </div>

          ${translations.length ? `
            <div class="card">
              <h3 class="card-title">双语对照 <span class="badge badge-pink">术语保真</span></h3>
              ${translations.map((pair) => `
                <div class="translation-pair">
                  <div class="translation-en">${esc(pair.en)}</div>
                  <div class="translation-zh">${esc(pair.zh)}</div>
                </div>
              `).join("")}
            </div>
          ` : ""}

          ${(data.figures || []).length ? `
            <div class="card">
              <h3 class="card-title">图表与公式 <span class="badge badge-muted">阅读提示</span></h3>
              ${data.figures.map((figure) => `
                <div class="figure-item">
                  <div class="figure-title">${esc(figure.title)} <span class="section-page">p.${esc(figure.page || "—")}</span></div>
                  <div class="figure-text"><strong>是什么：</strong>${esc(figure.what)}<br><strong>怎么看：</strong>${esc(figure.how_to_read)}</div>
                </div>
              `).join("")}
            </div>
          ` : ""}
        </div>

        <aside class="stack">
          ${metrics.length ? `
            <div class="card">
              <h3 class="card-title">关键指标 <span class="badge badge-soft">带条件</span></h3>
              <div class="metrics-grid">
                ${metrics.map(([key, value]) => `<div class="metric"><div class="metric-key">${esc(key)}</div><div class="metric-value">${esc(value)}</div></div>`).join("")}
              </div>
            </div>
          ` : ""}

          ${(data.results || []).length ? `
            <div class="card">
              <h3 class="card-title">可核对数值 <span class="badge badge-green">${paper.source === "upload" ? "规则抽取" : "带条件"}</span></h3>
              <div class="result-list">
                ${data.results.slice(0, 5).map((item) => `
                  <div class="result-item">
                    <div class="result-line"><strong>${esc(item.name || "指标")}</strong><span>${esc(item.value || "未提及")}</span></div>
                    <div class="section-note">${item.baseline ? `baseline：${esc(item.baseline)} · ` : ""}${esc(item.condition || item.context || "")}</div>
                  </div>
                `).join("")}
              </div>
            </div>
          ` : ""}

          ${reviewQuestions.length ? `
            <div class="card">
              <h3 class="card-title">审稿人会问什么</h3>
              ${reviewQuestions.map((question, index) => `<div class="review-q"><span class="num">Q${index + 1}</span><span>${esc(question)}</span></div>`).join("")}
            </div>
          ` : ""}

          <div class="card">
            <h3 class="card-title">证据链 <span class="badge badge-pink">${evidence.length} 条</span></h3>
            ${evidence.length ? evidence.slice(0, 6).map((item, index) => `
              <div class="evidence-item">
                <div class="evidence-label">${esc(item.label || item.field || "证据")}</div>
                <div class="evidence-quote">“${esc(item.quote)}”</div>
                <div class="evidence-source">${esc(item.section || "原文")} · p.${esc(item.page || "—")}${paper.local_pdf ? ` · <button class="link-btn" type="button" data-open-page="${esc(item.page || 1)}" data-open-quote="${esc(item.quote || "")}">查看原文页</button>` : ""}</div>
                ${evidence.length > 1 ? `<button class="btn btn-small btn-ghost" style="margin-top:5px" type="button" data-evidence-index="${index}">展开</button>` : ""}
              </div>
            `).join("") : `<div class="section-note">当前为摘要级材料，暂无逐条证据。</div>`}
          </div>

          <div class="card">
            <h3 class="card-title">Agent 运行轨迹 <span class="badge badge-soft">${data.llm_enriched ? "模型增强" : "规则模式"}</span></h3>
            <div class="trace">
              <div class="trace-row"><span class="trace-index">01</span><span class="trace-name">Scout</span><span class="trace-desc">检索 · 去重 · 排序</span><span class="trace-status done">完成</span></div>
              <div class="trace-row"><span class="trace-index">02</span><span class="trace-name">Reader</span><span class="trace-desc">PDF 解析 · 章节/页码</span><span class="trace-status done">完成</span></div>
              <div class="trace-row"><span class="trace-index">03</span><span class="trace-name">Analyst</span><span class="trace-desc">字段抽取 · 结构化</span><span class="trace-status done">完成</span></div>
              <div class="trace-row"><span class="trace-index">04</span><span class="trace-name">Critic</span><span class="trace-desc">证据校验 · 可比性</span><span class="trace-status done">${(data.evidence || []).length} 条</span></div>
            </div>
            <div class="section-note" style="margin-top:8px">轨迹展示的是当前请求实际走过的工具链；未配置模型时，抽取步骤使用内置规则与人工校订字段。</div>
          </div>

          <div class="card">
            <h3 class="card-title">下一步</h3>
            <div class="insight-list">
              <div class="insight-item">勾选 2–5 篇论文，去“对比”看共识与分歧</div>
              <div class="insight-item pink">去“创新点”看作者声称与证据是否匹配</div>
              <div class="insight-item">去“成果”生成复现计划与组会提纲</div>
              <div class="insight-item pink">收藏后去“我的库”写笔记，或到“证据问答”跨论文提问</div>
            </div>
          </div>
        </aside>
      </div>
    `;

    $$("[data-read-action]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.readAction;
        if (action === "save") saveCurrentPaper();
        if (action === "add-project") addPaperToProject(paper.id);
        if (action === "translate") switchTab("translate");
        if (action === "notes") downloadMarkdown(readNoteMarkdown(data), "paperpilot-note.md");
        if (action === "innovate") switchTab("innovate");
        if (action === "transform") switchTab("transform");
        if (action === "pages") openPageViewer(paper.id, Number((data.metrics || {})["页数"] || 1), 1);
      });
    });

    $$("[data-copy-section]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const section = sections[Number(button.dataset.copySection)];
        if (section) copyText(section.text);
      });
    });

    $$("[data-evidence-section]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const section = sections[Number(button.dataset.evidenceSection)];
        const matches = evidence.filter((item) => item.section === section.title);
        openModal("章节证据", matches.map((item) => evidenceBlock(item)).join("") || "该章节暂无单独证据。");
      });
    });

    $$("[data-evidence-index]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const item = evidence[Number(button.dataset.evidenceIndex)];
        if (item) openModal("原文证据", evidenceBlock(item));
      });
    });

    $$("[data-open-page]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        openPageViewer(
          paper.id,
          Number((data.metrics || {})["页数"] || 1),
          Number(button.dataset.openPage || 1),
          button.dataset.openQuote || "",
        );
      });
    });
  }

  function evidenceBlock(item) {
    return `<div class="evidence-item">
      <div class="evidence-label">${esc(item.label || item.field || "证据")}</div>
      <div class="evidence-quote">“${esc(item.quote || "未提供引文")}”</div>
      <div class="evidence-source">${esc(item.section || "原文")} · p.${esc(item.page || "—")}</div>
    </div>`;
  }

  async function loadTranslation() {
    if (!state.activePaper) return;
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在加载翻译工作台", "读取章节、术语和本地翻译缓存…")}`;
    try {
      const query = `target=${encodeURIComponent(state.translationTarget)}&provider=${encodeURIComponent(state.translationProvider)}`;
      const data = await api(`/api/translation/${encodeURIComponent(state.activePaper)}?${query}`);
      state.translation = data;
      renderTranslation(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function translationSectionBadges(section) {
    if (!section.translated) return badge("未翻译", "muted");
    const items = [badge(section.provider_label || "已翻译", section.provider === "llm" ? "green" : "amber")];
    if (section.cached) items.push(badge("缓存", "soft"));
    if (section.truncated) items.push(badge("公共预览", "amber"));
    if (section.checks && section.checks.numbers_preserved === false) items.push(badge("数字待核对", "pink"));
    if (section.checks && section.checks.missing_tokens > 0) items.push(badge("公式占位符缺失", "pink"));
    return items.join("");
  }

  function renderTranslation(data, focusIndex = null) {
    const providers = data.providers || {};
    const llm = providers.llm || {};
    const publicProvider = providers.public || {};
    const currentProvider = data.provider === "llm" ? "模型翻译" : "公共翻译接口（额度限制）";
    const total = data.total_sections || 0;
    const translated = data.translated_sections || 0;
    const percent = total ? Math.round((translated / total) * 100) : 0;
    const targetOptions = (providers.targets || [{ id: "zh", label: "简体中文" }])
      .map((item) => `<option value="${esc(item.id)}" ${item.id === data.target ? "selected" : ""}>${esc(item.label)}</option>`)
      .join("");
    const fullDisabled = !total;
    const privacy = llm.enabled
      ? "模型翻译只在配置的模型服务内处理，不经过公共翻译站。"
      : "公共翻译会把当前章节的原文发送给第三方公共翻译服务，并受每日额度限制；未公开论文请先配置模型。";
    const sections = (data.sections || []).map((section, index) => {
      const translatedSection = section.translated;
      const open = translatedSection && index === 0;
      return `
        <article class="card translation-section" id="translation-section-${section.index}">
          <div class="translation-section-head">
            <div class="translation-section-title">
              <span class="translation-index">${String(section.index + 1).padStart(2, "0")}</span>
              <span>${esc(section.title)}</span>
              <span class="section-page">p.${esc(section.page || "—")}</span>
            </div>
            <div class="translation-actions">
              <button class="btn btn-small ${translatedSection ? "" : "btn-primary"}" type="button" data-translate-section="${section.index}" data-force="${translatedSection ? "1" : "0"}">
                ${translatedSection ? "重译本节" : "翻译本节"}
              </button>
              ${translatedSection ? `<button class="btn btn-small btn-ghost" type="button" data-copy-translation="${section.index}">复制译文</button>` : ""}
            </div>
          </div>
          <div class="translation-section-meta">
            ${translationSectionBadges(section)}
            <span>${section.source_chars || 0} 字符</span>
            ${section.translated_source_chars && section.translated_source_chars < section.source_chars ? `<span>已译 ${section.translated_source_chars} 字符</span>` : ""}
            ${translatedSection ? `<span>${esc(section.translated_at || "")}</span>` : ""}
          </div>
          ${translatedSection && section.note ? `<div class="translation-note">${esc(section.note)}</div>` : ""}
          <details class="translation-compare" ${open ? "open" : ""}>
            <summary>${translatedSection ? "中英对照" : "查看原文"} <span class="section-page">${translatedSection ? "原文 / 译文" : "尚未翻译"}</span></summary>
            <div class="translation-columns">
              <div class="translation-source-col">
                <div class="translation-col-label">原文</div>
                <div class="translation-body-text">${esc(section.source)}</div>
              </div>
              <div class="translation-target-col">
                <div class="translation-col-label">${esc(data.target_label || "译文")}</div>
                ${translatedSection
                  ? `<div class="translation-body-text">${esc(section.translation)}</div>`
                  : `<div class="translation-placeholder">尚未翻译。可选择“翻译本节”生成${llm.enabled ? "完整" : "公共接口预览"}译文。</div>`}
              </div>
            </div>
          </details>
        </article>
      `;
    }).join("");
    const glossary = (data.glossary_hits || []).map((item) => `<span class="chip">${esc(item.term)} → ${esc(item.zh)}</span>`).join("");
    el.panel.innerHTML = `
      <section class="paper-head translation-head">
        <div class="paper-head-top">
          <div style="min-width:0">
            <div class="eyebrow">PAPER TRANSLATION / 论文翻译</div>
            <h2 class="paper-title">${esc(data.paper.title)}</h2>
            <div class="paper-meta">
              <span>${esc(data.paper.year || "—")}</span><span>·</span>
              <span>${esc(data.paper.venue || "—")}</span><span>·</span>
              <span>${esc(authorsText(data.paper.authors))}</span>
              ${badge(data.paper.extraction_level || "摘要级", "soft")}
              ${badge(currentProvider, llm.enabled ? "green" : "amber")}
            </div>
          </div>
          <div class="translation-head-actions">
            <select id="translationTarget" aria-label="翻译目标语言">${targetOptions}</select>
            <button class="btn btn-small btn-primary" type="button" id="translationAbstract" ${total ? "" : "disabled"}>翻译摘要</button>
            <button class="btn btn-small" type="button" id="translationAll" ${fullDisabled ? "disabled" : ""} title="${llm.enabled ? "逐节翻译全文" : "公共接口逐节翻译，受每日额度限制"}">${llm.enabled ? "翻译整篇" : "翻译各节"}</button>
            <button class="btn btn-small btn-ghost" type="button" id="translationExport">导出双语 Markdown</button>
          </div>
        </div>
        <div class="translation-progress-row">
          <div class="translation-progress"><div style="width:${percent}%"></div></div>
          <span>${translated} / ${total} 节已翻译</span>
        </div>
        <div class="translation-mode ${llm.enabled ? "green" : "amber"}">
          <strong>${llm.enabled ? "模型翻译已启用" : "无密钥公共翻译"}</strong>
          <span>${esc(privacy)}</span>
        </div>
      </section>
      <div class="read-grid">
        <div class="stack">${sections || `<div class="warn-box">这篇材料没有可翻译的章节。</div>`}</div>
        <aside class="stack">
          <div class="card">
            <h3 class="card-title">翻译通道 <span class="badge ${llm.enabled ? "badge-green" : "badge-amber"}">${llm.enabled ? "完整" : "公共额度"}</span></h3>
            <div class="translation-provider-row"><strong>${llm.enabled ? "模型翻译" : "公共翻译接口"}</strong><span>${llm.enabled ? esc(llm.model || "已配置") : `≤ ${publicProvider.preview_chars || 0} 字符/节`}</span></div>
            <div class="section-note">${esc(llm.enabled ? llm.note : publicProvider.note)}</div>
            ${llm.enabled ? "" : `<div class="translation-upgrade">配置 OpenAI-compatible 模型后可稳定翻译完整章节，并自动使用术语表。</div>`}
          </div>
          <div class="card">
            <h3 class="card-title">术语表 <span class="badge badge-soft">${(data.glossary_hits || []).length} 个命中</span></h3>
            ${glossary ? `<div class="chips">${glossary}</div>` : `<div class="section-note">当前章节没有命中内置术语表。</div>`}
            <button class="btn btn-small btn-ghost" id="glossaryBrowse" type="button" style="margin-top:9px">浏览全部术语</button>
          </div>
          <div class="card">
            <h3 class="card-title">质量校验</h3>
            <div class="insight-list">
              <div class="insight-item">公式、引用与 URL 使用占位符保护后再翻译。</div>
              <div class="insight-item">自动核对数字与公式占位符是否保留；异常会标黄。</div>
              <div class="insight-item pink">机器译文仅用于阅读辅助，引用前请对照原文。</div>
            </div>
          </div>
        </aside>
      </div>
    `;
    const targetSelect = $("#translationTarget");
    if (targetSelect) {
      targetSelect.addEventListener("change", async () => {
        state.translationTarget = targetSelect.value;
        state.translation = null;
        await loadTranslation();
      });
    }
    const abstractButton = $("#translationAbstract");
    if (abstractButton) abstractButton.addEventListener("click", () => translateTranslationSection(0, false));
    const allButton = $("#translationAll");
    if (allButton) allButton.addEventListener("click", translateTranslationAll);
    const exportButton = $("#translationExport");
    if (exportButton) exportButton.addEventListener("click", () => exportTranslation(data));
    const glossaryButton = $("#glossaryBrowse");
    if (glossaryButton) glossaryButton.addEventListener("click", openGlossaryBrowser);
    $$("[data-translate-section]", el.panel).forEach((button) => {
      button.addEventListener("click", () => translateTranslationSection(Number(button.dataset.translateSection), button.dataset.force === "1"));
    });
    $$("[data-copy-translation]", el.panel).forEach((button) => {
      button.addEventListener("click", () => {
        const section = data.sections.find((item) => item.index === Number(button.dataset.copyTranslation));
        if (section) copyText(section.translation);
      });
    });
    if (focusIndex !== null) {
      requestAnimationFrame(() => {
        document.getElementById(`translation-section-${focusIndex}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
      });
    }
  }

  function mergeTranslationSection(result) {
    if (!state.translation) return;
    const section = state.translation.sections.find((item) => item.index === result.index);
    if (!section) return;
    Object.assign(section, result, { translated: true, cached: !!result.cached, source: section.source });
    const done = state.translation.sections.filter((item) => item.translated).length;
    state.translation.translated_sections = done;
    state.translation.total_translated_chars = state.translation.sections.reduce((total, item) => total + (item.translated ? String(item.translation || "").length : 0), 0);
  }

  async function translateTranslationSection(index, force = false) {
    if (!state.translation || state.translationBusy) return;
    state.translationBusy = true;
    const buttons = $$("[data-translate-section], #translationAbstract, #translationAll", el.panel);
    buttons.forEach((button) => { button.disabled = true; });
    const button = $(`[data-translate-section="${index}"]`, el.panel);
    if (button) button.textContent = "翻译中…";
    try {
      const result = await api("/api/translation/section", {
        method: "POST",
        body: JSON.stringify({
          paper_id: state.activePaper,
          section_index: index,
          target: state.translationTarget,
          provider: state.translationProvider,
          force,
        }),
      });
      mergeTranslationSection(result);
      renderTranslation(state.translation, index);
      toast(result.provider === "llm" ? "本节已由模型翻译" : "本节已生成快速预览译文");
    } catch (error) {
      toast(error.message, true);
      renderTranslation(state.translation, index);
    } finally {
      state.translationBusy = false;
    }
  }

  async function translateTranslationAll() {
    if (!state.translation || state.translationBusy) return;
    const usingLlm = !!(state.translation.providers.llm || {}).enabled;
    if (!usingLlm) toast("公共接口逐节翻译，受每日额度限制；额度耗尽会停止");
    state.translationBusy = true;
    const allButton = $("#translationAll");
    const total = state.translation.sections.length;
    let done = 0;
    try {
      for (const section of state.translation.sections) {
        if (section.translated) { done += 1; continue; }
        setLoadingText(allButton, `翻译 ${done + 1}/${total}…`);
        const result = await api("/api/translation/section", {
          method: "POST",
          body: JSON.stringify({
            paper_id: state.activePaper,
            section_index: section.index,
            target: state.translationTarget,
            provider: state.translationProvider,
            force: false,
          }),
        });
        mergeTranslationSection(result);
        done += 1;
      }
      renderTranslation(state.translation);
      toast(`已完成 ${state.translation.translated_sections}/${total} 节翻译`);
    } catch (error) {
      renderTranslation(state.translation);
      toast(error.message, true);
    } finally {
      state.translationBusy = false;
      restoreButton(allButton);
    }
  }

  function translationMarkdown(data) {
    const lines = [
      `# ${data.paper.title} · 双语翻译`,
      "",
      `> ${data.target_label} · ${data.provider === "llm" ? "模型翻译" : "公共接口预览"} · 导出时间：${new Date().toLocaleString()}`,
      `> 作者：${(data.paper.authors || []).join(", ") || "未提供"}`,
      "",
    ];
    for (const section of data.sections || []) {
      lines.push(`## ${section.title}（p.${section.page || "—"}）`, "", "### 原文", "", section.source || "");
      if (section.translated) {
        lines.push("", "### 译文", "", section.translation || "");
        if (section.truncated) lines.push("", "> 本节受公共接口单节长度限制，译文不完整。");
      } else {
        lines.push("", "> 本节尚未翻译。");
      }
      lines.push("");
    }
    if ((data.glossary_hits || []).length) {
      lines.push("## 术语表", "");
      for (const item of data.glossary_hits) lines.push(`- ${item.term} → ${item.zh}`);
      lines.push("");
    }
    lines.push("> 机器翻译仅供阅读辅助；公式、数字与引用请回到原文核对。");
    return lines.join("\n");
  }

  function exportTranslation(data) {
    if (!(data.sections || []).some((section) => section.translated)) {
      toast("还没有可导出的译文", true);
      return;
    }
    downloadMarkdown(translationMarkdown(data), `paperpilot-${data.paper.id || "paper"}-${data.target}.md`);
  }

  async function loadCitations() {
    if (!state.activePaper) return;
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在读取引用网络", "向 OpenCitations 查询参考工作与被引工作，并用 Crossref 补全题录…")}`;
    try {
      const data = await api(`/api/citations/${encodeURIComponent(state.activePaper)}?limit=8`);
      state.citations = data;
      renderCitations(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">引用网络获取失败：${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function citationPaperTitle() {
    const brief = (state.read && state.read.paper)
      || state.papers.find((item) => item.id === state.activePaper)
      || state.library.find((item) => item.paper_id === state.activePaper);
    return brief ? brief.title : "当前论文";
  }

  function citationCard(item) {
    const saved = isSaved(item.paper_id);
    const authors = authorsText(item.authors || []);
    const url = safeUrl(item.url);
    const pdf = safeUrl(item.pdf_url);
    return `
      <article class="citation-card">
        <div class="citation-card-top">
          <div style="min-width:0">
            <div class="citation-title">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(item.title || "未命名记录")}</a>` : esc(item.title || "未命名记录")}</div>
            <div class="citation-meta">${esc(item.year || "—")} · ${esc(item.venue || "未注明来源")} · ${esc(authors)}</div>
          </div>
          <button class="btn btn-small ${saved ? "btn-pink" : "btn-ghost"}" type="button" data-citation-save="${esc(item.paper_id)}">${saved ? "★ 已收藏" : "☆ 收藏"}</button>
        </div>
        ${item.abstract ? `<p class="citation-abstract">${esc(shortTitle(item.abstract, 240))}</p>` : ""}
        <div class="citation-links">
          ${item.doi ? `<span class="chip">DOI ${esc(item.doi)}</span>` : ""}
          ${pdf ? `<a class="btn btn-small btn-ghost" href="${esc(pdf)}" target="_blank" rel="noopener">PDF</a>` : ""}
          ${url ? `<a class="btn btn-small btn-ghost" href="${esc(url)}" target="_blank" rel="noopener">原文</a>` : ""}
        </div>
      </article>`;
  }

  function citationList(items, title, note) {
    return `
      <section class="card">
        <h3 class="card-title">${title} <span class="badge badge-soft">${items.length}</span></h3>
        <div class="section-note" style="margin-bottom:8px">${esc(note)}</div>
        ${items.length ? `<div class="citation-list">${items.map(citationCard).join("")}</div>` : `<div class="translation-placeholder">当前没有返回条目。</div>`}
      </section>`;
  }

  function renderCitations(data) {
    const title = citationPaperTitle();
    if (!data.available) {
      el.panel.innerHTML = `
        <section class="paper-head">
          <div class="paper-head-top">
            <div>
              <div class="eyebrow">CITATION NETWORK / 引用网络</div>
              <h2 class="paper-title">${esc(title)}</h2>
              <div class="paper-meta">${badge("OpenCitations", "amber")}</div>
            </div>
            <div class="paper-actions"><button class="btn btn-small" type="button" id="citationBack">返回精读</button></div>
          </div>
        </section>
        <div class="warn-box"><strong>暂时无法构建引用网络：</strong>${esc(data.reason || "未知原因")}</div>
        <div class="card">
          <h3 class="card-title">怎么获得引用网络？</h3>
          <div class="insight-list">
            <div class="insight-item">在左侧检索来源选择 Crossref、DOAJ 或 HAL，打开带 DOI 的论文。</div>
            <div class="insight-item">上传含 DOI 的开放获取 PDF，或先收藏到研究库。</div>
            <div class="insight-item pink">OpenCitations 只解析 DOI 引用关系；无 DOI 的预印本暂不支持。</div>
          </div>
        </div>`;
      const back = $("#citationBack");
      if (back) back.addEventListener("click", () => switchTab("read"));
      return;
    }
    el.panel.innerHTML = `
      <section class="paper-head">
        <div class="paper-head-top">
          <div style="min-width:0">
            <div class="eyebrow">CITATION NETWORK / 引用网络</div>
            <h2 class="paper-title">${esc(title)}</h2>
            <div class="paper-meta">
              ${badge("OpenCitations", "green")}
              ${data.cached ? badge("缓存", "soft") : ""}
              ${data.doi ? badge(`DOI ${data.doi}`, "soft") : ""}
              ${data.fetched_at ? `<span>${esc(data.fetched_at)}</span>` : ""}
            </div>
          </div>
          <div class="paper-actions">
            <button class="btn btn-small" type="button" id="citationExport">导出引用 Markdown</button>
            <button class="btn btn-small btn-ghost" type="button" id="citationBack">返回精读</button>
          </div>
        </div>
        <div class="citation-stats">
          <div><strong>${data.reference_count || 0}</strong><span>参考工作</span></div>
          <div><strong>${data.citation_count || 0}</strong><span>被引工作</span></div>
          <div><strong>${data.doi ? "DOI" : "—"}</strong><span>解析枢纽</span></div>
        </div>
        <div class="translation-mode green">
          <strong>引用关系来自 OpenCitations，题录由 Crossref 补全</strong>
          <span>${esc(data.note || "")} 引用关系不等于研究质量；请回到原文核对。</span>
        </div>
      </section>
      <div class="citation-grid">
        ${citationList(data.references || [], "参考工作 · References", "当前论文引用的工作。")}
        ${citationList(data.citations || [], "被引工作 · Cited by", "引用当前论文的工作，可用于追踪后续影响。")}
      </div>
    `;
    const back = $("#citationBack");
    if (back) back.addEventListener("click", () => switchTab("read"));
    const exportButton = $("#citationExport");
    if (exportButton) exportButton.addEventListener("click", () => exportCitations(data));
    $$("[data-citation-save]", el.panel).forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.dataset.citationSave;
        await toggleResultSave(id);
        if (state.citations) renderCitations(state.citations);
      });
    });
  }

  function citationMarkdown(data) {
    const lines = [
      `# 引用网络：${citationPaperTitle()}`,
      "",
      `> DOI：${data.doi || "—"} · 来源：OpenCitations + Crossref · 导出时间：${new Date().toLocaleString()}`,
      "",
      `## 参考工作（${data.reference_count || 0}）`,
      "",
    ];
    for (const item of data.references || []) {
      lines.push(`- ${item.title || "未命名记录"}${item.year ? ` (${item.year})` : ""}${item.venue ? ` · ${item.venue}` : ""}${item.doi ? ` · DOI: ${item.doi}` : ""}`);
    }
    lines.push("", `## 被引工作（${data.citation_count || 0}）`, "");
    for (const item of data.citations || []) {
      lines.push(`- ${item.title || "未命名记录"}${item.year ? ` (${item.year})` : ""}${item.venue ? ` · ${item.venue}` : ""}${item.doi ? ` · DOI: ${item.doi}` : ""}`);
    }
    lines.push("", "> 引用关系来自 OpenCitations；题录可能受 Crossref 覆盖范围影响。");
    return lines.join("\n");
  }

  function exportCitations(data) {
    downloadMarkdown(citationMarkdown(data), `paperpilot-citations-${data.doi || "paper"}.md`);
  }

  async function openSourcesManager() {
    openModal("来源与检索设置", `<div class="source-manager"><div class="loading-line"></div><div class="section-note">正在读取自定义来源…</div></div>`);
    await loadSourcesManager();
  }

  async function loadSourcesManager(editId = "") {
    try {
      const data = await api("/api/sources/custom");
      refreshMetaSources(data.sources, data.presets);
      renderSourcesManager(data, editId);
    } catch (error) {
      el.modalBody.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    }
  }

  function renderSourcesManager(data, editId = "") {
    const sources = data.sources || [];
    const presets = data.presets || [];
    const editing = editId ? sources.find((item) => item.id === editId) : null;
    const presetId = editing ? editing.preset : (presets[0] ? presets[0].id : "generic_json");
    const preset = presets.find((item) => item.id === presetId) || {};
    const defaults = preset.defaults || {};
    const cfg = editing || defaults;
    const fieldMap = JSON.stringify(editing ? editing.field_map || {} : defaults.field_map || {}, null, 2);
    const extraParams = JSON.stringify(editing ? editing.extra_params || {} : defaults.extra_params || {}, null, 2);
    const headers = JSON.stringify(editing ? editing.headers || {} : {}, null, 2);
    const date = (value) => esc(value || "—");
    el.modalBody.innerHTML = `
      <div class="source-manager">
        <div class="source-manager-head">
          <div>
            <div class="eyebrow">Sources</div>
            <h3>${editing ? "编辑自定义来源" : "配置你的论文 API"}</h3>
            <p>支持 Semantic Scholar、CORE 或任意返回 JSON 的论文 API。账号/token 只保存在当前实例的隔离文件中，接口只返回掩码。</p>
          </div>
          <button class="btn btn-small" type="button" data-source-new>新增来源</button>
        </div>
        <div class="source-presets">
          <span class="section-note">快速预设</span>
          ${presets.map((item) => `<button class="chip" type="button" data-source-preset="${esc(item.id)}">${esc(item.name)}${item.auth_type !== "none" ? " · 可配密钥" : ""}</button>`).join("")}
        </div>
        <div class="source-list">
          ${sources.length ? sources.map((item) => `
            <article class="source-card ${item.enabled ? "" : "disabled"}">
              <div class="source-card-head">
                <div><strong>${esc(item.name)}</strong> ${badge(item.preset || "custom", "soft")} ${item.enabled ? badge("启用", "green") : badge("停用", "muted")} ${item.has_token ? badge("密钥已配置", "amber") : ""}</div>
                <div class="chips">
                  <button class="btn btn-small btn-ghost" type="button" data-source-edit="${esc(item.id)}">编辑</button>
                  <button class="btn btn-small btn-ghost" type="button" data-source-test="${esc(item.id)}">测试</button>
                  <button class="btn btn-small btn-ghost" type="button" data-source-toggle="${esc(item.id)}" data-enabled="${item.enabled ? "0" : "1"}">${item.enabled ? "停用" : "启用"}</button>
                  <button class="btn btn-small btn-ghost" type="button" data-source-delete="${esc(item.id)}">删除</button>
                </div>
              </div>
              <div class="source-card-meta">${esc(item.base_url)} · ${esc(item.method || "GET")} · ${item.masked_token ? `token ${esc(item.masked_token)}` : "未配置 token"}</div>
              <div class="source-test-result" id="sourceTest-${esc(item.id)}"></div>
            </article>
          `).join("") : `<div class="section-note">还没有自定义来源；从右侧预设开始，或新增一个通用 JSON API。</div>`}
        </div>
        <form id="sourceForm" class="source-form">
          <input type="hidden" id="sourceId" value="${editing ? esc(editing.id) : ""}" />
          <div class="source-form-grid">
            <label>预设
              <select id="sourcePreset">
                ${presets.map((item) => `<option value="${esc(item.id)}" ${item.id === presetId ? "selected" : ""}>${esc(item.name)}</option>`).join("")}
              </select>
            </label>
            <label>来源名称<input id="sourceName" maxlength="80" value="${esc(editing ? editing.name : preset.name || "")}" placeholder="例如：实验室 Semantic Scholar" /></label>
            <label>API 地址<input id="sourceBaseUrl" value="${esc(cfg.base_url || "")}" placeholder="https://api.example.org/search" /></label>
            <label>结果路径<input id="sourceResultPath" value="${esc(cfg.result_path || "results")}" placeholder="data / results / message.items" /></label>
            <label>查询参数<input id="sourceQueryParam" value="${esc(cfg.query_param || "query")}" /></label>
            <label>数量参数<input id="sourceLimitParam" value="${esc(cfg.limit_param || "limit")}" /></label>
            <label>认证方式
              <select id="sourceAuthType">
                ${["none", "header", "bearer", "query", "basic"].map((value) => `<option value="${value}" ${cfg.auth_type === value ? "selected" : ""}>${value}</option>`).join("")}
              </select>
            </label>
            <label>认证头/参数名<input id="sourceAuthName" value="${esc(cfg.auth_name || "")}" placeholder="x-api-key / Authorization / api_key" /></label>
            <label>token<input id="sourceToken" type="password" autocomplete="new-password" placeholder="${editing && editing.has_token ? "已保存，留空则保持不变" : "只保存在当前实例"}" /></label>
            <label>账号 / 邮箱<input id="sourceAccount" value="${esc(cfg.account || "")}" placeholder="可选；作为 account_param 的参数值" /></label>
            <label>账号参数名<input id="sourceAccountParam" value="${esc(cfg.account_param || "")}" placeholder="可选，例如 mailto" /></label>
            <label>请求方法
              <select id="sourceMethod"><option value="GET" ${cfg.method !== "POST" ? "selected" : ""}>GET</option><option value="POST" ${cfg.method === "POST" ? "selected" : ""}>POST</option></select>
            </label>
          </div>
          <details class="source-advanced">
            <summary>高级字段映射（JSON）</summary>
            <label>field_map<textarea id="sourceFieldMap" rows="7">${esc(fieldMap)}</textarea></label>
            <label>extra_params<textarea id="sourceExtraParams" rows="4">${esc(extraParams)}</textarea></label>
            <label>headers<textarea id="sourceHeaders" rows="3">${esc(headers)}</textarea></label>
          </details>
          <label class="source-check"><input id="sourceEnabled" type="checkbox" ${!editing || editing.enabled ? "checked" : ""} /> 启用该来源</label>
          ${editing && editing.has_token ? `<label class="source-check"><input id="sourceClearSecret" type="checkbox" /> 清除已保存 token</label>` : ""}
          <div class="source-form-actions">
            <button class="btn btn-primary" type="button" id="sourceSave">保存来源</button>
            <button class="btn btn-ghost" type="button" data-source-new>清空 / 新增</button>
            <span class="section-note">仅 HTTPS、443 端口，禁止本机/内网地址。</span>
          </div>
        </form>
        <div class="source-note">密钥文件：<code>data/custom_sources.secrets.json</code>（仅当前实例，发布包与仓库均不包含）。测试连接会真实请求一次 API，可能消耗额度。</div>
      </div>
    `;
    const presetOptions = {};
    presets.forEach((item) => { presetOptions[item.id] = item; });
    const fillPreset = (id) => {
      const item = presetOptions[id];
      if (!item) return;
      const values = item.defaults || {};
      const set = (selector, value) => { const node = $(selector); if (node !== null) node.value = value; };
      set("#sourceName", item.name || "");
      set("#sourceBaseUrl", values.base_url || "");
      set("#sourceResultPath", values.result_path || "results");
      set("#sourceQueryParam", values.query_param || "query");
      set("#sourceLimitParam", values.limit_param || "limit");
      set("#sourceAuthType", values.auth_type || "none");
      set("#sourceAuthName", values.auth_name || "");
      set("#sourceAccountParam", values.account_param || "");
      set("#sourceMethod", values.method || "GET");
      set("#sourceFieldMap", JSON.stringify(values.field_map || {}, null, 2));
      set("#sourceExtraParams", JSON.stringify(values.extra_params || {}, null, 2));
      set("#sourceHeaders", JSON.stringify(values.headers || {}, null, 2));
    };
    el.modalBody.querySelectorAll("[data-source-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        const id = button.dataset.sourcePreset;
        const select = $("#sourcePreset");
        if (select) select.value = id;
        fillPreset(id);
        $("#sourceForm")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    const presetSelect = $("#sourcePreset");
    if (presetSelect) presetSelect.addEventListener("change", () => fillPreset(presetSelect.value));
    el.modalBody.querySelectorAll("[data-source-new]").forEach((button) => {
      button.addEventListener("click", async () => { await loadSourcesManager(); });
    });
    el.modalBody.querySelectorAll("[data-source-edit]").forEach((button) => {
      button.addEventListener("click", async () => { await loadSourcesManager(button.dataset.sourceEdit); });
    });
    el.modalBody.querySelectorAll("[data-source-test]").forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.dataset.sourceTest;
        const target = $(`#sourceTest-${CSS.escape(id)}`);
        setLoadingText(button, "测试中…");
        if (target) target.innerHTML = `<div class="section-note">正在请求 API…</div>`;
        try {
          const result = await api(`/api/sources/custom/${encodeURIComponent(id)}/test`, { method: "POST" });
          if (target) target.innerHTML = `<div class="ok-box">连接成功 · ${result.count} 条 · ${result.latency_ms} ms<br>${ (result.titles || []).map((title) => esc(shortTitle(title, 70))).join("<br>") }</div>`;
        } catch (error) {
          if (target) target.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
        } finally {
          restoreButton(button);
        }
      });
    });
    el.modalBody.querySelectorAll("[data-source-toggle]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/sources/custom/${encodeURIComponent(button.dataset.sourceToggle)}/toggle`, {
            method: "POST",
            body: JSON.stringify({ enabled: button.dataset.enabled === "1" }),
          });
          await loadSourcesManager();
          toast("来源状态已更新");
        } catch (error) { toast(error.message, true); }
      });
    });
    el.modalBody.querySelectorAll("[data-source-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("删除这个自定义来源？已保存的 token 也会一并删除。")) return;
        try {
          await api(`/api/sources/custom/${encodeURIComponent(button.dataset.sourceDelete)}/delete`, { method: "POST" });
          await loadSourcesManager();
          toast("来源已删除");
        } catch (error) { toast(error.message, true); }
      });
    });
    const saveButton = $("#sourceSave");
    if (saveButton) {
      saveButton.addEventListener("click", async () => {
        let field_map;
        let extra_params;
        let headers;
        try {
          field_map = JSON.parse($("#sourceFieldMap").value || "{}");
          extra_params = JSON.parse($("#sourceExtraParams").value || "{}");
          headers = JSON.parse($("#sourceHeaders").value || "{}");
        } catch (error) {
          toast("高级配置不是有效 JSON", true);
          return;
        }
        const payload = {
          id: $("#sourceId").value || undefined,
          name: $("#sourceName").value,
          preset: $("#sourcePreset").value,
          base_url: $("#sourceBaseUrl").value,
          method: $("#sourceMethod").value,
          query_param: $("#sourceQueryParam").value,
          limit_param: $("#sourceLimitParam").value,
          extra_params,
          headers,
          result_path: $("#sourceResultPath").value,
          field_map,
          auth_type: $("#sourceAuthType").value,
          auth_name: $("#sourceAuthName").value,
          account_param: $("#sourceAccountParam").value,
          account: $("#sourceAccount").value,
          enabled: $("#sourceEnabled").checked,
          token: $("#sourceToken").value,
          password: "",
          clear_secret: Boolean($("#sourceClearSecret")?.checked),
        };
        setLoadingText(saveButton, "保存中…");
        try {
          const result = await api("/api/sources/custom", { method: "POST", body: JSON.stringify(payload) });
          toast("自定义来源已保存");
          await loadSourcesManager(result.source.id);
        } catch (error) {
          toast(error.message, true);
        } finally {
          restoreButton(saveButton);
        }
      });
    }
  }

  async function openSearchHistory() {
    openModal("检索记录", `<div class="history-panel"><div class="loading-line"></div><div class="section-note">正在读取检索留痕…</div></div>`);
    await loadSearchHistory();
  }

  async function loadSearchHistory() {
    try {
      const data = await api("/api/history?limit=100");
      const entries = data.entries || [];
      el.modalBody.innerHTML = `
        <div class="history-panel">
          <div class="history-head">
            <div><div class="eyebrow">Search Log</div><h3>检索留痕</h3><p>记录查询、来源、结果数与分层，便于复现检索过程；不代表结果质量。</p></div>
            <div class="chips">
              <a class="btn btn-small" href="/api/history/export?format=markdown&t=${Date.now()}">导出 Markdown</a>
              <a class="btn btn-small" href="/api/history/export?format=csv&t=${Date.now()}">导出 CSV</a>
              <button class="btn btn-small btn-ghost" type="button" id="clearHistory">清空</button>
            </div>
          </div>
          ${entries.length ? `<div class="history-list">${entries.map((entry) => `
            <article class="history-item">
              <div class="history-item-head"><strong>${esc(entry.query)}</strong><span>${esc(entry.at)}</span></div>
              <div class="history-meta">${esc(entry.source_label || entry.source)} · ${entry.count} 篇 · ${Object.entries(entry.layers || {}).map(([key, value]) => `${esc(key)} ${value}`).join(" / ") || "无分层"}</div>
              ${(entry.titles || []).length ? `<div class="section-note">${entry.titles.map((title) => esc(shortTitle(title, 60))).join(" · ")}</div>` : ""}
            </article>
          `).join("")}</div>` : `<div class="ok-box">还没有检索记录。完成一次检索后会自动留痕。</div>`}
        </div>
      `;
      const clearButton = $("#clearHistory");
      if (clearButton) clearButton.addEventListener("click", async () => {
        if (!window.confirm("清空所有检索留痕？")) return;
        try { await api("/api/history/clear", { method: "POST" }); await loadSearchHistory(); toast("检索记录已清空"); }
        catch (error) { toast(error.message, true); }
      });
    } catch (error) {
      el.modalBody.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    }
  }

  async function openGlossaryBrowser() {
    openModal("术语表", `<div class="glossary-browser"><div class="section-note">正在加载术语…</div></div>`);
    try {
      const data = await api("/api/glossary");
      const all = data.items || [];
      const categories = data.categories || [];
      let activeCategory = "";
      let query = "";
      const body = el.modalBody.querySelector(".glossary-browser");
      body.innerHTML = `
        <div class="glossary-toolbar">
          <input id="glossarySearch" type="search" placeholder="搜索英文或中文术语" aria-label="搜索术语" />
          <span class="section-note">${data.all_total || all.length} 个术语</span>
          <button class="btn btn-small btn-ghost" type="button" id="glossaryManage">管理词库</button>
        </div>
        <div class="chips glossary-categories">
          <button class="chip active" type="button" data-glossary-category="">全部</button>
          ${categories.map((category) => `<button class="chip" type="button" data-glossary-category="${esc(category.id)}">${esc(category.label)} ${category.count}</button>`).join("")}
        </div>
        <div class="glossary-results"></div>`;
      const results = body.querySelector(".glossary-results");
      const render = () => {
        const needle = query.trim().toLowerCase();
        const filtered = all.filter((item) =>
          (!activeCategory || item.category === activeCategory)
          && (!needle || item.term.toLowerCase().includes(needle) || item.zh.toLowerCase().includes(needle))
        );
        results.innerHTML = filtered.length
          ? filtered.map((item) => `<div class="glossary-row"><strong>${esc(item.term)}</strong><span>${esc(item.zh)}</span><em>${esc(item.category)}${item.source && item.source !== "内置术语" ? " · " + esc(item.source) : ""}</em></div>`).join("")
          : `<div class="translation-placeholder">没有匹配的术语。</div>`;
        body.querySelectorAll("[data-glossary-category]").forEach((button) => {
          button.classList.toggle("active", button.dataset.glossaryCategory === activeCategory);
        });
      };
      body.querySelector("#glossarySearch").addEventListener("input", (event) => {
        query = event.target.value;
        render();
      });
      body.querySelectorAll("[data-glossary-category]").forEach((button) => {
        button.addEventListener("click", () => {
          activeCategory = button.dataset.glossaryCategory;
          render();
        });
      });
      const manageButton = body.querySelector("#glossaryManage");
      if (manageButton) manageButton.addEventListener("click", () => openGlossaryManager());
      render();
    } catch (error) {
      el.modalBody.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    }
  }

  async function openGlossaryManager() {
    openModal("术语库管理", `<div class="glossary-manager"><div class="loading-line"></div><div class="section-note">正在读取术语包…</div></div>`);
    await loadGlossaryManager();
  }

  async function loadGlossaryManager() {
    try {
      const data = await api("/api/glossary/packs");
      const builtin = data.builtin || {};
      const packs = data.packs || [];
      const presets = data.presets || [];
      const effective = (builtin.count || 0) + packs.filter((pack) => pack.enabled).reduce((sum, pack) => sum + (pack.count || 0), 0);
      if (state.meta) state.meta.glossary_count = effective;
      el.modalBody.innerHTML = `
        <div class="glossary-manager">
          <div class="glossary-manager-head">
            <div><div class="eyebrow">Glossary Library</div><h3>在线与本地术语包</h3><p>内置术语优先；扩展包补充新词。在线包只保存到当前实例，并显示来源与许可。</p></div>
            <div class="chips"><button class="btn btn-small" type="button" id="glossaryBack">返回浏览</button></div>
          </div>
          <div class="glossary-pack-builtin">
            <div><strong>${esc(builtin.name || "内置术语")}</strong> ${badge(`${builtin.count || 0} 条`, "green")} ${badge("始终启用", "soft")}</div>
            <div class="section-note">${esc(builtin.license || "项目内置")} · 覆盖 7 个分类的论文阅读与翻译常用术语。</div>
          </div>
          <div class="section-note">已安装扩展包</div>
          <div class="glossary-pack-list">
            ${packs.length ? packs.map((pack) => {
              const presetId = pack.id && pack.id.startsWith("preset-") ? pack.id.slice(7) : "";
              return `
                <article class="glossary-pack ${pack.enabled ? "" : "disabled"}">
                  <div class="glossary-pack-head"><strong>${esc(pack.name)}</strong>${pack.enabled ? badge("启用", "green") : badge("停用", "muted")} ${badge(`${pack.count} 条`, "soft")}</div>
                  <div class="glossary-pack-meta">${esc(pack.license || "未标注许可")} · ${esc(pack.attribution || "本地导入")}${pack.source_url ? ` · <a href="${esc(safeUrl(pack.source_url))}" target="_blank" rel="noopener">来源</a>` : ""}</div>
                  <div class="chips">
                    <button class="btn btn-small btn-ghost" type="button" data-pack-toggle="${esc(pack.id)}" data-enabled="${pack.enabled ? "0" : "1"}">${pack.enabled ? "停用" : "启用"}</button>
                    ${presetId ? `<button class="btn btn-small btn-ghost" type="button" data-pack-update="${esc(presetId)}">更新</button>` : ""}
                    <button class="btn btn-small btn-ghost" type="button" data-pack-delete="${esc(pack.id)}">删除</button>
                  </div>
                </article>
              `;
            }).join("") : `<div class="section-note">还没有扩展包；可以从下方在线预设下载，或导入本地文件。</div>`}
          </div>
          <div class="section-note">在线术语库</div>
          <div class="glossary-preset-grid">
            ${presets.map((preset) => `
              <article class="glossary-preset">
                <div class="glossary-preset-head"><strong>${esc(preset.name)}</strong>${preset.installed ? badge("已安装", "green") : ""}</div>
                <div class="section-note">${esc(preset.note || "")}</div>
                <div class="glossary-pack-meta">${esc(preset.license || "未标注许可")} · ${esc(preset.attribution || "")}${preset.homepage ? ` · <a href="${esc(safeUrl(preset.homepage))}" target="_blank" rel="noopener">项目主页</a>` : ""}</div>
                <button class="btn btn-small btn-primary" type="button" data-preset-download="${esc(preset.id)}">${preset.installed ? "更新在线包" : "下载并导入"}</button>
              </article>
            `).join("")}
          </div>
          <div class="glossary-import-grid">
            <form class="glossary-import-card" id="glossaryImportForm">
              <strong>导入本地术语文件</strong>
              <div class="section-note">支持 JSON、CSV/TSV、Markdown 表格；最大 5 MB。</div>
              <label>名称<input id="glossaryImportName" maxlength="80" placeholder="例如：实验室术语表" /></label>
              <label>许可<input id="glossaryImportLicense" maxlength="160" placeholder="例如：internal / CC BY 4.0" /></label>
              <label>分类
                <select id="glossaryImportCategory">
                  ${["在线扩展", "大模型与推理", "机器学习基础", "芯片与存算", "数学与统计", "系统与性能", "科研与写作", "开放科学"].map((value) => `<option value="${value}">${value}</option>`).join("")}
                </select>
              </label>
              <input id="glossaryImportFile" type="file" accept=".json,.csv,.tsv,.md,.txt,application/json,text/csv,text/plain" />
              <button class="btn btn-small" type="button" id="glossaryImportBtn">导入</button>
            </form>
            <div class="glossary-import-card">
              <strong>从 URL 下载</strong>
              <div class="section-note">只允许 HTTPS 公网地址；支持 JSON/CSV/TSV/Markdown。请确认来源许可。</div>
              <label>URL<input id="glossaryOnlineUrl" placeholder="https://example.org/terms.json" /></label>
              <label>名称<input id="glossaryOnlineName" maxlength="80" placeholder="在线术语包" /></label>
              <button class="btn btn-small" type="button" id="glossaryOnlineBtn">下载并导入</button>
            </div>
          </div>
          <div class="source-note">术语包保存在 <code>data/glossaries/</code>，不进入发布包或仓库；在线数据仍受原项目许可与 API 可用性约束。内置术语在重名时优先。</div>
        </div>
      `;
      $("#glossaryBack")?.addEventListener("click", () => openGlossaryBrowser());
      el.modalBody.querySelectorAll("[data-pack-toggle]").forEach((button) => {
        button.addEventListener("click", async () => {
          try {
            await api(`/api/glossary/packs/${encodeURIComponent(button.dataset.packToggle)}/toggle`, { method: "POST", body: JSON.stringify({ enabled: button.dataset.enabled === "1" }) });
            await loadGlossaryManager();
            toast("术语包状态已更新");
          } catch (error) { toast(error.message, true); }
        });
      });
      el.modalBody.querySelectorAll("[data-pack-delete]").forEach((button) => {
        button.addEventListener("click", async () => {
          if (!window.confirm("删除这个术语包？只会删除当前实例中的副本。")) return;
          try {
            await api(`/api/glossary/packs/${encodeURIComponent(button.dataset.packDelete)}/delete`, { method: "POST" });
            await loadGlossaryManager();
            toast("术语包已删除");
          } catch (error) { toast(error.message, true); }
        });
      });
      const downloadPack = async (presetId, button) => {
        if (button) setLoadingText(button, "下载中…");
        try {
          const result = await api("/api/glossary/packs/online", { method: "POST", body: JSON.stringify({ preset_id: presetId }) });
          await loadGlossaryManager();
          toast(`已导入 ${result.pack.count} 条术语`);
        } catch (error) {
          toast(error.message, true);
          if (button) restoreButton(button);
        }
      };
      el.modalBody.querySelectorAll("[data-preset-download]").forEach((button) => {
        button.addEventListener("click", () => downloadPack(button.dataset.presetDownload, button));
      });
      el.modalBody.querySelectorAll("[data-pack-update]").forEach((button) => {
        button.addEventListener("click", () => downloadPack(button.dataset.packUpdate, button));
      });
      const importButton = $("#glossaryImportBtn");
      if (importButton) importButton.addEventListener("click", async () => {
        const file = $("#glossaryImportFile")?.files?.[0];
        const name = $("#glossaryImportName")?.value.trim() || "";
        if (!file) { toast("先选择一个术语文件", true); return; }
        const form = new FormData();
        form.append("file", file);
        form.append("name", name || file.name);
        form.append("license", $("#glossaryImportLicense")?.value.trim() || "");
        form.append("category", $("#glossaryImportCategory")?.value || "在线扩展");
        setLoadingText(importButton, "导入中…");
        try {
          const result = await api("/api/glossary/packs/import", { method: "POST", body: form });
          await loadGlossaryManager();
          toast(`已导入 ${result.pack.count} 条术语`);
        } catch (error) { toast(error.message, true); restoreButton(importButton); }
      });
      const onlineButton = $("#glossaryOnlineBtn");
      if (onlineButton) onlineButton.addEventListener("click", async () => {
        const url = $("#glossaryOnlineUrl")?.value.trim() || "";
        if (!url) { toast("先填写在线术语库 URL", true); return; }
        setLoadingText(onlineButton, "下载中…");
        try {
          const result = await api("/api/glossary/packs/online", { method: "POST", body: JSON.stringify({ url, name: $("#glossaryOnlineName")?.value.trim() || "在线术语包" }) });
          await loadGlossaryManager();
          toast(`已导入 ${result.pack.count} 条术语`);
        } catch (error) { toast(error.message, true); restoreButton(onlineButton); }
      });
    } catch (error) {
      el.modalBody.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    }
  }

  async function switchTab(tab) {
    // Leaving the projects view tears down the draft workbench, so unsaved
    // text has to be flushed before the panel is replaced.
    if (window.PaperPilotDraft?.isDirty?.() && !(await window.PaperPilotDraft.flush())) return;
    state.tab = tab;
    if (tab !== "agent") stopAgentPolling();
    if (tab !== "agent") window.PaperPilotLive?.disconnect();
    if (location.hash !== `#${tab}`) {
      history.replaceState(null, "", `#${tab}`);
    }
    $$(".tab", el.tabs).forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
    await loadTab(tab);
  }

  async function loadTab(tab) {
    if (tab === "copilot") {
      if (window.PaperPilotProjects?.openCopilot) {
        await window.PaperPilotProjects.openCopilot(el.panel);
      } else {
        el.panel.innerHTML = emptyState("Copilot 模块未加载", "请刷新页面重试。");
      }
      return;
    }

    if (tab === "projects") {
      if (window.PaperPilotProjects) {
        await window.PaperPilotProjects.render(el.panel);
      } else {
        el.panel.innerHTML = emptyState("项目模块未加载", "请刷新页面重试。");
      }
      return;
    }

    if (tab === "dossier") {
      if (window.PaperPilotDossier) {
        await window.PaperPilotDossier.render(el.panel);
      } else {
        el.panel.innerHTML = emptyState("档案模块未加载", "请刷新页面重试。");
      }
      return;
    }

    if (tab === "read") {
      if (state.read) renderRead(state.read);
      else if (state.activePaper) openPaper(state.activePaper, false);
      else el.panel.innerHTML = emptyState("先选择一篇论文", "从左侧结果里点一篇，或者先检索一个方向。");
      return;
    }

    if (tab === "translate") {
      if (!state.activePaper) {
        el.panel.innerHTML = emptyState("先选择一篇论文", "翻译工作台需要对一篇论文生成双语对照。");
        return;
      }
      await loadTranslation();
      return;
    }

    if (tab === "citations") {
      if (!state.activePaper) {
        el.panel.innerHTML = emptyState("先选择一篇论文", "引用网络需要一篇带 DOI 的论文作为起点。");
        return;
      }
      await loadCitations();
      return;
    }

    if (tab === "compare") {
      if (state.selected.size < 2) {
        el.panel.innerHTML = emptyState("至少选择 2 篇论文", "在左侧结果卡片上勾选论文，再回到这里看结构化对比。");
        return;
      }
      await loadCompare();
      return;
    }

    if (tab === "graph") {
      await loadGraph();
      return;
    }

    if (tab === "innovate") {
      if (!state.activePaper) {
        el.panel.innerHTML = emptyState("先选择一篇论文", "创新点提炼需要一篇论文作为对象。");
        return;
      }
      await loadInnovate();
      return;
    }

    if (tab === "transform") {
      if (!state.activePaper) {
        el.panel.innerHTML = emptyState("先选择一篇论文", "复现计划和课程案例需要一篇论文作为对象。");
        return;
      }
      await loadTransform();
      return;
    }

    if (tab === "gaps") {
      await loadGaps();
      return;
    }

    if (tab === "topic") {
      await loadTopic();
      return;
    }

    if (tab === "writing") {
      await loadWriting();
      return;
    }

    if (tab === "ask") {
      await loadAsk();
      return;
    }

    if (tab === "agent") {
      await loadAgent();
      return;
    }

    if (tab === "library") {
      await loadLibrary();
      return;
    }

    if (tab === "eval") {
      await loadEval();
    }
  }

  async function loadCompare() {
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在生成对比", "抽取字段、检查可比性、寻找共识与分歧…")}`;
    try {
      const data = await api("/api/compare", {
        method: "POST",
        body: JSON.stringify({ paper_ids: Array.from(state.selected) }),
      });
      state.compare = data;
      renderCompare(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  const LEGACY_COMPARE_COLUMNS = [
    { key: "title", label: "论文", kind: "paper" },
    { key: "process", label: "工艺 / 验证" },
    { key: "adc", label: "ADC 方案" },
    { key: "task", label: "任务 / 负载" },
    { key: "headline", label: "头条指标" },
    { key: "limitation", label: "主要局限" },
    { key: "evidence", label: "原文证据", kind: "quote" },
  ];

  function compareColumns(data) {
    return Array.isArray(data?.columns) && data.columns.length
      ? data.columns
      : LEGACY_COMPARE_COLUMNS;
  }

  function compareValue(row, column) {
    const raw = row?.values && Object.prototype.hasOwnProperty.call(row.values, column.key)
      ? row.values[column.key]
      : row?.[column.key];
    if (Array.isArray(raw)) return raw.join("；");
    if (raw === null || raw === undefined || raw === "") return "未提及";
    return String(raw);
  }

  function compareCell(row, column) {
    if (column.kind === "paper" || column.key === "title") {
      const url = safeUrl(row.source_url);
      return `
        <div class="table-title">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(row.title || "未命名论文")}</a>` : esc(row.title || "未命名论文")}</div>
        <div class="table-sub">${esc(row.year || "—")} · ${esc(row.venue || "—")} · ${esc(row.extraction_level || "摘要级")}</div>
      `;
    }
    const value = compareValue(row, column);
    if (column.kind === "quote" || column.key === "evidence") {
      return `<span class="section-note">“${esc(value)}”</span>`;
    }
    return esc(value);
  }

  function renderCompare(data) {
    const rows = data.rows || [];
    const columns = compareColumns(data);
    el.panel.innerHTML = `
      <div class="compare-toolbar">
        <span class="badge badge-soft">${rows.length} 篇论文</span>
        <button class="btn btn-small" type="button" id="compareExport">导出对比 Markdown</button>
        <button class="btn btn-small btn-ghost" type="button" id="compareClear">清空选择</button>
      </div>
      <div class="warn-box">
        <strong>可比性提醒：</strong>${esc((data.warnings || []).join(" "))}
      </div>
      ${(data.comparability || []).length ? `
      <div class="card compare-comparability">
        <h3 class="card-title">可比性判定 <span class="badge badge-soft">${data.comparability.length} 对</span></h3>
        <div class="comparability-list">
          ${data.comparability.map((pair) => {
            const titles = pair.titles || pair.paper_ids || [];
            return `
              <div class="comparability-item ${pair.comparable ? "is-comparable" : "is-not"}">
                <div class="comparability-head">
                  ${badge(pair.comparable ? "可比" : "不可直接比较", pair.comparable ? "green" : "pink")}
                  <span class="comparability-pair">${esc(titles.join(" ↔ "))}</span>
                </div>
                <p>${esc(pair.reason || "")}</p>
                ${(pair.differences || []).length ? `<div class="muted small">差异：${esc(pair.differences.join("；"))}</div>` : ""}
              </div>
            `;
          }).join("")}
        </div>
      </div>` : ""}
      <div class="table-wrap">
        <table class="compare">
          <thead><tr>
            ${columns.map((column) => `<th>${esc(column.label || column.key)}</th>`).join("")}
          </tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                ${columns.map((column) => `<td${column.key === "title" ? ' class="title-cell"' : ""}>${compareCell(row, column)}</td>`).join("")}
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      <div class="stack" style="margin-top:12px">
        <div class="card">
          <h3 class="card-title">共识 <span class="badge badge-green">跨论文</span></h3>
          <div class="insight-list">${(data.consensus || []).map((item) => `<div class="insight-item">${esc(item)}</div>`).join("")}</div>
        </div>
        <div class="card">
          <h3 class="card-title">分歧与取舍 <span class="badge badge-pink">不要直接排序</span></h3>
          <div class="insight-list">${(data.divergence || []).map((item) => `<div class="insight-item pink">${esc(item)}</div>`).join("")}</div>
        </div>
        <div class="card">
          <h3 class="card-title">可继续做的空白</h3>
          <div class="insight-list">${(data.gaps || []).map((gap) => `<div class="insight-item">${esc(gap.title)}：${esc(gap.question)}</div>`).join("")}</div>
        </div>
      </div>
    `;
    const exportButton = $("#compareExport");
    if (exportButton) exportButton.addEventListener("click", exportMarkdown);
    const clearButton = $("#compareClear");
    if (clearButton) clearButton.addEventListener("click", () => {
      state.selected.clear();
      el.selectAll.checked = false;
      renderResults();
      updateCompareCount();
      loadTab("compare");
    });
  }

  const GRAPH_TYPES = {
    paper: { label: "论文", color: "#4ce3f7", rx: 118, ry: 70 },
    concept: { label: "概念 / 方法", color: "#8fb7ff", rx: 235, ry: 148 },
    task: { label: "数据 / 任务", color: "#6ee7b7", rx: 322, ry: 198 },
    metric: { label: "指标", color: "#ffca6b", rx: 380, ry: 232 },
    limitation: { label: "局限", color: "#ff6aa9", rx: 424, ry: 252 },
  };
  const GRAPH_EDGE_LABELS = {
    discusses: "讨论",
    evaluates_on: "评估于",
    reports: "报告",
    limited_by: "局限",
    co_occurs: "共现",
  };

  function graphText(value, limit = 18) {
    const text = String(value || "");
    return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
  }

  function graphLayout(nodes) {
    const groups = {};
    nodes.forEach((node) => {
      if (!groups[node.type]) groups[node.type] = [];
      groups[node.type].push(node);
    });
    const positions = {};
    Object.entries(groups).forEach(([type, items]) => {
      const config = GRAPH_TYPES[type] || GRAPH_TYPES.concept;
      const offset = type === "paper" ? 0 : Math.PI / 8;
      items.forEach((node, index) => {
        const angle = offset + (Math.PI * 2 * index) / Math.max(1, items.length);
        let x = 470 + config.rx * Math.cos(angle);
        let y = 300 + config.ry * Math.sin(angle);
        if (type === "paper" && items.length === 1) {
          x = 470;
          y = 300;
        }
        positions[node.id] = {
          x: Math.max(42, Math.min(918, x)),
          y: Math.max(34, Math.min(566, y)),
        };
      });
    });
    return positions;
  }

  function graphNeighbors(data, focusId) {
    const set = new Set();
    if (!focusId) return set;
    (data.edges || []).forEach((edge) => {
      if (edge.source === focusId) set.add(edge.target);
      if (edge.target === focusId) set.add(edge.source);
    });
    return set;
  }

  function graphSideDefault(data) {
    const concepts = (data.nodes || [])
      .filter((node) => node.type !== "paper")
      .sort((a, b) => (b.paper_count || 0) - (a.paper_count || 0))
      .slice(0, 8);
    return `
      <div class="graph-side-head">
        <h3>图谱怎么读</h3>
        ${badge(`${data.stats.papers} 篇论文`, "soft")}
      </div>
      <p class="graph-hint">节点由论文标题/方法句、结构化指标、结果名与局限句合并而来；同名节点只代表共现，不代表结论一致。</p>
      <div class="graph-rank">
        ${concepts.map((node) => `
          <button class="graph-rank-row" type="button" data-graph-jump="${esc(node.id)}">
            <span class="graph-dot" style="background:${GRAPH_TYPES[node.type]?.color || "#8fb7ff"}"></span>
            <span>${esc(graphText(node.label, 22))}</span>
            <b>${node.paper_count}</b>
          </button>
        `).join("") || `<span class="muted small">没有可展示的非论文节点。</span>`}
      </div>
      <p class="muted small">点击画布节点查看来源论文与原文摘句。</p>
    `;
  }

  function graphSideFocus(data, node) {
    const type = GRAPH_TYPES[node.type]?.label || node.type;
    const paperMap = {};
    (data.papers || []).forEach((paper) => {
      paperMap[paper.paper_id] = paper;
      paperMap[paper.id] = paper;
    });
    const linked = (node.papers || []).map((paperId) => paperMap[paperId]).filter(Boolean);
    const neighborIds = graphNeighbors(data, node.id);
    const nodeMap = {};
    (data.nodes || []).forEach((item) => { nodeMap[item.id] = item; });
    const neighbors = Array.from(neighborIds)
      .map((id) => nodeMap[id])
      .filter(Boolean)
      .slice(0, 8);
    return `
      <div class="graph-side-head">
        <h3>${esc(node.label)}</h3>
        ${badge(type, "soft")}
      </div>
      <p class="graph-hint">${node.paper_count} 篇论文涉及此节点。</p>
      <div class="graph-side-section">
        <div class="section-note">来源论文</div>
        ${linked.map((paper) => `
          <div class="graph-paper-row">
            <b>${esc(paper.year || "—")}</b>
            <span>${esc(graphText(paper.title || paper.paper_id, 46))}</span>
          </div>
        `).join("") || `<span class="muted small">未记录来源论文。</span>`}
      </div>
      <div class="graph-side-section">
        <div class="section-note">原文摘句</div>
        ${(node.evidence || []).map((item) => `
          <blockquote class="graph-quote">${esc(item.quote)}</blockquote>
        `).join("") || `<span class="muted small">该节点来自结构化字段，没有单独摘句。</span>`}
      </div>
      <div class="graph-side-section">
        <div class="section-note">相邻节点</div>
        <div class="chips">
          ${neighbors.map((item) => `<button class="chip" type="button" data-graph-jump="${esc(item.id)}">${esc(graphText(item.label, 20))}</button>`).join("") || `<span class="muted small">无相邻节点。</span>`}
        </div>
      </div>
    `;
  }

  function renderGraph(data, options = {}) {
    const previousSide = $("#graphSide");
    const previousScroll = previousSide ? previousSide.scrollTop : 0;
    const nodes = data.nodes || [];
    if (!nodes.length) {
      el.panel.innerHTML = emptyState("没有可绘制的论文", (data.notes || [])[0] || "先检索、上传或收藏论文，再打开图谱。");
      return;
    }
    const positions = graphLayout(nodes);
    const focus = state.graphFocus;
    const neighbors = focus ? graphNeighbors(data, focus) : new Set();
    const typeFilter = state.graphType || "all";
    const nodeMap = {};
    nodes.forEach((node) => { nodeMap[node.id] = node; });
    const edgeLines = (data.edges || []).map((edge) => {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) return "";
      const active = !focus || edge.source === focus || edge.target === focus;
      const dimType = typeFilter !== "all"
        && nodeMap[edge.source]?.type !== typeFilter
        && nodeMap[edge.target]?.type !== typeFilter;
      const opacity = dimType ? 0.04 : (active ? (edge.type === "co_occurs" ? 0.22 : 0.42) : 0.06);
      const dash = edge.type === "co_occurs" ? ` stroke-dasharray="3 4"` : "";
      return `<line x1="${from.x.toFixed(1)}" y1="${from.y.toFixed(1)}" x2="${to.x.toFixed(1)}" y2="${to.y.toFixed(1)}"
        class="graph-edge graph-edge-${esc(edge.type)}" stroke-opacity="${opacity}"${dash} />`;
    }).join("");
    const nodeShapes = nodes.map((node) => {
      const pos = positions[node.id];
      const config = GRAPH_TYPES[node.type] || GRAPH_TYPES.concept;
      const radius = (node.type === "paper" ? 13 : 8) + Math.min(6, node.paper_count || 1);
      const active = !focus || node.id === focus || neighbors.has(node.id);
      const dimType = typeFilter !== "all" && node.type !== typeFilter;
      const opacity = dimType ? 0.18 : (active ? 1 : 0.28);
      const labelY = pos.y > 330 ? pos.y - radius - 7 : pos.y + radius + 13;
      return `
        <g class="graph-node ${node.id === focus ? "is-focus" : ""}" data-graph-node="${esc(node.id)}"
           data-type="${esc(node.type)}" tabindex="0" role="button" aria-label="${esc(node.label)}" opacity="${opacity}">
          <circle cx="${pos.x.toFixed(1)}" cy="${pos.y.toFixed(1)}" r="${radius}" fill="${config.color}" />
          <text x="${pos.x.toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle">${esc(graphText(node.label, 17))}</text>
          <title>${esc(node.label)} · ${node.paper_count} 篇</title>
        </g>
      `;
    }).join("");
    el.panel.innerHTML = `
      <div class="graph-toolbar">
        <span class="badge badge-soft">${data.stats.papers} 篇论文</span>
        <span class="badge badge-soft">${data.stats.nodes} 节点</span>
        <span class="badge badge-soft">${data.stats.edges} 边</span>
        <div class="graph-filters">
          <button class="chip ${typeFilter === "all" ? "active" : ""}" type="button" data-graph-type="all">全部</button>
          ${Object.entries(GRAPH_TYPES).map(([key, config]) => `<button class="chip ${typeFilter === key ? "active" : ""}" type="button" data-graph-type="${key}">${config.label}</button>`).join("")}
        </div>
        ${focus ? `<button class="chip graph-reset" type="button" data-graph-reset>重置聚焦</button>` : ""}
        <span class="graph-toolbar-spacer"></span>
        <button class="btn btn-small" type="button" id="graphExportMd">导出 Markdown</button>
        <button class="btn btn-small btn-ghost" type="button" id="graphExportJson">导出 JSON</button>
      </div>
      <div class="warn-box graph-note">${esc((data.notes || []).join(" "))}</div>
      <div class="graph-layout">
        <div class="graph-canvas">
          <svg id="graphSvg" class="graph-svg" viewBox="0 0 960 620" role="img" aria-label="论文概念图谱">
            <g class="graph-edges">${edgeLines}</g>
            <g class="graph-nodes">${nodeShapes}</g>
          </svg>
          <div class="graph-legend">
            ${Object.entries(GRAPH_TYPES).map(([, config]) => `<span><i style="background:${config.color}"></i>${config.label}</span>`).join("")}
          </div>
        </div>
        <aside class="graph-side" id="graphSide">
          ${focus && nodeMap[focus] ? graphSideFocus(data, nodeMap[focus]) : graphSideDefault(data)}
        </aside>
      </div>
    `;
    const nextSide = $("#graphSide");
    if (nextSide) nextSide.scrollTop = options.keepScroll ? previousScroll : 0;
    const svg = $("#graphSvg");
    if (svg) {
      const toggleFocus = (event) => {
        const group = event.target.closest("[data-graph-node]");
        if (!group) return false;
        const nodeId = group.dataset.graphNode || "";
        state.graphFocus = state.graphFocus === nodeId ? "" : nodeId;
        renderGraph(data);
        return true;
      };
      svg.addEventListener("click", (event) => {
        toggleFocus(event);
      });
      svg.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        if (toggleFocus(event)) event.preventDefault();
      });
    }
    $$("[data-graph-type]").forEach((button) => {
      button.addEventListener("click", () => {
        state.graphType = button.dataset.graphType || "all";
        renderGraph(data, { keepScroll: true });
      });
    });
    $$("[data-graph-reset]").forEach((button) => {
      button.addEventListener("click", () => {
        state.graphFocus = "";
        renderGraph(data);
      });
    });
    $$("[data-graph-jump]").forEach((button) => {
      button.addEventListener("click", () => {
        state.graphFocus = button.dataset.graphJump || "";
        renderGraph(data);
      });
    });
    const exportMd = $("#graphExportMd");
    if (exportMd) exportMd.addEventListener("click", () => graphExport(data, "md"));
    const exportJson = $("#graphExportJson");
    if (exportJson) exportJson.addEventListener("click", () => graphExport(data, "json"));
  }

  function graphExport(data, kind) {
    const stamp = new Date().toISOString().slice(0, 10);
    if (kind === "json") {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `paperpilot-graph-${stamp}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast("图谱 JSON 已导出");
      return;
    }
    let text = `# PaperPilot 概念图谱\n\n> 范围：${data.scope_label} · ${data.stats.papers} 篇论文 · ${data.stats.nodes} 节点 · ${data.stats.edges} 边\n\n`;
    Object.entries(GRAPH_TYPES).forEach(([type, config]) => {
      const items = (data.nodes || []).filter((node) => node.type === type);
      if (!items.length) return;
      text += `## ${config.label}\n\n`;
      items.forEach((node) => {
        text += `- **${node.label}**（${node.paper_count} 篇）\n`;
      });
      text += "\n";
    });
    text += `## 关系\n\n`;
    const nodeMap = {};
    (data.nodes || []).forEach((node) => { nodeMap[node.id] = node; });
    (data.edges || []).forEach((edge) => {
      const from = nodeMap[edge.source]?.label || edge.source;
      const to = nodeMap[edge.target]?.label || edge.target;
      text += `- ${from} —${GRAPH_EDGE_LABELS[edge.type] || edge.type}→ ${to}（${edge.weight}）\n`;
    });
    text += `\n---\n\n${(data.notes || []).map((note) => `- ${note}`).join("\n")}\n`;
    downloadMarkdown(text, `paperpilot-graph-${stamp}.md`);
  }

  async function loadGraph() {
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在生成概念图谱", "汇总论文、方法、数据任务、指标与局限…")}`;
    try {
      const useSelected = state.selected.size >= 2;
      const data = await api("/api/graph", {
        method: "POST",
        body: JSON.stringify({
          scope: useSelected ? "selected" : "all",
          paper_ids: useSelected ? Array.from(state.selected) : [],
          topic: $("#queryInput")?.value || "",
        }),
      });
      state.graph = data;
      state.graphFocus = "";
      state.graphType = "all";
      renderGraph(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  async function loadInnovate() {
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在提炼创新点", "分离作者声称、实验证据与模型推断…")}`;
    try {
      const data = await api("/api/innovate", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.activePaper }),
      });
      state.innovate = data;
      renderInnovate(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function renderInnovate(data) {
    const scores = data.scores || {};
    el.panel.innerHTML = `
      <section class="paper-head">
        <div class="paper-head-top">
          <div>
            <div class="eyebrow">创新点核验</div>
            <h2 class="paper-title" style="font-size:16px">${esc(data.paper.title)}</h2>
            <div class="paper-meta">${badge(data.paper.extraction_level || "摘要级", "soft")}${badge("三栏分离", "pink")}</div>
          </div>
          <div class="paper-actions">
            <button class="btn btn-small" type="button" id="innovateToGaps">去看研究空白</button>
          </div>
        </div>
        <div class="paper-tldr">${esc(data.note || "")}</div>
      </section>

      <div class="three-col">
        <div class="claim-col claim-a">
          <h4>作者声称</h4>
          <ul>${(data.claims || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
        </div>
        <div class="claim-col claim-b">
          <h4>实验证据</h4>
          <ul>${(data.evidence || []).slice(0, 6).map((item) => `<li>${esc(item.label || item.field || "证据")}：${esc(item.quote)}</li>`).join("")}</ul>
        </div>
        <div class="claim-col claim-c">
          <h4>模型推断</h4>
          <ul>${(data.inferences || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
        </div>
      </div>

      <div class="read-grid" style="margin-top:12px">
        <div class="stack">
          <div class="card">
            <h3 class="card-title">假设与边界</h3>
            <div class="insight-list">${(data.assumptions || []).map((item) => `<div class="insight-item">${esc(item)}</div>`).join("") || `<div class="section-note">材料未明确列出假设。</div>`}</div>
          </div>
          <div class="card">
            <h3 class="card-title">局限 <span class="badge badge-amber">证据缺口</span></h3>
            <div class="insight-list">${(data.limitations || []).map((item) => `<div class="insight-item pink">${esc(item)}</div>`).join("") || `<div class="section-note">材料未明确列出局限。</div>`}</div>
          </div>
          <div class="card">
            <h3 class="card-title">审稿人问题</h3>
            ${(data.reviewer_questions || []).map((question, index) => `<div class="review-q"><span class="num">Q${index + 1}</span><span>${esc(question)}</span></div>`).join("") || `<div class="section-note">暂无。</div>`}
          </div>
        </div>
        <aside class="stack">
          <div class="card">
            <h3 class="card-title">启发式评分</h3>
            ${Object.entries(scores).map(([key, value]) => `
              <div class="score-row">
                <span>${esc(key)}</span>
                <div class="score-bar"><div class="score-fill" style="width:${Math.max(0, Math.min(100, Number(value) * 20))}%"></div></div>
                <strong style="color:var(--cyan);font-family:var(--mono)">${esc(value)}</strong>
              </div>
            `).join("")}
            <div class="section-note" style="margin-top:8px">评分用于讨论，不是论文结论。</div>
          </div>
          <div class="card">
            <h3 class="card-title">下一步</h3>
            <div class="insight-list">
              <div class="insight-item">把“证据不足”的字段列成实验清单</div>
              <div class="insight-item pink">用研究空白雷达找可验证的问题</div>
            </div>
          </div>
        </aside>
      </div>
    `;
    const button = $("#innovateToGaps");
    if (button) button.addEventListener("click", () => switchTab("gaps"));
  }

  async function loadTransform() {
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在生成可执行成果", "复现计划、代码骨架、组会提纲与课程案例…")}`;
    try {
      const data = await api("/api/transform", {
        method: "POST",
        body: JSON.stringify({ paper_id: state.activePaper, outputs: ["repro", "code", "slides", "quiz", "course"] }),
      });
      state.transform = data;
      renderTransform(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function renderTransform(data) {
    const repro = data.repro || {};
    const slides = data.slides || [];
    const quiz = data.quiz || [];
    el.panel.innerHTML = `
      <section class="paper-head">
        <div class="paper-head-top">
          <div>
            <div class="eyebrow">论文 → 可执行成果</div>
            <h2 class="paper-title" style="font-size:16px">${esc(data.paper.title)}</h2>
            <div class="paper-meta">${badge(data.source === "llm" ? "模型生成" : "内置脚手架", "soft")}${badge("人工验证后使用", "amber")}</div>
          </div>
          <div class="paper-actions">
            <button class="btn btn-small" type="button" id="transformToGaps">继续找空白</button>
          </div>
        </div>
        <div class="paper-tldr">${esc(data.disclaimer || "")}</div>
      </section>

      <div class="transform-grid">
        <div class="transform-card wide">
          <h4>复现计划</h4>
          <div class="two-col-inline" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div>
              <div class="section-note">环境</div>
              <ul class="step-list">${(repro.environment || []).map((item) => `<li>${esc(item)}</li>`).join("") || `<li>待补充</li>`}</ul>
              <div class="section-note" style="margin-top:8px">最小验证实验</div>
              <p class="card-sub">${esc(repro.minimal_experiment || "待补充")}</p>
            </div>
            <div>
              <div class="section-note">步骤</div>
              <ol class="step-list">${(repro.steps || []).map((item) => `<li>${esc(item)}</li>`).join("") || `<li>待补充</li>`}</ol>
            </div>
          </div>
          <div class="section-note" style="margin-top:8px">风险</div>
          <div class="chips">${(repro.risks || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div>
        </div>

        <div class="transform-card wide">
          <h4>代码骨架 <span class="badge badge-amber">非自动复现</span></h4>
          <div class="code-box">
            <pre class="code" id="codeBlock">${esc(data.code_scaffold || "暂无代码骨架。")}</pre>
            <button class="btn btn-small copy-btn" type="button" id="copyCode">复制</button>
          </div>
        </div>

        <div class="transform-card">
          <h4>组会 Slides 提纲</h4>
          <ol class="step-list">${slides.map((item) => `<li>${esc(item)}</li>`).join("") || `<li>待生成</li>`}</ol>
        </div>

        <div class="transform-card">
          <h4>自测 Quiz</h4>
          ${quiz.map((item) => `<div class="quiz-item"><div class="quiz-q">${esc(item.q)}</div><div class="quiz-a">${esc(item.a)}</div></div>`).join("") || `<div class="section-note">暂无。</div>`}
        </div>

        <div class="transform-card wide">
          <h4>课程案例</h4>
          <p class="card-sub">${esc(data.course_case || "待生成")}</p>
        </div>
      </div>
    `;
    const copyButton = $("#copyCode");
    if (copyButton) copyButton.addEventListener("click", () => copyText(data.code_scaffold || ""));
    const gapButton = $("#transformToGaps");
    if (gapButton) gapButton.addEventListener("click", () => switchTab("gaps"));
  }

  async function loadGaps(topic = null) {
    const finalTopic = topic || (state.gaps && state.gaps.topic) || ($("#queryInput")?.value || "").trim() || "面向存算一体的低功耗 ADC 设计";
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在扫描研究空白", "从证据链里找矛盾、缺失消融和未测组合…")}`;
    try {
      const data = await api("/api/gaps", {
        method: "POST",
        body: JSON.stringify({ topic: finalTopic }),
      });
      state.gaps = data;
      renderGaps(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function renderGaps(data) {
    el.panel.innerHTML = `
      <div class="gap-head">
        <input id="gapTopic" value="${esc(data.topic || "")}" aria-label="研究主题" />
        <button class="btn btn-primary" type="button" id="rescanGaps">重新扫描</button>
      </div>
      <div class="warn-box"><strong>使用方式：</strong>${esc(data.note || "")}</div>
      <div class="gap-grid">
        ${(data.gaps || []).map((gap) => `
          <article class="gap-card">
            <div class="gap-title">${esc(gap.title)}</div>
            <div class="gap-question">${esc(gap.question)}</div>
            <div class="gap-why"><strong>为什么是空白：</strong>${esc(gap.why_gap)}</div>
            <div class="chips">${(gap.evidence || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}</div>
            <div class="gap-scores">
              <div class="gap-score"><strong>${esc(gap.novelty)}</strong><span>新颖性</span></div>
              <div class="gap-score"><strong>${esc(gap.feasibility)}</strong><span>可行性</span></div>
              <div class="gap-score"><strong>${esc(gap.impact)}</strong><span>影响</span></div>
            </div>
            <div class="gap-exp"><strong>最小实验：</strong>${esc(gap.experiment)}</div>
          </article>
        `).join("")}
      </div>
      <div class="card" style="margin-top:12px">
        <h3 class="card-title">Idea Critic <span class="badge badge-pink">先挑刺</span></h3>
        <div class="insight-list">${(data.critic || []).map((item) => `<div class="insight-item pink">${esc(item)}</div>`).join("")}</div>
      </div>
    `;
    const button = $("#rescanGaps");
    if (button) button.addEventListener("click", () => {
      const topic = $("#gapTopic").value.trim();
      if (topic) loadGaps(topic);
    });
  }

  async function loadEval() {
    el.panel.innerHTML = `<div class="loading-line"></div>${emptyState("正在加载评测", "读取回归集与目标指标…")}`;
    try {
      const data = await api("/api/eval");
      state.eval = data;
      renderEval(data);
    } catch (error) {
      el.panel.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
      toast(error.message, true);
    }
  }

  function renderEval(data) {
    const summary = data.summary || {};
    const cases = data.cases || [];
    el.panel.innerHTML = `
      <div class="card">
        <h3 class="card-title">评测目标 <span class="badge badge-soft">可复现</span></h3>
        <div class="eval-targets">
          ${(data.targets || []).map((item) => `
            <div class="eval-target">
              <div class="dim">${esc(item.dimension)}</div>
              <div class="metric-name">${esc(item.metric)}</div>
              <div class="target">${esc(item.target)}</div>
            </div>
          `).join("")}
        </div>
        <div class="section-note" style="margin-top:10px">${esc(data.note || "")}</div>
      </div>
      ${summary.accuracy != null ? `
        <div class="card" style="margin-top:12px">
          <h3 class="card-title">最近一次回归结果 <span class="badge badge-green">${esc(summary.accuracy)}%</span></h3>
          <div class="metrics-grid">
            <div class="metric"><div class="metric-key">通过</div><div class="metric-value"><strong>${esc(summary.passed)}</strong> / ${esc(summary.total)}</div></div>
            <div class="metric"><div class="metric-key">运行时间</div><div class="metric-value">${esc(summary.run_at || "—")}</div></div>
          </div>
        </div>
      ` : ""}
      <div class="card" style="margin-top:12px">
        <h3 class="card-title">回归用例 <span class="badge badge-muted">${cases.length} 条</span></h3>
        <div class="case-list">
          ${cases.map((item) => {
            const result = summary.cases ? summary.cases.find((entry) => entry.id === item.id) : null;
            const pass = result ? result.passed : null;
            return `<div class="case-item">
              <span class="case-id">${esc(item.id)}</span>
              <span style="flex:1">${esc(item.dimension)} · ${esc(item.check)}</span>
              ${pass === true ? `<span class="case-pass">PASS</span>` : pass === false ? `<span class="case-fail">FAIL</span>` : ""}
            </div>`;
          }).join("")}
        </div>
      </div>
    `;
  }

  function openPageViewer(paperId, totalPages, startPage = 1, quote = "") {
    const total = Math.max(1, Number(totalPages) || 1);
    let page = Math.min(total, Math.max(1, Number(startPage) || 1));
    const render = () => {
      el.modalTitle.textContent = `原文页 · p.${page}/${total}`;
      el.modalBody.innerHTML = `
        <div class="page-viewer">
          <div class="page-viewer-toolbar">
            <button class="btn btn-small btn-ghost" type="button" data-page-prev ${page <= 1 ? "disabled" : ""}>上一页</button>
            <span class="mono">p.${page} / ${total}</span>
            <button class="btn btn-small btn-ghost" type="button" data-page-next ${page >= total ? "disabled" : ""}>下一页</button>
            <span class="badge badge-soft" data-page-locate>${quote ? "正在定位证据…" : "原文页"}</span>
          </div>
          <div class="page-viewer-stage">
            <div class="page-viewer-canvas" data-page-stage>
              <img src="/api/paper/${encodeURIComponent(paperId)}/pages/${page}" alt="论文第 ${page} 页原文" />
              <div class="page-highlight-layer" data-page-highlights></div>
            </div>
          </div>
          <div class="section-note">图片来自本地 PDF，仅用于对照原文；高亮依赖 PDF 文本层，扫描件/OCR 页不会出现词级高亮。</div>
        </div>`;
      const image = el.modalBody.querySelector("[data-page-stage] img");
      if (image) image.addEventListener("error", () => {
        const stage = el.modalBody.querySelector("[data-page-stage]");
        if (stage) stage.innerHTML = `<div class="warn-box">该页无法预览：论文没有本地 PDF 或页码超出范围。</div>`;
      });
      const prev = el.modalBody.querySelector("[data-page-prev]");
      const next = el.modalBody.querySelector("[data-page-next]");
      if (prev) prev.addEventListener("click", () => { if (page > 1) { page -= 1; render(); } });
      if (next) next.addEventListener("click", () => { if (page < total) { page += 1; render(); } });
      const badge = el.modalBody.querySelector("[data-page-locate]");
      const layer = el.modalBody.querySelector("[data-page-highlights]");
      if (!quote || !layer || !badge) return;
      fetch(`/api/paper/${encodeURIComponent(paperId)}/locate?page=${page}&quote=${encodeURIComponent(quote)}`)
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("定位失败")))
        .then((data) => {
          const rects = data.rects || [];
          layer.innerHTML = rects.map((rect) => `<span class="page-highlight" style="left:${rect.x0 * 100}%;top:${rect.y0 * 100}%;width:${(rect.x1 - rect.x0) * 100}%;height:${(rect.y1 - rect.y0) * 100}%"></span>`).join("");
          badge.textContent = rects.length ? `已定位证据 · ${rects.length} 处` : "未定位到文本层";
          badge.className = `badge ${rects.length ? "badge-green" : "badge-amber"}`;
          if (!rects.length && data.note) badge.title = data.note;
        })
        .catch(() => {
          badge.textContent = "未定位到文本层";
          badge.className = "badge badge-amber";
        });
    };
    openModal("原文页", "");
    render();
  }

  function openModal(title, html) {
    el.modalTitle.textContent = title;
    el.modalBody.innerHTML = html;
    el.modal.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    el.modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板");
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast("已复制到剪贴板");
    }
  }

  async function uploadPdfs(files) {
    const pdfs = Array.from(files || []).filter((file) => file.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) {
      toast("只支持 PDF 文件", true);
      return;
    }
    const batch = pdfs.slice(0, 5).filter((file) => {
      if (file.size > 25 * 1024 * 1024) {
        toast(`${file.name} 超过 25 MB，已跳过`, true);
        return false;
      }
      return true;
    });
    if (pdfs.length > 5) toast("单次最多解析 5 篇，其余已跳过", true);
    if (!batch.length) {
      toast("没有可解析的 PDF", true);
      return;
    }
    setLoadingText(el.uploadBtn, `解析 0/${batch.length}…`);
    const added = [];
    let completed = 0;
    try {
      const results = new Array(batch.length).fill(null);
      const workers = Array.from({ length: Math.min(3, batch.length) }, (_, workerIndex) => (async () => {
        for (let index = workerIndex; index < batch.length; index += 3) {
          const file = batch[index];
          const form = new FormData();
          form.append("file", file);
          try {
            const response = await fetch("/api/upload", { method: "POST", body: form });
            let data = null;
            try { data = await response.json(); } catch (_) { data = null; }
            if (!response.ok) {
              toast(`${file.name}：${(data && data.detail) || "解析失败"}`, true);
            } else {
              results[index] = data;
            }
          } catch (error) {
            toast(`${file.name}：${error.message}`, true);
          } finally {
            completed += 1;
            el.uploadBtn.textContent = `解析 ${completed}/${batch.length}…`;
          }
        }
      })());
      await Promise.all(workers);
      for (const data of results) {
        if (!data) continue;
        added.push(data);
        state.papers = [data.paper, ...state.papers.filter((paper) => paper.id !== data.paper.id)];
      }
      if (!added.length) {
        toast("没有成功解析的 PDF", true);
        return;
      }
      state.selected.clear();
      el.selectAll.checked = false;
      updateCompareCount();
      renderResults();
      const latest = added[added.length - 1];
      const ocrNote = latest.ocr_used
        ? ` · OCR ${latest.ocr_pages} 页${latest.ocr_truncated ? "（其余页未识别）" : ""}`
        : "";
      el.resultMeta.textContent = `已解析 ${added.length} 篇 PDF · 最近 ${latest.pages} 页 / ${latest.sections} 个章节${ocrNote}`;
      await openPaper(latest.paper_id, true);
      toast(`已解析 ${added.length} 篇 PDF${ocrNote}，可勾选进行对比`);
      if (latest.ocr_warning) toast(latest.ocr_warning, true);
    } catch (error) {
      toast(error.message, true);
    } finally {
      restoreButton(el.uploadBtn);
    }
  }

  function markdownTable(rows, columns) {
    const cols = columns && columns.length ? columns : LEGACY_COMPARE_COLUMNS;
    const header = "| " + cols.map((column) => column.label || column.key).join(" | ") + " |";
    const divider = "|" + cols.map(() => "---").join("|") + "|";
    const body = rows.map((row) => cols.map((column) => {
      const value = column.key === "title" ? row.title : compareValue(row, column);
      return String(value || "").replace(/\|/g, "\\|").replace(/\n/g, " ");
    }).join(" | ")).map((line) => `| ${line} |`).join("\n");
    return `${header}\n${divider}\n${body}`;
  }

  function exportMarkdown() {
    let content = "";
    const date = new Date().toISOString().slice(0, 10);
    if (state.tab === "compare" && state.compare) {
      content = `# PaperPilot 论文对比\n\n> 导出日期：${date}\n\n`;
      content += markdownTable(state.compare.rows || [], state.compare.columns || []);
      const pairs = state.compare.comparability || [];
      if (pairs.length) {
        content += "\n\n## 可比性判定\n\n" + pairs.map((pair) => {
          const titles = (pair.titles || pair.paper_ids || []).join(" ↔ ");
          const differences = (pair.differences || []).length ? `；差异：${pair.differences.join("；")}` : "";
          return `- ${pair.comparable ? "可比" : "不可直接比较"}：${titles} — ${pair.reason || ""}${differences}`;
        }).join("\n");
      }
      content += "\n\n## 可比性提醒\n\n" + (state.compare.warnings || []).map((item) => `- ${item}`).join("\n");
      content += "\n\n## 共识\n\n" + (state.compare.consensus || []).map((item) => `- ${item}`).join("\n");
      content += "\n\n## 分歧\n\n" + (state.compare.divergence || []).map((item) => `- ${item}`).join("\n");
    } else if (state.tab === "innovate" && state.innovate) {
      content = `# ${state.innovate.paper.title}\n\n## 作者声称\n\n${(state.innovate.claims || []).map((item) => `- ${item}`).join("\n")}`;
      content += `\n\n## 实验证据\n\n${(state.innovate.evidence || []).map((item) => `- ${item.quote}（${item.section || "原文"} p.${item.page || "—"}）`).join("\n")}`;
      content += `\n\n## 模型推断\n\n${(state.innovate.inferences || []).map((item) => `- ${item}`).join("\n")}`;
    } else if (state.tab === "transform" && state.transform) {
      content = `# ${state.transform.paper.title} · 复现与教学转化\n\n${state.transform.disclaimer}\n\n## 复现步骤\n\n${(state.transform.repro.steps || []).map((item, index) => `${index + 1}. ${item}`).join("\n")}\n\n## 代码骨架\n\n\`\`\`python\n${state.transform.code_scaffold}\n\`\`\``;
      content += `\n\n## 组会提纲\n\n${(state.transform.slides || []).map((item) => `- ${item}`).join("\n")}`;
    } else if (state.tab === "topic" && state.topic) {
      content = topicMarkdown(state.topic);
    } else if (state.tab === "writing") {
      if (state.draft) content = state.draft.markdown || "";
      else if (state.check) content = checkMarkdown(state.check);
      else {
        toast("先生成草稿或完成核查", true);
        return;
      }
    } else if (state.tab === "ask" && state.ask) {
      content = `# 证据问答：${state.ask.question}\n\n${state.ask.answer}\n\n## 引用\n\n`;
      content += (state.ask.citations || []).map((item) => `${item.index}. ${item.title}（${item.section || "原文"} · p.${item.page || "—"}）\n\n   > ${item.quote}`).join("\n\n");
      content += `\n\n---\n\n范围：${state.ask.scope_label} · 检索 ${state.ask.papers_considered} 篇论文 / ${state.ask.passages_searched} 个片段`;
    } else if (state.tab === "library") {
      const link = document.createElement("a");
      link.href = "/api/library/export";
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast("研究库 Markdown 已导出");
      return;
    } else if (state.tab === "gaps" && state.gaps) {
      content = `# 研究空白雷达\n\n主题：${state.gaps.topic}\n\n${(state.gaps.gaps || []).map((gap) => `## ${gap.title}\n\n- 问题：${gap.question}\n- 为什么是空白：${gap.why_gap}\n- 评分：新颖性 ${gap.novelty} / 可行性 ${gap.feasibility} / 影响 ${gap.impact}\n- 最小实验：${gap.experiment}`).join("\n\n")}`;
    } else if (state.read) {
      const data = state.read;
      content = `# ${data.paper.title}\n\n> ${data.paper.venue} ${data.paper.year}\n\n## TL;DR\n\n${data.tldr}\n\n## 问题\n\n${data.problem}\n\n## 方法\n\n${data.method}`;
      content += `\n\n## 创新点\n\n${(data.novelty || []).map((item) => `- ${item}`).join("\n")}`;
      content += `\n\n## 局限\n\n${(data.limitations || []).map((item) => `- ${item}`).join("\n")}`;
      content += `\n\n## 证据\n\n${(data.evidence || []).map((item) => `- “${item.quote}”（${item.section || "原文"} p.${item.page || "—"}）`).join("\n")}`;
    } else {
      toast("当前没有可导出的内容", true);
      return;
    }
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `paperpilot-${state.tab}-${date}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("Markdown 已导出");
  }

  window.PaperPilot = {
    api,
    esc,
    state,
    switchTab,
    toast,
    openAgentRun,
    renderAgentRun,
    startAgentPolling,
    stopAgentPolling,
    addPaperToProject,
    pickProject,
    panel: el.panel,
  };

  init();
})();
