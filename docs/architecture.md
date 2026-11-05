# Architecture

Harnix is a modular monolith: one backend process (FastAPI), one database, a React UI, and sandboxed command execution. The frontend and backend are independently runnable (the UI only calls `/api`). For the step-by-step runtime behaviour read [../flow.md](../flow.md); for why it is built this way read [../decision.md](../decision.md).

## Modules (`backend/harnix/`)

| Module | Responsibility | Key files |
|---|---|---|
| `api` | HTTP endpoints, request validation, auth, error format, SSE | `app.py`, `schemas.py` |
| `harness` | Lifecycle, agent loop, context, pruning/compaction, skills, sub-agents, project memory, report, state machine | `runner.py`, `agent.py`, `context.py`, `report.py`, `states.py` |
| `models` | Provider-neutral message/tool types, interface, adapters | `base.py`, `openai_provider.py`, `anthropic_provider.py`, `scripted.py` (labelled mock), `factory.py` |
| `tools` | Tool schemas, implementations, registry, the single executor | `schemas.py`, `files.py`, `commands.py`, `registry.py`, `executor.py` |
| `policies` | Authorization, command classification, path containment, budgets, approvals, redaction | `policy.py`, `commands.py`, `paths.py`, `budget.py`, `approvals.py`, `redaction.py` |
| `execution` | Workspace isolation, hardened git, repo inspection, sandboxes | `workspace.py`, `git.py`, `project.py`, `sandbox.py` |
| `verification` | Check planning, execution, aggregation into a verdict | `engine.py` |
| `persistence` | SQLAlchemy models, session factory, the only DB access | `models.py`, `db.py`, `store.py` (+ `backend/migrations/`) |
| `observability` | Structured JSON logging (events are persisted by the store) | `logging.py` |
| `evaluations` | Deterministic cases, ground-truth scoring, CLI | `runner.py`, `__main__.py`, `cases/` |
| `graph` | Optional knowledge graph of the repository in Neo4j: extraction (Python `ast`), incremental time-budgeted indexer, name-based import/call resolution, bounded queries, the `<knowledge_graph>` context block, the `graph_query` tool's backend. See [knowledge-graph.md](knowledge-graph.md) | `extract.py`, `walker.py`, `resolve.py`, `indexer.py`, `store.py` (the only Neo4j access), `query.py`, `manager.py` |
| `cli` / `client` | The `forge` command (serve, interactive session, one-shot tasks, apply, doctor) and its stdlib HTTP/SSE client | `cli.py`, `client.py` |
| `execution/apply` | The single, strict path that writes a task's result into the real repository | `apply.py` |

Outside the Python package: `frontend/` (dashboard) and `vscode-extension/` (VS Code front end: `core.ts` API client and event wording, `session.ts` run/approve/apply logic, `extension.ts` + `webview.ts` VS Code glue). All front ends are clients of the same HTTP API; none contains agent logic.

### Dependency direction (no cycles)

```
api ──► harness ──► tools ──► execution
          │           │  └──► policies (paths, commands)        harness/states ◄── persistence
          │           └─────► models.base (ToolSpec)
          ├──► policies ──► persistence (approvals) 
          ├──► verification ──► execution, policies.commands
          └──► models, persistence
```

`policies` and `execution` never import `harness` or `api`. `tools/*` never import `policies.policy`; only `tools/executor.py` does, so a tool cannot run without policy.

## Core interfaces

* `ModelProvider` (`models/base.py`, a `Protocol`): `complete(system, messages, tools, max_tokens, timeout) -> ModelResponse`, raising `ProviderError(retryable=…)`. Messages/tools are provider-neutral dataclasses; each adapter converts them and may keep opaque state in `Message.raw`.
* `Sandbox` (`execution/sandbox.py`, a `Protocol`): `run(argv, workspace, workdir, timeout, cancel, max_output) -> CommandResult`, `available()`. Implementations: `LocalSandbox`, `DockerSandbox`.
* `ToolExecutor.execute(call) -> ToolResult`: the only way an action runs.
* `WorkspaceManager.create(task_id, repo) -> Path`; `VerificationEngine.run/aggregate`; `Store` (persistence): concrete, typed, injected through constructors.

## Data model (`persistence/models.py`)

| Table | Contents |
|---|---|
| `tasks` | definition, limits, status, outcome, error, counters (iterations, calls, tokens, failures, denials, recoveries), report (JSON), final diff, timestamps, `parent_task_id` |
| `events` | append-only, ordered by id: `task_id, ts, type, data(JSON, redacted)` |
| `verification_results` | per check: phase (baseline/final), attempt, kind, command, status, exit code, duration, output, note |
| `approvals` | tool, exact arguments, action hash, reason, status (pending/approved/rejected/expired), note, timestamps |

Schema authority is Alembic (`backend/migrations`). SQLite (WAL) for development/tests and PostgreSQL for deployments use the same models.

### Event types

`task.created`, `task.state`, `task.cancel_requested`, `task.finished`, `workspace.created`, `project.inspected`, `task.references`, `graph.indexed`, `graph.unavailable`, `graph.context`, `skills.available`, `skill.loaded`, `skill.missing`, `agents.problems`, `memory.context`, `memory.saved`, `subagent.started`, `task.images`, `session.continued`, `session.restored`, `workspace.reused`, `subagent.finished`, `subagent.delivered`, `subagent.waiting`, `subagent.stopped`, `context.pruned`, `context.compacted`, `context.compaction_failed`, `context.compaction_deferred`, `model.request`, `model.response`, `tool.proposed`, `tool.authorization`, `tool.result`, `approval.requested`, `approval.decided`, `verification.check`, `verification.result`, `retry.decision`, `error`.

## Task states

`queued → running ⇄ awaiting_approval`, `running → verifying → completed`, `verifying → running` (recovery), and `failed` / `cancelled` from any active state (`queued → cancelled` too). Terminal states have no exits. Defined in `harness/states.py`, enforced by a compare-and-set update.

## Security boundaries

1. **Repository selection**: only directories under `HARNIX_ALLOWED_REPO_ROOTS`.
2. **Workspace**: an isolated copy with a fresh git baseline; all model-supplied paths resolve inside it.
3. **Policy**: allow / approve / deny decided in code (`policies/`), independent of model text.
4. **Execution**: Docker container (no network, no capabilities, non-root, read-only root, only the workspace mounted) or, by explicit opt-in, a scrubbed local process tree.
5. **Verification**: run by the harness, with a baseline, not by the model.
6. **Knowledge graph** (optional): built from the secret-free workspace copy, queried only through fixed parameterized Cypher, holds no source text, and its output is untrusted, redacted, size-capped model input. An unavailable graph degrades the task, never fails it.

See [security.md](security.md) for the threat model and limits.

## Frontend (`frontend/src/`)

`App.tsx` (hash routing, config/health banners, token prompt) → `TaskForm` + `TaskListView` (home) or `TaskDetail` (per task). `hooks.ts:useTaskLive` combines REST fetches with the SSE stream; `lib.ts` derives the tool-call table, timeline text and parsed diffs from event/DB rows. No state or router libraries.

## Extension points

* New provider: implement `ModelProvider`, register in `models/factory.py`.
* New tool: schema → function → `tools/registry.py` → branch in `policies/policy.py` (unknown tools are denied) → tests.
* New check kind: `verification/engine.py` (decide whether it is blocking).
* Another sandbox backend: implement `Sandbox`, return it from `make_sandbox`.
