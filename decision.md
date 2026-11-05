# Harnix: Architecture & Implementation Decisions

This document records the key architectural and engineering decisions made in designing and building Harnix, the rationale behind each choice, and their trade-offs.

How the pieces fit together is in [flow.md](flow.md). For module boundaries see [docs/architecture.md](docs/architecture.md).

---

## 1. Architecture

### D1. Modular monolith, synchronous core, worker threads
**Chosen:** one FastAPI process; sync SQLAlchemy sessions; each task runs on a worker thread (`ThreadPoolExecutor`). Routes are sync `def` (FastAPI runs them in its threadpool); only the SSE endpoint is `async`.
**Why:** the agent loop is blocking by nature (subprocesses, the model SDK, polling for approvals). Threads keep that code straight-line and testable. Async buys nothing here and would force async DB sessions and async subprocess handling through every module.
**Cost:** concurrency is bounded by `FORGE_MAX_CONCURRENT_TASKS`; it is a single-process design (see D18).

### D2. Layering and dependency direction
**Chosen:** `api → harness → {tools, policies, verification, execution, models, persistence}`. `policies` and `execution` never import `harness` or `api`. Tools do not import policy; the executor calls policy. `persistence/store.py` is the only module that touches the database.
**Why:** our safety architecture requires that tools cannot bypass policy and that domain logic does not depend on route handlers. With this direction, the only way to run a tool is `ToolExecutor.execute`, which always runs validation → policy → approval first.

### D3. Interfaces: `Protocol` where there are real alternatives, plain typed classes elsewhere
**Chosen:** `ModelProvider` and `Sandbox` are `Protocol`s (two real implementations each: Anthropic/scripted, local/Docker). The tool executor, workspace manager, verification engine and store are concrete classes with typed public methods, injected through constructors.
**Why:** an interface with one implementation is ceremony. The classes are still injected (the tests swap the provider, sandbox and settings freely), which is what "explicit interfaces and dependency injection" is for.
**Cost:** if a second persistence backend were ever needed, a `Protocol` would be extracted from `Store`.

### D4. Events are rows, and everything reads rows
**Chosen:** one `events` table written through `Store.add_event` (secret-redacted). The timeline, tool-call history, SSE stream and tests all read it.
**Why:** a single source of truth makes "observable decisions" true by construction and makes runs reproducible/debuggable after the fact. No second in-memory log can drift from it.

### D5. SSE by polling the table, not an in-process pub/sub
**Chosen:** the stream endpoint reads `events WHERE id > last` every 0.5 s and ends after the task is terminal and two polls were quiet. It honours `Last-Event-ID` and `?after=`.
**Why:** it is correct across reconnects, restarts and any number of viewers, needs no broker, and cannot lose events (the table is the log). SSE is the simpler of the two options our requirements allow.
**Cost:** ≤0.5 s latency and one small query per open stream per tick. Fine for this scale; a `LISTEN/NOTIFY` fast path is the upgrade.

### D6. Unnamed SSE messages
**Chosen:** events are sent without an `event:` line (the type is inside the JSON); only the terminator is a named `end` event.
**Why:** `EventSource.onmessage` only receives unnamed events, so a client needs no hardcoded list of event types and new event types show up automatically.

---

## 2. Task model and lifecycle

### D7. Explicit state machine with a `verifying` state
**Chosen:** `queued, running, awaiting_approval, verifying, completed, failed, cancelled`. Transitions are a table in `harness/states.py` and are applied as a compare-and-set `UPDATE` (`Store.transition`). `completed` is reachable only from `verifying`.
**Why:** it makes "completed requires the verification policy to run" structurally impossible to skip, and gives duplicate `run` requests a natural 409 (the second CAS loses) without any extra lock.
**Note:** `verifying → running` exists so the recovery loop is a legal transition rather than a special case.

### D8. `completed` + `outcome`, not a separate "unverified" status
**Chosen:** a finished task is `completed` with `outcome ∈ {verified, unverified}`; a verification failure after the recovery budget is `failed` with `outcome = verification_failed`. The UI shows an explicit "Not verified" badge and banner.
**Why:** our design specification says that if verification is unavailable the task must be reported but *not labelled fully verified*. An outcome field keeps the status machine small and still makes the distinction impossible to miss in the API, report and UI.

### D9. Isolation by workspace instead of locking the source repo
**Chosen:** every task gets its own copy of the repository. Two tasks on the same repo therefore never conflict, so no repo-level lock exists.
**Why:** our requirements allow concurrent runs "unless isolation is provided". Isolation is also what keeps the user's repository safe, so it is the stronger choice.

### D10. Workspace = copy + fresh baseline git repo
**Chosen:** `execution/workspace.py` copies the working tree (including *uncommitted* changes) and `execution/git.py` creates a **new** git repo with a baseline commit. The source `.git` is not copied. `.git`, `node_modules`, `.venv`, caches, secret-looking files (`.env`, `*.pem`, `id_rsa*`, …) and **all symlinks** are skipped; placeholders like `.env.example` are kept. A size cap (`FORGE_MAX_REPO_COPY_MB`) applies.
**Why (fresh repo):**
1. Diff-from-baseline is exactly "what the agent changed", and the user's existing uncommitted work is preserved yet excluded from that diff.
2. A repository is untrusted input; its `.git/config` (fsmonitor, diff drivers, hooks, remotes) is a code-execution vector for any tool that runs `git`. A repo we created has none of that.
3. With no remotes copied, "push to a remote" cannot actually succeed from a workspace.
**Cost:** git history is not available inside the workspace (`git log` shows only the baseline). `git worktree` was rejected because it writes into the source repo's `.git`.

### D11. Authorized repository roots instead of a repository table
**Chosen:** `FORGE_ALLOWED_REPO_ROOTS` lists directories; a task may target any directory beneath one of them (checked at creation and again at execution). `GET /api/config` returns the roots and the UI offers them as one-click prefixes.
**Why:** it satisfies "select an authorized registered repository" with no extra entity, CRUD, or UI. The operator's allowlist is configuration, not data a request can change. With no roots configured, task creation is refused with an actionable message.
⚠ No `repositories` table and no per-repository settings (see section 7).

### D12. Re-run creates a new task
**Chosen:** `POST /tasks/{id}/rerun` copies the definition into a new queued task with `parent_task_id`. There is no separate "run attempts" table.
**Why:** each run keeps its own workspace, events, verification and report, which is exactly what run comparison needs. A runs table would duplicate the task row.

---

## 3. Agent runtime

### D13. Budgets, and what is not counted
**Chosen:** hard limits on iterations, wall-clock time, tool calls and cumulative tokens, plus per-call output and file-size caps (`policies/budget.py`). Time spent **waiting for a human approval is excluded** from the duration budget. Per-task limits can only lower the server limits.
**Why:** otherwise a slow reviewer would silently burn the agent's time budget and fail the task for something the agent did not do.

### D14. What counts as a "tool failure"
**Chosen:** a command that ran and exited non-zero is a *successful tool call* with `exit_code ≠ 0` (a failing test run is normal progress). Timeouts, missing executables, policy denials, invalid arguments and tool exceptions are failures. The evaluation metric `tool_failures` uses this definition.
**Why:** counting every red test run as a "failure" would make the metric meaningless.

### D15. Repeated-failure detection
**Chosen:** the same call (tool + canonical args) failing 3 times with no successful workspace write in between gets a harness notice; the 5th aborts the task (`repeated_failure`). A successful write clears the counters.
**Why:** it stops loops cheaply, without penalizing the legitimate "edit, re-run the same test" cycle.

### D16. Malformed and empty model output
**Chosen:** invalid tool arguments and unknown tools come back as structured tool errors (the model can correct itself). An empty response, or one cut off by `max_tokens`, is not treated as a final answer; three in a row abort the task. Transient provider errors retry twice with backoff, then fail; fatal ones fail immediately.

### D17. Context handling
**Chosen:** the model sees the task, acceptance criteria, detected project info, baseline check results and repository instruction files (wrapped as `trust="untrusted"`), then the tool-result history. Older tool output is elided **only** when the history exceeds 600k characters; below that the history is sent unchanged.
**Why:** current Claude models bind thinking blocks to an unedited history, so rewriting earlier turns has a cost. Elision is therefore a last resort, and the stored history is never mutated (only the copy sent to the model).
**Cost:** a very long run could still hit a provider limit; that surfaces as a normal provider error.

### D18. Single process, no queue, in-memory cancellation
**Chosen:** cancellation uses an in-memory `threading.Event` per running task (plus a DB flag). Starting a task when `max_concurrent_tasks` are active returns HTTP 429 instead of silently queuing. After a crash, `recover_orphans` marks tasks left active as `failed` at the next start (re-run to retry).
**Why:** a hidden queue would show tasks as `running` that are not running. Distributed execution is explicitly out of scope.
⚠ Resuming an interrupted task mid-run is not implemented; recovery means "fail honestly, then re-run".

---

## 4. Security

### D19. Execution mode defaults to Docker; local is an explicit opt-in
**Chosen:** `FORGE_EXECUTION_MODE=docker` is the default. If Docker or the image is missing, `Run` fails with the exact fix. `local` must be chosen deliberately, logs a warning at startup, and shows a banner in the UI.
**Why:** "do not silently fall back to insecure execution".
**Docker flags:** `--network none`, `--cap-drop ALL`, `no-new-privileges`, `--read-only` root with a 256 MB tmpfs `/tmp`, non-root `--user`, memory/CPU/PID limits, and only the workspace bind-mounted. The host environment is not forwarded. On timeout or cancel the container is `docker kill`ed (killing the CLI alone would not stop it).
**Honest limit:** Docker is not a complete boundary (shared kernel, the workspace is writable); documented in [docs/security.md](docs/security.md). Local mode is **not** a security boundary and the docs say so.

### D20. No shell, ever
**Chosen:** commands are parsed with `shlex` and executed as an argv list. Unquoted shell metacharacters (`; | & < > \`` and `$(`) are rejected rather than passed through.
**Why:** the model's text is never interpreted by a shell, so injection through quoting tricks does not exist; one command per call keeps each call independently authorizable.

### D21. Three-way command policy: allow / approve / deny
**Chosen** (`policies/commands.py`):
* **allow** — recognised validation commands (`python -m pytest|ruff|mypy|pyright…`, `pytest`, `npm test|run lint|build…`, read-only `git`, `cargo/go test`…), plus harness-detected and task-specified verification commands.
* **approve** — everything else: arbitrary `python -c` / script execution, installs, `git push/commit`, destructive commands (`rm`, `del`, `mv`…), path-qualified programs, unknown tools.
* **deny** — `sudo`-class commands, shell syntax, and any attempt to leave the workspace.
**Why:** a small allowlist makes the common validation loop frictionless, and the human is asked about exactly the risky remainder. `FORGE_APPROVAL_POLICY` adds `strict` (approve every file write) and `deny_sensitive` (never ask; refuse; used by evaluations/headless).

### D22. Out-of-workspace access is denied, not approvable ⚠
**Chosen:** any path that resolves outside the workspace (traversal, absolute path, symlink/junction escape) is a hard deny with no approval offered. Reading secret-looking files is also denied.
**Why:** our design lists "access outside the permitted workspace" under actions that need approval. But the tools expose no legitimate need for it, and an approval prompt is a social-engineering surface for a prompt-injected model. Denial is the stricter, simpler behaviour. If a real need appears, add an `approve` branch in `policies/policy.py`.

### D23. Approvals are bound to one exact action
**Chosen:** an approval stores the tool, the exact arguments, and `sha256(task, tool, args)`. The decision endpoint accepts one decision per approval (`UPDATE … WHERE status='pending'`); the gate re-verifies the hash and executes the *validated arguments held in memory*, not anything re-read later. Approvals expire (`FORGE_APPROVAL_TIMEOUT_SECONDS`) and are rejected on cancel.
**Why:** "an approval must authorize a specific action and relevant parameters".

### D24. Untrusted content handling
**Chosen:** repository instruction files and all tool output are labelled untrusted in the prompt, but the real defence is code, not wording: policy is evaluated by the harness on every call and cannot be changed by anything the model reads. The `injection_attempt` evaluation case verifies that commands injected via a README are blocked.

### D25. Secret handling (best effort)
**Chosen:** `.env`/key files are not copied into workspaces; the sandbox environment is rebuilt from an allowlist (no `ANTHROPIC_API_KEY`, no cloud credentials; `HOME` points at a per-task directory); `policies/redaction.py` scrubs known secret values, common token formats and `name=value` pairs from events, tool output, reports, logs and API responses.
**Honest limit:** pattern redaction cannot catch every secret; documented.

### D26. API access control
**Chosen:** unauthenticated by default and bound to `127.0.0.1` in the documented command (single-user tool). Setting `FORGE_API_TOKEN` requires `Authorization: Bearer …` on every `/api` route except `/api/health`. `?token=` is also accepted because browsers' `EventSource` cannot send headers.
**Cost:** a token in a URL can appear in access logs; documented. There are no per-user accounts, so "unauthorized task access" means "not holding the token".

### D27. Git hardening
**Chosen:** all host-side git calls disable hooks, fsmonitor, external diff/textconv and pager, ignore system/global config, and never prompt. Text search uses `git grep` rather than Python `re` over repository text (no ReDoS in an uncancellable thread).

---

## 5. Verification and honesty

### D28. Baseline first, then judge the delta
**Chosen:** checks run before the agent starts and again after each completion claim. A failing `test`/`acceptance` check always blocks (the task exists to make it pass); a failing lint/typecheck blocks only if it passed at baseline. `verified` requires ≥1 test/acceptance check to have passed; lint alone never proves correctness.
**Why:** it separates regressions from pre-existing failures ("detect existing failures where practical") without letting a repo with a red test suite be reported as fixed.

### D29. Free-text acceptance criteria are never "verified"
**Chosen:** each free-text criterion becomes a check with status `unavailable` and the note "needs human review", and is listed in the report limitations. Machine-checkable criteria are given as `verification_commands`.
**Why:** an LLM judging its own criteria would violate "self-assessment cannot substitute for independent verification".

### D30. Distinguishing the four statuses
`pytest` exit code 5 (no tests collected), a missing executable, and a missing tool module (`No module named pytest`) are `unavailable`, not `failed` or `passed`. Cancelled/never-run checks are `skipped`.

### D31. Reports state limitations
The report always lists local-mode execution, mock provider, unavailable checks and un-checked criteria. `false_success_claim` is set when the agent claimed completion and independent verification failed.

---

## 6. Model integration, stack, data and UI

### D32. Anthropic adapter specifics
**Chosen:** the official SDK, `messages.create`, default model `claude-sonnet-5-5` (`FORGE_MODEL`). The assistant turn's SDK content (including thinking blocks) is replayed verbatim; no `temperature` and no forced `tool_choice` are sent (unsupported on current models); errors map to retryable (rate limit, 5xx, network, timeout) vs fatal (auth, permission, not found, bad request); a `refusal` stop reason is a distinct failure, never treated as a finished answer; `max_tokens` defaults to 16000 (non-streaming, so well inside SDK timeouts). Token usage includes cache read/creation tokens. Cost is shown only when both pricing variables are set; no prices are hardcoded.
⚠ Not implemented: server-side refusal fallbacks, streaming, prompt caching. They are provider-specific optimisations that cannot be validated here without credentials. (The Anthropic adapter itself has only been tested against a mock transport: see D32b for the OpenAI adapter that was tested for real.)

### D32b. OpenAI adapter on Chat Completions (added after the first version)
**Why OpenAI at all:** the owner had no Anthropic key but an OpenAI key with a hard **$4** budget, so a second real provider was needed. It slots in behind the same `ModelProvider` interface; nothing in the harness changed except where noted below.
**API choice:** Chat Completions rather than the newer Responses API. Both were probed against the live API with tools on `gpt-5.4-nano` and both worked. Chat Completions is stateless (no reasoning-item replay), maps one-to-one onto Harnix's provider-neutral messages, and keeps the adapter small. If a future reasoning-heavy configuration benefits from Responses-style reasoning replay, it is a drop-in second adapter.
**Details:** `max_completion_tokens` (not `max_tokens`) and no `temperature`, as reasoning-family models require; tool results become `tool` messages directly after the assistant's `tool_calls`; invalid JSON in tool arguments is turned into a clear tool error the model can fix (`INVALID_JSON_KEY` in `models/base.py`) instead of a crash; `finish_reason: length` maps to the harness's "truncated response" handling; a refusal or content-filter stop is a distinct failure; a 429 with `insufficient_quota` is **fatal** (retrying a spent budget is pointless) while ordinary 429s retry with backoff.

### D32c. Model choice: `gpt-6-luna` with reasoning off (after measuring four models)
**Process (and a correction):** I first searched for current prices and shortlisted `gpt-5.4-nano` ($0.20 / $1.25 per 1M tokens in/out), because the model list I pulled from the key was filtered to `gpt-5*` names and so hid the `gpt-6*` family; the owner then asked about "gpt 6 luna". I re-listed the key's models without that filter, fetched OpenAI's pricing page (`gpt-6-luna` is $0.10 / $0.50, `gpt-5.6-luna` $0.20 / $1.20), and measured all four candidates on the same four shipped cases in the Docker sandbox, each under a hard budget cap.
**Result (4 cases each, all 4/4 completed and independently verified, 0 false claims):**

| Model | Cost (4 tasks) | Tool failures | Mean s/task |
|---|---|---|---|
| gpt-6-luna | **$0.0028** | 0 | 12.8 |
| gpt-5.6-luna | $0.0055 | 0 | 11.6 |
| gpt-5.4-nano | $0.0077 | 3 | 16.0 |
| gpt-5.4-mini | $0.0214 | 0 | 9.8 |

**Chosen:** `gpt-6-luna`: equal outcomes, the lowest cost (2.7× below nano, 7.6× below mini), no tool failures. With a fixed budget, the cost of *long* tasks is what bites, so the cheapest model that is demonstrably reliable wins.
**The catch, probed live:** the Luna models reject function tools on Chat Completions unless `reasoning_effort` is `none` (HTTP 400 at default or `low`; the error points to the Responses API). So the shipped config sets `FORGE_REASONING_EFFORT=none`: Luna runs **without a reasoning phase**. The vendor's headline Luna benchmark numbers are at high reasoning effort and do not describe this setup. The adapter turns the raw 400 into an actionable message (`kind="config"`).
**Not done:** a Responses API adapter would let Luna reason with tools. I did not build it: the four cases show no need, it adds a second code path (reasoning-item replay), and reasoning tokens would cost more. It is the natural next step if hard tasks fail. **Caveats:** the shipped cases are tiny and cannot separate these models on hard work; the code default stays `gpt-5.4-nano` because it works with no extra setting; `gpt-5.4-mini` is the suggested switch for harder tasks (README lists the config lines). Capability claims from search results (third-party sites) are unverified; prices come from OpenAI's page.

### D32d. Dollar guards, because the budget is fixed
**Chosen (revised 2026-10-22):** spending is **measured, not limited**. Token use and estimated dollars (from the configured prices; none are hardcoded) are counted per task and in total and shown in the UI, CLI and reports, but nothing is stopped or refused for them. The original design had two caps (`FORGE_MAX_COST_PER_TASK_USD` failing a task as `budget_cost`, and `FORGE_SPEND_LIMIT_USD` refusing new runs with HTTP 402) plus a token cap; they were removed on request because a hard stop in the middle of a task wasted the work already paid for. What remains are the loop limits (iterations, wall-clock time, tool calls), which exist to stop a runaway loop, not to control cost. The old variables are still parsed so that an old `.env` starts, and a startup warning says they are ignored. Evaluation runs keep their opt-in `--budget-usd`. The trade-off is explicit: a mis-behaving model can now spend as much as the loop limits allow (30 iterations by default), so a provider-side spending limit is the only hard cap.
**Conservative by design:** input tokens are costed at the full rate (cache discounts ignored), so estimates err high. `FORGE_MAX_CONCURRENT_TASKS` is set to 1 in the shipped `.env` because simultaneously started tasks each see the same remaining budget.
**Honest limits:** these are estimates, not billing. One model call can overshoot a cap, runs outside the app database (evaluations) are not counted against the global limit, and the real provider-side limit is whatever the provider enforces.

### D32e. Configuration robustness
* **`.env` discovery:** repository root, then `backend/`, then the working directory (later wins), so Harnix works whatever directory it is started from. The owner's `.env` lives at the repository root while the backend is normally started from `backend/`; before this change it would silently not have been read.
* **Provider-specific default model:** `FORGE_MODEL` is optional; `openai` → `gpt-5.4-nano`, `anthropic` → `claude-sonnet-5-5`.
* **Mismatch and placeholder checks:** a Claude model name with `FORGE_PROVIDER=openai` (or the reverse), a missing key, or an `.env.example` placeholder such as `your-…-here` is reported with the fix, in the health endpoint, the UI banner, and as HTTP 503 on `Run`. This caught a real problem: the owner's `.env` initially paired `FORGE_MODEL=claude-sonnet-5-5` with the example's placeholder Anthropic key.
* **Secrets:** both keys are registered with the redactor; the sandbox never receives them; I edited the owner's `.env` with a script that did not print or rewrite the key lines.
* **Absolute paths in `.env`:** database, workspace root and repository root are absolute, because a relative SQLite URL resolves against the working directory and would silently create a second empty database when the backend and Alembic are started from different folders.

### D32f. IDE integration: a CLI first, a VS Code extension on top (added after the first version)
**Ask:** "use it just like Claude Code in any IDE". **Chosen:** (1) a `harnix` command-line agent, because every IDE has a terminal, so it is the one option that really works in *any* IDE; (2) a VS Code extension for the in-editor experience. Both are thin clients of the existing HTTP API (no second implementation of the agent); other editors can use the same API.
* **CLI** (`harnix/cli.py`, `harnix/client.py`): standard library only for HTTP/SSE (no new dependency), proxies are bypassed for localhost. `harnix "task"` shorthand, bare `harnix` is a REPL, `serve/doctor/tasks/apply`. Distinct exit codes so scripts can tell "verified" from "completed but not verified". Non-interactive input answers every approval with **no** and never applies. `harnix serve --allow DIR` authorizes a folder for that server instance only and runs migrations; it refuses a non-loopback host without `FORGE_API_TOKEN`.
* **Each prompt is an independent task**, not a conversation with memory. That keeps the isolation, budget and verification model intact; after an apply, the next prompt sees the applied changes because it copies the repository afresh. A multi-turn mode is a possible later step.
* **Extension** (`vscode-extension/`): split into `core.ts` (API client + SSE parser + event wording, no `vscode` import), `session.ts` (run/approve/cancel/apply logic against an abstract UI) and `extension.ts`/`webview.ts` (the VS Code glue). That split is what allowed real tests without a GUI. The panel inserts all model/repo text with `textContent` only and a nonce-based CSP, so repository content cannot inject script. Approvals appear both in the panel and as a notification; the first answer wins and a second is ignored (the server also accepts one decision per approval); a dismissed notification decides nothing. The API token lives in VS Code's secret storage, not in settings. The server is started by running the `harnix` executable directly in a terminal (no shell), so paths with spaces need no quoting.
* **What was verified:** protocol compatibility with the real backend and a real model; and, after the user asked for a test "in VS Code itself", the UI in a real VS Code window: `test/ui/drive.cjs` launches an isolated instance with `--remote-debugging-port`, attaches with puppeteer-core, and drives and screenshots it (the panel is nested webview iframes; notifications and dialogs are workbench DOM). 31 checks across panel/fix/approval/cancel/doctor + 4 for a stopped backend. **Not verified:** the packaged `.vsix` installed into the user's own VS Code; Windows' *native* dialogs (the test profile sets `window.dialogStyle: custom` because native dialogs are OS windows invisible to the page; I first mistook that for a bug, reproduced it, then confirmed it by switching the style); light themes; macOS/Linux.
* **Fixes that only looking at the real UI revealed:** a prompt box too small to type in; an approval command clipped by a horizontal scrollbar (now wraps, because a person approving must see all of it); a stale Approve notification after answering elsewhere (popups are hidden when the approval settles; VS Code offers no API to close one notification); an Apply button still enabled after applying; an unsaved "Untitled" diff tab (now a read-only virtual document `harnix-diff:`); the user's prompt lost when the backend is down (restored to the box); and a duplicated "Not approved" line (also fixed in the CLI).
* **A driver bug worth recording:** clicking an already-open activity-bar icon toggles the sidebar shut; early failures with `Runtime.callFunctionOn timed out` were my driver doing that, not the extension. `openPanel` now clicks only when the container is not active.

### D32g. Apply-back is the only write to the user's repository, so it is deliberately strict
The first version left applying the diff to the user. For the IDE experience it had to exist, so it was built with these rules (`harnix/execution/apply.py`):
* **Raw workspace files, not the diff.** The diff served by the API is secret-redacted; applying it could have written `[REDACTED]` into source files. Apply reads the workspace files themselves.
* **Direct verified copy, not `git apply`.** `git apply` run inside a subdirectory of a larger repository interprets paths relative to the repository root and silently skips files outside the directory. Instead: for each changed file (A/M/D from `git diff --no-renames -z`), write it only if the repository's copy is still byte-identical to the baseline blob (or absent, for adds). Anything you changed since the task started is never overwritten.
* **All or nothing:** every file is validated first (conflicts, unsafe paths, symlinks, escape through links inside your repo); one problem means nothing is written. A failed write mid-way restores the earlier files.
* **Refused paths:** secret-looking files, `.git`/`.harnix`, traversal, absolute and drive paths.
* **Exact bytes:** the workspace baseline sets `* -text -eol -filter` in `.git/info/attributes` so CRLF files and `.gitattributes` EOL rules cannot make the baseline differ from your files; binary files and CRLF round-trip exactly (tested).
* **Explicit and once:** the endpoint needs `confirm=true` (or `dry_run`), only for `completed` tasks, re-checks that the repository is still inside the authorized roots, and a second apply is refused (a `task.applied` event is recorded). Unverified results can be applied, but every front end says so loudly.

### D32h. File references and "insights" in the editor (added after the IDE work)
**Ask:** "refer files and see insights, like Claude Code does; maybe take inspiration from DeepSeek's open-source harness". **Research, honestly:** I searched for an official DeepSeek agent harness and could not confirm one (there are community projects such as a DeepSeek terminal UI, and DeepSeek models are usable from other harnesses). I took two ideas in spirit from what I found and from how Claude Code behaves, and wrote the implementation myself: (1) `@file` / `@file#10-20` mentions that attach files to a task, and (2) a per-session "receipt" of what the agent did, with cost shown as **unknown** rather than zero when prices are not configured.
* **References are resolved on the server, from the isolated copy, not by the client.** The extension/CLI only send `{path, start_line?, end_line?}`. `harness/references.py` loads them through the same `WorkspacePaths` containment as every tool (traversal, links, secret/protected files refused), so a compromised or buggy client cannot make the backend read anything outside the task's workspace. Skipped files are reported with the reason in a `task.references` event and shown to the user (a mentioned `.env` is refused visibly, not silently dropped).
* **Referenced content is untrusted data.** It is wrapped in `<referenced_file path=… lines="a-b of N" trust="untrusted">` blocks; a file that contains the closing tag cannot break out (`fence()` rewrites `</referenced_file` and the same for repository instructions); the system prompt says such blocks are data. Hard caps (10 files, 400 default lines, 20k chars each, 60k total) bound context cost; truncation is stated in the block.
* **Mentions only become references when the file exists.** `@alice`, e-mail addresses, `@../x` and unknown names stay plain text, so ordinary prose is never mangled. The prompt text sent to the model has the mention rewritten to the plain path (`src/a.py (lines 3-9)`); the panel still shows what the user typed.
* **Unsaved editor changes are not silently ignored.** Harnix copies saved files, so if a mentioned file has unsaved changes the extension asks ("Save and run" / "Run anyway").
* **Insights are derived from data the backend already stores.** The live bar polls `GET /api/tasks/{id}` every 2 s (steps, tool calls, failures/refusals, tokens, estimated cost, duration) and takes the context size from the last `model.response` usage; the activity "receipt" (files read, files changed, commands with exit codes, approvals approved/rejected, refused/failed calls) is built from the same event stream the timeline uses, matched by `call_id`. Nothing is estimated client-side except what is labelled `~`.
* **Webview strings are never trusted.** Opening a file or a diff goes through the extension, which re-validates the path (relative, no `..`, inside the workspace folder) and, for diffs, only accepts files that Harnix itself reported as changed for that task; the agent's version is read from the isolated copy (no symlinks, no binaries, ≤ 1 MB) and shown as a read-only virtual document next to the user's real file. Inline edit previews come from the tool-call arguments in the event stream and are inserted with `textContent`.
* **Autocomplete never suggests secret-looking files** (the same name rules as the backend) and skips names that cannot be written as a mention (spaces, `@`, `#`). The index is capped at 5000 files and refreshed on file create/delete.
* **Verified:** 16 backend tests (`test_references.py`), CLI mention tests, migration `0002` on SQLite and PostgreSQL, 45 extension unit tests, the extension-host test, and an 18-check UI scenario in a real VS Code window (selection hint, Alt+K, autocomplete, no `.env` suggestion, stats bar, attached/refused notes, inline diff, activity receipt, click-to-open, native side-by-side diff). **Not verified:** the packaged `.vsix` installed into the user's own VS Code, light themes, macOS/Linux, whether `Alt+K` clashes with a user's own keybinding.
* **Two test mistakes worth recording (both were in my tests, not the product):** I expected `rangesum.py:1-3` in the timeline, but a range that covers the whole file is, correctly, shown as the plain path; and Monaco renders spaces as U+00A0, so the diff-content check needed ` ` (and the diff editor falls back to inline mode in a narrow window, so the test profile pins side-by-side).

### D32i. Knowledge graph in Neo4j, used for context management (added after the IDE work)
**Ask:** a knowledge graph of the project, used in context management "for better results", Neo4j in Docker, industry standard for large scale. Full design in [docs/knowledge-graph.md](docs/knowledge-graph.md); the decisions:
* **Optional and failure-isolated.** `FORGE_GRAPH_ENABLED` is off in code and on in this deployment. A missing password fails at startup with the fix; an unreachable Neo4j never fails a task: the runner records `graph.unavailable` (with the reason) and the model is not offered the tool or the context block. A graph *bug* (any exception) is contained the same way. Why: the graph improves context, it is not a correctness mechanism, so it must not be able to take verification or the task down with it.
* **Neo4j Community 5.26 LTS, pinned, official Python driver** (pooled, managed transactions with retry, server-side transaction timeouts). Community has one database, so a repository is a namespace: every node has `repo_id` (hash of the authorized path; the path itself is not stored) and every statement filters on it. Constraints on `uid`, indexes on `(repo_id, path|name)`, a text index for substring search. No APOC/GDS (not in Community, and not needed).
* **Structure, not content.** Nodes are Repo/File/Symbol/External; edges are DEFINES, CONTAINS, IMPORTS, IMPORTS_EXTERNAL, TESTS, CALLS, INHERITS. The graph holds names, signatures, line ranges, hashes and the first docstring line, never source text. **Found while testing:** signatures originally carried default values, so `def connect(password="...")` would have stored a secret; signatures now keep parameter names and annotations and replace defaults with `...` (regression tests include secret-shaped defaults).
* **Parse with `ast`, not regexes; resolve conservatively.** Python is parsed exactly with the standard library. An edge exists only when a name ties to one definition (local name, imported name or module alias, `self.method()` in the same class, a unique module path). Ambiguity (two files claiming `utils`) gives no edge. **Not resolved, deliberately:** calls through instance attributes (`self.executor.execute()`); guessing by method name would add confident-looking false edges, so callers/callees are documented as a lower bound and tool results say so. Other languages get File nodes only until a tree-sitter extractor is added (the `extract.py` seam exists). I did not pull in tree-sitter now because I could not verify multi-language extractors without test repositories for each language.
* **Incremental, time-budgeted, resumable.** A file whose (size, mtime) match the stored record is not read; unchanged content is not rewritten; only changed files are re-parsed. Re-resolution covers the changed files, their importers, importers of removed files, and importers of newly added modules (found through each file's stored `imports_mods`). An interrupted run (time budget, cancel, crash) marks the repository incomplete; the next run finishes the files and then re-resolves everything once, converging to the same graph as a fresh index (tested). In-task indexing has a time budget (`FORGE_GRAPH_INDEX_TIMEOUT_SECONDS`, default 60 s); big repositories are pre-indexed with `harnix graph index` / `POST /api/graph/index` (own, larger budget) so tasks only pay for changes.
* **Index the task's workspace copy**, not the live repository: it is secret-free and symlink-free by construction and faithful to what the agent sees. The graph describes the task *start*; edits the agent makes are not written to the shared graph (they would pollute other tasks and survive failed ones). Instead the tool result lists the files the agent changed ("not reflected") and flags `stale` if another run re-indexed the repository meanwhile.
* **Two ways into the model's context, both bounded.** (1) A `<knowledge_graph>` block in the first message (default 6,000 characters): overview, most-depended-on files, and a *focus* section seeded from files the user attached, paths and identifiers in the task, and the **test files that fail at baseline** (found from the baseline traceback), each with definitions, imports, importers and callers. (2) A `graph_query` tool for on-demand lookups (progressive disclosure instead of stuffing the prompt). The tool has eight fixed operations; the model supplies a value, never Cypher. It is read-only, offered only when a graph exists, and goes through the same validate, policy and executor pipeline as every tool. Graph text is untrusted: flattened, size-capped, redacted, fenced and labelled.
* **Dependents by BFS, not variable-length paths.** `dependents`/`tests_for` walk the import graph one bounded query per level (depth <= 3). Variable-length path matching can explode combinatorially on dense import graphs.
* **What this does *not* establish:** that the graph makes a real model better. The scripted-provider tests prove wiring; see docs/knowledge-graph.md "Measured" for the small real-model comparison that was run and how little it can show.
* **Unrelated bug found on the way:** `Settings` reprs included API keys (pytest printed one while showing a failing fixture); secret fields are now `repr=False`, with a regression test.

### D32j. Skills, sub-agents, pruning/compaction, thinking levels and project memory
**Design requirement:** Support skill mechanisms, sub-agent spawning, context pruning, configurable thinking levels, and bounded project memory in Harnix. Multi-agent orchestration and long-term agent memory were implemented deliberately small, bounded, and failure-isolated.

What opencode does (read from `packages/core/src`): skills are `SKILL.md` files with frontmatter, only id/name/description are listed to the model and a `skill` tool returns the body plus a sampled file list; sub-agents are child sessions started by a `subagent` tool with fresh context, built-in `explore` (read-only) and `general` profiles, a depth limit of 1 by default; context is kept in bounds by compaction (a structured summary template, the newest ~15k tokens kept verbatim, triggered at 85% of the window with at least 16k free, and after a provider "too long" error); thinking levels are provider "variants" (OpenAI reasoning effort, Anthropic adaptive thinking + effort). opencode has no cross-session memory beyond instruction files.

How Harnix does each, and why:
* **Skills** (`harness/skills.py`, `load_skill`): same file format, parsed with a small frontmatter parser (no YAML dependency). Loaded from **operator** directories (`FORGE_SKILLS_DIRS`); skills inside the target repository only with `FORGE_REPO_SKILLS=true` and labelled untrusted, because a skill is instructions and repository text is not trusted (opencode loads project skills by default; Harnix's threat model does not allow that). Listing goes into the system prompt (stable, cache-friendly); bodies come through the tool or are preloaded when the task names them. Skill files are read with the same containment rules as workspace paths. A skill cannot change policy.
* **Sub-agents** (`harness/agents.py`, `harness/subagents.py`, `spawn_agent`): the same `AgentLoop` with a fresh context, the profile's tools through the *same* executor (validation, policy, approvals, redaction), the task's shared budget and cancellation, at most `FORGE_SUBAGENT_MAX_ITERATIONS` steps, **depth 1** (no `spawn_agent`) and no memory writes. Foreground only: tool calls run one after another, so a background mode would need concurrent tool execution, which Harnix does not do. A sub-agent's report is returned to the parent as a tool result and is *not* verified; only the main agent's final answer goes through independent verification. Events are tagged with the sub-agent's label.
* **Pruning and compaction** (`harness/compaction.py`, `AgentLoop._manage_context`): cheapest first. Pruning replaces old tool output with a one-line note (newest `FORGE_PRUNE_PROTECT_TOKENS` never, the newest tool results never, skill content and sub-agent reports never; only when at least `FORGE_PRUNE_MINIMUM_TOKENS` is reclaimed). Compaction summarizes the older conversation into opencode's handover template, with no tools, and keeps the task message plus the newest `FORGE_COMPACTION_KEEP_TOKENS` verbatim, starting at an assistant turn so tool calls and their results stay paired. Both rewrite the *stored* history, so requests stay append-only between these steps. The old "elide above 600k characters on every request" behaviour was removed. A provider `context_overflow` error triggers one compaction (keeping much less) and a retry.
* **Thinking levels** (`thinking` on tasks, `FORGE_THINKING_LEVEL`, per-agent): mapped per provider. OpenAI: reasoning effort; **the Chat Completions API refuses reasoning together with function tools for the Luna models**, so the level is downgraded to `none` there and the response says so (`model.response.thinking.reason`); a new **Responses API** adapter (`FORGE_OPENAI_API=responses`) carries reasoning between turns as encrypted items (`store=false`, replayed verbatim) and is what makes the levels real with tools. Anthropic: adaptive thinking with `output_config.effort` (current Claude models reject fixed `budget_tokens`); not run against the real API (no key). A level a model rejects (e.g. `xhigh`) is stepped down once and reported. An operator pin (`FORGE_REASONING_EFFORT`) still wins.
* **Project memory** (`harness/memory.py`, `remember`, `/api/memory`, `harnix memory`): short notes per repository (keyed like the graph), shown at the start of every task in a `<project_memory>` block, user notes first. The agent's `remember` needs **human approval by default** (`FORGE_MEMORY_WRITE=approve`), because a note outlives the task: an injected instruction written to memory would otherwise reach every future task. Agent notes are labelled unverified; notes are size-capped, deduplicated, and refused if they look like secrets.

**Found by real-model runs, not by the mock tests:** (1) with a small window the fixed part of each request (system prompt, tool schemas, task, checkpoint) is close to the threshold, so compaction re-ran almost every turn (12 summary calls in one run); compaction now waits for real growth since the last one and records `context.compaction_deferred` once. (2) Pruning before compaction removed file contents the summary then could not see ("results were pruned"), forcing re-reads; the originals of pruned output are now kept until they have been summarized. (3) The newest tool results could be pruned before the model had seen them with a small protect window; they are now never pruned. (4) The CLI shorthand failed when options came before the prompt (`harnix --thinking high "task"`); fixed.

**Verified:** unit and end-to-end tests (`test_agentic_unit.py`, `test_agentic_harness.py`, migration 0003 up/down); real OpenAI runs with `gpt-6-luna` on the Responses API: reasoning applied at `high` (11-65 reasoning tokens per call) while the `explore` sub-agent ran at `low`, a skill file loaded, a memory note saved and shown to the next task, pruning and compaction on a deliberately tiny window, tasks independently verified. **Not verified:** the Anthropic thinking parameters against the real API; whether these features improve results on large real tasks (no controlled comparison was run); the dashboard/extension UI in a real browser or VS Code window. **Known trade-off:** pruning and compaction edit earlier turns, which on current Claude models can drop replayed thinking blocks ("preserved thinking"); Harnix's Anthropic path is untested, so this is documented rather than solved.

### D33. The scripted provider is a labelled mock
**Chosen:** `ScriptedProvider` replays a fixed script. It is selectable (`FORGE_PROVIDER=scripted` + `FORGE_SCRIPTED_FILE`) so the whole stack can be demonstrated, but it reports itself as `scripted-mock`: the task record, API (`mock_provider: true`), report limitations, and UI (banner + `MOCK PROVIDER` badge) all say so; evaluation output carries `valid_for_llm_claims: false`.
**Why:** our safety architecture requires an end-to-end run with a mocked provider and forbids presenting a mock as a real model.

### D34. SQLite and PostgreSQL, Alembic is the schema authority
**Chosen:** the same SQLAlchemy models for both; SQLite uses WAL so the stream reader and agent writer overlap. The app refuses to start with an unmigrated database and tells you to run `alembic upgrade head`; `create_all` is used only by tests. The one migration was autogenerated and verified up/down/up on both SQLite and PostgreSQL 16.

### D35. Frontend: minimal dependencies
**Chosen:** React + Vite + TypeScript, hash routing (two screens), no router library, no state library, no chart library. Resource usage is shown as numbers, which is all the data supports.
**Why:** "avoid unnecessary dependencies"; a chart over a handful of counters would be decoration.

### D36. UI honesty rules
Proposed, executed and verified items have different event types and styles; unavailable/not-verified are shown as such (never as a green tick); setup problems appear as banners with the fix; there is no placeholder or demo data anywhere.

### D37. Windows specifics (the build machine)
Process-tree kill uses `taskkill /T /F`; the symlink-escape test uses a directory junction when the OS denies symlink creation; commands resolve through a scrubbed `PATH` that starts with the interpreter's own directory so `python -m pytest` uses the same environment as Harnix.

### D38. Tooling
`ruff` ignores E501 (the formatter wraps lines); pyright runs in `standard` mode with zero errors; the lock file `backend/requirements.lock` is a `pip freeze` of the tested environment; the frontend lock is `package-lock.json`.

---

## 7. Deviations, tightenings and items not built

| | Item | Status |
|---|---|---|
| ⚠ | Out-of-workspace access | **Denied** rather than approvable (D22) |
| ⚠ | Registered-repository entity and repository-specific config (P1) | **Not built**; authorized roots instead (D11) |
| ⚠ | Resuming an interrupted task | **Not built**; failed-then-re-run (D18) |
| ⚠ | OpenTelemetry | **Not built** (optional in our specification); structured JSON logs + persisted events instead |
| ⚠ | Metrics charts | **Not built**; numeric resource panel (D35) |
| ⚠ | Provider fallbacks / streaming / prompt caching | **Not built** (D32) |
| ⚠ | Workspace cleanup / retention | Workspaces are kept for inspection; no automatic deletion |
| ✔ | Applying the agent's changes back to the source repo | **Built later**, strictly (D32g); CLI, VS Code extension and dashboard all use it |
| ⚠ | JetBrains / other IDE plugins | **Not built**; the CLI works in any IDE terminal and the HTTP API is open (D32f) |
| ✔ / ⚠ | Extension in a real VS Code window | Tested and screenshotted (31 + 4 checks). Not done: installing the `.vsix` into the user's VS Code, Windows native dialogs, light theme, macOS/Linux |
| ✔ | P1: per-task limits, token/cost tracking, rerun, report export (JSON + Markdown), evaluation suite, configuration comparison | Built |
| ✔ | Real-model testing with OpenAI | Done on four small cases with two models (D32c); also exercised: spend guards, `.env` loading |
| ⚠ | Real-model testing with Anthropic | **Not performed**: no Anthropic key was available. The adapter is tested only against a mock HTTP transport |
| ⚠ | Dollar limits are estimates | Not provider billing (D32d); leave headroom below the real budget |


### D39. Sessions (2026-10-22)

**Problem.** Every prompt created an independent task with a fresh context and a fresh workspace copy, so a follow-up could not refer to what had just happened and a conversation could not be rolled back.

**Chosen.** A session is an ordered list of tasks (turns) sharing a workspace and a saved conversation, with a snapshot after each turn. This keeps every existing guarantee per turn (events, approvals, independent verification, diff, apply, revert) instead of inventing a second execution path. Snapshots are commits written without moving HEAD so the baseline-relative code paths are unchanged. Apply became "from the last applied snapshot to this turn's snapshot" with the same byte-for-byte conflict checks, which also makes applying turn 2 after turn 1 deliver only the difference. Restore rewrites only the workspace copy and marks later turns discarded.

**Alternatives rejected.** Replaying the old messages into a new workspace each turn (loses the edits unless applied); storing the whole conversation in a separate table (a JSON column per turn gives rollback for free: the last live turn is the state); making the model's reasoning payloads persistent (not valid across turns on every provider).

**Consequences.** Tasks from before sessions each became a one-message conversation (migration 0005). Restoring does not undo applied changes. The workspace is a snapshot of the project at the start of the conversation.
