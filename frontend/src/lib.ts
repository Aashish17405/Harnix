import type { HarnixEvent } from "./types";

export interface ToolCallRow {
  callId: string;
  tool: string;
  args: Record<string, unknown>;
  /** proposed -> (denied | awaiting approval | rejected) -> executed | failed */
  state: "proposed" | "denied" | "awaiting_approval" | "executed" | "failed";
  decision?: string;
  reason?: string;
  error?: string | null;
  output?: unknown;
  durationSeconds?: number;
  startedAt: string;
}

/** Fold tool.proposed / tool.authorization / approval.* / tool.result events into one row per call. */
export function deriveToolCalls(events: HarnixEvent[]): ToolCallRow[] {
  const rows = new Map<string, ToolCallRow>();
  let lastApprovalCall: string | null = null;
  for (const e of events) {
    const d = e.data;
    if (e.type === "tool.proposed") {
      rows.set(d.call_id, {
        callId: d.call_id,
        tool: d.tool,
        args: d.arguments ?? {},
        state: "proposed",
        startedAt: e.ts,
      });
      lastApprovalCall = d.call_id;
    } else if (e.type === "tool.authorization") {
      const r = rows.get(d.call_id);
      if (r) {
        r.decision = d.decision;
        r.reason = d.reason;
        if (d.decision === "approve") r.state = "awaiting_approval";
        if (d.decision === "deny") r.state = "denied";
      }
    } else if (e.type === "approval.decided") {
      const r = lastApprovalCall ? rows.get(lastApprovalCall) : undefined;
      if (r && d.approved) r.state = "proposed"; // approved: now executing
    } else if (e.type === "tool.result") {
      const r = rows.get(d.call_id);
      if (r) {
        r.error = d.error;
        r.output = d.data;
        r.durationSeconds = d.duration_seconds;
        const refused =
          typeof d.error === "string" && /^(Denied|Not approved)/.test(d.error);
        r.state = refused ? "denied" : d.ok ? "executed" : "failed";
      }
    }
  }
  return [...rows.values()];
}

export interface DiffFile {
  path: string;
  lines: { kind: "add" | "del" | "hunk" | "meta" | "ctx"; text: string }[];
}

/** Split a unified git diff into per-file blocks with line kinds for rendering. */
export function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const m = /^diff --git a\/(.+?) b\/(.+)$/.exec(line);
      cur = { path: m ? m[2] : line.slice(11), lines: [] };
      files.push(cur);
      continue;
    }
    if (!cur) continue;
    let kind: DiffFile["lines"][number]["kind"] = "ctx";
    if (line.startsWith("@@")) kind = "hunk";
    else if (
      /^(index |--- |\+\+\+ |new file|deleted file|similarity|rename|old mode|new mode|Binary)/.test(
        line,
      )
    )
      kind = "meta";
    else if (line.startsWith("+")) kind = "add";
    else if (line.startsWith("-")) kind = "del";
    cur.lines.push({ kind, text: line });
  }
  return files;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}

/** " · thinking high", or the downgrade and its reason when the provider could not honour the level. */
export function thinkingNote(
  t: { requested?: string; applied?: string; reason?: string } | undefined,
): string {
  if (!t?.requested) return "";
  if (t.applied && t.applied !== t.requested)
    return ` · thinking ${t.requested} → ${t.applied}${t.reason ? ` (${t.reason})` : ""}`;
  return ` · thinking ${t.requested}`;
}

export function describeEvent(e: HarnixEvent): {
  label: string;
  detail: string;
  category: string;
} {
  const d = e.data;
  switch (e.type) {
    case "task.created":
      return {
        label: "Task created",
        detail: d.repo_path ?? "",
        category: "state",
      };
    case "task.state":
      return {
        label: `Status: ${d.from} → ${d.to}`,
        detail: d.error ?? "",
        category: "state",
      };
    case "task.cancel_requested":
      return {
        label: "Cancellation requested",
        detail: "",
        category: "policy",
      };
    case "task.finished":
      return {
        label: `Finished: ${d.status}`,
        detail: d.outcome ? `outcome ${d.outcome}` : (d.reason ?? ""),
        category: "state",
      };
    case "workspace.created":
      return {
        label: "Isolated workspace created",
        detail: `${d.execution_mode} execution`,
        category: "state",
      };
    case "project.inspected":
      return {
        label: "Repository inspected",
        detail: `${(d.types ?? []).join(", ") || "unknown type"}; test: ${d.commands?.test ?? "none detected"}`,
        category: "state",
      };
    case "task.references": {
      const inc = (d.included ?? []) as { path: string }[];
      const skipped = (d.skipped ?? []) as { path: string; reason: string }[];
      return {
        label: inc.length
          ? `Attached ${inc.length} file(s)`
          : "No referenced file could be attached",
        detail: [
          ...inc.map((i) => i.path),
          ...skipped.map((s) => `skipped ${s.path}: ${s.reason}`),
        ].join("; "),
        category: skipped.length ? "policy" : "state",
      };
    }
    case "graph.indexed":
      if (d.skipped)
        return {
          label: "Knowledge graph not re-indexed",
          detail: d.skipped,
          category: "policy",
        };
      return {
        label: d.complete
          ? "Knowledge graph indexed"
          : "Knowledge graph partially indexed",
        detail: `${d.files} files, ${d.symbols} symbols · ${d.files_parsed} re-indexed, ${d.files_unchanged} unchanged · ${d.seconds}s${d.complete ? "" : " (the rest continues on the next run)"}`,
        category: d.complete ? "state" : "policy",
      };
    case "graph.unavailable":
      return {
        label: "Knowledge graph unavailable: continuing without it",
        detail: d.reason ?? "",
        category: "policy",
      };
    case "graph.context":
      return {
        label: "Knowledge graph context added",
        detail: `${d.chars} chars · ${d.seed_files} focus file(s), ${d.seed_tests} failing test(s), ${d.seed_symbols} symbol(s)`,
        category: "state",
      };
    case "skills.available": {
      const listed = (d.skills ?? [])
        .filter((k: { listed: boolean }) => k.listed)
        .map((k: { id: string }) => k.id);
      return {
        label: `Skills available: ${listed.length}`,
        detail: [listed.join(", "), ...(d.problems ?? [])]
          .filter(Boolean)
          .join("; "),
        category: "state",
      };
    }
    case "skill.loaded":
      return {
        label: `Skill loaded: ${d.id}`,
        detail: `${d.how} · ${d.source}`,
        category: "state",
      };
    case "skill.missing":
      return {
        label: `Skill not found: ${d.id}`,
        detail: d.reason ?? "",
        category: "policy",
      };
    case "memory.context":
      return {
        label: "Project memory in context",
        detail: `${d.shown} of ${d.notes} note(s)`,
        category: "state",
      };
    case "memory.saved":
      return {
        label: "Project memory note saved",
        detail: `${d.chars} characters`,
        category: "executed",
      };
    case "subagent.started":
      return {
        label: `Sub-agent started: ${d.agent}`,
        detail: `${d.description} · thinking ${d.thinking} · max ${d.max_steps} steps · tools: ${(d.tools ?? []).join(", ")}`,
        category: "proposed",
      };
    case "subagent.finished":
      return {
        label: `Sub-agent finished: ${d.agent}`,
        detail: `${d.reason} after ${d.steps} step(s)${d.error ? ` · ${d.error}` : ""}`,
        category: d.reason === "done" ? "executed" : "policy",
      };
    case "subagent.delivered":
      return {
        label: "Background report(s) delivered",
        detail: (d.agents ?? []).join(", "),
        category: "state",
      };
    case "subagent.waiting":
      return {
        label: "Waiting for background sub-agent(s)",
        detail: `${d.running} still running; the final answer is held`,
        category: "state",
      };
    case "subagent.stopped":
      return {
        label: "Background sub-agent(s) stopped",
        detail: d.reason ?? "",
        category: "policy",
      };
    case "context.pruned":
      return {
        label: "Context pruned",
        detail: `${d.results_pruned} old tool output(s), ~${d.tokens_reclaimed} tokens reclaimed (~${d.tokens_before} → ~${d.tokens_after})`,
        category: "state",
      };
    case "context.compacted":
      return {
        label: `Context compacted (${d.reason})`,
        detail: `~${d.tokens_before} → ~${d.tokens_after} tokens · ${d.summarized_messages} message(s) summarized`,
        category: "state",
      };
    case "context.compaction_failed":
      return {
        label: "Context compaction failed",
        detail: `${d.reason}: ${d.error}`,
        category: "policy",
      };
    case "context.compaction_deferred":
      return {
        label: "Context compaction deferred",
        detail: d.reason ?? "",
        category: "state",
      };
    case "agents.problems":
      return {
        label: "Custom agents skipped",
        detail: (d.problems ?? []).join("; "),
        category: "policy",
      };
    case "model.request":
      return {
        label: `Model request #${d.iteration}`,
        detail: `${d.messages} messages`,
        category: "model",
      };
    case "model.response":
      return {
        label: `${d.agent ? `[${d.agent}] ` : ""}Model response #${d.iteration}`,
        detail: `${d.text ? d.text.slice(0, 160) : "(no text)"}${d.tool_calls?.length ? ` · proposes ${d.tool_calls.map((c: { name: string }) => c.name).join(", ")}` : ""}${thinkingNote(d.thinking)}`,
        category: "model",
      };
    case "tool.proposed":
      return {
        label: `${d.agent ? `[${d.agent}] ` : ""}Proposed: ${d.tool}`,
        detail: JSON.stringify(d.arguments).slice(0, 200),
        category: "proposed",
      };
    case "tool.authorization":
      return {
        label: `Policy: ${d.decision} ${d.tool}`,
        detail: d.reason,
        category: d.decision === "deny" ? "error" : "policy",
      };
    case "tool.result":
      return {
        label: `${d.ok ? "Executed" : "Failed"}: ${d.tool}`,
        detail:
          d.error ??
          (d.data?.exit_code != null ? `exit ${d.data.exit_code}` : ""),
        category: d.ok ? "executed" : "error",
      };
    case "task.applied":
      return {
        label: "Changes applied to your repository",
        detail: (d.files ?? []).map((f: { path: string }) => f.path).join(", "),
        category: "executed",
      };
    case "approval.requested":
      return {
        label: `Approval requested: ${d.tool}`,
        detail: d.reason,
        category: "policy",
      };
    case "approval.decided":
      return {
        label: d.approved ? "Approved by human" : "Not approved",
        detail: d.reason,
        category: d.approved ? "executed" : "error",
      };
    case "verification.check":
      return {
        label: `Check ${d.status}: ${d.kind}`,
        detail: `${d.phase}${d.attempt ? ` #${d.attempt}` : ""} · ${d.command}${d.note ? ` (${d.note})` : ""}`,
        category:
          d.status === "passed"
            ? "verified"
            : d.status === "failed"
              ? "error"
              : "policy",
      };
    case "verification.result":
      return {
        label: `Verification verdict: ${d.outcome}`,
        detail: (d.reasons ?? []).join("; "),
        category:
          d.outcome === "verified"
            ? "verified"
            : d.outcome === "verification_failed"
              ? "error"
              : "policy",
      };
    case "retry.decision":
      return {
        label: `Retry: ${d.kind}`,
        detail: d.error ?? (d.reasons ?? []).join("; "),
        category: "policy",
      };
    case "error":
      return {
        label: `Error (${d.where})`,
        detail: d.message ?? "",
        category: "error",
      };
    default:
      return { label: e.type, detail: "", category: "state" };
  }
}

// ---- knowledge graph layout ---------------------------------------------------------------------------------
/** The AIDLC order, left to right: what the business wants, then what is built, then how it is checked. */
export const KIND_ORDER = [
  "brd", "doc", "requirement", "epic", "feature", "story", "ticket", "design", "code", "test",
  "actor", "concept", "field", "event", "system", "rule", "constraint", // the content graph: what the documents say
] as const; // prettier-ignore
export const NODE_W = 196;
export const NODE_H = 36;
const COL_GAP = 96;
const ROW_GAP = 12;

export interface Placed<N> {
  node: N;
  x: number;
  y: number;
  column: number;
}

/** Nodes in one column per kind that is present, ordered by name; returns positions and the canvas size. */
export function layoutKinds<N extends { kind: string; external_id: string }>(
  nodes: N[],
): { placed: Placed<N>[]; width: number; height: number; kinds: string[] } {
  const present = [...new Set(nodes.map((n) => n.kind))].sort((a, b) => {
    const ia = KIND_ORDER.indexOf(a as (typeof KIND_ORDER)[number]);
    const ib = KIND_ORDER.indexOf(b as (typeof KIND_ORDER)[number]);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const placed: Placed<N>[] = [];
  let rows = 0;
  present.forEach((kind, column) => {
    const col = nodes
      .filter((n) => n.kind === kind)
      .sort((a, b) =>
        a.external_id.localeCompare(b.external_id, undefined, {
          numeric: true,
        }),
      );
    rows = Math.max(rows, col.length);
    col.forEach((node, i) =>
      placed.push({
        node,
        column,
        x: 16 + column * (NODE_W + COL_GAP),
        y: 40 + i * (NODE_H + ROW_GAP),
      }),
    );
  });
  return {
    placed,
    kinds: present,
    width:
      32 +
      present.length * NODE_W +
      Math.max(present.length - 1, 0) * COL_GAP +
      56, // room for same-column loops
    height: 56 + rows * (NODE_H + ROW_GAP),
  };
}

/** An SVG path from one placed node to another (right side to left side; a loop when they share a column). */
export function edgePath(
  a: { x: number; y: number },
  b: { x: number; y: number },
): string {
  const ay = a.y + NODE_H / 2;
  const by = b.y + NODE_H / 2;
  if (Math.abs(a.x - b.x) < 1) {
    const x = a.x + NODE_W;
    return `M ${x} ${ay} C ${x + 44} ${ay}, ${x + 44} ${by}, ${x} ${by}`;
  }
  if (a.x < b.x) {
    const x1 = a.x + NODE_W;
    const mid = (x1 + b.x) / 2;
    return `M ${x1} ${ay} C ${mid} ${ay}, ${mid} ${by}, ${b.x} ${by}`;
  }
  const x1 = a.x;
  const x2 = b.x + NODE_W;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${ay} C ${mid} ${ay}, ${mid} ${by}, ${x2} ${by}`;
}
