import type { HarnixEvent } from "./types";

/** One step of a task's run, built only from the events the harness recorded (nothing is inferred or invented). */
export interface Stage {
  key:
    "prepare" | "index" | "context" | "agent" | "verify" | "update" | "finish";
  title: string;
  what: string; // one sentence: what this step does
  state: "done" | "warn" | "failed" | "skipped" | "running" | "pending";
  summary: string;
  ms: number | null; // wall time between the step's first and last event
  events: HarnixEvent[];
}

export interface ToolStep {
  callId: string;
  tool: string;
  mcp: boolean;
  args: unknown;
  decision: string | null; // allow / approve / deny
  reason: string | null;
  ok: boolean | null;
  error: string | null;
  seconds: number | null;
  output: unknown;
}

export interface Iteration {
  n: number;
  stop: string | null;
  text: string;
  tokensIn: number | null;
  tokensOut: number | null;
  tools: ToolStep[];
}

const STAGES: Pick<Stage, "key" | "title" | "what">[] = [
  {
    key: "prepare",
    title: "Prepare the workspace",
    what: "Copy the repository into an isolated workspace, detect how to test it, run the checks once before any change (baseline), connect the tool servers.",
  },
  {
    key: "index",
    title: "Index the repository as knowledge",
    what: "Read the repository's code and tests into the knowledge base (unchanged files are skipped) so they can be linked to requirements.",
  },
  {
    key: "context",
    title: "Build the context",
    what: "Exact id lookup, vector + keyword search, graph traversal (Neo4j), rerank, filter, budgets, then the sufficiency check. The result is handed to the model as untrusted data.",
  },
  {
    key: "agent",
    title: "Agent works",
    what: "The model proposes tool calls; the harness authorizes each one (policy), runs it in the sandbox and returns the result. Repeats until the model says it is done.",
  },
  {
    key: "verify",
    title: "Independent verification",
    what: "The harness runs the checks itself (tests, syntax, your commands). The model's claim of success is never used.",
  },
  {
    key: "update",
    title: "Update the knowledge",
    what: "After verified work only: new files become agent-origin knowledge and links (code implements story, test tests story) are proposed for a person to confirm; Neo4j is updated.",
  },
  {
    key: "finish",
    title: "Result",
    what: "Final status and outcome. Nothing is written to your repository until you apply the changes.",
  },
];

function stageOf(
  type: string,
  data: Record<string, unknown>,
): Stage["key"] | null {
  if (type === "verification.check")
    return data.phase === "baseline" ? "prepare" : "verify";
  if (
    [
      "task.created",
      "workspace.created",
      "project.inspected",
      "graph.indexed",
      "graph.context",
      "mcp.servers",
      "skills.loaded",
      "memory.loaded",
    ].includes(type)
  )
    return "prepare";
  if (type === "knowledge.indexed") return "index";
  if (type === "knowledge.bundle") return "context";
  if (type === "knowledge.unavailable")
    return data.phase === "index"
      ? "index"
      : data.phase === "capture"
        ? "update"
        : "context";
  if (
    type.startsWith("model.") ||
    type.startsWith("tool.") ||
    type.startsWith("approval.") ||
    type === "recovery" ||
    type.startsWith("agent.")
  )
    return "agent";
  if (type === "verification.result") return "verify";
  if (type === "knowledge.captured") return "update";
  if (type === "task.finished" || type === "task.applied") return "finish";
  return null;
}

function span(events: HarnixEvent[]): number | null {
  if (events.length < 2) return null;
  return Date.parse(events[events.length - 1].ts) - Date.parse(events[0].ts);
}

/** Group the task's events into the seven steps, with a state and a one-line summary for each. */
export function buildStages(
  events: HarnixEvent[],
  finished: boolean,
  hasKnowledge: boolean,
): Stage[] {
  const by: Record<string, HarnixEvent[]> = {};
  for (const e of events) {
    const k = stageOf(e.type, e.data);
    if (k) (by[k] ??= []).push(e);
  }
  const find = (k: string, type: string) =>
    (by[k] ?? []).find((e) => e.type === type)?.data;
  const out: Stage[] = [];
  for (const s of STAGES) {
    const evs = by[s.key] ?? [];
    let state: Stage["state"] = evs.length
      ? "done"
      : finished
        ? "skipped"
        : "pending";
    let summary = "";
    if (s.key === "prepare") {
      const ws = find("prepare", "workspace.created");
      const pi = find("prepare", "project.inspected");
      const base = evs.filter((e) => e.type === "verification.check");
      const mcp = find("prepare", "mcp.servers");
      summary = [
        ws ? `isolated copy (${ws.execution_mode})` : "",
        pi?.commands?.test ? `tests: ${pi.commands.test}` : "",
        base.length
          ? `baseline: ${base.filter((b) => b.data.status === "passed").length}/${base.length} passed`
          : "",
        mcp?.connected?.length
          ? `tool servers: ${mcp.connected.map((c: { name: string }) => c.name).join(", ")}`
          : "",
      ]
        .filter(Boolean)
        .join(" · ");
    } else if (s.key === "index") {
      const d = find("index", "knowledge.indexed");
      if (!hasKnowledge)
        [state, summary] = ["skipped", "no knowledge project on this task"];
      else if (d)
        summary = `${d.created} new, ${d.updated} updated, ${d.unchanged} unchanged${d.failed?.length ? `, ${d.failed.length} failed` : ""}`;
      if (find("index", "knowledge.unavailable")) state = "warn";
    } else if (s.key === "context") {
      const d = find("context", "knowledge.bundle");
      if (!hasKnowledge)
        [state, summary] = ["skipped", "no knowledge project on this task"];
      else if (d) {
        const items = Object.entries(d.items ?? {})
          .filter(([, n]) => (n as number) > 0)
          .map(([k, n]) => `${n} ${k}`);
        summary = `${d.status} · ${items.join(", ")}${d.missing?.length ? ` · missing: ${d.missing.join("; ")}` : ""}`;
        if (d.status !== "sufficient") state = "failed";
      } else if (find("context", "knowledge.unavailable")) state = "warn";
    } else if (s.key === "agent") {
      const its = iterations(events);
      const tools = its.flatMap((i) => i.tools);
      const mcp = tools.filter((t) => t.mcp).length;
      const failed = tools.filter((t) => t.ok === false).length;
      const denied = tools.filter((t) => t.decision === "deny").length;
      summary = evs.length
        ? `${its.length} model turns · ${tools.length} tool calls (${mcp} knowledge/MCP)${failed ? ` · ${failed} failed` : ""}${denied ? ` · ${denied} denied` : ""}`
        : "";
      if (denied || failed) state = state === "done" ? "warn" : state;
    } else if (s.key === "verify") {
      const r = find("verify", "verification.result");
      const checks = evs.filter((e) => e.type === "verification.check");
      if (r) {
        summary = `${r.outcome} · ${checks.filter((c) => c.data.status === "passed").length}/${checks.length} checks passed`;
        if (r.outcome !== "verified") state = "failed";
      }
    } else if (s.key === "update") {
      const d = find("update", "knowledge.captured");
      if (!hasKnowledge)
        [state, summary] = ["skipped", "no knowledge project on this task"];
      else if (d)
        summary = `${d.proposed} link(s) proposed for ${(d.requirements ?? []).join(", ") || "-"}${d.neo4j?.nodes != null ? ` · Neo4j: ${d.neo4j.nodes} nodes, ${d.neo4j.edges} links` : ""}`;
      else if (finished) summary = "only after verified work";
    } else if (s.key === "finish") {
      const d = find("finish", "task.finished");
      if (d) {
        summary = `${d.status} · ${d.outcome ?? "no outcome"}${d.reason ? ` · ${d.reason}` : ""}`;
        if (d.status !== "completed") state = "failed";
      }
    }
    out.push({ ...s, state, summary, ms: span(evs), events: evs });
  }
  // the step in progress is the first one not done yet while the task runs
  if (!finished) {
    const i = out.findIndex((s) => s.state === "pending");
    if (i > 0 && out[i - 1].state === "done") out[i - 1].state = "running";
  }
  return out;
}

/** The agent loop as model turns, each with the tool calls it asked for and what the harness did with them. */
export function iterations(events: HarnixEvent[]): Iteration[] {
  const its: Iteration[] = [];
  const calls: Record<string, ToolStep> = {};
  let cur: Iteration | null = null;
  for (const e of events) {
    const d = e.data;
    if (e.type === "model.response") {
      cur = {
        n: d.iteration,
        stop: d.stop_reason ?? null,
        text: d.text ?? "",
        tokensIn: d.usage?.input ?? null,
        tokensOut: d.usage?.output ?? null,
        tools: [],
      };
      its.push(cur);
    } else if (e.type === "tool.proposed") {
      const t: ToolStep = {
        callId: d.call_id,
        tool: d.tool,
        mcp: String(d.tool).startsWith("mcp__"),
        args: d.arguments,
        decision: null,
        reason: null,
        ok: null,
        error: null,
        seconds: null,
        output: null,
      };
      calls[d.call_id] = t;
      cur?.tools.push(t);
    } else if (e.type === "tool.authorization" && calls[d.call_id]) {
      Object.assign(calls[d.call_id], {
        decision: d.decision,
        reason: d.reason,
      });
    } else if (e.type === "tool.result" && calls[d.call_id]) {
      Object.assign(calls[d.call_id], {
        ok: d.ok,
        error: d.error,
        seconds: d.duration_seconds,
        output: d.data,
      });
    }
  }
  return its;
}

export const FOUND_BY_LABEL: Record<string, string> = {
  id: "exact id",
  search: "vector + keyword search",
  graph: "graph (database)",
  "graph:neo4j": "graph (Neo4j)",
};
