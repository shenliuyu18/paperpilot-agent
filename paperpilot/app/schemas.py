from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

PaperId = Annotated[str, Field(min_length=1, max_length=120)]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=8, ge=1, le=20)
    source: str = Field(default="demo", min_length=1, max_length=160)


class SearchPlanRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    source: str = Field(default="demo", min_length=1, max_length=160)


class ReadRequest(BaseModel):
    paper_id: str
    mode: Literal["tldr", "5min", "15min"] = "5min"


class TranslateRequest(BaseModel):
    paper_id: str | None = None
    text: str | None = Field(default=None, max_length=6000)


class TranslationSectionRequest(BaseModel):
    paper_id: str
    section_index: int = Field(ge=0, le=60)
    target: Literal["zh", "en"] = "zh"
    provider: Literal["auto", "llm", "public"] = "auto"
    force: bool = False


class CompareRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=10)


class SubscriptionCreateRequest(BaseModel):
    label: str = Field(default="", max_length=60)
    query: str = Field(min_length=2, max_length=160)
    source: str = Field(default="arxiv", max_length=40)
    interval_hours: int = Field(default=168)
    limit: int = Field(default=20, ge=1, le=50)
    project_id: str = Field(default="", max_length=40)
    enabled: bool = True


class SubscriptionUpdateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=60)
    query: str | None = Field(default=None, min_length=2, max_length=160)
    source: str | None = Field(default=None, max_length=40)
    interval_hours: int | None = None
    limit: int | None = Field(default=None, ge=1, le=50)
    project_id: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None


class IndexSyncRequest(BaseModel):
    project_id: str = Field(default="", max_length=40)
    paper_ids: list[PaperId] = Field(default_factory=list, max_length=50)


class StyleRequest(BaseModel):
    style: str = Field(default="", max_length=40)


class ManuscriptAuthor(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    affiliation: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=120)
    orcid: str = Field(default="", max_length=19)
    corresponding: bool = False


class ManuscriptUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)
    authors: list[ManuscriptAuthor] | None = None
    abstract: str | None = Field(default=None, max_length=2000)
    keywords: list[str] | None = None
    funding: str | None = Field(default=None, max_length=300)
    contributions: str | None = Field(default=None, max_length=300)
    data_availability: str | None = Field(default=None, max_length=500)
    code_availability: str | None = Field(default=None, max_length=500)
    competing_interests: str | None = Field(default=None, max_length=300)
    acknowledgments: str | None = Field(default=None, max_length=1000)
    citation_style: str | None = Field(default=None, max_length=40)
    language: str | None = Field(default=None, max_length=4)


class ContradictionScanRequest(BaseModel):
    paper_ids: list[PaperId] = Field(default_factory=list, max_length=40)


class GraphRequest(BaseModel):
    scope: Literal["all", "library", "selected"] = "selected"
    paper_ids: list[PaperId] = Field(default_factory=list, max_length=20)
    topic: str = Field(default="", max_length=200)


class PaperRequest(BaseModel):
    paper_id: str


class TransformRequest(BaseModel):
    paper_id: str
    outputs: list[str] = Field(
        default_factory=lambda: ["repro", "code", "slides", "quiz"]
    )


class GapRequest(BaseModel):
    topic: str = Field(default="面向存算一体的低功耗 ADC 设计", max_length=300)


class UploadResponse(BaseModel):
    paper_id: str
    title: str
    pages: int
    chars: int
    sections: int


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=400)
    scope: Literal["all", "library", "selected"] = "all"
    paper_ids: list[str] = Field(default_factory=list, max_length=10)
    top_k: int = Field(default=6, ge=3, le=10)


class LibrarySaveRequest(BaseModel):
    paper_id: str


class LibraryUpdateRequest(BaseModel):
    status: Literal["todo", "reading", "done"] | None = None
    tags: list[str] | None = Field(default=None, max_length=8)
    note: str | None = Field(default=None, max_length=20000)


class TopicRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=200)
    scope: Literal["all", "library", "selected"] = "all"
    paper_ids: list[str] = Field(default_factory=list, max_length=10)


class DraftRequest(TopicRequest):
    kind: Literal["related_work", "outline", "abstract"] = "related_work"


class DraftSaveRequest(BaseModel):
    revision: int = Field(ge=0)
    title: str = Field(default="", max_length=200)
    citation_style: Literal["numeric", "author-year", "gb7714", "apa", "ieee"] = "numeric"
    sections: list[dict] = Field(default_factory=list)
    citations: dict | None = None


class CitationRequest(BaseModel):
    paper_id: str
    revision: int = Field(ge=0)


class VersionRequest(BaseModel):
    label: str = Field(default="手动保存", max_length=40)


class CheckRequest(BaseModel):
    text: str = Field(min_length=5, max_length=8000)
    scope: Literal["all", "library", "selected"] = "all"
    paper_ids: list[str] = Field(default_factory=list, max_length=10)


class ArgumentRequest(BaseModel):
    text: str = Field(min_length=5, max_length=12000)
    topic: str = Field(default="", max_length=200)
    scope: Literal["all", "library", "selected"] = "all"
    paper_ids: list[str] = Field(default_factory=list, max_length=10)


class CustomSourceRequest(BaseModel):
    id: str | None = Field(default=None, max_length=160)
    name: str = Field(default="", max_length=80)
    preset: Literal["semantic_scholar", "core", "generic_json"] = "generic_json"
    base_url: str = Field(default="", max_length=500)
    method: Literal["GET", "POST"] = "GET"
    query_param: str = Field(default="query", max_length=60)
    limit_param: str = Field(default="limit", max_length=60)
    extra_params: dict = Field(default_factory=dict)
    headers: dict = Field(default_factory=dict)
    result_path: str = Field(default="results", max_length=200)
    field_map: dict = Field(default_factory=dict)
    auth_type: Literal["none", "header", "bearer", "query", "basic"] = "none"
    auth_name: str = Field(default="", max_length=80)
    account_param: str = Field(default="", max_length=80)
    account: str = Field(default="", max_length=200)
    enabled: bool = True
    token: str = Field(default="", max_length=2000)
    password: str = Field(default="", max_length=2000)
    clear_secret: bool = False


class CustomSourceToggleRequest(BaseModel):
    enabled: bool


class OnlineGlossaryRequest(BaseModel):
    preset_id: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=80)
    license: str | None = Field(default=None, max_length=160)



class AgentRunRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=300)
    source: str = Field(default="demo", min_length=1, max_length=160)
    mode: Literal["auto", "checkpoint", "step"] = "checkpoint"
    max_actions: int = Field(default=14, ge=1, le=30)
    max_seconds: int = Field(default=300, ge=30, le=1800)
    max_papers: int = Field(default=6, ge=2, le=10)
    max_llm_calls: int = Field(default=4, ge=0, le=20)
    project_id: str | None = Field(default=None, max_length=40)
    seed_memory: bool = True


class AgentActionRequest(BaseModel):
    action: Literal["approve", "retry", "skip", "cancel", "resume", "stop"]
    step_id: str | None = Field(default=None, max_length=40)
    paper_ids: list[str] | None = Field(default=None, max_length=10)
    topic_id: str | None = Field(default=None, max_length=80)
    constraint: str | None = Field(default=None, max_length=300)


class AgentControlRequest(BaseModel):
    action: Literal[
        "pause", "resume", "replan", "set_mode",
        "add_constraint", "add_papers", "inject_question",
    ]
    mode: Literal["auto", "checkpoint", "step"] | None = None
    constraint: str | None = Field(default=None, max_length=200)
    paper_ids: list[str] | None = Field(default=None, max_length=10)
    question: str | None = Field(default=None, max_length=300)
    priority: Literal["high", "medium", "low"] = "medium"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    question: str = Field(min_length=2, max_length=500)
    scope: str = Field(default="", max_length=1000)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=80)
    brief: dict | None = None
    tags: list[str] | None = Field(default=None, max_length=8)
    stage: Literal["exploring", "reading", "writing", "review", "done"] | None = None


class AssistantAskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class SemanticAuditRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=20)
    use_llm: bool = True


class ContradictionReviewRequest(BaseModel):
    use_llm: bool = False
    limit: int = Field(default=8, ge=1, le=20)


class SubmissionCheckRequest(BaseModel):
    profile: str = "course"


class ReferenceImportRequest(BaseModel):
    format: Literal["ris", "endnote"]
    text: str = Field(min_length=1, max_length=2_000_000)
    revision: int = Field(ge=0)


class ReviewerCommentsRequest(BaseModel):
    comments: list[str] = Field(min_length=1, max_length=20)


class LLMSettingsRequest(BaseModel):
    provider: str = Field(default="openai", max_length=40)
    base_url: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=120)
    api_key: str = Field(default="", max_length=300)
    clear_key: bool = False
    embeddings: bool = False
    embedding_model: str = Field(default="", max_length=120)
    timeout: float | None = Field(default=None, ge=1, le=300)
    retries: int | None = Field(default=None, ge=0, le=3)
    routes: dict[str, str] | None = None


class LLMTestRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=40)
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=300)
