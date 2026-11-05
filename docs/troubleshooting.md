# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Startup log: "HARNIX_SPEND_LIMIT_USD … no longer limit anything" | Those variables were removed: spending is measured, never limited. Delete them from `.env`. For a hard cap set a spending limit at your model provider |
| Backend exits with `Harnix configuration error: Database schema is missing…` | Run migrations from `backend/`: `alembic upgrade head` (with the same `HARNIX_DATABASE_URL` as the server) |
| `Harnix configuration error` / validation error on startup | An env var is invalid (for example `HARNIX_EXECUTION_MODE` must be `docker` or `local`; limits must be > 0). The message names the variable |
| UI banner "Model provider not configured: OPENAI_API_KEY is not set" (or `ANTHROPIC_API_KEY`) | Put the key in `.env` (repository root or `backend/`) or export it, and restart the backend. A value like `your-openai-api-key-here` counts as unset. For a demo without a key use the mock provider (README quick start) |
| "HARNIX_MODEL='claude-…' is an Anthropic model but HARNIX_PROVIDER=openai" (or the reverse) | Provider and model disagree: set `HARNIX_MODEL` to a model of the selected provider (OpenAI: `gpt-5.4-nano`), or leave it unset for the default |
| Task fails: "gpt-6-luna cannot use tools on the Chat Completions API with reasoning enabled" (HTTP 400 "Function tools with reasoning_effort are not supported…") | The Luna models allow tools on Chat Completions only without reasoning: set `HARNIX_REASONING_EFFORT=none` (already in the shipped `.env`). For `gpt-5.4-nano`/`-mini` leave that variable unset |
| Task fails with "insufficient_quota" | The provider says the key has no credit left. Add credit or use another key (not retried on purpose) |
| `Run` returns 503 `not_configured` | Same message as the banner: provider key missing, or sandbox unavailable. The task stays `queued`; fix the cause and press Run again |
| "Docker daemon is not running" | Start Docker, or (trusted repos only) set `HARNIX_EXECUTION_MODE=local` |
| "Docker image 'forge-sandbox:latest' not found" | `docker build -f docker/Dockerfile.sandbox -t forge-sandbox:latest docker/` (repository root). Add your toolchains to that Dockerfile |
| Tests report `unavailable: tool 'pytest' is not installed in the execution environment` | The sandbox lacks the tool. Docker: add it to the sandbox image (the container has no network). Local: install it into the Python that runs Harnix |
| Dependency install fails inside the sandbox | Expected: `--network none`. Bake dependencies into the image, or set `HARNIX_DOCKER_NETWORK=bridge` knowingly (this removes the network isolation) |
| "No repository roots are authorized" / "outside the authorized roots" | Set `HARNIX_ALLOWED_REPO_ROOTS` (os.pathsep-separated; `;` on Windows) to the parent directory of your repositories and restart |
| Task finished as "Not verified" | No test/acceptance check passed or none exists. See the Verification tab and the report's limitations. Provide `verification_commands` |
| Task `failed` with "Verification failed" | The agent claimed success but the checks fail; the failing output is in the Verification tab. It already had `HARNIX_MAX_RECOVERY_ATTEMPTS` chances to fix it |
| Task stuck in `awaiting approval` | A human decision is required (Approval card in the task page, or `POST /api/approvals/{id}/decision`). It expires after `HARNIX_APPROVAL_TIMEOUT_SECONDS`. Use `HARNIX_APPROVAL_POLICY=deny_sensitive` for unattended runs |
| A tool call is "denied by policy" | See its `tool.authorization` event for the reason. Typical: path outside the workspace, shell syntax (`|`, `&&`, `>`), a `sudo`-class command |
| 429 when starting a task | `HARNIX_MAX_CONCURRENT_TASKS` tasks are active; wait or raise the limit |
| Tasks marked failed after restarting the server ("Interrupted…") | A running task cannot survive a process crash; use Re-run. Only tasks silent for longer than any live task can be (approval wait, tool or model timeout, plus a minute) are marked, so a second backend on the same database or a `--reload` restart no longer kills another process's task. With `uvicorn --reload`, add `--reload-exclude ".forge/*"` (or set `HARNIX_WORKSPACE_ROOT` outside `backend/`): agent workspaces live under `backend/.forge/` and every file an agent writes otherwise restarts the server |
| `forge` is not recognized | Put the venv's Scripts folder on PATH (`$env:Path = "...\backend\.venv\Scripts;$env:Path"`) or call `...\backend\.venv\Scripts\forge.exe`. If missing, `pip install -e backend --no-deps` recreates it |
| CLI: "Cannot reach the Harnix backend" (exit 3) | Start it: `harnix serve --allow "<your projects folder>"`; use `--url` / `HARNIX_URL` for another address |
| CLI/extension: "outside the authorized roots" (exit 2) | The backend only works inside folders you authorized: restart it with `harnix serve --allow "<this folder>"` (or set `HARNIX_ALLOWED_REPO_ROOTS`) |
| Apply says "Nothing was written … changed in your repository since the task started" | You (or a tool) edited that file after the task began; Harnix never overwrites it. Re-run the task on the current files, or merge the diff by hand (`forge`'s diff / the Changes tab) |
| Apply says "already exists" / "workspace no longer exists" / "workspace is unusable" | A file with that name appeared meanwhile; or the task workspace under `HARNIX_WORKSPACE_ROOT` was deleted/damaged, so there is nothing to apply: re-run the task |
| Apply says "refused, looks like a secret/credential file" | The agent created a file such as `.env` or `*.pem`; Harnix will not copy those into your repository. Create it yourself if you really want it |
| VS Code: "Harnix: Start Backend Server" opens a terminal that exits at once | `forge` is not on PATH: set the `forge.command` setting to the full path of `forge.exe` |
| VS Code: panel shows "Cannot reach the Harnix backend" | Start the backend (command above) or set `forge.serverUrl`; if the backend uses `HARNIX_API_TOKEN` run **Harnix: Set API Token** |
| UI shows "Cannot reach the Harnix backend" | Backend not running on `127.0.0.1:8000`, or set `HARNIX_BACKEND_URL` for the Vite dev proxy |
| `401 Missing or invalid API token` | `HARNIX_API_TOKEN` is set: enter the token in the UI prompt or send `Authorization: Bearer …` |
| Live timeline says "reconnecting" | The SSE connection dropped; the browser reconnects automatically and resumes from the last event id |
| On Windows, cancelled task left a process | Report it with the command; Harnix uses `taskkill /T /F` on the process tree. Containers are killed with `docker kill` |
| `HARNIX_GRAPH_ENABLED=true needs NEO4J_PASSWORD` at startup | Set `NEO4J_PASSWORD` in `.env` (same value for docker compose and Harnix) or set `HARNIX_GRAPH_ENABLED=false` |
| `docker compose up -d neo4j` fails with "set NEO4J_PASSWORD in .env" | Compose reads `.env` from the repository root; add `NEO4J_PASSWORD=...` there |
| Neo4j container restarts; logs say "Unrecognized setting" | A `NEO4J_*` setting in `docker-compose.yml` is misspelled for this Neo4j version (strict validation). Check `docker logs harness-neo4j-1` |
| Timeline: "knowledge graph unavailable, continuing without it: cannot reach Neo4j" | Neo4j is not running: `docker compose up -d neo4j`; wait for `docker compose ps` to show healthy. The task itself was not affected |
| "Neo4j rejected the credentials" | `NEO4J_PASSWORD` differs from the one the Neo4j volume was created with. Use the original value, or `docker compose down -v` (deletes the graph, which is only derived data) and start again |
| "Knowledge graph partially indexed" / `forge graph status` says INCOMPLETE | The indexing time budget ended on a large repository; nothing is lost. Run `forge graph index <repo>` (own, longer budget) or just run another task: it continues where it stopped |
| `graph_query` results look incomplete | Expected limits: static name-based analysis (calls through instance attributes are not resolved), Python only, and the graph describes the task start. Use the file tools to confirm |
| Graph is wrong or stale after big refactors | `forge graph drop <repo>`; the next task rebuilds it |
| Timeline: "thinking high -> none: … accepts function tools on Chat Completions only without reasoning" | The model (e.g. `gpt-6-luna`) cannot reason with tools on Chat Completions. Set `HARNIX_OPENAI_API=responses` (and leave `HARNIX_REASONING_EFFORT` unset) to get real reasoning levels |
| A task waits for approval of `remember` | Agent memory writes need approval by default (`HARNIX_MEMORY_WRITE=approve`). Approve, reject, or set `allow` / `deny` |
| `forge skills` lists nothing | Set `HARNIX_SKILLS_DIRS` to a directory containing `<id>/SKILL.md` folders; skipped skills are listed with the reason |
| Many `context.compacted` events | The context window is small relative to the fixed prompt; raise `HARNIX_CONTEXT_WINDOW_TOKENS` to your model's real window. Compaction defers itself when it cannot help (`context.compaction_deferred`) |
| `pytest` in `backend/` is slow | The end-to-end tests run real subprocesses and git (several minutes in total); run a single file while iterating. Docker tests are skipped when Docker is unavailable |
| Symlink test skipped | Windows needs privilege for file symlinks; the directory-junction variant still runs |
| PostgreSQL: `psycopg` import error | `pip install -e ".[dev,postgres]"` |

Useful checks: `GET /api/health` (sandbox/provider status), `GET /api/tasks/{id}/events` (what happened and why), `GET /api/tasks/{id}/report?format=md`.
