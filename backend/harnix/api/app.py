"""FastAPI application factory and routes. Thin: validation and HTTP mapping only."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anyio.to_thread
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from sqlalchemy import inspect

from harnix import __version__
from harnix.api import schemas as sc
from harnix.api.knowledge import make_router as make_knowledge_router
from harnix.config import ConfigError, Settings, get_settings
from harnix.execution import git
from harnix.execution.apply import ApplyConflict, apply_workspace_to_repo, revert_applied, unsafe_reason
from harnix.execution.sandbox import Sandbox, make_sandbox
from harnix.execution.workspace import WorkspaceManager
from harnix.graph.manager import GraphManager
from harnix.harness.agents import load_agents
from harnix.harness.images import ImageError, to_base64, validate_image
from harnix.harness.memory import validate_note
from harnix.harness.report import estimated_cost, render_markdown
from harnix.harness.runner import CapacityError, TaskRunner
from harnix.harness.skills import SkillRegistry
from harnix.harness.states import ACTIVE, TERMINAL, InvalidTransition, TaskStatus
from harnix.models.base import ModelProvider
from harnix.models.factory import provider_factory as default_provider_factory
from harnix.observability.logging import configure_logging
from harnix.persistence.db import make_engine
from harnix.persistence.models import Task
from harnix.persistence.store import NotFound, Store, duration_seconds
from harnix.policies.paths import PathViolation, WorkspacePaths, authorize_repo, repo_key
from harnix.policies.redaction import Redactor
from harnix.tools.registry import TOOLS

log = logging.getLogger("forge.api")


@dataclass
class AppState:
    settings: Settings
    store: Store
    runner: TaskRunner
    redactor: Redactor
    sandbox: Sandbox
    graph: GraphManager | None = None


def _state(request: Request) -> AppState:
    return getattr(request.app.state, "harnix", getattr(request.app.state, "forge", None))


def _supplied(request: Request, token: str | None) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else token  # EventSource cannot set headers


def _acl_match(acl: dict[str, list[str]], supplied: str | None) -> list[str] | None:
    """The project list of the ACL entry whose token equals `supplied` (constant-time compare), if any."""
    found = None
    for tok, projects in acl.items():
        if supplied and hmac.compare_digest(supplied, tok):
            found = projects
    return found


def _auth(request: Request, token: str | None = Query(None, include_in_schema=False)) -> None:
    s = _state(request).settings
    acl = s.knowledge_acl_map
    if not s.api_token and not acl:
        return
    supplied = _supplied(request, token)
    if supplied and s.api_token and hmac.compare_digest(supplied, s.api_token):
        return
    if _acl_match(acl, supplied) is not None:
        return
    raise HTTPException(401, "Missing or invalid API token")


def _principal(request: Request) -> tuple[str, list[str] | None]:
    """Who is asking and which knowledge projects they may use (None = all). The API token is the administrator; a
    token listed in FORGE_KNOWLEDGE_ACL gets exactly its projects; with neither configured everything is open (POC)."""
    s = _state(request).settings
    supplied = _supplied(request, request.query_params.get("token"))
    if supplied and s.api_token and hmac.compare_digest(supplied, s.api_token):
        return "admin", None
    projects = _acl_match(s.knowledge_acl_map, supplied)
    if projects is not None:
        label = "acl:" + hashlib.sha256((supplied or "").encode()).hexdigest()[:8]
        return label, (None if "*" in projects else projects)
    return "local", None


def _task(request: Request, task_id: str) -> Task:
    try:
        return _state(request).store.get_task(task_id)
    except NotFound:
        raise HTTPException(404, f"Task not found: {task_id}") from None


def _out(task: Task, s: Settings, cls=sc.TaskOut, apply_state: str | None = None, images: list | None = None):
    o = cls.model_validate(task)
    o.apply_state = apply_state
    o.has_checkpoint = bool(task.checkpoint)
    o.images = [sc.ImageInfo(**i) for i in images or []]
    o.duration_seconds = duration_seconds(task)
    o.estimated_cost_usd = estimated_cost(task, s)
    return o


public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(_auth)])
ERR: dict[int | str, dict[str, Any]] = {
    400: {"model": sc.ErrorOut},
    404: {"model": sc.ErrorOut},
    409: {"model": sc.ErrorOut},
}


@public.get("/health")
def health(request: Request) -> dict:
    st = _state(request)
    ok, detail = st.sandbox.available()
    problem = st.settings.provider_problem()
    return {
        "status": "ok", "version": __version__, "database": "ok",
        "execution_mode": st.settings.execution_mode,
        "sandbox": {"available": ok, "detail": detail},
        "provider": {"name": st.settings.provider, "model": st.settings.model_name, "configured": problem is None, "problem": problem},
        "mock_provider": st.settings.provider == "scripted",
        "auth_required": bool(st.settings.api_token),
        "spend": st.runner.spend(),
        "graph": st.graph.health() if st.graph else {"enabled": False, "available": False, "detail": "FORGE_GRAPH_ENABLED is false"},
    }  # fmt: skip


@router.get("/config")
def config(request: Request) -> dict:
    s = _state(request).settings
    return {
        "provider": s.provider, "model": s.model_name, "execution_mode": s.execution_mode,
        "approval_policy": s.approval_policy, "allowed_repo_roots": [str(p) for p in s.repo_roots],
        "limits": {
            "max_iterations": s.max_iterations, "max_duration_seconds": s.max_duration_seconds,
            "tool_timeout_seconds": s.tool_timeout_seconds, "max_output_bytes": s.max_output_bytes,
            "max_tool_calls": s.max_tool_calls, "max_recovery_attempts": s.max_recovery_attempts,
        },
    }  # fmt: skip


@router.post("/tasks", status_code=201, response_model=sc.TaskDetail, responses=ERR)
def create_task(body: sc.TaskCreate, request: Request):
    st = _state(request)
    s = st.settings
    try:
        repo = authorize_repo(body.repo_path, s.repo_roots)
    except PathViolation as e:
        raise HTTPException(422, str(e)) from None
    if body.skills and not s.repo_skills:  # repository skills are only known at run time
        known = SkillRegistry.discover(s.skill_dirs).skills
        unknown = [x for x in body.skills if x not in known]
        if unknown:
            raise HTTPException(
                422,
                f"Unknown skill(s): {', '.join(unknown)}. Available: {', '.join(sorted(known)) or 'none'}",
            )
    session_id = None
    if body.session_id:
        try:
            chat = st.store.get_chat_session(body.session_id)
        except NotFound:
            raise HTTPException(404, f"Session {body.session_id} not found") from None
        if chat.repo_path != str(repo):
            raise HTTPException(
                422, "This session belongs to another repository; start a new session for this one"
            )
        if st.store.session_has_active_turn(chat.id):
            raise HTTPException(
                409, "This session is still working on its previous message; wait for it or cancel it"
            )
        session_id = chat.id
    images = []
    if body.images:
        if not s.images_enabled:
            raise HTTPException(422, "Image input is disabled on this server (FORGE_IMAGES_ENABLED=false)")
        if len(body.images) > s.max_images:
            raise HTTPException(422, f"At most {s.max_images} images can be attached to a task")
        try:
            images = [
                validate_image(im.name, im.media_type, im.data, max_bytes=s.max_image_bytes, index=n)
                for n, im in enumerate(body.images, 1)
            ]
        except ImageError as e:
            raise HTTPException(422, str(e)) from None
    for name, val, cap in (("max_iterations", body.max_iterations, s.max_iterations), ("max_duration_seconds", body.max_duration_seconds, s.max_duration_seconds)):  # fmt: skip
        if val is not None and val > cap:
            raise HTTPException(422, f"{name} may not exceed the server limit of {cap}")
    if body.knowledge_project:
        _, allowed = _principal(request)
        if allowed is not None and body.knowledge_project not in allowed:
            raise HTTPException(403, f"No access to knowledge project '{body.knowledge_project}'")
    task = st.store.create_task(
        session_id=session_id,
        knowledge_project=body.knowledge_project,
        title=(body.title or body.instructions.strip().splitlines()[0])[:200],
        repo_path=str(repo), instructions=body.instructions, acceptance_criteria=body.acceptance_criteria,
        verification_commands=body.verification_commands,
        references=[r.model_dump(exclude_none=True) for r in body.references],
        thinking=body.thinking, skills=body.skills,
        max_iterations=body.max_iterations or s.max_iterations,
        max_duration_seconds=body.max_duration_seconds or s.max_duration_seconds,
    )  # fmt: skip
    if images:
        st.store.add_attachments(task.id, images)
    return _out(task, s, sc.TaskDetail, images=st.store.attachment_info(task.id))


@router.get("/tasks", response_model=sc.TaskList)
def list_tasks(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    repo_path: str | None = Query(None, max_length=2000, description="Only tasks for this repository folder"),
):
    st = _state(request)
    if status and status not in {x.value for x in TaskStatus}:
        raise HTTPException(422, f"Unknown status '{status}'")
    if repo_path:  # the same normalization tasks were stored with (8.3 names, "..", case), so spellings match
        try:
            repo_path = str(authorize_repo(repo_path, st.settings.repo_roots))
        except PathViolation:
            return sc.TaskList(items=[], total=0, limit=limit, offset=offset)
    rows, total = st.store.list_tasks(limit, offset, status, repo_path)
    states = st.store.apply_states([t.id for t in rows])
    return sc.TaskList(
        items=[_out(t, st.settings, apply_state=states.get(t.id)) for t in rows],
        total=total, limit=limit, offset=offset,
    )  # fmt: skip


@router.get("/tasks/{task_id}", response_model=sc.TaskDetail, responses=ERR)
def get_task(task_id: str, request: Request):
    st = _state(request)
    t = _task(request, task_id)
    return _out(
        t, st.settings, sc.TaskDetail, st.store.apply_states([t.id]).get(t.id), st.store.attachment_info(t.id)
    )


@router.post("/tasks/{task_id}/run", status_code=202, response_model=sc.TaskOut, responses=ERR)
def run_task(task_id: str, request: Request):
    st = _state(request)
    _task(request, task_id)
    try:
        return _out(st.runner.start(task_id), st.settings)
    except InvalidTransition as e:
        raise HTTPException(409, f"Task cannot be started: {e}") from None
    except CapacityError as e:
        raise HTTPException(429, str(e)) from None
    # ConfigError -> 503 via handler


@router.post("/tasks/{task_id}/cancel", status_code=202, response_model=sc.TaskOut, responses=ERR)
def cancel_task(task_id: str, request: Request):
    st = _state(request)
    _task(request, task_id)
    try:
        st.runner.cancel(task_id)
    except InvalidTransition as e:
        raise HTTPException(409, f"Task cannot be cancelled: {e}") from None
    return _out(st.store.get_task(task_id), st.settings)


@router.post("/tasks/{task_id}/rerun", status_code=201, response_model=sc.TaskDetail, responses=ERR)
def rerun_task(task_id: str, request: Request):
    st = _state(request)
    old = _task(request, task_id)
    if TaskStatus(old.status) not in TERMINAL:
        raise HTTPException(409, "Only finished tasks can be re-run")
    new = st.store.create_task(
        title=f"{old.title[:180]} (rerun)", repo_path=old.repo_path, instructions=old.instructions,
        acceptance_criteria=old.acceptance_criteria, verification_commands=old.verification_commands,
        references=old.references, thinking=old.thinking, skills=old.skills, max_iterations=old.max_iterations, max_duration_seconds=old.max_duration_seconds, parent_task_id=old.id,
    )  # fmt: skip
    blobs = st.store.attachment_blobs(old.id)
    if blobs:  # the rerun sees the same images
        st.store.add_attachments(
            new.id,
            [
                validate_image(
                    i["name"], i["media_type"], to_base64(d), max_bytes=len(d) + 1, index=i["index"]
                )
                for i, d in blobs
            ],
        )
    return _out(new, st.settings, sc.TaskDetail, images=st.store.attachment_info(new.id))


def _session(request: Request, session_id: str):
    try:
        return _state(request).store.get_chat_session(session_id)
    except NotFound:
        raise HTTPException(404, f"Session {session_id} not found") from None


def _session_out(cs, summary: dict | None, cls=sc.SessionOut):
    o = cls.model_validate(cs)
    d = summary or {}
    o.turns, o.last_status, o.last_outcome, o.last_prompt = (
        d.get("turns", 0),
        d.get("last_status"),
        d.get("last_outcome"),
        d.get("last_prompt"),
    )
    return o


@router.get("/sessions", response_model=sc.SessionList)
def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    repo_path: str | None = Query(
        None, max_length=2000, description="Only sessions of this repository folder"
    ),
    archived: bool = False,
):
    st = _state(request)
    if repo_path:
        try:
            repo_path = str(authorize_repo(repo_path, st.settings.repo_roots))
        except PathViolation:
            return sc.SessionList(items=[], total=0, limit=limit, offset=offset)
    rows, total = st.store.list_chat_sessions(limit, offset, repo_path, archived)
    sums = st.store.session_summaries([r.id for r in rows])
    return sc.SessionList(
        items=[_session_out(r, sums.get(r.id)) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/sessions/{session_id}", response_model=sc.SessionDetail, responses=ERR)
def get_session(session_id: str, request: Request):
    st = _state(request)
    cs = _session(request, session_id)
    turns = st.store.session_turns(cs.id)
    base = _session_out(cs, st.store.session_summaries([cs.id]).get(cs.id))
    states = st.store.apply_states([t.id for t in turns])
    items = [
        _out(t, st.settings, sc.TaskDetail, states.get(t.id), st.store.attachment_info(t.id)) for t in turns
    ]
    return sc.SessionDetail(**base.model_dump(), items=items)  # type: ignore[arg-type]


@router.patch("/sessions/{session_id}", response_model=sc.SessionOut, responses=ERR)
def patch_session(session_id: str, body: sc.SessionPatch, request: Request):
    st = _state(request)
    cs = _session(request, session_id)
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if fields:
        st.store.update_chat_session(cs.id, **fields)
    return _session_out(_session(request, session_id), st.store.session_summaries([cs.id]).get(cs.id))


@router.post("/sessions/{session_id}/restore", response_model=sc.RestoreOut, responses=ERR)
def restore_session(session_id: str, body: sc.RestoreRequest, request: Request):
    """Go back to an earlier response: the workspace returns to how it was after turn N and the later turns are
    set aside (kept for the record, ignored when the conversation continues). Your real repository is not touched:
    revert anything you already applied from those turns separately."""
    st = _state(request)
    cs = _session(request, session_id)
    if not (body.confirm or body.dry_run):
        raise HTTPException(
            422, "This rolls the session back: send confirm=true (or dry_run=true to preview)"
        )
    if st.store.session_has_active_turn(cs.id):
        raise HTTPException(409, "The session is working on a message: wait for it or cancel it first")
    ws = Path(cs.workspace_path) if cs.workspace_path else None
    if ws is None or not ws.is_dir() or ws.resolve().parent != st.settings.workspace_root_path:
        raise HTTPException(409, "The session workspace no longer exists, so it cannot be rolled back")
    live = st.store.session_turns(cs.id, include_discarded=False)
    try:
        if body.turn == 0:
            target, anchor = git.head(ws), None
        else:
            anchor = next((t for t in live if t.turn == body.turn), None)
            if anchor is None:
                raise HTTPException(404, f"Session has no turn {body.turn}")
            if not anchor.checkpoint:
                raise HTTPException(409, f"Turn {body.turn} has no checkpoint to go back to")
            target = anchor.checkpoint
        later = [t for t in live if t.turn > body.turn]
        files = [sc.AppliedFile(path=p, status=s) for s, p in git.changed_paths_raw(ws, target, None)]
        if not body.dry_run:
            git.restore(ws, target)
            st.store.discard_turns([t.id for t in later])
            note = {"to_turn": body.turn, "discarded_turns": [t.turn for t in later]}
            for t in [anchor or (later[0] if later else None)]:
                if t is not None:
                    st.store.add_event(t.id, "session.restored", note)
            st.store.touch_chat_session(cs.id)
    except git.GitError as e:
        raise HTTPException(409, f"The session workspace is unusable (nothing was changed): {e}") from None
    return sc.RestoreOut(
        turn=body.turn, restored=not body.dry_run, discarded_tasks=[t.id for t in later], files=files
    )


@router.get("/tasks/{task_id}/images/{index}", responses=ERR)
def get_image(task_id: str, index: int, request: Request):
    """The bytes of an image attached to a task (for thumbnails; the type was checked when it was uploaded)."""
    _task(request, task_id)
    row = _state(request).store.attachment_blob(task_id, index)
    if row is None:
        raise HTTPException(404, f"Task {task_id} has no image {index}")
    return Response(
        content=row[2], media_type=row[1],
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"},
    )  # fmt: skip


@router.get("/tasks/{task_id}/events", response_model=list[sc.EventOut], responses=ERR)
def list_events(
    task_id: str, request: Request, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)
):
    _task(request, task_id)
    return _state(request).store.events_after(task_id, after, limit)


@router.get("/tasks/{task_id}/events/stream", responses=ERR)
async def stream_events(task_id: str, request: Request, after: int = Query(0, ge=0)):
    st = _state(request)
    await anyio.to_thread.run_sync(_task, request, task_id)
    last = int(request.headers.get("last-event-id") or after)

    async def gen():
        nonlocal last
        idle_terminal = 0
        ticks = 0
        while not await request.is_disconnected():
            events = await anyio.to_thread.run_sync(st.store.events_after, task_id, last, 200)
            for e in events:
                last = e.id
                payload = sc.EventOut.model_validate(e).model_dump_json()
                yield f"id: {e.id}\ndata: {payload}\n\n"  # unnamed: clients need only onmessage
            if events:
                idle_terminal = ticks = 0
                continue
            task = await anyio.to_thread.run_sync(st.store.get_task, task_id)
            if TaskStatus(task.status) in TERMINAL:
                idle_terminal += 1
                if idle_terminal >= 2:  # two quiet polls after terminal: everything has been flushed
                    yield "event: end\ndata: {}\n\n"
                    return
            ticks += 1
            if ticks % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks/{task_id}/diff", response_model=sc.DiffOut, responses=ERR)
def get_diff(task_id: str, request: Request):
    st = _state(request)
    task = _task(request, task_id)
    ws = Path(task.workspace_path) if task.workspace_path else None
    if TaskStatus(task.status) in ACTIVE and ws and ws.exists():
        try:
            base = task.base_ref or "HEAD"  # this turn's changes
            diff, files = st.redactor.redact(git.diff(ws, base=base)), git.changed_files(ws, base)
            return sc.DiffOut(diff=diff, files=files, live=True)  # type: ignore[arg-type]
        except git.GitError as e:
            raise HTTPException(409, str(e)) from None
    files = (task.report or {}).get("changed_files", [])
    return sc.DiffOut(diff=task.final_diff or "", files=files, live=False)  # type: ignore[arg-type]


FILE_VIEW_LIMIT = 1_000_000


@router.get("/tasks/{task_id}/file", response_model=sc.FileContentOut, responses=ERR)
def get_file_version(
    task_id: str,
    request: Request,
    path: str = Query(..., min_length=1, max_length=1000),
    version: Literal["baseline", "final"] = "final",
):
    """One file as the task started (`baseline`) or as it left it (`final`), for side-by-side review.
    Same path rules as apply: nothing outside the workspace, no secret-looking or protected files; secrets are redacted."""
    st = _state(request)
    task = _task(request, task_id)
    ws = Path(task.workspace_path) if task.workspace_path else None
    root = st.settings.workspace_root_path
    if ws is None or not ws.is_dir() or ws.resolve().parent != root:
        raise HTTPException(409, "The task workspace no longer exists")
    if (why := unsafe_reason(path)) is not None:
        raise HTTPException(422, f"{path}: refused, {why}")
    data: bytes | None
    try:
        if version == "baseline":
            data = git.baseline_bytes(ws, path, task.base_ref or "HEAD")  # as this turn found it
        elif task.checkpoint:
            data = git.blob(
                ws, path, task.checkpoint
            )  # as this turn left it, even if later turns changed it again
        else:
            try:
                target = WorkspacePaths(ws).resolve(path)
            except PathViolation as e:
                raise HTTPException(422, str(e)) from None
            data = target.read_bytes() if target.is_file() else None
    except git.GitError as e:
        raise HTTPException(409, str(e)) from None
    if data is None:
        return sc.FileContentOut(
            path=path, version=version, exists=False, binary=False, truncated=False, content=""
        )
    if b"\0" in data[:8000]:
        return sc.FileContentOut(
            path=path, version=version, exists=True, binary=True, truncated=False, size=len(data), content=""
        )
    truncated = len(data) > FILE_VIEW_LIMIT
    text = data[:FILE_VIEW_LIMIT].decode("utf-8", "replace")
    return sc.FileContentOut(
        path=path,
        version=version,
        exists=True,
        binary=False,
        truncated=truncated,
        size=len(data),
        content=st.redactor.redact(text),
    )


@router.post("/tasks/{task_id}/apply", response_model=sc.ApplyOut, responses=ERR)
def apply_task(task_id: str, body: sc.ApplyRequest, request: Request):
    """Copy a completed task's changes into the real repository (the only endpoint that writes there)."""
    st = _state(request)
    task = _task(request, task_id)
    if TaskStatus(task.status) != TaskStatus.COMPLETED:
        raise HTTPException(409, f"Only completed tasks can be applied (this one is {task.status})")
    if not (body.confirm or body.dry_run):
        raise HTTPException(
            422, "This modifies your repository: send confirm=true (or dry_run=true to preview)"
        )
    if task.discarded:
        raise HTTPException(409, "This turn was rolled back, so its changes cannot be applied")
    if st.store.apply_states([task_id]).get(task_id) == "applied":
        raise HTTPException(
            409, "This task's changes were already applied (revert them first to apply again)"
        )
    try:
        repo = authorize_repo(task.repo_path, st.settings.repo_roots)
    except PathViolation as e:
        raise HTTPException(422, str(e)) from None
    ws = Path(task.workspace_path) if task.workspace_path else None
    root = st.settings.workspace_root_path
    if ws is None or not ws.is_dir() or ws.resolve().parent != root:
        raise HTTPException(409, "The task workspace no longer exists, so its changes cannot be applied")
    # with snapshots, applying a turn moves the repository from what was last applied to the end of this turn
    chat = st.store.get_chat_session(task.session_id) if task.session_id else None
    from_ref, to_ref = "HEAD", None
    try:
        if task.checkpoint:
            to_ref = task.checkpoint
            from_ref = (chat.applied_ref if chat and chat.applied_ref else None) or git.head(ws)
        changes = apply_workspace_to_repo(ws, repo, dry_run=body.dry_run, from_ref=from_ref, to_ref=to_ref)
    except ApplyConflict as e:
        raise HTTPException(409, "Nothing was written. " + "; ".join(e.conflicts)) from None
    except git.GitError as e:
        raise HTTPException(
            409,
            f"The task workspace is unusable, so its changes cannot be applied (nothing was written): {e}",
        ) from None
    files = [sc.AppliedFile(path=c.path, status=c.status) for c in changes]
    if not body.dry_run:
        data: dict = {"files": [f.model_dump() for f in files]}
        if to_ref:
            data.update(from_ref=from_ref, to_ref=to_ref)
            if chat:
                st.store.update_chat_session(chat.id, applied_ref=to_ref)
        st.store.add_event(task_id, "task.applied", data)
    return sc.ApplyOut(
        applied=not body.dry_run, dry_run=body.dry_run, files=files, verified=task.outcome == "verified"
    )


@router.post("/tasks/{task_id}/revert", response_model=sc.ApplyOut, responses=ERR)
def revert_task(task_id: str, body: sc.ApplyRequest, request: Request):
    """Undo an earlier apply, restoring your repository to what it was before. Refuses (writing nothing) if any
    affected file was changed in your repository since the apply."""
    st = _state(request)
    task = _task(request, task_id)
    if st.store.apply_states([task_id]).get(task_id) != "applied":
        raise HTTPException(
            409, "This task's changes are not currently applied, so there is nothing to revert"
        )
    if not (body.confirm or body.dry_run):
        raise HTTPException(
            422, "This modifies your repository: send confirm=true (or dry_run=true to preview)"
        )
    try:
        repo = authorize_repo(task.repo_path, st.settings.repo_roots)
    except PathViolation as e:
        raise HTTPException(422, str(e)) from None
    ws = Path(task.workspace_path) if task.workspace_path else None
    root = st.settings.workspace_root_path
    if ws is None or not ws.is_dir() or ws.resolve().parent != root:
        raise HTTPException(409, "The task workspace no longer exists, so the apply cannot be reverted")
    applied = [e for e in st.store.events_after(task_id, 0, 100000) if e.type == "task.applied"]
    files = [(f["status"], f["path"]) for f in applied[-1].data.get("files", [])]
    from_ref, to_ref = applied[-1].data.get("from_ref") or "HEAD", applied[-1].data.get("to_ref")
    chat = st.store.get_chat_session(task.session_id) if task.session_id else None
    if to_ref and chat and chat.applied_ref and chat.applied_ref != to_ref:
        raise HTTPException(
            409, "A later turn was applied after this one: revert that one first (nothing was written)"
        )
    try:
        changes = revert_applied(ws, repo, files, dry_run=body.dry_run, from_ref=from_ref, to_ref=to_ref)
    except ApplyConflict as e:
        raise HTTPException(409, "Nothing was written. " + "; ".join(e.conflicts)) from None
    except git.GitError as e:
        raise HTTPException(409, f"The task workspace is unusable (nothing was written): {e}") from None
    out = [sc.AppliedFile(path=c.path, status=c.status) for c in changes]
    if not body.dry_run:
        if to_ref and chat:
            st.store.update_chat_session(chat.id, applied_ref=from_ref)
        st.store.add_event(task_id, "task.reverted", {"files": [f.model_dump() for f in out]})
    return sc.ApplyOut(applied=False, dry_run=body.dry_run, files=out, verified=task.outcome == "verified")


def _graph(request: Request) -> GraphManager:
    g = _state(request).graph
    if g is None:
        raise ConfigError(
            "The knowledge graph is disabled. Set FORGE_GRAPH_ENABLED=true and start Neo4j (docker compose up -d neo4j)."
        )
    return g


def _graph_repo(request: Request, repo_path: str) -> Path:
    try:
        return authorize_repo(repo_path, _state(request).settings.repo_roots)
    except PathViolation as e:
        raise HTTPException(422, str(e)) from None


@router.get("/graph/status", response_model=sc.GraphStatus, responses=ERR)
def graph_status(request: Request, repo_path: str = Query(..., max_length=2000)):
    """Index state of one authorized repository (and the state of the last pre-index job)."""
    return _graph(request).status(_graph_repo(request, repo_path))


@router.post("/graph/index", status_code=202, response_model=sc.GraphJob, responses=ERR)
def graph_index(body: sc.GraphIndexRequest, request: Request):
    """Pre-index a repository in the background (large repositories: do this once, tasks then stay incremental)."""
    g = _graph(request)
    if not g.health()["available"]:
        raise ConfigError(f"The knowledge graph database is not reachable: {g.health()['detail']}")
    return g.start_index(_graph_repo(request, body.repo_path))


@router.delete("/graph", responses=ERR)
def graph_drop(request: Request, repo_path: str = Query(..., max_length=2000)):
    """Delete a repository's graph (derived data: it is rebuilt by the next task or pre-index)."""
    return {"deleted_nodes": _graph(request).drop(_graph_repo(request, repo_path))}


@router.get("/processes")
def list_processes(request: Request):
    """Background processes the agent started on this machine (FORGE_TERMINAL=host); they die with the backend."""
    return {"items": _state(request).runner.processes.list()}


@router.post("/processes/{process_id}/stop")
def stop_process(process_id: str, request: Request):
    if not _state(request).runner.processes.stop(process_id):
        raise HTTPException(404, "No such process")
    return {"id": process_id, "stopped": True}


@router.get("/skills", response_model=sc.SkillList)
def list_skills(request: Request):
    """Operator skills (FORGE_SKILLS_DIRS). Repository skills, if enabled, are discovered per task at run time."""
    s = _state(request).settings
    reg = SkillRegistry.discover(s.skill_dirs)
    items = [
        sc.SkillOut(id=k.id, name=k.name, description=k.description, source=k.source, listed=k.autoinvoke)
        for k in sorted(reg.skills.values(), key=lambda k: k.id)
    ]
    return sc.SkillList(items=items, problems=reg.problems, repo_skills=s.repo_skills)


@router.get("/agents", response_model=sc.AgentList)
def list_agents(request: Request):
    s = _state(request).settings
    agents, problems = load_agents(Path(s.agents_dir) if s.agents_dir else None, set(TOOLS))
    items = [
        sc.AgentOut(id=a.id, description=a.description, tools=sorted(a.tools) if a.tools is not None else None,
                    thinking=a.thinking, max_iterations=a.max_iterations, source=a.source)
        for a in sorted(agents.values(), key=lambda a: a.id)
    ]  # fmt: skip
    return sc.AgentList(items=items, problems=problems)


def _memory_scope(request: Request, repo_path: str) -> str:
    st = _state(request)
    if not st.settings.memory_enabled:
        raise ConfigError("Project memory is disabled (FORGE_MEMORY_ENABLED=false).")
    try:
        return repo_key(authorize_repo(repo_path, st.settings.repo_roots))
    except PathViolation as e:
        raise HTTPException(422, str(e)) from None


@router.get("/memory", response_model=list[sc.MemoryOut], responses=ERR)
def list_memory(request: Request, repo_path: str = Query(..., max_length=2000)):
    return _state(request).store.memories(_memory_scope(request, repo_path))


@router.post("/memory", status_code=201, response_model=sc.MemoryOut, responses=ERR)
def add_memory(body: sc.MemoryCreate, request: Request):
    st = _state(request)
    scope = _memory_scope(request, body.repo_path)
    try:
        note = validate_note(body.content, st.redactor)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    existing = st.store.memories(scope)
    if len(existing) >= st.settings.memory_max_entries:
        raise HTTPException(
            409, f"Project memory is full ({st.settings.memory_max_entries} notes); delete some first"
        )
    return st.store.add_memory(scope, note, "user")


@router.delete("/memory/{memory_id}", responses=ERR)
def delete_memory(memory_id: str, request: Request, repo_path: str = Query(..., max_length=2000)):
    if not _state(request).store.delete_memory(_memory_scope(request, repo_path), memory_id):
        raise HTTPException(404, f"Memory note not found: {memory_id}")
    return {"deleted": memory_id}


@router.get("/tasks/{task_id}/verifications", response_model=list[sc.VerificationOut], responses=ERR)
def get_verifications(task_id: str, request: Request):
    _task(request, task_id)
    return _state(request).store.verifications(task_id)


@router.get("/tasks/{task_id}/approvals", response_model=list[sc.ApprovalOut], responses=ERR)
def get_approvals(task_id: str, request: Request):
    _task(request, task_id)
    return _state(request).store.approvals(task_id)


@router.post("/approvals/{approval_id}/decision", response_model=sc.ApprovalOut, responses=ERR)
def decide(approval_id: str, body: sc.ApprovalDecision, request: Request):
    st = _state(request)
    try:
        a = st.store.get_approval(approval_id)
    except NotFound:
        raise HTTPException(404, f"Approval not found: {approval_id}") from None
    new_status = "approved" if body.decision == "approve" else "rejected"
    if not st.store.decide_approval(approval_id, new_status, body.note):
        raise HTTPException(409, f"Approval is already {a.status}; it can only be decided once")
    return st.store.get_approval(approval_id)


@router.get("/tasks/{task_id}/report", responses=ERR)
def get_report(task_id: str, request: Request, format: str = Query("json", pattern="^(json|md)$")):
    task = _task(request, task_id)
    if not task.report:
        raise HTTPException(409, "Report is available once the task has finished")
    if format == "md":
        return PlainTextResponse(render_markdown(task.report), media_type="text/markdown")
    return JSONResponse(task.report)


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def create_app(
    settings: Settings | None = None,
    *,
    provider_factory: Callable[[], ModelProvider] | None = None,
    sandbox: Sandbox | None = None,
    create_schema: bool = False,
) -> FastAPI:
    settings = settings or get_settings()
    redactor = Redactor(settings.secret_values())
    configure_logging(settings.log_level, redactor)
    if settings.ignored_limits():
        log.warning(
            "%s %s no longer limit anything: Forge measures token use and estimated cost but never stops a task "
            "for spending (only the iteration, time and tool-call limits stop a task). Remove them from .env.",
            ", ".join(settings.ignored_limits()),
            "is set but" if len(settings.ignored_limits()) == 1 else "are set but",
        )
    engine = make_engine(settings.database_url)
    store = Store(engine, redactor)
    if create_schema:
        store.create_schema()
    elif "tasks" not in inspect(engine).get_table_names():
        raise ConfigError(
            "Database schema is missing. Run migrations first: `alembic upgrade head` (from the backend directory)."
        )
    sb = sandbox or make_sandbox(settings)
    graph = GraphManager(settings) if settings.graph_enabled else None
    runner = TaskRunner(
        settings, store, provider_factory or default_provider_factory(settings), sb,
        WorkspaceManager(settings.workspace_root_path, settings.max_repo_copy_mb), redactor, graph,
    )  # fmt: skip

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        n = runner.recover_orphans()
        if n:
            log.warning("marked %d interrupted task(s) as failed", n)
        # tasks that were active a moment ago may belong to this database's other process, or to the one that just
        # died: look again once a live task would have shown a sign of life
        later = threading.Timer(runner.orphan_grace_seconds(), runner.recover_orphans)
        later.daemon = True
        later.start()
        if settings.execution_mode == "local":
            log.warning(
                "EXECUTION MODE IS LOCAL: agent commands run on this host without container isolation"
            )
        if settings.provider_problem():
            log.warning("model provider not configured: %s", settings.provider_problem())
        if graph is not None:
            h = graph.health()
            if not h["available"]:
                log.warning(
                    "knowledge graph enabled but Neo4j is not reachable: %s (tasks run without it)",
                    h["detail"],
                )
        yield
        runner.shutdown()
        if graph is not None:
            graph.close()

    app = FastAPI(
        title="Harnix",
        version=__version__,
        description="Self-verifying autonomous AI coding agent harness & spec intelligence engine",
        lifespan=lifespan,
    )
    app.state.harnix = AppState(settings, store, runner, redactor, sb, graph)
    app.state.forge = app.state.harnix
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(public)
    app.include_router(router)
    app.include_router(make_knowledge_router(_state, _principal, [Depends(_auth)]))

    @app.exception_handler(HTTPException)
    async def _http(_: Request, e: HTTPException):
        return _error(e.status_code, {401: "unauthorized", 404: "not_found", 409: "conflict", 422: "invalid_request", 429: "capacity"}.get(e.status_code, "error"), str(e.detail))  # fmt: skip

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        details = [
            {"field": ".".join(str(x) for x in err["loc"][1:]), "message": err["msg"]} for err in e.errors()
        ]
        return _error(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(ConfigError)
    async def _config(_: Request, e: ConfigError):
        return _error(503, "not_configured", str(e))

    @app.exception_handler(InvalidTransition)
    async def _transition(_: Request, e: InvalidTransition):
        return _error(409, "invalid_transition", str(e))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("unhandled error")
        return _error(500, "internal_error", "Internal server error")

    return app


def app_factory() -> FastAPI:  # `uvicorn forge.api.app:app_factory --factory`
    try:
        return create_app()
    except ConfigError as e:  # fail early with one actionable line instead of a traceback
        raise SystemExit(f"Forge configuration error: {e}") from None
