# Knowledge graph (Neo4j)

Harnix can build a graph of the repository a task works on and use it for **context management**: instead of making the model discover how code is connected by reading file after file, the harness hands it a compact, structure-aware summary up front and lets it ask targeted questions ("who calls this?", "which tests cover this file?", "what depends on this module?") through one read-only tool.

This page explains what is built, how it works, how to run and operate it, what protects it, how it was measured, and what it does **not** do. Related: [decision.md](../decision.md) D32i (why), [flow.md](../flow.md) (where it sits in a run), [security.md](security.md), [harness-engineering.md](harness-engineering.md) (context and tools in general).

## 1. Quick start

```powershell
# .env: NEO4J_PASSWORD=<any strong value>   (compose refuses to start Neo4j without it)
docker compose up -d neo4j                 # Neo4j 5.26 LTS Community; ports bound to 127.0.0.1; browser UI http://127.0.0.1:7474
# .env: HARNIX_GRAPH_ENABLED=true           then restart the backend
forge doctor                               # graph        neo4j connected
forge graph index  "C:\path\to\repo"       # optional pre-index (background job on the backend; Ctrl+C only stops watching)
forge graph status "C:\path\to\repo"       # files, symbols, imports, calls, version, complete/INCOMPLETE
forge graph drop   "C:\path\to\repo"       # delete this repository's graph (derived data; rebuilt on demand)
```

Without `forge graph index` nothing is lost: the first task on a repository indexes it (within a time budget), and every later task only updates what changed.

## 2. What the model gets

### 2.1 A `<knowledge_graph>` block in the first message

Built after the baseline checks run, capped at `HARNIX_GRAPH_CONTEXT_MAX_CHARS` (default 6,000), redacted, fenced and labelled `trust="untrusted"`:

```
Static, name-based index of the repository as it was when this task started. It can be incomplete or approximate ...
- 101 indexed files (python 86, json 6, ...); index complete
- main areas: forge (73), tests (14), migrations (3) ...
- most depended-on files: forge/config.py (20), forge/models/base.py (16), forge/persistence/store.py (14) ...
Focus (from your task, attached files and failing tests):
- tests/test_x.py (a test that fails before your changes) imports: forge/a.py, forge/b.py
- forge/a.py [python, 91 lines, 3 dependents]
    defines: class A L24-91; method A.run L40-91; ...
    imports: forge/b.py, forge/c.py
    imported by: forge/harness/agent.py, tests/test_x.py (test)
- function compute_total at forge/a.py:40; callers: main (forge/cli.py); calls: round_money (forge/money.py)
More: graph_query ops search_symbols, find_symbol, callers, callees, hierarchy, file_info, dependents, tests_for.
```

**Where the focus comes from** (`RepoGraph.seeds`): (1) files the user attached with `@path`; (2) paths named in the task text that exist in the graph (an unqualified name like `util.py` is used only if exactly one file has it); (3) **test files that fail at baseline**, extracted from the baseline run's traceback (`FAILED path::test`, `File "path", line N`, `path:LINE`); (4) identifiers in the task (backticked, `snake_case` or `CamelCase`) that match one to three symbols (a name matching 40 symbols is useless as a seed and is dropped). The system prompt gains a short paragraph describing the block and the tool, only when the block is present.

### 2.2 The `graph_query` tool

Eight fixed operations; the model supplies an operation name and a value, never query text:

| `op` | `target` | Returns |
|---|---|---|
| `search_symbols` | name fragment | symbols whose name contains it (case-insensitive), best matches first |
| `find_symbol` | `name` or `Class.method` | definitions: kind, path, line, signature, first docstring line |
| `callers` | `name` or `Class.method` | who calls it (resolved calls only) |
| `callees` | `name` or `Class.method` | what it calls (resolved calls only) |
| `hierarchy` | class name | base classes and subclasses (within the repository) |
| `file_info` | repo-relative path | symbols with line ranges, imports, importers (tests flagged), external modules; a near-miss path returns similar paths |
| `dependents` | repo-relative path | files that import it, directly or transitively (`depth` 1-3), with distance |
| `tests_for` | repo-relative path | test files that import it, directly or transitively |

Every result carries a `snapshot` note: index version, whether the index is complete, the accuracy caveat, the files the agent has changed since the task started ("not reflected"), and a `stale` flag if the repository was re-indexed by another run meanwhile. The tool is read-only, goes through the normal validate → policy → executor pipeline, is offered to the model only when the task has a graph, and returns a structured error (not a crash) if the graph becomes unavailable mid-task.

## 3. Data model

One Neo4j database (Community edition has only one), partitioned by repository. Every node has a `repo_id` (first 16 hex characters of the SHA-256 of the resolved repository path: no path is stored in the graph) and every statement filters on it.

```
(:Repo   {uid, repo_id, name, version, complete, files, symbols, imports, calls, indexed_at, notes})
(:File   {uid, repo_id, path, name, dir, language, size, lines, sha256, mtime_ns, is_test, parsed, error,
          in_degree, imports_raw, imports_mods})
(:Symbol {uid, repo_id, kind: class|function|method, name, lname, qualname, path, start, end,
          signature, doc, top, is_test, calls_raw, bases_raw})
(:External {uid, repo_id, name})                      a top-level module outside the repository

(File)-[:DEFINES]->(Symbol)      (Symbol)-[:CONTAINS]->(Symbol)       class -> its methods / nested classes
(File)-[:IMPORTS]->(File)        (File)-[:IMPORTS_EXTERNAL]->(External)
(File)-[:TESTS]->(File)          a test file -> the non-test files it imports
(Symbol)-[:CALLS]->(Symbol)      (Symbol)-[:INHERITS]->(Symbol)
```

**Schema** (created idempotently on first use): uniqueness constraints on `uid` for all four labels; range indexes on `File(repo_id)`, `File(repo_id, path)`, `File(repo_id, name)`, `Symbol(repo_id)`, `Symbol(repo_id, name)`, `Symbol(repo_id, path, name)`; a text index on `Symbol(lname)` for substring search. `in_degree` (importers per file) is denormalized and recomputed when an index run changed something, so "most depended-on files" is a sort, not a graph traversal.

**What is stored and what is not**: names, signatures, line ranges, hashes, the first docstring line (one line, control characters removed, 160 characters). **Never source text, and never default values**: a function `def connect(password="...")` is stored as `connect(password=...)`. This was found by a test, not by design review (see D32i).

## 4. How indexing works

Source: `backend/harnix/graph/`. The pure parts (`extract.py`, `walker.py`, `resolve.py`) have no database access.

1. **Walk** (`walker.py`): the task's isolated workspace copy (already secret-free and symlink-free), skipping caches/vendor/`*.egg-info` directories, secret-looking names, links, and files without a recognised text/source extension. Capped at `HARNIX_GRAPH_MAX_FILES` (default 100,000; the rest are reported, never deleted).
2. **Change detection**: stored `(size, mtime_ns)` per file. A match means the file is not even read. For files that differ the content hash is compared, so a touched-but-identical file is not rewritten.
3. **Extract** (`extract.py`): Python is parsed with `ast` (exact); symbols, nested classes, conditional definitions, signatures, calls (`name()`, `self.method()`, `alias.func()`), base classes and imports. Anything else becomes a File node. A file that fails to parse (syntax error, pathological nesting, oversized) is still recorded with the reason and never aborts the run.
4. **Write** (`store.py`): batches of 200 files per read step, `UNWIND` writes in managed transactions (driver retries transient failures), delete-and-recreate per changed file, so a rerun is idempotent.
5. **Resolve** (`resolve.py`, `indexer.py`): turn raw imports/calls/bases into edges, **only when a name ties to exactly one definition**:
   * module names follow Python's own rules (climb while the directory has `__init__.py`), plus path suffixes for `src/`-style layouts; two files claiming one name make it ambiguous, which gives no edge;
   * relative imports resolve by path arithmetic; `from pkg import name` is a module if `pkg.name` is a file, otherwise a symbol of `pkg`;
   * a call resolves to a module-level function/class defined or imported by name, to `self.method()` in the same class, to `alias.func()` through an imported module, or to `Class.method()` through a local or imported class.
6. **Re-resolve only what can have changed**: the changed files, their importers (found through `IMPORTS` before and after the change), importers of removed files, and importers of newly added modules (found through each file's stored `imports_mods`, which is how an import that could not resolve yesterday resolves today without anyone editing the importer). After an interrupted run the next run re-resolves everything once.
7. **Finish**: bump the repository `version` (only if something changed), recompute `in_degree` and the counts, record `complete`.

**Time budget and resumability.** Indexing inside a task stops at `HARNIX_GRAPH_INDEX_TIMEOUT_SECONDS` (default 60) or on cancel, marks the repository incomplete, and the task proceeds with what exists (the `graph.indexed` event says "partial"). The next run continues where this one stopped and then re-resolves everything once; a test confirms it converges to the same graph as a fresh index. For large repositories, pre-index once with `forge graph index` / `POST /api/graph/index`: it runs as a background job with its own, larger budget (`HARNIX_GRAPH_BACKGROUND_INDEX_TIMEOUT_SECONDS`, default 3,600), so tasks only pay for what changed since.

**Concurrency.** One lock per repository inside the backend process: a task that cannot get it within the budget uses the stored graph and says so; two repositories index independently. Writes are idempotent, so a second backend process cannot corrupt the graph, but only the single-process deployment is supported and tested.

## 5. Failure behaviour: the graph never fails a task

| Situation | What happens |
|---|---|
| `HARNIX_GRAPH_ENABLED=true` without `NEO4J_PASSWORD` | Backend refuses to start, with the fix |
| Neo4j down at backend start | Backend starts; warning in the log; `/api/health` shows `graph.available=false`; tasks run without the graph |
| Neo4j down or slow during a task | `graph.unavailable` event with the reason (connection refused, bad credentials, timeout); the task runs without the context block and without the tool |
| Neo4j goes away mid-task | `graph_query` returns a structured error; the loop continues |
| Bug in graph code | Any exception is contained, event `graph.unavailable` with `internal error: <type>` (the message is not leaked), task continues |
| Model calls `graph_query` when there is no graph | Structured "not available" error |
| Wrong password | "Neo4j rejected the credentials"; the password is never echoed |

The principle: the graph improves context, it is not a correctness mechanism; verification is unchanged and independent.

## 6. Security

See [security.md](security.md) for the list. In short: no source text or default values stored; built from the secret-free workspace copy; every statement a constant with parameters, no query language exposed to the model, injection payloads tested against every operation; every node and statement namespaced by `repo_id`; output flattened, size-capped, redacted, fenced and labelled untrusted; read-only for the model; Neo4j published on loopback only with no default password, no plugins, a server-side transaction timeout and failed-login lockout; the password registered with the redactor and hidden from `Settings` reprs.

## 7. Operating it

| Topic | Guidance |
|---|---|
| **Run** | `docker compose up -d neo4j` (service in `docker-compose.yml`, image `neo4j:5.26-community`, data in the `harness_forge-neo4j-data` volume, healthcheck on :7474) |
| **Memory** | Compose defaults: heap 512 MB initial / 1 GB max, page cache 512 MB; override with `NEO4J_HEAP_INITIAL`, `NEO4J_HEAP_MAX`, `NEO4J_PAGECACHE` in `.env`. Size the page cache to hold the graph (see the measured sizes below) |
| **Inspect** | Browser UI at http://127.0.0.1:7474, e.g. `MATCH (f:File {repo_id: "<id>"})-[:IMPORTS]->(g) RETURN f, g LIMIT 50` (`forge graph status` and `/api/graph/status` show the `repo_id`) |
| **Backup / reset** | The graph is derived data and can always be rebuilt (`forge graph drop` + next task). For a volume backup, stop Neo4j and copy the volume. `docker compose down -v` deletes the graph |
| **Upgrade** | Stay within the pinned 5.26 LTS line for patch updates (`docker compose pull neo4j`); schema statements are `IF NOT EXISTS` |
| **Remote Neo4j** | Use `HARNIX_NEO4J_URI=neo4j+s://host:7687` (TLS) and a network boundary. Bolt on localhost is unencrypted |
| **Multiple Harnix instances** | Not supported: per-repository locking is in-process |
| **Settings** | All `HARNIX_GRAPH_*` and `HARNIX_NEO4J_*` variables are documented in `.env.example` |

## 8. Measured (development machine, synthetic repositories)

Synthetic Python repositories (modules with imports and call chains between them, plus test files), indexed into the Docker Neo4j (heap 1 GB, page cache 512 MB) on the Windows development machine:

| Files | Symbols | Import / call edges | First index | No-change re-index | One file changed | Query latency (median) | Context block |
|---|---|---|---|---|---|---|---|
| 2,060 | 14,040 | 6,034 / 14,037 | 9.3 s | 0.23 s | 0.45 s | 9-20 ms | 57 ms |
| 20,600 | 140,400 | 60,394 / 140,397 | 135 s | 3.3 s | 3.9 s | 16-51 ms | 170 ms |

Read these as a sanity check of the design (incremental work is proportional to the change plus a stat walk; queries stay bounded), not as a benchmark: one machine, synthetic code, one run each. The first index of a 20k-file repository exceeds the in-task budget (60 s) by design and is meant to be done with `forge graph index` once. A controlled real-model comparison of results with and without the graph was **not** run.

## 9. Limits of the analysis (read these)

* **Static and name-based.** Python is dynamic; an edge exists only where a name ties to one definition. **Calls through instance attributes (`self.executor.execute()`, `obj.method()`) are not resolved**, so `callers`/`callees` are lower bounds (every result says so). Guessing by method name would add confident-looking false edges, so it is deliberately not done.
* **Python only for symbols and edges.** Other recognised languages become File nodes (path, size, line count, language). Adding a language means adding an extractor (tree-sitter is the natural choice); the seam is `extract.py`. Not built because multi-language extractors cannot be verified without test repositories for each language.
* **A snapshot of the task start.** The agent's own edits are not written to the shared graph (they would pollute other tasks and survive failed ones). Tool results list the files the agent changed.
* **Shared per repository.** Two tasks started on a repository with different uncommitted states see whichever index ran last; results are flagged `stale` when the repository version moved during the task.
* **One database, no roles.** Community edition: repositories are isolated by namespace in code, not by Neo4j permissions.
* **Benefit for a real model is not established beyond the small comparison below.**
