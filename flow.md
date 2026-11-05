# Harnix: code and execution flow

This document follows one task from the HTTP request to the terminal state, naming the file that does each step.
For the reasons behind the design see [decision.md](decision.md); for module boundaries see [docs/architecture.md](docs/architecture.md).

Paths are relative to `backend/harnix/` unless they start with `frontend/`.

## 1. Big picture

```
 Browser (React)                         FastAPI process (single process, modular monolith)
 ┌──────────────┐  REST /api/*   ┌─────────────────────────────────────────────────────────────┐
 │ TaskForm     │ ─────────────► │ api/app.py   validation, HTTP mapping, auth, SSE            │
 │ TaskDetail   │ ◄───── SSE ─── │      │                                                       │
 └──────────────┘                │      ▼                                                       │
                                 │ harness/runner.py  TaskRunner  (one worker thread per task)  │
                                 │      │   owns lifecycle + terminal state + report            │
                                 │      ▼                                                       │
                                 │ harness/agent.py   AgentLoop   (bounded model↔tool loop)     │
                                 │   │            │                                             │
                                 │   ▼            ▼                                             │
                                 │ models/*    tools/executor.py  ToolExecutor                  │
                                 │ (provider)    │ validate → policies/policy.py → approvals    │
                                 │               ▼                                              │
                                 │            tools/*  ──►  execution/sandbox.py (local|docker) │
                                 │                                                              │
                                 │ verification/engine.py  (independent checks, run by harness) │
                                 │ persistence/store.py    (the only DB access; events/state)   │
                                 └─────────────────────────────────────────────────────────────┘
                                            SQLite (dev/test) or PostgreSQL
```

Rules that shape every flow below:

* The model only ever *proposes*. Code in `tools/executor.py` + `policies/` decides, and only the tool layer acts.
* Every state change and decision is written to the `events` table through `Store.add_event`, which redacts secrets. The UI and the SSE stream read those same rows, so what you see is what happened.
* Completion is decided by `verification/engine.py`, never by the model's own claim.

## 2. Task lifecycle (states)

Defined in `harness/states.py`; enforced by a compare-and-set `UPDATE` in `persistence/store.py:Store.transition`.

```
            ┌──────────► cancelled
 queued ──► running ◄──────────────┐
   │           │  ▲                │ (recovery after failed verification)
   │           ▼  │                │
   │   awaiting_approval           │
   │           │                   │
   │           ▼                   │
   │       verifying ──────────────┘
   │           │
   │           ├──► completed   (only reachable from `verifying`)
   └──► cancelled      └──► failed   (also from running / awaiting_approval)
```

`completed` additionally carries an `outcome`: `verified`, or `unverified` (finished, but no test/acceptance check could confirm it). `failed` can carry `verification_failed`.
Terminal states have no exits. A second `POST /run` on a started task loses the compare-and-set and returns HTTP 409.

## 3. Step by step

### 3.1 Create (`POST /api/tasks`) — `api/app.py:create_task`

1. Pydantic (`api/schemas.py:TaskCreate`) rejects unknown fields and shell syntax in `verification_commands`.
2. `policies/paths.py:authorize_repo` resolves the path and requires it to be inside `FORGE_ALLOWED_REPO_ROOTS`.
3. Per-task limits may only *lower* the server limits.
4. `Store.create_task` inserts the row (`queued`) and the `task.created` event.

### 3.2 Start (`POST /api/tasks/{id}/run`) — `harness/runner.py:TaskRunner.start`

1. `preflight()`: provider configured (key present, model matches the provider)? sandbox available (Docker daemon + image, or local)? Otherwise HTTP 503 with the exact fix, and the task stays `queued`. There is no spend check: cost is measured, never a reason to refuse a run.
2. Under a lock: reject with HTTP 429 if `max_concurrent_tasks` are already active; otherwise atomically move `queued → running` and register a cancellation `threading.Event` for the task.
3. Submit `_execute(task_id)` to the thread pool and return HTTP 202.

### 3.3 Setup phase — `TaskRunner._execute`

| Step | Code | What happens |
|---|---|---|
| Provider | `models/factory.py` | Builds the adapter (Anthropic, or the labelled scripted mock). |
| Authorize repo again | `policies/paths.py` | Defense in depth: the check is repeated at execution time. |
| Isolated workspace | `execution/workspace.py:create` | Copies the working tree (including uncommitted work) to `HARNIX_WORKSPACE_ROOT/<task_id>`; skips `.git`, secrets, symlinks, caches; enforces a size cap; then `execution/git.py:init_baseline` makes a *fresh* repo with a baseline commit so every later diff is "what the agent changed". The source repository is never touched. |
| Inspect | `execution/project.py:inspect_project` | Detects Python/Node/Rust/Go, package manager, test/lint/typecheck/build commands, and reads repository instructions (`HARNIX.md`, `AGENTS.md`, etc., truncated, treated as untrusted). |
| References | `harness/references.py:load_references` | Loads the files the user pointed at (`task.references`: `{path, start_line?, end_line?}`) **from the isolated copy** through the same path containment as the tools; secrets, binaries, missing files and over-limit mentions are skipped with a reason. Emits `task.references` (`included` / `skipped`). The `ContextBuilder` later wraps each file in an untrusted `<referenced_file>` block. |
| Knowledge graph | `graph/manager.py:index_for_task` → `graph/indexer.py` | If enabled: index the workspace copy into Neo4j within the time budget (incremental: only changed files are parsed; see `docs/knowledge-graph.md`). Emits `graph.indexed`; on any problem emits `graph.unavailable` and the task continues without the graph. |
| Plan checks | `verification/engine.py:plan_checks` | Explicit `verification_commands` (kind `acceptance`) + detected test/lint/typecheck. Free-text acceptance criteria become `criterion` checks that are always reported as *unavailable* (not machine-checkable). |
| Baseline | `VerificationEngine.run` | Runs the checks before the agent starts, so pre-existing failures are known (stored as phase `baseline`). |
| Graph context | `graph/query.py:RepoGraph.context_block` | After the baseline: seed from attached files, task text and the baseline's failing tests; render a size-capped, redacted `<knowledge_graph>` block for the first message (`graph.context` event). |
| Wire up | `_execute` | Budget, `ToolContext`, `PolicyEngine`, `ApprovalGate`, `ToolExecutor`, `ContextBuilder`, `AgentLoop`. |

### 3.4 The agent loop — `harness/agent.py:AgentLoop.run`

```
loop:
  cancelled?                     → return "cancelled"
  budget.next_iteration()        → iterations / duration / tokens / dollar cost   → return "budget_*"
                                   (cost = tokens × configured prices; cap = min(per-task cap, remaining global budget))
  ContextBuilder.build(history)  → system prompt + messages (elides old tool output only if over the char cap)
  provider.complete(...)         → retry transient ProviderError up to 2x with backoff; fatal → "provider_error"
  record usage + model.response event
  if the response has tool calls:
      for each call: ToolExecutor.execute(call)         (section 3.5)
      append all tool results as one user message
      repeated identical failing call? nudge at 3, abort at 5   (counter resets when a write succeeds)
      continue
  if the text is empty or truncated (max_tokens): count it; 3 in a row → "invalid_responses"
  otherwise the model claims it is done → verify() (section 3.6)
      verdict ok            → return "done"
      verdict failed        → if recovery attempts remain: send the failing output back and loop
                              else return "verification_failed"
```

**Context management, thinking and sub-agents inside the loop.** Before every model call `_manage_context` estimates the request (characters / 4): at half the compaction threshold it *prunes* old tool output (events `context.pruned`), at the threshold it *compacts* the older conversation into a checkpoint summary with one tool-less model call (`context.compacted`; not again until the context has grown, `context.compaction_deferred`). A provider `context_overflow` error compacts once and retries. Every call carries the task's thinking level; `model.response.thinking` records what the provider actually applied. The `spawn_agent` tool runs a child `AgentLoop` (see `harness/subagents.py`) with a fresh context, the profile's tools through the same executor, the shared budget, `max_steps`, no verification; its events carry `agent`, and `subagent.started` / `subagent.finished` bracket it. Setup adds `skills.available`, `skill.loaded`/`skill.missing` (preloaded skills) and `memory.context` events; `remember` emits `memory.saved`.

### 3.5 One tool call — `tools/executor.py:ToolExecutor.execute`

```
tool.proposed event
budget.add_tool_call()                         → "budget_tool_calls"
registry lookup                                → unknown tool  → structured failure
pydantic args_model (extra="forbid")           → invalid args  → structured failure
PolicyEngine.authorize(tool, args)  ───────────► allow | deny | approve        (tool.authorization event)
    deny    → failure result, policy_denials += 1
    approve → ApprovalGate.request(...)  (section 3.7); rejected / expired / cancelled → failure result
tool.run(args, ctx)                            ← always the *validated* args, never the raw model input
    files  → WorkspacePaths.resolve()          (traversal, absolute, symlink/junction escapes rejected)
    git    → execution/git.py                  (hardened flags, fresh repo)
    search → git grep (no Python regex on repo text)
    commands → parse_command (no shell) → Sandbox.run(...)
redact result, bound its size, tool.result event
```

`policies/policy.py` is a pure function of (tool, args, settings). `policies/commands.py` classifies argv: allowlisted validation commands run; destructive/unlisted ones need approval; `sudo`-class commands are always denied; shell metacharacters outside quotes are rejected.

### 3.6 Verification — `TaskRunner._execute:verify`

1. `VerificationEngine.run(checks)` runs every planned check again in the workspace (same sandbox). Each result is `passed`, `failed`, `skipped` or `unavailable`, with command, exit code, duration and output; it is persisted (`verification_results`) and emitted as `verification.check`.
2. `verification/engine.py:aggregate(baseline, final)`:
   * a failed `test`/`acceptance` check is **blocking** even if it also failed at baseline;
   * a failed lint/typecheck check blocks only if it passed at baseline;
   * `verified` requires at least one test/acceptance check to have **passed**; otherwise `unverified`.
3. `failed` verdict + recovery attempts left → status goes `verifying → running`, the failing output is fed to the model, loop continues (`retry.decision` event). Otherwise the loop returns.

### 3.7 Approvals — `policies/approvals.py:ApprovalGate.request`

1. Create an `approvals` row (`pending`) holding the tool, the exact arguments and `sha256(task, tool, args)`; emit `approval.requested`; status → `awaiting_approval`.
2. The worker thread waits (polling the DB, waking on cancellation). Waiting time is excluded from the duration budget.
3. A human calls `POST /api/approvals/{id}/decision` (`approve` | `reject`). The store accepts exactly one decision (`UPDATE … WHERE status='pending'`); a second call is HTTP 409.
4. The gate re-checks the stored hash against the action it is about to run. Approved → execute; anything else (rejected, expired by timeout, cancelled, hash mismatch) → the tool call fails and nothing runs. Status returns to `running`.

An approval therefore authorizes one specific action once; it can never be reused for a different call.

### 3.8 Cancellation — `TaskRunner.cancel`

* `queued` task: moved straight to `cancelled`.
* active task: the DB flag is set and the task's `threading.Event` is set. The event is observed (a) at every loop iteration and before every tool call, (b) inside `execution/sandbox.py:supervise`, which kills the **whole process tree** (`taskkill /T` on Windows, `killpg` elsewhere, plus `docker kill` for containers), (c) while waiting for an approval.
* The worker finalizes the task as `cancelled` with its report and diff. Terminal tasks return HTTP 409.

### 3.8b Applying the result — `POST /api/tasks/{id}/apply` (`api/app.py:apply_task`, `execution/apply.py`)

The only step that writes to your real repository, started explicitly by a person (CLI prompt, extension button, dashboard button).

```
guards: task is `completed` · confirm=true or dry_run · not applied before (no `task.applied` event)
        repo still inside FORGE_ALLOWED_REPO_ROOTS · workspace still exists under FORGE_WORKSPACE_ROOT
plan:   git diff --cached --no-renames --name-status -z   (workspace vs its baseline)  → A / M / D per file
check:  for every file, BEFORE writing anything
          unsafe path (secret-looking, .git, traversal, drive) ......... refuse
          resolves outside the repo (link inside your repo) ............ refuse
          A: path already exists ....................................... conflict
          M/D: your file differs from the baseline blob byte for byte .. conflict ("changed since the task started")
        any conflict → HTTP 409 listing all of them, NOTHING written
write:  copy the raw workspace bytes (never the redacted diff); remember old bytes; on a failed write restore them
record: `task.applied` event with the file list
```

### 3.9 Finalization — `TaskRunner._finalize`

1. Compute the final diff and changed files from the workspace (`execution/git.py`).
2. Map the loop outcome to a terminal status (`completed` / `failed` / `cancelled`) and `outcome`.
3. `harness/report.py:build_report` assembles the structured report (changed files, baseline and final checks, independent verdict, false-claim flag, usage, cost if pricing is configured, known limitations). It is redacted and stored on the task together with the diff.
4. `Store.transition(...)` to the terminal state, `task.finished` event, and the cancellation event is released. The workspace directory is kept on disk so the result can be inspected; nothing is deleted automatically.

If anything in the worker raises, the `finally` block still finalizes the task as `failed`. On server start, `TaskRunner.recover_orphans` marks tasks left active by a crashed process as `failed`.

## 4. How the UI stays live

```
TaskDetail (frontend/src/components/TaskDetail.tsx)
  └─ useTaskLive(taskId)  (frontend/src/hooks.ts)
       ├─ GET /api/tasks/{id}, /verifications, /approvals, /diff      initial load + after each batch of events
       └─ EventSource /api/tasks/{id}/events/stream                    api/app.py:stream_events
              server loop: SELECT events WHERE id > last  → "data: {...}" (poll 0.5s), "event: end" when
              the task is terminal and two polls were quiet; resumes via Last-Event-ID after a reconnect
```

* Timeline, tool-call table and verification views are derived from event/DB rows (`frontend/src/lib.ts`: `deriveToolCalls`, `describeEvent`, `parseDiff`). Nothing in the UI is hardcoded.
* Proposed vs executed vs verified are different event types and are styled differently.
* A 5-second refetch while a task is active is a safety net if the stream stalls.

## 4b. How the IDE front ends use the same flow

```
forge CLI (forge/cli.py)                         VS Code extension (vscode-extension/src)
  run_task()                                       Session.run()                (session.ts, abstract UI)
   ├ GET  /api/health          preflight             ├ GET  /api/health
   ├ POST /api/tasks  + /run                         ├ POST /api/tasks + /run
   ├ GET  /events/stream  → render_event()           ├ streamEvents()  → describeEvent() → panel / status bar
   ├ approval.requested → prompt [y/N]               ├ approval.requested → panel card + notification (first answer wins)
   │     POST /approvals/{id}/decision               │     POST /approvals/{id}/decision
   ├ Ctrl+C → POST /cancel, drain stream             ├ Cancel button / command → POST /cancel
   ├ GET /tasks/{id}, /verifications, /diff          ├ GET /tasks/{id}, /diff
   └ prompt → POST /apply {confirm}                  └ "Apply" → dry_run preview → modal confirm → POST /apply {confirm}
```

**File references and insights (extension).** Before `Session.run`, `extension.ts` turns `@path` / `@path#10-20` mentions of existing workspace files into `references` (`core.ts:extractReferences`; the CLI does the same in `cli.py:extract_references`) and warns about unsaved buffers. While the task runs, `Session` (a) feeds every event to an `ActivityLog` (files read/changed, commands and exit codes, approvals, refusals), (b) polls `GET /api/tasks/{id}` every 2 s for the live stats bar, taking the context size from the last `model.response`, and (c) posts one final stats message before the result card. Timeline items carry `file`/`line` and, for edits, an inline `diff` taken from the tool arguments; clicking a file posts `open` or `fileDiff` to the extension, which re-validates the path before touching anything. The `@` dropdown asks the extension (`files`), which answers from a cached `findFiles` index filtered by `fuzzyFilter` (never secret-looking names).

Both reconnect a dropped event stream and resume after the last event id they received. Neither decides anything on the user's behalf: no terminal or a dismissed notification means "no decision" / "reject", never "approve".

## 5. Evaluation flow — `evaluations/`

`python -m forge.evaluations run` loads `cases/*/case.json`, copies each case repository to a temp authorized root, runs it through the *same* `TaskRunner`, then runs the case's `ground_truth` command in the resulting workspace **independently of the harness verdict**. That separation is how it measures false success claims (agent said done, ground truth disagrees) and harness false accepts (harness said verified, ground truth disagrees). The `scripted` provider is a self-test of this machinery and is labelled as such in its output.

## 6. Where to look when debugging

| Symptom | Look at |
|---|---|
| Task stuck in `awaiting_approval` | `GET /api/tasks/{id}/approvals`; decide it, or cancel |
| Tool call refused | `tool.authorization` event: `decision` and `reason` come from `policies/policy.py` / `commands.py` |
| "verified" missing | `GET /api/tasks/{id}/verifications` and the report's `known_limitations` |
| Orphaned process after cancel | `execution/sandbox.py:supervise` / `kill_tree` |
| Nothing happens on `Run` | HTTP 503 body (provider/sandbox preflight) |
