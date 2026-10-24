(() => {
  "use strict";

  const state = {
    projectId: "",
    messages: [],
    mode: "offline",
    suggestions: [],
    busy: false,
  };
  let bound = null;

  function api(path, options = {}) {
    return window.PaperPilot.api(path, options);
  }
  function esc(value) {
    return window.PaperPilot.esc(value);
  }
  function toast(message, isError = false) {
    window.PaperPilot.toast(message, isError);
  }

  const KIND_LABELS = {
    project: "项目",
    evidence: "证据",
    paper: "论文",
    question: "问题",
    decision: "决策",
    draft_block: "草稿",
    run: "运行",
  };

  function modeLabel(mode) {
    return mode === "online" ? "模型增强模式" : "离线规则模式";
  }

  function citationChip(item, position = 0) {
    const number = item.index || position + 1;
    const label = `${KIND_LABELS[item.kind] || "来源"} · ${item.title || item.id || "未命名"}`;
    const locator = item.locator ? ` · ${item.locator}` : "";
    return `
      <button class="assistant-cite" type="button"
        data-assistant-cite
        data-cite-tab="${esc(item.target?.tab || "overview")}"
        data-cite-id="${esc(item.target?.id || item.id || "")}"
        title="${esc(item.quote || label)}">
        <span>[${number}] ${esc(label)}</span>
        <small>${esc(locator)}</small>
      </button>
    `;
  }

  function actionCard(item) {
    return `
      <button class="assistant-action" type="button"
        data-assistant-action
        data-action-type="${esc(item.type || "")}"
        data-action-payload="${esc(JSON.stringify(item.payload || {}))}">
        <strong>${esc(item.label || "建议动作")}</strong>
        <span>${esc(item.reason || "")}</span>
        <em>需确认</em>
      </button>
    `;
  }

  function messageCard(message) {
    const role = message.role === "user" ? "user" : "assistant";
    const citations = (message.citations || []).map(citationChip).join("");
    const grounding = message.grounding || {};
    const badges = [];
    if (role === "assistant" && message.mode === "llm") {
      badges.push('<span class="badge badge-green">模型增强</span>');
    } else if (role === "assistant" && message.mode === "llm_degraded") {
      badges.push('<span class="badge badge-amber">模型降级</span>');
    }
    if (role === "assistant" && grounding.status === "insufficient") {
      badges.push('<span class="badge badge-muted" data-assistant-insufficient>证据不足</span>');
    } else if (role === "assistant" && grounding.status === "partial") {
      badges.push('<span class="badge badge-amber">部分接地</span>');
    } else if (role === "assistant" && grounding.status === "grounded") {
      badges.push('<span class="badge badge-soft">已接地</span>');
    }
    const suggestions = role === "assistant"
      ? (message.suggestions || []).map(actionCard).join("")
      : "";
    return `
      <article class="assistant-msg ${role}" data-assistant-msg="${esc(message.id || "")}">
        <div class="assistant-msg-head">
          <span class="assistant-role">${role === "user" ? "你" : "Copilot"}</span>
          ${badges.join("")}
          <span class="muted small">${esc(message.created_at || "")}</span>
        </div>
        <div class="assistant-text">${esc(message.text || "").replace(/\n/g, "<br />")}</div>
        ${citations ? `<div class="assistant-citations">${citations}</div>` : ""}
        ${suggestions ? `<div class="assistant-actions"><span class="muted small">可执行建议（点击后仍需确认）</span>${suggestions}</div>` : ""}
      </article>
    `;
  }

  function render(container) {
    const messages = state.messages.length
      ? state.messages.map(messageCard).join("")
      : `
        <div class="assistant-empty">
          <h3>从项目资产开始提问</h3>
          <p class="muted">Copilot 只读取当前项目的证据、开放问题、决策、草稿和运行报告；没有证据时会明确说不足。</p>
          <div class="assistant-samples">
            <button type="button" class="link-btn" data-assistant-sample="这个项目下一步该做什么？">这个项目下一步该做什么？</button>
            <button type="button" class="link-btn" data-assistant-sample="有哪些证据支持我的研究问题？">有哪些证据支持我的研究问题？</button>
            <button type="button" class="link-btn" data-assistant-sample="我的草稿哪里有问题？">我的草稿哪里有问题？</button>
          </div>
        </div>
      `;
    const lastAssistant = [...state.messages].reverse().find((message) => message.role === "assistant");
    const suggestions = (lastAssistant?.suggestions || state.suggestions || []).map(actionCard).join("");
    container.innerHTML = `
      <div class="assistant-panel">
        <header class="assistant-head">
          <div>
            <div class="eyebrow">研究 Copilot</div>
            <h3>围绕这个项目提问</h3>
            <p class="muted small">引用只指向项目内资产；建议动作不会自动执行。</p>
          </div>
          <div class="assistant-head-actions">
            <span class="badge badge-soft" data-assistant-mode>${esc(modeLabel(state.mode))}</span>
            <button class="btn btn-ghost btn-small" type="button" data-assistant-export>导出对话</button>
            <button class="btn btn-ghost btn-small" type="button" data-assistant-clear>清空</button>
          </div>
        </header>
        <div class="assistant-stream" data-assistant-stream>
          ${messages}
          ${!state.messages.length && suggestions ? `<div class="assistant-actions"><span class="muted small">项目当前可做的事</span>${suggestions}</div>` : ""}
        </div>
        <form class="assistant-form" data-assistant-form>
          <textarea data-assistant-input rows="3" maxlength="1000"
            placeholder="例如：有哪些证据支持 4.2 fJ 的结论？下一步该补什么？"></textarea>
          <div class="assistant-form-bar">
            <span class="muted small">Enter 发送，Shift+Enter 换行</span>
            <button class="btn btn-primary" type="submit" data-assistant-send>发送</button>
          </div>
        </form>
      </div>
    `;
    const stream = container.querySelector("[data-assistant-stream]");
    if (!stream) return;
    const answers = stream.querySelectorAll(".assistant-msg.assistant");
    const lastAnswer = answers[answers.length - 1];
    if (lastAnswer) {
      // Show the beginning of the newest answer rather than the absolute
      // bottom: a grounded answer is taller than the stream and the user
      // needs its conclusion first.
      const streamRect = stream.getBoundingClientRect();
      const messageRect = lastAnswer.getBoundingClientRect();
      stream.scrollTop += messageRect.top - streamRect.top - 8;
    } else {
      stream.scrollTop = 0;
    }
  }

  async function load(container) {
    const data = await api(`/api/projects/${encodeURIComponent(state.projectId)}/assistant`);
    state.messages = data.messages || [];
    state.mode = data.mode || "offline";
    state.suggestions = data.suggestions || [];
    render(container);
  }

  async function send(container) {
    if (state.busy) return;
    const input = container.querySelector("[data-assistant-input]");
    const message = (input?.value || "").trim();
    if (!message) return;
    state.busy = true;
    const button = container.querySelector("[data-assistant-send]");
    if (button) {
      button.disabled = true;
      button.textContent = "思考中…";
    }
    try {
      await api(`/api/projects/${encodeURIComponent(state.projectId)}/assistant`, {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      if (input) input.value = "";
      await load(container);
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = false;
      const fresh = container.querySelector("[data-assistant-send]");
      if (fresh) {
        fresh.disabled = false;
        fresh.textContent = "发送";
      }
    }
  }

  async function clear(container) {
    if (!window.confirm("清空当前项目的 Copilot 对话？此操作不可撤销。")) return;
    try {
      await api(`/api/projects/${encodeURIComponent(state.projectId)}/assistant`, { method: "DELETE" });
      state.messages = [];
      await load(container);
      toast("对话已清空");
    } catch (error) {
      toast(error.message, true);
    }
  }

  function bind(container) {
    if (bound === container) return;
    bound = container;

    container.addEventListener("submit", (event) => {
      if (!event.target.closest("[data-assistant-form]")) return;
      event.preventDefault();
      send(container).catch((error) => toast(error.message, true));
    });

    container.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const sample = target.closest("[data-assistant-sample]");
      if (sample) {
        const input = container.querySelector("[data-assistant-input]");
        if (input) {
          input.value = sample.dataset.assistantSample || "";
          input.focus();
        }
        return;
      }
      const cite = target.closest("[data-assistant-cite]");
      if (cite) {
        window.PaperPilotProjects?.focusCitation({
          tab: cite.dataset.citeTab || "overview",
          id: cite.dataset.citeId || "",
        });
        return;
      }
      const action = target.closest("[data-assistant-action]");
      if (action) {
        let payload = {};
        try {
          payload = JSON.parse(action.dataset.actionPayload || "{}");
        } catch (_) {
          payload = {};
        }
        const label = action.querySelector("strong")?.textContent || "这个建议动作";
        if (!window.confirm(`确认执行「${label}」？`)) return;
        window.PaperPilotProjects?.handleAssistantAction(
          action.dataset.actionType || "",
          payload,
        ).catch((error) => toast(error.message, true));
        return;
      }
      if (target.closest("[data-assistant-export]")) {
        window.location.href = `/api/projects/${encodeURIComponent(state.projectId)}/assistant/export`;
        return;
      }
      if (target.closest("[data-assistant-clear]")) {
        clear(container).catch((error) => toast(error.message, true));
      }
    });

    container.addEventListener("keydown", (event) => {
      const input = event.target instanceof Element ? event.target.closest("[data-assistant-input]") : null;
      if (!input || event.key !== "Enter" || event.shiftKey) return;
      event.preventDefault();
      input.closest("form")?.requestSubmit();
    });
  }

  async function mount(container, projectId) {
    if (!container || !projectId) return;
    state.projectId = String(projectId);
    state.messages = [];
    state.suggestions = [];
    state.busy = false;
    bound = null;
    container.innerHTML = `<div class="card"><div class="loading-line"></div><p class="muted">正在读取项目资产…</p></div>`;
    bind(container);
    try {
      await load(container);
    } catch (error) {
      container.innerHTML = `<div class="warn-box">${esc(error.message)}</div>`;
    }
  }

  window.PaperPilotAssistant = { mount };
})();
