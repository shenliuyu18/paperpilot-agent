from __future__ import annotations

import unittest
from pathlib import Path

from app import config


ROOT = Path(__file__).resolve().parent.parent


def function_body(source: str, signature: str) -> str:
    """Return the source of a top-level function (2-space indented inside the IIFE)."""
    start = source.index(signature)
    candidates = [
        pos
        for pos in (
            source.find("\n  function ", start + len(signature)),
            source.find("\n  async function ", start + len(signature)),
        )
        if pos != -1
    ]
    end = min(candidates) if candidates else len(source)
    return source[start:end]


class FrontendContractTests(unittest.TestCase):
    def test_index_loads_v3_assets_and_project_tab(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="projects"', html)
        self.assertIn("/static/assistant.js?v=8.2.0", html)
        self.assertIn("/static/assistant.css?v=8.2.0", html)
        self.assertIn("/static/projects.js?v=8.2.0", html)
        self.assertIn("/static/agent-live.js?v=8.2.0", html)
        self.assertIn(f"PaperPilot {config.APP_VERSION}", html)

    def test_new_frontend_modules_expose_namespaces(self) -> None:
        for filename, namespace in [
            ("projects.js", "PaperPilotProjects"),
            ("draft.js", "PaperPilotDraft"),
            ("agent-live.js", "PaperPilotLive"),
        ]:
            content = (ROOT / "static" / filename).read_text(encoding="utf-8")
            self.assertIn(f"window.{namespace}", content)

    def test_draft_module_has_studio_contract(self) -> None:
        content = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        self.assertIn("PaperPilotDraft", content)
        self.assertIn("async function mount(container, projectId)", content)
        self.assertIn("window.PaperPilotDraft = { mount, focus, flush", content)
        self.assertIn("data-draft-save", content)
        self.assertIn("data-draft-export", content)
        self.assertIn("data-draft-audit", content)
        self.assertIn("data-draft-version", content)
        self.assertIn("data-draft-add-section", content)
        self.assertIn("data-draft-add-block", content)
        self.assertIn("data-draft-block-delete", content)
        self.assertIn("data-draft-evidence", content)
        self.assertIn("data-draft-cite", content)
        self.assertIn("data-draft-restore", content)
        index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("draft.js?v=8.2.0", index)
        self.assertLess(
            index.index("draft.js?v=8.2.0"),
            index.index("agent-live.js?v=8.2.0"),
        )

    def test_draft_module_wires_revision_conflict_and_draft_api(self) -> None:
        content = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        for endpoint in (
            '"/draft"',
            '"/draft/citations"',
            '"/draft/versions"',
            '"/draft/audit"',
            '"/draft/export?format=',
        ):
            self.assertIn(endpoint, content)
        self.assertIn("encodeURIComponent", content)
        self.assertIn("revision: state.draft.revision", content)
        self.assertRegex(content, r"error\.status === 409")
        self.assertIn("window.PaperPilotDraft", content)
        self.assertIn("window.PaperPilot.api", content)
        self.assertIn("window.PaperPilot.esc", content)
        self.assertIn("window.PaperPilot.toast", content)

    def test_dirty_draft_is_flushed_before_the_view_is_rebuilt(self) -> None:
        draft = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        projects = (ROOT / "static" / "projects.js").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        # The workbench exports a flush the project view calls before it
        # rebuilds itself.
        self.assertIn("async function flush()", draft)
        self.assertIn("window.PaperPilotDraft.flush()", projects)
        self.assertIn("window.PaperPilotDraft.flush()", app)
        # The remount path saves before it clears the local draft.
        self.assertIn("if (state.dirty && !(await flush())) return;", draft)
        # Leaving the page with unsaved text warns instead of dropping it.
        self.assertIn('window.addEventListener("beforeunload"', draft)
        # A failed save has to surface; the text must not disappear quietly.
        self.assertIn("window.confirm", draft)
        self.assertIn("state.saveError", draft)
        # A revision conflict keeps the local body instead of reloading the
        # server copy over it.
        self.assertIn("bodyFingerprint", draft)
        self.assertIn("adoptServerRevision", draft)

    def test_app_exposes_project_route(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"projects"', app)
        self.assertIn("window.PaperPilot", app)

    def test_project_tabs_include_assistant_and_mount(self) -> None:
        projects = (ROOT / "static" / "projects.js").read_text(encoding="utf-8")
        self.assertIn('["assistant", "Copilot"]', projects)
        self.assertIn("renderAssistant", projects)
        self.assertIn("window.PaperPilotAssistant.mount", projects)
        self.assertIn("focusCitation", projects)
        self.assertIn("handleAssistantAction", projects)

    def test_assistant_module_contract(self) -> None:
        assistant = (ROOT / "static" / "assistant.js").read_text(encoding="utf-8")
        self.assertIn("window.PaperPilotAssistant", assistant)
        self.assertIn("async function mount(container, projectId)", assistant)
        self.assertIn("window.PaperPilotAssistant = { mount }", assistant)
        for marker in (
            "data-assistant-form",
            "data-assistant-input",
            "data-assistant-send",
            "data-assistant-msg",
            "data-assistant-cite",
            "data-assistant-action",
            "data-assistant-export",
            "data-assistant-clear",
            "data-assistant-mode",
            "data-assistant-insufficient",
        ):
            self.assertIn(marker, assistant)
        for endpoint in ("/assistant", "/assistant/export"):
            self.assertIn(endpoint, assistant)
        self.assertIn("window.PaperPilotProjects?.focusCitation", assistant)
        self.assertIn("window.PaperPilotProjects?.handleAssistantAction", assistant)

    def test_copilot_is_in_main_navigation(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="copilot"', html)
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('tab === "copilot"', app)
        self.assertIn("openCopilot", app)
        projects = (ROOT / "static" / "projects.js").read_text(encoding="utf-8")
        self.assertIn("async function openCopilot", projects)
        self.assertIn("data-project-copilot", projects)

    def test_compare_columns_are_dynamic(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("compareColumns", app)
        self.assertIn("row.values", app)
        self.assertIn("state.compare.columns", app)
        self.assertNotIn("<th>工艺 / 验证</th><th>ADC 方案</th>", app)

    def test_concept_graph_tab_and_render_contract(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="graph"', html)
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for marker in (
            "/api/graph",
            "function loadGraph",
            "function renderGraph",
            "function graphLayout",
            "data-graph-node",
            "data-graph-type",
            "paperpilot-graph-",
        ):
            self.assertIn(marker, app)
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".graph-layout", styles)
        self.assertIn(".graph-svg", styles)

    def test_compare_renders_comparability_and_export(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render = function_body(app, "function renderCompare(data)")
        self.assertIn("comparability", render)
        self.assertIn("可比性判定", render)
        self.assertIn("不可直接比较", render)
        export = function_body(app, "function exportMarkdown()")
        self.assertIn("## 可比性判定", export)

    def test_draft_semantic_audit_contract(self) -> None:
        draft = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        self.assertIn("data-draft-semantic", draft)
        self.assertIn("data-draft-semantic-block", draft)
        self.assertIn("/draft/semantic-audit", draft)
        self.assertIn("window.PaperPilotDraft = { mount, focus, flush", draft)

    def test_leaving_run_detail_tears_down_live_stream(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.PaperPilotLive?.disconnect()", app)

        # 返回任务列表
        back = function_body(app, "function handleAgentClick(event)")
        self.assertRegex(
            back,
            r"if \(back\) \{[\s\S]*?state\.agentRun = null;[\s\S]*?window\.PaperPilotLive\?\.disconnect\(\);[\s\S]*?stopAgentPolling\(\);",
        )

        # delete run
        delete_run = function_body(app, "async function deleteAgentRun(runId)")
        self.assertRegex(
            delete_run,
            r"state\.agentRun = null;[\s\S]*?window\.PaperPilotLive\?\.disconnect\(\);[\s\S]*?stopAgentPolling\(\);",
        )

        # loadAgent: the run vanished on the server
        load_agent = function_body(app, "async function loadAgent()")
        self.assertRegex(
            load_agent,
            r"catch \(_\) \{\s*state\.agentRun = null;\s*window\.PaperPilotLive\?\.disconnect\(\);\s*stopAgentPolling\(\);",
        )

        # tab switch
        switch_tab = function_body(app, "async function switchTab(tab)")
        self.assertIn('if (tab !== "agent") stopAgentPolling();', switch_tab)
        self.assertIn('if (tab !== "agent") window.PaperPilotLive?.disconnect();', switch_tab)

    def test_snapshot_handler_requires_current_run_id(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render = function_body(app, "function renderAgentRun(run)")
        self.assertIn("snapshot.id === state.agentRun.id", render)
        self.assertIn('state.tab === "agent" && Boolean(state.agentRun)', render)
        # Guard runs before the fetch and again after it resolves.
        self.assertGreaterEqual(render.count("isCurrentRun()"), 2)
        self.assertLess(render.index("isCurrentRun()"), render.index("await api("))
        self.assertGreater(render.rindex("isCurrentRun()"), render.index("await api("))

    def test_index_loads_manuscript_panel(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/manuscript.js?v=8.2.0", html)
        self.assertIn("/static/submission.js?v=8.2.0", html)
        self.assertIn("/static/submission.css?v=8.2.0", html)
        self.assertIn("/static/manuscript.css?v=8.2.0", html)

    def test_manuscript_module_exposes_drawer_contract(self) -> None:
        js = (ROOT / "static" / "manuscript.js").read_text(encoding="utf-8")
        self.assertIn("window.PaperPilotManuscript = { open, close }", js)
        for hook in ("data-manuscript-drawer", "data-manuscript-field", "data-manuscript-save",
                     "data-manuscript-export", "data-manuscript-preflight", "data-manuscript-refs",
                     "data-manuscript-add-author"):
            self.assertIn(hook, js, hook)
        # Escaping must stay in place: every interpolation of server data goes through esc().
        self.assertIn("const esc = (value)", js)
        self.assertIn("esc(meta.title", js)
        self.assertIn("esc(row.text)", js)

    def test_draft_toolbar_opens_manuscript_and_exports_docx(self) -> None:
        js = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        self.assertIn("data-manuscript-open", js)
        self.assertIn('data-draft-export="docx"', js)
        self.assertIn("window.PaperPilotManuscript?.open(state.projectId)", js)
        self.assertIn("gb7714", js)
        self.assertIn("apa", js)
        self.assertIn("ieee", js)

    def test_live_module_keeps_one_source_and_closes_on_run_change(self) -> None:
        live = (ROOT / "static" / "agent-live.js").read_text(encoding="utf-8")
        self.assertEqual(live.count("new EventSource("), 1)
        connect = function_body(live, "function connect(run, callbacks)")
        self.assertLess(connect.index("disconnect();"), connect.index("new EventSource("))
        mount = function_body(live, "function mount(panel, run, callbacks = {})")
        self.assertRegex(mount, r"if \(terminal\) \{\s*disconnect\(\);\s*return;\s*\}")
        self.assertIn("if (currentRunId === run.id) return;", mount)

    def test_model_drawer_is_mounted_in_the_topbar(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/model.js?v=8.2.0", html)
        self.assertIn("/static/model.css?v=8.2.0", html)
        self.assertIn('id="modelBtn"', html)
        self.assertIn('id="modelCallout"', html)
        self.assertLess(
            html.index('id="modelBtn"'),
            html.index('id="uploadBtn"'),
        )
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('["#modelBtn", "#modelSideBtn", "#modelCalloutBtn"]', app)
        self.assertIn("window.PaperPilotModel.open()", app)
        self.assertIn('window.addEventListener("paperpilot:llm-changed"', app)

    def test_model_module_exposes_drawer_contract(self) -> None:
        js = (ROOT / "static" / "model.js").read_text(encoding="utf-8")
        self.assertIn("window.PaperPilotModel = { open, close }", js)
        for hook in ("data-model-drawer", "data-model-provider", "data-model-field",
                     "data-model-flag", "data-model-route", "data-model-test", "data-model-save",
                     "data-model-clear", "data-model-status", "data-model-close"):
            self.assertIn(hook, js, hook)
        for endpoint in ("/api/llm/settings", '"/api/llm/settings/test"', "/api/llm/settings/clear"):
            self.assertIn(endpoint, js, endpoint)
        self.assertIn('window.dispatchEvent(new CustomEvent("paperpilot:llm-changed"))', js)
        # /api/llm/settings returns {settings, calls}; the stats must read the sibling.
        self.assertIn("state.calls = data.calls", js)
        self.assertIn("renderCalls(state.calls)", js)
        # The key is a write-only field: never render a stored key back into the DOM.
        self.assertIn('data-model-field="api_key" type="password"', js)
        self.assertNotIn('data-model-field="api_key" value=', js)

    def test_model_drawer_escapes_server_strings(self) -> None:
        js = (ROOT / "static" / "model.js").read_text(encoding="utf-8")
        self.assertIn("const esc = (value)", js)
        self.assertIn("esc(settings.masked_key", js)
        self.assertIn("esc(item.label)", js)
        self.assertIn("esc(preset.note", js)
        self.assertIn('.test(preset.key_url', js)

    def test_slash_shortcut_does_not_steal_keystrokes(self) -> None:
        """v8.0.0: base URLs, API keys and org/model names all need "/" while typing."""
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function isTextEntry(target)", app)
        self.assertIn("function drawerOpen()", app)
        self.assertIn('event.key === "/" && !isTextEntry(event.target) && !drawerOpen()', app)
        self.assertNotIn("document.activeElement !== el.queryInput", app)
        model = (ROOT / "static" / "model.js").read_text(encoding="utf-8")
        self.assertIn('state.form.base_url = preset.base_url || ""', model)
        self.assertIn('state.form.model = preset.default_model || (preset.models || [])[0] || ""', model)


    def test_pdf_page_viewer_contract(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-read-action="pages"', app)
        self.assertIn("openPageViewer", app)
        self.assertIn("/pages/${page}", app)
        self.assertIn("data-page-prev", app)
        self.assertIn("data-page-next", app)
        self.assertIn("data-open-page", app)
        self.assertIn("data-open-quote", app)
        self.assertIn("/locate?page=", app)
        self.assertIn("page-highlight", app)
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".page-viewer-stage", css)
        self.assertIn(".page-viewer-toolbar", css)
        self.assertIn(".page-highlight", css)

    def test_citation_pool_can_be_filled_and_pruned(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function addPaperToProject", app)
        self.assertIn("data-add-project", app)
        self.assertIn("data-library-add-project", app)
        self.assertIn('data-read-action="add-project"', app)
        self.assertIn("/papers", app)
        self.assertIn("addPaperToProject,", app)

        projects = (ROOT / "static" / "projects.js").read_text(encoding="utf-8")
        self.assertIn('["papers", "引用池"]', projects)
        self.assertIn("data-paper-remove", projects)
        self.assertIn("kind=papers", projects)

        draft = (ROOT / "static" / "draft.js").read_text(encoding="utf-8")
        self.assertIn("data-draft-add-paper", draft)
        self.assertIn('projectPath("/papers")', draft)
        self.assertIn("/api/paper-candidates", draft)
        # The old copy promised an "import papers" action that never existed.
        self.assertNotIn("启动运行或导入论文后即可插入引用", draft)

    def test_dossier_tab_and_module_contract(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="dossier"', html)
        self.assertIn("研究档案", html)
        self.assertIn("/static/dossier.js", html)
        self.assertIn("/static/dossier.css", html)
        self.assertLess(html.index("dossier.js"), html.index("app.js"))
        self.assertIn('tab === "dossier"', (ROOT / "static" / "app.js").read_text(encoding="utf-8"))
        js = (ROOT / "static" / "dossier.js").read_text(encoding="utf-8")
        for hook in (
            "data-dossier-project", "data-dossier-export", "data-dossier-file",
            "data-dossier-verify-preview", "data-dossier-import", "data-dossier-result",
        ):
            self.assertIn(hook, js, hook)
        for endpoint in ("/dossier", "/api/dossiers/verify", "/api/dossiers/import"):
            self.assertIn(endpoint, js, endpoint)
        self.assertIn("window.PaperPilotDossier = { render }", js)
        self.assertIn("FormData", js)
        self.assertNotIn("innerHTML = preview.root_sha256", js)
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"dossier"', app)
        self.assertIn('"copilot"', app)


    def test_aggregate_search_shows_each_source_outcome(self) -> None:
        """A failed source must be visible, not silently merged away."""
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sourceNotice"', html)
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderSourceNotice(", app)
        self.assertIn("renderSourceNotice(data.source_status)", app)
        self.assertIn('badge("回退演示语料"', app)
        search = (ROOT / "app" / "services" / "search.py").read_text(encoding="utf-8")
        self.assertIn("def search_auto_report(", search)
        self.assertIn("def search_with_report(", search)


if __name__ == "__main__":
    unittest.main()
