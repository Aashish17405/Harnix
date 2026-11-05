"""Task lifecycle orchestration (runs each task on a worker thread)."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from harnix.config import ConfigError, Settings
from harnix.execution import git
from harnix.execution.project import ProjectInfo, inspect_project
from harnix.execution.sandbox import Sandbox
from harnix.execution.terminal import ProcessManager
from harnix.execution.workspace import WorkspaceManager
from harnix.graph.manager import GraphManager
from harnix.graph.query import RepoGraph
from harnix.graph.store import GraphUnavailable
from harnix.harness.agent import AgentLoop, AgentOutcome, ContextPolicy
from harnix.harness.agents import load_agents, render_agents
from harnix.harness.background import BackgroundJobs
from harnix.harness.context import ContextBuilder
from harnix.harness.images import to_base64
from harnix.harness.memory import render as render_memory
from harnix.harness.memory import validate_note
from harnix.harness.references import RefReport, load_references
from harnix.harness.report import build_report
from harnix.harness.skills import SkillRegistry
from harnix.harness.states import ACTIVE, TERMINAL, InvalidTransition, TaskStatus
from harnix.harness.subagents import SubagentDeps, make_spawner
from harnix.harness.transcript import MAX_SAVED_BYTES, dump_messages, load_messages, summary_of_unsaved_turn
from harnix.knowledge import context as kctx
from harnix.knowledge import tools as knowledge_tools
from harnix.knowledge.capture import capture as capture_knowledge
from harnix.knowledge.embeddings import make_embedder
from harnix.knowledge.ingest import Ingestor
from harnix.knowledge.repo import KnowledgeRepo
from harnix.knowledge.service import KnowledgeService, make_service
from harnix.mcp.config import ServerConfig
from harnix.mcp.config import discover as discover_mcp
from harnix.mcp.manager import McpManager
from harnix.models.base import ImageBlock, Message, ModelProvider
from harnix.persistence.models import Task
from harnix.persistence.store import Store
from harnix.policies.approvals import ApprovalGate
from harnix.policies.budget import Budget
from harnix.policies.paths import WorkspacePaths, authorize_repo, repo_key
from harnix.policies.policy import PolicyEngine
from harnix.policies.redaction import Redactor
from harnix.tools.base import ToolContext, ToolResult
from harnix.tools.executor import ToolExecutor
from harnix.tools.registry import TERMINAL_TOOLS, TOOLS, specs
from harnix.verification.engine import (
    BLOCKING_KINDS,
    CheckResult,
    Verdict,
    VerificationEngine,
    aggregate,
    plan_checks,
    plan_final_checks,
)

log = logging.getLogger("forge.runner")
MAX_STORED_DIFF = 1_000_000


class CapacityError(Exception):
    pass


class TaskRunner:
    def __init__(
        self, settings: Settings, store: Store, provider_factory: Callable[[], ModelProvider], sandbox: Sandbox,
        workspaces: WorkspaceManager, redactor: Redactor, graph: GraphManager | None = None,
    ):  # fmt: skip
        self.settings, self.store, self.provider_factory = settings, store, provider_factory
        self.sandbox, self.workspaces, self.redactor = sandbox, workspaces, redactor
        self.graph = graph  # None = knowledge graph disabled
        self.processes = ProcessManager(settings.terminal_max_processes)  # host terminal; outlives tasks
        self.pool = ThreadPoolExecutor(
            max_workers=max(settings.max_concurrent_tasks, 1), thread_name_prefix="forge-task"
        )
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ---- control surface used by the API ----------------------------------------------------------
    def spend(self) -> dict[str, float | None] | None:
        """Estimated spend over all recorded tasks; None when prices are not configured. Never a limit."""
        s = self.settings
        spent = s.cost_usd(*self.store.usage_totals())
        if spent is None:
            return None
        return {"spent_usd": round(spent, 4)}  # measured only: there is no spending limit

    def preflight(self) -> None:
        problem = self.settings.provider_problem()
        if problem:
            raise ConfigError(problem)
        ok, msg = self.sandbox.available()
        if not ok:
            raise ConfigError(msg)

    def start(self, task_id: str) -> Task:
        """Atomically claim a queued task (duplicate runs raise InvalidTransition) and execute it in the background."""
        self.preflight()
        with self._lock:
            if len(self._cancels) >= self.settings.max_concurrent_tasks:
                raise CapacityError(
                    f"Maximum concurrent tasks reached ({self.settings.max_concurrent_tasks}); try again later"
                )
            task = self.store.transition(task_id, TaskStatus.RUNNING)
            self._cancels[task_id] = threading.Event()
        self.pool.submit(self._execute, task_id)
        return task

    def cancel(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        status = TaskStatus(task.status)
        if status in TERMINAL:
            raise InvalidTransition(status, TaskStatus.CANCELLED)
        self.store.request_cancel(task_id)
        self.store.add_event(task_id, "task.cancel_requested", {})
        with self._lock:
            ev = self._cancels.get(task_id)
        if ev is not None:
            ev.set()  # the worker observes this, kills any running process tree and finalizes
        else:
            self.store.transition(task_id, TaskStatus.CANCELLED, error="cancelled before start")

    def orphan_grace_seconds(self) -> float:
        """The longest a live task can be silent: waiting for an approval, a tool or the model, plus a margin."""
        s = self.settings
        return float(max(s.approval_timeout_seconds, s.tool_timeout_seconds, s.model_timeout_seconds) + 60)

    def recover_orphans(self, quiet_seconds: float | None = None) -> int:
        """Tasks left active by a dead process can never finish; mark them failed (rerun to retry). Only tasks silent
        for longer than any live task can be (see orphan_grace_seconds) count, so another process's task is safe."""
        ids = self.store.orphaned_tasks(
            self.orphan_grace_seconds() if quiet_seconds is None else quiet_seconds
        )
        for tid in ids:
            self.store.transition(
                tid, TaskStatus.FAILED, error="Interrupted: the server restarted while this task was active"
            )
        return len(ids)

    def shutdown(self) -> None:
        for ev in list(self._cancels.values()):
            ev.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.processes.stop_all()

    # ---- worker -------------------------------------------------------------------------------
    def _record(self, task_id: str, results: list[CheckResult], phase: str, attempt: int) -> None:
        for r in results:
            self.store.add_verification(
                task_id, phase=phase, attempt=attempt, name=r.name, kind=r.kind, command=r.command, status=r.status,
                exit_code=r.exit_code, duration_seconds=r.duration_seconds, output=r.output, note=r.note,
            )  # fmt: skip
            self.store.add_event(task_id, "verification.check", {"phase": phase, "attempt": attempt, "kind": r.kind, "command": r.command or r.name, "status": r.status, "exit_code": r.exit_code, "note": r.note})  # fmt: skip

    def _execute(self, task_id: str) -> None:
        cancel = self._cancels[task_id]
        ws: Path | None = None
        project = ProjectInfo()
        baseline: list[CheckResult] = []
        outcome = AgentOutcome("error")
        provider_name = "unknown"
        background_ref: list[BackgroundJobs] = []  # stopped and joined before the task is finalized
        mcp_ref: list[McpManager] = []  # server processes: closed when the task ends
        loop: AgentLoop | None = None
        try:
            task = self.store.get_task(task_id)
            provider = self.provider_factory()
            provider_name = provider.name
            self.store.update_task(task_id, provider=provider.name, model=provider.model)
            repo = authorize_repo(task.repo_path, self.settings.repo_roots)
            chat = self.store.get_chat_session(task.session_id) if task.session_id else None
            ws, reused = self._workspace_for(task, chat, repo)
            self.store.update_task(task_id, workspace_path=str(ws))
            if reused:
                self.store.add_event(task_id, "workspace.reused", {"path": str(ws), "turn": task.turn, "execution_mode": self.sandbox.name})  # fmt: skip
            else:
                self.store.add_event(
                    task_id, "workspace.created", {"path": str(ws), "execution_mode": self.sandbox.name}
                )
            # where this turn starts from, and what the model already knows from earlier turns of the session
            previous = [
                t
                for t in (self.store.session_turns(chat.id, include_discarded=False) if chat else [])
                if t.turn < task.turn
            ]
            if previous and previous[-1].checkpoint:
                base_ref = previous[-1].checkpoint
            elif previous:
                base_ref = git.snapshot(ws, f"before turn {task.turn}", parent=git.head(ws))
            else:
                base_ref = git.head(ws)
            self.store.update_task(task_id, base_ref=base_ref)
            prior = self._prior_messages(previous)
            if prior:
                self.store.add_event(task_id, "session.continued", {"turn": task.turn, "earlier_turns": len(previous), "messages": len(prior)})  # fmt: skip
            project = inspect_project(ws)
            self.store.add_event(task_id, "project.inspected", {"types": project.kinds, "commands": project.commands, "notes": project.notes, "instructions": list(project.instructions)})  # fmt: skip
            ref_report = None
            if (
                task.references
            ):  # files the user pointed at, read from the isolated copy under the usual containment rules
                ref_report = load_references(WorkspacePaths(ws), task.references)
                self.store.add_event(task_id, "task.references", ref_report.event_data())

            knowledge_bundle, knowledge_stop = self._knowledge_bundle(task, task_id)
            if knowledge_stop:
                outcome = AgentOutcome("needs_clarification", error=knowledge_stop)
                self.store.add_event(task_id, "error", {"where": "knowledge", "message": knowledge_stop})
                return

            tool_ctx_touched: set[str] = set()
            graph_view = self._index_graph(task_id, ws, repo, cancel, tool_ctx_touched)

            checks = plan_checks(project, task.verification_commands, task.acceptance_criteria)
            engine = VerificationEngine(self.sandbox, timeout=self.settings.tool_timeout_seconds * 3, max_output=self.settings.max_output_bytes * 2)  # fmt: skip
            baseline = engine.run(checks, ws, cancel)
            self._record(task_id, baseline, "baseline", 0)

            s = self.settings
            budget = Budget(
                task.max_iterations, task.max_duration_seconds, s.max_tool_calls,
                price_in_per_mtok=s.price_input_per_mtok, price_out_per_mtok=s.price_output_per_mtok,
            )  # fmt: skip
            ctx = ToolContext(
                paths=WorkspacePaths(ws), sandbox=self.sandbox, project=project, timeout=s.tool_timeout_seconds,
                max_output=s.max_output_bytes, max_read=s.max_read_bytes, max_write=s.max_write_bytes, cancel=cancel,
                graph=graph_view, touched=tool_ctx_touched,
            )  # fmt: skip
            policy = PolicyEngine(s, ctx.paths, trusted_commands=[c.command for c in checks if c.command])
            gate = ApprovalGate(
                self.store, task_id, timeout=s.approval_timeout_seconds, cancel=cancel, budget=budget
            )
            executor = ToolExecutor(store=self.store, task_id=task_id, ctx=ctx, policy=policy, gate=gate, budget=budget, redactor=self.redactor)  # fmt: skip
            top = sorted(p.name + ("/" if p.is_dir() else "") for p in ws.iterdir() if p.name != ".git")
            graph_section = self._graph_context(task_id, graph_view, task, ref_report, baseline)

            image_blocks = [
                ImageBlock(i["media_type"], to_base64(d), i["name"], "high", ref=f"{task_id}:{i['index']}")
                for i, d in self.store.attachment_blobs(task_id)
            ]
            if image_blocks:
                self.store.add_event(task_id, "task.images", {
                    "count": len(image_blocks),
                    "items": [{"name": b.name, "media_type": b.media_type, "bytes": len(b.data) * 3 // 4} for b in image_blocks],
                })  # fmt: skip
            # looking at pictures is where the most careful reasoning pays off: default to the top level
            thinking = task.thinking or ("xhigh" if image_blocks else s.thinking_level)
            skills = self._skills(task_id, ws, task)
            memory_text, scope = self._memory(task_id, repo)
            agents, agent_problems = load_agents(Path(s.agents_dir) if s.agents_dir else None, set(TOOLS))
            if agent_problems:
                self.store.add_event(task_id, "agents.problems", {"problems": agent_problems[:20]})
            optional = {"spawn_agent"}
            if graph_view is not None:
                optional.add("graph_query")
            if skills.skills:
                optional.add("load_skill")
                ctx.skills = skills
            if scope is not None and s.memory_write != "deny":
                optional.add("remember")
                ctx.remember = self._remember_fn(task_id, scope)
            if s.terminal == "host":
                optional |= TERMINAL_TOOLS
                ctx.processes, ctx.owner, ctx.task_id = (
                    self.processes,
                    (chat.id if chat else task_id),
                    task_id,
                )
                ctx.secrets = [x for x in s.secret_values() if x]
                self.store.add_event(task_id, "terminal.enabled", {"where": "host", "note": "commands run on this machine outside Docker; each non-trivial command asks for approval"})  # fmt: skip
            tools = specs(optional=optional)
            mcp = self._start_mcp(task_id, repo, task.knowledge_project if knowledge_bundle else None)
            if mcp is not None:
                mcp_ref.append(mcp)
                ctx.mcp = mcp
                executor.redactor = self.redactor.with_secrets(
                    mcp.secrets()
                )  # server env/header values never reach events
                tools = [*tools, *mcp.specs()]
            context_policy = ContextPolicy(
                enabled=s.compaction_enabled, threshold=s.compaction_threshold_tokens, keep=s.compaction_keep_tokens,
                protect=s.prune_protect_tokens, minimum=s.prune_minimum_tokens,
            )  # fmt: skip
            skills_list = skills.render_list() if skills.listed() else None
            background = BackgroundJobs(cancel, max_running=s.subagent_max_background)
            background_ref.append(background)
            ctx.spawn = make_spawner(SubagentDeps(
                provider=provider, store=self.store, task_id=task_id, executor=executor, budget=budget, cancel=cancel,
                project=project, top_level=top, agents=agents, main_tools={t.name for t in tools}, thinking=thinking,
                skills_list=skills_list, max_steps=s.subagent_max_iterations, max_tokens=s.model_max_tokens,
                model_timeout=s.model_timeout_seconds, result_limit=int(s.max_output_bytes * 1.5),
                context_policy=context_policy, background=background, max_total=s.subagent_max_total,
            ))  # fmt: skip
            preloaded = [
                skills.render(skills.skills[sid]) for sid in (task.skills or []) if sid in skills.skills
            ]
            context = ContextBuilder(
                instructions=task.instructions, criteria=task.acceptance_criteria, project=project, baseline=baseline,
                top_level=top, references=ref_report, graph_section=graph_section, skills_list=skills_list,
                agents_list=render_agents(agents), preloaded_skills=preloaded, memory=memory_text,
                images=image_blocks,
                knowledge_section=kctx.render(knowledge_bundle.content) if knowledge_bundle else None,
            )  # fmt: skip

            def verify(attempt: int) -> tuple[list[CheckResult], Verdict]:
                self.store.transition(task_id, TaskStatus.VERIFYING)
                final_checks = plan_final_checks(
                    checks, inspect_project(ws), [f["path"] for f in git.changed_files(ws, base_ref) if f["status"] != "D"],
                    baseline_had_test=any(c.kind in BLOCKING_KINDS and c.command for c in checks),
                )  # fmt: skip
                results = engine.run(final_checks, ws, cancel)
                self._record(task_id, results, "final", attempt)
                verdict = aggregate(baseline, results)
                self.store.add_event(
                    task_id, "verification.result", {"attempt": attempt, **verdict.as_dict()}
                )
                return results, verdict

            loop = AgentLoop(
                provider=provider, store=self.store, task_id=task_id, executor=executor, budget=budget, cancel=cancel,
                context=context, verify=verify, max_recovery=s.max_recovery_attempts, max_tokens=s.model_max_tokens,
                model_timeout=s.model_timeout_seconds, result_limit=int(s.max_output_bytes * 1.5),
                tools=tools, thinking=thinking, context_policy=context_policy, background=background,
                prior=prior, turn=task.turn, changed_files=[f["path"] for f in git.changed_files(ws)] if prior else [],
            )  # fmt: skip
            outcome = loop.run()
            if outcome.reason in (
                "done",
                "verification_failed",
            ):  # a clean conversation the next turn can continue
                saved = dump_messages(loop.history)
                if len(str(saved)) <= MAX_SAVED_BYTES:
                    self.store.update_task(task_id, messages=saved)
        except Exception as e:  # lifecycle must always reach a terminal state
            log.exception("task %s crashed", task_id)
            outcome = AgentOutcome("error", error=f"{type(e).__name__}: {e}")
            self.store.add_event(task_id, "error", {"where": "runner", "message": outcome.error})
        finally:
            for m in mcp_ref:
                try:
                    m.close()
                except Exception:  # noqa: BLE001 - cleanup must not prevent finalization
                    log.exception("task %s: closing MCP servers failed", task_id)
            for bg in background_ref:
                try:
                    if bg.stop():
                        self.store.add_event(
                            task_id, "subagent.stopped", {"reason": "the task ended while they were running"}
                        )
                except Exception:  # noqa: BLE001 - cleanup must not prevent finalization
                    log.exception("task %s: stopping background sub-agents failed", task_id)
            try:
                self._finalize(task_id, ws, project, baseline, outcome, provider_name, cancel)
            except Exception:
                log.exception("task %s failed to finalize", task_id)
                try:
                    self.store.transition(
                        task_id, TaskStatus.FAILED, error="Internal error while finalizing the task"
                    )
                except Exception:
                    pass
            with self._lock:
                self._cancels.pop(task_id, None)

    # ---- MCP servers (optional; never allowed to fail a task) ----------------------------------------
    def _start_mcp(self, task_id: str, repo: Path, knowledge_project: str | None = None) -> McpManager | None:
        s = self.settings
        if not s.mcp_enabled and not knowledge_project:
            return None
        try:
            servers, problems, skipped = (
                discover_mcp(s.mcp_config_paths, repo / ".mcp.json", s.mcp_trust_project)
                if s.mcp_enabled
                else ([], [], [])
            )
            if (
                knowledge_project
            ):  # the harness's own retrieval server: fixed to this task's project, read-only
                servers = [x for x in servers if x.name != "knowledge"] + [
                    self._knowledge_server(knowledge_project)
                ]
            if not servers and not problems and not skipped:
                return None
            mgr = McpManager()
            mgr.start(servers, float(s.mcp_start_timeout_seconds), str(repo))
            self.store.add_event(task_id, "mcp.servers", {
                "connected": [{**mgr.configs[n].describe(), "tools": sum(1 for t in mgr.tools.values() if t.server == n)} for n in mgr.clients],
                "failed": {n: self.redactor.redact(e)[:500] for n, e in mgr.failed.items()},
                "problems": [self.redactor.redact(p)[:300] for p in problems][:20],
                "skipped_project_servers": skipped,
                "note": "repository .mcp.json ignored (FORGE_MCP_TRUST_PROJECT=false)" if skipped else None,
            })  # fmt: skip
            return mgr
        except Exception as e:  # noqa: BLE001
            log.exception("task %s: MCP setup failed", task_id)
            self.store.add_event(task_id, "mcp.servers", {"connected": [], "failed": {"mcp": f"{type(e).__name__}: {self.redactor.redact(str(e))[:300]}"}})  # fmt: skip
            return None

    # ---- knowledge base (optional; a problem here is reported, never allowed to crash a task) --------------
    def _knowledge_server(self, project: str) -> ServerConfig:
        s = self.settings
        env = {
            "FORGE_KNOWLEDGE_PROJECTS": project, "FORGE_DATABASE_URL": s.database_url,
            "FORGE_EMBEDDING_PROVIDER": s.resolved_embedding_provider, "FORGE_EMBEDDING_MODEL": s.embedding_model,
            "FORGE_KNOWLEDGE_VECTOR": s.knowledge_vector,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }  # fmt: skip
        if s.openai_api_key and s.resolved_embedding_provider == "openai":
            env["OPENAI_API_KEY"] = (
                s.openai_api_key
            )  # to embed the query; our own server, redacted from every event
        tools = frozenset(knowledge_tools.NAMES)  # every knowledge tool is read-only
        return ServerConfig(
            "knowledge", "stdio", command=sys.executable, args=["-m", "forge.knowledge.mcp_server"], env=env,
            auto_approve=tools, source="forge (built in)",
        )  # fmt: skip

    def _knowledge_service(self, project: str) -> KnowledgeService:
        return make_service(self.settings, KnowledgeRepo(self.store), make_embedder(self.settings), [project])

    def _knowledge_bundle(self, task: Task, task_id: str) -> tuple[kctx.Bundle | None, str | None]:
        """(bundle, stop_reason). stop_reason is set when the context is insufficient and the operator asked to stop."""
        project = task.knowledge_project
        if not project:
            return None, None
        if not self.settings.knowledge_enabled:
            self.store.add_event(task_id, "knowledge.disabled", {"project": project})
            return None, None
        try:
            svc = self._knowledge_service(project)
            if self.settings.knowledge_index_repo:
                self._index_repo(svc, project, Path(task.repo_path), task_id)
            bundle = kctx.build_bundle(svc, project, task.instructions, task.acceptance_criteria)
            repo = svc.repo
            repo.save_bundle(bundle.id, project, task_id, bundle.status, bundle.content)
            repo.audit(project, f"task:{task_id}", "bundle.built", "bundle", bundle.id, {"status": bundle.status, "items": sum(len(v) for v in bundle.content["slots"].values())})  # fmt: skip
            suff = bundle.content["sufficiency"]
            self.store.add_event(task_id, "knowledge.bundle", {
                "bundle_id": bundle.id, "project": project, "status": bundle.status,
                "items": {k: len(v) for k, v in bundle.content["slots"].items()},
                "missing": suff["missing"], "questions": bundle.content["unresolved_questions"],
                "embedding_model": bundle.content["embedding_model"],
            })  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.exception("task %s: knowledge bundle failed", task_id)
            self.store.add_event(task_id, "knowledge.unavailable", {"project": project, "message": f"{type(e).__name__}: {self.redactor.redact(str(e))[:300]}"})  # fmt: skip
            return None, None
        if bundle.status == "insufficient" and self.settings.knowledge_require_sufficient:
            qs = suff["missing"] + bundle.content["unresolved_questions"]
            return bundle, "Needs clarification before work can start (the knowledge base does not support this task): " + "; ".join(qs)[:900]  # fmt: skip
        return bundle, None

    def _index_repo(self, svc: KnowledgeService, project: str, repo: Path, task_id: str) -> None:
        """The repository the agent will change becomes code/test knowledge of the project, so the bundle can show what
        exists and link it to the requirements. Contained: a failure is reported and the task goes on."""
        try:
            res = Ingestor(svc.repo, svc.embedder, f"task:{task_id}").ingest_repository(
                project, repo, max_files=self.settings.knowledge_index_max_files
            )
            self.store.add_event(task_id, "knowledge.indexed", {
                "repository": repo.name, "created": res.created, "updated": res.updated, "unchanged": res.unchanged,
                "partial": res.partial[:10], "failed": res.failed[:10], "skipped": res.skipped[:10],
            })  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.exception("task %s: indexing the repository failed", task_id)
            self.store.add_event(task_id, "knowledge.unavailable", {"phase": "index", "message": f"{type(e).__name__}: {self.redactor.redact(str(e))[:300]}"})  # fmt: skip

    def _capture_knowledge(self, task_id: str) -> None:
        """After a verified task: propose (never publish) relationships for a human to review."""
        task = self.store.get_task(task_id)
        if not (task.knowledge_project and task.outcome == "verified" and self.settings.knowledge_enabled):
            return
        try:
            repo = KnowledgeRepo(self.store)
            row = repo.bundle_for_task(task_id)
            files = (
                git.changed_files(Path(task.workspace_path), task.base_ref or "HEAD")
                if task.workspace_path
                else []
            )
            # NEW verified files become (unapplied, agent-origin) code/test knowledge, so links can point at them;
            # changed files already are sources (the repository was indexed) and keep the repository's version
            changed = {f["path"] for f in files if f["status"] == "A"}
            svc = self._knowledge_service(task.knowledge_project)
            if changed and task.workspace_path:
                Ingestor(repo, svc.embedder, f"task:{task_id}").ingest_repository(
                    task.knowledge_project, Path(task.workspace_path), only=changed, origin="agent",
                    extra_meta={"task": task_id, "applied": False},
                )  # fmt: skip
            res = capture_knowledge(
                repo, task.knowledge_project, task_id, row.content if row else None, files
            )
            if svc.graph is not None:  # the graph database shows the new links at once (best effort)
                try:
                    res["neo4j"] = svc.graph.sync(repo, task.knowledge_project)
                except GraphUnavailable as ge:
                    res["neo4j"] = {"error": str(ge)}
            self.store.add_event(task_id, "knowledge.captured", res)
        except Exception as e:  # noqa: BLE001
            log.exception("task %s: knowledge capture failed", task_id)
            self.store.add_event(
                task_id, "knowledge.unavailable", {"phase": "capture", "message": f"{type(e).__name__}"}
            )

    # ---- sessions -------------------------------------------------------------------------------------
    def _workspace_for(self, task: Task, chat: Any, repo: Path) -> tuple[Path, bool]:
        """The session's workspace if it still exists (a follow-up turn continues in it), else a fresh copy."""
        root = self.settings.workspace_root_path
        if chat is not None and chat.workspace_path:
            p = Path(chat.workspace_path)
            if p.is_dir() and (p / ".git").is_dir() and p.resolve().parent == root.resolve():
                return p, True
        ws = self.workspaces.create(chat.id if chat is not None else task.id, repo)
        if chat is not None:
            self.store.update_chat_session(chat.id, workspace_path=str(ws))
        return ws, False

    def _prior_messages(self, previous: list[Task]) -> list[Message]:
        """What the model remembers: the last saved conversation of the session, plus a short note for every turn
        after it that ended early (those have no clean conversation to save)."""
        last = max((i for i, t in enumerate(previous) if t.messages), default=None)
        msgs: list[Message] = []
        if last is not None:
            msgs = load_messages(previous[last].messages or [], self._load_image)
        for t in previous[(last + 1 if last is not None else 0) :]:
            files = [f["path"] for f in ((t.report or {}).get("changed_files") or [])]
            reason = ((t.report or {}).get("agent") or {}).get("finish_reason")
            msgs += summary_of_unsaved_turn(t.turn, t.instructions, t.status, reason or t.error, files)
        return msgs

    def _load_image(self, ref: str) -> ImageBlock | None:
        task_id, _, idx = ref.partition(":")
        if not idx.isdigit():
            return None
        row = self.store.attachment_blob(task_id, int(idx))
        return None if row is None else ImageBlock(row[1], to_base64(row[2]), row[0], "high", ref=ref)

    # ---- skills and project memory ------------------------------------------------------------------
    def _skills(self, task_id: str, ws: Path, task: Task) -> SkillRegistry:
        reg = SkillRegistry.discover(self.settings.skill_dirs, ws if self.settings.repo_skills else None)
        if reg.skills or reg.problems:
            self.store.add_event(task_id, "skills.available", {
                "skills": [{"id": k.id, "source": k.source, "listed": k.autoinvoke} for k in reg.skills.values()][:100],
                "problems": reg.problems[:20],
            })  # fmt: skip
        for sid in task.skills or []:
            if sid in reg.skills:
                self.store.add_event(
                    task_id, "skill.loaded", {"id": sid, "how": "preloaded", "source": reg.skills[sid].source}
                )
            else:
                self.store.add_event(
                    task_id, "skill.missing", {"id": sid, "reason": "no skill with this id is available"}
                )
        return reg

    def _memory(self, task_id: str, repo: Path) -> tuple[str | None, str | None]:
        if not self.settings.memory_enabled:
            return None, None
        scope = repo_key(repo)
        notes = self.store.memories(scope)
        text, shown = render_memory(notes, self.settings.memory_context_chars)
        if notes:
            self.store.add_event(
                task_id, "memory.context", {"notes": len(notes), "shown": shown, "chars": len(text or "")}
            )
        return text, scope

    def _remember_fn(self, task_id: str, scope: str) -> Callable[[str], ToolResult]:
        def remember(note: str) -> ToolResult:
            try:
                clean = validate_note(note, self.redactor)
            except ValueError as e:
                return ToolResult(False, error=str(e))
            existing = self.store.memories(scope)
            if any(m.content == clean for m in existing):
                return ToolResult(True, {"saved": False, "note": "an identical note already exists"})
            if len(existing) >= self.settings.memory_max_entries:
                return ToolResult(
                    False,
                    error=f"project memory is full ({self.settings.memory_max_entries} notes); ask the user to prune it",
                )
            m = self.store.add_memory(scope, clean, "agent", task_id)
            self.store.add_event(task_id, "memory.saved", {"id": m.id, "chars": len(clean)})
            return ToolResult(True, {"saved": True, "id": m.id})

        return remember

    # ---- knowledge graph (optional; never allowed to fail a task) -----------------------------------
    def _index_graph(
        self, task_id: str, ws: Path, repo: Path, cancel: threading.Event, touched: set[str]
    ) -> RepoGraph | None:
        if self.graph is None:
            return None
        try:
            report, view, note = self.graph.index_for_task(ws, repo, cancel, touched=lambda: touched)
            data = report.event_data() if report else {"skipped": note}
            if note and report is not None:
                data["note"] = note
            self.store.add_event(task_id, "graph.indexed", data)
            return view
        except GraphUnavailable as e:
            self.store.add_event(task_id, "graph.unavailable", {"reason": str(e)})
        except Exception as e:  # noqa: BLE001 - a graph bug must not take a task down
            log.exception("graph indexing failed for task %s", task_id)
            self.store.add_event(
                task_id, "graph.unavailable", {"reason": f"internal error: {type(e).__name__}"}
            )
        return None

    def _graph_context(
        self,
        task_id: str,
        view: RepoGraph | None,
        task: Task,
        refs: RefReport | None,
        baseline: list[CheckResult],
    ) -> str | None:
        if view is None:
            return None
        try:
            failing = "\n".join(r.output for r in baseline if r.status == "failed")[-20_000:]
            ref_paths = [b.path for b in refs.blocks] if refs else []
            text, stats = view.context_block(
                task.instructions, ref_paths, failing, self.settings.graph_context_max_chars
            )
            self.store.add_event(task_id, "graph.context", stats)
            return self.redactor.redact(
                text
            )  # repository-derived text: same redaction as every other model input
        except GraphUnavailable as e:
            self.store.add_event(task_id, "graph.unavailable", {"reason": str(e), "stage": "context"})
        except Exception as e:  # noqa: BLE001
            log.exception("graph context failed for task %s", task_id)
            self.store.add_event(
                task_id,
                "graph.unavailable",
                {"reason": f"internal error: {type(e).__name__}", "stage": "context"},
            )
        return None

    def _finalize(self, task_id: str, ws: Path | None, project: ProjectInfo, baseline: list[CheckResult],
                  outcome: AgentOutcome, provider_name: str, cancel: threading.Event) -> None:  # fmt: skip
        diff, files = "", []
        if ws is not None and ws.exists():
            try:
                base = (
                    self.store.get_task(task_id).base_ref or "HEAD"
                )  # this turn's changes, not the whole session's
                diff, files = git.diff(ws, base=base), git.changed_files(ws, base)
                checkpoint = git.snapshot(
                    ws, f"after turn of task {task_id}", parent=None if base == "HEAD" else base
                )
                self.store.update_task(task_id, checkpoint=checkpoint)
            except git.GitError as e:
                self.store.add_event(task_id, "error", {"where": "diff", "message": str(e)})
        reason = "cancelled" if (cancel.is_set() and outcome.reason != "done") else outcome.reason
        if reason == "cancelled":
            status, task_outcome, error = TaskStatus.CANCELLED, None, "Cancelled by user"
        elif reason == "done" and outcome.verdict:
            status, task_outcome, error = TaskStatus.COMPLETED, outcome.verdict.outcome, None
        elif reason == "verification_failed":
            status, task_outcome, error = TaskStatus.FAILED, "verification_failed", outcome.error
        else:
            status, task_outcome, error = TaskStatus.FAILED, None, outcome.error or f"Stopped: {reason}"
        self.store.update_task(
            task_id, outcome=task_outcome, final_diff=self.redactor.redact(diff)[:MAX_STORED_DIFF]
        )
        self._capture_knowledge(task_id)  # before the terminal state, so a finished task has its proposals
        report = build_report(
            task=self.store.get_task(task_id), settings=self.settings, project=project, changed_files=files,
            baseline=baseline, outcome=outcome, verdict=outcome.verdict, final_checks=outcome.final_checks or [],
            provider_name=provider_name, sandbox_name=self.sandbox.name, status=status.value, error=error,
        )  # fmt: skip
        report = self.redactor.redact_obj(report)
        task = self.store.get_task(task_id)
        if TaskStatus(task.status) in ACTIVE:
            self.store.transition(task_id, status, error=error, report=report)
        self.store.add_event(
            task_id, "task.finished", {"status": status.value, "outcome": task_outcome, "reason": reason}
        )
        sid = self.store.get_task(task_id).session_id
        if sid:
            self.store.touch_chat_session(sid)
