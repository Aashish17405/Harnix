import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
  ProjectPipeline as Data,
  KnowledgeJob,
  CoverageReport,
  FindingsReport,
  MarkdownExport,
} from "../types";
import {
  CoveragePanel,
  ExportPanel,
  FindingsPanel,
  LinkPanel,
} from "./QualityPanels";
import { Loading } from "./Spinner";

/** Which stage card a reported step belongs to. */
const CARD_OF: Record<string, string> = {
  scan: "ingest", upload: "ingest", read: "ingest", embed: "chunks", extract: "extract",
  ontology: "ontology", link: "graph", suggest: "graph", infer: "infer", neo4j: "neo4j",
}; // prettier-ignore
const KIND_LABEL: Record<string, string> = {
  ingest: "Indexing a folder",
  upload: "Adding a document",
  extract: "Extracting concepts and rules",
  link: "Finding links between documents",
  rebuild: "Rebuilding chunks and embeddings",
};

const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString() : "never";
const sum = (o: Record<string, number>) =>
  Object.values(o).reduce((a, b) => a + b, 0);
const list = (o: Record<string, number>) =>
  Object.entries(o)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ");

const KINDS = [
  "doc",
  "brd",
  "requirement",
  "epic",
  "feature",
  "story",
  "ticket",
  "design",
];

/** Documents whose declared kind was not recognised: kept as `doc` until a person accepts or reclassifies them. */
function ReviewTable({
  items,
  onDone,
}: {
  items: NonNullable<Data["ingest"]["review"]>;
  onDone: () => void;
}) {
  const [pick, setPick] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!items.length) return <>none</>;
  async function decide(id: string, kind: string) {
    setBusy(id);
    setErr(null);
    try {
      await api.reviewKind(id, kind);
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      {items.length} document(s) declared a kind that is not known. They are
      kept as <code>doc</code> and searchable meanwhile; accept or choose a
      kind:
      <table className="pipe-table" aria-label="Documents needing review">
        <thead>
          <tr>
            <th>Document</th>
            <th>Declared kind</th>
            <th>Decision</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.source_id}>
              <td>
                {r.external_id}
                {r.title && r.title !== r.external_id ? ` · ${r.title}` : ""}
              </td>
              <td>{r.requested_kind ?? "?"}</td>
              <td>
                <select
                  aria-label={`Kind for ${r.external_id}`}
                  value={pick[r.source_id] ?? "doc"}
                  onChange={(e) =>
                    setPick({ ...pick, [r.source_id]: e.target.value })
                  }
                >
                  {KINDS.map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>{" "}
                <button
                  disabled={busy !== null}
                  aria-busy={busy === r.source_id}
                  onClick={() =>
                    void decide(r.source_id, pick[r.source_id] ?? "doc")
                  }
                >
                  Apply
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {err && <p className="banner banner-bad">{err}</p>}
    </>
  );
}

interface Step {
  key: string;
  title: string;
  value: string;
  sub: string;
  state: "done" | "warn" | "failed" | "skipped";
  detail: React.ReactNode;
}

/** The project's knowledge pipeline, left to right, with what each stage holds now and how it got there. */
export function ProjectPipeline({
  project,
  refreshKey,
  live,
}: {
  project: string;
  refreshKey?: unknown;
  live?: KnowledgeJob | null;
}) {
  const [d, setD] = useState<Data | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polled, setPolled] = useState<KnowledgeJob | null>(null);
  const [tick, setTick] = useState(0);
  const [reloads, setReloads] = useState(0); // bumped after a person's review decision
  // results are kept with the project they describe, so another project never shows them
  const [fs, setFs] = useState<{
    project: string;
    v: FindingsReport | null;
  } | null>(null);
  const [covS, setCovS] = useState<{
    project: string;
    v: CoverageReport;
  } | null>(null);
  const [expS, setExpS] = useState<{
    project: string;
    v: MarkdownExport;
  } | null>(null);
  const findings = fs?.project === project ? fs.v : null;
  const cov = covS?.project === project ? covS.v : null;
  const exp = expS?.project === project ? expS.v : null;
  // the job to show: the one this page started, else the newest one for the project (e.g. started from the CLI)
  const job = live ?? polled;
  const running = job?.status === "running";
  const jobKey = `${job?.id ?? ""}-${job?.status ?? ""}-${job?.events.length ?? 0}`;
  useEffect(() => {
    api.knowledgePipeline(project).then(
      (x) => (setD(x), setError(null)),
      (e: Error) => setError(e.message),
    );
  }, [project, refreshKey, jobKey, reloads]);
  useEffect(() => {
    api.knowledgeFindings(project).then(
      (v) => setFs({ project, v }),
      () => setFs({ project, v: null }),
    );
  }, [project, refreshKey, jobKey, reloads]);
  // watch for background work on this project: often while something runs, rarely otherwise
  useEffect(() => {
    let stop = false;
    const t = setTimeout(
      () =>
        api.knowledgeJobs(project).then(
          (r) => {
            if (stop) return;
            const newest = r.jobs[0] ?? null;
            const fresh =
              newest &&
              (newest.status === "running" ||
                Date.now() / 1000 - (newest.finished_at ?? 0) < 600);
            setPolled(fresh ? newest : null);
            setTick((n) => n + 1);
          },
          () => setTick((n) => n + 1),
        ),
      running ? 1200 : 8000,
    );
    return () => {
      stop = true;
      clearTimeout(t);
    };
  }, [project, running, tick]);
  if (error)
    return <p className="muted">Pipeline status unavailable: {error}</p>;
  if (!d) return <Loading>Loading the pipeline…</Loading>;

  const docs = sum(d.ingest.documents);
  const current = d.chunks.current ?? d.chunks.embedded;
  const li = d.ingest.last;
  const review = d.ingest.review ?? [];
  const ex = d.extraction.last?.detail;
  const steps: Step[] = [
    {
      key: "ingest",
      title: "Documents",
      value: String(docs),
      sub: list(d.ingest.documents) || "nothing yet",
      state: review.length ? "warn" : docs ? "done" : "skipped",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            Files are read, split (BRD sections, epics, features, one source per
            user story), given a namespace from their folder (BRD-02/US-001),
            versioned by content hash, and linked as they declare (story →
            feature → epic → BRD).
          </dd>
          <dt>By kind</dt>
          <dd>{list(d.ingest.documents) || "-"}</dd>
          <dt>States</dt>
          <dd>{list(d.ingest.states) || "-"}</dd>
          <dt>Last ingest</dt>
          <dd>
            {li
              ? `${when(li.at)} · ${li.detail.created} new, ${li.detail.updated} updated, ${li.detail.unchanged} unchanged${li.detail.failed?.length ? `, failed: ${li.detail.failed.join("; ")}` : ""}`
              : "never"}
          </dd>
          <dt>Needs review</dt>
          <dd>
            <ReviewTable
              items={review}
              onDone={() => setReloads((n) => n + 1)}
            />
          </dd>
        </dl>
      ),
    },
    {
      key: "chunks",
      title: "Chunks & embeddings",
      value: `${current}/${d.chunks.total}`,
      sub:
        current < d.chunks.embedded
          ? `${d.chunks.embedded - current} in an older format: rebuild`
          : `${d.chunks.vector_index === "pgvector" ? "pgvector" : "Python"} index`,
      state:
        d.chunks.total === 0
          ? "skipped"
          : current < d.chunks.total
            ? "warn"
            : "done",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            Each source is cut into chunks by heading and paragraph; every chunk
            gets an embedding vector in one batch per ingest. Search ranks
            chunks by cosine similarity
            {d.chunks.vector_index === "pgvector"
              ? " inside PostgreSQL (pgvector)"
              : ""}{" "}
            and by keywords (BM25), fused.
          </dd>
          <dt>Embedder</dt>
          <dd>
            <code>{d.chunks.embedder}</code>
          </dd>
          <dt>Embedded</dt>
          <dd>
            {d.chunks.embedded} of {d.chunks.total} chunks
            {d.chunks.embedded < d.chunks.total
              ? " (index the folder again to retry the rest)"
              : ""}
          </dd>
          <dt>By model</dt>
          <dd>{list(d.chunks.by_model) || "-"}</dd>
          <dt>Format</dt>
          <dd>
            Each chunk is embedded with its document&rsquo;s kind, id, title and
            section (<code>{d.chunks.current_format ?? d.chunks.embedder}</code>
            ). {current} of {d.chunks.total} chunks are in this format
            {current < d.chunks.embedded
              ? "; the others are not searched by vector until they are rebuilt"
              : ""}
            .{" "}
            <button
              type="button"
              onClick={() =>
                void api.startRebuild(project).then(() => setTick((n) => n + 1))
              }
              disabled={running}
            >
              Rebuild chunks and embeddings
            </button>
          </dd>
        </dl>
      ),
    },
    {
      key: "extract",
      title: "Concepts & rules",
      value: String(d.extraction.concepts + d.extraction.rules),
      sub: `${d.extraction.concepts} concepts · ${d.extraction.rules} rules`,
      state: d.extraction.last ? "done" : "skipped",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            The model reads each BRD, epic, feature and story (BRDs first) and
            lists the concepts, actors, data fields, events, systems, rules and
            limits it states, each with an exact quote. Anything whose quote is
            not in the document, or whose link the ontology does not allow, is
            dropped.
          </dd>
          <dt>Last run</dt>
          <dd>
            {d.extraction.last
              ? `${when(d.extraction.last.at)} · ${ex?.sources} documents read, ${ex?.skipped_unchanged} unchanged`
              : "never: press Extract concepts"}
          </dd>
          {ex && (
            <>
              <dt>Kept</dt>
              <dd>
                {ex.entities} concepts/rules, {ex.relations} relationships,{" "}
                {ex.mentions} "mentions" links
              </dd>
            </>
          )}
          {ex && (
            <>
              <dt>Dropped</dt>
              <dd>
                {ex.rejected_entities} entities and {ex.rejected_relations}{" "}
                relationships (no quote in the text, or not allowed by the
                ontology)
              </dd>
            </>
          )}
        </dl>
      ),
    },
    {
      key: "infer",
      title: "Implied links",
      value: String(sum(d.linking?.by_status ?? {})),
      sub: d.linking?.last
        ? list(d.linking.by_status) || "none proposed"
        : "not run yet",
      state: d.linking?.last
        ? d.linking.last.detail.failed?.length
          ? "warn"
          : "done"
        : "skipped",
      detail: (
        <LinkPanel
          project={project}
          d={d}
          running={running}
          onStarted={(j) => {
            setPolled(j);
            setTick((n) => n + 1);
          }}
        />
      ),
    },
    {
      key: "ontology",
      title: "Ontology check",
      value: `${d.ontology.violations_total + (d.ontology.refused_total ?? 0)} issues`,
      sub: `${d.ontology.links_checked} links checked`,
      state:
        d.ontology.violations_total || d.ontology.refused_total
          ? "warn"
          : "done",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            Every link written is checked against the ontology (
            {d.ontology.types} types with an is-a hierarchy,{" "}
            {d.ontology.relations} link types with the types each may join).
            File: <code>backend/harnix/knowledge/ontology.json</code>.
          </dd>
          <dt>Synonyms</dt>
          <dd>
            {Object.entries(d.ontology.synonyms)
              .map(([a, c]) => `${a} = ${c}`)
              .join(" · ") || "none (from the BRD glossaries)"}
          </dd>
          <dt>Stored links breaking it</dt>
          <dd>
            {d.ontology.violations_total
              ? d.ontology.violations
                  .map(
                    (v) =>
                      `${v.from} (${v.from_type}) ${v.type} ${v.to} (${v.to_type})`,
                  )
                  .join("; ")
              : "none"}
          </dd>
          <dt>Refused at intake</dt>
          <dd>
            {d.ontology.refused_total ? (
              <>
                {d.ontology.refused_total} declared link(s) were not stored
                because the ontology does not allow them:
                <table className="pipe-table" aria-label="Refused links">
                  <thead>
                    <tr>
                      <th>Declared in</th>
                      <th>Link</th>
                      <th>Points at</th>
                      <th>Why refused</th>
                      <th>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.ontology.refused ?? []).map((r) => (
                      <tr key={`${r.from}|${r.type}|${r.to}`}>
                        <td>
                          {r.from} ({r.from_type})
                        </td>
                        <td>{r.type}</td>
                        <td>
                          {r.to} ({r.to_type})
                        </td>
                        <td>{r.rule}</td>
                        <td>{when(r.at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : (
              "none"
            )}
          </dd>
        </dl>
      ),
    },
    {
      key: "graph",
      title: "Links (PostgreSQL)",
      value: String(d.graph.edges),
      sub: `${d.graph.by_status.verified ?? 0} verified · ${d.graph.by_status.proposed ?? 0} proposed`,
      state: d.graph.edges ? "done" : "skipped",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            Typed links are stored with their trust level: verified (declared by
            a document, or a name found verbatim), proposed (extracted or
            suggested, waiting for a person), rejected.
          </dd>
          <dt>By status</dt>
          <dd>{list(d.graph.by_status) || "-"}</dd>
          <dt>By origin</dt>
          <dd>{list(d.graph.by_origin) || "-"}</dd>
          <dt>By type</dt>
          <dd>{list(d.graph.by_type) || "-"}</dd>
        </dl>
      ),
    },
    {
      key: "neo4j",
      title: "Neo4j copy",
      value: d.neo4j.enabled
        ? d.neo4j.nodes != null
          ? `${d.neo4j.nodes} nodes`
          : "not built"
        : "off",
      sub: !d.neo4j.enabled
        ? "HARNIX_GRAPH_ENABLED=false"
        : d.neo4j.available === false
          ? "unreachable"
          : d.neo4j.current
            ? "up to date"
            : "out of date",
      state: !d.neo4j.enabled
        ? "skipped"
        : d.neo4j.available === false
          ? "failed"
          : d.neo4j.current
            ? "done"
            : "warn",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            The sources and non-rejected links are copied to Neo4j (labels
            :Story, :Epic, :BRD, :Rule…; ids and titles only, no text) after
            every ingest, extraction, review and task, and again before a
            traversal if the database changed. Retrieval walks the graph there.
          </dd>
          <dt>Copy</dt>
          <dd>
            {d.neo4j.enabled
              ? d.neo4j.available === false
                ? d.neo4j.detail
                : `${d.neo4j.nodes ?? 0} nodes, ${d.neo4j.edges ?? 0} relationships, rebuilt ${when(d.neo4j.synced_at)}`
              : "disabled"}
          </dd>
          <dt>Browse</dt>
          <dd>
            <code>http://localhost:7474</code> ·{" "}
            <code>{`MATCH (n:Knowledge {project: '${d.project}'})-[r]->(m) RETURN n, r, m LIMIT 300`}</code>
          </dd>
        </dl>
      ),
    },
    {
      key: "eval",
      title: "Evaluation",
      value: d.evaluation.retrieval
        ? `${Math.round((d.evaluation.retrieval.detail.bundles?.target_in_bundle ?? d.evaluation.retrieval.detail.hybrid?.["hit@5"] ?? 0) * 100)}%`
        : "-",
      sub: d.evaluation.retrieval
        ? d.evaluation.retrieval.detail.bundles
          ? "right story in the context"
          : "hit@5 of search"
        : "not measured yet",
      state: d.evaluation.retrieval ? "done" : "skipped",
      detail: (
        <dl className="pipe-kv">
          <dt>What happens</dt>
          <dd>
            Questions with known answers are run through search and through the
            full context builder: is the right story found, how high, are its
            rules included, how much comes from the right backlog. Run:{" "}
            <code>
              forge knowledge eval CASES.json --project {d.project} --bundles
            </code>
            .
          </dd>
          {d.evaluation.retrieval && (
            <>
              <dt>Latest</dt>
              <dd>
                {when(d.evaluation.retrieval.at)} ·{" "}
                {d.evaluation.retrieval.detail.cases} questions · search hit@1{" "}
                {d.evaluation.retrieval.detail.hybrid?.["hit@1"]}, hit@5{" "}
                {d.evaluation.retrieval.detail.hybrid?.["hit@5"]}, MRR{" "}
                {d.evaluation.retrieval.detail.hybrid?.mrr}
                {d.evaluation.retrieval.detail.bundles
                  ? ` · context: right story ${d.evaluation.retrieval.detail.bundles.target_in_bundle}, rules included ${d.evaluation.retrieval.detail.bundles.rules_included}, right backlog ${d.evaluation.retrieval.detail.bundles.same_backlog}`
                  : ""}
              </dd>
            </>
          )}
          <dt>Context bundles built</dt>
          <dd>{d.evaluation.bundles_built}</dd>
        </dl>
      ),
    },
    {
      key: "findings",
      title: "Findings",
      value: findings ? String(findings.count) : "-",
      sub: findings
        ? findings.count
          ? list(findings.by_kind).replaceAll("_", " ")
          : "no problems in the documents"
        : "loading",
      state: !findings ? "skipped" : findings.count ? "warn" : "done",
      detail: <FindingsPanel report={findings} />,
    },
    {
      key: "coverage",
      title: "Coverage",
      value:
        cov?.inputs?.score != null
          ? `${Math.floor(cov.inputs.score * 100)}%`
          : cov
            ? "ok"
            : "-",
      sub: cov
        ? cov.inputs
          ? `${cov.inputs.mapped} of ${cov.inputs.datapoints} values found`
          : `${cov.chunks.uncovered} lines outside chunks`
        : "not checked yet",
      state: !cov
        ? "skipped"
        : cov.chunks.uncovered || (cov.inputs?.missing_total ?? 0)
          ? "warn"
          : "done",
      detail: (
        <CoveragePanel
          project={project}
          result={cov}
          onResult={(v) => setCovS({ project, v })}
        />
      ),
    },
    {
      key: "export",
      title: "Markdown copy",
      value: exp ? String(exp.files) : "-",
      sub: exp
        ? `${exp.written} written, ${exp.unchanged} unchanged`
        : "not written yet",
      state: exp ? "done" : "skipped",
      detail: (
        <ExportPanel
          project={project}
          result={exp}
          onResult={(v) => setExpS({ project, v })}
        />
      ),
    },
  ];
  const cur = steps.find((s) => s.key === open);
  // what the current (or just finished) job said, per stage card
  const lastOf: Record<string, KnowledgeJob["events"][number]> = {};
  for (const e of job?.events ?? [])
    if (CARD_OF[e.stage]) lastOf[CARD_OF[e.stage]] = e;
  const activeCard = running && job ? CARD_OF[job.stage] : undefined;
  return (
    <div className="ppipe" aria-label="Knowledge pipeline">
      <ol className="ppipe-row">
        {steps.map((s) => {
          const ev = lastOf[s.key];
          const liveState = s.key === activeCard ? "live" : ev ? "fresh" : "";
          const pct =
            ev && typeof ev.data.done === "number" && ev.data.total
              ? Math.min(100, (100 * ev.data.done) / ev.data.total)
              : null;
          return (
            <li key={s.key}>
              <button
                type="button"
                className={`ppipe-card st-${s.state} ${liveState}`}
                aria-expanded={open === s.key}
                aria-busy={liveState === "live"}
                onClick={() => setOpen(open === s.key ? null : s.key)}
              >
                <span className="ppipe-title">
                  {s.title}
                  {liveState === "live" && (
                    <span className="ppipe-pulse" aria-label="running" />
                  )}
                </span>
                <span className="ppipe-value">{s.value}</span>
                <span className="ppipe-sub">{ev ? ev.message : s.sub}</span>
                {pct != null && (
                  <span
                    className="progress"
                    role="progressbar"
                    aria-valuenow={Math.round(pct)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <i style={{ width: `${pct}%` }} />
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
      {job && <ActivityLog job={job} />}
      {cur ? (
        <div className="ppipe-detail">{cur.detail}</div>
      ) : (
        <p className="help">
          Click a stage to see exactly what happens in it and what it holds now.
        </p>
      )}
    </div>
  );
}

/** Every step the job reported, as it happens. */
function ActivityLog({ job }: { job: KnowledgeJob }) {
  const box = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [job.events.length]);
  const t0 = job.started_at;
  const elapsed =
    (job.finished_at ?? job.events[job.events.length - 1]?.at ?? t0) - t0;
  const state =
    job.status === "running"
      ? "running"
      : job.status === "done"
        ? "done"
        : "failed";
  return (
    <div className={`ppipe-log ${job.status}`} aria-live="polite">
      <div className="ppipe-log-head">
        <b>{KIND_LABEL[job.kind] ?? job.kind}</b>
        <span className="muted">{job.title}</span>
        <span className={`chip st-${state}`}>
          {job.status === "running"
            ? "running"
            : job.status === "done"
              ? "finished"
              : "failed"}
        </span>
        <span className="chip">{elapsed.toFixed(1)} s</span>
        {job.total > 0 && job.status === "running" && (
          <span className="chip">
            {job.done}/{job.total}
          </span>
        )}
      </div>
      <ol ref={box} className="ppipe-events">
        {job.events.map((e, i) => (
          <li key={i} className={`ev-${e.stage}`}>
            <span className="ppipe-t">+{(e.at - t0).toFixed(1)}s</span>
            <span className="chip">{e.stage}</span>
            <span>{e.message}</span>
          </li>
        ))}
        {job.status === "running" && <li className="ppipe-wait">working…</li>}
      </ol>
      {job.error && <p className="banner banner-bad">{job.error}</p>}
    </div>
  );
}
