from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, Text
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
    locale: str = Field(default="zh", index=True)  # zh | en | ja
    # Instantiated from Agent.system_prompt template at session create time.
    system_prompt: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    prompt_vars_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
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
    started_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
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
