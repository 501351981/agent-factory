from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ToolDef(BaseModel):
    name: str
    description: str = ""
    execution: Literal["remote_http", "client", "builtin"] = "client"
    endpoint: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """When true, pause for human approve/edit/reject before executing (client/remote_http)."""
    require_approval: bool = False


class SkillDef(BaseModel):
    """Catalog entry only — skill bodies live on the client and load on demand."""

    name: str
    description: str = ""

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


class ContextPolicy(BaseModel):
    mode: Literal["auto", "off"] = "auto"
    soft_ratio: float = 0.70
    hard_ratio: float = 0.85
    keep_last_runs: int = 4
    tool_result_max_chars: int = 12000
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
    max_playbook_chars: int = 200
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


class AgentRuntimeConfig(BaseModel):
    max_tool_rounds: int = 8
    on_max_rounds: Literal["summarize", "stop"] = "summarize"
    tool_timeout_seconds: float = 30.0
    run_timeout_seconds: float = 300.0
    parallel_tool_calls: bool = False
    tool_result_max_chars: int = 12000  # legacy; prefer context_policy.tool_result_max_chars
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)


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


class EmbedTokenRequest(BaseModel):
    user_key: Optional[str] = None
    ttl_seconds: Optional[int] = None


class EmbedTokenOut(BaseModel):
    embed_token: str
    agent_id: str
    user_key: Optional[str] = None
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
    locale: str = "zh"
    """Instantiated Agent system_prompt at session creation (template → resolved)."""
    system_prompt: Optional[str] = None
    """Prompt template variables passed when the session was created."""
    prompt_vars: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


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
