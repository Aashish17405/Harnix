# Harnix ⚡

> **The Self-Verifying Autonomous AI Coding Harness & Spec Intelligence Engine**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19%20%7C%20TypeScript-61dafb.svg)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Sandbox-Docker%20Isolated-2496ed.svg)](https://www.docker.com/)
[![Neo4j](https://img.shields.io/badge/Knowledge%20Graph-Neo4j%205-008cc1.svg)](https://neo4j.com/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

---

## The Problem

Most AI coding agents (Devin-style bots, Claude Code, Cline, Aider) operate on a flawed assumption: **they trust the model when it says "I'm done."**

In reality:
1. **Hallucinated Completions:** Models claim a bug is resolved, but tests still fail or critical edge cases are skipped.
2. **Rogue Shell Execution:** Giving an LLM open terminal access risks accidental repository corruption, leaked secrets, or destructive commands.
3. **Spec Blindness:** Coding agents work in isolation from product context—they don't understand the original Business Requirements (BRDs), user stories, or architecture constraints.

---

## The Harnix Solution

**Harnix** is an autonomous, self-hostable coding harness engineered around **independent deterministic verification, sandboxed security, and enterprise spec intelligence**.

The model only *proposes* actions. All validation, policy enforcement, human approval, execution, and verification run through deterministic code that the model cannot alter or bypass.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   HARNIX PLATFORM                                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   Web Dashboard (React)  │  CLI (`harnix`)  │  VS Code Extension                       │
│             │                      │                   │                               │
│             └──────────────────────┼───────────────────┘                               │
│                                    ▼                                                   │
│                      FastAPI Modular Monolith                                          │
│                                    │                                                   │
│           ┌────────────────────────┴────────────────────────┐                          │
│           ▼                                                 ▼                          │
│   Autonomous Agent Loop                             Spec Knowledge Engine              │
│   • Token Compaction & Budgets                      • Neo4j Graph + pgvector           │
│   • Sub-agents (Foreground & Background)            • Ingests BRDs, Stories, Jira      │
│   • Memory & Skills System                          • Hybrid MCP Retrieval Tools       │
│           │                                                 │                          │
│           ▼                                                 ▼                          │
│   Policy & Tool Executor                           Context Bundle Assembler            │
│   • Command allowlist (No raw shells)              • Task-specific grounded context    │
│   • Human-in-the-loop approval gates               • Quote & provenance checking       │
│           │                                                                            │
│           ▼                                                                            │
│   Execution Sandboxes (Docker / Local) ──► Independent Verification Engine             │
│                                            (Tests, Linters, Acceptance Criteria)       │
│                                                           │                            │
│                                                           ▼                            │
│                                             Atomic Apply-Back to User Repo             │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Innovations

### 1. Independent Deterministic Verification
* When an LLM declares "task complete", Harnix ignores the claim.
* The **Verification Engine** independently executes project test suites, linters, and custom acceptance criteria in a fresh container.
* If checks fail, Harnix enters an automated **recovery loop** with the error diagnostics fed back to the agent.
* If verification cannot confirm the fix, the task is explicitly flagged **`completed (not verified)`**.

### 2. Defense-in-Depth Security Architecture
* **Zero Raw Shells:** Commands run strictly as validated `argv` lists classified by safety policy (`policies/commands.py`). Arbitrary piping, subshells, and chained commands are blocked.
* **Isolated Workspaces:** Every task runs on an isolated repository copy inside a Docker sandbox (`docker/Dockerfile.sandbox`).
* **Controlled Write-Back:** The model never writes directly to your repository. Changes can only be applied through an atomic, byte-exact patch (`execution/apply.py`) after explicit human confirmation.
* **Secret Redaction:** High-entropy tokens, API keys, and sensitive environment variables are masked in all database logs and live SSE events.

### 3. Spec-to-Code Knowledge Graph & MCP
* Connects upstream product specifications (BRDs, PRDs, user stories, architecture docs) to AST code entities using Neo4j and vector embeddings.
* Exposes standardized **Model Context Protocol (MCP)** tools for semantic search, dependency traversal, and traceability.
* Assembles grounded **Context Bundles** before tasks start, preventing the agent from guessing business logic.

---

## Interfaces (Tri-Modal UX)

Harnix fits into any developer workflow:

### 1. Web Dashboard
Real-time task monitoring powered by Server-Sent Events (SSE):
- Live agent timeline and tool invocation inspector
- Interactive human-in-the-loop approval cards
- Unified side-by-side diff viewer
- Interactive Neo4j knowledge graph visualizer and query console

### 2. Terminal CLI (`harnix`)
Use Harnix from any terminal or IDE:
```bash
# Start backend server
harnix serve --allow /path/to/projects

# Interactive session in current directory
harnix

# Single-shot task with verification command
harnix "fix the off-by-one bug in rangesum.py" -v "pytest tests/test_rangesum.py"

# Reference specific files and line numbers with @
harnix "refactor @src/parser.py#20-60 and add unit tests"

# Check system health, sandbox status, and spend accounting
harnix doctor
```

### 3. VS Code Extension
Native IDE extension (`vscode-extension/`):
- Sidebar activity panel with live timeline and diff preview
- Inline approval dialogs and quick notifications
- Fast `@file` context attachment with automated secret exclusion (`.env`, keys)
- Global shortcut: `Ctrl+Alt+H` for instant task dispatch

---

## Quickstart

### Prerequisites
- **Python 3.11+**
- **Node.js 20+**
- **Docker** (optional, recommended for sandboxed execution)
- Git

---

### Option A: 60-Second Mock Demo (No API Key Required)

Experience the entire execution loop, human approval gate, and verification engine instantly using our built-in deterministic script runner:

#### 1. Backend
```bash
cd backend
python -m venv .venv

# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
alembic upgrade head

# Run mock provider
export HARNIX_PROVIDER=scripted
export HARNIX_SCRIPTED_FILE=examples/mock-script.json
export HARNIX_EXECUTION_MODE=local
export HARNIX_ALLOWED_REPO_ROOTS="$PWD/examples/repos"

# On Windows PowerShell:
# $env:HARNIX_PROVIDER="scripted"; $env:HARNIX_SCRIPTED_FILE="examples/mock-script.json"; $env:HARNIX_EXECUTION_MODE="local"; $env:HARNIX_ALLOWED_REPO_ROOTS=(Resolve-Path examples/repos).Path

python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000
```

#### 2. Frontend
In a second terminal:
```bash
cd frontend
npm install
npm run dev
```
Open [http://127.0.0.1:5173](http://127.0.0.1:5173), select the demo repository (`demo-bug`), and click **Create and run**. You will see the agent propose a fix, trigger the approval prompt, run the test suite, and achieve verified completion.

---

### Option B: Real Model Run (OpenAI / Anthropic)

1. Copy `.env.example` to `.env` in the repository root:
   ```bash
   cp .env.example .env
   ```
2. Set your model provider and API key in `.env`:
   ```dotenv
   HARNIX_PROVIDER=openai
   HARNIX_MODEL=gpt-5.4-nano
   OPENAI_API_KEY=sk-...
   HARNIX_ALLOWED_REPO_ROOTS=/path/to/your/projects
   ```
   *(For Anthropic, set `HARNIX_PROVIDER=anthropic`, `HARNIX_MODEL=claude-sonnet-5-5`, and `ANTHROPIC_API_KEY`)*

3. (Optional) Build the Docker sandbox image:
   ```bash
   docker build -f docker/Dockerfile.sandbox -t harnix-sandbox:latest docker/
   ```

4. Launch the platform:
   ```bash
   # Terminal 1: Backend
   cd backend && uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

   # Terminal 2: Frontend
   cd frontend && npm run dev
   ```

---

## Measured Benchmark Results

Tested with real models through isolated Docker sandbox containers across representative code-repair tasks:

| Metric | `gpt-6-luna` | `gpt-5.6-luna` | `gpt-5.4-nano` | `gpt-5.4-mini` |
|:---|:---:|:---:|:---:|:---:|
| **Verified Fix Rate** | **100% (4/4)** | 100% (4/4) | 100% (4/4) | 100% (4/4) |
| **False Completion Claims** | **0** | 0 | 0 | 0 |
| **Harness False Accepts** | **0** | 0 | 0 | 0 |
| **Mean Task Duration** | **12.8s** | 11.6s | 16.0s | 9.8s |
| **Estimated Cost (4 Tasks)** | **$0.0028** | $0.0055 | $0.0077 | $0.0214 |

---

## Project Structure

```
Harnix/
├── backend/
│   ├── harnix/
│   │   ├── api/            # FastAPI REST & SSE endpoints
│   │   ├── harness/        # Agent loop, runner, token compaction, sub-agents
│   │   ├── policies/       # Security policies, command allowlists, budgets
│   │   ├── execution/      # Docker sandboxes, workspace git baselines, apply
│   │   ├── verification/   # Deterministic verification engine & check suite
│   │   ├── knowledge/      # Neo4j graph parser, pgvector embeddings, MCP server
│   │   ├── models/         # Model adapters (OpenAI, Anthropic, Scripted Mock)
│   │   ├── persistence/    # SQLAlchemy models, SQLite/PostgreSQL store
│   │   └── cli.py          # Unified `harnix` CLI command
│   ├── migrations/         # Alembic database migrations
│   └── tests/              # 180+ automated unit & integration tests
├── frontend/               # React 19 + TypeScript + Vite web dashboard
├── vscode-extension/       # VS Code IDE companion extension
├── docker/                 # Sandbox Dockerfile & container isolation setup
├── docs/                   # Deep architectural documentation & API specifications
└── docker-compose.yml      # Multi-container setup (PostgreSQL, Neo4j)
```

---

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Execution & Task Flow](flow.md)
- [Architecture Decision Records (ADRs)](decision.md)
- [Harness Engineering Guide](docs/harness-engineering.md)
- [Knowledge Graph & Context Retrieval](docs/knowledge.md)
- [Model Context Protocol (MCP) Guide](docs/mcp.md)
- [Security & Sandbox Containment](docs/security.md)
- [Testing & Quality Verification](docs/testing.md)

---

## License

Harnix is licensed under the Apache 2.0 License.
