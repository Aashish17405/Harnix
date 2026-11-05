"""Typed request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from harnix.policies.commands import CommandSyntaxError, parse_command

UtcDatetime = Annotated[datetime, PlainSerializer(lambda d: d.isoformat() + "Z", return_type=str)]


class FileRef(BaseModel):
    """A file (optionally a line range) the user pointed at. The backend reads it from the isolated copy."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    start_line: int | None = Field(None, ge=1)
    end_line: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _range(self) -> FileRef:
        if self.end_line is not None and self.start_line is None:
            raise ValueError("end_line needs start_line")
        if self.end_line is not None and self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        return self


class ImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("", max_length=200, description="File name, for display only")
    media_type: str = Field("", max_length=64, description="Optional; the real type is read from the bytes")
    data: str = Field(
        min_length=1,
        max_length=30_000_000,
        description="The image file's bytes, base64-encoded (no data: prefix)",
    )


class ImageInfo(BaseModel):
    index: int
    name: str
    media_type: str
    size: int
    width: int | None = None
    height: int | None = None


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_path: str = Field(
        min_length=1, max_length=1000, description="Directory inside an authorized repository root"
    )
    instructions: str = Field(min_length=1, max_length=20000)
    title: str | None = Field(None, max_length=200)
    acceptance_criteria: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    verification_commands: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )
    references: list[FileRef] = Field(default_factory=list, max_length=10)
    thinking: Literal["low", "medium", "high", "xhigh"] | None = Field(
        None, description="Reasoning depth (xhigh = extra high). Default: FORGE_THINKING_LEVEL"
    )
    skills: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]] = Field(
        default_factory=list, max_length=10, description="Skill ids whose instructions are loaded up front"
    )
    images: list[ImageIn] = Field(
        default_factory=list, max_length=10, description="Images the model should look at"
    )
    session_id: str | None = Field(
        None,
        pattern=r"^[0-9a-f]{8,32}$",
        description="Continue this session: the model keeps the earlier conversation and the workspace keeps the earlier changes",
    )
    knowledge_project: str | None = Field(
        None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description="Retrieve this knowledge project's BRDs, requirements, tickets, design, code and tests for the task",
    )
    max_iterations: int | None = Field(None, ge=1, description="Lower than the server limit only")
    max_duration_seconds: int | None = Field(None, ge=10, description="Lower than the server limit only")

    @field_validator("verification_commands")
    @classmethod
    def _commands_parse(cls, v: list[str]) -> list[str]:
        for c in v:
            try:
                parse_command(c)
            except CommandSyntaxError as e:
                raise ValueError(f"{c!r}: {e}") from e
        return v


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    repo_path: str
    instructions: str
    acceptance_criteria: list[str]
    verification_commands: list[str]
    references: list[dict[str, Any]] = Field(default_factory=list)
    workspace_path: str | None = None
    thinking: str | None = None
    skills: list[str] = Field(default_factory=list)
    knowledge_project: str | None = None
    status: str
    outcome: str | None
    error: str | None
    parent_task_id: str | None
    provider: str | None
    model: str | None
    max_iterations: int
    max_duration_seconds: int
    iterations: int
    model_calls: int
    tool_calls: int
    tool_failures: int
    policy_denials: int
    recovery_attempts: int
    input_tokens: int
    output_tokens: int
    cancel_requested: bool
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    duration_seconds: float | None = None
    estimated_cost_usd: float | None = None
    apply_state: str | None = None  # applied | reverted | None (never applied)
    session_id: str | None = None
    turn: int = 1
    discarded: bool = False  # rolled back: ignored when the session continues
    has_checkpoint: bool = False  # the workspace can be put back to how it was after this turn
    images: list[ImageInfo] = Field(default_factory=list)

    @field_validator("references", "skills", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return v or []


class TaskDetail(TaskOut):
    report: dict[str, Any] | None = None


class TaskList(BaseModel):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    ts: UtcDatetime
    type: str
    data: dict[str, Any]


class VerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phase: str
    attempt: int
    name: str
    kind: str
    command: str | None
    status: str
    exit_code: int | None
    duration_seconds: float
    output: str
    note: str | None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    status: str
    note: str | None
    created_at: UtcDatetime
    decided_at: UtcDatetime | None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=500)


class ChangedFile(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int


class DiffOut(BaseModel):
    diff: str
    files: list[ChangedFile]
    live: bool


class FileContentOut(BaseModel):
    path: str
    version: str  # baseline (as the task started) | final (as the task left it)
    exists: bool  # False: the file did not exist in that version (added / deleted)
    binary: bool
    truncated: bool
    size: int = 0  # bytes of that version (0 when it does not exist)
    content: str


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = Field(False, description="Must be true to modify the repository")
    dry_run: bool = Field(False, description="Preview the files that would change; writes nothing")


class AppliedFile(BaseModel):
    path: str
    status: str  # A | M | D


class ApplyOut(BaseModel):
    applied: bool
    dry_run: bool
    files: list[AppliedFile]
    verified: bool  # False: the task finished but was not independently verified


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody


class GraphIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_path: str = Field(
        min_length=1, max_length=1000, description="Directory inside an authorized repository root"
    )


class GraphJob(BaseModel):
    repo_id: str
    state: str  # running | done | failed
    stage: str | None = None
    progress: int = 0
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    report: dict[str, Any] | None = None


class GraphStatus(BaseModel):
    enabled: bool
    available: bool
    detail: str
    repo_id: str | None = None
    indexed: bool | None = None
    graph: dict[str, Any] | None = None
    job: GraphJob | None = None


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    source: str
    listed: bool  # offered to the model (False: disable-model-invocation, only when a task names it)


class SkillList(BaseModel):
    items: list[SkillOut]
    problems: list[str]
    repo_skills: bool  # whether skills inside target repositories are also loaded at run time


class AgentOut(BaseModel):
    id: str
    description: str
    tools: list[str] | None  # None = every tool the task has (minus spawn_agent / remember)
    thinking: str | None
    max_iterations: int | None
    source: str


class AgentList(BaseModel):
    items: list[AgentOut]
    problems: list[str]


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_path: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=500)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content: str
    source: str
    task_id: str | None
    created_at: UtcDatetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    repo_path: str
    archived: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
    turns: int = 0  # turns that are still part of the conversation (rolled-back ones are not counted)
    last_status: str | None = None
    last_outcome: str | None = None
    last_prompt: str | None = None


class SessionDetail(SessionOut):
    items: list[TaskDetail] = Field(
        default_factory=list, description="Every turn in order, rolled-back ones included (`discarded`)"
    )


class SessionList(BaseModel):
    items: list[SessionOut]
    total: int
    limit: int
    offset: int


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    archived: bool | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: int = Field(
        ge=0, description="Go back to the end of this turn (0 = before any turn, the session's start)"
    )
    confirm: bool = Field(False, description="Must be true to roll back")
    dry_run: bool = Field(False, description="Preview what would be rolled back; changes nothing")


class RestoreOut(BaseModel):
    turn: int
    restored: bool
    discarded_tasks: list[str]
    files: list[
        AppliedFile
    ]  # workspace files that change when going back (A = would be removed, D = would come back)
