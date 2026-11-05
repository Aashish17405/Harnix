"""`harnix`: use the self-verifying coding harness from the terminal of any IDE.

    harnix serve [--allow DIR]       start the backend (migrates the database, authorizes DIR)
    harnix                           interactive session in the current directory
    harnix "fix the failing test"    one task, streamed live, approvals inline, then offer to apply
    harnix "explain @src/app.py#10-40 and fix @tests/test_app.py"   point at files (and line ranges) with @
    harnix apply TASK_ID             apply a finished task's changes to the repository later
    harnix doctor | harnix tasks

Each prompt is an independent task on an isolated copy of the repository. Nothing touches your files
until you answer yes to "apply". Exit codes: 0 verified, 4 completed but NOT verified, 1 failed,
2 usage/setup problem, 3 backend unreachable or not configured, 130 cancelled.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from harnix.client import HarnixClient, HarnixError, ForgeClient, ForgeError

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_SETUP, EXIT_UNVERIFIED, EXIT_CANCELLED = 0, 1, 2, 3, 4, 130
DEFAULT_URL = "http://127.0.0.1:8000"
SUBCOMMANDS = {
    "serve",
    "run",
    "apply",
    "doctor",
    "tasks",
    "graph",
    "skills",
    "agents",
    "memory",
    "knowledge",
    "ps",
    "stop",
}


class Style:
    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else s

    def dim(self, s):
        return self._w("2", s)

    def bold(self, s):
        return self._w("1", s)

    def red(self, s):
        return self._w("31", s)

    def green(self, s):
        return self._w("32", s)

    def yellow(self, s):
        return self._w("33", s)

    def cyan(self, s):
        return self._w("36", s)


def make_style(stream=None) -> Style:
    stream = stream or sys.stdout
    on = bool(getattr(stream, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ
    if on and os.name == "nt":
        os.system("")  # enables ANSI escape processing in the Windows console
    return Style(on)


# ---- rendering ----------------------------------------------------------------------------------


def short_args(args: dict, limit: int = 110) -> str:
    text = ", ".join(
        f"{k}={v!r}" if not isinstance(v, str) else f"{k}={v}"
        for k, v in args.items()
        if v not in (None, ".", False)
    )
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tail(text: str, n: int = 8) -> list[str]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return (["    …"] if len(lines) > n else []) + [f"    {ln}" for ln in lines[-n:]]


def render_event(e: dict, s: Style) -> list[str]:
    """Human-readable lines for one event (empty list = not interesting enough to print)."""
    t, d = e["type"], e["data"]
    if t == "workspace.created":
        return [s.dim(f"workspace ready ({d.get('execution_mode')})")]
    if t == "task.references":
        inc = [
            f"{i['path']}"
            + (f":{i['start']}-{i['end']}" if i.get("truncated") or i["start"] != 1 else "")
            + (" (cut)" if i.get("truncated") else "")
            for i in d.get("included", [])
        ]
        lines = [s.dim(f"attached {len(inc)} file(s): {', '.join(inc)}")] if inc else []
        lines += [s.yellow(f"  could not attach {k['path']}: {k['reason']}") for k in d.get("skipped", [])]
        return lines
    if t == "skills.available":
        listed = [k["id"] for k in d.get("skills", []) if k.get("listed")]
        lines = [s.dim(f"skills available: {', '.join(listed)}")] if listed else []
        return lines + [s.yellow(f"  skill skipped: {p}") for p in d.get("problems", [])[:3]]
    if t == "skill.loaded":
        return [s.dim(f"skill loaded: {d['id']} ({d.get('how')})")]
    if t == "skill.missing":
        return [s.yellow(f"skill not found: {d['id']}")]
    if t == "memory.context":
        return [s.dim(f"project memory: {d.get('shown')} of {d.get('notes')} note(s) in context")]
    if t == "memory.saved":
        return [s.dim("  saved a project-memory note")]
    if t == "subagent.started":
        return [
            s.bold(
                f"  ⇢ sub-agent {d['agent']}: {d.get('description')} (thinking {d.get('thinking')}, max {d.get('max_steps')} steps)"
            )
        ]
    if t == "subagent.finished":
        colour = s.green if d.get("reason") == "done" else s.yellow
        return [colour(f"  ⇠ sub-agent {d['agent']} finished ({d.get('reason')}, {d.get('steps')} steps)")]
    if t == "subagent.delivered":
        return [s.dim(f"  background report(s) delivered: {', '.join(d.get('agents', []))}")]
    if t == "subagent.waiting":
        return [s.dim(f"  waiting for {d.get('running')} background sub-agent(s) before finishing")]
    if t == "subagent.stopped":
        return [s.yellow("  background sub-agent(s) stopped: the task ended")]
    if t == "context.pruned":
        return [
            s.dim(
                f"context: pruned {d['results_pruned']} old tool output(s), ~{d['tokens_reclaimed']} tokens reclaimed"
            )
        ]
    if t == "context.compacted":
        return [s.dim(f"context: compacted ({d.get('reason')}) ~{d['tokens_before']} -> ~{d['tokens_after']} tokens, "
                      f"{d['summarized_messages']} message(s) summarized")]  # fmt: skip
    if t == "context.compaction_failed":
        return [s.yellow(f"context: compaction failed ({d.get('reason')}): {d.get('error')}")]
    if t == "graph.indexed":
        if "skipped" in d:
            return [s.yellow(f"knowledge graph: not re-indexed ({d['skipped']})")]
        part = "" if d.get("complete") else s.yellow(" (partial: continues on the next run)")
        return [
            s.dim(
                f"knowledge graph: {d.get('files', 0)} files, {d.get('symbols', 0)} symbols; "
                f"{d.get('files_parsed', 0)} re-indexed, {d.get('files_unchanged', 0)} unchanged "
                f"in {d.get('seconds', 0)}s"
            )
            + part
        ]
    if t == "graph.unavailable":
        return [s.yellow(f"knowledge graph unavailable, continuing without it: {d.get('reason')}")]
    if t == "graph.context":
        return [
            s.dim(
                f"knowledge graph context: {d.get('chars', 0)} chars, {d.get('seed_files', 0)} focus file(s)"
            )
        ]
    if t == "project.inspected":
        return [
            s.dim(
                f"detected {', '.join(d.get('types') or ['unknown project'])}; test command: {d.get('commands', {}).get('test', 'none')}"
            )
        ]
    if t == "verification.check":
        colour = {"passed": s.green, "failed": s.red}.get(d["status"], s.yellow)
        extra = f" ({d['note']})" if d.get("note") else ""
        return [
            f"  {colour('[' + d['status'] + ']')} {d['kind']}: {d['command']}  {s.dim(d['phase'])}{s.dim(extra)}"
        ]
    if t == "verification.result":
        colour = {"verified": s.green, "verification_failed": s.red}.get(d["outcome"], s.yellow)
        return [
            colour(f"verification: {d['outcome']}")
            + (s.dim(" - " + "; ".join(d["reasons"])) if d.get("reasons") else "")
        ]
    if t == "model.response":
        out = []
        th = d.get("thinking") or {}
        if th.get("reason") and th.get("requested") != th.get("applied"):
            out.append(s.yellow(f"  thinking {th.get('requested')} -> {th.get('applied')}: {th['reason']}"))
        if d.get("text"):
            out.append("")
            out += [s.cyan(ln) for ln in d["text"].strip().splitlines()[:12]]
        return out
    if t == "tool.proposed":
        who = f"[{d['agent']}] " if d.get("agent") else ""
        return [f"  {who}{s.bold('▶ ' + d['tool'])} {s.dim(short_args(d.get('arguments', {})))}".rstrip()]
    if t == "tool.authorization":
        if d["decision"] == "deny":
            return [s.red(f"    ✖ refused by policy: {d['reason']}")]
        if d["decision"] == "approve":
            return [s.yellow(f"    ⚠ needs your approval: {d['reason']}")]
        return []
    if t == "tool.result":
        if not d.get("ok"):
            if str(d.get("error") or "").startswith("Not approved"):
                return []  # the approval.decided line just before already says so
            return [s.red(f"    ✗ {d.get('error') or 'failed'}")]
        data = d.get("data") or {}
        if "exit_code" in data:
            lines = [s.dim(f"    exit {data['exit_code']} in {data.get('duration_seconds', '?')}s")]
            if data["exit_code"] != 0:
                lines += [s.dim(ln) for ln in tail(data.get("output", ""))]
            return lines
        return [s.dim("    ✓")]
    if t == "approval.decided":
        return [
            (s.green if d["approved"] else s.red)(
                f"    {'approved' if d['approved'] else 'not approved'}: {d['reason']}"
            )
        ]
    if t == "retry.decision":
        what = {
            "verification_recovery": "verification failed; asking the agent to fix it",
            "provider": "model error; retrying",
        }.get(d.get("kind"), d.get("kind", "retry"))
        return [s.yellow(f"↻ {what} ({d.get('attempt', '?')})")]
    if t == "error":
        return [s.red(f"! {d.get('message', 'error')}")]
    if t == "task.state" and d.get("to") == "verifying":
        return [s.dim("running independent verification…")]
    if t == "task.applied":
        return []
    return []


def render_diff(diff: str, s: Style) -> list[str]:
    out = []
    for ln in diff.splitlines():
        if ln.startswith(("+++", "---", "diff --git", "index ")):
            out.append(s.dim(ln))
        elif ln.startswith("@@"):
            out.append(s.cyan(ln))
        elif ln.startswith("+"):
            out.append(s.green(ln))
        elif ln.startswith("-"):
            out.append(s.red(ln))
        else:
            out.append(ln)
    return out


# ---- running a task -----------------------------------------------------------------------------


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def _safe_ask(ask: Callable[[str], str], prompt: str) -> str:
    try:
        return ask(prompt)
    except EOFError:  # no terminal attached: the safe answer is "no"
        return "n"


def handle_approval(client: ForgeClient, ev: dict, out, ask, s: Style) -> None:
    d = ev["data"]
    out(s.yellow("  ┌─ approval required"))
    out(f"  │ {d['tool']}: {d['reason']}")
    for k, v in d["arguments"].items():
        if v not in (None, ".", False):
            out(f"  │   {k}: {str(v)[:300]}")
    answer = _safe_ask(ask, s.yellow("  └─ Approve exactly this action? [y/N] "))
    try:
        client.decide(d["approval_id"], "approve" if _yes(answer) else "reject")
    except ForgeError as e:  # already decided elsewhere (UI) or expired
        out(s.dim(f"    ({e.message})"))


_MENTION = re.compile(r"(?<!\S)@([^\s@#]+?)(?:#(\d+)(?:-(\d+))?)?([.,;:!?)]*)(?=\s|$)")


def extract_references(prompt: str, repo: Path) -> tuple[str, list[dict]]:
    """Turn `@path` / `@path#10-20` mentions of existing files inside `repo` into references.

    The mention is replaced by the plain path in the text; anything that is not an existing file inside the
    repository (`@alice`, an email address, `@../secret`) is left exactly as typed.
    """
    refs: list[dict] = []
    root = repo.resolve()

    def sub(m: re.Match) -> str:
        path, a, b, trail = m.groups()
        rel = path.replace("\\", "/")
        target = (root / rel).resolve()
        if not (target.is_file() and target.is_relative_to(root)):
            return m.group(0)
        ref: dict = {"path": rel}
        if a:
            ref.update(start_line=int(a), end_line=int(b or a))
        if ref not in refs:
            refs.append(ref)
        return f"{rel}{f' (lines {a}-{b or a})' if a else ''}{trail}"

    return _MENTION.sub(sub, prompt), refs


def run_task(
    client: ForgeClient, *, repo: Path, prompt: str, criteria: list[str] | None = None,
    verify: list[str] | None = None, max_iterations: int | None = None, max_minutes: int | None = None,
    out: Callable[[str], None] = print, ask: Callable[[str], str] = input, auto_apply: bool = False,
    show_diff: bool = True, style: Style | None = None, references: list[dict] | None = None,
    thinking: str | None = None, skills: list[str] | None = None, images: list[dict] | None = None,
    conversation: dict | None = None, knowledge_project: str | None = None,
    _rounds: int = 0,
) -> int:  # fmt: skip
    s = style or Style(False)
    try:
        health = client.health()
    except ForgeError as e:
        out(s.red(e.message))
        out("Start the backend in another terminal:  harnix serve --allow " + f'"{repo}"')
        return EXIT_SETUP
    prov = health["provider"]
    if not prov["configured"]:
        out(s.red(f"Model provider is not configured: {prov['problem']}"))
        return EXIT_SETUP
    if not health["sandbox"]["available"]:
        out(s.red(f"Execution sandbox unavailable: {health['sandbox']['detail']}"))
        return EXIT_SETUP
    try:
        task = client.create_task(
            repo_path=str(repo), instructions=prompt, acceptance_criteria=criteria, verification_commands=verify,
            references=references, thinking=thinking, skills=skills, images=images, session_id=(conversation or {}).get("id"),
            knowledge_project=knowledge_project,
            max_iterations=max_iterations, max_duration_seconds=max_minutes * 60 if max_minutes else None,
        )  # fmt: skip
    except ForgeError as e:
        out(s.red(e.message))
        if e.status == 422 and "authorized" in e.message.lower():
            out(f'Restart the backend allowing this directory:  harnix serve --allow "{repo}"')
        return EXIT_USAGE
    tid = task["id"]
    if conversation is not None:
        conversation["id"] = task.get(
            "session_id"
        )  # the next prompt of this session continues the same conversation
    try:
        client.run(tid)
    except ForgeError as e:
        out(s.red(f"Could not start the task: {e.message}"))
        return EXIT_SETUP
    spend = health.get("spend")
    out(
        s.dim(
            f"task {tid} · {prov['name']} {prov['model']} · {health['execution_mode']} sandbox"
            + (
                f" · session {task['session_id']} message {task.get('turn', 1)}"
                if task.get("session_id")
                else ""
            )
            + (f" · spent ${spend['spent_usd']:.3f}" if spend else "")
        )
    )
    interrupted = False
    try:
        for ev in client.stream_events(tid):
            for line in render_event(ev, s):
                out(line)
            if ev["type"] == "approval.requested":
                handle_approval(client, ev, out, ask, s)
    except KeyboardInterrupt:
        interrupted = True
        out(s.yellow("\nInterrupted: cancelling the task (running commands are killed)…"))
        try:
            client.cancel(tid)
            for _ in client.stream_events(tid, read_timeout=30, retries=2):
                pass
        except (ForgeError, KeyboardInterrupt):
            pass
    except ForgeError as e:
        out(s.red(e.message))
        out("The task may still be running; check it with: harnix tasks")
        return EXIT_FAILED

    task = client.get_task(tid)
    if (
        knowledge_project
        and task["status"] == "failed"
        and (task.get("error") or "").startswith("Needs clarification")
        and _rounds < 2
        and not interrupted
    ):  # the knowledge base could not support the task: ask the person, then try again with their answers
        out(s.yellow("● " + task["error"]))
        answer = ask("Your answer (what the agent should assume or follow; empty to stop): ").strip()
        if answer:
            return run_task(
                client, repo=repo, prompt=f"{prompt}\n\nClarifications from the requester (authoritative, not from the knowledge base):\n{answer}",
                criteria=criteria, verify=verify, max_iterations=max_iterations, max_minutes=max_minutes, out=out, ask=ask,
                auto_apply=auto_apply, show_diff=show_diff, style=style, references=references, thinking=thinking, skills=skills,
                images=images, conversation=conversation, knowledge_project=knowledge_project, _rounds=_rounds + 1,
            )  # fmt: skip
    return _finish(
        client,
        task,
        repo=repo,
        interrupted=interrupted,
        out=out,
        ask=ask,
        auto_apply=auto_apply,
        show_diff=show_diff,
        s=s,
    )


def _finish(client, task, *, repo, interrupted, out, ask, auto_apply, show_diff, s) -> int:
    tid, status, outcome = task["id"], task["status"], task["outcome"]
    out("")
    if status == "completed":
        out(
            s.green("✓ completed, verified by independent checks")
            if outcome == "verified"
            else s.yellow(
                "● completed but NOT verified: no test or acceptance check confirmed it. Review the changes yourself."
            )
        )
    elif status == "cancelled":
        out(s.yellow("● cancelled"))
    else:
        out(s.red(f"✗ {status}: {task.get('error') or 'stopped'}"))
    for v in [v for v in client.verifications(tid) if v["phase"] == "final"][-6:]:
        out(f"  {v['status']:11} {v['kind']}: {v['command'] or v['name']}")
    report = task.get("report") or {}
    if report.get("false_success_claim"):
        out(s.red("  the agent claimed success, but independent verification failed"))
    if report.get("known_limitations"):
        out(s.dim("limitations:"))
        for ln in report["known_limitations"][:6]:
            out(s.dim(f"  - {ln}"))
    cost = task.get("estimated_cost_usd")
    out(
        s.dim(
            f"{task['iterations']} model calls · {task['tool_calls']} tool calls ({task['tool_failures']} failed, {task['policy_denials']} refused) · {task['input_tokens']}/{task['output_tokens']} tokens"
            + (f" · ~${cost:.4f}" if cost is not None else "")
            + (f" · {task['duration_seconds']:.0f}s" if task.get("duration_seconds") else "")
        )
    )
    final = (report.get("agent") or {}).get("final_message")
    if final:
        out(s.dim("agent's summary (its own claim, not evidence):"))
        out(s.dim("  " + final.strip().replace("\n", "\n  ")[:1500]))

    code = {
        "completed": EXIT_OK if outcome == "verified" else EXIT_UNVERIFIED,
        "cancelled": EXIT_CANCELLED,
    }.get(status, EXIT_FAILED)
    if interrupted:
        code = EXIT_CANCELLED
    if status == "completed":
        d = client.diff(tid)
        files = d["files"]
        if not files:
            out("no file changes were made.")
            return code
        out("")
        out(s.bold(f"{len(files)} file(s) changed:"))
        for f in files:
            out(
                f"  {f['status']} {f['path']}  {s.green('+' + str(f['additions']))} {s.red('-' + str(f['deletions']))}"
            )
        if show_diff:
            out("")
            for ln in render_diff(d["diff"], s):
                out(ln)
        verified = outcome == "verified"
        do_apply = False
        if auto_apply:
            do_apply = verified
            if not verified:
                out(s.yellow("--apply skipped: the result is not verified."))
        else:
            q = f"Apply these changes to {repo}? [y/N] " + ("" if verified else "(NOT verified!) ")
            do_apply = _yes(_safe_ask(ask, "\n" + s.bold(q)))
        if do_apply:
            out(apply_and_report(client, tid, out=out, s=s))
        else:
            out(s.dim(f"not applied. Apply later with:  harnix apply {tid}"))
    return code


def apply_and_report(client: ForgeClient, tid: str, *, out, s: Style) -> str:
    try:
        r = client.apply(tid, confirm=True)
    except ForgeError as e:
        return s.red(f"not applied: {e.message}")
    return s.green(
        f"✓ applied {len(r['files'])} file(s) to your repository: "
        + ", ".join(f"{f['status']} {f['path']}" for f in r["files"])
    )


# ---- commands -----------------------------------------------------------------------------------


def client_from(args) -> ForgeClient:
    token = os.environ.get("FORGE_API_TOKEN")
    if not token:
        try:
            from harnix.config import Settings

            token = Settings().api_token
        except Exception:
            token = None
    return ForgeClient(args.url, token)


def load_images(paths: list[str]) -> list[dict]:
    """Read image files for a task. Raises ValueError with a message the user can act on."""
    import base64

    from harnix.harness.images import sniff

    out: list[dict] = []
    for raw in paths or []:
        p = Path(raw).expanduser()
        if not p.is_file():
            raise ValueError(f"--image {raw}: no such file")
        data = p.read_bytes()
        kind = sniff(data)
        if kind is None:
            raise ValueError(f"--image {raw}: not a PNG, JPEG, GIF or WebP image")
        out.append({"name": p.name, "media_type": kind, "data": base64.b64encode(data).decode("ascii")})
    return out


def cmd_run(args, out=print, ask=input) -> int:
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        out('Give the agent something to do, e.g.  forge "fix the failing test in tests/test_parse.py"')
        return EXIT_USAGE
    repo = Path(args.repo).resolve()
    prompt, refs = extract_references(prompt, repo)
    try:
        images = load_images(args.image)
    except ValueError as e:
        out(str(e))
        return EXIT_USAGE
    return run_task(
        client_from(args), repo=repo, prompt=prompt, criteria=args.criteria, verify=args.verify,
        max_iterations=args.max_iterations, max_minutes=args.max_minutes, out=out, ask=ask,
        auto_apply=args.apply, show_diff=not args.no_diff, style=make_style(), references=refs,
        thinking=args.thinking, skills=args.skill, images=images,
        conversation={"id": args.session} if getattr(args, "session", None) else None,
        knowledge_project=getattr(args, "knowledge", None),
    )  # fmt: skip


def cmd_apply(args, out=print, ask=input) -> int:
    s, client = make_style(), client_from(args)
    try:
        preview = client.apply(args.task_id, dry_run=True)
    except ForgeError as e:
        out(s.red(e.message))
        return EXIT_SETUP if e.unreachable else EXIT_FAILED
    out(s.bold(f"would change {len(preview['files'])} file(s):"))
    for f in preview["files"]:
        out(f"  {f['status']} {f['path']}")
    if not preview["verified"]:
        out(s.yellow("this result was NOT independently verified."))
    if not preview["files"]:
        return EXIT_OK
    if not (args.yes or _yes(_safe_ask(ask, "Apply to your repository? [y/N] "))):
        out("nothing applied.")
        return EXIT_OK
    msg = apply_and_report(client, args.task_id, out=out, s=s)
    out(msg)
    return EXIT_OK if "applied" in msg and "not applied" not in msg else EXIT_FAILED


def cmd_doctor(args, out=print, **_) -> int:
    s, client = make_style(), client_from(args)
    try:
        h, cfg = client.health(), client.config()
    except ForgeError as e:
        out(s.red(e.message))
        out("Start it with:  harnix serve")
        return EXIT_SETUP
    ok = h["provider"]["configured"] and h["sandbox"]["available"]
    out(f"backend      {args.url} (v{h['version']})")
    out(
        f"model        {h['provider']['name']} {h['provider']['model']}"
        + ("" if h["provider"]["configured"] else s.red(f"  NOT CONFIGURED: {h['provider']['problem']}"))
    )
    out(
        f"sandbox      {h['execution_mode']}: "
        + (
            h["sandbox"]["detail"]
            if h["sandbox"]["available"]
            else s.red("unavailable: " + h["sandbox"]["detail"])
        )
    )
    g = h.get("graph") or {}
    out(
        "graph        "
        + (
            (
                "neo4j connected"
                if g.get("available")
                else s.yellow(f"enabled but unavailable: {g.get('detail')}")
            )
            if g.get("enabled")
            else "disabled (FORGE_GRAPH_ENABLED=false)"
        )
    )
    out(f"approvals    {cfg['approval_policy']}")
    out(
        "repo roots   "
        + (", ".join(cfg["allowed_repo_roots"]) or s.red("none: start with  harnix serve --allow <dir>"))
    )
    if h.get("spend"):
        sp = h["spend"]
        out(f"spend        ${sp['spent_usd']:.4f} (measured, not limited)")
    return EXIT_OK if ok and cfg["allowed_repo_roots"] else EXIT_SETUP


def cmd_tasks(args, out=print, **_) -> int:
    client = client_from(args)
    try:
        items = client.list_tasks(15)["items"]
    except ForgeError as e:
        out(e.message)
        return EXIT_SETUP
    for t in items:
        out(f"{t['id']}  {t['status']:17} {str(t['outcome'] or '-'):20} {t['title'][:60]}")
    return EXIT_OK


def cmd_ps(args, out=print, **_) -> int:
    """harnix ps: background processes the agent started on this machine (FORGE_TERMINAL=host)."""
    try:
        items = client_from(args).processes()["items"]
    except ForgeError as e:
        out(e.message)
        return EXIT_SETUP
    if not items:
        out("No background processes.")
    for p in items:
        state = "running" if p["running"] else f"exited {p['exit_code']}"
        out(f"{p['id']:5} {state:10} pid {p['pid']:<7} {p['command'][:70]}   ({p['cwd']})")
    return EXIT_OK


def cmd_stop(args, out=print, **_) -> int:
    """harnix stop ID: stop a background process (see harnix ps)."""
    try:
        client_from(args).stop_process(args.process_id)
    except ForgeError as e:
        out(e.message)
        return EXIT_SETUP
    out(f"Stopped {args.process_id}.")
    return EXIT_OK


def cmd_knowledge(args, out=print, **_) -> int:
    """harnix knowledge ingest|search|bundle|edges|promote|reject|metrics: the project knowledge base (docs/knowledge.md)."""
    client = client_from(args)
    p = args.project
    try:
        if args.action == "ingest":
            src, path = args.value[0], str(Path(args.value[1]).resolve())
            r = client.knowledge(
                "POST", "/ingest", {"project": p, "source": src.replace("-", "_"), "path": path}
            )
            out(f"{r['created']} new, {r['updated']} updated, {r['unchanged']} unchanged, {r['edges_created']} relationships ({r['embedding_model']})")  # fmt: skip
            for k in ("partial", "failed", "skipped", "unresolved"):
                for x in r[k]:
                    out(f"  {k}: {x}")
        elif args.action == "search":
            q = urllib.parse.quote(" ".join(args.value))
            for h in client.knowledge("GET", f"/search?project={p}&q={q}")["hits"]:
                out(
                    f"{h['external_id']:18} {h['kind']:11} v{h['version']}  {h['score']:.4f}  {h['title'][:60]}"
                )
        elif args.action == "bundle":
            b = client.knowledge("POST", "/bundles", {"project": p, "instructions": " ".join(args.value)})
            out(f"bundle {b['bundle_id']}  {b['status']}")
            for slot, items in b["slots"].items():
                for i in items:
                    out(f"  {slot:13} {i['external_id']} v{i['version']}  {i['title'][:60]}")
            for m in b["sufficiency"]["missing"] + b["unresolved_questions"]:
                out(f"  ! {m}")
            if args.kiro:
                files = client.knowledge("GET", f"/bundles/{b['bundle_id']}?format=kiro")["files"]
                for rel, text in files.items():
                    dest = Path(args.kiro) / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(text, encoding="utf-8", newline="\n")
                    out(f"  wrote {dest}")
        elif args.action == "edges":
            for e in client.knowledge("GET", f"/edges?project={p}&status={args.status}")["edges"]:
                out(
                    f"{e['edge_id']}  {e['status']:9} {e['from']} -{e['type']}-> {e['to']}  ({e['origin']}: {e['evidence'][:70]})"
                )
        elif args.action in ("promote", "reject"):
            r = client.knowledge("POST", f"/edges/{args.value[0]}/{args.action}")
            out(f"{r['edge_id']}: {r['status']}")
        elif args.action == "eval":
            cases = json.loads(Path(args.value[0]).read_text(encoding="utf-8"))
            cases = cases["cases"] if isinstance(cases, dict) else cases
            body = {
                "project": p,
                "cases": cases,
                **({"keyword_weight": args.keyword_weight} if args.keyword_weight is not None else {}),
                **({"bundles": True} if args.bundles else {}),
            }
            r = client.knowledge("POST", "/eval", body)
            out(
                f"{r['cases']} questions, embeddings: {r['embedding_model']}, keyword weight {r['keyword_weight']} (vector 1.0)"
            )
            for mode, m in r["modes"].items():
                out(
                    f"  {mode:8} hit@1 {m['hit@1']:.2f}  hit@3 {m['hit@3']:.2f}  hit@5 {m['hit@5']:.2f}  mrr {m['mrr']:.3f}"
                )
                for x in m["misses"]:
                    out(f"           missed: {x['query'][:60]}  expected {x['expect'][:3]}  got {x['top3']}")
            if "bundles" in r:
                b = r["bundles"]
                out(f"  bundles  target in bundle {b['target_in_bundle']:.2f}  mrr {b['mrr']:.3f}  rules included {b['rules_included']}  same backlog {b['same_backlog']}  {b['seconds_per_bundle']}s each")  # fmt: skip
                for x in b["misses"]:
                    out(f"           missed: {x['query'][:60]}  expected {x['expect']}  got {x['got']}")
        elif args.action == "rebuild":
            job = client.knowledge("POST", f"/rebuild?project={urllib.parse.quote(p)}", None)
            out(f"re-chunking and re-embedding (job {job['id']}) ...")
            while job["status"] == "running":
                time.sleep(2)
                job = client.knowledge("GET", f"/jobs/{job['id']}")
            if job["status"] == "failed":
                out(f"failed: {job['error']}")
                return EXIT_FAILED
            r = job["report"]
            out(
                f"{r['sources']} sources, {r['chunks']} chunks re-embedded; {r['partial']} could not be embedded (run again to retry)"
            )
        elif args.action == "extract":
            job = client.knowledge("POST", "/extract", {"project": p})
            out(f"reading the documents with the model (job {job['id']}) ...")
            while job["status"] == "running":
                time.sleep(2)
                job = client.knowledge("GET", f"/extract/{job['id']}")
            if job["status"] == "failed":
                out(f"failed: {job['error']}")
                return EXIT_FAILED
            r = job["report"]
            out(f"read {r['sources']} documents ({r['skipped_unchanged']} unchanged, skipped): {r['entities']} concepts and rules, {r['relations']} relationships, {r['mentions']} new mentions links; {r['rejected_entities'] + r['rejected_relations']} proposals dropped (quote not in the document)")  # fmt: skip
            for f in r["failed"][:10]:
                out(f"  failed: {f}")
        elif args.action == "link":
            job = client.knowledge("POST", "/link", {"project": p})
            out(f"reading the documents for links between them (job {job['id']}) ...")
            while job["status"] == "running":
                time.sleep(2)
                job = client.knowledge("GET", f"/link/{job['id']}")
            if job["status"] == "failed":
                out(f"failed: {job['error']}")
                return EXIT_FAILED
            r = job["report"]
            out(f"read {r['sources']} documents ({r['skipped_unchanged']} unchanged, skipped): {r['proposed']} links proposed, {r['already_linked']} already linked; dropped: {r['rejected'] or 'none'}")  # fmt: skip
            for f in r["failed"][:10]:
                out(f"  failed: {f}")
        elif args.action == "export":
            if not args.value:
                raise ValueError("give the folder to write to: harnix knowledge export OUT_DIR --project P")
            r = client.knowledge(
                "POST", "/export", {"project": p, "path": str(Path(args.value[0]).resolve())}
            )
            out(
                f"{r['files']} files in {r['folder']}: {r['written']} written, {r['unchanged']} unchanged, {r['removed']} removed"
            )
        elif args.action == "findings":
            r = client.knowledge("GET", f"/findings?project={urllib.parse.quote(p)}")
            out(f"{r['count']} problems in the documents {r['by_kind'] or ''}")
            for f in r["findings"][:50]:
                out(f"  {f['kind']:22} {f['message'][:140]}")
        elif args.action == "coverage":
            folder = f"&path={urllib.parse.quote(str(Path(args.value[0]).resolve()))}" if args.value else ""
            r = client.knowledge("GET", f"/coverage?project={urllib.parse.quote(p)}{folder}")
            if "inputs" in r:
                i = r["inputs"]
                out(f"input files: {i['mapped']} of {i['datapoints']} datapoints found in the knowledge base ({(i['score'] or 0) * 100:.1f}%), {i['missing_total']} missing")  # fmt: skip
                for m in i["missing"][:20]:
                    out(f"  missing  {m['file']} {m['where']}: {m['text'][:80]}")
            c, o = r["chunks"], r["orphans"]
            out(f"chunks: {c['uncovered']} of {c['lines']} stored lines not in any chunk; {o['count']} sources with no link at all {o['by_kind'] or ''}")  # fmt: skip
            for e in c["examples"][:10]:
                out(f"  not chunked  {e['source']}: {e['text'][:80]}")
        elif args.action == "metrics":
            out(json.dumps(client.knowledge("GET", f"/metrics?project={p}"), indent=2))
    except (ForgeError, IndexError, OSError, ValueError, KeyError) as e:
        out(e.message if isinstance(e, ForgeError) else f"Cannot do that: {type(e).__name__}: {e}")
        return EXIT_SETUP
    return EXIT_OK


def cmd_graph(args, out=print, **_) -> int:
    """forge graph status|index|drop [PATH]: manage the knowledge graph of a repository (default: this directory)."""
    s, client = make_style(), client_from(args)
    repo = str(Path(args.path).resolve())
    try:
        if args.action == "status":
            st = client.graph_status(repo)
            out(f"neo4j        {'available' if st['available'] else s.red('unavailable')}: {st['detail']}")
            if not st["available"]:
                return EXIT_FAILED
            g = st.get("graph")
            if not st.get("indexed") or not g:
                out(f"repository   {repo}: not indexed yet (forge graph index)")
            else:
                out(
                    f"repository   {repo}: {g['files']} files, {g['symbols']} symbols, {g['imports']} imports, "
                    f"{g['calls']} calls; version {g['version']}; "
                    + ("complete" if g["complete"] else s.yellow("INCOMPLETE: run forge graph index"))
                )
            return EXIT_OK
        if args.action == "drop":
            out(f"deleted {client.graph_drop(repo)['deleted_nodes']} graph node(s) for {repo}")
            return EXIT_OK
        job = client.graph_index(repo)
        out(f"indexing {repo} (the backend works in the background; Ctrl+C stops watching, not the job)")
        last = ""
        while job["state"] == "running":
            time.sleep(1.0)
            job = client.graph_status(repo).get("job") or job
            line = f"  {job.get('stage')}: {job.get('progress')}"
            if line != last:
                out(s.dim(line))
                last = line
        if job["state"] == "failed":
            out(s.red(f"indexing failed: {job.get('error')}"))
            return EXIT_FAILED
        r = job["report"]
        out(
            f"done in {r['seconds']}s: {r['files']} files, {r['symbols']} symbols; {r['files_parsed']} (re)parsed, "
            f"{r['files_unchanged']} unchanged, {r['files_removed']} removed; "
            + ("complete" if r["complete"] else s.yellow("partial: run it again to continue"))
        )
        return EXIT_OK if r["complete"] else EXIT_FAILED
    except ForgeError as e:
        out(s.red(e.message))
        return EXIT_SETUP if e.status in (0, 503) else EXIT_FAILED


def cmd_skills(args, out=print, **_) -> int:
    s, client = make_style(), client_from(args)
    try:
        data = client.skills()
    except ForgeError as e:
        out(s.red(e.message))
        return EXIT_SETUP if e.unreachable else EXIT_FAILED
    if not data["items"]:
        out("no skills configured (put <id>/SKILL.md folders in a directory listed in FORGE_SKILLS_DIRS)")
    for k in data["items"]:
        flag = "" if k["listed"] else s.dim(" (only when a task names it: --skill)")
        out(f"{s.bold(k['id'])}  {k['description'] or s.dim('(no description)')}{flag}")
    for p in data["problems"]:
        out(s.yellow(f"skipped: {p}"))
    if data["repo_skills"]:
        out(
            s.dim(
                "skills inside the target repository (.claude/skills, .opencode/skills) are also loaded, as untrusted"
            )
        )
    return EXIT_OK


def cmd_agents(args, out=print, **_) -> int:
    s, client = make_style(), client_from(args)
    try:
        data = client.agents()
    except ForgeError as e:
        out(s.red(e.message))
        return EXIT_SETUP if e.unreachable else EXIT_FAILED
    for a in data["items"]:
        tools = "all task tools" if a["tools"] is None else ", ".join(a["tools"])
        out(f"{s.bold(a['id'])} [{a['source']}] {a['description']}")
        out(s.dim(f"    tools: {tools}; thinking: {a['thinking'] or 'task level'}"))
    for p in data["problems"]:
        out(s.yellow(f"skipped: {p}"))
    return EXIT_OK


def cmd_memory(args, out=print, **_) -> int:
    s, client = make_style(), client_from(args)
    repo = str(Path(args.repo).resolve())
    try:
        if args.action == "list":
            notes = client.memory(repo)
            if not notes:
                out("no project memory for this repository yet")
            for n in notes:
                out(f"{s.dim(n['id'])}  [{n['source']}] {n['content']}")
            return EXIT_OK
        if args.action == "add":
            text = " ".join(args.value).strip()
            if not text:
                out('give the note:  harnix memory add "tests need the DB container running"')
                return EXIT_USAGE
            out(f"saved {client.add_memory(repo, text)['id']}")
            return EXIT_OK
        if not args.value:
            out("give the note id (see harnix memory list)")
            return EXIT_USAGE
        client.delete_memory(repo, args.value[0])
        out(f"deleted {args.value[0]}")
        return EXIT_OK
    except ForgeError as e:
        out(s.red(e.message))
        return EXIT_SETUP if e.unreachable else (EXIT_USAGE if e.status == 422 else EXIT_FAILED)


def migrate_database(out=print) -> None:
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini.exists():
        out(
            "alembic.ini not found next to the package; run `alembic upgrade head` yourself if the database is new."
        )
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "migrations"))
    command.upgrade(cfg, "head")


def combined_roots(existing: list[Path], allow: list[str]) -> list[str]:
    roots = [str(p) for p in existing]
    for d in allow:
        p = d if d == "*" else str(Path(d).resolve())
        if p not in roots:
            roots.append(p)
    return roots


def cmd_serve(args, out=print, **_) -> int:
    from harnix.config import ConfigError, Settings

    try:
        settings = Settings()
    except Exception as e:
        out(f"Forge configuration error: {e}")
        return EXIT_SETUP
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not settings.api_token and not args.insecure:
        out(
            "Refusing to listen on a non-local address without FORGE_API_TOKEN (anyone could run code). Set a token, or pass --insecure."
        )
        return EXIT_USAGE
    if args.allow:
        os.environ["FORGE_ALLOWED_REPO_ROOTS"] = os.pathsep.join(
            combined_roots(settings.repo_roots, args.allow)
        )
        settings = Settings()
    if not args.no_migrate:
        try:
            migrate_database(out)
        except Exception as e:
            out(f"Database migration failed: {e}")
            return EXIT_SETUP
    out(
        f"Forge backend: http://{args.host}:{args.port}  model: {settings.provider}/{settings.model_name}  sandbox: {settings.execution_mode}"
    )
    out(
        "authorized repository roots: "
        + (", ".join(str(p) for p in settings.repo_roots) or "NONE (pass --allow <dir>)")
    )
    import uvicorn

    try:
        uvicorn.run(
            "forge.api.app:app_factory",
            factory=True,
            host=args.host,
            port=args.port,
            log_level=settings.log_level.lower(),
        )
    except ConfigError as e:
        out(f"Forge configuration error: {e}")
        return EXIT_SETUP
    return EXIT_OK


def interactive(args, out=print, ask=input) -> int:
    s = make_style()
    repo = Path(args.repo).resolve()
    client = client_from(args)
    out(
        s.bold("Forge")
        + s.dim(
            f"  repository: {repo}   (each prompt is a separate task on an isolated copy; /help for commands)"
        )
    )
    try:
        interactive_images = load_images(args.image)  # attached to the first task of this session
    except ValueError as e:
        out(str(e))
        return EXIT_USAGE
    conversation: dict = {
        "id": getattr(args, "session", None)
    }  # every prompt continues it; /new starts another
    last = EXIT_OK
    while True:
        try:
            line = ask(s.bold("forge> ")).strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return last
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            return last
        if line == "/help":
            out(
                "type a message and press Enter (the agent remembers the conversation) · /new starts a fresh conversation · /doctor · /tasks · /exit · Ctrl+C cancels a running task"
            )
        elif line == "/new":
            conversation["id"] = None
            out(s.dim("new conversation: the agent starts from nothing"))
        elif line == "/doctor":
            cmd_doctor(args, out=out)
        elif line == "/tasks":
            cmd_tasks(args, out=out)
        else:
            line, refs = extract_references(line, repo)
            last = run_task(
                client,
                repo=repo,
                references=refs,
                thinking=args.thinking,
                skills=args.skill,
                images=interactive_images,
                conversation=conversation,
                prompt=line,
                criteria=args.criteria,
                verify=args.verify,
                max_iterations=args.max_iterations,
                max_minutes=args.max_minutes,
                out=out,
                ask=ask,
                auto_apply=args.apply,
                show_diff=not args.no_diff,
                style=s,
            )
            interactive_images = []  # only the first task of the session gets the --image files


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--url", default=os.environ.get("FORGE_URL", DEFAULT_URL), help="backend URL (env FORGE_URL)"
    )
    run_opts = argparse.ArgumentParser(add_help=False)
    run_opts.add_argument("--repo", default=".", help="repository directory (default: current directory)")
    run_opts.add_argument(
        "-v",
        "--verify",
        action="append",
        default=[],
        metavar="CMD",
        help="verification command Forge runs itself (repeatable)",
    )
    run_opts.add_argument(
        "-c",
        "--criteria",
        action="append",
        default=[],
        metavar="TEXT",
        help="free-text acceptance criterion (repeatable; reported as needing human review)",
    )
    run_opts.add_argument(
        "--thinking",
        choices=["low", "medium", "high", "xhigh"],
        help="reasoning depth (xhigh = extra high); default: the backend's FORGE_THINKING_LEVEL",
    )
    run_opts.add_argument(
        "--knowledge",
        metavar="PROJECT",
        help="give the agent this knowledge project (BRDs, requirements, tickets, design, code, tests) as a context bundle",
    )
    run_opts.add_argument(
        "--session",
        metavar="ID",
        help="continue this conversation (the id is printed at the start of every task): the agent remembers it",
    )
    run_opts.add_argument(
        "--image",
        action="append",
        default=[],
        metavar="FILE",
        help="attach an image (PNG, JPEG, GIF or WebP) for the model to look at (repeatable)",
    )
    run_opts.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="ID",
        help="load this skill up front (repeatable; see forge skills)",
    )
    run_opts.add_argument("--max-iterations", type=int)
    run_opts.add_argument("--max-minutes", type=int)
    run_opts.add_argument(
        "--apply", action="store_true", help="apply automatically, but only if the result is verified"
    )
    run_opts.add_argument("--no-diff", action="store_true", help="do not print the diff")

    p = argparse.ArgumentParser(
        prog="forge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common, run_opts],
    )
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", parents=[common, run_opts], help="run one task")
    r.add_argument("prompt", nargs="*")
    a = sub.add_parser("apply", parents=[common], help="apply a finished task's changes to its repository")
    a.add_argument("task_id")
    a.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    sub.add_parser("doctor", parents=[common], help="check the backend, model, sandbox and spend")
    sub.add_parser("tasks", parents=[common], help="list recent tasks")
    kn = sub.add_parser("knowledge", parents=[common], help="project knowledge base: ingest | search | bundle | edges | promote | reject | metrics | eval | extract | link | coverage | findings | export | rebuild")  # fmt: skip
    kn.add_argument(
        "action",
        choices=[
            "ingest",
            "search",
            "bundle",
            "edges",
            "promote",
            "reject",
            "metrics",
            "eval",
            "extract",
            "link",
            "coverage",
            "findings",
            "export",
            "rebuild",
        ],
    )
    kn.add_argument("value", nargs="*", help="ingest: documents|repository|jira-mock PATH; coverage: input folder; export: output folder; search/bundle: text; promote/reject: edge id")  # fmt: skip
    kn.add_argument("--project", required=True, help="knowledge project name")
    kn.add_argument("--status", default="proposed", choices=["proposed", "verified", "rejected"], help="edges: which relationships")  # fmt: skip
    kn.add_argument(
        "--keyword-weight",
        type=float,
        help="eval: try this keyword weight (vector = 1.0); default the server's FORGE_KNOWLEDGE_KEYWORD_WEIGHT",
    )
    kn.add_argument("--bundles", action="store_true", help="eval: also measure the context bundles (slower)")
    kn.add_argument("--kiro", metavar="DIR", help="bundle: also write Kiro steering/spec/MCP files under DIR")
    sub.add_parser(
        "ps", parents=[common], help="list background processes the agent started (FORGE_TERMINAL=host)"
    )
    stp = sub.add_parser("stop", parents=[common], help="stop a background process (see harnix ps)")
    stp.add_argument("process_id")
    sub.add_parser("skills", parents=[common], help="list the skills the agent can load")
    sub.add_parser("agents", parents=[common], help="list the sub-agents the agent can start")
    mem = sub.add_parser("memory", parents=[common], help="project memory: list | add TEXT | rm ID")
    mem.add_argument("action", choices=["list", "add", "rm"])
    mem.add_argument("value", nargs="*", help="the note (add) or the note id (rm)")
    mem.add_argument("--repo", default=".", help="repository directory (default: current directory)")
    g = sub.add_parser("graph", parents=[common], help="knowledge graph: status | index | drop")
    g.add_argument("action", choices=["status", "index", "drop"])
    g.add_argument("path", nargs="?", default=".", help="repository (default: current directory)")
    sv = sub.add_parser("serve", parents=[common], help="start the backend")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="DIR",
        help="authorize DIR (and everything below it) for this server; repeatable",
    )
    sv.add_argument("--no-migrate", action="store_true")
    sv.add_argument("--insecure", action="store_true")
    return p


VALUE_OPTIONS = {
    "--knowledge",
    "--url",
    "--repo",
    "-v",
    "--verify",
    "-c",
    "--criteria",
    "--thinking",
    "--skill",
    "--image",
    "--session",
    "--max-iterations",
    "--max-minutes",
}


def _has_bare_prompt(argv: list[str]) -> bool:
    """True when the first positional word is not a subcommand (a task prompt, possibly after options)."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            i += 2 if tok in VALUE_OPTIONS else 1  # `--opt=value` forms are a single token
            continue
        return tok not in SUBCOMMANDS
    return False  # only options: the interactive session


def main(argv: list[str] | None = None, out=print, ask=input) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _has_bare_prompt(argv):
        argv.insert(0, "run")  # `forge [options] "fix the bug"` == `forge run [options] "fix the bug"`
    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # a cp1252 console or pipe must not crash on the arrows and bullets
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # pyright: ignore[reportAttributeAccessIssue]
    args = build_parser().parse_args(argv)
    handler = {
        "serve": cmd_serve,
        "run": cmd_run,
        "apply": cmd_apply,
        "doctor": cmd_doctor,
        "tasks": cmd_tasks,
        "ps": cmd_ps,
        "knowledge": cmd_knowledge,
        "stop": cmd_stop,
        "graph": cmd_graph,
        "skills": cmd_skills,
        "agents": cmd_agents,
        "memory": cmd_memory,
    }.get(args.cmd)
    if handler:
        return handler(args, out=out, ask=ask)
    return interactive(args, out=out, ask=ask)


if __name__ == "__main__":
    raise SystemExit(main())
