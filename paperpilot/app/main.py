from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, llm, store
from .demo_data import GLOSSARY
from .schemas import (
    ManuscriptUpdateRequest,
    StyleRequest,
    ContradictionScanRequest,
    ContradictionReviewRequest,
    ReferenceImportRequest,
    ReviewerCommentsRequest,
    SubmissionCheckRequest,
    IndexSyncRequest,
    LLMSettingsRequest,
    LLMTestRequest,
    SubscriptionCreateRequest,
    SubscriptionUpdateRequest,
    AgentActionRequest,
    AgentControlRequest,
    AgentRunRequest,
    ArgumentRequest,
    AskRequest,
    AssistantAskRequest,
    CheckRequest,
    CitationRequest,
    CompareRequest,
    CustomSourceRequest,
    CustomSourceToggleRequest,
    DraftRequest,
    DraftSaveRequest,
    GapRequest,
    GraphRequest,
    LibrarySaveRequest,
    LibraryUpdateRequest,
    OnlineGlossaryRequest,
    PaperRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ReadRequest,
    SearchPlanRequest,
    SearchRequest,
    SemanticAuditRequest,
    TopicRequest,
    TransformRequest,
    TranslateRequest,
    TranslationSectionRequest,
    VersionRequest,
)
from .services import (
    agent_compare,
    agent_runs,
    agent_state,
    agent_stream,
    analysis,
    argument,
    ask,
    assistant_service,
    citations,
    citation_styles,
    concept_graph,
    contradictions,
    dossier,
    references_io,
    reviewer,
    submission,
    manuscript,
    ocr,
    custom_sources,
    draft_semantic,
    draft_service,
    draft_state,
    glossary_store,
    history,
    library,
    llm_gateway,
    llm_settings,
    pdf,
    pdf_export,
    planner,
    project_service,
    paper_index,
    project_state,
    subscription_service,
    subscription_state,
    research,
    search,
    translation,
)

app = FastAPI(
    title="PaperPilot",
    description="从搜到论文，到读懂、比清、做出来的科研学习智能体。",
    version=config.APP_VERSION,
)
store.restore_uploads()
llm_settings.apply()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1200)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=86400")
    elif request.url.path == "/":
        response.headers.setdefault("Cache-Control", "no-cache")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Allow ModelScope to embed the space in its console while blocking arbitrary
    # third-party framing. X-Frame-Options cannot express a host allowlist.
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self' https://modelscope.cn https://*.modelscope.cn",
    )
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
showcase_dir = config.ROOT.parent / "showcase"
if showcase_dir.exists():
    app.mount("/showcase", StaticFiles(directory=str(showcase_dir), html=True), name="showcase")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


def resolve_paper(paper_id: str) -> dict | None:
    paper = search.get_paper(paper_id)
    if paper:
        return paper
    item = library.get(paper_id)
    return library.as_paper(item) if item else None


def resolve_source(source: str) -> tuple[str, str]:
    if source.startswith("custom:"):
        public = custom_sources.get_public(source.split(":", 1)[1])
        if not public:
            raise HTTPException(status_code=400, detail="自定义来源不存在，请先在“来源设置”中创建。")
        if not public.get("enabled", True):
            raise HTTPException(status_code=400, detail="自定义来源已停用，请先在“来源设置”中启用。")
        return source, public["name"]
    if source not in planner.SOURCE_LABELS:
        raise HTTPException(status_code=400, detail=f"不支持的检索来源：{source}")
    return source, planner.SOURCE_LABELS[source]


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": config.APP_VERSION,
        "mode": config.APP_MODE,
        "llm_enabled": config.LLM_ENABLED,
        "library_count": library.stats()["total"],
    }


@app.get("/api/citations/{paper_id}")
def api_citations(paper_id: str, limit: int = 8) -> dict:
    paper = resolve_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    try:
        return citations.citation_network(paper, limit=max(1, min(limit, 20)))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"引用网络获取失败：{exc}") from exc


@app.get("/api/glossary")
def api_glossary(q: str = "", category: str = "") -> dict:
    items = glossary_store.merged_items()
    category_counts = {}
    for item in items:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1
    categories = [
        {"id": name, "label": name, "count": count}
        for name, count in sorted(category_counts.items())
    ]
    needle = q.strip().lower()
    filtered = [
        item for item in items
        if (not needle or needle in item["term"].lower() or needle in item["zh"].lower())
        and (not category or item["category"] == category)
    ]
    filtered.sort(key=lambda item: (item["category"], item["term"].lower()))
    return {
        "total": len(filtered),
        "all_total": len(items),
        "categories": categories,
        "items": filtered,
    }


@app.get("/api/glossary/packs")
def api_glossary_packs() -> dict:
    return glossary_store.list_packs()


@app.post("/api/glossary/packs/import")
async def api_glossary_import(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    license: str = Form(default=""),
    category: str = Form(default=""),
) -> dict:
    content = await file.read()
    try:
        pack = glossary_store.import_text(
            file.filename or "glossary.csv",
            content,
            name=name or (file.filename or "本地术语包"),
            license=license,
            category=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "pack": pack}


@app.post("/api/glossary/packs/online")
def api_glossary_online(request: OnlineGlossaryRequest) -> dict:
    try:
        pack = glossary_store.fetch_online(
            request.preset_id,
            url=request.url,
            name=request.name,
            license=request.license,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"在线术语库下载失败：{exc}") from exc
    return {"ok": True, "pack": pack}


@app.post("/api/glossary/packs/{pack_id}/toggle")
def api_glossary_toggle(pack_id: str, request: CustomSourceToggleRequest) -> dict:
    pack = glossary_store.set_enabled(pack_id, request.enabled)
    if not pack:
        raise HTTPException(status_code=404, detail="术语包不存在。")
    return {"ok": True, "pack": pack}


@app.post("/api/glossary/packs/{pack_id}/delete")
def api_glossary_delete(pack_id: str) -> dict:
    if pack_id == "builtin":
        raise HTTPException(status_code=400, detail="内置术语包不能删除。")
    if not glossary_store.delete_pack(pack_id):
        raise HTTPException(status_code=404, detail="术语包不存在。")
    return {"ok": True, "deleted": pack_id}


@app.get("/api/meta")
def meta() -> dict:
    data = analysis.meta()
    data["glossary"] = [{"term": term, "zh": zh} for term, zh in GLOSSARY.items()]
    data["glossary_count"] = len(glossary_store.merged_items())
    packs = glossary_store.list_packs()
    data["glossary_packs"] = {
        "builtin": packs["builtin"],
        "packs": packs["packs"],
        "presets": packs["presets"],
    }
    data["custom_sources"] = custom_sources.list_sources(include_disabled=False)
    data["library"] = library.stats()
    data["history_count"] = len(history.list_entries(limit=200))
    data["translation"] = translation.provider_status()
    data["llm"] = llm.status()
    data["llm_settings"] = llm_settings.public()
    data["ocr"] = ocr.status()
    data["agent"] = {
        "steps": [dict(item) for item in agent_runs.STEP_DEFS],
        "max_runs": agent_runs.MAX_RUNS,
        "run_count": len(agent_runs.list_runs(limit=50)),
    }
    data["projects"] = {
        "count": len(project_service.list_projects(status="", limit=50)),
        "max": project_state.MAX_PROJECTS,
    }
    data["features"] = ["search", "read", "translate", "citations", "compare", "innovate", "transform", "gaps", "ask", "library", "topic", "draft", "check", "glossary", "planner", "library_health", "argument", "custom_sources", "glossary_packs", "history", "agent_runs", "agent_adaptive", "projects", "project_assistant", "assistant_grounding", "llm_gateway", "llm_audit", "semantic_audit", "claims", "contradictions", "manuscript", "docx", "citation_styles", "preflight", "subscriptions", "incremental_index", "llm_routes", "llm_summary", "pdf", "contradiction_review", "subscription_concurrency", "cross_process_lock", "submission", "reviewer_report", "reference_io", "author_metadata", "reviewer_mapping", "custom_llm", "dossier", "ocr", "pdf_pages", "pdf_evidence"]
    return data


def _project_or_404(project_id: str) -> dict:
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="研究项目不存在。")
    return project


@app.post("/api/projects")
def api_project_create(request: ProjectCreateRequest) -> dict:
    try:
        project = project_service.create_project(
            request.title,
            request.question,
            scope=request.scope,
            constraints=request.constraints,
            success_criteria=request.success_criteria,
            tags=request.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "project": project}


@app.get("/api/projects")
def api_project_list(status: str = "active", limit: int = 20) -> dict:
    return {"projects": project_service.list_projects(status=status, limit=limit)}


@app.get("/api/projects/{project_id}")
def api_project_get(project_id: str) -> dict:
    return {"project": _project_or_404(project_id)}


@app.patch("/api/projects/{project_id}")
def api_project_update(project_id: str, request: ProjectUpdateRequest) -> dict:
    try:
        project = project_service.update_project(
            project_id,
            {
                key: value for key, value in {
                    "title": request.title,
                    "brief": request.brief,
                    "tags": request.tags,
                    "stage": request.stage,
                }.items() if value is not None
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/archive")
def api_project_archive(project_id: str) -> dict:
    try:
        project = project_service.archive_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/restore")
def api_project_restore(project_id: str) -> dict:
    try:
        project = project_service.restore_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    return {"ok": True, "project": project}


@app.get("/api/projects/{project_id}/dossier")
def api_project_dossier(project_id: str) -> Response:
    try:
        blob = dossier.export_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except dossier.DossierError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    filename = f"paperpilot-dossier-{project_id}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/dossiers/verify")
async def api_dossier_verify(file: UploadFile = File(...)) -> dict:
    blob = await file.read(dossier.MAX_ARCHIVE_BYTES + 1)
    try:
        return dossier.verify_archive(blob)
    except dossier.DossierError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.post("/api/dossiers/import")
async def api_dossier_import(file: UploadFile = File(...)) -> dict:
    blob = await file.read(dossier.MAX_ARCHIVE_BYTES + 1)
    try:
        return dossier.import_archive(blob)
    except dossier.DossierError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.delete("/api/projects/{project_id}")
def api_project_delete(project_id: str, keep_runs: bool = True) -> dict:
    try:
        result = project_service.delete_project(project_id, keep_runs=keep_runs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/projects/{project_id}/memory")
def api_project_memory(
    project_id: str,
    kind: str = "evidence",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    _project_or_404(project_id)
    if kind not in {"evidence", "questions", "decisions", "papers"}:
        raise HTTPException(status_code=422, detail="未知记忆类型。")
    memory = project_state.read_memory(project_id)
    if kind == "papers":
        items = list((memory.get("papers") or {}).values())
    elif kind == "questions":
        items = list(memory.get("open_questions") or [])
    else:
        items = list(memory.get(kind) or [])
    return {"kind": kind, "total": len(items), "items": items[offset:offset + limit]}


@app.get("/api/projects/{project_id}/runs")
def api_project_runs(project_id: str, limit: int = 20) -> dict:
    project = _project_or_404(project_id)
    data = agent_state.read_store()
    runs = [
        agent_state.summarize_run(data["runs"][run_id])
        for run_id in project.get("run_ids") or []
        if run_id in data.get("runs", {})
    ]
    runs.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return {"runs": runs[:max(1, min(limit, 50))]}


@app.get("/api/projects/{project_id}/assistant")
def api_assistant_get(project_id: str) -> dict:
    _project_or_404(project_id)
    return assistant_service.get_state(project_id)


@app.post("/api/projects/{project_id}/assistant")
def api_assistant_ask(project_id: str, request: AssistantAskRequest) -> dict:
    _project_or_404(project_id)
    try:
        return assistant_service.answer(project_id, request.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc


@app.delete("/api/projects/{project_id}/assistant")
def api_assistant_clear(project_id: str) -> dict:
    _project_or_404(project_id)
    data = assistant_service.clear(project_id)
    return {"ok": True, "assistant": data}


@app.get("/api/projects/{project_id}/assistant/export")
def api_assistant_export(project_id: str) -> Response:
    project = _project_or_404(project_id)
    content = assistant_service.export_markdown(project_id)
    filename = f"{project.get('title') or '研究项目'}-研究助手.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _download_disposition(filename)},
    )


@app.get("/api/projects/{project_id}/compare")
def api_project_compare(project_id: str, run_ids: str = "") -> dict:
    ids = [item.strip() for item in run_ids.split(",") if item.strip()]
    try:
        return agent_compare.compare_runs(project_id, ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目或运行不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/runs/{run_id}/attach")
def api_project_attach_run(project_id: str, run_id: str) -> dict:
    try:
        project = project_service.attach_run(project_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目或运行不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "project": project}


@app.post("/api/projects/{project_id}/runs/{run_id}/detach")
def api_project_detach_run(project_id: str, run_id: str) -> dict:
    try:
        project = project_service.detach_run(project_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在。") from exc
    return {"ok": True, "project": project}


def _download_disposition(filename: str) -> str:
    """Build a Content-Disposition value safe for non-ASCII filenames.

    Response headers are latin-1, so a Chinese project title cannot be sent in
    the plain ``filename`` parameter. RFC 5987's ``filename*`` carries the
    UTF-8 name (percent encoded) while the quoted ``filename`` keeps a purely
    ASCII fallback for older browsers.
    """
    name = str(filename or "").replace("\\", "_").replace('"', "_")
    if name.isascii():
        return f'attachment; filename="{name}"'
    _, dot, extension = name.rpartition(".")
    suffix = (
        f".{extension}"
        if dot and extension.isascii() and extension.isalnum()
        and len(extension) <= 8
        else ""
    )
    return (
        f'attachment; filename="paperpilot-draft{suffix}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )


@app.get("/api/projects/{project_id}/draft")
def api_draft_get(project_id: str) -> dict:
    _project_or_404(project_id)
    return {"draft": draft_service.get_draft(project_id)}


@app.put("/api/projects/{project_id}/draft")
def api_draft_save(project_id: str, request: DraftSaveRequest) -> dict:
    try:
        payload = request.model_dump()
        if "citations" not in request.model_fields_set:
            payload.pop("citations", None)
        draft = draft_service.save_draft(project_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except draft_service.DraftConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except draft_service.DraftValidation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manuscript.update_citation_style(project_id, draft.get("citation_style") or "")
    return {"ok": True, "draft": draft}


@app.post("/api/projects/{project_id}/draft/citations")
def api_draft_citation(project_id: str, request: CitationRequest) -> dict:
    _project_or_404(project_id)
    try:
        return draft_service.add_citation(
            project_id, request.paper_id, request.revision
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="论文不在项目记忆中。"
        ) from exc
    except draft_service.DraftConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/draft/versions")
def api_draft_version(project_id: str, request: VersionRequest) -> dict:
    try:
        version = draft_service.create_version(project_id, request.label)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    return {"ok": True, "version": version}


@app.get("/api/projects/{project_id}/draft/versions")
def api_draft_versions(project_id: str) -> dict:
    _project_or_404(project_id)
    return {"versions": draft_service.list_versions(project_id)}


@app.get("/api/projects/{project_id}/draft/versions/{version_id}")
def api_draft_version_get(project_id: str, version_id: str) -> dict:
    _project_or_404(project_id)
    try:
        return {"version": draft_service.get_version(project_id, version_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="版本不存在。") from exc


@app.post("/api/projects/{project_id}/draft/versions/{version_id}/restore")
def api_draft_version_restore(project_id: str, version_id: str) -> dict:
    _project_or_404(project_id)
    try:
        draft = draft_service.restore_version(project_id, version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="版本不存在。") from exc
    return {"ok": True, "draft": draft}


@app.post("/api/projects/{project_id}/draft/audit")
def api_draft_audit(project_id: str) -> dict:
    try:
        return draft_service.audit_draft(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc


@app.get("/api/projects/{project_id}/contradictions")
def api_contradictions(project_id: str) -> dict:
    _project_or_404(project_id)
    return contradictions.read(project_id)


@app.post("/api/projects/{project_id}/contradictions/scan")
def api_contradictions_scan(
    project_id: str, request: ContradictionScanRequest | None = None
) -> dict:
    _project_or_404(project_id)
    paper_ids = (request.paper_ids if request else []) or None
    return contradictions.scan(project_id, paper_ids)


@app.post("/api/projects/{project_id}/contradictions/review")
def api_contradictions_review(
    project_id: str, request: ContradictionReviewRequest
) -> dict:
    _project_or_404(project_id)
    return contradictions.review(project_id, use_llm=request.use_llm, limit=request.limit)


@app.post("/api/projects/{project_id}/draft/semantic-audit")
def api_draft_semantic_audit(
    project_id: str, request: SemanticAuditRequest
) -> dict:
    _project_or_404(project_id)
    try:
        return draft_semantic.audit(
            project_id, limit=request.limit, use_llm=request.use_llm
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc


@app.get("/api/llm/audit")
def api_llm_audit(limit: int = 20) -> dict:
    return {"summary": llm_gateway.summary(), "calls": llm_gateway.audit_log(limit)}


@app.get("/api/llm/settings")
def api_llm_settings() -> dict:
    return {"settings": llm_settings.public(), "calls": llm_gateway.summary(200)}


@app.post("/api/llm/settings")
def api_llm_settings_save(request: LLMSettingsRequest) -> dict:
    try:
        settings = llm_settings.save(request.model_dump(exclude_unset=True))
    except llm_settings.LLMSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "settings": settings}


@app.post("/api/llm/settings/test")
def api_llm_settings_test(request: LLMTestRequest) -> dict:
    try:
        return llm_settings.test_connection(request.model_dump(exclude_unset=True))
    except llm_settings.LLMSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"连接测试失败：{exc}") from exc


@app.post("/api/llm/settings/clear")
def api_llm_settings_clear() -> dict:
    return {"ok": True, "settings": llm_settings.clear()}


@app.get("/api/subscriptions")
def api_subscriptions() -> dict:
    return {"items": subscription_state.list_items(),
            "index": paper_index.status(),
            "scheduler": subscription_service.scheduler_status()}


@app.post("/api/subscriptions")
def api_subscription_create(request: SubscriptionCreateRequest) -> dict:
    try:
        item = subscription_state.create(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "subscription": item}


@app.patch("/api/subscriptions/{subscription_id}")
def api_subscription_update(subscription_id: str, request: SubscriptionUpdateRequest) -> dict:
    try:
        item = subscription_state.update(subscription_id, request.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="订阅不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "subscription": item}


@app.delete("/api/subscriptions/{subscription_id}")
def api_subscription_delete(subscription_id: str) -> dict:
    if not subscription_state.remove(subscription_id):
        raise HTTPException(status_code=404, detail="订阅不存在。")
    return {"ok": True}


@app.post("/api/subscriptions/{subscription_id}/run")
def api_subscription_run(subscription_id: str) -> dict:
    try:
        return subscription_service.run_once(subscription_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="订阅不存在。") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"检索失败：{exc}"[:200]) from exc


@app.get("/api/index/status")
def api_index_status() -> dict:
    return {"index": paper_index.status(), "scheduler": subscription_service.scheduler_status()}


@app.post("/api/index/sync")
def api_index_sync(request: IndexSyncRequest) -> dict:
    papers: list[dict] = []
    if request.project_id:
        _project_or_404(request.project_id)
        memory = project_state.read_memory(request.project_id)
        papers = [paper for paper in (memory.get("papers") or {}).values() if isinstance(paper, dict)]
    for paper_id in request.paper_ids:
        paper = search.get_paper(paper_id)
        if paper:
            papers.append(paper)
    result = paper_index.sync(papers, project_id=request.project_id)
    return {"ok": True, "index": paper_index.status(), "added": len(result["added"]),
            "updated": len(result["updated"])}


@app.get("/api/projects/{project_id}/manuscript")
def api_manuscript_get(project_id: str) -> dict:
    _project_or_404(project_id)
    return manuscript.read(project_id)


@app.put("/api/projects/{project_id}/manuscript")
def api_manuscript_update(project_id: str, request: ManuscriptUpdateRequest) -> dict:
    _project_or_404(project_id)
    try:
        updated = manuscript.update(project_id, request.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "citation_style" in request.model_fields_set:
        draft_service.update_citation_style(project_id, updated.get("citation_style") or "")
    return updated


@app.get("/api/citation-styles")
def api_citation_styles() -> dict:
    return {"styles": citation_styles.styles_meta(), "default": citation_styles.DEFAULT_STYLE}


@app.post("/api/projects/{project_id}/draft/references/preview")
def api_references_preview(project_id: str, request: StyleRequest) -> dict:
    _project_or_404(project_id)
    try:
        return manuscript.references_preview(project_id, request.style or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/draft/preflight")
def api_draft_preflight(project_id: str, request: StyleRequest) -> dict:
    _project_or_404(project_id)
    try:
        return manuscript.preflight(project_id, request.style or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/draft/export")
def api_draft_export(
    project_id: str, format: str = "markdown", appendix: bool = False, style: str = ""
) -> Response:
    try:
        result = draft_service.export_draft(project_id, format, appendix, style or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except pdf_export.PDFUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except pdf_export.PDFBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except draft_service.DraftValidation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=result["content"],
        media_type=result["media_type"],
        headers={
            "Content-Disposition": _download_disposition(result["filename"])
        },
    )


@app.get("/api/submission/profiles")
def api_submission_profiles() -> dict:
    return {"profiles": submission.list_profiles()}


@app.post("/api/projects/{project_id}/submission/check")
def api_submission_check(project_id: str, request: SubmissionCheckRequest) -> dict:
    _project_or_404(project_id)
    try:
        return submission.check(project_id, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _submission_text(project_id: str, kind: str, profile: str = "course") -> Response:
    project = _project_or_404(project_id)
    try:
        if kind == "cover-letter":
            content = submission.cover_letter(project_id, profile)
        else:
            content = reviewer.response_template(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = submission.filename(project, kind + ".md")
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _download_disposition(filename)},
    )


@app.get("/api/projects/{project_id}/submission/cover-letter")
def api_submission_cover_letter(project_id: str, profile: str = "course") -> Response:
    return _submission_text(project_id, "cover-letter", profile)


@app.get("/api/projects/{project_id}/reviewer/response-template")
def api_reviewer_response_template(project_id: str) -> Response:
    return _submission_text(project_id, "response-template")


@app.get("/api/projects/{project_id}/submission/package")
def api_submission_package(project_id: str, profile: str = "course") -> Response:
    _project_or_404(project_id)
    try:
        result = submission.package(project_id, profile)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=result["content"],
        media_type=result["media_type"],
        headers={
            "Content-Disposition": _download_disposition(result["filename"]),
            "X-PaperPilot-PDF-Included": "1" if result["pdf_included"] else "0",
            "X-PaperPilot-Blinded": "1" if result["blinded"] else "0",
        },
    )


@app.post("/api/projects/{project_id}/reviewer/report")
def api_reviewer_report(project_id: str) -> dict:
    _project_or_404(project_id)
    return reviewer.report(project_id)


@app.post("/api/projects/{project_id}/reviewer/map-comments")
def api_reviewer_map_comments(
    project_id: str, request: ReviewerCommentsRequest
) -> dict:
    _project_or_404(project_id)
    try:
        return reviewer.map_comments(project_id, request.comments)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/reviewer/response-template")
def api_reviewer_response_template_post(
    project_id: str, request: ReviewerCommentsRequest
) -> Response:
    project = _project_or_404(project_id)
    try:
        content = reviewer.response_template(project_id, request.comments)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = submission.filename(project, "response-template.md")
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _download_disposition(filename)},
    )


@app.get("/api/projects/{project_id}/draft/references/export")
def api_references_export(project_id: str, format: str = "ris") -> Response:
    project = _project_or_404(project_id)
    draft = draft_state.read_draft(project_id)
    fmt = format.lower()
    if fmt == "ris":
        content, media, suffix = references_io.to_ris(draft), "application/x-research-info-systems", "references.ris"
    elif fmt in {"endnote", "xml"}:
        content, media, suffix = references_io.to_endnote(draft), "application/xml; charset=utf-8", "references.xml"
    else:
        raise HTTPException(status_code=422, detail="格式只能是 ris 或 endnote。")
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": _download_disposition(submission.filename(project, suffix))},
    )


@app.post("/api/projects/{project_id}/draft/references/import")
def api_references_import(project_id: str, request: ReferenceImportRequest) -> dict:
    _project_or_404(project_id)
    try:
        return references_io.import_into_draft(
            project_id, request.format, request.text, request.revision
        )
    except references_io.ReferenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except references_io.ReferenceValidation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/agent/runs")
def api_agent_create(request: AgentRunRequest) -> dict:
    try:
        run = agent_runs.create_run(
            request.goal,
            source=request.source,
            mode=request.mode,
            max_actions=request.max_actions,
            max_seconds=request.max_seconds,
            max_papers=request.max_papers,
            max_llm_calls=request.max_llm_calls,
            project_id=request.project_id,
            seed_memory=request.seed_memory,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在。") from exc
    except ValueError as exc:
        message = str(exc)
        status = 409 if ("活动运行" in message or "归档" in message) else 422
        raise HTTPException(status_code=status, detail=message) from exc
    return {"ok": True, "run": run}


@app.get("/api/agent/runs")
def api_agent_list(limit: int = 20) -> dict:
    return {"runs": agent_runs.list_runs(limit=limit)}


@app.get("/api/agent/runs/{run_id}")
def api_agent_get(run_id: str) -> dict:
    run = agent_runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="研究任务不存在。")
    return {"run": run}


@app.get("/api/agent/runs/{run_id}/stream")
def api_agent_stream(run_id: str) -> StreamingResponse:
    if not agent_runs.get_run(run_id):
        raise HTTPException(status_code=404, detail="研究任务不存在。")
    return StreamingResponse(
        agent_stream.event_stream(run_id, agent_runs.get_run),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/agent/runs/{run_id}/action")
def api_agent_action(run_id: str, request: AgentActionRequest) -> dict:
    try:
        run = agent_runs.act(
            run_id,
            request.action,
            step_id=request.step_id,
            paper_ids=request.paper_ids,
            topic_id=request.topic_id,
            constraint=request.constraint,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究任务不存在。") from exc
    except ValueError as exc:
        status = 409 if "活动运行" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"ok": True, "run": run}


@app.post("/api/agent/runs/{run_id}/control")
def api_agent_control(run_id: str, request: AgentControlRequest) -> dict:
    payload = request.model_dump(exclude_none=True)
    action = payload.pop("action")
    try:
        run = agent_runs.control(run_id, action, **payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="研究任务不存在。") from exc
    except ValueError as exc:
        message = str(exc)
        status = 409 if (
            action in {"pause", "resume", "replan"} or "任务已经结束" in message
        ) else 422
        raise HTTPException(status_code=status, detail=message) from exc
    return {"ok": True, "run": run}


@app.get("/api/agent/runs/{run_id}/report")
def api_agent_report(run_id: str) -> Response:
    markdown = agent_runs.report_markdown(run_id)
    if markdown is None:
        raise HTTPException(status_code=404, detail="研究简报尚未生成，或任务不存在。")
    filename = f"paperpilot-research-brief-{run_id}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/agent/runs/{run_id}")
def api_agent_delete(run_id: str) -> dict:
    if not agent_runs.remove_run(run_id):
        raise HTTPException(status_code=404, detail="研究任务不存在。")
    return {"ok": True}


@app.get("/api/sources/custom")
def api_custom_sources() -> dict:
    return {
        "sources": custom_sources.list_sources(),
        "presets": [
            {
                "id": key,
                "name": value.get("name"),
                "base_url": value.get("base_url"),
                "auth_type": value.get("auth_type"),
                "license": value.get("license"),
                "homepage": value.get("homepage"),
                "note": value.get("note"),
                "defaults": custom_sources.preset_defaults(key),
            }
            for key, value in custom_sources.PRESETS.items()
        ],
    }


@app.post("/api/sources/custom")
def api_custom_source_save(request: CustomSourceRequest) -> dict:
    try:
        public = custom_sources.upsert(request.model_dump(exclude_unset=True))
    except custom_sources.CustomSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "source": public}


@app.post("/api/sources/custom/{source_id}/toggle")
def api_custom_source_toggle(source_id: str, request: CustomSourceToggleRequest) -> dict:
    public = custom_sources.set_enabled(source_id, request.enabled)
    if not public:
        raise HTTPException(status_code=404, detail="自定义来源不存在。")
    return {"ok": True, "source": public}


@app.post("/api/sources/custom/{source_id}/delete")
def api_custom_source_delete(source_id: str) -> dict:
    if not custom_sources.delete(source_id):
        raise HTTPException(status_code=404, detail="自定义来源不存在。")
    return {"ok": True, "deleted": source_id}


@app.post("/api/sources/custom/{source_id}/test")
def api_custom_source_test(source_id: str) -> dict:
    try:
        return custom_sources.test_connection(source_id)
    except custom_sources.CustomSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"来源连接测试失败：{exc}") from exc


@app.get("/api/history")
def api_history(limit: int = 50) -> dict:
    return {"entries": history.list_entries(limit=limit)}


@app.post("/api/history/clear")
def api_history_clear() -> dict:
    history.clear()
    return {"ok": True}


@app.get("/api/history/export")
def api_history_export(format: str = "markdown") -> Response:
    fmt = format.lower()
    if fmt == "csv":
        content = history.export_csv()
        media_type = "text/csv; charset=utf-8"
        suffix = "csv"
    else:
        content = history.export_markdown()
        media_type = "text/markdown; charset=utf-8"
        suffix = "md"
    filename = f"paperpilot-search-history-{datetime.now().strftime('%Y%m%d')}.{suffix}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/search/plan")
def api_search_plan(request: SearchPlanRequest) -> dict:
    source, _ = resolve_source(request.source)
    return planner.build_plan(request.query, source)


@app.post("/api/search")
def api_search(request: SearchRequest) -> dict:
    source, source_label = resolve_source(request.source)
    try:
        papers = search.search(request.query, request.limit, source)
    except custom_sources.CustomSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"检索失败：{exc}") from exc
    annotated = planner.annotate_results(papers, request.query)
    layer_order = {"直接相关": 0, "方法参考": 1, "背景脉络": 2}
    annotated.sort(key=lambda paper: layer_order.get(paper.get("layer"), 3))
    layers = planner.layer_summary(annotated)
    try:
        history.record(
            request.query,
            source,
            source_label,
            len(annotated),
            layers,
            [paper.get("title") or "" for paper in annotated[:5]],
        )
    except OSError:
        pass
    return {
        "query": request.query,
        "source": source,
        "count": len(annotated),
        "papers": [analysis.brief(paper) for paper in annotated],
        "papers_full": annotated,
        "plan": planner.build_plan(request.query, source),
        "layers": layers,
    }


@app.get("/api/paper/{paper_id}")
def api_paper(paper_id: str) -> dict:
    paper = resolve_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    paper = dict(paper)
    paper["local_pdf"] = pdf.local_pdf_path(paper_id) is not None
    return paper


@app.get("/api/paper/{paper_id}/pages/{page}")
def api_paper_page(paper_id: str, page: int) -> Response:
    if not resolve_paper(paper_id):
        raise HTTPException(status_code=404, detail="论文不存在。")
    if page < 1:
        raise HTTPException(status_code=404, detail="页码从 1 开始。")
    image = pdf.render_page(paper_id, page)
    if image is None:
        raise HTTPException(status_code=404, detail="该论文没有可预览的本地 PDF 页面。")
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/paper/{paper_id}/locate")
def api_paper_locate(
    paper_id: str,
    page: int = 1,
    quote: str = Query(..., min_length=3, max_length=600),
) -> dict:
    if not resolve_paper(paper_id):
        raise HTTPException(status_code=404, detail="论文不存在。")
    result = pdf.locate_quote(paper_id, page, quote)
    if result is None:
        raise HTTPException(status_code=404, detail="该论文没有可定位的本地 PDF 页面。")
    if not result["matched"]:
        result["note"] = "文本层未找到引句；扫描件/OCR 页没有可搜索文本层，或引句跨行/断词。"
    return result


@app.post("/api/read")
def api_read(request: ReadRequest) -> dict:
    paper = resolve_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    result = analysis.read_paper(paper, request.mode)
    result["paper"] = {**result["paper"], "local_pdf": pdf.local_pdf_path(request.paper_id) is not None}
    return result


@app.get("/api/translation/{paper_id}")
def api_translation_paper(paper_id: str, target: str = "zh", provider: str = "auto") -> dict:
    paper = resolve_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    if target not in translation.TARGETS:
        raise HTTPException(status_code=400, detail=f"不支持的翻译目标：{target}")
    if provider not in {"auto", "llm", "public"}:
        raise HTTPException(status_code=400, detail=f"不支持的翻译通道：{provider}")
    return translation.paper_payload(paper, target=target, provider=provider)


@app.post("/api/translation/section")
def api_translation_section(request: TranslationSectionRequest) -> dict:
    paper = resolve_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    try:
        return translation.translate_section(
            paper,
            request.section_index,
            target=request.target,
            force=request.force,
            provider=request.provider,
        )
    except translation.ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except translation.TranslationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/translate")
def api_translate(request: TranslateRequest) -> dict:
    paper = resolve_paper(request.paper_id) if request.paper_id else None
    if request.paper_id and not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    if not paper and not request.text:
        raise HTTPException(status_code=400, detail="需要提供 paper_id 或 text。")
    return analysis.translate(paper=paper, text=request.text)


@app.post("/api/compare")
def api_compare(request: CompareRequest) -> dict:
    papers = []
    for paper_id in request.paper_ids:
        paper = resolve_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail=f"论文不存在：{paper_id}")
        papers.append(paper)
    return analysis.compare(papers)


@app.post("/api/graph")
def api_graph(request: GraphRequest) -> dict:
    return concept_graph.build(
        scope=request.scope,
        paper_ids=request.paper_ids,
        topic=request.topic,
    )


@app.post("/api/innovate")
def api_innovate(request: PaperRequest) -> dict:
    paper = resolve_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return analysis.innovate(paper)


@app.post("/api/transform")
def api_transform(request: TransformRequest) -> dict:
    paper = resolve_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return analysis.transform(paper, request.outputs)


@app.post("/api/gaps")
def api_gaps(request: GapRequest) -> dict:
    return analysis.gaps(request.topic)


@app.post("/api/topic")
def api_topic(request: TopicRequest) -> dict:
    if request.scope == "selected" and not request.paper_ids:
        raise HTTPException(status_code=400, detail="请先在左侧勾选至少一篇论文。")
    return research.topic_studio(request.topic, request.scope, request.paper_ids)


@app.post("/api/draft")
def api_draft(request: DraftRequest) -> dict:
    if request.scope == "selected" and not request.paper_ids:
        raise HTTPException(status_code=400, detail="请先在左侧勾选至少一篇论文。")
    try:
        return research.draft(request.topic, request.kind, request.scope, request.paper_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/check")
def api_check(request: CheckRequest) -> dict:
    if request.scope == "selected" and not request.paper_ids:
        raise HTTPException(status_code=400, detail="请先在左侧勾选至少一篇论文。")
    return research.check_claims(request.text, request.scope, request.paper_ids)


@app.post("/api/argument")
def api_argument(request: ArgumentRequest) -> dict:
    if request.scope == "selected" and not request.paper_ids:
        raise HTTPException(status_code=400, detail="请先在左侧勾选至少一篇论文。")
    return argument.argument_review(
        request.text,
        topic=request.topic,
        scope=request.scope,
        paper_ids=request.paper_ids,
    )


@app.post("/api/ask")
def api_ask(request: AskRequest) -> dict:
    if request.scope == "selected" and not request.paper_ids:
        raise HTTPException(status_code=400, detail="请先在左侧勾选至少一篇论文。")
    return ask.answer(request.question, request.scope, request.paper_ids, request.top_k)


@app.get("/api/library")
def api_library(query: str = "", status: str | None = None, tag: str | None = None) -> dict:
    return {
        "items": library.list_items(query, status, tag),
        "stats": library.stats(),
        "statuses": [{"id": key, "label": value} for key, value in library.STATUS_LABELS.items()],
    }


@app.get("/api/library/health")
def api_library_health(verify: bool = False) -> dict:
    return library.health(verify_dois=verify)


@app.get("/api/library/export")
def api_library_export(format: str = "markdown") -> Response:
    stamp = datetime.now().strftime("%Y%m%d")
    fmt = format.lower()
    if fmt == "json":
        content = json.dumps(library.export_json(), ensure_ascii=False, indent=2)
        media_type = "application/json; charset=utf-8"
        filename = f"paperpilot-library-{stamp}.json"
    elif fmt == "bibtex":
        content = library.export_bibtex()
        media_type = "application/x-bibtex; charset=utf-8"
        filename = f"paperpilot-library-{stamp}.bib"
    else:
        content = library.export_markdown()
        media_type = "text/markdown; charset=utf-8"
        filename = f"paperpilot-library-{stamp}.md"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/library/import")
async def api_library_import(request: Request) -> dict:
    body = await request.body()
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="备份文件超过 5 MB 限制。")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="备份文件不是有效的 JSON。") from exc
    try:
        return library.import_items(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/library")
def api_library_save(request: LibrarySaveRequest) -> dict:
    paper = resolve_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    item = library.save(paper)
    return {"item": item, "stats": library.stats()}


@app.patch("/api/library/{paper_id}")
@app.post("/api/library/{paper_id}/update")
def api_library_update(paper_id: str, request: LibraryUpdateRequest) -> dict:
    # Some hosting gateways only allow GET/POST; keep a POST alias for those.
    try:
        item = library.update(
            paper_id,
            status=request.status,
            tags=request.tags,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="研究库里没有这篇论文。")
    return {"item": item, "stats": library.stats()}


@app.delete("/api/library/{paper_id}")
@app.post("/api/library/{paper_id}/delete")
def api_library_remove(paper_id: str) -> dict:
    if not library.remove(paper_id):
        raise HTTPException(status_code=404, detail="研究库里没有这篇论文。")
    return {"ok": True, "stats": library.stats()}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "upload.pdf"
    if file.content_type not in {"application/pdf", "application/octet-stream"} and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="只支持 PDF 文件。")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    try:
        paper = await run_in_threadpool(pdf.parse_pdf_bytes, data, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search.register_upload(paper)
    return {
        "paper_id": paper["id"],
        "title": paper["title"],
        "pages": paper["metrics"].get("页数", 0),
        "chars": paper["metrics"].get("字符数", 0),
        "sections": len(paper["sections"]),
        "ocr_used": bool(paper.get("ocr_used")),
        "ocr_pages": int(paper.get("ocr_pages") or 0),
        "ocr_truncated": bool(paper.get("ocr_truncated")),
        "ocr_warning": str(paper.get("ocr_warning") or ""),
        "paper": analysis.brief(paper),
    }


@app.get("/api/eval")
def api_eval() -> dict:
    result_file = config.DATA_DIR / "eval_results.json"
    cases_file = config.DATA_DIR / "eval_set.json"
    result = {}
    if result_file.exists():
        result = json.loads(result_file.read_text(encoding="utf-8"))
    cases = []
    if cases_file.exists():
        cases = json.loads(cases_file.read_text(encoding="utf-8"))
    return {
        "summary": result,
        "cases": cases,
        "targets": [
            {"dimension": "检索", "metric": "Recall@5", "target": "≥ 0.80"},
            {"dimension": "字段抽取", "metric": "字段准确率", "target": "≥ 0.85"},
            {"dimension": "对比", "metric": "关键单元格准确率", "target": "≥ 0.85"},
            {"dimension": "翻译", "metric": "术语准确率", "target": "≥ 0.95"},
            {"dimension": "证据", "metric": "无证据断言率", "target": "< 5%"},
            {"dimension": "证据问答", "metric": "引用命中率/支持度", "target": "≥ 0.90"},
            {"dimension": "来源覆盖", "metric": "Crossref DOI 解析", "target": "100%"},
            {"dimension": "PDF 抽取", "metric": "作者/图表/结果可用率", "target": "≥ 0.80"},
            {"dimension": "研究库", "metric": "读写/备份导入成功率", "target": "100%"},
            {"dimension": "选题", "metric": "候选问题/阅读路径", "target": "≥ 3 个问题"},
            {"dimension": "写作", "metric": "草稿引用键完整率", "target": "100%"},
            {"dimension": "核查", "metric": "逐句支持度", "target": "可追溯到页码"},
            {"dimension": "效率", "metric": "5 篇综述耗时", "target": "4h → 40min"},
        ],
        "note": "内置回归集用于检查流程与证据链，不等同于人工评测；运行 scripts/evaluate.py 可刷新结果。",
    }


@app.get("/api/paper/{paper_id}/parsed")
def api_parsed(paper_id: str) -> dict:
    path = config.PARSED_DIR / f"{paper_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="没有该论文的解析缓存。")
    return json.loads(path.read_text(encoding="utf-8"))
