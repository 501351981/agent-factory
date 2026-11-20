from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, LargeBinary, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ModelProfileRecord(SQLModel, table=True):
    __tablename__ = "model_profiles"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    provider: str = "custom"
    base_url: str
    api_key: str = Field(default="", sa_column=Column(Text, nullable=False))
    model_name: str
    temperature: float = 0.3
    context_window_tokens: int = 128000
    reserve_output_tokens: int = 8192
    # CNY per 1M tokens; 0 = unset (cost shows as 0 until configured)
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    is_default: bool = False
    enabled: bool = True
    remark: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentRecord(SQLModel, table=True):
    __tablename__ = "agents"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    system_prompt: str = Field(sa_column=Column(Text, nullable=False))
    # JSON map: {"zh": "...", "en": "...", "ja": "..."}; empty → use system_prompt as zh.
    system_prompts_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    model: Optional[str] = None  # legacy free-text; prefer model_profile_id
    model_profile_id: Optional[str] = Field(default=None, index=True)
    tools_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    skills_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    runtime_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    # Empty-state / greeting UI (shown on new chat blank page).
    logo_url: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    welcome_title: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    welcome_subtitle: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    # JSON string array, e.g. ["帮我写周报", "解释这段代码"]
    suggested_prompts_json: Optional[str] = Field(
        default="[]", sa_column=Column(Text, nullable=True)
    )
    # Chat UI theme tokens JSON, e.g. {"preset":"dark","accent":"#2563eb"}
    ui_theme_json: Optional[str] = Field(
        default="{}", sa_column=Column(Text, nullable=True)
    )
    # Business credentials (per agent). SK stored hashed; plaintext shown once on rotate.
    access_key: Optional[str] = Field(default=None, index=True)
    secret_key_hash: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    title: Optional[str] = None
    user_key: Optional[str] = Field(default=None, index=True)
    client_device_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="active", index=True)  # active | archived
    # user = visible chat; system = hidden delegate child session
    visibility: str = Field(default="user", index=True)
    # When set, this system session was created for a schedule job (Phase H).
    schedule_job_id: Optional[str] = Field(default=None, index=True)
    locale: str = Field(default="zh", index=True)  # zh | en | ja
    # Instantiated from Agent.system_prompt template at session create time.
    system_prompt: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    prompt_vars_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    # Soft clear: items/messages with created_at <= this are hidden from APIs & LLM context.
    history_cleared_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class MemoryRecord(SQLModel, table=True):
    __tablename__ = "memories"

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    # legacy column; product memory is always cross-session ("longterm")
    scope: str = Field(default="longterm", index=True)
    # profile | playbook | detail
    kind: str = Field(default="profile", index=True)
    key: str = Field(index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    user_key: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    active: bool = Field(default=True, index=True)
    pinned: bool = Field(default=False, index=True)
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    source_run_id: Optional[str] = Field(default=None, index=True)
    last_accessed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    deleted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class SessionQueueItemRecord(SQLModel, table=True):
    """FIFO pending user messages for a session (server-side queue)."""

    __tablename__ = "session_queue_items"

    id: str = Field(default_factory=new_id, primary_key=True)
    session_id: str = Field(index=True)
    seq: int = Field(sa_column=Column(Integer, nullable=False))
    message: str = Field(sa_column=Column(Text, nullable=False))
    model_profile_id: Optional[str] = Field(default=None, index=True)
    # queued | dispatched | cancelled
    status: str = Field(default="queued", index=True)
    run_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RunRecord(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(default_factory=new_id, primary_key=True)
    session_id: str = Field(index=True)
    status: str = Field(default="running", index=True)
    # queued | running | waiting_human | completed | failed | cancelled | limit_reached
    user_input: str = Field(sa_column=Column(Text, nullable=False))
    model_name: Optional[str] = None
    rounds_used: int = 0
    finish_reason: Optional[str] = None
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    # When status=waiting_human: auto-cancel after this time (compute released).
    waiting_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # Multi-agent delegation (Phase G). Root runs leave these null / 0.
    parent_run_id: Optional[str] = Field(default=None, index=True)
    root_session_id: Optional[str] = Field(default=None, index=True)
    delegation_call_id: Optional[str] = Field(default=None, index=True)
    delegation_depth: int = Field(default=0)
    # HITL: when "run", subsequent require_approval tools in this Run skip the gate.
    tool_approval_grant: Optional[str] = Field(default=None, index=False)
    started_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class AgentTeamRecord(SQLModel, table=True):
    """Lead + member agents for hierarchical delegation."""

    __tablename__ = "agent_teams"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    lead_agent_id: str = Field(index=True)
    # JSON list: [{"agent_id": "...", "role": "pm"}]
    members_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    max_depth: int = 1
    max_parallel: int = 3
    max_child_rounds: int = 8
    result_max_chars: int = 4000
    allow_async: bool = True
    enabled: bool = True
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BoardProjectRecord(SQLModel, table=True):
    """Shared workboard for one lead (user-facing) session."""

    __tablename__ = "board_projects"

    id: str = Field(default_factory=new_id, primary_key=True)
    lead_session_id: str = Field(index=True, unique=True)
    team_id: Optional[str] = Field(default=None, index=True)
    title: str = "Project Board"
    user_key: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)  # active | archived
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BoardArtifactRecord(SQLModel, table=True):
    """One artifact on a project board (PRD, report, …)."""

    __tablename__ = "board_artifacts"
    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_board_artifacts_path"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(index=True)
    path: str = Field(index=True)
    title: str = ""
    content: str = Field(default="", sa_column=Column(Text, nullable=False))
    version: int = 1
    author_agent_id: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)  # active | archived
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentScheduleRecord(SQLModel, table=True):
    """Persisted agent timer / cron job (Phase H)."""

    __tablename__ = "agent_schedules"

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    user_key: str = Field(default="", index=True)
    created_from_session_id: str = Field(default="", index=True)
    created_by_run_id: Optional[str] = Field(default=None, index=True)
    name: str = ""
    schedule_kind: str = Field(default="at", index=True)  # at | every | cron
    run_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    every_ms: Optional[int] = Field(default=None)
    cron_expr: Optional[str] = Field(default=None)
    timezone: str = "Asia/Shanghai"
    session_target: str = Field(default="origin")  # origin | latest | session_id
    session_id: Optional[str] = Field(default=None, index=True)
    run_mode: str = Field(default="assistant_message")
    # assistant_message | synthetic_user | isolated_session
    payload_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="active", index=True)
    # active | paused | completed | cancelled | failed
    delete_after_run: bool = True
    next_run_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    last_run_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = 0
    last_fired_session_id: Optional[str] = Field(default=None, index=True)
    last_fired_run_id: Optional[str] = Field(default=None, index=True)
    claim_token: Optional[str] = Field(default=None, index=True)
    claimed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ItemRecord(SQLModel, table=True):
    __tablename__ = "items"

    id: str = Field(default_factory=new_id, primary_key=True)
    run_id: str = Field(index=True)
    session_id: str = Field(index=True)
    seq: int = Field(sa_column=Column(Integer, nullable=False))
    type: str = Field(index=True)
    # user_message | reasoning | assistant_message | tool_call | tool_result
    # | system_notice | context_summary
    call_id: Optional[str] = Field(default=None, index=True)
    name: Optional[str] = None
    content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    payload_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    visibility: str = Field(default="user")  # user | debug
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class MessageRecord(SQLModel, table=True):
    """Legacy flat messages. Kept for migration/compat reads."""

    __tablename__ = "messages"

    id: str = Field(default_factory=new_id, primary_key=True)
    session_id: str = Field(index=True)
    role: str
    content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    tool_call_id: Optional[str] = None
    tool_calls_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class LlmUsageRecord(SQLModel, table=True):
    """One row per upstream LLM call (token usage + estimated CNY cost)."""

    __tablename__ = "llm_usage"

    id: str = Field(default_factory=new_id, primary_key=True)
    session_id: str = Field(index=True)
    run_id: str = Field(index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    model_profile_id: Optional[str] = Field(default=None, index=True)
    model_name: str = Field(default="", index=True)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    # api = upstream usage; estimate = chars heuristic fallback
    source: str = Field(default="api", index=True)
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    estimated_cost: float = 0.0
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SkillPackageRecord(SQLModel, table=True):
    """Per-agent skill package. Catalog still lives in Agent.skills[]."""

    __tablename__ = "skill_packages"
    __table_args__ = (
        UniqueConstraint("agent_id", "name", name="uq_skill_packages_agent_name"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    name: str = Field(index=True)
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    version: Optional[str] = None
    source: str = Field(default="upload", index=True)  # upload | hub | chat
    status: str = Field(default="active", index=True)  # active | disabled
    total_bytes: int = 0
    file_count: int = 0
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PlatformSkillPackageRecord(SQLModel, table=True):
    """Factory-wide skill library (e.g. skill-creator). Agents mount by reference."""

    __tablename__ = "platform_skill_packages"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    version: Optional[str] = None
    source: str = Field(default="upload", index=True)  # upload | builtin
    status: str = Field(default="active", index=True)  # active | disabled
    total_bytes: int = 0
    file_count: int = 0
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentSkillMountRecord(SQLModel, table=True):
    """Agent enables a platform skill package."""

    __tablename__ = "agent_skill_mounts"
    __table_args__ = (
        UniqueConstraint("agent_id", "package_id", name="uq_agent_skill_mounts"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    package_id: str = Field(index=True)  # platform_skill_packages.id
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class KbDocumentRecord(SQLModel, table=True):
    """Knowledge-base document, always nested under an Agent.

    The folder tree is derived from ``path`` (e.g. ``售后/退货/退货政策.md``), so
    no separate folder table is needed: a folder exists while it holds documents.

    Inside one Agent there are two kinds of document:
    * ``scope="agent"`` — shared by every session of that Agent.
    * ``scope="user"``  — owned by one end user's ``user_key`` of that Agent.
    """

    __tablename__ = "kb_documents"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "scope", "user_key", "path", name="uq_kb_documents_path"
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    agent_id: str = Field(index=True)
    # agent = shared within the Agent; user = one end user of that Agent
    scope: str = Field(default="agent", index=True)
    # user_key when scope=user; empty when scope=agent
    user_key: str = Field(default="", index=True)
    path: str = Field(index=True)
    title: str = ""
    # Generated by the factory at upload time; ``summary_source`` records how.
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    summary_source: str = "auto"  # auto | llm | manual
    text_content: str = Field(default="", sa_column=Column(Text, nullable=False))
    size_bytes: int = 0
    sha256: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)  # active | disabled
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SkillPackageFileRecord(SQLModel, table=True):
    """Files for agent or platform packages (package_id points at either table)."""

    __tablename__ = "skill_package_files"
    __table_args__ = (
        UniqueConstraint("package_id", "path", name="uq_skill_package_files_path"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    package_id: str = Field(index=True)
    path: str = Field(index=True)
    kind: str = Field(default="doc", index=True)  # doc | script | asset
    is_binary: bool = False
    text_content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    blob: Optional[bytes] = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    size_bytes: int = 0
    sha256: str = ""
    mode: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
