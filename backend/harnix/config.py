"""Validated application settings (environment variables, prefix HARNIX_ or FORGE_)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised for missing/invalid configuration; message is user-actionable."""


# `.env` is looked up in the repository root, then backend/, then the working directory (later wins),
# so it is found no matter where the backend is started from.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILES = tuple(
    str(p) for p in (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env", Path(".env").resolve())
)


def _load_and_sync_env():
    """Sync HARNIX_ and legacy FORGE_ environment variables across runtime and .env files."""
    for env_path in ENV_FILES:
        p = Path(env_path)
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k.startswith("FORGE_"):
                        hk = "HARNIX_" + k[6:]
                        if hk not in os.environ:
                            os.environ[hk] = v
                    elif k.startswith("HARNIX_"):
                        fk = "FORGE_" + k[7:]
                        if fk not in os.environ:
                            os.environ[fk] = v
            except Exception:
                pass
    for k, v in list(os.environ.items()):
        if k.startswith("FORGE_"):
            hk = "HARNIX_" + k[6:]
            if hk not in os.environ:
                os.environ[hk] = v
        elif k.startswith("HARNIX_"):
            fk = "FORGE_" + k[7:]
            if fk not in os.environ:
                os.environ[fk] = v


_load_and_sync_env()

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5.4-nano", "scripted": "scripted"}
_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def _unset(key: str | None) -> bool:
    """Empty or an obvious placeholder copied from .env.example."""
    k = (key or "").strip().lower()
    return not k or k.startswith("your-") or k.endswith("-here")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNIX_", env_file=ENV_FILES, extra="ignore")

    # model provider
    provider: Literal["anthropic", "openai", "scripted"] = "anthropic"
    model: str | None = None  # defaults per provider, see model_name
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY", repr=False)
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY", repr=False)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = (
        None  # OpenAI reasoning models
    )
    scripted_file: str | None = None  # JSON script for provider=scripted (mock) runs
    model_timeout_seconds: float = 120.0
    model_max_tokens: int = 16000
    # pricing (USD per million tokens). Cost is only estimated and reported when both are set; nothing is hardcoded.
    # Spending is measured, never limited.
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    # Removed: dollar and token limits no longer stop anything. They are still read here only so that an old
    # .env can be told so at startup (see ignored_limits()).
    spend_limit_usd: float | None = Field(default=None, repr=False)
    max_cost_per_task_usd: float | None = Field(default=None, repr=False)
    max_total_tokens: int | None = Field(default=None, repr=False)

    # persistence
    database_url: str = "sqlite:///./harnix.db"

    # execution
    execution_mode: Literal["docker", "local"] = "docker"
    docker_image: str = "harnix-sandbox:latest"
    docker_network: str = "none"
    docker_memory: str = "1g"
    docker_cpus: str = "2"
    workspace_root: str = "./.harnix/workspaces"
    allowed_repo_roots: str = (
        ""  # os.pathsep-separated absolute directories; "*" = any folder (never a drive root)
    )
    max_repo_copy_mb: int = 500
    images_enabled: bool = True  # FORGE_IMAGES_ENABLED: accept images attached to a task
    max_images: int = 5  # per task
    max_image_bytes: int = 5 * 1024 * 1024  # per image, decoded
    max_concurrent_tasks: int = 2

    # limits
    max_iterations: int = 30
    max_duration_seconds: int = 1800
    max_tool_calls: int = 200
    max_recovery_attempts: int = 2
    tool_timeout_seconds: int = 120
    max_output_bytes: int = 20_000
    max_read_bytes: int = 100_000
    max_write_bytes: int = 200_000

    # knowledge graph (Neo4j). Off unless enabled; the harness works without it and says so in the task events.
    graph_enabled: bool = False
    neo4j_uri: str = "bolt://127.0.0.1:7687"  # use neo4j+s:// (TLS) for anything that is not this machine
    neo4j_user: str = "neo4j"
    # shared with docker compose; repr=False keeps it out of tracebacks, pytest output and logs
    neo4j_password: str | None = Field(default=None, validation_alias="NEO4J_PASSWORD", repr=False)
    neo4j_database: str = "neo4j"
    graph_index_timeout_seconds: int = (
        60  # budget for indexing inside a task; the rest resumes on the next run
    )
    graph_background_index_timeout_seconds: int = 3600  # budget for `POST /api/graph/index` (pre-indexing)
    graph_max_files: int = 100_000  # files indexed per repository (more are skipped and reported)
    graph_max_parse_bytes: int = 1_000_000  # larger source files are recorded but not parsed
    graph_context_max_chars: int = 6000  # size of the <knowledge_graph> block in the first message
    graph_query_timeout_seconds: int = 10  # per query: transaction timeout on the server
    graph_query_max_results: int = 25  # rows returned by one graph_query call

    # reasoning ("thinking") depth: default for tasks that do not choose one; xhigh = "extra high"
    thinking_level: Literal["low", "medium", "high", "xhigh"] = "medium"
    openai_api: Literal["chat", "responses"] = (
        "chat"  # responses = OpenAI Responses API (reasoning with tools)
    )

    # context management (token counts are estimates: characters / 4, as opencode does)
    context_window_tokens: int = 200_000  # the model's window; compaction aims to stay below it
    compaction_enabled: bool = True
    compaction_reserve_tokens: int = 0  # 0 = keep 15% of the window (at least 16k) free, as opencode does
    compaction_keep_tokens: int = 15_000  # recent conversation kept verbatim beside the summary
    prune_protect_tokens: int = 40_000  # the newest tool output (this many tokens) is never pruned
    prune_minimum_tokens: int = 20_000  # prune only when at least this much can be reclaimed

    # skills and sub-agents
    skills_dirs: str = (
        ""  # os.pathsep-separated directories of operator skills (each skill: <dir>/<id>/SKILL.md)
    )
    repo_skills: bool = (
        False  # also load skills shipped inside the target repository (untrusted; off by default)
    )
    agents_dir: str = ""  # directory of custom sub-agent definitions (<id>.md)
    subagent_max_iterations: int = 15  # per sub-agent run, within the task's overall budget
    subagent_max_background: int = 3  # read-only sub-agents running in parallel threads at once
    subagent_max_total: int = 20  # sub-agents started per task (foreground + background)

    # project memory (persists across tasks, per repository)
    memory_enabled: bool = True
    memory_write: Literal["approve", "allow", "deny"] = "approve"  # the agent's `remember` tool
    memory_context_chars: int = 4000
    memory_max_entries: int = 200

    # MCP (Model Context Protocol) servers, configured in mcp.json like Claude Code's
    mcp_enabled: bool = True
    mcp_config: str = ""  # os.pathsep-separated mcp.json files (trusted); empty = ~/.forge/mcp.json
    mcp_trust_project: bool = False  # also use <repo>/.mcp.json: it is repository text and starts programs
    mcp_start_timeout_seconds: int = 30  # per server: start + initialize + tools/list

    # host terminal: real commands and long-running processes on this machine, outside Docker (opt-in)
    terminal: Literal["off", "host"] = "off"
    terminal_max_processes: int = 5  # background processes running at once

    # knowledge base (BRDs, requirements, tickets, design, code, tests) with hybrid retrieval through MCP
    knowledge_enabled: bool = True
    embedding_provider: Literal["auto", "openai", "hash"] = (
        "auto"  # auto = openai when OPENAI_API_KEY is set, else the hash MOCK
    )
    embedding_model: str = "text-embedding-3-small"
    knowledge_vector: Literal["python", "pgvector"] = (
        "python"  # pgvector needs PostgreSQL with the vector extension
    )
    knowledge_roots: str = ""  # os.pathsep-separated directories POST /api/knowledge/ingest may read; empty = any existing folder; set = only these plus FORGE_ALLOWED_REPO_ROOTS
    knowledge_acl: str = Field(
        default="", repr=False
    )  # JSON {"<token>": ["project", ...] | ["*"]}; empty = every token sees every project (POC)
    knowledge_require_sufficient: bool = (
        True  # a task whose context bundle is insufficient stops and asks instead of guessing
    )
    # index the task's repository as code/test knowledge before building its context (unchanged files cost nothing)
    # weight of keyword matches against vector matches in search (vector = 1.0). Tune it with `forge knowledge eval`:
    # documents that share a lot of boilerplate (templated stories) do better with a smaller value
    knowledge_keyword_weight: float = 0.5
    # a task that names no id needs at least one requirement this similar (cosine) to it, else its context is
    # "insufficient: nothing relevant" instead of the nearest unrelated stories. Depends on the embedding model:
    # unset = 0.28 for OpenAI text-embedding-3-* (measured on two backlogs), off for the hash mock; 0 = off
    knowledge_min_relevance: float | None = Field(default=None, ge=0, le=1)
    knowledge_index_repo: bool = True
    knowledge_index_max_files: int = 500  # files indexed per task; the rest is reported, not silently dropped
    knowledge_max_doc_bytes: int = 5_000_000  # a document larger than this is skipped (and reported), not cut
    knowledge_max_chunks: int = 5000  # chunks indexed per source; more are cut and reported, never silently

    # policy
    approval_policy: Literal["sensitive", "strict", "deny_sensitive"] = "sensitive"
    approval_timeout_seconds: int = 900

    # api / ops
    log_level: str = "INFO"
    api_token: str | None = Field(default=None, repr=False)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def ignored_limits(self) -> list[str]:
        """Names of removed spend/token limits that are still set (they stop nothing; cost is only measured)."""
        return [
            f"FORGE_{n.upper()}"
            for n in ("spend_limit_usd", "max_cost_per_task_usd", "max_total_tokens")
            if getattr(self, n) is not None
        ]

    @model_validator(mode="after")
    def _check(self) -> Settings:
        for name in ("max_iterations", "max_duration_seconds", "tool_timeout_seconds", "max_output_bytes"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"FORGE_{name.upper()} must be > 0")
        if self.graph_enabled and _unset(self.neo4j_password):
            raise ConfigError(
                "FORGE_GRAPH_ENABLED=true needs NEO4J_PASSWORD (the same value docker compose uses for Neo4j). "
                "Set it in .env (see .env.example), or set FORGE_GRAPH_ENABLED=false."
            )
        if self.knowledge_vector == "pgvector" and not self.database_url.startswith("postgresql"):
            raise ConfigError(
                "FORGE_KNOWLEDGE_VECTOR=pgvector needs PostgreSQL: set FORGE_DATABASE_URL=postgresql+psycopg://..."
            )
        if self.knowledge_acl.strip():
            try:
                acl = json.loads(self.knowledge_acl)
                if not isinstance(acl, dict) or not all(isinstance(v, list) and v for v in acl.values()):
                    raise ValueError("expected an object mapping each token to a non-empty list of projects")
            except ValueError as e:
                raise ConfigError(f"FORGE_KNOWLEDGE_ACL is not valid: {e}") from None
        for name in (
            "context_window_tokens", "compaction_keep_tokens", "prune_protect_tokens", "prune_minimum_tokens",
            "subagent_max_iterations", "subagent_max_background", "subagent_max_total", "memory_context_chars", "memory_max_entries",
            "graph_index_timeout_seconds", "graph_background_index_timeout_seconds", "graph_max_files",
            "graph_max_parse_bytes", "graph_context_max_chars", "graph_query_timeout_seconds",
            "graph_query_max_results", "mcp_start_timeout_seconds",
            "knowledge_index_max_files", "terminal_max_processes", "knowledge_max_doc_bytes", "knowledge_max_chunks",
        ):  # fmt: skip
            if getattr(self, name) <= 0:
                raise ConfigError(f"FORGE_{name.upper()} must be > 0")
        return self

    @property
    def compaction_threshold_tokens(self) -> int:
        """Estimated context size at which the conversation is compacted."""
        reserve = self.compaction_reserve_tokens or max(int(self.context_window_tokens * 0.15), 16_000)
        return max(self.context_window_tokens - reserve, self.context_window_tokens // 2)

    @property
    def knowledge_acl_map(self) -> dict[str, list[str]]:
        if not self.knowledge_acl.strip():
            return {}
        data = json.loads(self.knowledge_acl)
        return {str(k): [str(p) for p in v] for k, v in data.items()}

    @property
    def min_relevance(self) -> float:
        if self.knowledge_min_relevance is not None:
            return self.knowledge_min_relevance
        return 0.28 if self.resolved_embedding_provider == "openai" else 0.0

    @property
    def knowledge_root_paths(self) -> list[Path]:
        raw = [p for p in self.knowledge_roots.split(os.pathsep) if p.strip()]
        # the authorized repository roots are always readable: a task may index the repository it works on
        return [*(Path(p).expanduser().resolve() for p in raw), *self.repo_roots]

    @property
    def resolved_embedding_provider(self) -> str:
        if self.embedding_provider == "auto":
            return "openai" if not _unset(self.openai_api_key) else "hash"
        return self.embedding_provider

    @property
    def mcp_config_paths(self) -> list[Path]:
        raw = [p for p in self.mcp_config.split(os.pathsep) if p.strip()] or [
            str(Path.home() / ".forge" / "mcp.json")
        ]
        return [Path(p).expanduser().resolve() for p in raw]

    @property
    def skill_dirs(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.skills_dirs.split(os.pathsep) if p.strip()]

    @property
    def model_name(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]

    @property
    def prices_known(self) -> bool:
        return self.price_input_per_mtok is not None and self.price_output_per_mtok is not None

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float | None:
        """Estimated cost (input tokens billed at the full rate: conservative, ignores cache discounts)."""
        if self.price_input_per_mtok is None or self.price_output_per_mtok is None:
            return None
        return (
            input_tokens / 1e6 * self.price_input_per_mtok + output_tokens / 1e6 * self.price_output_per_mtok
        )

    @property
    def repo_roots(self) -> list[Path]:
        # "*" = any folder (explicit opt-in; policies.paths.ANY_ROOT); anything else is a directory that is resolved
        raw = [p.strip() for p in self.allowed_repo_roots.split(os.pathsep) if p.strip()]
        return [Path(p) if p == "*" else Path(p).resolve() for p in raw]

    @property
    def workspace_root_path(self) -> Path:
        return Path(self.workspace_root).resolve()

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def provider_problem(self) -> str | None:
        """Return an actionable message if the provider cannot be used, else None."""
        name = self.model_name
        if self.provider == "anthropic":
            if _unset(self.anthropic_api_key):
                return "ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example) or export it, then restart the backend."
            if name.startswith(_OPENAI_PREFIXES):
                return f"FORGE_MODEL={name!r} is an OpenAI model but FORGE_PROVIDER=anthropic. Set FORGE_PROVIDER=openai or choose a Claude model."
        if self.provider == "openai":
            if _unset(self.openai_api_key):
                return "OPENAI_API_KEY is not set. Put it in .env (see .env.example) or export it, then restart the backend."
            if name.startswith("claude"):
                return f"FORGE_MODEL={name!r} is an Anthropic model but FORGE_PROVIDER=openai. Set FORGE_MODEL to an OpenAI model such as gpt-5.4-nano."
        if self.provider == "scripted" and not self.scripted_file:
            return "FORGE_PROVIDER=scripted requires FORGE_SCRIPTED_FILE=<path to JSON script>."
        return None

    def secret_values(self) -> list[str]:
        vals = [self.anthropic_api_key, self.openai_api_key, self.api_token, self.neo4j_password]
        return [v for v in vals if v]


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ConfigError:
        raise
    except Exception as e:  # pydantic ValidationError -> actionable message
        raise ConfigError(f"Invalid configuration: {e}") from e
