/* Model settings drawer (v6.2): pick a common LLM, paste your own API key. */
(() => {
  const state = {
    settings: null,
    calls: {},
    form: null,
    result: null,
    error: "",
    busy: false,
    open: false,
  };

  const esc = (value) => window.PaperPilot.esc(value);
  const toast = (message, error = false) => window.PaperPilot.toast(message, error);

  function blankForm(settings) {
    return {
      provider: settings.provider || "openai",
      base_url: settings.base_url || "",
      model: settings.model || "",
      api_key: "",
      clear_key: false,
      embeddings: Boolean(settings.embeddings),
      embedding_model: settings.embedding_model || "",
      timeout: settings.timeout ?? 60,
      retries: settings.retries ?? 1,
      routes: { ...(settings.routes || {}) },
    };
  }

  function presetOf(id) {
    const items = (state.settings && state.settings.providers) || [];
    return items.find((item) => item.id === id) || items[0] || { models: [], base_url: "", label: "" };
  }

  function statusPills() {
    const settings = state.settings || {};
    const pills = [
      settings.enabled
        ? `<span class="ml-pill ml-ok">在线增强</span>`
        : `<span class="ml-pill ml-off">离线规则</span>`,
      `<span class="ml-pill">${esc(settings.provider_label || "未选择")} · ${esc(settings.model || "无模型")}</span>`,
      settings.has_key
        ? `<span class="ml-pill ml-key">密钥 ${esc(settings.masked_key || "已配置（不回显）")}</span>`
        : `<span class="ml-pill">尚未配置密钥</span>`,
    ];
    if (settings.driven_by === "env") pills.push(`<span class="ml-pill">由环境变量提供</span>`);
    if (!settings.persist_keys) pills.push(`<span class="ml-pill ml-warn">仅内存保存</span>`);
    return `<div class="ml-pills">${pills.join("")}</div>`;
  }

  const REASON_LABELS = {
    disabled: "未配置", schema: "格式复核", transport: "网络", auth: "鉴权失败",
    rate_limit: "限流/额度", request: "请求错误", budget: "预算用尽",
  };

  function renderCalls(calls) {
    const summary = calls || {};
    const reasons = Object.entries(summary.degrade_reasons || {})
      .map(([reason, count]) => `${esc(REASON_LABELS[reason] || reason)} × ${esc(count)}`)
      .join(" · ");
    return `
      <div class="ml-stats">
        <span>调用 ${esc(summary.calls || 0)}</span>
        <span>成功 ${esc(summary.ok || 0)}</span>
        <span>失败 ${esc(summary.failed || 0)}</span>
        <span>命中缓存 ${esc(summary.cached || 0)}</span>
        <span>平均 ${esc(summary.avg_latency_ms || 0)} ms</span>
      </div>
      ${reasons ? `<p class="muted small">降级原因：${reasons}</p>` : ""}`;
  }

  function render(container) {
    const settings = state.settings || {};
    const form = state.form || {};
    const preset = presetOf(form.provider);
    const purposes = settings.purposes || [];
    const models = preset.models || [];
    const embedModels = preset.embedding_models || [];
    container.innerHTML = `
      <div class="ml-backdrop" data-model-close></div>
      <aside class="ml-drawer" data-model-drawer role="dialog" aria-label="模型设置" aria-modal="true">
        <header class="ml-head">
          <div>
            <p class="eyebrow">自定义大模型</p>
            <h2>接入你自己的模型</h2>
            <p class="muted small">选择一个常见大模型，粘贴你的 API Key，PaperPilot 立刻从离线规则模式切到模型增强模式。</p>
          </div>
          <button class="btn btn-ghost btn-small" type="button" data-model-close>关闭</button>
        </header>
        <div class="ml-body">
          ${statusPills()}

          <div class="ml-providers">
            ${(settings.providers || []).map((item) => `
              <button class="ml-chip${item.id === form.provider ? " active" : ""}" type="button"
                      data-model-provider="${esc(item.id)}">${esc(item.label)}</button>`).join("")}
          </div>
          <p class="muted small">${esc(preset.note || "")}</p>

          <div class="ml-grid">
            <label class="ml-label">接口地址
              <input class="ml-input" data-model-field="base_url" value="${esc(form.base_url || "")}"
                     list="mlBaseUrlList" placeholder="https://api.openai.com/v1" autocomplete="off" />
              <datalist id="mlBaseUrlList">
                ${(settings.providers || []).filter((item) => item.base_url)
                  .map((item) => `<option value="${esc(item.base_url)}"></option>`).join("")}
              </datalist>
            </label>
            <label class="ml-label">模型名
              <input class="ml-input" data-model-field="model" value="${esc(form.model || "")}"
                     list="mlModelList" placeholder="deepseek-chat" autocomplete="off" />
              <datalist id="mlModelList">${models.map((item) => `<option value="${esc(item)}"></option>`).join("")}</datalist>
            </label>
            <label class="ml-label">API Key
              <input class="ml-input" data-model-field="api_key" type="password" autocomplete="new-password"
                     placeholder="${settings.has_key ? "已保存，留空则保持不变" : "sk-…；只写入当前实例"}" />
            </label>
            <label class="ml-label">超时（秒）
              <input class="ml-input" data-model-field="timeout" type="number" min="1" max="300" step="1"
                     value="${esc(form.timeout ?? 60)}" />
            </label>
            <label class="ml-label">重试次数
              <input class="ml-input" data-model-field="retries" type="number" min="0" max="3" step="1"
                     value="${esc(form.retries ?? 1)}" />
            </label>
            <label class="ml-label">向量模型（可选）
              <input class="ml-input" data-model-field="embedding_model" value="${esc(form.embedding_model || "")}"
                     list="mlEmbedList" placeholder="text-embedding-3-small" autocomplete="off" />
              <datalist id="mlEmbedList">${embedModels.map((item) => `<option value="${esc(item)}"></option>`).join("")}</datalist>
            </label>
          </div>

          <div class="ml-checks">
            <label class="ml-check"><input type="checkbox" data-model-flag="embeddings" ${form.embeddings ? "checked" : ""} />
              启用向量检索增强</label>
            ${settings.has_key ? `<label class="ml-check"><input type="checkbox" data-model-flag="clear_key" /> 清除已保存密钥</label>` : ""}
            ${/^https:\/\//.test(preset.key_url || "")
              ? `<a class="ml-link" href="${esc(preset.key_url)}" target="_blank" rel="noreferrer">去 ${esc(preset.label)} 控制台取 Key</a>` : ""}
          </div>

          <div class="ml-actions">
            <button class="btn btn-primary btn-small" type="button" data-model-test>测试连接</button>
            <button class="btn btn-ghost btn-small" type="button" data-model-save>保存并启用</button>
            <button class="btn btn-ghost btn-small" type="button" data-model-reset>回到表单</button>
            ${settings.has_key || settings.driven_by === "ui"
              ? `<button class="btn btn-ghost btn-small" type="button" data-model-clear>清除配置</button>` : ""}
          </div>
          <div class="ml-status muted small${state.error ? " error" : ""}" data-model-status>${esc(state.error || state.result || "")}</div>

          <section class="ml-section">
            <h3>分用途模型（高级）</h3>
            <p class="muted small">为耗时不同的任务指定不同模型；留空则统一使用上面的默认模型。</p>
            <table class="ml-table">
              <thead><tr><th>用途</th><th>模型名</th></tr></thead>
              <tbody>
                ${purposes.map((item) => `
                  <tr>
                    <td>${esc(item.label)}</td>
                    <td><input class="ml-input" data-model-route="${esc(item.id)}"
                               value="${esc((form.routes || {})[item.id] || "")}"
                               placeholder="${esc(form.model || "默认模型")}" autocomplete="off" /></td>
                  </tr>`).join("")}
              </tbody>
            </table>
          </section>

          <section class="ml-section">
            <h3>调用统计</h3>
            ${renderCalls(state.calls)}
          </section>

          <p class="ml-note">
            密钥只写入当前实例的 <code>data/llm_settings.secrets.json</code>，接口只返回掩码，仓库与交付包都不包含该文件。
            ${settings.persist_keys
              ? "重启后仍然有效；换电脑或重新部署后需要重新填写。"
              : "本实例设置为不落盘：密钥只留在内存，重启即失效，公共演示环境请使用自己的私有实例。"}
            ${settings.allow_private ? "" : "接口地址只允许公网 HTTPS。"}
          </p>
        </div>
      </aside>`;
  }

  function setStatus(container, text, error = false) {
    state.result = error ? "" : text;
    state.error = error ? text : "";
    const node = container.querySelector("[data-model-status]");
    if (!node) return;
    node.textContent = text;
    node.classList.toggle("error", Boolean(error));
  }

  async function load() {
    const data = await window.PaperPilot.api("/api/llm/settings");
    state.settings = data.settings;
    state.calls = data.calls || {};
    state.form = blankForm(data.settings);
    return data;
  }

  async function reload(container) {
    try {
      const data = await load();
      render(container);
      return data;
    } catch (error) {
      toast(error.message, true);
      return null;
    }
  }

  function collect(container) {
    container.querySelectorAll("[data-model-field]").forEach((node) => {
      const key = node.dataset.modelField;
      state.form[key] = node.type === "number" ? Number(node.value || 0) : node.value.trim();
    });
    container.querySelectorAll("[data-model-flag]").forEach((node) => {
      state.form[node.dataset.modelFlag] = node.checked;
    });
    const routes = {};
    container.querySelectorAll("[data-model-route]").forEach((node) => {
      const value = node.value.trim();
      if (value) routes[node.dataset.modelRoute] = value;
    });
    state.form.routes = routes;
    return state.form;
  }

  async function testConnection(container) {
    const form = collect(container);
    setStatus(container, "正在请求一次最小补全…");
    state.busy = true;
    try {
      const result = await window.PaperPilot.api("/api/llm/settings/test", {
        method: "POST",
        body: JSON.stringify({
          provider: form.provider,
          base_url: form.base_url,
          model: form.model,
          api_key: form.api_key || undefined,
        }),
      });
      setStatus(container, `连接成功 · ${result.model} · ${result.latency_ms} ms · 回复「${result.reply}」`);
    } catch (error) {
      setStatus(container, error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function save(container) {
    const form = collect(container);
    if (!form.model) {
      setStatus(container, "先选择或填写模型名。", true);
      return;
    }
    state.busy = true;
    setStatus(container, "保存中…");
    try {
      const data = await window.PaperPilot.api("/api/llm/settings", {
        method: "POST",
        body: JSON.stringify(form),
      });
      await load();
      render(container);
      setStatus(container, data.settings.enabled
        ? `已启用 ${data.settings.provider_label} · ${data.settings.model}`
        : "已保存，但未配置密钥，仍是离线规则模式。");
      window.dispatchEvent(new CustomEvent("paperpilot:llm-changed"));
    } catch (error) {
      setStatus(container, error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function clear(container) {
    if (!window.confirm("清除本机保存的模型配置和密钥？环境变量提供的配置会恢复。")) return;
    setStatus(container, "清除中…");
    try {
      await window.PaperPilot.api("/api/llm/settings/clear", { method: "POST", body: "{}" });
      await load();
      render(container);
      setStatus(container, "已清除，当前为离线规则模式。");
      window.dispatchEvent(new CustomEvent("paperpilot:llm-changed"));
    } catch (error) {
      setStatus(container, error.message, true);
    }
  }

  function pickProvider(container, id) {
    state.form.provider = id;
    const preset = presetOf(id);
    // Start from the chosen provider's own defaults; "custom" has none, so the
    // fields must clear instead of keeping the previous provider's URL.
    state.form.base_url = preset.base_url || "";
    state.form.model = preset.default_model || (preset.models || [])[0] || "";
    state.form.routes = {};
    render(container);
  }

  async function open() {
    if (!window.PaperPilot) return;
    close();
    state.open = true;
    state.error = "";
    state.result = "";
    const container = document.createElement("div");
    container.className = "ml-root";
    container.dataset.modelRoot = "1";
    document.body.appendChild(container);
    container.innerHTML = `<div class="ml-backdrop" data-model-close></div>
      <aside class="ml-drawer"><div class="ml-body muted small">读取模型配置…</div></aside>`;
    container.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.closest("[data-model-close]")) { close(); return; }
      const provider = target.closest("[data-model-provider]");
      if (provider) { pickProvider(container, provider.dataset.modelProvider); return; }
      if (target.closest("[data-model-test]")) { await testConnection(container); return; }
      if (target.closest("[data-model-save]")) { await save(container); return; }
      if (target.closest("[data-model-reset]")) {
        state.form = blankForm(state.settings);
        state.error = "";
        state.result = "";
        render(container);
        return;
      }
      if (target.closest("[data-model-clear]")) { await clear(container); return; }
    });
    container.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (target.matches("[data-model-field]")) {
        const key = target.dataset.modelField;
        state.form[key] = target.type === "number" ? Number(target.value || 0) : target.value.trim();
      } else if (target.matches("[data-model-route]")) {
        state.form.routes[target.dataset.modelRoute] = target.value.trim();
      }
    });
    container.addEventListener("change", (event) => {
      const target = event.target;
      if (target instanceof Element && target.matches("[data-model-flag]")) {
        state.form[target.dataset.modelFlag] = target.checked;
      }
    });
    container.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
    await reload(container);
    return true;
  }

  function close() {
    document.querySelectorAll("[data-model-root]").forEach((node) => node.remove());
    state.open = false;
  }

  window.PaperPilotModel = { open, close };
})();
