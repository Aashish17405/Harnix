import { useState } from "react";
import { api } from "../api";
import type { GraphQueryResponse, KGNode } from "../types";
import { NetworkView } from "./NetworkView";
import { Loading } from "./Spinner";

const EXAMPLES: { label: string; cypher: string }[] = [
  { label: "A story's path to its BRD", cypher: "MATCH p=(s:Story {project: $project})-[:DERIVES_FROM*1..3]->(x)\nWHERE s.external_id ENDS WITH 'US-003'\nRETURN p LIMIT 25" },
  { label: "Count by kind", cypher: "MATCH (n:Knowledge {project: $project})\nRETURN n.kind AS kind, count(*) AS nodes ORDER BY nodes DESC" },
  { label: "Rules and what they constrain", cypher: "MATCH (r:Rule {project: $project})-[c:CONSTRAINS]->(x)\nRETURN r, c, x LIMIT 50" },
  { label: "Stories that depend on other stories", cypher: "MATCH (a:Story {project: $project})-[d:DEPENDS_ON]->(b:Story)\nRETURN a, d, b LIMIT 50" },
  { label: "Unconfirmed links", cypher: "MATCH (a:Knowledge {project: $project})-[r {status: 'proposed'}]->(b)\nRETURN type(r) AS link, count(*) AS proposed ORDER BY proposed DESC" },
]; // prettier-ignore
const QUESTIONS = [
  "Which rules constrain favourite accounts, and which stories mention them?",
  "Show the epics of BRD-02 with their features and stories",
  "Which stories have no tests yet?",
];

interface Frame {
  id: number;
  input: string;
  mode: "cypher" | "ask";
  res?: GraphQueryResponse;
  error?: string;
  busy: boolean;
}

/** Query the knowledge graph like a graph database browser: Cypher, or a question the model turns into Cypher. */
export function QueryConsole({ project }: { project: string }) {
  const [mode, setMode] = useState<"cypher" | "ask">("cypher");
  const [text, setText] = useState(EXAMPLES[0].cypher);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [next, setNext] = useState(1);

  async function run(input = text, m = mode) {
    if (!input.trim()) return;
    const id = next;
    setNext(id + 1);
    setFrames((f) => [{ id, input, mode: m, busy: true }, ...f].slice(0, 12));
    try {
      const res = await api.graphQuery(
        project,
        m === "cypher" ? { cypher: input } : { question: input },
      );
      setFrames((f) =>
        f.map((x) => (x.id === id ? { ...x, res, busy: false } : x)),
      );
    } catch (e) {
      setFrames((f) =>
        f.map((x) =>
          x.id === id ? { ...x, error: (e as Error).message, busy: false } : x,
        ),
      );
    }
  }

  return (
    <div className="qc">
      <div className="qc-editor">
        <div className="seg" role="group" aria-label="Query language">
          <button
            type="button"
            aria-pressed={mode === "cypher"}
            onClick={() => setMode("cypher")}
          >
            Cypher
          </button>
          <button
            type="button"
            aria-pressed={mode === "ask"}
            onClick={() => (setMode("ask"), setText(""))}
          >
            Ask in English
          </button>
        </div>
        <div className="qc-input">
          <span className="qc-prompt" aria-hidden="true">
            {mode === "cypher" ? "forge$" : "ask›"}
          </span>
          <textarea
            aria-label={
              mode === "cypher" ? "Cypher query" : "Question about the graph"
            }
            value={text}
            rows={Math.min(8, Math.max(2, text.split("\n").length))}
            spellCheck={false}
            placeholder={
              mode === "cypher"
                ? "MATCH (n:Knowledge {project: $project}) RETURN n LIMIT 25"
                : "e.g. Which stories depend on US-001?"
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void run();
              }
            }}
          />
          <button
            type="button"
            className="primary"
            onClick={() => void run()}
            disabled={!text.trim()}
          >
            Run
          </button>
        </div>
        <div className="qc-examples">
          <span className="muted">Try:</span>
          {mode === "cypher"
            ? EXAMPLES.map((x) => (
                <button
                  key={x.label}
                  type="button"
                  className="chip"
                  onClick={() => setText(x.cypher)}
                >
                  {x.label}
                </button>
              ))
            : QUESTIONS.map((q) => (
                <button
                  key={q}
                  type="button"
                  className="chip"
                  onClick={() => setText(q)}
                >
                  {q}
                </button>
              ))}
        </div>
        <p className="help">
          Ctrl+Enter runs it. <code>$project</code> is always this project.
          Read-only: Neo4j refuses any change, file loading and admin procedures
          are blocked, results show this project only. In English mode the model
          writes the Cypher; you see it and can edit it.
        </p>
      </div>
      {frames.map((f) => (
        <QueryFrame
          key={f.id}
          f={f}
          onClose={() => setFrames((all) => all.filter((x) => x.id !== f.id))}
          onEdit={(c) => (setMode("cypher"), setText(c))}
          onRerun={(c) => void run(c, "cypher")}
        />
      ))}
    </div>
  );
}

function QueryFrame({
  f,
  onClose,
  onEdit,
  onRerun,
}: {
  f: Frame;
  onClose: () => void;
  onEdit: (c: string) => void;
  onRerun: (c: string) => void;
}) {
  const r = f.res?.result;
  const hasGraph = !!r && r.nodes.length > 0;
  const [tab, setTab] = useState<"graph" | "table">("graph");
  const [sel, setSel] = useState<KGNode | null>(null);
  const shown = hasGraph ? tab : "table";
  return (
    <section className="qc-frame" aria-label="Query result">
      <header className="qc-frame-head">
        <code className="qc-frame-q">
          {f.mode === "ask" ? `ask› ${f.input}` : f.input}
        </code>
        <button
          type="button"
          className="linklike"
          onClick={onClose}
          aria-label="Close this result"
        >
          ✕
        </button>
      </header>
      {f.busy && (
        <Loading>{f.mode === "ask" ? "Writing the query and running it…" : "Running…"}</Loading>
      )}
      {f.error && <p className="banner banner-bad">{f.error}</p>}
      {f.res && (
        <>
          {f.res.generated && (
            <div className="qc-generated">
              <span className="muted">
                Cypher written by the model
                {f.res.explanation ? `: ${f.res.explanation}` : ""}
              </span>
              <pre>{f.res.cypher}</pre>
              <div className="row">
                <button type="button" onClick={() => onEdit(f.res!.cypher)}>
                  Edit
                </button>
                <button type="button" onClick={() => onRerun(f.res!.cypher)}>
                  Run again as Cypher
                </button>
              </div>
            </div>
          )}
          {r && (
            <>
              <div className="qc-frame-bar">
                {hasGraph && (
                  <div className="seg" role="group" aria-label="Result view">
                    <button
                      type="button"
                      aria-pressed={shown === "graph"}
                      onClick={() => setTab("graph")}
                    >
                      Graph
                    </button>
                    <button
                      type="button"
                      aria-pressed={shown === "table"}
                      onClick={() => setTab("table")}
                    >
                      Table
                    </button>
                  </div>
                )}
                <span className="muted">
                  {r.records} record{r.records === 1 ? "" : "s"}
                  {hasGraph
                    ? ` · ${r.nodes.length} nodes, ${r.edges.length} relationships`
                    : ""}{" "}
                  · {r.ms} ms
                  {r.truncated ? " · cut at 500 records" : ""}
                  {r.hidden
                    ? ` · ${r.hidden} matches outside this project not shown`
                    : ""}
                </span>
              </div>
              {shown === "graph" ? (
                <div className="qc-graph">
                  <NetworkView
                    nodes={r.nodes}
                    edges={r.edges}
                    selected={sel?.id ?? null}
                    onSelect={setSel}
                    height={420}
                  />
                  {sel && (
                    <dl className="pipe-kv qc-props">
                      <dt>external_id</dt>
                      <dd>{sel.external_id}</dd>
                      <dt>title</dt>
                      <dd>{sel.title}</dd>
                      <dt>kind</dt>
                      <dd>{sel.subtype ?? sel.kind}</dd>
                      <dt>version</dt>
                      <dd>{sel.version}</dd>
                    </dl>
                  )}
                </div>
              ) : r.records === 0 ? (
                <p className="muted">No records.</p>
              ) : (
                <div className="scroll-x">
                  <table className="pipe-table">
                    <thead>
                      <tr>
                        {r.columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {r.rows.map((row, i) => (
                        <tr key={i}>
                          {row.map((v, j) => (
                            <td key={j}>{cell(v)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function cell(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (Array.isArray(o.labels))
      return `(:${(o.labels as string[]).join(":")} ${o.external_id ?? ""})`;
    if (typeof o.type === "string") return `[:${o.type}]`;
    return JSON.stringify(v);
  }
  return String(v);
}
