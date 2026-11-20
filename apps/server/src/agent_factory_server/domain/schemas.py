from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ApprovalPolicy(BaseModel):
    """Optional gate when ``require_approval`` is true.

    Evaluation order for each tool call:
    1. ``always_require`` — any matching rule → pause for approval
    2. ``allow`` — any matching rule → skip approval and execute
    3. ``default`` — ``require`` | ``skip`` | ``ask_client``

    ``ask_client``: factory asks the Bound Bridge via WS ``approval_check``;
    Bridge replies ``{require: bool}``. Offline / timeout / non-client tools
    fall back to ``require``.

    Each rule is an AND-map of argument path → matcher. Path uses dotted keys
    (e.g. ``command`` or ``options.force``). Matcher is either a literal for
    equality, or ``{"regex": "..."}`` / ``{"eq": ...}`` / ``{"contains": "..."}``.
    """

    default: Literal["require", "skip", "ask_client"] = "require"
    allow: list[dict[str, Any]] = Field(default_factory=list)
    always_require: list[dict[str, Any]] = Field(default_factory=list)


class ToolDef(BaseModel):
    name: str
    description: str = ""
    execution: Literal["remote_http", "client", "builtin"] = "client"
    endpoint: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """Master switch: when false, never pause. When true, evaluate ``approval_policy``."""
    require_approval: bool = False
    """Argument-aware gate; ignored unless ``require_approval`` is true. None = always require."""
    approval_policy: Optional[ApprovalPolicy] = None


class SkillDef(BaseModel):
    """Catalog entry only — bodies load on demand from factory skill packages."""

    name: str
    description: str = ""
    """package=agent-owned; platform=factory library mount; client=legacy Bridge."""
    origin: Literal["package", "platform", "client"] = "package"

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_content(cls, data: Any) -> Any:
        if isinstance(data, dict) and "content" in data:
            # Legacy Agent JSON had full bodies; never re-inject them into prompts.
            cleaned = dict(data)
            cleaned.pop("content", None)
            if not str(cleaned.get("description") or "").strip():
                cleaned["description"] = ""
            return cleaned
        return data


class SkillPackageFileOut(BaseModel):
    path: str
    kind: str
    is_binary: bool = False
    size_bytes: int = 0
    sha256: str = ""
    mode: Optional[str] = None


class SkillPackageOut(BaseModel):
    id: str
    agent_id: str
    name: str
    description: str = ""
    version: Optional[str] = None
    source: str = "upload"
    status: str = "active"
    total_bytes: int = 0
    file_count: int = 0
    files: list[SkillPackageFileOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SkillPackageUpdate(BaseModel):
    """Patch package metadata (catalog description). Name is immutable."""

    description: str = Field(..., min_length=0, max_length=8000)


class PlatformSkillPackageOut(BaseModel):
    id: str
    name: str
    description: str = ""
    version: Optional[str] = None
    source: str = "upload"
    status: str = "active"
    total_bytes: int = 0
    file_count: int = 0
    files: list[SkillPackageFileOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mount_count: int = 0
    created_at: datetime
    updated_at: datetime


class AgentSkillMountOut(BaseModel):
    agent_id: str
    package_id: str
    name: str
    description: str = ""
    version: Optional[str] = None
    source: str = "upload"
    status: str = "active"
    total_bytes: int = 0
    file_count: int = 0
    files: list[SkillPackageFileOut] = Field(default_factory=list)
    created_at: datetime
    mounted_at: datetime


class ContextPolicy(BaseModel):
    mode: Literal["auto", "off"] = "auto"
    soft_ratio: float = 0.70
    hard_ratio: float = 0.85
    keep_last_runs: int = 4
    tool_result_max_chars: int = 12000
    # Skill bodies are linear SOPs: fragmenting them breaks procedure order, so
    # skill_load/skill_read get their own (larger) projection cap.
    skill_result_max_chars: int = 40000
    summarize_enabled: bool = True
    summarize_max_input_chars: int = 30000
    chars_per_token: float = 4.0


class MemoryPolicy(BaseModel):
    """Per-agent memory policy (quotas + inject caps). Stored in agent.runtime.memory."""

    enabled: bool = True
    # Prompt inject caps (active rows only)
    profile_top_k: int = 30
    playbook_top_k: int = 40
    max_chars: int = 8000
    # Hard quotas per user_key (active rows)
    max_profile_items: int = 30
    max_playbook_items: int = 80
    max_profile_chars: int = 500
    # Catalog title only — keep tiny so every-turn inject stays lean.
    max_playbook_chars: int = 80
    max_detail_chars: int = 6000
    # When over quota on write: deactivate oldest unpinned (keep readable via memory_read)
    evict_when_full: bool = True
    # legacy alias
    longterm_top_k: Optional[int] = None


class MemoryUsageOut(BaseModel):
    agent_id: str
    user_key: str
    profile_active: int = 0
    profile_inactive: int = 0
    playbook_active: int = 0
    playbook_inactive: int = 0
    detail_active: int = 0
    detail_inactive: int = 0
    approx_chars: int = 0
    quotas: MemoryPolicy


class KnowledgePolicy(BaseModel):
    """Per-agent knowledge-base policy. Stored in agent.runtime.knowledge."""

    enabled: bool = True
    # Hard cap for the injected catalog (paths + summaries), in characters.
    # Over budget: summaries are dropped first, then the tree is collapsed.
    max_catalog_chars: int = Field(default=3000, ge=200, le=20000)
    # Personal (user-scoped) documents are visible only to their user_key.
    include_user_docs: bool = True
    # kb_list page size (tree browsing fallback when the catalog is collapsed).
    max_list_items: int = Field(default=50, ge=5, le=200)


class MultiAgentPolicy(BaseModel):
    """Lead-side multi-agent orchestration. Bound via ``team_id``."""

    enabled: bool = False
    team_id: Optional[str] = None


class SchedulerPolicy(BaseModel):
    """Per-agent scheduled jobs (reminders / cron). Stored in agent.runtime.scheduler."""

    enabled: bool = True
    # Max active jobs per (agent_id, user_key)
    max_active_jobs: int = Field(default=50, ge=1, le=500)


class ScheduleOut(BaseModel):
    id: str
    agent_id: str
    user_key: str = ""
    created_from_session_id: str = ""
    created_by_run_id: Optional[str] = None
    name: str = ""
    schedule_kind: str
    run_at: Optional[str] = None
    every_ms: Optional[int] = None
    cron_expr: Optional[str] = None
    timezone: str = "Asia/Shanghai"
    session_target: str = "origin"
    session_id: Optional[str] = None
    run_mode: str
    payload_text: str = ""
    status: str
    delete_after_run: bool = True
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_error: Optional[str] = None
    attempt_count: int = 0
    last_fired_session_id: Optional[str] = None
    last_fired_run_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ScheduleStatsOut(BaseModel):
    agent_id: str
    user_key: Optional[str] = None
    active: int = 0
    paused: int = 0
    completed: int = 0
    cancelled: int = 0
    failed: int = 0
    total: int = 0
    max_active_jobs: int = 50
    due_soon: int = 0  # active with next_run_at within 1h


class AgentRuntimeConfig(BaseModel):
    # Per-run (single user turn) guards. Persisted rounds/tokens survive ask_user
    # / approval resumes, so a parked run cannot reset its budget.
    max_tool_rounds: int = Field(default=8, ge=1, le=200)
    on_max_rounds: Literal["summarize", "stop"] = "summarize"
    # 0 = inherit Settings.max_run_tokens
    max_run_tokens: int = Field(default=0, ge=0)
    tool_timeout_seconds: float = 30.0
    run_timeout_seconds: float = 300.0
    parallel_tool_calls: bool = True
    """Run sibling client/remote_http tool calls concurrently when >1 per round."""
    tool_result_max_chars: int = 12000  # legacy; prefer context_policy.tool_result_max_chars
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    knowledge: KnowledgePolicy = Field(default_factory=KnowledgePolicy)
    multi_agent: MultiAgentPolicy = Field(default_factory=MultiAgentPolicy)
    scheduler: SchedulerPolicy = Field(default_factory=SchedulerPolicy)


class KbDocumentOut(BaseModel):
    id: str
    # Owning Agent — every document is nested under one Agent.
    agent_id: str = ""
    # agent = shared within the Agent; user = one end user of that Agent
    scope: str = "agent"
    # Set only when scope=user.
    user_key: str = ""
    path: str
    title: str = ""
    summary: str = ""
    summary_source: str = "auto"
    size_bytes: int = 0
    sha256: str = ""
    status: str = "active"
    chars: int = 0
    created_at: datetime
    updated_at: datetime


class KbDocumentUpdate(BaseModel):
    """Patch metadata. Supplying ``summary`` marks it as manual (never overwritten)."""

    title: Optional[str] = None
    summary: Optional[str] = None
    path: Optional[str] = None
    status: Optional[Literal["active", "disabled"]] = None


class KbDocumentPage(BaseModel):
    """One page of documents for the table view.

    ``scope=user`` without ``user_key`` lists every end user of this Agent.
    """

    items: list[KbDocumentOut]
    total: int
    page: int
    page_size: int
    # Distinct user_keys matching the current filter (scope=user only).
    user_keys: list[str] = Field(default_factory=list)


class KbDocumentContent(BaseModel):
    """Document body preview for the detail drawer."""

    id: str
    path: str
    content: str
    total_chars: int
    truncated: bool = False


class KbUploadResult(BaseModel):
    document: KbDocumentOut
    created: bool = True
    summary_source: str = "auto"
    warnings: list[str] = Field(default_factory=list)


class KbFolderResult(BaseModel):
    affected: int = 0


class KbFolderMove(BaseModel):
    """Agent comes from the URL; only the scope selector travels in the body."""

    scope: Literal["agent", "user"] = "agent"
    # Required when scope=user.
    user_key: Optional[str] = None
    path: str
    new_path: str


class KbFolderDelete(BaseModel):
    scope: Literal["agent", "user"] = "agent"
    user_key: Optional[str] = None
    path: str


class ModelProfileCreate(BaseModel):
    name: str
    provider: str = "custom"
    base_url: str
    api_key: str = ""
    model_name: str
    temperature: float = 0.3
    context_window_tokens: int = 128000
    reserve_output_tokens: int = 8192
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    is_default: bool = False
    enabled: bool = True
    remark: Optional[str] = None


class DiscoverModelsRequest(BaseModel):
    """Probe upstream OpenAI-compatible GET /models."""

    base_url: str
    api_key: str = ""
    """When editing an existing profile and api_key is blank, pass profile_id to reuse stored key."""
    profile_id: Optional[str] = None


class RemoteModelInfo(BaseModel):
    id: str
    context_window_tokens: Optional[int] = None
    owned_by: Optional[str] = None
    raw_keys: list[str] = Field(default_factory=list)


class DiscoverModelsOut(BaseModel):
    models: list[RemoteModelInfo] = Field(default_factory=list)
    note: str = ""


class ModelProfileUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    context_window_tokens: Optional[int] = None
    reserve_output_tokens: Optional[int] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None
    remark: Optional[str] = None


class ModelProfileOut(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    api_key_masked: str
    has_api_key: bool
    model_name: str
    temperature: float
    context_window_tokens: int = 128000
    reserve_output_tokens: int = 8192
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    is_default: bool
    enabled: bool
    remark: Optional[str]
    created_at: datetime
    updated_at: datetime


class LlmUsageOut(BaseModel):
    id: str
    session_id: str
    run_id: str
    agent_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    uncached_prompt_tokens: int = 0
    source: str = "api"
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    estimated_cost: float = 0.0
    created_at: datetime


class LlmUsagePage(BaseModel):
    items: list[LlmUsageOut]
    total: int
    page: int
    page_size: int


class UsageSummaryBucket(BaseModel):
    key: str
    label: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    uncached_prompt_tokens: int = 0
    estimated_cost: float = 0.0


class UsageSummaryOut(BaseModel):
    group_by: str
    from_time: Optional[datetime] = None
    to_time: Optional[datetime] = None
    totals: UsageSummaryBucket
    buckets: list[UsageSummaryBucket] = Field(default_factory=list)
    note: str = (
        "estimated_cost uses model profile prices at call time (CNY). "
        "cached_tokens / uncached_prompt_tokens split prompt input; "
        "source=estimate rows used char heuristics when upstream usage was missing."
    )


class CompactReportOut(BaseModel):
    estimated_tokens_before: int
    estimated_tokens_after: int
    budget: int
    soft: int
    hard: int
    layers_applied: list[str] = Field(default_factory=list)
    covered_run_ids: list[str] = Field(default_factory=list)
    covered_until_run_id: Optional[str] = None
    summarize_failed: bool = False
    overflow: bool = False


class PromptSnapshotOut(BaseModel):
    id: str
    run_id: str
    created_at: datetime
    agent_system_prompt: str = ""
    memory_block: str = ""
    context_summary: str = ""
    message_count: int = 0
    estimated_tokens_after: Optional[int] = None
    layers_applied: list[str] = Field(default_factory=list)


class LlmMessagesDebugOut(BaseModel):
    session_id: str
    """Agent system_prompt + skills (stable unless agent edited)."""
    agent_system_prompt: str = ""
    """Injected memory block (profile + playbook catalog); may change across turns."""
    memory_block: str = ""
    """Phase D context summary text if any."""
    context_summary: str = ""
    messages: list[dict[str, Any]]
    report: CompactReportOut
    """Historical prefixes recorded at each model call (newest first)."""
    prompt_snapshots: list[PromptSnapshotOut] = Field(default_factory=list)
    note: str = (
        "agent_system_prompt is usually stable; memory_block / context_summary / history "
        "can change every model call. prompt_snapshots are historical records."
    )


class AgentCreate(BaseModel):
    name: str
    """Legacy / default (usually zh). Prefer ``system_prompts`` for multi-locale."""
    system_prompt: str = "你是一个有用的助手。"
    """Per-locale templates: keys ``zh`` / ``en`` / ``ja``."""
    system_prompts: dict[str, str] = Field(default_factory=dict)
    model: Optional[str] = None
    model_profile_id: Optional[str] = None
    tools: list[ToolDef] = Field(default_factory=list)
    skills: list[SkillDef] = Field(default_factory=list)
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    """Avatar / logo URL shown on the new-chat empty state."""
    logo_url: Optional[str] = None
    """Empty-state headline; falls back to a generic greeting when empty."""
    welcome_title: Optional[str] = None
    """Empty-state supporting line under the title."""
    welcome_subtitle: Optional[str] = None
    """Clickable suggested prompts on the empty state (max 12)."""
    suggested_prompts: list[str] = Field(default_factory=list)
    """Chat UI theme tokens (colors / radii); see runtime.ui_theme."""
    ui_theme: dict[str, str] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    system_prompts: Optional[dict[str, str]] = None
    model: Optional[str] = None
    model_profile_id: Optional[str] = None
    tools: Optional[list[ToolDef]] = None
    skills: Optional[list[SkillDef]] = None
    runtime: Optional[AgentRuntimeConfig] = None
    logo_url: Optional[str] = None
    welcome_title: Optional[str] = None
    welcome_subtitle: Optional[str] = None
    suggested_prompts: Optional[list[str]] = None
    """Replace whole theme map when set (empty ``{}`` clears to defaults)."""
    ui_theme: Optional[dict[str, str]] = None


class AgentOut(BaseModel):
    id: str
    name: str
    system_prompt: str
    system_prompts: dict[str, str] = Field(default_factory=dict)
    model: Optional[str]
    model_profile_id: Optional[str]
    model_profile_name: Optional[str] = None
    tools: list[ToolDef]
    skills: list[SkillDef]
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    logo_url: Optional[str] = None
    welcome_title: Optional[str] = None
    welcome_subtitle: Optional[str] = None
    suggested_prompts: list[str] = Field(default_factory=list)
    ui_theme: dict[str, str] = Field(default_factory=dict)
    access_key: Optional[str] = None
    has_secret_key: bool = False
    created_at: datetime
    updated_at: datetime


class AgentCredentialOut(BaseModel):
    agent_id: str
    access_key: str
    """Plaintext secret — returned only on generate/rotate."""
    secret_key: str
    has_secret_key: bool = True


class AgentDirectoryItem(BaseModel):
    """Public agent directory: enough to pick an agent, nothing sensitive."""

    id: str
    name: str
    logo_url: Optional[str] = None


class AgentDirectoryItem(BaseModel):
    """Public agent directory: enough to pick an agent, nothing sensitive."""

    id: str
    name: str
    logo_url: Optional[str] = None


class EmbedTokenRequest(BaseModel):
    user_key: Optional[str] = None
    ttl_seconds: Optional[int] = None


class EmbedTokenOut(BaseModel):
    embed_token: str
    agent_id: str
    user_key: Optional[str] = None
    expires_at: datetime
    token_type: str = "Bearer"


class AdminLoginRequest(BaseModel):
    """Admin console login payload."""

    username: str = Field(default="admin", max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminSessionOut(BaseModel):
    token: str
    username: str
    expires_at: datetime
    token_type: str = "Bearer"


class SessionCreate(BaseModel):
    agent_id: str
    user_key: str
    title: Optional[str] = None
    """Session language: ``zh`` / ``en`` / ``ja`` (aliases accepted)."""
    locale: str = "zh"
    """Variables for Agent ``system_prompt`` template, e.g. ``{\"userId\": \"u_1\"}``."""
    prompt_vars: dict[str, Any] = Field(default_factory=dict)
    """Local Bridge device for client tools; sticky for this session."""
    client_device_id: Optional[str] = None


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None
    """Rebind local Bridge for this session (None leaves unchanged; empty clears)."""
    client_device_id: Optional[str] = None


class SessionOut(BaseModel):
    id: str
    agent_id: str
    title: Optional[str]
    user_key: Optional[str] = None
    client_device_id: Optional[str] = None
    status: str = "active"
    """user = normal chat; system = hidden delegate child session."""
    visibility: str = "user"
    locale: str = "zh"
    """Instantiated Agent system_prompt at session creation (template → resolved)."""
    system_prompt: Optional[str] = None
    """Prompt template variables passed when the session was created."""
    prompt_vars: dict[str, str] = Field(default_factory=dict)
    """Soft-clear watermark; history at or before this time is hidden."""
    history_cleared_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TeamMember(BaseModel):
    agent_id: str
    role: str = ""


class TeamCreate(BaseModel):
    name: str
    lead_agent_id: str
    members: list[TeamMember] = Field(default_factory=list)
    max_depth: int = Field(default=1, ge=1, le=3)
    max_parallel: int = Field(default=3, ge=1, le=8)
    max_child_rounds: int = Field(default=8, ge=1, le=32)
    result_max_chars: int = Field(default=4000, ge=200, le=20000)
    allow_async: bool = True
    enabled: bool = True


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    lead_agent_id: Optional[str] = None
    members: Optional[list[TeamMember]] = None
    max_depth: Optional[int] = Field(default=None, ge=1, le=3)
    max_parallel: Optional[int] = Field(default=None, ge=1, le=8)
    max_child_rounds: Optional[int] = Field(default=None, ge=1, le=32)
    result_max_chars: Optional[int] = Field(default=None, ge=200, le=20000)
    allow_async: Optional[bool] = None
    enabled: Optional[bool] = None


class TeamOut(BaseModel):
    id: str
    name: str
    lead_agent_id: str
    members: list[TeamMember] = Field(default_factory=list)
    max_depth: int = 1
    max_parallel: int = 3
    max_child_rounds: int = 8
    result_max_chars: int = 4000
    allow_async: bool = True
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class BoardArtifactOut(BaseModel):
    id: str
    project_id: str
    path: str
    title: str = ""
    content: str = ""
    version: int = 1
    author_agent_id: str = ""
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class BoardArtifactWrite(BaseModel):
    path: str
    title: Optional[str] = None
    content: str = ""
    author_agent_id: Optional[str] = None


class BoardProjectOut(BaseModel):
    id: str
    lead_session_id: str
    team_id: Optional[str] = None
    title: str = "Project Board"
    user_key: str = ""
    status: str = "active"
    artifacts: list[BoardArtifactOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ChildRunOut(BaseModel):
    run_id: str
    session_id: str
    agent_id: str
    status: str
    user_input: str
    parent_run_id: Optional[str] = None
    root_session_id: Optional[str] = None
    delegation_call_id: Optional[str] = None
    delegation_depth: int = 0
    finish_reason: Optional[str] = None
    error_message: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    summary: Optional[str] = None


class SessionPage(BaseModel):
    items: list[SessionOut]
    total: int
    page: int
    page_size: int


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: Optional[str]
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    created_at: datetime


MemoryKind = Literal["profile", "playbook", "detail"]


class MemoryCreate(BaseModel):
    agent_id: str
    user_key: str
    kind: MemoryKind = "profile"
    key: str
    content: str
    metadata: Optional[dict[str, Any]] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    active: Optional[bool] = None
    pinned: Optional[bool] = None


class MemoryOut(BaseModel):
    id: str
    agent_id: str
    kind: str
    key: str
    content: str
    user_key: Optional[str] = None
    active: bool = True
    pinned: bool = False
    metadata: Optional[dict[str, Any]] = None
    source_run_id: Optional[str] = None
    last_accessed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class MemoryPage(BaseModel):
    items: list[MemoryOut]
    total: int
    page: int
    page_size: int


class TimelineStep(BaseModel):
    id: str
    type: str
    call_id: Optional[str] = None
    name: Optional[str] = None
    content: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: datetime


class TimelineRun(BaseModel):
    run_id: str
    status: str
    user_message: str
    model_name: Optional[str] = None
    rounds_used: int = 0
    finish_reason: Optional[str] = None
    error_message: Optional[str] = None
    parent_run_id: Optional[str] = None
    root_session_id: Optional[str] = None
    delegation_call_id: Optional[str] = None
    delegation_depth: int = 0
    # True when this run was fired by the scheduler (assistant_message or synthetic trigger).
    from_schedule: bool = False
    started_at: datetime
    finished_at: Optional[datetime] = None
    waiting_expires_at: Optional[datetime] = None
    steps: list[TimelineStep] = Field(default_factory=list)


class SessionTimeline(BaseModel):
    session_id: str
    runs: list[TimelineRun]


class ChatRequest(BaseModel):
    agent_id: str
    message: str
    session_id: Optional[str] = None
    # Required when creating a new session (session_id omitted).
    user_key: Optional[str] = None
    # Session language when creating via /chat: zh / en / ja.
    locale: str = "zh"
    # Template vars for Agent system_prompt when creating a session via /chat.
    prompt_vars: dict[str, Any] = Field(default_factory=dict)
    # Bind local Bridge when creating a session via /chat.
    client_device_id: Optional[str] = None
    # Optional per-turn override; falls back to agent.model_profile_id / default.
    model_profile_id: Optional[str] = None


class RunResumeRequest(BaseModel):
    call_id: str
    """Ask User: free text / selected option."""
    answer: Optional[str] = None
    selected_option: Optional[str] = None
    """Approval: approve | edit | reject. Omit for Ask User resumes."""
    decision: Optional[Literal["approve", "edit", "reject"]] = None
    """Required when decision=edit; replaces the pending tool arguments."""
    edited_arguments: Optional[dict[str, Any]] = None
    """When true with approve/edit: skip further tool approvals for the rest of this Run."""
    grant_run: bool = False


class SessionQueueItemOut(BaseModel):
    id: str
    session_id: str
    seq: int
    message: str
    model_profile_id: Optional[str] = None
    status: str
    run_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionQueueOut(BaseModel):
    session_id: str
    items: list[SessionQueueItemOut] = Field(default_factory=list)
