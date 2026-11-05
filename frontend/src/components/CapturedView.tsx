import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { CapturedChunk, CapturedSource } from "../types";
import { Loading } from "./Spinner";

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The document with every chunk marked in it; text outside any chunk is shown dimmed (it was not embedded). */
function Marked({
  content,
  chunks,
  picked,
  onPick,
}: {
  content: string;
  chunks: CapturedChunk[];
  picked: string | null;
  onPick: (id: string) => void;
}) {
  const pieces = useMemo(() => {
    const out: { text: string; chunk?: CapturedChunk }[] = [];
    let at = 0;
    const placed = chunks
      .filter((c) => c.start !== null && c.end !== null)
      .sort((a, b) => (a.start as number) - (b.start as number));
    for (const c of placed) {
      const s = Math.max(c.start as number, at);
      const e = Math.min(c.end as number, content.length);
      if (e <= s) continue;
      if (s > at) out.push({ text: content.slice(at, s) });
      out.push({ text: content.slice(s, e), chunk: c });
      at = e;
    }
    if (at < content.length) out.push({ text: content.slice(at) });
    return out;
  }, [content, chunks]);
  return (
    <pre className="cap-doc" aria-label="Document text with its chunks marked">
      {pieces.map((p, i) =>
        p.chunk ? (
          <mark
            key={i}
            id={`cap-${p.chunk.id}`}
            className={`cap-chunk cap-c${p.chunk.ordinal % 2}${picked === p.chunk.id ? " cap-picked" : ""}`}
            onClick={() => onPick((p.chunk as CapturedChunk).id)}
            title={`Chunk ${p.chunk.ordinal + 1}: click to see what was embedded`}
          >
            <sup className="cap-no">{p.chunk.ordinal + 1}</sup>
            {p.text}
          </mark>
        ) : (
          <span
            key={i}
            className="cap-gap"
            title="Not inside any chunk: not embedded"
          >
            {p.text}
          </span>
        ),
      )}
    </pre>
  );
}

/** What ingesting one document produced: its chunks (where they sit in the text, exactly what was embedded and the
 * vector's start), the relationships and concepts around it, and its node in Neo4j. Loaded from what is stored. */
export function CapturedView({
  project,
  refId,
}: {
  project: string;
  refId: string;
}) {
  const [d, setD] = useState<CapturedSource | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.knowledgeCaptured(project, refId).then(
      (r) => alive && setD(r),
      (e) => alive && setErr(msg(e)),
    );
    return () => {
      alive = false;
    };
  }, [project, refId]);

  if (err)
    return (
      <p className="banner banner-bad" role="alert">
        {err}
      </p>
    );
  if (!d) return <Loading>Loading what was captured…</Loading>;

  const pick = (id: string) => {
    setPicked(id);
    document
      .getElementById(`cap-${id}`)
      ?.scrollIntoView?.({ block: "nearest" });
  };
  const embedded = d.chunks.filter((c) => c.embedded).length;
  const unplaced = d.chunks.filter((c) => c.start === null).length;
  const pct = d.length ? Math.round((100 * d.covered_chars) / d.length) : 0;
  const concepts = d.links.filter(
    (l) =>
      l.direction === "out" &&
      l.type === "mentions" &&
      l.other &&
      ["concept", "rule"].includes(l.other.kind),
  );
  const other = d.links.filter((l) => !concepts.includes(l));
  const declared = (d.metadata.declared ?? {}) as Record<string, string[]>;
  const node = d.neo4j.node;

  return (
    <div
      className="cap"
      aria-label={`What was captured from ${d.source.external_id}`}
    >
      <p className="help">
        <b>{d.source.external_id}</b> v{d.source.version} ({d.source.node_type},
        from {d.source.origin}), {d.length.toLocaleString()} characters,{" "}
        <b>{d.chunks.length}</b> chunks, <b>{embedded}</b> embedded. {pct}% of
        the text is inside a chunk
        {pct < 100 ? "; the dimmed text is not embedded" : ""}.
        {d.source.state !== "ok" && (
          <span className="chip st-stale">indexing: {d.source.state}</span>
        )}
        {d.source.error && (
          <span className="banner banner-bad">{d.source.error}</span>
        )}
      </p>

      <h4>1. The document and its chunks</h4>
      {d.truncated && (
        <p className="muted">Showing the first 300,000 characters.</p>
      )}
      {d.chunks.length === 0 ? (
        <p className="muted">This version has no chunks (nothing to embed).</p>
      ) : (
        <div className="cap-split">
          <Marked
            content={d.content}
            chunks={d.chunks}
            picked={picked}
            onPick={pick}
          />
          <ol className="cap-chunks" aria-label="Chunks">
            {d.chunks.map((c) => (
              <li
                key={c.id}
                className={picked === c.id ? "cap-picked" : undefined}
              >
                <button
                  type="button"
                  className="cap-chunk-head"
                  aria-expanded={picked === c.id}
                  onClick={() =>
                    picked === c.id ? setPicked(null) : pick(c.id)
                  }
                >
                  <b>#{c.ordinal + 1}</b>{" "}
                  {c.heading || <span className="muted">(no heading)</span>}{" "}
                  <span className="muted">
                    {c.chars} chars
                    {c.start !== null ? ` · text ${c.start}-${c.end}` : ""}
                  </span>{" "}
                  <span
                    className={`chip ${c.embedded ? "st-verified" : "st-stale"}`}
                  >
                    {c.embedded
                      ? `embedded · ${c.dimensions}d`
                      : "not embedded"}
                  </span>
                </button>
                {picked === c.id && (
                  <div className="cap-chunk-body">
                    <p className="help">
                      Exactly what was sent to the embedding model:
                    </p>
                    <pre className="mcp-pre">{c.embedded_text}</pre>
                    {c.embedded ? (
                      <p className="help">
                        Stored vector from <code>{c.model}</code>:{" "}
                        {c.dimensions} numbers, norm {c.norm}, starting{" "}
                        <code>[{c.vector_head.join(", ")}, …]</code>
                      </p>
                    ) : (
                      <p className="help">
                        No vector stored for this chunk (the source is left
                        partial and retried on the next run).
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
      {unplaced > 0 && (
        <p className="help">
          {unplaced} chunk{unplaced === 1 ? "" : "s"} could not be placed in the
          text (joined pieces or redacted text); they are still listed.
        </p>
      )}

      <h4>2. Links in the graph</h4>
      {Object.keys(declared).length > 0 && (
        <p className="help">
          Declared by the document:{" "}
          {Object.entries(declared).map(([t, refs]) => (
            <span key={t}>
              <span className="ing-rel">{t}</span> {refs.join(", ")}{" "}
            </span>
          ))}
        </p>
      )}
      {other.length === 0 ? (
        <p className="muted">No links to or from this item.</p>
      ) : (
        <ul className="ing-links">
          {other.map((l) => (
            <li key={l.id} title={l.evidence}>
              {l.direction === "out" ? "→" : "←"}{" "}
              <span className="ing-rel">{l.type}</span>{" "}
              <code>{l.other?.external_id ?? "?"}</code>{" "}
              {l.other?.title && <span className="muted">{l.other.title}</span>}{" "}
              <span className={`chip st-${l.status}`}>{l.status}</span>{" "}
              <span className="muted">origin: {l.origin}</span>
            </li>
          ))}
        </ul>
      )}

      {concepts.length > 0 && (
        <>
          <h4>3. Concepts and rules read out of it</h4>
          <ul className="ing-links">
            {concepts.map((l) => (
              <li key={l.id}>
                <span className="chip">{l.other?.node_type}</span>{" "}
                <b>{l.other?.title}</b>{" "}
                <span className={`chip st-${l.status}`}>{l.status}</span>{" "}
                <q className="muted">{l.evidence}</q>
              </li>
            ))}
          </ul>
        </>
      )}

      <h4>{concepts.length > 0 ? "4" : "3"}. Its node in Neo4j</h4>
      {!d.neo4j.enabled ? (
        <p className="muted">
          Neo4j is off (HARNIX_GRAPH_ENABLED): the graph stays in PostgreSQL
          only.
        </p>
      ) : d.neo4j.error ? (
        <p className="banner banner-bad">{d.neo4j.error}</p>
      ) : !node ? (
        <p className="muted">Not in Neo4j yet.</p>
      ) : (
        <>
          <div className="ing-counts">
            {node.labels.map((l) => (
              <span key={l} className="chip">
                :{l}
              </span>
            ))}
          </div>
          <dl className="pipe-kv">
            {Object.entries(node.properties).map(([k, v]) => (
              <span key={k} className="cap-prop">
                <dt>{k}</dt>
                <dd>{String(v ?? "")}</dd>
              </span>
            ))}
          </dl>
          <ul className="ing-links">
            {node.relationships.length === 0 && (
              <li className="muted">No relationships on the node.</li>
            )}
            {node.relationships.map((r, i) => (
              <li key={i}>
                {r.outgoing ? "-" : "<-"}[:{r.type}]{r.outgoing ? "->" : "-"}{" "}
                <code>{r.other}</code> <span className="chip">{r.kind}</span>{" "}
                <span className="muted">
                  {r.status} · {r.origin}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
