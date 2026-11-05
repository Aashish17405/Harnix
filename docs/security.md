# Security

Harnix runs an AI model that writes and executes code on a repository you point it at. Assume the model can be wrong, manipulated by text inside the repository (prompt injection), or simply careless. This page states what Harnix enforces, where each control lives, how it is tested, and, importantly, what it does **not** protect against.

## Threat model

| Threat | Control |
|---|---|
| Model proposes an action outside the task (read host files, traversal, exfiltration) | All paths resolve through `WorkspacePaths` (`policies/paths.py`); escapes via `..`, absolute paths, symlinks/junctions are denied; secret-looking files cannot be read |
| Model runs a dangerous command | No shell; argv classified allow / approve / deny (`policies/commands.py`); unknown or risky commands need a human approval; `sudo`-class commands are refused |
| Text in the repo or tool output tells the model to ignore the rules | Policy is code evaluated on every call; nothing the model reads can change it. Instruction files and tool output are labelled untrusted in the prompt. Covered by the `injection_attempt` evaluation case |
| Approval confusion ("you approved X, so I ran Y") | An approval stores the exact tool + arguments + hash, allows one decision, and is checked before execution (`policies/approvals.py`) |
| Model claims success falsely | The harness runs its own checks (with a baseline); `verified` needs a passing test/acceptance check (`verification/engine.py`) |
| Runaway loops / cost / hangs | Hard limits on iterations, time, tool calls, tokens, output sizes, retries; repeated-failure abort (`policies/budget.py`, `harness/agent.py`) |
| Malicious repository tooling (git config, hooks) | Workspace gets a *fresh* git repo; git calls disable hooks/fsmonitor/ext-diff and ignore global/system config (`execution/git.py`) |
| Secrets leaking to the model, logs or UI | `.env`/key files are not copied; sandbox environment is rebuilt from an allowlist; `Redactor` scrubs events, outputs, reports, logs, diffs |
| Cancelled tasks leaving processes behind | Cancellation kills the whole process tree / container (`execution/sandbox.py`); tested including grandchildren |
| Two tasks corrupting each other or the source repo | Each task has its own workspace copy; the source is never written |
| Unauthorized API use | Optional bearer token; default bind to localhost |

## Execution modes

**Docker (default).** Per command: `docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp --user <non-root> --memory --cpus --pids-limit`, with only the workspace bind-mounted at `/workspace`. The host environment is not forwarded. Timeout/cancel runs `docker kill` on the named container. Tested against real containers (`tests/test_execution.py::TestDockerReal`): workspace-only mount, non-root, read-only root, no network, no host env, container killed on timeout and on cancel.

**Local (explicit opt-in).** Runs on the host as your user. The environment is scrubbed (no API keys or cloud credentials; per-task `HOME`), and process trees are killed on timeout/cancel, **but this is not a security boundary**: an approved or allowlisted command (for example `pytest`, which imports repository code) can do anything your user can. Use it only for repositories you trust. Harnix logs a warning at startup and the UI shows a banner.

## Human approvals

`HARNIX_APPROVAL_POLICY`: `sensitive` (default: ask for destructive/unlisted actions), `strict` (also ask before every file write), `deny_sensitive` (never ask, refuse; for unattended and evaluation runs). Approvals expire after `HARNIX_APPROVAL_TIMEOUT_SECONDS`, are rejected on cancel, and approval wait time does not count against the task's duration budget.

Access outside the workspace is **denied outright rather than approvable** (see decision D22).

## Writing to your repository (apply)

Until you apply, nothing the agent does touches your repository. Applying (`POST /api/tasks/{id}/apply`, used by the CLI, the VS Code extension and the dashboard) is the only write path and is guarded by `execution/apply.py`: it needs an explicit `confirm`, works from the raw workspace files (the redacted diff is never applied, so redaction cannot corrupt your code), writes a file only if your copy is still byte-identical to what the task started from, validates every file before writing any, refuses secret-looking files, `.git`/`.forge`, traversal, drive paths and links that point outside the repository, and rolls back earlier writes if one fails. Tested in `tests/test_apply.py`.

Which folders a task may target is `HARNIX_ALLOWED_REPO_ROOTS`. Setting it to `*` allows any existing folder (a drive root is always refused): convenient on your own machine, but then anyone who can reach the API can point a task, and later an apply, at any folder the Harnix process can write to. Keep the API on localhost or set a token when you use it. Empty still means nothing is allowed. The apply guards above hold either way.

Limits worth knowing: the diff of what is applied is whatever the agent produced, so **review it**: verified means the checks passed, not that every change is wanted (unverified results can be applied, and every front end warns). Executable bits are copied on POSIX only. If the repository changed while the task ran, apply refuses instead of merging.

## Front ends

* The CLI and the extension hold no agent logic and execute nothing themselves; they call the local HTTP API. The extension stores the optional API token in VS Code's secret storage, renders all model/repository text with `textContent` under a nonce-based CSP (no markup injection from a hostile repository), and never answers an approval on your behalf (a dismissed notification or a missing terminal means no approval).
* They talk to `http://127.0.0.1:8000` by default over plain HTTP: anything running as you on the same machine can reach the API unless you set `HARNIX_API_TOKEN`. `harnix serve` refuses to listen on a non-local address without a token.

## Secrets

* Never commit credentials: `.gitignore` excludes `.env*` (except `.env.example`, which holds placeholders). Keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `HARNIX_API_TOKEN`) are read from the environment or `.env` only; none are hardcoded, all are registered with the redactor, and none are passed to sandboxed commands. The real `.env` in the repository root contains a live key: keep it out of version control, backups and screen shares.
* A leaked or over-used key is bounded only by provider-side limits. Harnix measures and shows estimated spend but **does not cap it** (the old dollar and token caps were removed), so set a spending limit at the provider. A runaway task is still bounded by the iteration, time and tool-call limits.
* The API never returns secrets; `GET /api/config` omits them and the health endpoint reports only whether a key is configured.
* Redaction is **pattern based and best effort**: known token formats (Anthropic/OpenAI-style keys, AWS, GitHub, Slack), private key blocks, `Authorization: Bearer`, `name=value` pairs with secret-shaped values (8+ chars mixing letters and digits), and the exact values of configured secrets. A secret in an unusual format can slip through.

## Conversations (sessions)

* A conversation's workspace is one isolated copy made at its start. Checkpoints are git commits written *without moving HEAD* (HEAD stays the baseline), so the apply logic that compares against the baseline keeps its meaning.
* Apply moves your repository from the last applied checkpoint to the end of the chosen message, with the same strictness as before: every file must still be byte-identical to what the previous apply left (so anything you edited is refused, never overwritten), nothing is written unless everything checks out, secret-looking files, `.git`, symlinks and escapes are refused. Revert is refused when a later message was applied after it.
* Restore only rewrites the workspace copy and marks later messages discarded; it never writes to your repository. It is refused while a message is running.
* The saved conversation is exactly what the model saw (tool output was already redacted when it was produced); provider-private reasoning payloads and image bytes are not stored in it. Rolled-back messages stay in the database for the record but are never given to the model again.

## Images

* Images are untrusted input. The type comes from the bytes (PNG, JPEG, GIF, WebP only; SVG is refused because it can carry script), the base64 must decode strictly, sizes and pixel dimensions are bounded, names are sanitized, and the declared type must agree with the bytes.
* They are stored in the `attachments` table, served back only through the authenticated API with `nosniff` and a `default-src 'none'` CSP, and **never** written into events, logs or reports (only name, type and size are).
* The model is told that text inside an image is data, never an instruction. That reduces but does not remove the risk that an image steers it: policy, approvals and independent verification still decide what runs and what counts as done.
* Remaining limits: images go to the model provider unchanged and are not scanned for secrets; only the main agent sees them; `HARNIX_IMAGES_ENABLED=false` switches the feature off.

## Skills, sub-agents and project memory

* **Skills are instructions**, so where they come from matters: operator directories only by default; skills inside a target repository (`.claude/skills`, `.opencode/skills`) load only with `HARNIX_REPO_SKILLS=true` and are labelled `trust="untrusted"`, and an operator skill always wins over a repository skill with the same id. Skill content is fenced so it cannot close its own wrapper; skill resource files are read with workspace-style containment (no traversal, no links out, no secret-looking files, size-capped). A skill never changes policy: whatever it suggests still passes validation, policy and approval.
* **Sub-agents** run with the same executor (policy, approvals, redaction), a profile allowlist enforced by the executor (a call outside it is refused and counted as a denial), the task's budget and cancellation, and cannot start sub-agents or write memory. Background sub-agents are limited to read-only profiles (a writer would race the parent on the workspace and on the single approval state), run on a bounded thread pool, share a lock-protected budget, are stopped and joined before the task is finalized (also on cancellation), and their reports reach the parent fenced as untrusted data. Their reports are not evidence; only independent verification decides the outcome.
* **Project memory persists across tasks**, which makes it a persistence channel for prompt injection: a note an agent was tricked into writing would reach every later task. Mitigations: agent writes need human approval by default (`HARNIX_MEMORY_WRITE=approve`; `deny_sensitive` refuses them), agent notes are labelled unverified in the model's context, notes are short, deduplicated, refused if secret-shaped and redacted, and users can list and delete them (`forge memory`, the dashboard panel, the API). Memory is keyed per repository; anyone with API access can read the notes of authorized repositories.
* **Context management** edits the stored conversation (pruning/compaction). The summary call has no tools and a system prompt saying the transcript is data; its output is fenced. Summaries can still lose detail; the agent is told to re-read files rather than trust exact contents from a summary.

## Knowledge base

* **Untrusted content.** Documents, tickets and code are repository-like text: the bundle is fenced as untrusted in the prompt, the system prompt says it is data, and nothing in it can change policy, approvals or paths. Ingested text is redacted before it is stored.
* **Access.** Project-level allow-list per bearer token (`HARNIX_KNOWLEDGE_ACL`), enforced on ingest, retrieval, bundles, edge decisions and task creation; the MCP server serves only the projects in `HARNIX_KNOWLEDGE_PROJECTS`, fixed at start (the runner sets it to the task's project). With no ACL configured every caller sees every project: fine for a proof of concept, not for shared use. There are no per-document permissions.
* **Ingestion paths** are named by the operator (the agent has no ingest tool). With `HARNIX_KNOWLEDGE_ROOTS` empty, any existing folder the Harnix process can read may be indexed, so anyone who can reach the API can copy that folder's text into the knowledge base and read it back by search: keep the API on localhost or set a token and `HARNIX_KNOWLEDGE_ROOTS` when it is shared. Set `HARNIX_KNOWLEDGE_ROOTS` (plus the allowed repository roots) to restrict it. Secret files, `.git`, dependencies and symlinks are skipped either way.
* **Agent-written knowledge is never authoritative on its own.** Only verified tasks produce proposals, they are `proposed` until a person decides, retrieval can hide them, and every decision is in the audit log.
* **Data leaves the machine** when the OpenAI embedding provider is used: chunk text (and queries) are sent to OpenAI to be embedded. Use `HARNIX_EMBEDDING_PROVIDER=hash` to keep everything local (low quality), or leave the knowledge base off. The built-in knowledge MCP server is given `OPENAI_API_KEY` in its environment for query embedding (it is our own process; the value is redacted from events).
* **Query console (`POST /api/knowledge/query`)**: Cypher typed by a person or written by the model is untrusted. It runs in a read-only transaction (Neo4j refuses writes), with `LOAD CSV`, procedure calls (except four schema ones), `USE` and admin commands refused before it runs, the server-side timeout, at most 500 records, and only nodes of the requested project returned. A query must reference `$project`; a deliberately crafted query can still return aggregate numbers computed over other projects (for example `WHERE n.project = $project OR true ... RETURN count(*)`), so with several tenants on one Neo4j use the access list and a database per tenant. Questions are sent to the configured model with the graph schema (labels, relationship types), not with document text.
* **Concept extraction sends document text to the configured model** (`harnix knowledge extract`, the dashboard's "Extract concepts"): each story and BRD is sent in full, so do not run it on documents that may not go to that provider. Its output is untrusted like any model output: every entity and relationship needs an exact quote that is found in the document, otherwise it is dropped; relationships between concepts stay `proposed` until a person confirms them; the model has no tools in this call.

## Host terminal (opt-in)

* Off by default. With `HARNIX_TERMINAL=host` the agent can run commands and keep processes running on the host, **outside Docker**. Starting a process always needs approval (bound to the exact command); `terminal_run` uses the normal command classification (allowlisted validation tools run, others ask, `sudo`/`format`/... are refused); `approval_policy=deny_sensitive` refuses everything that would ask.
* Still no shell, and the working directory is resolved inside the workspace copy; the child gets the operator's environment minus secret-looking variables, `HARNIX_*` and Harnix's secrets. It is not a sandbox: an approved command has your user's rights. Processes are scoped to the conversation that started them (tools cannot stop or read another's) but `forge ps` / the API list and stop all of them. Details: [terminal.md](terminal.md).

## MCP servers

* An MCP server is a program (stdio) or a remote service (HTTP) that the **operator** names in `mcp.json` (`HARNIX_MCP_CONFIG`, default `~/.forge/mcp.json`). It runs with the operator's rights, **outside the sandbox and the workspace copy**; the workspace/apply guarantees do not cover what it does.
* A `.mcp.json` inside the repository is repository text (untrusted) and would let a repo run programs on your machine, so it is ignored unless `HARNIX_MCP_TRUST_PROJECT=true`; the task events list what was skipped.
* Every call to an MCP tool needs human approval (bound to the exact arguments) unless the operator put that tool in the server's `autoApprove`. `approval_policy=deny_sensitive` refuses all of them. Hints a server sends about its own tools (read-only, etc.) are never used to lower the approval.
* The child process gets a short allow-list of environment variables plus the server's own `env`, never the backend's environment (API keys). Values from `env` and `headers` are redacted from tool output and events; `${VAR}` expansion happens at load time and the secrets are never written to events (the `mcp.servers` event shows command/URL without query only).
* MCP tool output is untrusted data like any tool output; images and binary blobs are described, not passed on; output is truncated to `HARNIX_MAX_OUTPUT_BYTES`.
* Sub-agents never get MCP tools. A server that fails to start never fails the task (reported in `mcp.servers`).

## Knowledge graph (Neo4j)

The graph is a second data store holding information *derived from repositories*, so it has its own rules (details in [knowledge-graph.md](knowledge-graph.md)):

* **No source text is stored**: names, signatures (parameter names and annotations; default values are replaced by `...` because they can be secrets), line ranges, hashes and the first docstring line. A test indexes a file containing a secret literal and checks that it appears in no node property.
* **Built from the isolated workspace copy**, which already excludes secret-looking files and symlinks; the indexer applies the same exclusions again.
* **No query language is exposed.** The model's `graph_query` tool takes one of eight operations and a value; every statement is a constant string with parameters (labels and relationship types are constants). Tests throw Cypher/quote/backtick payloads at every operation and confirm nothing is altered and nothing matches.
* **Read-only for the model**: the tool is classified read-only by the policy engine; only the harness writes the graph, and only from the indexer.
* **Tenant isolation**: every node carries a `repo_id` (hash of the authorized repository path) and every query filters on it; a test confirms one repository's graph is invisible from, and survives deletion of, another's.
* **Untrusted output**: graph text originates in the repository, so it is flattened to one line, size-capped, passed through the redactor, and wrapped in a fenced `<knowledge_graph trust="untrusted">` block that the system prompt tells the model not to obey.
* **Neo4j itself**: no default password (compose refuses to start without `NEO4J_PASSWORD`), ports published on loopback only, no plugins, server-side transaction timeout, failed-login lockout, `no-new-privileges`. Bolt is unencrypted on localhost; for any remote Neo4j use `neo4j+s://` and a network boundary. The password is registered with the redactor and hidden from `Settings` reprs.
* **Availability**: if Neo4j is down, slow or erroring, the task continues without the graph and records a `graph.unavailable` event with the reason.

## Known limitations (read these)

1. **Local mode is not isolated** (above). **Docker mode is not a perfect boundary** either: containers share the host kernel, and the workspace is writable by design, so an approved command can alter anything inside the workspace (the source repository is still untouched, because the workspace is a copy).
2. An approved or allowlisted command that runs repository code (`pytest`, `npm test`, `cargo test`…) executes untrusted code inside the chosen sandbox. In local mode that is your host.
3. Git on the host reads the workspace's `.git` for diffs. It is hardened as described, but code executed in the workspace could still alter that directory; a determined attacker with local mode could target that. Prefer Docker mode for untrusted repositories.
4. `HARNIX_API_TOKEN` is a shared secret, not user authentication; there are no per-user permissions or audit identities. `?token=` for SSE can appear in access logs. Serve over TLS behind a reverse proxy if the API is reachable beyond localhost.
5. Prompt-injection resistance rests on policy being enforced in code. The model can still be *talked into proposing* harmful actions; those are blocked or require approval, but a human approving carelessly defeats the control.
6. Symlinks are skipped when copying the repository and rejected by path resolution. Hard links and special files are not specially handled.
7. Docker mode with a non-default `HARNIX_DOCKER_NETWORK` removes the no-network guarantee.
8. Resource limits for local mode are timeouts and output caps only (no CPU/memory limits); Docker mode adds memory/CPU/PID limits.
9. Workspaces are retained on disk (they contain a copy of the repository without secret-looking files) until you delete them.
10. The graph is a static, name-based approximation (it can miss edges, never invents one it cannot tie to a single definition) and it is shared per repository: two tasks started against a repository with different uncommitted states see whichever index ran last (tool results flag `stale` when the version moved). Community-edition Neo4j has one database and no per-user roles, so anything with the Neo4j password can read every indexed repository's structure.
11. Model-specific behaviour is only lightly measured: four small cases with two OpenAI models (including a prompt-injection case, where neither model attempted a blocked action). The Anthropic path has not been run against the real API.

## Reporting

Report vulnerabilities privately to the repository owner rather than in a public issue.
