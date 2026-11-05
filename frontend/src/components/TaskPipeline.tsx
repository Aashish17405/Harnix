import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import {
  FOUND_BY_LABEL,
  buildStages,
  iterations,
  type Stage,
} from "../pipeline";
import type { BundleCandidate, HarnixEvent, StoredBundle, Task } from "../types";
import { isActive } from "../types";
import { RetrievalDrilldown } from "./RetrievalDrilldown";
import { downloadReport } from "../lib/bundleReport";
import { Loading } from "./Spinner";

const STATE_LABEL: Record<Stage["state"], string> = {
  done: "done",
  warn: "done with warnings",
  failed: "failed",
  skipped: "skipped",
  running: "running",
  pending: "waiting",
};
const STAGE_MS: Record<string, string> = {
  id_lookup_and_search: "Exact id lookup + vector/keyword search",
  graph_traversal: "Graph traversal from the best matches",
  rerank_filter_budget: "Rerank, filter, budgets",
  assemble: "Fetch the chosen items, relationships, sufficiency",
};

const ms = (n: number | null) =>
  n == null
    ? ""
    : n < 1000
      ? `${Math.round(n)} ms`
      : `${(n / 1000).toFixed(1)} s`;
const short = (v: unknown, n = 160) => {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s && s.length > n ? `${s.slice(0, n - 1)}…` : (s ?? "");
};

/** The task's run as a pipeline: each step, what it does, what it decided, and the evidence behind it. */
export function TaskPipeline({
  task,
  events,
}: {
  task: Task;
  events: HarnixEvent[];
}) {
  const finished = !isActive(task.status) && task.status !== "queued";
  const stages = useMemo(
    () => buildStages(events, finished, !!task.knowledge_project),
    [events, finished, task.knowledge_project],
  );
  return (
    <div className="pipe">
      <p className="help">
        Built from the events the harness recorded while the task ran. Open a
        step to see exactly what happened in it.
      </p>
      <ol className="pipe-steps">
        {stages.map((s, i) => (
          <li key={s.key} className={`pipe-step st-${s.state}`}>
            <span className="pipe-dot" aria-hidden="true">
              {i + 1}
            </span>
            <details open={s.key === "context" || s.key === "agent"}>
              <summary>
                <span className="pipe-title">{s.title}</span>
                <span className={`chip pipe-state st-${s.state}`}>
                  {STATE_LABEL[s.state]}
                </span>
                {s.ms != null && <span className="chip">{ms(s.ms)}</span>}
                <span className="pipe-sum">{s.summary}</span>
              </summary>
              <p className="pipe-what">{s.what}</p>
              <StageDetail stage={s} task={task} events={events} />
            </details>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StageDetail({
  stage,
  task,
  events,
}: {
  stage: Stage;
  task: Task;
  events: HarnixEvent[];
}) {
  const ev = (type: string) => stage.events.find((e) => e.type === type)?.data;
  if (stage.state === "skipped" || stage.state === "pending")
    return (
      <p className="muted">
        {stage.summary || "Nothing recorded for this step."}
      </p>
    );
  switch (stage.key) {
    case "prepare": {
      const ws = ev("workspace.created");
      const pi = ev("project.inspected");
      const graph = ev("graph.indexed");
      const mcp = ev("mcp.servers");
      const base = stage.events.filter((e) => e.type === "verification.check");
      return (
        <dl className="pipe-kv">
          {ws && (
            <>
              <dt>Workspace</dt>
              <dd>
                <code>{ws.path}</code> · runs in <b>{ws.execution_mode}</b>
              </dd>
            </>
          )}
          {pi && (
            <>
              <dt>Project</dt>
              <dd>
                {(pi.types ?? []).join(", ") || "unknown type"} · test command{" "}
                <code>{pi.commands?.test ?? "none found"}</code>
              </dd>
            </>
          )}
          {base.length > 0 && (
            <>
              <dt>Baseline (before any change)</dt>
              <dd>
                {base.map((b) => (
                  <div key={b.id}>
                    <span
                      className={`chip st-${b.data.status === "passed" ? "done" : "failed"}`}
                    >
                      {b.data.status}
                    </span>{" "}
                    <code>{b.data.command}</code> exit {b.data.exit_code}
                  </div>
                ))}
              </dd>
            </>
          )}
          {graph && (
            <>
              <dt>Code graph</dt>
              <dd>
                {graph.files} files, {graph.symbols} symbols (
                {graph.files_parsed} parsed now, {graph.files_unchanged}{" "}
                unchanged) in {graph.seconds}s
              </dd>
            </>
          )}
          {mcp && mcp.connected?.length > 0 && (
            <>
              <dt>Tool servers (MCP)</dt>
              <dd>
                {mcp.connected.map(
                  (c: {
                    name: string;
                    source: string;
                    auto_approve?: string[];
                  }) => (
                    <div key={c.name}>
                      <b>{c.name}</b> ({c.source}) · no approval needed for:{" "}
                      {(c.auto_approve ?? []).join(", ") || "none"}
                    </div>
                  ),
                )}
              </dd>
            </>
          )}
        </dl>
      );
    }
    case "index": {
      const d = ev("knowledge.indexed");
      return d ? (
        <dl className="pipe-kv">
          <dt>Repository</dt>
          <dd>{d.repository}</dd>
          <dt>Files</dt>
          <dd>
            {d.created} new · {d.updated} changed (new version) · {d.unchanged}{" "}
            unchanged (skipped)
          </dd>
          {d.failed?.length > 0 && (
            <>
              <dt>Failed</dt>
              <dd>{d.failed.join("; ")}</dd>
            </>
          )}
          {d.partial?.length > 0 && (
            <>
              <dt>Not embedded yet</dt>
              <dd>{d.partial.join(", ")}</dd>
            </>
          )}
        </dl>
      ) : (
        <p className="muted">
          Indexing was not possible: {ev("knowledge.unavailable")?.message}
        </p>
      );
    }
    case "context":
      return (
        <ContextDetail
          bundleId={ev("knowledge.bundle")?.bundle_id}
          event={ev("knowledge.bundle")}
        />
      );
    case "agent":
      return <AgentDetail events={events} />;
    case "verify": {
      const checks = stage.events.filter(
        (e) => e.type === "verification.check",
      );
      const r = ev("verification.result");
      return (
        <>
          <div className="scroll-x">
            <table className="pipe-table">
              <thead>
                <tr>
                  <th>Attempt</th>
                  <th>Check</th>
                  <th>Command</th>
                  <th>Result</th>
                  <th>Exit</th>
                </tr>
              </thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={c.id}>
                    <td>{c.data.attempt}</td>
                    <td>{c.data.kind}</td>
                    <td>
                      <code>{c.data.command ?? "-"}</code>
                    </td>
                    <td>
                      <span
                        className={`chip st-${c.data.status === "passed" ? "done" : c.data.status === "skipped" ? "skipped" : "failed"}`}
                      >
                        {c.data.status}
                      </span>
                    </td>
                    <td>{c.data.exit_code ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {r && (
            <p>
              <b>Verdict:</b> {r.outcome}
              {r.blocking?.length
                ? ` · blocking: ${r.blocking.join(", ")}`
                : ""}
              {r.reasons?.length ? ` · ${r.reasons.join("; ")}` : ""}
            </p>
          )}
        </>
      );
    }
    case "update": {
      const d = ev("knowledge.captured");
      return d ? (
        <dl className="pipe-kv">
          <dt>Target requirement</dt>
          <dd>{(d.requirements ?? []).join(", ") || "none"}</dd>
          <dt>Links proposed</dt>
          <dd>
            {d.proposed} new
            {d.existing ? `, ${d.existing} already proposed` : ""} (they stay
            dashed until a person confirms them on the Knowledge tab)
          </dd>
          {d.files_without_source?.length > 0 && (
            <>
              <dt>Files not linked</dt>
              <dd>{d.files_without_source.join(", ")}</dd>
            </>
          )}
          {d.neo4j && (
            <>
              <dt>Neo4j</dt>
              <dd>
                {d.neo4j.error
                  ? `not updated: ${d.neo4j.error}`
                  : `rebuilt: ${d.neo4j.nodes} nodes, ${d.neo4j.edges} relationships`}
              </dd>
            </>
          )}
        </dl>
      ) : (
        <p className="muted">{stage.summary}</p>
      );
    }
    case "finish": {
      const d = ev("task.finished");
      return (
        <dl className="pipe-kv">
          <dt>Status</dt>
          <dd>
            {d?.status ?? task.status} · outcome{" "}
            {d?.outcome ?? task.outcome ?? "-"}
          </dd>
          <dt>Totals</dt>
          <dd>
            {task.iterations} model turns · {task.tool_calls} tool calls ·{" "}
            {task.input_tokens} tokens in / {task.output_tokens} out
            {task.estimated_cost_usd != null
              ? ` · about $${task.estimated_cost_usd}`
              : ""}
          </dd>
          <dt>Your repository</dt>
          <dd>
            {events.some((e) => e.type === "task.applied")
              ? "changes applied"
              : "untouched until you apply the changes (Changes tab)"}
          </dd>
        </dl>
      );
    }
  }
}

function ContextDetail({
  bundleId,
  event,
}: {
  bundleId?: string;
  event?: Record<string, unknown>;
}) {
  const [b, setB] = useState<StoredBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showBudget, setShowBudget] = useState(false);
  useEffect(() => {
    if (!bundleId) return;
    api.storedBundle(bundleId).then(setB, (e: Error) => setError(e.message));
  }, [bundleId]);
  if (!bundleId) return <p className="muted">No context was built.</p>;
  if (error)
    return <p className="muted">Could not load the context bundle: {error}</p>;
  if (!b) return <Loading>Loading the context bundle…</Loading>;
  const p = b.pipeline;
  const order = { chosen: 0, over_budget: 1, dropped: 2 } as const;
  const cands = p
    ? [...p.candidates].sort(
        (x, y) =>
          order[x.decision] - order[y.decision] ||
          (y.score ?? -9) - (x.score ?? -9),
      )
    : [];
  const overBudget = cands.filter((c) => c.decision === "over_budget").length;
  const dropped = cands.filter((c) => c.decision === "dropped").length;
  // the chosen and the dropped matter most; the long tail that did not fit is one click away
  const shown = cands.filter((c) => showBudget || c.decision !== "over_budget");
  return (
    <div className="pipe-context">
      <p className="help">
        Bundle <code>{b.bundle_id.slice(0, 16)}</code> · embeddings{" "}
        {b.embedding_model} · the same inputs always give the same bundle id.
      </p>
      {p ? (
        <>
          {p.relevance && (
            <p className="help">
              Relevance: the closest requirement is{" "}
              {p.relevance.best.toFixed(2)} similar to the task
              {p.relevance.floor
                ? ` (floor ${p.relevance.floor.toFixed(2)}: below it, and with no id named, the context is "insufficient")`
                : " (no relevance floor set)"}
              .
            </p>
          )}
          <h5>1. What the task names</h5>
          <p>
            {p.named_ids.length ? (
              p.named_ids.map((x) => (
                <span key={x} className="chip">
                  {x}
                </span>
              ))
            ) : (
              <span className="muted">
                No ids in the task text: everything comes from search and the
                graph.
              </span>
            )}
          </p>
          <h5>2. Sub-steps and time</h5>
          <ul className="pipe-times">
            {Object.entries(p.timings_ms).map(([k, v]) => (
              <li key={k}>
                <span>{STAGE_MS[k] ?? k}</span>
                <b>{ms(v)}</b>
              </li>
            ))}
          </ul>
          <h5>
            3. Graph seeds (the strongest matches the graph was expanded from)
          </h5>
          <p>
            {p.seeds.map((x, i) => (
              <span key={x} className="chip">
                {i === 0 ? "best: " : ""}
                {x}
              </span>
            ))}
          </p>
          <h5>
            4. Every candidate, its score and what happened to it (
            {cands.length - overBudget - dropped} chosen of {cands.length})
          </h5>
          <p className="help">
            {dropped} dropped (with the reason) · {overBudget} relevant but did
            not fit the budget{" "}
            {overBudget > 0 && (
              <button
                type="button"
                className="linklike"
                onClick={() => setShowBudget(!showBudget)}
                aria-expanded={showBudget}
              >
                {showBudget ? "hide them" : "show them"}
              </button>
            )}
          </p>
          <div className="scroll-x">
            <table className="pipe-table">
              <thead>
                <tr>
                  <th>Decision</th>
                  <th>Source</th>
                  <th>Kind</th>
                  <th>Found by</th>
                  <th className="num">Similarity</th>
                  <th className="num">Bonuses</th>
                  <th className="num">Score</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <CandidateRow key={`${c.decision}-${c.external_id}`} c={c} />
                ))}
              </tbody>
            </table>
          </div>
          <p className="help">
            Score = similarity to the task (cosine of the best chunk) +{" "}
            {p.weights.named_id} for an id the task names + {p.weights.search}{" "}
            if search found it + {p.weights.best_seed_link} if linked to the
            best match + a small preference for stories/requirements/tickets.
            Graph-only items below {Math.round(p.weights.floor * 100)}% of the
            best similarity are dropped unless linked to the best match.
            Budgets: {p.budgets.max_items} items,{" "}
            {Object.entries(p.budgets.per_slot)
              .map(([k, v]) => `${k} ${v}`)
              .join(", ")}
            .
          </p>
        </>
      ) : (
        <p className="muted">
          This bundle was built before candidate details were recorded; rebuild
          it (Preview the context) to see them.
        </p>
      )}
      <h5>{p ? "5." : ""} Sufficiency check</h5>
      <p>
        <span
          className={`chip st-${b.status === "sufficient" ? "done" : "failed"}`}
        >
          {b.status}
        </span>{" "}
        {b.sufficiency.missing.length
          ? `missing: ${b.sufficiency.missing.join("; ")}`
          : "a requirement, story or ticket matches the task"}
        {b.sufficiency.contradictions ? " · contradicting sources" : ""}
      </p>
      {b.sufficiency.notes.length > 0 && (
        <ul className="pipe-notes">
          {b.sufficiency.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {b.unresolved_questions.length > 0 && (
        <ul className="pipe-notes">
          {b.unresolved_questions.map((q) => (
            <li key={q}>Question: {q}</li>
          ))}
        </ul>
      )}
      <p className="row">
        <button type="button" onClick={() => downloadReport(b, "md")}>
          Download report (.md)
        </button>
        <button type="button" onClick={() => downloadReport(b, "json")}>
          Download raw data (.json)
        </button>
      </p>
      <details>
        <summary>
          Drill down: what was searched, ranked, pulled from the graph and
          chosen, layer by layer
        </summary>
        <RetrievalDrilldown b={b} />
      </details>
      {b.retrieval_trace && (
        <details>
          <summary>Retrieval tool calls ({b.retrieval_trace.length})</summary>
          <ol className="pipe-trace">
            {b.retrieval_trace.map((t, i) => (
              <li key={i}>
                <code>{t.tool}</code> {short(t.args, 120)} → {t.results}{" "}
                result(s)
              </li>
            ))}
          </ol>
        </details>
      )}
      {event && (
        <p className="help">
          Handed to the model:{" "}
          {Object.entries((event.items as Record<string, number>) ?? {})
            .map(([k, n]) => `${n} ${k}`)
            .join(", ")}
          , fenced as untrusted data in the first message.
        </p>
      )}
    </div>
  );
}

function CandidateRow({ c }: { c: BundleCandidate }) {
  const b = c.bonus;
  const bonuses = [
    b.named_id && `id +${b.named_id.toFixed(2)}`,
    b.search && `search +${b.search.toFixed(2)}`,
    b.best_seed_link && `seed link +${b.best_seed_link.toFixed(2)}`,
    b.kind && `kind +${b.kind.toFixed(2)}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const label = {
    chosen: "chosen",
    over_budget: "over budget",
    dropped: "dropped",
  }[c.decision];
  return (
    <tr className={`cand-${c.decision}`}>
      <td>
        <span
          className={`chip st-${c.decision === "chosen" ? "done" : c.decision === "dropped" ? "failed" : "warn"}`}
        >
          {label}
        </span>
      </td>
      <td>
        <b>{c.external_id}</b>
        {c.seed ? " ★" : ""}
      </td>
      <td>{c.kind}</td>
      <td>{c.found_by.map((f) => FOUND_BY_LABEL[f] ?? f).join(" + ")}</td>
      <td className="num">{c.similarity.toFixed(3)}</td>
      <td className="num">{bonuses || "-"}</td>
      <td className="num">{c.score != null ? c.score.toFixed(3) : "-"}</td>
      <td>{c.reason ?? ""}</td>
    </tr>
  );
}

function AgentDetail({ events }: { events: HarnixEvent[] }) {
  const its = iterations(events);
  if (!its.length)
    return <p className="muted">The model has not answered yet.</p>;
  return (
    <ol className="pipe-turns">
      {its.map((it) => (
        <li key={it.n}>
          <div className="pipe-turn-head">
            <b>Turn {it.n}</b>
            <span className="muted">
              {it.stop === "tool_use"
                ? "asked for tools"
                : it.stop === "end_turn"
                  ? "said it is done"
                  : it.stop}
            </span>
            {it.tokensIn != null && (
              <span className="chip">
                {it.tokensIn} in / {it.tokensOut} out tokens
              </span>
            )}
          </div>
          {it.text && <p className="pipe-text">{short(it.text, 400)}</p>}
          {it.tools.length > 0 && (
            <div className="scroll-x">
              <table className="pipe-table">
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Arguments</th>
                    <th>Policy decision</th>
                    <th>Result</th>
                    <th className="num">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {it.tools.map((t) => (
                    <tr key={t.callId}>
                      <td>
                        <code>{t.tool.replace(/^mcp__knowledge__/, "")}</code>
                        {t.mcp && (
                          <span className="chip st-proposed">
                            knowledge MCP
                          </span>
                        )}
                      </td>
                      <td className="pipe-args">
                        <code>{short(t.args)}</code>
                      </td>
                      <td>
                        <span
                          className={`chip st-${t.decision === "deny" ? "failed" : t.decision === "approve" ? "warn" : "done"}`}
                        >
                          {t.decision ?? "…"}
                        </span>{" "}
                        <span className="muted">{t.reason}</span>
                      </td>
                      <td>
                        {t.ok == null ? (
                          "…"
                        ) : t.ok ? (
                          <span className="chip st-done">ok</span>
                        ) : (
                          <span className="chip st-failed">
                            {short(t.error, 80)}
                          </span>
                        )}
                      </td>
                      <td className="num">
                        {t.seconds != null ? `${t.seconds.toFixed(2)}s` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
