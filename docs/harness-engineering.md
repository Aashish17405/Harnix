# Harness engineering in Harnix: every component, and how it works

This document explains the *harness* in this repository: everything that is not the model. It is written from the code as it is now (module and function names are real; read them next to this file). Harnix has context pruning and compaction, skills, sub-agents, per-repository project memory and configurable thinking levels (several of them modelled on [opencode](https://github.com/sst/opencode)); each is described below together with its limits. Where Harnix does **not** have something that agent harnesses commonly have (MCP, hooks, a planner, parallel tool calls, token streaming, explicit prompt caching), this document says so instead of stretching a word to cover it.

Related documents: [flow.md](../flow.md) (step-by-step execution flow), [decision.md](../decision.md) (why each choice was made), [architecture.md](architecture.md), [security.md](security.md), [api.md](api.md).

---

## 0. Short answers to the questions that prompted this document

| Question | Short answer | Details |
|---|---|---|
| **How is context managed?** | A single message list per task, replayed in full on every model call. It is assembled from a system prompt (plus the skill and sub-agent lists when those exist), one structured first message (task, criteria, detected project info, baseline check results, user-attached files, **an optional knowledge-graph summary**, **project memory notes**, preloaded skills, repository instruction files), then alternating assistant/tool-result turns. Every tool result is size-capped on arrival. Sizes are **estimated in tokens (characters / 4)**. When the estimate passes about half the compaction threshold, old tool output is **pruned** (replaced by a one-line note, no model call); when it passes the threshold (default 170,000 of a 200,000-token window) the older conversation is **compacted**: one extra model call writes a structured checkpoint summary that is appended to the task message, and the newest ~15,000 tokens stay verbatim. A provider "request too long" error forces one compaction and a retry. Both steps rewrite the stored history, and both are events. The only cross-task memory is the per-repository **project memory** notes. See [knowledge-graph.md](knowledge-graph.md) for the optional Neo4j graph context. | [§3](#3-context-management) |
| **How is the loop built?** | A synchronous `while True` in `harness/agent.py:AgentLoop.run`, one per task, running on a worker thread. Each turn: check cancel, charge the iteration budget, prune/compact if the context is large, call the model (with retries), record usage, then either execute the proposed tool calls in order, or treat the reply as a final answer and run **independent verification**. A failed verification feeds the failing output back to the model for a bounded number of recovery turns. Every exit has an explicit reason. The same loop class also runs **sub-agents** (started by the `spawn_agent` tool), which share the task's budget and policy but are not independently verified; only the main agent's answer is. | [§4](#4-the-loop-architecture) |
| **How are tools called?** | The model returns structured tool-use blocks. Nothing runs from the loop directly: every call goes through `ToolExecutor.execute`: propose-event, budget charge, registry lookup, JSON check, strict Pydantic validation, policy decision (allow / approve / deny), optional human approval, execution of the *validated* arguments, redaction, result event. Commands never touch a shell. | [§5](#5-how-tools-are-called) |
| **How are skills used?** | Skills are `SKILL.md` packs found in **operator-configured** directories (`HARNIX_SKILLS_DIRS`; skills inside the target repository are loaded only with `HARNIX_REPO_SKILLS=true` and are labelled untrusted). Only each skill's id, name and description are listed in the system prompt; the body is fetched on demand by the read-only `load_skill` tool (through the normal executor pipeline) or preloaded when the task names the skill. A skill is guidance only: it cannot change policy, tools, approvals or budgets. | [§6.1](#61-skills) |
| **Sub-agents, memory, thinking?** | `spawn_agent` starts a fresh-context child loop (built-in `explore` read-only and `general`, or operator-defined profiles) that shares the workspace, policy, approvals and budget and returns a report the parent must check. `remember` and `forge memory` keep short per-repository notes across tasks. Each task has a thinking level (`low`…`xhigh`) mapped by the provider adapter. | [§6.2](#62-sub-agents), [§6.3](#63-project-memory), [§6.4](#64-thinking-levels) |

---

## 1. The mental model: the model proposes, the harness decides

```
                 untrusted                                   trusted (harness code + operator config)
  ┌──────────────────────────────────┐        ┌────────────────────────────────────────────────────────────┐
  │ model output (text, tool calls)  │───────▶│ validate ─▶ authorize ─▶ (human approval) ─▶ execute ─▶ log │
  │ repository files, tool outputs   │        │ budgets · cancellation · redaction · independent verification│
  └──────────────────────────────────┘        └────────────────────────────────────────────────────────────┘
```

Three rules explain most of the design:

1. **Model output is input.** A tool call is a *proposal*. It is validated, checked against policy and logged before anything happens.
2. **The harness, not the model, decides what "done" means.** A final answer only triggers verification. The task is `completed/verified` only if checks that the harness ran itself pass.
3. **Everything observable is a row.** State changes, model calls, tool proposals, decisions, results and verification are persisted as events; every UI is a view of those rows.

---

## 2. Component map

```
 clients                   api                    harness (orchestration)                 services
┌───────────┐        ┌────────────┐        ┌───────────────────────────────┐        ┌─────────────────────┐
│ React UI  │        │ app.py     │        │ runner.py  TaskRunner         │        │ models/   providers │
│ forge CLI │──HTTP──│ schemas.py │───────▶│  ├ agent.py AgentLoop         │───────▶│ tools/    registry  │
│ VS Code   │  +SSE  │ (routes,   │        │  ├ context.py ContextBuilder  │        │ policies/ rules     │
│ extension │◀───────│  SSE)      │        │  ├ compaction.py              │        │ execution/ sandbox  │
└───────────┘        └────────────┘        │  ├ skills.py agents.py        │        │ verification/       │
                                           │  ├ subagents.py memory.py     │        │ persistence/ store  │
                                           │  ├ references.py states.py    │        │ graph/ (optional)   │
                                           │  └ report.py                  │        └─────────────────────┘
                                           └───────────────────────────────┘
```

Dependency direction (enforced by convention and review, see `AGENTS.md`): `api → harness → {tools, policies, verification, execution, models, persistence}`. `policies` and `execution` never import `harness` or `api`. `persistence/store.py` is the only code that talks to the database.

| # | Component | Main files | One-line job |
|---|---|---|---|
| 1 | Task intake and API | `api/app.py`, `api/schemas.py` | Validate, authorize the repository, persist, start, cancel, stream |
| 2 | Lifecycle state machine | `harness/states.py`, `persistence/store.py:transition` | Explicit statuses and the only legal transitions (compare-and-set) |
| 3 | Event log | `persistence/store.py:add_event`, `models.py:Event` | Single source of truth for everything that happened |
| 4 | Workspace isolation | `execution/workspace.py`, `execution/git.py` | A private copy plus a git baseline, so diffs mean "what the agent changed" |
| 5 | Repository inspection | `execution/project.py` | Detect project type, commands, instruction files |
| 6 | File references | `harness/references.py` | Load files the user pointed at, safely |
| 6b | Knowledge graph (optional) | `graph/*`, `tools/graph.py` | Neo4j graph of the repository: incremental indexer, bounded queries, the context block and the `graph_query` tool ([knowledge-graph.md](knowledge-graph.md)) |
| 7 | Context builder | `harness/context.py` | System prompt, first message (the history itself is no longer rewritten here) |
| 7b | Context management | `harness/compaction.py`, `AgentLoop._manage_context` | Token estimates, pruning of old tool output, checkpoint compaction |
| 7c | Skills | `harness/skills.py`, `tools/agentic.py:load_skill` | Discovery, listing, on-demand loading, untrusted-by-default repository skills |
| 7d | Sub-agents | `harness/agents.py`, `harness/subagents.py`, `tools/agentic.py:spawn_agent` | Agent profiles and the child loops they start |
| 7e | Project memory | `harness/memory.py`, `persistence` (`memories` table), `remember` tool | Short per-repository notes that persist across tasks |
| 8 | Model provider layer | `models/*.py` | Provider-neutral types; Anthropic, OpenAI (Chat Completions and Responses) and mock adapters; thinking levels |
| 9 | Agent loop | `harness/agent.py` | The bounded loop (main agent and sub-agents) and all its exits |
| 10 | Tool system | `tools/*.py` | Registry, strict schemas, the executor pipeline, the tools |
| 11 | Policy engine | `policies/policy.py`, `commands.py`, `paths.py` | allow / approve / deny |
| 12 | Human approvals | `policies/approvals.py` | Approval bound to one exact action |
| 13 | Sandbox | `execution/sandbox.py` | Docker (isolated) or local (explicitly trusted) command execution |
| 14 | Budgets | `policies/budget.py` | Iterations, time, tool calls, tokens, dollars (shared with sub-agents) |
| 15 | Redaction | `policies/redaction.py` | Strip secrets from events, results and logs |
| 16 | Verification | `verification/engine.py` | Baseline, final checks, honest verdict |
| 17 | Report and finalization | `harness/report.py`, `runner.py:_finalize` | Terminal state, structured report |
| 18 | Apply-back | `execution/apply.py` | The only write to the user's repository |
| 19 | Streaming | `api/app.py:stream_events` | SSE over the event table |
| 20 | Front ends | `frontend/`, `backend/harnix/cli.py`, `vscode-extension/` | Three clients of the same API |
| 21 | Evaluation | `evaluations/` | Known tasks, ground truth, honest labelling of mock runs |
| 22 | Configuration | `config.py` | Validated settings, fail-early messages |

---

## 3. Context management

### 3.1 What goes into the context

Context is built in `harness/context.py` by `ContextBuilder`. The model receives three things on every call:

1. **The system prompt** (`ContextBuilder.system()`): the constant `SYSTEM_PROMPT`, which states the working rules (inspect before changing, minimal edits, no shell, don't work around policy denials), declares that tool results, `<repository_instructions>`, `<referenced_file>`, `<knowledge_graph>`, agent-written `<project_memory>` notes and repository skills are **untrusted data**, and says that a plain-text reply with no tool calls means "I'm done" and that the harness will then verify independently ("your claim is not trusted and a failing check will be sent back to you"). Operator-controlled guidance that is stable for the whole run is appended here, so it sits in the cacheable prefix: a short paragraph about the knowledge graph (only when the task has one), the `<available_skills>` list (only when skills exist) and the `<available_agents>` list (always: the built-in `explore` and `general` profiles exist). Sub-agents use a different, shorter prompt (`SUBAGENT_PROMPT`).
2. **The first user message** (`initial_message()`), assembled from sections in this order:

   | Section | Source | Notes |
   |---|---|---|
   | `# Task` | the user's instructions | |
   | `# Acceptance criteria` | `acceptance_criteria` | Only if provided. Free text; never machine-verified |
   | `# Project (detected by the harness)` | `execution/project.py` | Types, package managers, test/lint/typecheck/build commands, notes, up to 60 top-level entries |
   | `# Baseline checks before your changes` | baseline verification | Pass/fail of each check *before* the agent touched anything, so pre-existing failures are known |
   | `# Files the user pointed at` | `references.py` | `<referenced_file path=".." lines="a-b of N" trust="untrusted">…</referenced_file>` blocks, plus `(could not include X: reason)` lines |
   | `<knowledge_graph trust="untrusted">` | `graph/query.py` (only when the graph is enabled and reachable) | Overview, most-depended-on files, and a focus section seeded from attached files, paths/identifiers in the task and the tests failing at baseline; at most `HARNIX_GRAPH_CONTEXT_MAX_CHARS` (6,000), redacted, fenced. The system prompt gains a short paragraph about it and the `graph_query` tool only in this case |
   | `<project_memory trust="mixed">` | `harness/memory.py` (only when the repository has notes and memory is enabled) | User notes first, then the newest agent notes, at most `HARNIX_MEMORY_CONTEXT_CHARS` (4,000); each note is tagged with its source and agent notes are labelled unverified |
   | `<skill_content …>` | `harness/skills.py` (only skills the task named) | Preloaded skill bodies; other skills are loaded on demand with `load_skill` |
   | `<repository_instructions source="AGENTS.md" trust="untrusted">` | repo files | Up to 8,000 characters each, regular files only (symlinks are ignored) |

3. **The running history**: alternating assistant turns (text and tool calls) and user turns (tool results, plus harness notices). Unlike the first two, this part is changed by the harness over a long run (pruning, compaction, §3.4).

Untrusted text is *fenced*: `fence(text, tag)` rewrites any `</tag` inside the content to `</ tag`, so a file that contains `</repository_instructions>` cannot close its own wrapper and smuggle text outside it. This applies to repository instructions, referenced files, the graph block, memory notes, skill content and the compaction summary.

### 3.2 The history is stateless on the wire, stateful in the loop

The provider APIs are stateless, so the loop keeps `history: list[Message]` and sends it on each call (`ContextBuilder.build(history)` now returns it unchanged; shrinking happens on the stored list itself, §3.4). The consequences:

* **Every call resends everything.** Input tokens per call grow with each turn, so total input tokens grow roughly quadratically with run length until a prune or compaction shrinks the list. This is the main cost driver (see the per-task and global dollar caps in §4.6 and §9).
* **Requests share a prefix.** Because pruning and compaction rewrite the stored history only occasionally (rather than recomputing a trimmed copy on every call), consecutive requests are append-only between those steps, which is what provider-side prompt caches need. Harnix itself sets no cache markers (§6.5); it only avoids breaking the prefix needlessly.
* **Provider differences stay in the adapters.** The neutral `Message` has `TextBlock`, `ToolUseBlock`, `ToolResultBlock`. Both the Anthropic and the OpenAI Responses adapter replay an assistant turn **verbatim** from `Message.raw` when present (thinking blocks and encrypted reasoning items stay valid). The OpenAI Chat adapter rebuilds assistant turns from blocks and turns tool results into `tool` role messages that directly follow the assistant `tool_calls`.

### 3.3 Keeping each piece bounded

Context growth is limited at the *source* first:

| Where | Limit (default) | Mechanism |
|---|---|---|
| One tool result sent to the model | 30,000 chars (`max_output_bytes × 1.5`) | `ToolResult.to_text(limit)` cuts the JSON and appends `"[result truncated]"` |
| Command output | 20,000 bytes shown to the model | `tools/base.py:truncate` keeps head and tail; the sandbox collector (`_Collector`) keeps the first and last halves of the stream because test failures are usually at the end |
| `read_file` | 100,000 bytes read, 400 lines by default (max 2,000) | line-windowed; result says `truncated` |
| `list_files` | 200 entries by default (max 1,000) | skips caches, secrets and links |
| `search_text` | 50 results by default (max 200), 300 chars per line | `git grep` |
| Referenced files | 10 files, 400 lines when no range given, 20,000 chars each, 60,000 total | `references.py`; truncation is stated inside the block |
| Repository instruction files | 8,000 chars each | `project.py` |
| Verification feedback after a failed check | 6,000 chars, split across failing checks | `verification_feedback()` |
| Assistant text stored in events | 2,000 chars | `model.response` event |
| Sub-agent report returned to its parent | 20,000 chars | `subagents.py:REPORT_LIMIT` |
| Skill body / skill resource file | 64,000 bytes / 100,000 bytes | `skills.py`; larger skills are skipped and listed as problems |
| Project memory block | 4,000 chars, at most 500 chars per note, 200 notes per repository | `memory.py`, `HARNIX_MEMORY_*` |

### 3.4 When the history gets too large: prune, then compact

Size is estimated, not counted: `compaction.estimate()` sums characters / 4 over the system prompt, the tool specs and every message (there is no tokenizer in the project). The loop calls `AgentLoop._manage_context` at the start of every turn, before the model request. Both steps below change the **stored** history, so later requests are append-only until the next step.

```text
window    = HARNIX_CONTEXT_WINDOW_TOKENS                      (default 200,000)
threshold = window - max(15% of window, 16,000)              (170,000; never below window / 2)
            (HARNIX_COMPACTION_RESERVE_TOKENS overrides the reserve)
estimate >= threshold / 2  ->  try PRUNE
estimate >= threshold      ->  COMPACT
```

**Step 1: prune (no model call).** `compaction.prune()` replaces the content of old tool results with `[pruned to save context] output of <tool> (N tokens) removed; run it again if you need it`.

* The newest `HARNIX_PRUNE_PROTECT_TOKENS` (40,000) of tool output are never pruned; neither is the newest tool-result message (the model has not seen it yet), the task message, results under 50 tokens, or the results of `load_skill` and `spawn_agent` (instructions and sub-agent reports are not bulk output).
* Nothing happens unless at least `HARNIX_PRUNE_MINIMUM_TOKENS` (20,000) can be reclaimed.
* Event `context.pruned {results_pruned, tokens_reclaimed, tokens_before, tokens_after}`.

**Step 2: compact (one extra model call).** If the estimate is still at or above the threshold, `compaction.compact()`:

1. picks a split point: the newest messages worth at most `HARNIX_COMPACTION_KEEP_TOKENS` (15,000) stay verbatim, starting at an assistant turn so tool calls and their results stay paired;
2. renders everything older as a plain-text transcript (each tool result cut to 2,000 chars, whole transcript capped at 400,000 chars) and asks the model, with **no tools** and a system prompt that says the transcript is data, for a structured summary (Objective, Requirements, Decisions, Work State, Next Move, Relevant Files, Important Context);
3. appends the summary to the task message as a fenced `<conversation-checkpoint>` block and keeps the verbatim tail. A second compaction updates the existing checkpoint instead of stacking a new one.

The summary call counts like any other model call (model-call counter, tokens, dollars, time budget; it asks for `low` thinking when the task has a thinking level). It does not consume an iteration. Event `context.compacted {reason, tokens_before, tokens_after, summarized_messages, kept_messages, summary (≤ 4,000 chars)}`.

**When it fails or is not enough:**

* If the summary call errors, returns nothing, or there is nothing older than the kept tail, the loop records `context.compaction_failed` and carries on with the uncompacted history.
* If the provider itself rejects a request as too long (`ProviderError.kind == "context_overflow"`, mapped by the Anthropic and OpenAI adapters), the loop compacts **once** with a much smaller kept tail (`min(keep, max(estimate / 4, 1000))`, because the estimate evidently ran low) and retries the same request. If that does not help, the run ends as `provider_error`.
* `HARNIX_COMPACTION_ENABLED=false` turns both steps off; the history is then sent as it is and a long run ends as a provider error when it outgrows the window.

Caveats worth knowing: the estimate is rough (code and non-English text tokenize at a different ratio than 4 characters per token); the window size is a setting, not read from the model, so set it to the real window of the model you use; the summary is itself model output and can lose or distort detail, which is why the system text tells the model to re-read files for exact contents; pruned output is gone from the model's view (the events keep it). Sub-agents use the same policy. Tests: `tests/test_agentic_unit.py` (estimate, prune, split point, compact, transcript bounds, "never hides results the model has not seen yet") and `tests/test_agentic_harness.py`.

### 3.5 Context the *harness* injects mid-run

The loop adds these user-role messages so the model gets feedback without trusting it (the compaction checkpoint of §3.4 is the fourth kind of harness-written context):

| Message | When | Text (abridged) |
|---|---|---|
| Repeated-failure notice | same failing call ≥ 3 times, at most twice | "Harness notice: you have repeated the same failing call several times without changing anything. Try a different approach." |
| Empty/cut-off notice | reply had no text and no tool call, or `stop_reason == max_tokens` | "Your last response was empty or cut off. Use a tool, or give your final plain-text summary." |
| Verification failure | final answer rejected by independent checks | `The harness ran independent verification (recovery attempt N) and it FAILED…` + reasons + the tail of each failing command's output |

### 3.6 What is deliberately *not* in the context

* **Secrets.** `.env`, keys and credential files are not copied into the workspace; reads are denied; tool output is redacted; the host environment is never forwarded to commands.
* **The user's `.git`.** The workspace gets a fresh repository.
* **Anything from previous tasks, except project memory.** Each task starts from a fresh copy of the repository and an empty history; the only thing carried over is the short, bounded `<project_memory>` block (§6.3).

---

## 4. The loop architecture

### 4.1 Where the loop lives and what surrounds it

```
POST /api/tasks/{id}/run
   └─ TaskRunner.start()            preflight (provider, sandbox) → atomic queued→running → thread pool
        └─ TaskRunner._execute()    (worker thread)
             1. provider = provider_factory()
             2. authorize_repo() again (defense in depth)
             3. WorkspaceManager.create()            isolated copy + git baseline
             4. inspect_project()                    commands, instruction files
             5. load_references()                    (if the task has @file references)
             6. graph index + context                (only if the knowledge graph is enabled; never fails the task)
             7. plan_checks() + VerificationEngine.run()   BASELINE (before the agent starts)
             8. Budget, ToolContext, PolicyEngine, ApprovalGate, ToolExecutor
             9. thinking level, skills (discover), project memory, agent profiles, optional-tool selection,
                ContextPolicy, sub-agent spawner, ContextBuilder
            10. AgentLoop.run()                      ◀── the loop
            11. finally: _finalize()                 diff, report, terminal state, task.finished event
```

`_execute` catches every exception and still reaches `_finalize`, so a crashing task ends as `failed`, never as a zombie `running`. At server start, `recover_orphans()` marks tasks left active by a dead process as `failed` ("Interrupted…"); resuming mid-run is not implemented.

Concurrency: a `ThreadPoolExecutor` with `max_concurrent_tasks` workers (default 2). Starting a task beyond that returns HTTP 429; there is no hidden queue. Cancellation is an in-memory `threading.Event` per task plus a DB flag.

### 4.2 One turn of `AgentLoop.run`

```
while True:
  ① cancelled?                         → return "cancelled"
     sub-agent only: steps >= max_steps → return "max_steps"
  ② budget.next_iteration()            → BudgetExceeded → return "budget_<kind>"
     _manage_context()                 prune / compact the stored history if the estimate is large (§3.4)
  ③ event model.request
  ④ _call_model()                      provider.complete(system, history, tools, max_tokens, timeout, thinking)
        context_overflow        → compact once, retry the same request
        retryable ProviderError → up to 2 retries, backoff 1s, 2s (cancel-aware), event retry.decision
        other ProviderError     → event error, return "provider_error"
  ⑤ add_usage(), bump counters, event model.response (text ≤2000 chars, tool call ids/names,
     usage incl. reasoning tokens, requested vs applied thinking level)
  ⑥ history += assistant message
  ⑦ if the reply has tool calls:
        for each call, in order:  cancelled? → "cancelled";  executor.execute(call)  (§5)
        history += one user message holding all tool results
        update repeated-failure counters; nudge at 3 (max 2), abort at 5
        continue
  ⑧ no tool calls:
        empty text or max_tokens → count it; at 3 → "invalid_responses"; else send a notice and continue
        otherwise it is a FINAL ANSWER:
            sub-agent (verify is None) → return "done" with the text as its report
            verify()                                    (§4.4)
            not verification_failed → return "done"     (outcome verified or unverified)
            recovery budget left    → feed failures back, status back to running, continue
            else                    → return "verification_failed"
```

Tool calls in one reply are executed **sequentially**, in the order the model gave them (no parallel execution). The cancel flag is checked before each call and inside the sandbox supervisor every 100 ms.

A `spawn_agent` call runs a whole child loop inside the parent's tool call (§6.2): the parent's turn blocks until the sub-agent finishes. The child's events are tagged with an `agent` field.

### 4.3 Every way the loop can end

| `AgentOutcome.reason` | Cause | Resulting task state |
|---|---|---|
| `done` | final answer accepted by verification | `completed` with `outcome` = `verified` or `unverified` |
| `verification_failed` | verification still failing after the recovery attempts | `failed`, `outcome=verification_failed` |
| `cancelled` | user cancel (also during an approval wait or a running command) | `cancelled` |
| `budget_iterations` / `budget_duration` / `budget_tool_calls` | a loop limit (tokens and dollars are counted, never limits) | `failed`, error says which |
| `provider_error` | non-retryable provider error, or retries exhausted | `failed` |
| `invalid_responses` | 3 unusable replies in a row | `failed` |
| `repeated_failure` | the identical failing call 5 times with no workspace change in between | `failed` |
| `max_steps` | **sub-agents only**: the step limit was reached without a report. The parent receives a failed tool result, not a task failure | n/a (the parent decides) |
| `error` | an unexpected exception in the runner | `failed` |

### 4.4 Verification inside the loop (and recovery)

When the model stops calling tools, `verify(attempt)` runs (a closure in `runner.py`):

1. `store.transition(VERIFYING)`.
2. `VerificationEngine.run(checks, workspace)` executes every planned check in the sandbox.
3. Results are persisted (`verification_results` rows plus `verification.check` events), then `aggregate(baseline, final)` produces the verdict and a `verification.result` event.

If the verdict is `verification_failed` and `recovery < max_recovery_attempts` (default 2), the loop:

* increments `recovery_attempts`, emits `retry.decision {kind: verification_recovery}`;
* transitions `verifying → running`;
* clears the repeated-failure counters;
* appends the verification-failure user message (§3.5) and lets the model try again.

Recovery turns consume the same iteration, time, token and cost budgets as normal turns. Only the main agent is verified and recovers this way; a sub-agent's final answer is returned to its parent as a report and is never treated as evidence.

### 4.5 Repeated-failure detection

After each tool result the loop computes a signature `sha256(tool name + canonical JSON of the arguments)`. It increments a counter when the call failed (`not ok`) or a command exited non-zero. The counters are cleared whenever a **successful mutating non-command tool** runs (`write_file`, `edit_file`, `delete_file`): the workspace changed, so an earlier failure is no longer comparable. `run_command`/`run_tests` are excluded from the reset (they report `mutated=True` because commands may write files, but running tests again is not "progress"). At 3 the model gets a notice (twice at most); at 5 the run stops.

### 4.6 Budgets (`policies/budget.py`)

One `Budget` object per task, checked at defined points. Sub-agents use the **same object**: their iterations, tool calls, tokens, dollars and wall-clock time count against the task's limits, and a sub-agent hitting a task limit ends the sub-agent and the parent's next check ends the task. Compaction calls are charged too.

| Limit | Default | Checked |
|---|---|---|
| Iterations | 30 (per task via API, can only be lowered) | start of each turn |
| Wall-clock | 1,800 s | before each model call and each turn; **time spent waiting for a human is excluded** (`budget.pause`) |
| Tool calls | 200 | in `ToolExecutor.execute` before the call |
| Tokens | 2,000,000 (input + output; reasoning tokens are already part of output) | start of each turn |
| Dollars | estimated only when prices are set | counted after every model call from `HARNIX_PRICE_INPUT/OUTPUT_PER_MTOK`; reported, never a cap |

Dollar limits refuse to configure without prices (`Settings._check`): Harnix cannot enforce a dollar limit it cannot compute. The estimate counts input tokens at the full rate (it ignores provider cache discounts), so it errs high. Model timeouts are also clamped to the time left: `min(model_timeout, max(5, remaining_seconds))`.

### 4.7 Threads, the database as the message bus, and cancellation

The runner thread and the API threads never share objects; they share **rows**. `Store.transition` is an atomic compare-and-set (`UPDATE … WHERE status IN (allowed sources)`), so two concurrent `run` calls cannot both start a task, and a cancel racing a finish cannot corrupt state. Cancel sets the event; the loop notices between steps, the sandbox supervisor kills the whole process tree (`taskkill /T` on Windows, `killpg` elsewhere) or `docker kill`s the container, and `_finalize` records `cancelled`.

---

## 5. How tools are called

### 5.1 From schema to the model

* Each tool has a strict Pydantic args model in `tools/schemas.py` (`extra="forbid"`: unknown fields are rejected).
* `Tool.spec()` turns it into a provider-neutral `ToolSpec(name, description, input_schema)` using `model_json_schema()`.
* `tools/registry.py` holds the registry (`TOOLS`), and `specs()` is what the loop sends on every call. The agent can only invoke what is registered. Four tools are **optional** (`OPTIONAL_TOOLS`) and are offered only when the task has what they need: `graph_query` (a reachable knowledge graph), `load_skill` (at least one skill), `spawn_agent` (always, in the main agent) and `remember` (memory enabled and `HARNIX_MEMORY_WRITE` is not `deny`). A sub-agent gets its profile's tools intersected with the task's, minus `spawn_agent` and `remember`.
* Adapters translate: Anthropic gets `{"name", "description", "input_schema"}`; OpenAI gets `{"type":"function","function":{…,"parameters": schema}}`.

### 5.2 From the model's reply to a `ToolUseBlock`

Adapters parse tool calls into `ToolUseBlock(id, name, input)`. If the arguments are not valid JSON (or not an object), the adapter does not guess: it puts the raw text under the reserved key `__invalid_json__`, and the executor rejects the call with a message telling the model to resend valid JSON.

### 5.3 The executor pipeline (`tools/executor.py:ToolExecutor.execute`)

This is the **only** path by which a model-proposed action can run. Order matters:

| Step | What happens | Event / counter |
|---|---|---|
| 1 | Record the proposal (before any check, so even invalid proposals are visible) | `tool.proposed {call_id, tool, arguments}` |
| 2 | Charge the tool-call budget | `tool_calls += 1`; may raise `BudgetExceeded` |
| 3 | Look up the tool; unknown name → failure listing the available tools | `tool.result {ok:false}` |
| 3b | **Sub-agents only:** a tool outside the agent's allowlist is refused (counted as a policy denial) | `tool.result {ok:false}` |
| 4 | Reject `__invalid_json__` arguments | `tool.result {ok:false}` |
| 5 | Validate with the tool's Pydantic model; errors are returned field by field | `tool.result {ok:false}` |
| 6 | `PolicyEngine.authorize(tool, validated_args)` → allow / approve / deny | `tool.authorization {decision, reason}` |
| 7 | `deny` → structured failure (`policy_denials += 1`). `approve` → `ApprovalGate.request(...)` blocks for a human decision; not approved → failure | `approval.requested`, `approval.decided` |
| 8 | Run the tool with the **validated** arguments (never the raw model input) | |
| 9 | Exceptions are contained: path/command violations become denials, everything else becomes `Tool error: …`; the loop never crashes on a tool bug | |
| 10 | Redact the result data and error, clip event strings to 4,000 chars, persist | `tool.result {ok, error, duration_seconds, data}` |

The loop then converts the `ToolResult` to JSON text (`{"ok": …, …data, "error": …}`), capped at 30,000 chars, and returns it to the model as a `ToolResultBlock(is_error = not ok)`.

Two accounting subtleties:

* A command that runs and exits non-zero is **`ok: true`** with `exit_code != 0`. It is not a tool failure (the tool did its job), but the loop's repeated-failure detector does count it.
* `tool_failures` counts failed results; `policy_denials` counts the subset refused by policy or by a human.

### 5.4 The toolset

| Tool | Args (all strict) | Policy decision | Mutates? |
|---|---|---|---|
| `list_files` | `path`, `recursive`, `max_entries` | allow (path must resolve inside the workspace; secrets are never listed) | no |
| `read_file` | `path`, `start_line`, `max_lines` | allow; **deny** for secret/credential files | no |
| `search_text` | `pattern`, `path`, `regex`, `case_sensitive`, `max_results` | allow | no |
| `git_status` | none | allow | no |
| `git_diff` | `path?` | allow | no |
| `write_file` | `path`, `content` | allow; **deny** for protected paths (`.git`, `.forge`) or content over the size cap; `approve` for every write under `approval_policy=strict` | yes |
| `edit_file` | `path`, `old_text`, `new_text`, `replace_all` | same as `write_file`; must match exactly once unless `replace_all`; preserves CRLF files | yes |
| `delete_file` | `path` | **approve** always ("deleting files is destructive"); deny for protected paths | yes |
| `graph_query` | `op` (one of 8 fixed operations), `target`, `limit`, `depth` | allow (read-only; offered to the model only when the task has a graph) | no |
| `load_skill` | `id`, `file?` | allow (read-only; offered only when skills exist) | no |
| `spawn_agent` | `agent`, `description`, `prompt` | allow, because each action of the sub-agent is authorized separately by this same policy | indirectly (a `general` sub-agent can edit and run commands) |
| `remember` | `note` (≤ 500 chars) | by `HARNIX_MEMORY_WRITE`: **approve** (default), `allow`, or `deny` | writes a database row, not the workspace |
| `run_command` | `command`, `workdir`, `timeout_seconds` | see §5.5 | commands may change files |
| `run_tests` | `command?` (defaults to the detected test command) | allow for the detected command; otherwise classified like `run_command` | same |

New tool checklist (from `AGENTS.md`): args model, tool function, registry entry, **and** a branch in `policies/policy.py`. A tool that is registered but unknown to the policy is denied (`KNOWN_TOOLS`). If the tool needs a harness resource, make it optional (`OPTIONAL_TOOLS`) and reach the resource through a callback on `ToolContext`, as `tools/agentic.py` does; a tool never touches the runner, the database or the provider directly.

### 5.5 Command policy: argv, never a shell

`policies/commands.py`:

1. `parse_command` rejects shell metacharacters outside quotes (`; | & < > \`` and `$(`) with an explanatory error, then `shlex.split`s into argv. There is no shell anywhere.
2. If the exact command string equals a **trusted** command (a detected project check or a task-supplied `verification_command`), it is allowed.
3. Otherwise `classify_command(argv)`:

| Command | Decision |
|---|---|
| `sudo`, `su`, `shutdown`, `mkfs`, `format`, `chmod`, `chown`, … | **deny** |
| Path-qualified program (`./x`, `C:\x.exe`) | approve |
| `rm`, `del`, `mv`, `rename`, `truncate`, … | approve |
| `git status/diff/log/show/rev-parse/ls-files/blame` without risky flags (`-c`, `--output`, `--ext-diff`, …) | allow |
| `git push`, any other git subcommand, or git with global options | approve |
| `python -m pytest/unittest/ruff/mypy/pyright/flake8/black/isort`, `python --version`, read-only `pip list/show/check/freeze` | allow |
| other `python …` | approve ("arbitrary python execution") |
| `pytest`, `ruff`, `mypy`, `pyright`, `flake8`, `tsc`, `eslint`, `prettier` | allow |
| `npm/pnpm/yarn test` or `run test|lint|build|typecheck|check|type-check`, `--version`, `ls` | allow; any other package-manager command approve |
| `cargo test/check/build/clippy/fmt`, `go test/vet/build/fmt` | allow |
| anything else | approve ("not on the permitted command list") |

`approval_policy=deny_sensitive` turns every `approve` into a `deny` (no human in the loop at all); `strict` additionally requires approval for every file write.

### 5.6 Paths and secrets (`policies/paths.py`)

* `WorkspacePaths.resolve(raw)` resolves relative or absolute input, **follows symlinks first**, then proves the result is inside the workspace root (case-normalised on Windows). NUL bytes, traversal and links pointing out are `PathViolation`.
* Secret-looking names (`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`, `*.pfx`, `.npmrc`, `.pypirc`, `credentials`, and anything under `.ssh/.aws/.gnupg/.kube`) are not copied into the workspace, not listed, not readable, and not applicable back. Placeholder files (`.env.example`, `.sample`, `.template`) are allowed.
* Access outside the workspace is **denied, not approvable** (decision D22): approving would only let a human talk themselves into a bad idea at 2 a.m.

### 5.7 Human approvals (`policies/approvals.py`)

```
executor ── approve decision ──▶ ApprovalGate.request(tool, args, reason)
   1. create approvals row: tool, exact arguments, sha256(task, tool, args), reason, status=pending
   2. event approval.requested        3. task running → awaiting_approval
   4. poll the row every 0.25 s on the worker thread:
        approved  → verify the stored hash equals the hash of the in-memory action → proceed
        rejected  → fail the call ("Not approved: …")
        cancel    → reject, fail the call
        timeout (default 900 s) → expire, fail the call
   5. budget.pause(wait)  – waiting for a person does not eat the time budget
   6. event approval.decided          7. task awaiting_approval → running
```

An approval authorizes **one** `(task, tool, arguments)` action. `decide_approval` is `UPDATE … WHERE status='pending'`, so exactly one decision can be recorded (a second returns HTTP 409). The executed arguments are the validated ones held in memory, not something re-read later. A "no answer" from any front end means "no".

### 5.8 The sandbox (`execution/sandbox.py`)

| | `DockerSandbox` (default) | `LocalSandbox` (explicit opt-in) |
|---|---|---|
| Isolation | container per command: `--network none`, `--cap-drop ALL`, `no-new-privileges`, `--read-only` root with 256 MB tmpfs `/tmp`, non-root user, memory/CPU/PID limits, only the workspace bind-mounted, host environment not forwarded | none; environment is scrubbed and `HOME/TEMP` are redirected into the task directory, but this is **not a security boundary** |
| Timeout / cancel | supervisor kills the CLI **and** runs `docker kill <name>` | `taskkill /T` or `killpg` on the process tree |
| Output | head + tail capture with a hard cap | same |
| Image | `forge-sandbox:latest` (`docker/Dockerfile.sandbox`: Python, git, Node, pytest, ruff, mypy, pyright) | host toolchain |

If Docker mode is configured but the daemon or image is missing, `preflight()` returns HTTP 503 with the exact fix. Harnix never silently falls back to host execution.

---

## 6. Skills, sub-agents, project memory, thinking levels, and what is still missing

These four mechanisms sit on top of the core loop. None of them can widen what the agent is allowed to do: a skill, a sub-agent profile or a memory note is *text or configuration*; every action it leads to still goes through validate → authorize → approve → execute (§5.3).

### 6.1 Skills

A skill is a directory `<id>/SKILL.md` (frontmatter `name`, `description`, optional `disable-model-invocation`, then the Markdown instructions) with optional resource files, or a single `<id>.md` file. Implemented in `harness/skills.py`; tests in `tests/test_agentic_unit.py`.

**Where skills come from**

| Source | Enabled by | Trust |
|---|---|---|
| Operator directories | `HARNIX_SKILLS_DIRS` (path-separated list) | trusted operator guidance (`source="operator"`) |
| The target repository (`.claude/skills`, `.opencode/skills` in the isolated copy) | `HARNIX_REPO_SKILLS=true` (default **off**) | **untrusted**: labelled `source="repository" trust="untrusted"`; an operator skill with the same id always wins |

**Discovery rules.** Symlinks, hidden directories and `node_modules`/`.venv`/`__pycache__` are skipped; at most 200 skills, depth 4; a `SKILL.md` over 64,000 bytes, an empty body, an invalid id (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`) or a duplicate id is skipped and reported as a *problem* (shown by `GET /api/skills` and `forge skills`, and in the `skills.available` event). The frontmatter parser is a deliberately small subset (no YAML dependency); anything it does not understand is ignored, never executed.

**Progressive disclosure.** A skill is *listed* only if it has a description and does not set `disable-model-invocation`. The system prompt gets an `<available_skills>` block with each listed skill's id, name and description and nothing else. The model loads a skill with `load_skill(id)` (the whole `<skill_content>` block, size-capped and fenced) or `load_skill(id, file=...)` for one resource file (contained in the skill's own directory, no links, no secret-looking names, text only, 100,000 bytes). A task can also **preload** skills: `skills: [ids]` on the task (CLI `--skill`, up to 10) puts their content in the first message and records `skill.loaded {how: "preloaded"}`; an unknown id records `skill.missing`. Unlisted skills are not advertised to the model, so preloading is how to use one. On-demand loads appear as ordinary `tool.*` events.

**Boundaries.** `load_skill` is read-only and goes through the executor like any tool. A skill cannot register tools, extend the command allowlist, change `approval_policy` or relax budgets. Loaded skill content is exempt from pruning (§3.4). At task creation the API rejects unknown operator skill ids (unless repository skills are on, because those are only known at run time).

**Limits.** No skill versioning, no remote skill registries, no skill-defined tools or hooks. The web dashboard's task form has a skill picker (from `GET /api/skills`); the VS Code extension has none, so use the dashboard, the API or `forge run --skill ID`.

### 6.2 Sub-agents

`spawn_agent(agent, description, prompt)` starts a child `AgentLoop` (`harness/subagents.py`). The child:

* gets a **fresh context**: only its prompt, the detected project facts and repository instruction files, and the skills list if it can load skills. It does not see the parent's history, references, graph block or memory;
* uses the **same** workspace, `PolicyEngine`, `ApprovalGate` (approvals still reach a human), `Redactor`, `Budget` and cancel flag, through `ToolExecutor.for_agent`, which adds an allowlist and tags every event with `agent` (for example `explore-1`);
* has a **step limit**: `min(profile.max_iterations, HARNIX_SUBAGENT_MAX_ITERATIONS)`, default 15 turns. Hitting it (or any budget, or cancellation) ends the child with a failed tool result; the parent decides what to do;
* cannot start another sub-agent (depth 1) or write project memory;
* is **not independently verified**. Its final text is returned as `report` (≤ 20,000 chars) and is a claim for the parent to check; the final task verdict still comes from the harness-run checks. Edits it makes count as workspace progress for the parent's repeated-failure detector. Its report is exempt from pruning.

Profiles (`harness/agents.py`): built-in `explore` (read-only tools only: `list_files`, `read_file`, `search_text`, `git_status`, `git_diff`, `graph_query`, `load_skill`; thinking `low`) and `general` (every task tool except the two forbidden ones). Operators can add profiles as `HARNIX_AGENTS_DIR/<id>.md` with frontmatter `description` (required: the parent picks agents by it), `tools` (must be registered tool names), `thinking` and `max_iterations`; the body is appended to the sub-agent prompt. Profiles are never read from the target repository. Invalid profiles are skipped and reported (`agents.problems`, `GET /api/agents`, `forge agents`). Events: `subagent.started {agent, profile, description, thinking, tools, max_steps}` and `subagent.finished {agent, reason, steps, report_chars}`.

Honest costs: a sub-agent's tokens count against the task's budgets (so delegating does not hide cost), and the parent's turn blocks while the child runs (no concurrency). A sub-agent's history gets the same pruning and compaction as the main agent.

### 6.3 Project memory

Short notes that persist across tasks on the same repository (`harness/memory.py`, table `memories`, scoped by `repo_key(repo)`; an authorized repository is required for every access).

* **Writers.** The user, through `POST /api/memory` or `forge memory add TEXT` (source `user`), and the agent, through the `remember` tool (source `agent`). Agent writes need human approval by default (`HARNIX_MEMORY_WRITE=approve`; `allow` or `deny` are the alternatives) because a note outlives the task and shapes future ones.
* **Validation.** One fact per note, at most 500 characters, whitespace normalised; a note that the redactor would change (looks like a secret) is refused; identical notes are not stored twice; at most `HARNIX_MEMORY_MAX_ENTRIES` (200) notes per repository.
* **Reading.** At task start the notes are rendered into the first message as `<project_memory trust="mixed">`, user notes first and then the newest agent notes, within `HARNIX_MEMORY_CONTEXT_CHARS` (4,000); omitted notes are counted. Agent notes are labelled unverified because they may have been written after reading untrusted repository text; user notes are presented as the user's instructions. Event `memory.context {notes, shown, chars}` when the block is built, `memory.saved` when an agent note is stored.
* **Management.** `GET/POST /api/memory`, `DELETE /api/memory/{id}`, `forge memory list|add|rm`. `HARNIX_MEMORY_ENABLED=false` switches the whole feature off.

It is a plain table, not a vector store: no search, no summarisation, no expiry. Poisoning risk is reduced (approval, labels, secret check), not removed: an approved agent note is still model-written text that future tasks will read.

### 6.4 Thinking levels

A task has an optional `thinking` level (`low | medium | high | xhigh`; default `HARNIX_THINKING_LEVEL`, `medium`; `forge run --thinking`). The loop passes it to `provider.complete(thinking=...)` and each adapter maps it, then reports what it actually did in `ModelResponse.thinking` (`{"requested", "applied", "reason?"}`), which is stored on the `model.response` event together with reasoning tokens. Sub-agent profiles can set their own level; summary calls for compaction use `low`.

| Adapter | Mapping |
|---|---|
| Anthropic | `thinking={"type": "adaptive"}` plus `output_config={"effort": level}`. Assistant turns are replayed verbatim so thinking blocks stay valid |
| OpenAI Chat Completions (default) | `reasoning_effort` = the level; `xhigh` is retried once as `high` if the model rejects it; if the model refuses reasoning together with function tools (the shipped Luna models) the call is retried without reasoning and the event says so (`applied: "none"` with the reason). `HARNIX_REASONING_EFFORT` is an operator pin that **wins** over task levels (the event shows `reason: pinned by HARNIX_REASONING_EFFORT`) |
| OpenAI Responses (`HARNIX_OPENAI_API=responses`) | `reasoning.effort` works together with function tools; reasoning items are carried between turns as encrypted content (`store=False`, `include=["reasoning.encrypted_content"]`) and replayed from `Message.raw`. A non-reasoning model is detected once and the call is repeated without reasoning. Here the pin applies only when no level is requested; because the runner always passes the task's level (`task.thinking` or `HARNIX_THINKING_LEVEL`), the task level wins in practice, unlike on Chat Completions |
| Scripted mock | echoes the requested level so tests can assert on it |

The level is a request, not a guarantee: always read `applied` on the event.

### 6.5 What is still not built

| Feature | Status in Harnix |
|---|---|
| Token counting | **Estimates only** (characters / 4) for context management. Provider-reported usage is what is recorded and billed |
| Prompt caching | **Not controlled by Harnix.** No cache markers are sent; the harness only keeps the request prefix stable between prune/compaction steps and puts stable guidance in the system prompt |
| Streaming of model tokens | **No.** `provider.complete()` is a single blocking call; the UI streams *events*, not tokens |
| Parallel tool calls / parallel sub-agents | **No.** Executed one after another; a sub-agent blocks its parent |
| Nested sub-agents | **No** (depth 1) |
| Explicit planner | **No.** The model plans freely; the harness's structure is the baseline check, the budgets and the verification feedback loop |
| Hooks | **No.** The policy engine and approval gate are the interception points |
| MCP / external tool servers | **No** |
| Vector store / semantic memory | **No** (out of scope). The knowledge graph is structural retrieval, not semantic memory |
| Resuming a task after a crash | **No** (`recover_orphans` marks it failed) |
| UI for the new features | CLI and API: everything. Web dashboard: thinking select and skill picker in the task form, a project-memory panel (list/add/delete) on the task page, and timeline wording for `context.*`, `subagent.*`, `skill*`, `memory.*` and thinking downgrades (unit-tested; not yet checked in a browser). VS Code extension: a `forge.thinking` setting and the same event wording (unit-tested; not yet re-checked in a VS Code window); no skill picker or memory panel |

---

## 7. The other harness components in detail

### 7.1 Task intake and API (`api/app.py`, `api/schemas.py`)

Routes are thin: validate, map errors, call the runner or store. Highlights:

* `POST /api/tasks` validates the body (unknown fields rejected; `verification_commands` may not contain shell syntax; at most 10 `references`), requires the repository to be inside `HARNIX_ALLOWED_REPO_ROOTS` (`authorize_repo`), and lets per-task limits only **lower** the server limits.
* `POST …/run` calls `preflight()` (provider configured, sandbox available) and claims the task atomically. Errors map to 409 (not startable), 429 (capacity), 503 (not configured, with the fix in the message).
* `POST /api/tasks` also accepts `thinking` (§6.4) and `skills` (§6.1; unknown operator skill ids are a 422).
* Beyond tasks: `GET /api/config` (non-secret settings and limits), `GET /api/skills`, `GET /api/agents`, `GET/POST /api/memory` and `DELETE /api/memory/{id}` (§6.3), and `GET /api/graph/status`, `POST /api/graph/index`, `DELETE /api/graph` for the optional knowledge graph. The full list is in [api.md](api.md).
* `GET …/events/stream` is the SSE endpoint (§7.8). `GET …/diff`, `…/verifications`, `…/approvals`, `…/report?format=json|md`, `POST …/rerun` (new queued task with the same definition and `parent_task_id`), `POST …/apply` (§7.7).
* Optional bearer token (`HARNIX_API_TOKEN`), compared with `hmac.compare_digest`; the stream also accepts `?token=` because `EventSource` cannot set headers. Errors have one shape: `{"error": {"code", "message", "details?"}}`.

### 7.2 State machine (`harness/states.py`)

```
queued ──▶ running ◀──▶ awaiting_approval
              │  ▲
              ▼  │
          verifying ──▶ completed
   (running | awaiting_approval | verifying) ──▶ failed | cancelled        queued ──▶ cancelled
```

`TRANSITIONS` is a dict of allowed edges; `Store.transition` computes the allowed *source* statuses for the target and performs a single `UPDATE … WHERE status IN (…)`. Zero rows updated means the edge was illegal for the current state and raises `InvalidTransition` (HTTP 409). `completed`, `failed`, `cancelled` are terminal. A task that completes carries `outcome ∈ {verified, unverified, verification_failed}` separately from its status (D8).

### 7.3 Event log (`persistence`)

Tables: `tasks` (definition including `thinking` and `skills`, limits, counters, report, final redacted diff), `events` (append-only, `(task_id, id)` indexed), `verification_results`, `approvals`, `memories` (project notes, not tied to one task's lifetime). `Store.add_event` redacts the payload before writing. Event types: `task.created`, `task.state`, `task.cancel_requested`, `task.finished`, `task.applied`, `task.references`, `workspace.created`, `project.inspected`, `model.request`, `model.response`, `tool.proposed`, `tool.authorization`, `tool.result`, `approval.requested`, `approval.decided`, `verification.check`, `verification.result`, `retry.decision`, `error`, plus the newer families: `context.pruned`, `context.compacted`, `context.compaction_failed` (§3.4); `subagent.started`, `subagent.finished` (§6.2); `skills.available`, `skill.loaded`, `skill.missing`, `agents.problems` (§6.1–6.2); `memory.context`, `memory.saved` (§6.3); `graph.indexed`, `graph.context`, `graph.unavailable` (knowledge graph). Events written by a sub-agent (its model, tool and approval events) carry an `agent` field with its label; events without it belong to the main agent.

Because the UI, CLI, VS Code panel, the Activity "receipt" and the report are all derived from these rows (matched by `call_id` where needed), there is no second log that can drift.

### 7.4 Workspace isolation (`execution/workspace.py`, `execution/git.py`)

`WorkspaceManager.create` copies the repository (including uncommitted work) to `HARNIX_WORKSPACE_ROOT/<task_id>`, skipping `.git`, caches, `node_modules`, `.venv`, secret directories/files and every symlink, with a size cap (500 MB). It then makes a **fresh** git repository with a baseline commit, so no untrusted config, hooks or remotes from the source repo exist. `.git/info/attributes` is set to `* -text -eol -filter -ident` so the baseline stores exact bytes (CRLF files, repository EOL rules and filter drivers cannot distort diffs). All git calls use a hardened environment: no system/global config, no hooks, no fsmonitor, no external diff or textconv. The source repository is never modified by the agent.

### 7.5 Repository inspection (`execution/project.py`)

Detects Python (pip/poetry/uv/pipenv; pytest, ruff, pyright/mypy), Node (npm/pnpm/yarn; `test`, `lint`, `typecheck`, `build` scripts), Rust and Go, notes a `Makefile` (targets are not auto-run), and records "No test command detected" when applicable. Reads `HARNIX.md`, `AGENTS.md`, and other repo instruction files. Everything read here is data from an untrusted source.

Honest caveat: detected commands such as `npm test` execute repository-controlled scripts. In Docker mode that happens inside the no-network container; in local mode it is host execution of repository code, which is why local mode is an explicit opt-in.

### 7.6 Verification (`verification/engine.py`)

* `plan_checks`: task `verification_commands` (kind `acceptance`) first, then the detected test, lint and typecheck commands. If nothing blocking exists, a placeholder `tests` check is added as *unavailable* ("No test command detected"). Free-text acceptance criteria become `criterion` checks that are always *unavailable* ("needs human review").
* **The model cannot choose which checks run.** They come from the harness's detection or from the user.
* Baseline run before the agent starts; final run after each completion claim. Timeout per check is `tool_timeout × 3`.
* Statuses: `passed`, `failed`, `skipped`, `unavailable`. Special cases: pytest exit code 5 (no tests) and "No module named pytest/ruff/…" are `unavailable`, not `passed` or `failed`.
* `aggregate(baseline, final)`:
  * a failing **blocking** check (`test`, `acceptance`) blocks, even if it also failed before the task;
  * a failing lint/typecheck blocks only if it was **not** already failing at baseline (otherwise it is recorded as a limitation);
  * `verified` requires at least one blocking check to pass and none to fail;
  * otherwise `unverified`, with the reason ("No test or acceptance check passed…").
* `false_success_claim` in the report is true when the model claimed done and the verdict is `verification_failed`.

### 7.7 Finalization, report, apply-back

`_finalize` always runs: it diffs the workspace, maps the loop's outcome to a terminal status, stores the **redacted** diff (≤ 1 MB), builds the report (status, outcome, provider/model, agent claim, independent verdict, baseline and final checks, false-claim flag, changed files, usage and estimated cost, **known limitations** such as local mode, mock provider, unavailable checks), redacts it, transitions (only if the task is still active) and emits `task.finished`.

`execution/apply.py` is the **only** code that writes to the user's repository, and only after explicit confirmation (`confirm=true`; `dry_run=true` previews):

* works from the raw workspace files, never the redacted diff (applying a redacted diff could write `[REDACTED]` into source);
* writes a file only if the repository copy is still byte-identical to the baseline blob (or absent, for added files), so changes you made since the task started are never overwritten;
* validates everything first and is all-or-nothing; a failed write mid-way restores earlier files;
* refuses secret-looking files, `.git`/`.forge`, traversal, absolute/drive paths and writes through links;
* once only (`task.applied` event), only for `completed` tasks, and re-checks the repository is still authorized.

### 7.8 Streaming (`api/app.py:stream_events`)

SSE by polling the event table every 0.5 s: unnamed `data:` messages (clients need only `onmessage`), `id:` is the event id (resume via `Last-Event-ID` or `?after=`), a keep-alive comment every ~15 s, and an `event: end` after two quiet polls following a terminal state (so everything has been flushed). Polling the table (rather than an in-process pub/sub) means events written by any thread, or after a reconnect, are never missed (D5).

### 7.9 Redaction (`policies/redaction.py`)

Best effort, not a guarantee: exact configured secrets (API keys, token), known token shapes (`sk-…`, AWS access keys, GitHub tokens, Slack tokens, PEM private keys, bearer headers) and `NAME=value` pairs where the name looks secret *and* the value looks secret (≥ 8 chars with a letter and a digit, so code like `max_tokens=16000` is untouched). Applied to events, tool results and errors, the stored diff, the report and logs. The workspace never receives secret files to begin with.

### 7.10 Provider layer (`models/`)

`ModelProvider` is a `Protocol` with one method, `complete(system, messages, tools, max_tokens, timeout) -> ModelResponse`. Adapters map SDK errors into `ProviderError(retryable, kind)`: rate limits, timeouts, network errors and 5xx are retryable; auth, permission, not-found, bad request, refusals, quota exhaustion and config problems are not. The loop adds its own retry (2 attempts with backoff) on top of the SDK's `max_retries=2`.

Adapters also classify "request too long" as `kind="context_overflow"` (which makes the loop compact once, §3.4) and map the requested thinking level (§6.4).

* `OpenAIProvider` (Chat Completions, the default): `max_completion_tokens`, `reasoning_effort` from the thinking level or the operator pin, recognises `insufficient_quota` as fatal and the "function tools need reasoning none" error (it then retries without reasoning and records that). **Exercised against the real API** (cost measurements in the README).
* `OpenAIResponsesProvider` (`HARNIX_OPENAI_API=responses`): the Responses API, where reasoning and function tools work together; reasoning is carried between turns as encrypted items. Covered by unit tests of request shape, parsing, replay and downgrade behaviour (`tests/test_agentic_unit.py`); this review did not run it against the real API.
* `AnthropicProvider` (Messages API): verbatim assistant replay; a thinking level is sent as adaptive thinking with an effort setting. **Never run against the real API** in this project; it is tested only against a mock HTTP transport.
* `ScriptedProvider`: replays a JSON script. It reports itself as `scripted-mock`; the task, API, UI and evaluation output all say so. Never quote its results as model performance.

### 7.11 Front ends

* **Web dashboard** (`frontend/`): React + Vite. `useTaskLive` loads the task, verifications, approvals and diff, opens an `EventSource`, de-duplicates by event id, refetches derived resources (debounced) after each batch of events, and polls every 5 s while the task is active as a safety net. Approval buttons, cancel, diff viewer and verification panel show backend data only.
* **`forge` CLI** (`backend/harnix/cli.py`, `client.py`): standard-library HTTP/SSE client. Interactive and one-shot modes, `--thinking LEVEL` and `--skill ID`, subcommands `doctor`, `tasks`, `apply`, `skills`, `agents`, `memory`, `graph` and `serve`, inline `[y/N]` approvals (no terminal means "no"), `@file` mentions turned into references, distinct exit codes (0 verified, 4 completed but unverified, 1 failed, 2 usage, 3 backend unreachable, 130 cancelled), `harnix serve --allow DIR` (applies migrations, authorizes a folder for that process, refuses a non-loopback host without a token).
* **VS Code extension** (`vscode-extension/`): `core.ts` (API client, SSE parser, event wording, mention parsing, fuzzy search, activity receipt, stats formatting), `session.ts` (run, approve, cancel, apply, live stats against an abstract UI), `extension.ts` + `webview.ts` (VS Code glue; the panel uses `textContent` only and a nonce CSP). Webview messages are never trusted: opening a file or a diff re-validates the path in the extension host.

### 7.12 Evaluation (`evaluations/`)

Five cases (`off_by_one`, `wrong_operator`, `missing_feature`, `claims_without_fix`, `injection_attempt`), each with a failing repo and a **ground-truth command** run independently after the task. Metrics: completion rate, verification pass rate, ground-truth pass rate, **false success claims**, **harness false accepts** (verified but ground truth fails: a harness bug), tool failures, retries, blocked policy violations, duration, tokens, estimated cost. Scripted runs are labelled `valid_for_llm_claims: false`. Real runs support a `--budget-usd` stop.

### 7.13 Configuration (`config.py`)

Validated `Settings` from `HARNIX_*` environment variables (plus `.env` discovered in the repository root, `backend/` and the working directory). Failure is early and actionable: placeholder keys, a model that does not match the provider, non-positive limits. Key defaults: 30 iterations, 1,800 s, 200 tool calls, 2 recovery attempts, 120 s tool timeout, 20,000-byte output cap, 100,000-byte read cap, 200,000-byte write cap, 900 s approval timeout, 2 concurrent tasks, `approval_policy=sensitive`, `execution_mode=docker`. Newer groups (all `HARNIX_*`; size-like values must be positive): **context** `CONTEXT_WINDOW_TOKENS` 200,000, `COMPACTION_ENABLED` true, `COMPACTION_RESERVE_TOKENS` 0 (= 15 % of the window, at least 16,000), `COMPACTION_KEEP_TOKENS` 15,000, `PRUNE_PROTECT_TOKENS` 40,000, `PRUNE_MINIMUM_TOKENS` 20,000; **thinking** `THINKING_LEVEL` medium, `OPENAI_API` chat, `REASONING_EFFORT` unset; **skills and agents** `SKILLS_DIRS` empty, `REPO_SKILLS` false, `AGENTS_DIR` empty, `SUBAGENT_MAX_ITERATIONS` 15; **memory** `MEMORY_ENABLED` true, `MEMORY_WRITE` approve, `MEMORY_CONTEXT_CHARS` 4,000, `MEMORY_MAX_ENTRIES` 200; **graph** `GRAPH_ENABLED` false plus its limits (see [knowledge-graph.md](knowledge-graph.md)). The repository's own `.env` overrides some of these for this deployment (model, prices).

---

## 8. A real run, event by event

Observed in a real VS Code session against the live backend (model `gpt-6-luna`, Docker sandbox), task: "range_sum in @rangesum.py#1-3 returns wrong sums. Fix it and run the tests. Hints may be in @.env." The run is a recorded example and uses only the core tools; none of the optional mechanisms of §6 (skills, sub-agents, memory, compaction) appear in it, and it was not re-run for this update. The timeline the user saw, mapped to harness stages:

| What the timeline showed | Stage |
|---|---|
| Isolated workspace ready (docker) | `workspace.created` (copy + git baseline; `.env` was **not** copied) |
| Detected python; test command: python -m pytest -q | `project.inspected` |
| Attached 1 file(s): rangesum.py; could not attach .env: looks like a secret or protected file | `task.references` (the secret mention refused with a reason) |
| [failed] test: python -m pytest -q (baseline) | baseline verification: the bug exists before the agent starts |
| list_files, read_file ×2, git_status | read-only tools, policy `allow` |
| edit_file rangesum.py (inline red/green preview) | policy `allow` for a file change inside the workspace |
| run_tests → exit 0 | detected test command, trusted, `allow` |
| git_diff | read-only |
| model's plain-text summary | no tool calls → treated as a *claim* |
| Running independent verification… [passed] test (final) → Verification: verified | `verifying` state, harness-run checks, `aggregate()` → `verified` |
| Card: Completed: verified by independent checks | `_finalize`; workspace file still untouched until **Apply** |

Counters for that run: 5 model calls, 7 tool calls, about 8.0k input / 193 output tokens, about $0.0009 estimated, about 20 s. Note how input tokens (8.0k) are dominated by resending the history (§3.2), not by the answer.

---

## 9. Failure behaviour at a glance

| Situation | What the harness does |
|---|---|
| Model asks for an unknown tool or sends invalid JSON / wrong arguments | Structured error back to the model; counts as a tool failure; no side effect |
| Model asks for `../../etc/passwd` or a symlink escape | `PathViolation` → `deny`; counts as a policy denial |
| Model asks to read `.env` | `deny` ("Reading secret/credential files is not permitted") |
| Model runs `rm -rf x`, `git push`, arbitrary `python -c …` | `approve`: waits for a human; reject, timeout or cancel means it does not run |
| Model tries `sudo` or a shell pipeline | `deny` / syntax error; never executes |
| Command hangs | killed at the timeout (whole process tree or container); result says timed out |
| Model repeats the same failing call | notice at 3, stop at 5 |
| Model returns empty output | notice; stop after 3 in a row |
| Model claims success but tests fail | claim rejected; failure output fed back; up to 2 recovery turns; else `failed` with `false_success_claim` |
| No tests exist | final verdict is `unverified`, never `verified`, and says why |
| Provider rate limit / 5xx / timeout | 2 retries with backoff (cancel-aware); then `provider_error` |
| Context estimate reaches the threshold | Old tool output is pruned, then the older conversation is summarized into a checkpoint (§3.4); if that fails the run continues uncompacted and says so in `context.compaction_failed` |
| Provider rejects the request as too long | One forced compaction with a smaller kept tail, then a retry; if that fails, `provider_error` |
| Sub-agent hits its step limit, a budget, or errors | The parent gets a failed tool result (`Sub-agent … stopped without a report`) and carries on; task budgets still apply to the whole task |
| Agent asks to save a memory note | Needs approval by default; a note that looks like a secret, is too long, is a duplicate or exceeds the cap is refused with a reason |
| Model rejects the requested thinking level | The adapter downgrades or drops it and records `requested` vs `applied` with the reason on `model.response` |
| Budget (iterations, time, tokens, tool calls, dollars) exhausted | task ends `failed` with the exact limit |
| User cancels | event set; running process or container killed; approvals rejected; terminal `cancelled` |
| Server restarts mid-task | task marked `failed` ("Interrupted") at next start; rerun to retry |
| Repository changed between task and apply | apply refuses and writes nothing |

---

## 10. Known limitations and honest caveats

* **Context:** token counts are estimates (characters / 4) and the window size is a setting, not read from the model, so a wrong `HARNIX_CONTEXT_WINDOW_TOKENS` makes compaction fire too early or too late; the history is still resent on every call. Compaction summaries are model-written and lossy, and each one costs an extra model call. If a request is still too long after the one forced compaction the run ends as a provider error. `ContextBuilder` still accepts a `max_chars` argument that is no longer used (the old 600,000-character elision was replaced by §3.4).
* **Skills, sub-agents and memory** are text and configuration, not security boundaries: a repository skill is untrusted and off by default, an approved agent memory note is still model-written text that later tasks read, and a sub-agent's report is a claim, not evidence. Sub-agents run one at a time inside the parent's turn and share its budgets.
* **Not implemented:** MCP, hooks, explicit prompt-cache control, token streaming, parallel tool calls, nested sub-agents (§6.5).
* **Clients:** the dashboard has thinking, skill and memory controls and the extension a thinking setting; both render the newer event types, verified by unit tests but not yet by looking at them in a browser or VS Code window.
* **Anthropic adapter:** tested with a mock transport only; never run against the real API here. The OpenAI path is the one exercised for real.
* **Dollar limits are estimates**, with input tokens billed at the full rate.
* **Local execution mode is not a security boundary.** Docker is also not claimed to remove all risk.
* **Redaction is best effort.**
* **Mixed trust in detection:** detected commands run repository-defined scripts (inside the container in Docker mode).
* **Concurrency model:** single process, thread pool, SQLite or PostgreSQL; not distributed.
* The extension's `.vsix` has not been installed into the user's own VS Code; native Windows dialogs, light themes and macOS/Linux were not verified (see the README).

## 11. How to extend (each step is a rule from `AGENTS.md`)

* **Add a tool:** args model in `tools/schemas.py` → function in `tools/` → registry entry → decision branch in `policies/policy.py` (unknown tools are denied) → tests including the failure path.
* **Add a provider:** one adapter implementing `complete(..., thinking=None)` that maps errors to `ProviderError` (use `kind="context_overflow"` for "request too long"), maps the thinking level and reports what it applied, and replays history correctly; register it in `models/factory.py` and `Settings.provider`.
* **Add a skill:** a `<id>/SKILL.md` with `description` frontmatter in a directory listed in `HARNIX_SKILLS_DIRS`; no code change. Check `forge skills` for problems.
* **Add a sub-agent profile:** `<id>.md` in `HARNIX_AGENTS_DIR` with a `description` and, optionally, `tools`, `thinking`, `max_iterations`; no code change. Check `forge agents`.
* **Add an optional, resource-backed tool** (like `load_skill`): add it to `OPTIONAL_TOOLS`, reach the resource through a `ToolContext` callback, decide in `policies/policy.py` whether sub-agents may use it (`NEVER_FOR_SUBAGENTS` in `harness/agents.py`), and have the runner enable it only when the resource exists.
* **Widen the command allowlist:** a security change; add a test in `tests/test_policies.py`.
* **Add a state or edge:** only in `harness/states.py`; update `flow.md`.
* **Change the schema:** Alembic migration, tested up and down on SQLite and PostgreSQL.
* **Add an evaluation case:** `evaluations/cases/<id>/{case.json, repo/}` with a failing repo and a ground-truth command (see `docs/testing.md`).


## Sessions: conversations that keep their context

A *session* (`chat_sessions`) is a conversation; every message in it is a normal task (a *turn*) with `session_id`, `turn`, `discarded`, `base_ref`, `checkpoint` and `messages` columns. What makes a follow-up a follow-up:

* **One workspace.** The first turn creates the workspace under the session id; later turns find it again (`workspace.reused`) and continue in it. HEAD of its git repository stays the baseline; after each turn `git.snapshot` writes the working tree as a commit (parent = the previous snapshot) without moving HEAD, and that sha is the turn's `checkpoint`. A turn's diff and changed files are against its `base_ref` (the previous checkpoint), so each message shows only its own changes, and verification's syntax check looks only at what that turn changed.
* **One conversation.** At the end of a turn that finished cleanly (`done` or `verification_failed`) `transcript.dump_messages` stores what the model saw as JSON on the task (no provider `raw` payloads, images as `task:index` references). The next turn loads the last saved conversation of the session's *live* turns, appends a short note for each later turn that ended early (`summary_of_unsaved_turn`), and `ContextBuilder.followup_message` adds the new request with where things stand and freshly measured baseline checks. Pruning and compaction then work on it as on any long history (`session.continued` event).
* **Apply and revert from checkpoints.** Applying a turn moves the repository from `chat_sessions.applied_ref` (the last applied checkpoint, or the baseline) to the turn's checkpoint, comparing files byte-for-byte with what is expected to be there; the `task.applied` event records `from_ref`/`to_ref`, which `revert` uses to restore the originals (refused when a later turn was applied after).
* **Restore.** `POST /api/sessions/{id}/restore` runs `git read-tree --reset -u <checkpoint>` plus `git clean` on the workspace and marks later turns `discarded`; the next turn builds its conversation and base from the last live turn. The repository is never touched.
* **Guards.** One active turn per session (409), same repository only (422), no restore while running.

Not done: sub-agents and background agents see only their own prompt, not the conversation; a session cannot be shared between repositories; the workspace does not pick up edits made to the real project after it was created.
