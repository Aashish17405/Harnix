import { useMemo, useState } from "react";
import type { BundleCandidate, SearchRow, StoredBundle } from "../types";
import { CapturedView } from "./CapturedView";

type Row = SearchRow;

const DECISION: Record<BundleCandidate["decision"], string> = {
  chosen: "in the context",
  over_budget: "relevant, did not fit",
  dropped: "dropped",
};
const DECISION_CHIP: Record<BundleCandidate["decision"], string> = {
  chosen: "st-done",
  over_budget: "st-warn",
  dropped: "st-failed",
};
const FOUND: Record<string, string> = {
  id: "named in the prompt",
  search: "search",
  graph: "graph (database)",
  "graph:neo4j": "graph (Neo4j)",
};
const f3 = (n: number) => n.toFixed(3);

/** A bar for a similarity or score between 0 and 1. */
function Bar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <span className="simbar" role="img" aria-label={`${pct.toFixed(0)}%`}>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}

function Layer({
  n,
  title,
  summary,
  open,
  children,
}: {
  n: number;
  title: string;
  summary: string;
  open?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="layer" open={open}>
      <summary>
        <span className="layer-n">{n}</span>
        <b>{title}</b>
        <span className="muted"> · {summary}</span>
      </summary>
      <div className="layer-body">{children}</div>
    </details>
  );
}

/** One ranked list of chunks (keyword, vector or fused), each row opening the stored chunk and its embedding. */
function ChunkTable({
  project,
  rows,
  valueLabel,
  value,
  extra,
}: {
  project: string;
  rows: Row[];
  valueLabel: string;
  value: (r: Row) => string;
  extra?: (r: Row) => string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  if (!rows.length)
    return <p className="muted">Nothing ranked in this list.</p>;
  return (
    <table className="pipe-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Document</th>
          <th>Section</th>
          <th>{valueLabel}</th>
          {extra && <th>In the other lists</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <ChunkRow
            key={r.chunk_id}
            project={project}
            r={r}
            i={i}
            open={open === r.chunk_id}
            onToggle={() => setOpen(open === r.chunk_id ? null : r.chunk_id)}
            value={value(r)}
            extra={extra?.(r)}
          />
        ))}
      </tbody>
    </table>
  );
}

function ChunkRow({
  project,
  r,
  i,
  open,
  onToggle,
  value,
  extra,
}: {
  project: string;
  r: Row;
  i: number;
  open: boolean;
  onToggle: () => void;
  value: string;
  extra?: string;
}) {
  return (
    <>
      <tr>
        <td>{i + 1}</td>
        <td>
          <button
            type="button"
            className="linklike"
            onClick={onToggle}
            aria-expanded={open}
          >
            {r.external_id}
          </button>{" "}
          <span className="chip">{r.kind}</span>
        </td>
        <td>
          {r.heading || <span className="muted">(no heading)</span>}
          {r.ordinal != null && (
            <span className="muted"> · chunk {r.ordinal + 1}</span>
          )}
        </td>
        <td>{value}</td>
        {extra !== undefined && <td>{extra}</td>}
      </tr>
      {open && (
        <tr>
          <td colSpan={extra !== undefined ? 5 : 4}>
            <p className="muted">The matching text of this chunk:</p>
            <pre className="cgraph-text">{r.snippet}</pre>
            <details>
              <summary>
                Open the stored chunks, what was embedded and the vector
              </summary>
              <CapturedView project={project} refId={r.source_id} />
            </details>
          </td>
        </tr>
      )}
    </>
  );
}

/** Everything the context builder looked at for one prompt, layer by layer, down to the chunk, the vector and the link. */
export function RetrievalDrilldown({ b }: { b: StoredBundle }) {
  const p = b.pipeline;
  const project = b.project;
  const [show, setShow] = useState<
    Record<BundleCandidate["decision"], boolean>
  >({
    chosen: true,
    over_budget: true,
    dropped: true,
  });
  const [openCand, setOpenCand] = useState<string | null>(null);
  const [allNodes, setAllNodes] = useState<Record<number, boolean>>({});
  const decisionOf = useMemo(() => {
    const m = new Map<string, BundleCandidate["decision"]>();
    for (const c of p?.candidates ?? [])
      if (!m.has(c.external_id) || c.decision === "chosen")
        m.set(c.external_id, c.decision);
    return m;
  }, [p]);
  if (!p)
    return (
      <p className="muted">
        This context was built before the pipeline was recorded.
      </p>
    );
  const old = !p.search && !p.expansion;
  const cands = [...p.candidates]
    .filter((c) => show[c.decision])
    .sort((a, z) => (z.score ?? -9) - (a.score ?? -9));
  const pulled = new Set(
    (p.expansion ?? []).flatMap((e) => e.nodes.map((n) => n.external_id)),
  );
  const items = Object.entries(b.slots).flatMap(([slot, list]) =>
    list.map((i) => ({ ...i, slot })),
  );

  return (
    <div className="drill" aria-label="Retrieval drill-down">
      <p className="help">
        Everything considered for this prompt, from the text down to the stored
        chunk and its vector. Open a layer, then a row, to go deeper.
        {old &&
          " This bundle was built before the search and graph layers were recorded: build it again to see them."}
      </p>

      <Layer
        n={1}
        title="The prompt"
        summary={`${p.prompt?.named.length ?? p.named_ids.length} id(s) named · embedded by ${p.prompt?.embedding?.model ?? b.embedding_model}`}
        open
      >
        <pre className="cgraph-text">{p.prompt?.text ?? b.query ?? ""}</pre>
        <dl className="pipe-kv">
          <dt>Ids named in it</dt>
          <dd>
            {(p.prompt?.named.map((x) => x.id) ?? p.named_ids).length ? (
              (p.prompt?.named.map((x) => x.id) ?? p.named_ids).map((x) => (
                <span key={x} className="chip">
                  {x}
                </span>
              ))
            ) : (
              <span className="muted">
                none: everything comes from search and the graph
              </span>
            )}{" "}
            <span className="muted">looked up exactly, and ranked first</span>
          </dd>
          {p.prompt?.embedding && (
            <>
              <dt>Its embedding</dt>
              <dd>
                {p.prompt.embedding.model} · {p.prompt.embedding.dims} numbers,
                the first {p.prompt.embedding.head.length}:{" "}
                <code>[{p.prompt.embedding.head.join(", ")}, …]</code>
                {p.prompt.embedding.top &&
                  p.prompt.embedding.top.length > 0 && (
                    <>
                      . {p.prompt.embedding.nonzero} of them are not zero; the
                      strongest (number: value):{" "}
                      <code>
                        {p.prompt.embedding.top
                          .map(([i, v]) => `#${i}: ${v}`)
                          .join(", ")}
                      </code>
                    </>
                  )}
                {p.prompt.embedding.model.startsWith("mock") && (
                  <span className="muted">
                    {" "}
                    (the test embedder: word counts hashed into a vector, not a
                    real meaning)
                  </span>
                )}
              </dd>
            </>
          )}
        </dl>
      </Layer>

      {p.search && (
        <Layer
          n={2}
          title="Search over the stored chunks"
          summary={`${p.search.chunks_searched} chunks searched · top ${p.search.fused.length} fused`}
        >
          <p className="help">
            Every chunk is ranked twice: by words (BM25) and by its
            embedding&rsquo;s cosine similarity to the prompt. The two ranks are
            fused:{" "}
            <code>
              score = {p.search.weights.keyword}/({p.search.rrf_k}+rank in
              keywords+1) + {p.search.weights.vector}/({p.search.rrf_k}+rank in
              vectors+1)
            </code>
            . A chunk that ranks well in both rises.
          </p>
          <h5>Fused: what the search returned</h5>
          <ChunkTable
            project={project}
            rows={p.search.fused}
            valueLabel="Fused score"
            value={(r) => f3(r.rrf ?? 0)}
            extra={(r) =>
              `keywords #${r.keyword_rank ?? "-"} · vectors #${r.vector_rank ?? "-"}`
            }
          />
          <h5>Keyword ranking (BM25)</h5>
          <ChunkTable
            project={project}
            rows={p.search.keyword}
            valueLabel="BM25 score"
            value={(r) => f3(r.score ?? 0)}
          />
          <h5>Vector ranking (cosine similarity to the prompt)</h5>
          <ChunkTable
            project={project}
            rows={p.search.vector}
            valueLabel="Cosine"
            value={(r) => (r.score ?? 0).toFixed(4)}
          />
        </Layer>
      )}

      <Layer
        n={3}
        title="Documents considered, reranked"
        summary={`${p.candidates.length} candidates · ${p.candidates.filter((c) => c.decision === "chosen").length} chosen`}
      >
        <p className="help">
          Each candidate document is rescored by how similar its best chunks are
          to the prompt, plus fixed bonuses (named in the prompt, found by
          search, linked to the best match, a story or requirement rather than a
          summary). Open a row for its chunks.
        </p>
        <p>
          {(Object.keys(DECISION) as BundleCandidate["decision"][]).map((d) => (
            <label key={d} className="chip">
              <input
                type="checkbox"
                checked={show[d]}
                onChange={() => setShow({ ...show, [d]: !show[d] })}
              />{" "}
              {DECISION[d]} (
              {p.candidates.filter((c) => c.decision === d).length})
            </label>
          ))}
        </p>
        <table className="pipe-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Document</th>
              <th>Found by</th>
              <th>Similarity</th>
              <th>Bonuses</th>
              <th>Score</th>
              <th>Fate</th>
            </tr>
          </thead>
          <tbody>
            {cands.map((c, i) => {
              const key = `${c.external_id}|${c.decision}`;
              const open = openCand === key;
              return (
                <CandidateRows
                  key={key}
                  c={c}
                  i={i}
                  open={open}
                  project={project}
                  onToggle={() => setOpenCand(open ? null : key)}
                />
              );
            })}
          </tbody>
        </table>
      </Layer>

      {p.expansion && (
        <Layer
          n={4}
          title="Graph nodes and relations pulled"
          summary={`${p.seeds.length} seeds · ${pulled.size} nodes reached`}
        >
          <p className="help">
            The graph is expanded from the strongest requirement-class matches
            (the seeds): their backlog structure two hops out, and the concepts
            and rules they mention one hop out. Every node reached and every
            link walked is listed; the badge says what became of the node.
          </p>
          {p.expansion.map((e, i) => {
            const all = allNodes[i];
            const nodes = all ? e.nodes : e.nodes.slice(0, 20);
            return (
              <div key={i} className="exp">
                <h5>
                  From <code>{e.seed}</code>{" "}
                  {e.seed === p.seeds[0] && (
                    <span className="chip st-done">best match</span>
                  )}
                  <span className="chip">
                    {e.link_types.length > 1
                      ? "structure"
                      : e.link_types.join(", ")}
                  </span>
                  <span className="chip">depth {e.depth}</span>
                  <span className="chip">
                    via {e.via?.startsWith("neo4j") ? "Neo4j" : "database"}
                  </span>
                  {e.truncated && (
                    <span className="chip st-warn">cut: too many nodes</span>
                  )}
                </h5>
                <p className="muted">
                  {e.nodes.length} node(s), {e.edges.length} link(s). Link types
                  allowed: {e.link_types.join(", ")}.
                </p>
                <table className="pipe-table">
                  <thead>
                    <tr>
                      <th>Hop</th>
                      <th>Node</th>
                      <th>Kind</th>
                      <th>What became of it</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodes.map((n) => {
                      const d = decisionOf.get(n.external_id);
                      return (
                        <tr key={n.external_id}>
                          <td>{n.hop ?? "-"}</td>
                          <td>
                            {n.external_id}{" "}
                            <span className="muted">{n.title}</span>
                          </td>
                          <td>{n.kind}</td>
                          <td>
                            {d ? (
                              <span className={`chip ${DECISION_CHIP[d]}`}>
                                {DECISION[d]}
                              </span>
                            ) : (
                              <span className="chip">
                                not a candidate (a concept or rule goes in as
                                evidence)
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {e.nodes.length > 20 && (
                  <button
                    type="button"
                    className="linklike"
                    onClick={() => setAllNodes({ ...allNodes, [i]: !all })}
                  >
                    {all
                      ? "Show fewer nodes"
                      : `Show all ${e.nodes.length} nodes`}
                  </button>
                )}
                {e.edges.length > 0 && (
                  <details>
                    <summary>The {e.edges.length} links walked</summary>
                    <table className="pipe-table">
                      <thead>
                        <tr>
                          <th>From</th>
                          <th>Link</th>
                          <th>To</th>
                          <th>Status</th>
                          <th>Evidence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {e.edges.map((x, k) => (
                          <tr key={k}>
                            <td>{x.from}</td>
                            <td>
                              <code>{x.type}</code>
                            </td>
                            <td>{x.to}</td>
                            <td>
                              <span className={`chip st-${x.status}`}>
                                {x.status}
                              </span>
                              {x.stale && (
                                <span className="chip st-stale">stale</span>
                              )}
                            </td>
                            <td className="muted">{x.evidence || x.origin}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </details>
                )}
              </div>
            );
          })}
        </Layer>
      )}

      <Layer
        n={5}
        title="Filter and budget"
        summary={`${p.candidates.filter((c) => c.decision === "dropped").length} dropped · ${p.candidates.filter((c) => c.decision === "over_budget").length} over budget`}
      >
        <dl className="pipe-kv">
          <dt>Relevance</dt>
          <dd>
            the closest requirement is{" "}
            {p.relevance ? p.relevance.best.toFixed(3) : "?"} similar to the
            prompt
            {p.relevance?.floor
              ? `; below ${p.relevance.floor.toFixed(2)} and with no id named, the context is "insufficient"`
              : "; no relevance floor is set"}
          </dd>
          <dt>Budget</dt>
          <dd>
            at most {p.budgets.max_items} items; per slot{" "}
            {Object.entries(p.budgets.per_slot)
              .map(([k, v]) => `${k} ${v}`)
              .join(", ")}
            ; per kind{" "}
            {Object.entries(p.budgets.per_kind)
              .map(([k, v]) => `${k} ${v}`)
              .join(", ")}
            , others {p.budgets.default_per_kind}
          </dd>
        </dl>
        <table className="pipe-table">
          <thead>
            <tr>
              <th>Document</th>
              <th>Fate</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {p.candidates
              .filter((c) => c.decision !== "chosen")
              .map((c) => (
                <tr key={`${c.external_id}|${c.decision}`}>
                  <td>{c.external_id}</td>
                  <td>
                    <span className={`chip ${DECISION_CHIP[c.decision]}`}>
                      {DECISION[c.decision]}
                    </span>
                  </td>
                  <td>{c.reason}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </Layer>

      <Layer
        n={6}
        title="What the agent received"
        summary={`${items.length} items · ${b.relationships?.length ?? 0} relationships · ${b.status}`}
      >
        <table className="pipe-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>Slot</th>
              <th>Version</th>
              <th>Given as</th>
              <th>Parts</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.source_id}>
                <td>
                  {i.external_id}{" "}
                  {i.target && <span className="chip st-done">target</span>}
                  <div className="muted">{i.title}</div>
                </td>
                <td>{i.slot}</td>
                <td>
                  v{i.version} <code>{i.content_hash?.slice(0, 8)}</code>
                </td>
                <td>{i.content_truncated ? i.excerpt_of : "the whole text"}</td>
                <td>
                  {(i.sections ?? []).length
                    ? i.sections!.map((s) => (
                        <div key={s.chunk_id}>
                          {s.heading || "(start)"}{" "}
                          <span className="muted">{f3(s.similarity)}</span>
                        </div>
                      ))
                    : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(b.relationships ?? []).length > 0 && (
          <details>
            <summary>
              The {b.relationships!.length} relationships between these items
            </summary>
            <ul className="pipe-notes">
              {b.relationships!.map((r, i) => (
                <li key={i}>
                  {r.from} <code>{r.type}</code> {r.to}{" "}
                  <span className={`chip st-${r.status}`}>{r.status}</span>
                  {r.stale && <span className="chip st-stale">stale</span>}{" "}
                  <span className="muted">{r.evidence}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        <dl className="pipe-kv">
          <dt>Sufficiency</dt>
          <dd>
            <span
              className={`chip st-${b.status === "sufficient" ? "done" : "failed"}`}
            >
              {b.status}
            </span>{" "}
            {b.sufficiency.missing.join("; ")}{" "}
            {b.sufficiency.contradictions
              ? "A contradiction is in the context."
              : ""}
          </dd>
          {b.sufficiency.notes.length > 0 && (
            <>
              <dt>Notes</dt>
              <dd>
                <ul className="pipe-notes">
                  {b.sufficiency.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </Layer>
    </div>
  );
}

function CandidateRows({
  c,
  i,
  open,
  project,
  onToggle,
}: {
  c: BundleCandidate;
  i: number;
  open: boolean;
  project: string;
  onToggle: () => void;
}) {
  return (
    <>
      <tr>
        <td>{i + 1}</td>
        <td>
          <button
            type="button"
            className="linklike"
            onClick={onToggle}
            aria-expanded={open}
          >
            {c.external_id}
          </button>{" "}
          <span className="chip">{c.kind}</span>
          {c.seed && <span className="chip st-done">seed</span>}
          {c.title && <div className="muted">{c.title}</div>}
        </td>
        <td>{c.found_by.map((f) => FOUND[f] ?? f).join(" + ")}</td>
        <td>
          {f3(c.similarity)} <Bar value={c.similarity} />
        </td>
        <td className="muted">
          {Object.entries(c.bonus)
            .filter(([, v]) => v)
            .map(([k, v]) => `${k.replace(/_/g, " ")} +${v.toFixed(2)}`)
            .join(", ") || "none"}
        </td>
        <td>{c.score != null ? f3(c.score) : "-"}</td>
        <td>
          <span className={`chip ${DECISION_CHIP[c.decision]}`}>
            {DECISION[c.decision]}
          </span>
          {c.reason && <div className="muted">{c.reason}</div>}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7}>
            {c.pulled_by && c.pulled_by !== c.external_id && (
              <p className="muted">
                Pulled in by the graph from <code>{c.pulled_by}</code>.
              </p>
            )}
            {c.chunks && c.chunks.length > 0 ? (
              <>
                <p className="muted">
                  Its best matching chunks (similarity of the chunk&rsquo;s
                  embedding to the prompt):
                </p>
                <ol className="drill-chunks">
                  {c.chunks.map((k) => (
                    <li key={k.chunk_id}>
                      <b>{k.heading || "(start)"}</b> · {f3(k.similarity)}{" "}
                      <Bar value={k.similarity} />
                      <pre className="cgraph-text">{k.text}</pre>
                    </li>
                  ))}
                </ol>
              </>
            ) : (
              <p className="muted">
                No chunk list was recorded for this candidate.
              </p>
            )}
            <details>
              <summary>
                Open the stored chunks, what was embedded and the vector
              </summary>
              <CapturedView
                project={project}
                refId={c.source_id ?? c.external_id}
              />
            </details>
          </td>
        </tr>
      )}
    </>
  );
}
