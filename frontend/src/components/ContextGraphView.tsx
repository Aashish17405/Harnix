import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { FOUND_BY_LABEL } from "../pipeline";
import { RetrievalDrilldown } from "./RetrievalDrilldown";
import type {
  BundleItem,
  BundleSummary,
  KGEdge,
  KGNode,
  StoredBundle,
} from "../types";
import { GraphLegend } from "./GraphView";
import { NetworkView } from "./NetworkView";
import { downloadReport } from "../lib/bundleReport";
import { Loading } from "./Spinner";

const SLOT_LABEL: Record<string, string> = {
  requirements: "Requirements, stories and tickets",
  design: "Design",
  code: "Code",
  tests: "Tests",
  constraints: "Rules, concepts and constraints",
};

/** One context graph: exactly what an agent was given for a task, drawn as a graph, with the content of every node. */
export function ContextGraphView({ bundleId }: { bundleId: string }) {
  const [b, setB] = useState<StoredBundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [showText, setShowText] = useState(false);
  const [considered, setConsidered] = useState(true); // also draw what was looked at but not given to the agent

  useEffect(() => {
    api.storedBundle(bundleId).then(
      (x) => (setB(x), setError(null)),
      (e: Error) => setError(e.message),
    );
  }, [bundleId]);

  const items = useMemo(
    () =>
      b
        ? Object.entries(b.slots).flatMap(([slot, list]) =>
            list.map((i) => ({ ...i, slot })),
          )
        : [],
    [b],
  );
  const { nodes, edges } = useMemo(() => {
    const byExt = new Map(items.map((i) => [i.external_id, i.source_id]));
    const nodes: KGNode[] = items.map((i) => ({
      id: i.source_id,
      kind: i.kind,
      external_id: i.external_id,
      title: i.title,
      version: i.version,
      origin: i.origin ?? "ingest",
      state: i.ingestion_state ?? "ok",
      in_bundle: true,
      subtype: i.kind === "concept" || i.kind === "rule" ? i.kind : null,
    }));
    const edges: KGEdge[] = [];
    const seen = new Set<string>();
    const addEdge = (r: {
      from: string;
      to: string;
      type: string;
      status: string;
      evidence?: string;
      stale?: boolean;
    }) => {
      const id = `${r.from}|${r.type}|${r.to}`;
      if (seen.has(id)) return;
      seen.add(id);
      edges.push({
        id,
        from: byExt.get(r.from) ?? r.from,
        to: byExt.get(r.to) ?? r.to,
        type: r.type,
        status: r.status as KGEdge["status"],
        origin: "",
        evidence: r.evidence ?? "",
        stale: !!r.stale,
        in_bundle: byExt.has(r.from) && byExt.has(r.to),
      });
    };
    (b?.relationships ?? []).forEach(addEdge);
    if (considered) {
      const add = (ext: string, kind: string, title: string) => {
        if (byExt.has(ext)) return;
        byExt.set(ext, ext);
        nodes.push({
          id: ext,
          kind,
          external_id: ext,
          title,
          version: 0,
          origin: "considered",
          state: "ok",
          in_bundle: false,
          subtype: kind === "concept" || kind === "rule" ? kind : null,
        });
      };
      for (const c of b?.pipeline?.candidates ?? [])
        add(c.external_id, c.kind, c.title ?? "");
      for (const x of b?.pipeline?.expansion ?? [])
        for (const n of x.nodes) add(n.external_id, n.kind, n.title);
      for (const x of b?.pipeline?.expansion ?? [])
        for (const e of x.edges)
          if (byExt.has(e.from) && byExt.has(e.to)) addEdge(e);
    }
    return { nodes, edges };
  }, [items, b, considered]);

  if (error)
    return <p className="muted">Could not load this context graph: {error}</p>;
  if (!b) return <Loading>Loading the context graph…</Loading>;
  const selectedIsItem =
    selected == null || items.some((i) => i.source_id === selected);
  const sel = items.find((i) => i.source_id === selected) ?? items[0];
  const selectedCand = b.pipeline?.candidates.find(
    (c) => c.external_id === selected,
  );
  const cand = b.pipeline?.candidates.find(
    (c) => c.external_id === sel?.external_id && c.decision === "chosen",
  );

  async function toggleText() {
    if (!showText && text == null) {
      try {
        setText((await api.bundleText(bundleId)).text);
      } catch (e) {
        setText(`Could not load: ${(e as Error).message}`);
      }
    }
    setShowText(!showText);
  }

  return (
    <div className="cgraph">
      <div className="cgraph-head">
        <span
          className={`chip st-${b.status === "sufficient" ? "done" : "failed"}`}
        >
          {b.status}
        </span>
        <span className="chip">bundle {b.bundle_id.slice(0, 12)}</span>
        <span className="chip">{items.length} items</span>
        <span className="chip">{edges.length} relationships</span>
        <span className="muted">
          {Object.entries(b.slots)
            .filter(([, v]) => v.length)
            .map(([k, v]) => `${v.length} ${k}`)
            .join(" · ")}
        </span>
      </div>
      <p className="cgraph-query">
        <b>Asked:</b> {b.query ?? ""}
      </p>
      <div className="row">
        <button type="button" onClick={() => downloadReport(b, "md")}>
          Download report (.md)
        </button>
        <button type="button" onClick={() => downloadReport(b, "json")}>
          Download raw data (.json)
        </button>
      </div>
      <GraphLegend />
      <div className="cgraph-body">
        <div className="cgraph-graph">
          <label className="row">
            <input
              type="checkbox"
              checked={considered}
              onChange={(e) => setConsidered(e.target.checked)}
            />{" "}
            Also show what was considered but not given to the agent (faded)
          </label>
          <NetworkView
            nodes={nodes}
            edges={edges}
            selected={selected}
            onSelect={(n) => setSelected(n?.id ?? null)}
            emphasizeBundle
          />
        </div>
        {sel && selectedIsItem && <ItemPanel item={sel} cand={cand} />}
        {!selectedIsItem && selectedCand && (
          <aside className="cgraph-item" aria-label="Considered item">
            <h4>{selectedCand.external_id}</h4>
            <p className="muted">{selectedCand.title}</p>
            <p>
              <span className="chip">{selectedCand.kind}</span>
              <span className="chip st-warn">{selectedCand.decision}</span>
            </p>
            <p>
              {selectedCand.reason ?? ""} similarity{" "}
              {selectedCand.similarity.toFixed(3)}
            </p>
          </aside>
        )}
      </div>
      <h4>Drill down, layer by layer</h4>
      <RetrievalDrilldown b={b} />
      <button
        type="button"
        onClick={() => void toggleText()}
        aria-expanded={showText}
      >
        {showText ? "Hide" : "Show"} the text the agent received
      </button>
      {showText && (
        <>
          <p className="help">
            This is the context exactly as it is placed in the agent's first
            message, inside a block marked as untrusted data (the agent is told
            to treat it as information, never as instructions).
          </p>
          <pre className="cgraph-text">{text ?? "Loading…"}</pre>
        </>
      )}
    </div>
  );
}

function ItemPanel({
  item,
  cand,
}: {
  item: BundleItem & { slot: string };
  cand?: {
    similarity: number;
    score: number | null;
    bonus: Record<string, number>;
  };
}) {
  return (
    <aside className="cgraph-item" aria-label="Selected item">
      <h4>{item.external_id}</h4>
      <p className="muted">{item.title}</p>
      <p>
        <span className="chip">{item.kind}</span>
        <span className="chip">v{item.version}</span>
        <span className="chip">{SLOT_LABEL[item.slot] ?? item.slot}</span>
        {item.origin && item.origin !== "ingest" && (
          <span className="chip st-warn">origin: {item.origin}</span>
        )}
      </p>
      <dl className="pipe-kv">
        <dt>How it was found</dt>
        <dd>
          {(item.found_by ?? [])
            .map((f) => FOUND_BY_LABEL[f] ?? f)
            .join(" + ") || "-"}
        </dd>
        {cand && (
          <>
            <dt>Score</dt>
            <dd>
              {cand.score != null ? cand.score.toFixed(3) : "-"} (similarity{" "}
              {cand.similarity.toFixed(3)}
              {Object.entries(cand.bonus)
                .filter(([, v]) => v)
                .map(([k, v]) => ` + ${k.replace(/_/g, " ")} ${v.toFixed(2)}`)
                .join("")}
              )
            </dd>
          </>
        )}
        {item.target && (
          <>
            <dt>Role</dt>
            <dd>
              <span className="chip st-done">target</span> what the task is
              about, given whole
            </dd>
          </>
        )}
        {item.sections && item.sections.length > 0 && (
          <>
            <dt>Sections given</dt>
            <dd>
              {item.sections.map((s) => (
                <div key={s.chunk_id}>
                  {s.heading || `part ${s.ordinal + 1}`}{" "}
                  <span className="muted">
                    (similarity {s.similarity.toFixed(2)})
                  </span>
                </div>
              ))}
            </dd>
          </>
        )}
        <dt>Given as</dt>
        <dd>
          {item.excerpt_of ?? "whole source"}
          {item.content_truncated
            ? " (the agent can fetch the rest with knowledge_get)"
            : ""}
        </dd>
        {item.content_hash && (
          <>
            <dt>Content hash</dt>
            <dd>
              <code>{item.content_hash.slice(0, 16)}</code>
            </dd>
          </>
        )}
        {item.open_questions && item.open_questions.length > 0 && (
          <>
            <dt>Open questions</dt>
            <dd>{item.open_questions.join("; ")}</dd>
          </>
        )}
      </dl>
      <h5>What the agent received for this item</h5>
      <pre className="cgraph-content">{item.content}</pre>
    </aside>
  );
}

/** Every context graph built for a project (task runs and previews); pick one to see it with its content. */
export function ContextGraphList({
  project,
  focus,
}: {
  project: string;
  focus?: string | null;
}) {
  const [rows, setRows] = useState<BundleSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => {
    api.knowledgeBundles(project).then(
      (r) => (setRows(r.bundles), setError(null)),
      (e: Error) => setError(e.message),
    );
  }, [project, focus]);
  if (error)
    return <p className="muted">Could not list the context graphs: {error}</p>;
  if (!rows) return <Loading>Loading…</Loading>;
  if (!rows.length)
    return (
      <p className="muted">
        None yet. Preview the context (step 4) or run a task with this project's
        knowledge.
      </p>
    );
  const current = picked ?? focus ?? rows[0].bundle_id;
  return (
    <div className="cgraph-list">
      <div className="scroll-x">
        <table className="pipe-table">
          <thead>
            <tr>
              <th>Built</th>
              <th>What was asked</th>
              <th>Holds</th>
              <th>Used by</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.bundle_id}
                className={
                  r.bundle_id === current ? "cgraph-row on" : "cgraph-row"
                }
                onClick={() => setPicked(r.bundle_id)}
              >
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>
                  <button
                    type="button"
                    className="linklike"
                    onClick={() => setPicked(r.bundle_id)}
                    aria-pressed={r.bundle_id === current}
                  >
                    {r.query.length > 110
                      ? `${r.query.slice(0, 109)}…`
                      : r.query}
                  </button>
                </td>
                <td>
                  {Object.entries(r.items)
                    .map(([k, n]) => `${n} ${k}`)
                    .join(", ")}{" "}
                  · {r.relationships} links
                </td>
                <td>
                  {r.tasks.map((t) => (
                    <a
                      key={t}
                      href={`#/tasks/${t}`}
                      className="chip"
                      onClick={(e) => e.stopPropagation()}
                    >
                      task {t.slice(0, 8)}
                    </a>
                  ))}
                  {r.previews > 0 && (
                    <span className="chip">
                      {r.previews} preview{r.previews > 1 ? "s" : ""}
                    </span>
                  )}
                </td>
                <td>
                  <span
                    className={`chip st-${r.status === "sufficient" ? "done" : "failed"}`}
                  >
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ContextGraphView key={current} bundleId={current} />
    </div>
  );
}
