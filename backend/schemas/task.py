"""
backend/schemas/task.py
Pydantic v2 schemas for all domain objects.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


class ProviderName(str, Enum):
    GEMINI = "gemini"
    GROK = "grok"
    NVIDIA = "nvidia"
    OLLAMA = "ollama"
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class TaskType(str, Enum):
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_EDIT = "code_edit"
    EXPLANATION = "explanation"
    DEBUGGING = "debugging"
    PLANNING = "planning"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------

class ModelUsage(BaseModel):
    """Token usage and cost tracking for one provider call."""

    provider: ProviderName = ProviderName.UNKNOWN
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def tokens_per_second(self) -> float:
        if self.latency_ms <= 0:
            return 0.0
        return (self.completion_tokens / self.latency_ms) * 1000


class ConversationMessage(BaseModel):
    """A single message in a conversation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str
    provider: Optional[ProviderName] = None
    model: Optional[str] = None
    usage: Optional[ModelUsage] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FileEdit(BaseModel):
    """Represents a proposed file change."""

    path: str
    original_content: Optional[str] = None
    new_content: str
    description: str = ""


# ---------------------------------------------------------------------------
# Task schemas
# ---------------------------------------------------------------------------

class TaskSpec(BaseModel):
    """Specification for a task to be executed."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: TaskType = TaskType.GENERAL
    description: str
    context: Optional[str] = None
    file_paths: list[str] = Field(default_factory=list)
    conversation_history: list[ConversationMessage] = Field(default_factory=list)
    workspace_root: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.2
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ImplementationTask(BaseModel):
    """A decomposed sub-task for implementation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: str
    order: int = 0
    title: str
    description: str
    files_to_modify: list[str] = Field(default_factory=list)
    files_to_create: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    estimated_complexity: str = "medium"  # low / medium / high
    depends_on: list[str] = Field(default_factory=list)  # other task IDs
    status: TaskStatus = TaskStatus.PENDING
    result: Optional["TaskResult"] = None


class TaskResult(BaseModel):
    """Result of executing a task."""

    task_id: str
    status: TaskStatus
    content: Optional[str] = None
    file_edits: list[FileEdit] = Field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[ProviderName] = None
    model: Optional[str] = None
    usage: Optional[ModelUsage] = None
    sub_results: list["TaskResult"] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None


# Resolve forward references
ImplementationTask.model_rebuild()
TaskResult.model_rebuild()


# ---------------------------------------------------------------------------
# API request/response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    workspace_root: Optional[str] = None
    routing_mode: Optional[str] = None
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation_id: str
    message: ConversationMessage
    usage: Optional[ModelUsage] = None


class StreamChunk(BaseModel):
    """A single streaming chunk sent over WebSocket."""

    type: str  # "text" | "usage" | "error" | "done" | "status"
    content: Optional[str] = None
    usage: Optional[ModelUsage] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceSetRequest(BaseModel):
    path: str


class UsageSummary(BaseModel):
    total_requests: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    by_provider: dict[str, dict[str, Any]] = Field(default_factory=dict)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
