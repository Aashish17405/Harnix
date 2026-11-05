import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { KnowledgeJob, KnowledgeProject } from "../types";
import { CapturedView } from "./CapturedView";
import { toBase64 } from "./KnowledgePage";

type Source = "documents" | "repository" | "jira_mock" | "file";
type Ev = KnowledgeJob["events"][number];

interface Entity {
  kind: string;
  external_id: string;
  title: string;
  status: "created" | "updated" | "unchanged";
  version: number;
  chunks: number;
  chars: number;
  sections: string[];
  declares: Record<string, string[]>;
}
interface Link {
  from: string;
  type: string;
  to: string;
  status: "verified" | "proposed";
  how: string;
}

const SOURCES: { value: Source; label: string; hint: string }[] = [
  { value: "documents", label: "Folder of documents", hint: "BRDs, epics, stories, requirements, design notes: .md, .txt, .docx and any .json (backlogs, Jira-style exports, other data)" },
  { value: "repository", label: "Git repository", hint: "code and tests become sources; `Implements: REQ-1` comments become links" },
  { value: "jira_mock", label: "Jira export (JSON)", hint: "tickets with their links (mock format)" },
  { value: "file", label: "One file", hint: "upload a BRD, story or domain document" },
]; // prettier-ignore

/** Ingestion, live: what each document becomes and where it is written (PostgreSQL rows, embeddings, graph links, Neo4j). */
export function IngestionPage() {
  const [projects, setProjects] = useState<KnowledgeProject[]>([]);
  const [project, setProject] = useState("");
  const [source, setSource] = useState<Source>("documents");
  const [path, setPath] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("brd");
  const [jobs, setJobs] = useState<KnowledgeJob[]>([]);
  const [job, setJob] = useState<KnowledgeJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.knowledgeProjects().then(
      (r) => (
        setProjects(r.projects),
        setProject((p) => p || r.projects[0]?.project || "")
      ),
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(() => {
    if (!project.trim()) return;
    api.knowledgeJobs(project).then(
      (r) => setJobs(r.jobs.filter((j) => j.kind !== "rebuild")),
      () => setJobs([]),
    );
  }, [project, job?.status]);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const p = project.trim();
    if (!p) return setError("Name the project the documents belong to.");
    try {
      let j: KnowledgeJob;
      if (source === "file") {
        if (!file) return setError("Choose a file.");
        j = await api.startUpload({
          project: p,
          kind,
          filename: file.name,
          data_base64: await toBase64(file),
        });
      } else {
        if (!path.trim())
          return setError(
            "Give the full path of the folder or file on the server.",
          );
        j = await api.startIngest(p, source, path.trim());
      }
      setJob(j);
      await api.waitJob(j, setJob).catch(() => undefined); // a failed job is shown in place
      api.knowledgeProjects().then(
        (r) => setProjects(r.projects),
        () => undefined,
      );
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const running = job?.status === "running";
  const hint = SOURCES.find((s) => s.value === source)?.hint;

  return (
    <div className="ing-page">
      <section className="card ing-intro">
        <h1>Ingestion</h1>
        <p className="help">
          Watch documents become knowledge. Each file is read and split into
          items (a BRD into its requirements, a backlog into epics, features and
          stories). Each item is stored as a versioned row and cut into chunks,
          and every chunk is embedded. Links the documents declare become
          verified relationships, and likely links are proposed for you to
          confirm. The graph is then copied to Neo4j. This page shows each of
          those steps as it happens and what it wrote.
        </p>
        <form className="ing-form" onSubmit={start}>
          <label>
            Project
            <input
              list="ing-projects"
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder="e.g. payments"
            />
            <datalist id="ing-projects">
              {projects.map((p) => (
                <option key={p.project} value={p.project} />
              ))}
            </datalist>
          </label>
          <label>
            What to ingest
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as Source)}
            >
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {source === "file" ? (
            <>
              <label>
                It is a
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  {["brd", "story", "requirement", "design", "doc"].map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label className="ing-wide">
                File (.md, .txt, .docx)
                <input
                  type="file"
                  accept=".md,.txt,.docx,.markdown,.json"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </label>
            </>
          ) : (
            <label className="ing-wide">
              Path on the server
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={
                  source === "jira_mock"
                    ? "C:\\data\\jira.json"
                    : "C:\\data\\testing_docs"
                }
                spellCheck={false}
              />
            </label>
          )}
          <button type="submit" className="primary" disabled={running} aria-busy={running}>
            {running ? "Ingesting…" : "Start ingestion"}
          </button>
        </form>
        {hint && (
          <p className="help">
            {hint}. Unchanged documents are recognised by their hash and
            skipped.
          </p>
        )}
        {error && (
          <p className="banner banner-bad" role="alert">
            {error}
          </p>
        )}
        {jobs.length > 0 && (
          <div className="ing-jobs">
            <span className="muted">Recent runs in {project}:</span>
            {jobs.map((j) => (
              <button
                key={j.id}
                type="button"
                className="chip"
                aria-pressed={j.id === job?.id}
                onClick={() =>
                  j.status === "running"
                    ? void api.waitJob(j, setJob).catch(() => undefined)
                    : setJob(j)
                }
              >
                {j.title} · {j.status} ·{" "}
                {new Date(j.started_at * 1000).toLocaleTimeString()}
              </button>
            ))}
          </div>
        )}
      </section>
      {job ? (
        <JobView job={job} />
      ) : (
        <p className="muted ing-empty">
          Start an ingestion, or pick a recent run, to see what was captured and
          where it was stored. Runs are kept in memory until the backend
          restarts.
        </p>
      )}
    </div>
  );
}

/** Everything one job reported, grouped by where it went. */
function JobView({ job }: { job: KnowledgeJob }) {
  const ev = useMemo(() => {
    const by = (st: string) => job.events.filter((e) => e.stage === st);
    const reads = by("read").filter((e) => Array.isArray(e.data.entities));
    const embeds = by("embed");
    const links = [...by("link"), ...by("suggest")].flatMap(
      (e) => (e.data.links as Link[] | undefined) ?? [],
    );
    return {
      reads,
      entities: reads.flatMap((e) => e.data.entities as Entity[]),
      embedProgress: [...embeds]
        .reverse()
        .find((e) => typeof e.data.total === "number"),
      sample: embeds.find((e) => e.data.sample),
      embedFailed: embeds
        .filter((e) => e.data.failed)
        .reduce((n, e) => n + Number(e.data.failed), 0),
      links,
      missing: (by("link").at(-1)?.data.missing as string[] | undefined) ?? [],
      ontology: by("ontology").at(-1),
      pg: by("postgres"),
      neo4j: by("neo4j").at(-1),
      extract: by("extract").filter((e) => Array.isArray(e.data.concepts)),
    };
  }, [job.events]);

  const created = ev.entities.filter((x) => x.status === "created").length;
  const updated = ev.entities.filter((x) => x.status === "updated").length;
  const chunks = ev.entities.reduce((n, x) => n + x.chunks, 0);
  const pct = job.total
    ? Math.round((100 * job.done) / job.total)
    : job.status === "running"
      ? 0
      : 100;
  const secs =
    Math.round(
      ((job.finished_at ?? job.events.at(-1)?.at ?? job.started_at) -
        job.started_at) *
        10,
    ) / 10;

  return (
    <div className="ing-job" aria-live="polite">
      <section className="card ing-head">
        <div className="row">
          <strong>{job.title}</strong>
          <span className={`chip st-${job.status}`}>{job.status}</span>
          <span className="muted">
            step: {job.stage} · {secs} s
          </span>
        </div>
        <div
          className="ing-bar"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span style={{ width: `${pct}%` }} />
        </div>
        {job.error && <p className="banner banner-bad">{job.error}</p>}
        <Flow job={job} />
      </section>

      <div className="ing-stores">
        <StoreCard
          title="PostgreSQL"
          sub={`${ev.pg[0]?.data.dialect ?? "database"}: the registry, the source of truth`}
        >
          <Postgres pg={ev.pg} />
        </StoreCard>
        <StoreCard
          title="Embeddings"
          sub="one vector per chunk, for semantic search"
        >
          {ev.sample ? (
            <>
              <dl className="pipe-kv">
                <dt>model</dt>
                <dd>{String(ev.sample.data.model)}</dd>
                <dt>dimensions</dt>
                <dd>{String(ev.sample.data.dims)}</dd>
                <dt>chunks embedded</dt>
                <dd>
                  {String(ev.embedProgress?.data.done ?? "?")} /{" "}
                  {String(ev.embedProgress?.data.total ?? "?")}
                  {ev.embedFailed
                    ? ` · ${ev.embedFailed} failed (source left partial, retried next run)`
                    : ""}
                </dd>
                <dt>stored in</dt>
                <dd>
                  <code>knowledge_chunks.embedding</code> (pgvector copies it
                  into <code>embedding_vec</code> before a search)
                </dd>
              </dl>
              <details open>
                <summary>One chunk, exactly as it was embedded</summary>
                <pre className="mcp-pre">
                  {String(
                    (ev.sample.data.sample as { embedded_text: string })
                      .embedded_text,
                  )}
                </pre>
                <p className="help">
                  First values of its vector:{" "}
                  <code>
                    [
                    {(
                      ev.sample.data.sample as { vector_head: number[] }
                    ).vector_head.join(", ")}
                    , …]
                  </code>
                  . The first line names the item, so the same section heading
                  in two documents does not look alike.
                </p>
              </details>
            </>
          ) : (
            <p className="muted">
              {job.status === "running"
                ? "Waiting for the embedding step…"
                : "Nothing new to embed (every document was unchanged)."}
            </p>
          )}
        </StoreCard>
        <StoreCard
          title="Graph"
          sub="relationships in PostgreSQL, mirrored to Neo4j"
        >
          <Graph
            links={ev.links}
            missing={ev.missing}
            ontology={ev.ontology}
            neo4j={ev.neo4j}
          />
        </StoreCard>
      </div>

      <section className="card">
        <h2>What was extracted</h2>
        <p className="help">
          {ev.entities.length} items from {ev.reads.length} file
          {ev.reads.length === 1 ? "" : "s"}: {created} new, {updated} changed
          (new version), {ev.entities.length - created - updated} unchanged;{" "}
          {chunks} chunks cut.
        </p>
        {ev.reads.length === 0 && <p className="muted">Nothing read yet.</p>}
        {ev.reads.map((r, i) => (
          <FileRow key={i} r={r} project={job.project} />
        ))}
      </section>

      {ev.extract.length > 0 && (
        <section className="card">
          <h2>Concepts and rules read out of the documents</h2>
          {ev.extract.map((e, i) => (
            <Concepts key={i} e={e} />
          ))}
        </section>
      )}

      <details className="card">
        <summary>Every step as reported ({job.events.length})</summary>
        <ol className="ing-log">
          {job.events.map((e, i) => (
            <li key={i}>
              <code>{e.stage}</code> {e.message}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

const FLOW: { stage: string; label: string }[] = [
  { stage: "postgres", label: "Count rows" },
  { stage: "scan", label: "Find files" },
  { stage: "read", label: "Read & split" },
  { stage: "embed", label: "Embed chunks" },
  { stage: "link", label: "Link" },
  { stage: "ontology", label: "Check ontology" },
  { stage: "neo4j", label: "Mirror to Neo4j" },
];

function Flow({ job }: { job: KnowledgeJob }) {
  const seen = new Set(job.events.map((e) => e.stage));
  if (job.kind === "upload") seen.add("scan");
  return (
    <ol className="ing-flow" aria-label="Steps">
      {FLOW.map((f) => {
        const state =
          job.stage === f.stage && job.status === "running"
            ? "now"
            : seen.has(f.stage)
              ? "done"
              : "todo";
        return (
          <li key={f.stage} className={`ing-step ing-step-${state}`}>
            {f.label}
          </li>
        );
      })}
    </ol>
  );
}

function StoreCard({
  title,
  sub,
  children,
}: {
  title: string;
  sub: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card ing-store">
      <h2>{title}</h2>
      <p className="muted ing-sub">{sub}</p>
      {children}
    </section>
  );
}

const TABLES: [string, string][] = [
  ["knowledge_sources", "one row per item (BRD, requirement, story, file…)"],
  ["knowledge_versions", "the full text of each version, by content hash"],
  ["knowledge_chunks", "searchable pieces of the current version"],
  ["chunks_with_embedding", "chunks that have their vector"],
  ["knowledge_edges", "relationships, verified or proposed"],
  ["knowledge_audit", "who did what, when"],
];

function Postgres({ pg }: { pg: Ev[] }) {
  if (!pg.length)
    return <p className="muted">No counts reported for this run.</p>;
  const before = pg[0].data.rows as Record<string, number>;
  const after =
    pg.length > 1
      ? (pg[pg.length - 1].data.rows as Record<string, number>)
      : null;
  return (
    <table className="pipe-table ing-table">
      <thead>
        <tr>
          <th>table</th>
          <th>before</th>
          <th>written</th>
          <th>now</th>
        </tr>
      </thead>
      <tbody>
        {TABLES.map(([t, what]) => {
          const d = after ? after[t] - before[t] : null;
          return (
            <tr key={t} title={what}>
              <td>
                <code>{t}</code>
              </td>
              <td>{before[t]}</td>
              <td className={d ? "ing-delta" : undefined}>
                {d === null ? "…" : d > 0 ? `+${d}` : d}
              </td>
              <td>{after ? after[t] : "…"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Graph({
  links,
  missing,
  ontology,
  neo4j,
}: {
  links: Link[];
  missing: string[];
  ontology?: Ev;
  neo4j?: Ev;
}) {
  const verified = links.filter((l) => l.status === "verified");
  const proposed = links.filter((l) => l.status === "proposed");
  const n = neo4j?.data as { nodes?: number; edges?: number; labels?: Record<string, number>; relationships?: Record<string, number>; error?: string } | undefined; // prettier-ignore
  return (
    <>
      <p>
        <strong>{verified.length}</strong> links declared by the documents
        (verified), <strong>{proposed.length}</strong> proposed for you to
        confirm.
      </p>
      {links.length > 0 && (
        <ul className="ing-links">
          {links.slice(0, 60).map((l, i) => (
            <li key={i} title={l.how}>
              <code>{l.from}</code> <span className="ing-rel">{l.type}</span>{" "}
              <code>{l.to}</code>
              {l.status === "proposed" && (
                <span className="chip st-proposed">proposed</span>
              )}
            </li>
          ))}
          {links.length > 60 && (
            <li className="muted">and {links.length - 60} more</li>
          )}
        </ul>
      )}
      {missing.length > 0 && (
        <details>
          <summary>{missing.length} references not found (yet)</summary>
          <ul className="ing-links">
            {missing.map((m) => (
              <li key={m}>
                <code>{m}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
      {ontology && <p className="help">Ontology: {ontology.message}</p>}
      <h3 className="ing-h3">Neo4j</h3>
      {!neo4j ? (
        <p className="muted">Not reached yet.</p>
      ) : n?.labels ? (
        <>
          <p className="help">
            {n.nodes} nodes and {n.edges} relationships for this project after
            the sync.
          </p>
          <div className="ing-counts">
            {Object.entries(n.labels).map(([k, v]) => (
              <span key={k} className="chip">
                :{k[0].toUpperCase() + k.slice(1)} {v}
              </span>
            ))}
          </div>
          <div className="ing-counts">
            {Object.entries(n.relationships ?? {}).map(([k, v]) => (
              <span key={k} className="chip">
                [:{k}] {v}
              </span>
            ))}
          </div>
        </>
      ) : (
        <p className={n?.error ? "banner banner-bad" : "muted"}>
          {neo4j.message}
        </p>
      )}
    </>
  );
}

function FileRow({ r, project }: { r: Ev; project: string }) {
  const ents = r.data.entities as Entity[];
  const [open, setOpen] = useState<string | null>(null);
  return (
    <details className="ing-file" open={ents.length <= 12}>
      <summary>
        <code>{String(r.data.file ?? "document")}</code>{" "}
        <span className="muted">
          {r.message.split(": ").slice(1).join(": ")}
        </span>
      </summary>
      {ents.length === 0 ? (
        <p className="muted">
          Nothing taken in (skipped or failed: see the step log).
        </p>
      ) : (
        <div className="scroll-x">
          <table className="pipe-table ing-table">
            <thead>
              <tr>
                <th>kind</th>
                <th>id</th>
                <th>title</th>
                <th>stored</th>
                <th>chunks</th>
                <th>sections</th>
                <th>declares</th>
                <th>captured</th>
              </tr>
            </thead>
            <tbody>
              {ents.map((x) => (
                <Fragment key={`${x.kind}:${x.external_id}`}>
                  <tr>
                    <td>
                      <span className="chip">{x.kind}</span>
                    </td>
                    <td>
                      <code>{x.external_id}</code>
                    </td>
                    <td>{x.title}</td>
                    <td
                      className={x.status === "unchanged" ? "muted" : undefined}
                    >
                      {x.status} v{x.version}
                    </td>
                    <td>{x.status === "unchanged" ? "-" : x.chunks}</td>
                    <td className="ing-sections">{x.sections.join(" · ")}</td>
                    <td>
                      {Object.entries(x.declares).map(([t, refs]) => (
                        <div key={t}>
                          <span className="ing-rel">{t}</span> {refs.join(", ")}
                        </div>
                      ))}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        aria-expanded={open === x.external_id}
                        aria-label={`Inspect ${x.external_id}`}
                        onClick={() =>
                          setOpen(open === x.external_id ? null : x.external_id)
                        }
                      >
                        {open === x.external_id ? "Hide" : "Inspect"}
                      </button>
                    </td>
                  </tr>
                  {open === x.external_id && (
                    <tr>
                      <td colSpan={8} className="cap-cell">
                        <CapturedView project={project} refId={x.external_id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

function Concepts({ e }: { e: Ev }) {
  const cs = e.data.concepts as { name: string; type: string; quote: string }[];
  const rs = e.data.relations as {
    from: string;
    type: string;
    to: string;
    quote: string;
  }[];
  return (
    <details className="ing-file">
      <summary>
        <code>{String(e.data.source)}</code>{" "}
        <span className="muted">
          {cs.length} concepts/rules, {rs.length} relationships,{" "}
          {String(e.data.dropped)} dropped
        </span>
      </summary>
      <ul className="ing-links">
        {cs.map((c) => (
          <li key={c.name}>
            <span className="chip">{c.type}</span> <strong>{c.name}</strong>{" "}
            <q className="muted">{c.quote}</q>
          </li>
        ))}
        {rs.map((r, i) => (
          <li key={`r${i}`}>
            <code>{r.from}</code> <span className="ing-rel">{r.type}</span>{" "}
            <code>{r.to}</code> <q className="muted">{r.quote}</q>
          </li>
        ))}
      </ul>
    </details>
  );
}
