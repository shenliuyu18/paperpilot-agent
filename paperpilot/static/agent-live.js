(() => {
  "use strict";

  const TERMINAL = ["completed", "cancelled", "failed", "interrupted"];
  const MODES = ["auto", "checkpoint", "step"];

  let source = null;
  let currentRunId = "";
  let fallbackRunId = "";
  let fallbackUntil = 0;
  const boundPanels = new WeakSet();

  function disconnect() {
    if (source) {
      source.onerror = null;
      source.close();
    }
    source = null;
    currentRunId = "";
  }

  function connected() {
    return Boolean(source);
  }

  function api(path, options = {}) {
    return window.PaperPilot.api(path, options);
  }

  async function control(runId, action, payload = {}) {
    return api(`/api/agent/runs/${encodeURIComponent(runId)}/control`, {
      method: "POST",
      body: JSON.stringify({ action, ...payload }),
    });
  }

  function controlButtons(run) {
    if (TERMINAL.includes(run.status)) return "";
    const pauseResume = run.status === "paused"
      ? `<button class="btn btn-ghost btn-small" type="button" data-live-control="resume">恢复</button>`
      : ["queued", "running"].includes(run.status)
        ? `<button class="btn btn-ghost btn-small" type="button" data-live-control="pause">暂停</button>`
        : "";
    return `
      ${pauseResume}
      <button class="btn btn-ghost btn-small" type="button" data-live-control="replan">重新规划</button>
      <button class="btn btn-ghost btn-small" type="button" data-live-control="set_mode">切换模式</button>
      <button class="btn btn-ghost btn-small" type="button" data-live-control="add_constraint">补充约束</button>
      <button class="btn btn-ghost btn-small" type="button" data-live-control="inject_question">注入问题</button>
      <span class="muted small">控制在当前动作结束后生效</span>
    `;
  }

  function connect(run, callbacks) {
    disconnect();
    currentRunId = run.id;
    source = new EventSource(`/api/agent/runs/${encodeURIComponent(run.id)}/stream`);
    source.addEventListener("snapshot", (event) => {
      if (currentRunId !== run.id) return;
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      const snapshot = payload?.run;
      if (!snapshot) return;
      if (callbacks.onSnapshot) callbacks.onSnapshot(snapshot);
      if (TERMINAL.includes(snapshot.status)) disconnect();
    });
    source.addEventListener("heartbeat", () => {
      if (currentRunId !== run.id) return;
      const label = document.querySelector("#agent-live-bar .live-heartbeat");
      if (label) label.textContent = "心跳正常";
    });
    source.onerror = () => {
      const fallback = callbacks.onFallback;
      fallbackRunId = run.id;
      fallbackUntil = Date.now() + 30000;
      disconnect();
      if (fallback) fallback();
    };
  }

  function mount(panel, run, callbacks = {}) {
    if (!panel || !run) return;
    const bar = panel.querySelector("#agent-live-bar");
    if (!bar) return;
    const terminal = TERMINAL.includes(run.status);
    bar.innerHTML = `
      <span class="live-dot ${terminal ? "off" : "on"}"></span>
      <span class="live-label">${terminal ? "运行已结束" : run.status === "paused" ? "实时连接 · 已暂停" : "实时连接"}</span>
      <span class="live-heartbeat muted small"></span>
      ${controlButtons(run)}
    `;
    if (terminal) {
      disconnect();
      return;
    }
    if (currentRunId === run.id) return;
    if (fallbackRunId === run.id && Date.now() < fallbackUntil) return;
    connect(run, callbacks);
  }

  function promptFor(action, run) {
    if (action === "add_constraint") {
      const constraint = (window.prompt("补充约束（200 字以内）", "") || "").trim();
      return constraint ? { constraint } : null;
    }
    if (action === "inject_question") {
      const question = (window.prompt("注入问题", "") || "").trim();
      return question ? { question, priority: "high" } : null;
    }
    if (action === "set_mode") {
      const mode = (window.prompt("自主程度：auto / checkpoint / step", run.mode || "checkpoint") || "").trim().toLowerCase();
      return MODES.includes(mode) ? { mode } : null;
    }
    return {};
  }

  function controlToast(action) {
    if (action === "pause") return "暂停请求已提交，将在当前动作结束后生效";
    if (action === "resume") return "已请求恢复运行";
    if (action === "replan") return "重新规划已提交，将在当前动作结束后生效";
    if (action === "set_mode") return "模式切换已提交，将在当前动作结束后生效";
    if (action === "add_constraint") return "约束已提交，将在当前动作结束后生效";
    if (action === "inject_question") return "问题已注入，将在当前动作结束后生效";
    return "控制指令已提交";
  }

  function bind(panel) {
    if (!panel || boundPanels.has(panel)) return;
    boundPanels.add(panel);
    panel.addEventListener("click", async (event) => {
      const target = event.target instanceof Element ? event.target.closest("[data-live-control]") : null;
      if (!target) return;
      const run = window.PaperPilot.state.agentRun;
      if (!run || !run.id) return;
      const action = target.dataset.liveControl;
      const payload = promptFor(action, run);
      if (payload === null) return;
      target.disabled = true;
      try {
        const data = await control(run.id, action, payload);
        if (data?.run) {
          window.PaperPilot.state.agentRun = data.run;
          if (TERMINAL.includes(data.run.status)) disconnect();
          window.PaperPilot.renderAgentRun(data.run);
        }
        window.PaperPilot.toast(controlToast(action));
      } catch (error) {
        window.PaperPilot.toast(error.message, true);
      } finally {
        target.disabled = false;
      }
    });
  }

  window.PaperPilotLive = { mount, bind, disconnect, control, connected };
})();
