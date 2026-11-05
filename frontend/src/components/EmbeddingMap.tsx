import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type {
  EmbeddingMapData,
  EmbeddingPoint,
  KnowledgeSearchResult,
  Neighbors,
} from "../types";
import { Loading } from "./Spinner";

const KIND_COLOR: Record<string, string> = {
  brd: "#7c6bd6",
  doc: "#7c6bd6",
  requirement: "#2d5bd1",
  epic: "#b0489f",
  feature: "#1590a3",
  story: "#17877a",
  ticket: "#17877a",
  design: "#b7791f",
  code: "#2f8f4e",
  test: "#c4572a",
};
const W = 760;
const H = 440;
const msg = (e: unknown) =>
  e instanceof ApiError || e instanceof Error ? e.message : String(e);

/** Every embedded chunk as a dot on a 2-D map (similar texts sit close together), plus a search box that shows scores. */
export function EmbeddingMap({
  project,
  refreshKey,
}: {
  project: string;
  refreshKey: number;
}) {
  const [data, setData] = useState<EmbeddingMapData | null>(null);
  const [picked, setPicked] = useState<EmbeddingPoint | null>(null);
  const [near, setNear] = useState<Neighbors | null>(null);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!project) return;
    try {
      setData(await api.knowledgeEmbeddings(project));
      setError(null);
    } catch (e) {
      setError(msg(e));
    }
  }, [project]);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load, refreshKey]);

  async function pick(p: EmbeddingPoint) {
    setPicked(p);
    setNear(null);
    try {
      setNear(await api.chunkNeighbors(project, p.id));
    } catch (e) {
      setError(msg(e));
    }
  }
  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    try {
      setResult(await api.knowledgeSearch(project, query.trim()));
      setError(null);
    } catch (x) {
      setError(msg(x));
    }
  }
  const hitIds = new Set(result?.hits.flatMap((h) => h.chunk_ids) ?? []);
  const nearIds = new Set(near?.neighbors.map((n) => n.id) ?? []);
  const kinds = [...new Set(data?.points.map((p) => p.kind) ?? [])];

  return (
    <div className="embedding-map">
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}
      {data && data.points.length === 0 && (
        <p className="muted">
          No embeddings yet ({data.chunks} chunks stored). Add a document first.
        </p>
      )}
      {data && data.points.length > 0 && (
        <>
          <p className="muted">
            {data.embedded} chunks embedded with <b>{data.model}</b> (
            {data.dimensions} numbers each)
            {Object.keys(data.by_model).length > 1 &&
              ` · other models present: ${Object.entries(data.by_model)
                .map(([m, n]) => `${m} ${n}`)
                .join(", ")}`}
            {data.truncated && " · showing the first part only"}. Dots that sit
            close together are texts the model finds alike. Click a dot for its
            nearest neighbours.
          </p>
          <div className="row graph-legend">
            {kinds.map((k) => (
              <span key={k}>
                <i
                  className="lg dot"
                  style={{ background: KIND_COLOR[k] ?? "#888" }}
                />{" "}
                {k}
              </span>
            ))}
          </div>
          <div className="emb-layout">
            <svg
              className="emb"
              viewBox={`0 0 ${W} ${H}`}
              width="100%"
              role="img"
              aria-label={`Embedding map of ${data.points.length} chunks`}
            >
              <rect x={0} y={0} width={W} height={H} className="emb-bg" />
              {data.points.map((p) => {
                const hit = hitIds.has(p.id);
                const isNear = nearIds.has(p.id);
                return (
                  <circle
                    key={p.id}
                    cx={p.x * W}
                    cy={(1 - p.y) * H}
                    r={picked?.id === p.id ? 9 : hit || isNear ? 7 : 5}
                    fill={KIND_COLOR[p.kind] ?? "#888"}
                    className={`epoint${picked?.id === p.id ? " sel" : ""}${hit ? " hit" : ""}${isNear ? " near" : ""}`}
                    opacity={result && !hit ? 0.25 : 0.85}
                    tabIndex={0}
                    role="button"
                    aria-label={`${p.external_id}${p.heading ? `: ${p.heading}` : ""}`}
                    onClick={() => void pick(p)}
                    onKeyDown={(ev) =>
                      (ev.key === "Enter" || ev.key === " ") &&
                      (ev.preventDefault(), void pick(p))
                    }
                  >
                    <title>{`${p.external_id} (${p.kind})${p.heading ? `\n${p.heading}` : ""}\n${p.text.slice(0, 160)}`}</title>
                  </circle>
                );
              })}
            </svg>
            <aside className="card emb-side" aria-label="Chunk details">
              {!picked && <p className="muted">Click a dot.</p>}
              {picked && (
                <>
                  <h4>
                    {picked.external_id}{" "}
                    <span className="chip">{picked.kind}</span>
                  </h4>
                  {picked.heading && <p className="muted">{picked.heading}</p>}
                  <pre className="src-content">
                    {near?.chunk.text ?? picked.text}
                  </pre>
                  {near && (
                    <p className="help">
                      Vector: {near.chunk.dimensions} numbers (
                      {near.chunk.model}), starting [
                      {near.chunk.vector_head.join(", ")}, …]
                    </p>
                  )}
                  <h4>Nearest in meaning</h4>
                  {!near && <Loading>Loading…</Loading>}
                  <ol className="plain">
                    {near?.neighbors.map((n) => (
                      <li key={n.id}>
                        <b>{n.external_id}</b>{" "}
                        <span className="chip">{n.similarity.toFixed(3)}</span>{" "}
                        <span className="muted">
                          {n.heading || n.text.slice(0, 70)}
                        </span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </aside>
          </div>
        </>
      )}

      <form className="row" onSubmit={search} aria-label="Search the knowledge">
        <label className="grow">
          Try a search (semantic + keyword)
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="how many favourites can a user pin"
          />
        </label>
        <button type="submit">Search</button>
      </form>
      {result && (
        <div>
          <p className="help">
            {result.hits.length} sources, best first (score = fused rank;{" "}
            <i>matched by</i> says which search found it). Matching dots are
            highlighted on the map.
            {result.note && ` ${result.note}`}
          </p>
          <ol className="plain">
            {result.hits.map((h) => (
              <li key={h.source_id}>
                <b>{h.external_id}</b> <span className="chip">{h.kind}</span>{" "}
                <span className="chip">{h.score.toFixed(4)}</span>{" "}
                <span className="chip">{h.matched_by.join(" + ")}</span>{" "}
                <span className="muted">{h.snippet.slice(0, 120)}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
