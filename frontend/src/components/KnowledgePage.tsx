import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type {
  ExtractJob,
  KnowledgeJob,
  KGEdge,
  KGNode,
  KGraph,
  KnowledgeBundle,
  KnowledgeIngestResult,
  KnowledgeProject,
  KnowledgeSource,
} from "../types";
import { CapturedView } from "./CapturedView";
import { EmbeddingMap } from "./EmbeddingMap";
import { GraphLegend, GraphView } from "./GraphView";
import { ContextGraphList } from "./ContextGraphView";
import { NetworkView } from "./NetworkView";
import { QueryConsole } from "./QueryConsole";
import { ProjectPipeline } from "./ProjectPipeline";
import { Loading } from "./Spinner";

const KINDS = [
  { value: "brd", label: "BRD" },
  { value: "story", label: "User story / stories file" },
  { value: "requirement", label: "Requirement" },
  { value: "epic", label: "Epics file (E-001 ...)" },
  { value: "design", label: "Design document" },
  { value: "ticket", label: "Ticket" },
  { value: "doc", label: "Other document" },
];

const msg = (e: unknown) =>
  e instanceof ApiError || e instanceof Error ? e.message : String(e);
const FOUND_BY: Record<string, string> = {
  id: "exact id",
  search: "vector + keyword search",
  graph: "graph (database)",
  "graph:neo4j": "graph (Neo4j)",
};
/** "3 by exact id, 6 by search, 5 by graph (Neo4j)": which retrieval path brought each item (first one counts). */
const foundBy = (b: KnowledgeBundle) =>
  Object.entries(
    Object.values(b.slots)
      .flat()
      .reduce<Record<string, number>>((acc, i) => {
        const f = FOUND_BY[i.found_by?.[0] ?? ""] ?? "unknown";
        acc[f] = (acc[f] ?? 0) + 1;
        return acc;
      }, {}),
  )
    .map(([k, n]) => `${n} by ${k}`)
    .join(", ");
/** "story:BRD-02/US-001" -> { story: 5, ... }: what a folder gave us, by kind. */
const countKinds = (ids: string[]) =>
  ids.reduce<Record<string, number>>((acc, i) => {
    const k = i.split(":", 1)[0] ?? i;
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});

export async function toBase64(file: File): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i += 0x8000)
    s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

/** One numbered step of the page: the same header everywhere. */
function Step({
  n,
  title,
  hint,
  children,
  label,
}: {
  n: number;
  title: string;
  hint: string;
  children: React.ReactNode;
  label: string;
}) {
  return (
    <section className="card section" aria-label={label}>
      <header className="section-head">
        <span className="step" aria-hidden="true">
          {n}
        </span>
        <div>
          <h3>{title}</h3>
          <p className="help">{hint}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

/** Edge list of one source with the controls to confirm or reject a proposed relationship. */
export function SourceDetail({
  project,
  node,
  edges,
  nodes,
  onChanged,
}: {
  project: string;
  node: KGNode;
  edges: KGEdge[];
  nodes: KGNode[];
  onChanged: () => void;
}) {
  const [src, setSrc] = useState<KnowledgeSource | null>(null);
  const evidence =
    (src?.metadata?.evidence as
      { source: string; quote: string }[] | undefined) ?? [];
  const [err, setErr] = useState<string | null>(null);
  const [captured, setCaptured] = useState(false); // loaded only when the section is opened
  useEffect(() => {
    // the parent gives this component key={node.id}, so state starts clean for every source
    api.knowledgeSource(project, node.id).then(setSrc, (e) => setErr(msg(e)));
  }, [project, node.id]);
  const name = (id: string) => {
    const n = nodes.find((x) => x.id === id);
    return n ? (n.subtype ? n.title : n.external_id) : id;
  };
  const mine = edges.filter((e) => e.from === node.id || e.to === node.id);
  async function decide(e: KGEdge, d: "promote" | "reject") {
    try {
      await api.decideEdge(e.id, d);
      onChanged();
    } catch (x) {
      setErr(msg(x));
    }
  }
  return (
    <aside
      className="card detail-side"
      aria-label={`Details of ${node.external_id}`}
    >
      <h3>
        {node.subtype ? node.title : node.external_id}{" "}
        <span className="chip">v{node.version}</span>{" "}
        <span className="chip">{node.subtype ?? node.kind}</span>
      </h3>
      <p className="muted">
        {node.title}
        {node.origin !== "ingest" && ` · origin: ${node.origin}`}
        {node.state !== "ok" && ` · indexing: ${node.state}`}
      </p>
      {err && (
        <div className="banner banner-bad" role="alert">
          {err}
        </div>
      )}
      <h4>Relationships</h4>
      {mine.length === 0 && <p className="muted">None yet.</p>}
      <ul className="plain">
        {mine.map((e) => (
          <li key={e.id}>
            {name(e.from)} <b>{e.type.replace("_", " ")}</b> {name(e.to)}{" "}
            <span className={`chip st-${e.status}`}>{e.status}</span>
            {e.stale && <span className="chip st-stale">changed</span>}
            {e.status === "proposed" && (
              <span className="row">
                <button onClick={() => decide(e, "promote")}>Confirm</button>
                <button
                  className="secondary"
                  onClick={() => decide(e, "reject")}
                >
                  Reject
                </button>
              </span>
            )}
            {e.evidence && <div className="help">{e.evidence}</div>}
          </li>
        ))}
      </ul>
      {evidence.length > 0 && (
        <>
          <h4>Where the documents say it</h4>
          <ul className="evidence">
            {evidence.map((e, i) => (
              <li key={i}>
                <b>{e.source}</b> <q>{e.quote}</q>
              </li>
            ))}
          </ul>
        </>
      )}
      <details onToggle={(e) => setCaptured(e.currentTarget.open)}>
        <summary>What ingestion captured (chunks, embeddings, graph)</summary>
        {captured && <CapturedView project={project} refId={node.id} />}
      </details>
      <h4>Content</h4>
      {src ? (
        <pre className="src-content">{src.content}</pre>
      ) : (
        !err && <Loading>Loading…</Loading>
      )}
    </aside>
  );
}

export function KnowledgePage() {
  const [projects, setProjects] = useState<KnowledgeProject[]>([]);
  const [project, setProject] = useState("");
  const [newName, setNewName] = useState("");
  const [graph, setGraph] = useState<KGraph | null>(null);
  const [selected, setSelected] = useState<KGNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  // intake
  const [kind, setKind] = useState("brd");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [extId, setExtId] = useState("");
  const [derives, setDerives] = useState("");
  const [scope, setScope] = useState("");
  const [folder, setFolder] = useState("");
  const [group, setGroup] = useState("");
  const [fit, setFit] = useState(true);
  const [view, setView] = useState<"documents" | "concepts" | "all">("all");
  const [layout, setLayout] = useState<"network" | "columns">("network"); // network = like a graph database browser
  const [job, setJob] = useState<ExtractJob | null>(null);
  const [liveJob, setLiveJob] = useState<KnowledgeJob | null>(null); // the job running now, shown live in the pipeline
  const [find, setFind] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<KnowledgeIngestResult | null>(null);
  // context preview / build
  const [goal, setGoal] = useState("");
  const [bundle, setBundle] = useState<KnowledgeBundle | null>(null);
  const [repo, setRepo] = useState("");

  const loadProjects = useCallback(async () => {
    try {
      const p = (await api.knowledgeProjects()).projects;
      setProjects(p);
      setProject((cur) => cur || p[0]?.project || "");
    } catch (e) {
      setError(msg(e));
    }
  }, []);
  useEffect(() => {
    void Promise.resolve().then(loadProjects);
  }, [loadProjects]);

  const loadGraph = useCallback(async () => {
    if (!project) {
      setGraph(null);
      return;
    }
    try {
      setGraph(
        await api.knowledgeGraph(
          project,
          bundle?.project === project ? bundle.bundle_id : undefined,
          false,
          view,
        ),
      );
    } catch (e) {
      setError(msg(e));
    }
  }, [project, bundle, view]);
  useEffect(() => {
    void Promise.resolve().then(loadGraph);
  }, [loadGraph]);

  const active = (newName.trim() || project).trim();
  // ids like BRD-02/US-001 belong to the pack BRD-02: one backlog at a time keeps a big graph readable
  const groupOf = (n: KGNode) => n.external_id.split("/")[0];
  const groups = Object.entries(
    (graph?.nodes ?? []).reduce<Record<string, number>>(
      (acc, n) => ((acc[groupOf(n)] = (acc[groupOf(n)] ?? 0) + 1), acc),
      {},
    ),
  ).filter(([, n]) => n > 1);
  const shown = (() => {
    if (graph && view !== "documents") {
      const all = graph.nodes.map((n) => ({ ...n, kind: n.subtype ?? n.kind }));
      const q = find.trim().toLowerCase();
      if (!q) return { nodes: all, edges: graph.edges };
      // what matches, plus what it is directly related to: enough to read one idea in context
      const hit = new Set(
        all.filter((n) => n.title.toLowerCase().includes(q)).map((n) => n.id),
      );
      const keep = new Set(hit);
      for (const e of graph.edges) {
        if (hit.has(e.from)) keep.add(e.to);
        if (hit.has(e.to)) keep.add(e.from);
      }
      return {
        nodes: all.filter((n) => keep.has(n.id)),
        edges: graph.edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
      };
    }
    if (!graph || !group)
      return { nodes: graph?.nodes ?? [], edges: graph?.edges ?? [] };
    const nodes = graph.nodes.filter((n) => groupOf(n) === group);
    const ids = new Set(nodes.map((n) => n.id));
    return {
      nodes,
      edges: graph.edges.filter((e) => ids.has(e.from) && ids.has(e.to)),
    };
  })();

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!active)
      return setError("Choose a project or type a name for a new one.");
    if (!file && !text.trim())
      return setError(
        "Paste the document text or choose a file (.md, .txt, .docx).",
      );
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const links = derives.trim()
        ? { derives_from: derives.split(/[,\s]+/).filter(Boolean) }
        : undefined;
      const body = file
        ? {
            project: active,
            kind,
            filename: file.name,
            data_base64: await toBase64(file),
            external_id: extId.trim() || undefined,
            links,
            scope: scope.trim() || undefined,
          }
        : {
            project: active,
            kind,
            filename: kind === "brd" ? "brd.md" : "",
            text,
            external_id: extId.trim() || undefined,
            links,
            scope: scope.trim() || undefined,
          };
      const job = await api.waitJob(await api.startUpload(body), setLiveJob);
      setResult(job.report as KnowledgeIngestResult);
      setText("");
      setFile(null);
      setExtId("");
      setDerives("");
      setProject(active);
      setNewName("");
      await loadProjects();
      setBundle(null);
      setGraph(await api.knowledgeGraph(active));
    } catch (x) {
      setError(msg(x));
    } finally {
      setBusy(false);
    }
  }

  async function extract() {
    if (!project) return;
    setError(null);
    try {
      let j = await api.startExtract(project);
      setJob(j);
      setLiveJob(j as unknown as KnowledgeJob);
      while (j.status === "running") {
        await new Promise((r) => setTimeout(r, 1000));
        j = await api.extractStatus(j.id);
        setJob(j);
        setLiveJob(j as unknown as KnowledgeJob);
      }
      if (j.status === "failed") setError(j.error ?? "Extraction failed");
      setView("concepts");
      await loadGraph();
    } catch (x) {
      setError(msg(x));
    }
  }

  async function indexFolder() {
    if (!active)
      return setError("Choose a project or type a name for a new one.");
    if (!folder.trim())
      return setError("Give the folder that holds the documents.");
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const job = await api.waitJob(
        await api.startIndex(active, folder.trim()),
        setLiveJob,
      );
      setResult(job.report as KnowledgeIngestResult);
      setProject(active);
      setNewName("");
      await loadProjects();
      setBundle(null);
      setGraph(await api.knowledgeGraph(active));
    } catch (x) {
      setError(msg(x));
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    if (!project || !goal.trim())
      return setError("Pick a project and describe what should be built.");
    setError(null);
    try {
      // the repository the agent will change counts as code knowledge, as it does when the task runs
      if (repo.trim()) await api.indexRepository(project, repo.trim());
      setBundle(await api.knowledgeBundle(project, goal.trim()));
    } catch (x) {
      setError(msg(x));
    }
  }

  async function build() {
    if (!repo.trim() || !goal.trim())
      return setError("Give the repository folder and describe what to build.");
    setBusy(true);
    setError(null);
    try {
      const t = await api.createTask({
        repo_path: repo.trim(),
        instructions: goal.trim(),
        acceptance_criteria: [],
        verification_commands: [],
        knowledge_project: project,
      });
      await api.runTask(t.id);
      location.hash = `#/tasks/${t.id}`;
    } catch (x) {
      setError(msg(x));
    } finally {
      setBusy(false);
    }
  }

  // while a job runs, the pipeline follows the project it is filling (a new one, before it is selected)
  const pipeProject = liveJob?.status === "running" ? liveJob.project : project;
  return (
    <div className="knowledge-page">
      <header className="page-head">
        <h2>Knowledge</h2>
        <p className="muted">
          Give Harnix your BRD and user stories. It embeds them, links them into
          a graph, builds the context an agent needs for a task, and lets you
          check what the agent did against it.
        </p>
      </header>
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}

      <section className="card project-bar" aria-label="Project">
        <label>
          Project
          <select
            value={project}
            onChange={(e) => (
              setProject(e.target.value),
              setBundle(null),
              setSelected(null)
            )}
          >
            {projects.length === 0 && <option value="">(none yet)</option>}
            {projects.map((p) => (
              <option key={p.project} value={p.project}>
                {p.project} ({p.sources} sources)
              </option>
            ))}
          </select>
        </label>
        <label>
          or start a new one
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. virtual-accounts"
            pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
          />
        </label>
      </section>

      {pipeProject && (
        <section className="card" aria-label="Knowledge pipeline">
          <h3>Pipeline for {pipeProject}</h3>
          <ProjectPipeline
            project={pipeProject}
            live={liveJob?.project === pipeProject ? liveJob : null}
            refreshKey={`${graph?.nodes.length ?? 0}-${graph?.edges.length ?? 0}-${result?.created ?? ""}-${job?.status ?? ""}`}
          />
        </section>
      )}

      <Step
        n={1}
        title="Add documents"
        hint="Paste or upload a BRD, user stories, requirements or a design, or index a whole folder at once."
        label="Add a document"
      >
        <div className="two-col">
          <form onSubmit={add} aria-label="Add a document">
            <div className="row">
              <label>
                Type
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  {KINDS.map((k) => (
                    <option key={k.value} value={k.value}>
                      {k.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Id <span className="help">(optional, e.g. US-12)</span>
                <input
                  value={extId}
                  onChange={(e) => setExtId(e.target.value)}
                />
              </label>
            </div>
            <div className="row">
              <label>
                Derives from <span className="help">(optional ids)</span>
                <input
                  value={derives}
                  onChange={(e) => setDerives(e.target.value)}
                  placeholder="BF-AS-VA-02"
                />
              </label>
              <label>
                Namespace <span className="help">(optional, e.g. BRD-01)</span>
                <input
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                  placeholder="BRD-01"
                />
              </label>
            </div>
            <label>
              Paste the text
              <textarea
                rows={8}
                value={text}
                onChange={(e) => setText(e.target.value)}
                disabled={!!file}
                placeholder="Markdown or plain text. A BRD with sections named like BF-AS-VA-02: ... is split into requirements."
              />
            </label>
            <label>
              or upload a file <span className="help">(.md, .txt, .docx)</span>
              <input
                type="file"
                accept=".md,.txt,.docx,.json"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <div>
              <button className="primary" type="submit" disabled={busy} aria-busy={busy}>
                {busy ? "Working…" : "Add and index"}
              </button>
            </div>
          </form>
          <div className="folder-box" role="group" aria-label="Index a folder">
            <h4>Index a whole folder</h4>
            <p className="help">
              Every .md, .txt, .docx and .json file in the folder and its
              sub-folders is read, so give the parent folder to take in several
              backlogs at once. BRD.md, epic.md and user_story.md are
              understood; a folder named BRD-01-… is a namespace, so several
              backlogs can each have a US-001.
            </p>
            <label>
              Folder
              <input
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                placeholder="C:/path/to/docs"
              />
            </label>
            <div>
              <button
                type="button"
                onClick={() => void indexFolder()}
                disabled={busy}
              >
                Index the folder
              </button>
            </div>
          </div>
        </div>
        {result && (
          <div className="banner banner-ok" role="status">
            <span>
              {result.created} new, {result.updated} updated, {result.unchanged}{" "}
              unchanged · {result.edges_created} relationships ·{" "}
              {result.suggested} suggested for you to confirm · embeddings:{" "}
              {result.embedding_model}
            </span>
            {result.ingested.length > 0 && (
              <details>
                <summary>
                  Read {result.ingested.length} documents:{" "}
                  {Object.entries(countKinds(result.ingested))
                    .map(([k, n]) => `${n} ${k}`)
                    .join(", ")}
                </summary>
                <ul className="evidence">
                  {result.ingested.map((i) => (
                    <li key={i}>{i}</li>
                  ))}
                </ul>
              </details>
            )}
            {result.failed.length > 0 && (
              <span>Could not read: {result.failed.join("; ")}</span>
            )}
            {result.skipped.length > 0 && (
              <span>Skipped: {result.skipped.join("; ")}</span>
            )}
            {result.partial.length > 0 && (
              <span>
                Not embedded yet (add again to retry):{" "}
                {result.partial.join(", ")}
              </span>
            )}
            {result.unresolved.length > 0 && (
              <span>Unknown references: {result.unresolved.join("; ")}</span>
            )}
          </div>
        )}
      </Step>

      <Step
        n={2}
        title="The graph"
        hint="Two views of the same documents: how they trace to each other, and what is written inside them."
        label="Knowledge graph"
      >
        {project && (
          <details className="qc-wrap" open>
            <summary>Query the graph (Cypher or plain English)</summary>
            <QueryConsole project={project} />
          </details>
        )}
        <div className="row">
          <div className="seg" role="group" aria-label="Graph view">
            <button
              type="button"
              aria-pressed={view === "all"}
              onClick={() => (setView("all"), setSelected(null))}
            >
              Everything
            </button>
            <button
              type="button"
              aria-pressed={view === "documents"}
              onClick={() => (setView("documents"), setSelected(null))}
            >
              Documents
            </button>
            <button
              type="button"
              aria-pressed={view === "concepts"}
              onClick={() => (setView("concepts"), setSelected(null))}
            >
              Concepts &amp; rules
            </button>
          </div>
          <div className="seg" role="group" aria-label="Layout">
            <button
              type="button"
              aria-pressed={layout === "network"}
              onClick={() => setLayout("network")}
            >
              Network
            </button>
            <button
              type="button"
              aria-pressed={layout === "columns"}
              onClick={() => setLayout("columns")}
            >
              Columns
            </button>
          </div>
          {view !== "documents" && (
            <button
              type="button"
              onClick={() => void extract()}
              disabled={!project || job?.status === "running"}
            >
              {job?.status === "running"
                ? `Reading documents… ${job.done}/${job.total}`
                : "Extract concepts"}
            </button>
          )}
        </div>
        {view !== "documents" && graph && graph.nodes.length > 0 && (
          <label>
            Find a concept or rule{" "}
            <span className="help">
              ({graph.nodes.length} in all; shows matches and what they are
              linked to)
            </span>
            <input
              value={find}
              onChange={(e) => setFind(e.target.value)}
              placeholder="favorite, entitlement, statement, 12-month ..."
            />
          </label>
        )}
        {view === "concepts" && job?.status === "running" && job.total > 0 && (
          <div
            className="progress"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={job.total}
            aria-valuenow={job.done}
          >
            <i style={{ width: `${(100 * job.done) / job.total}%` }} />
          </div>
        )}
        {view === "concepts" && job?.report && (
          <div className="banner banner-ok" role="status">
            <span>
              Read {job.report.sources} documents (
              {job.report.skipped_unchanged} unchanged, skipped):{" "}
              {job.report.entities} concepts and rules, {job.report.relations}{" "}
              relationships between them, {job.report.mentions} new
              &ldquo;mentions&rdquo; links.{" "}
              {job.report.rejected_entities + job.report.rejected_relations}{" "}
              proposals dropped because their quote is not in the document.
            </span>
            {job.report.failed.length > 0 && (
              <span>Failed: {job.report.failed.join("; ")}</span>
            )}
          </div>
        )}
        <GraphLegend />
        {graph && view === "concepts" && graph.nodes.length === 0 && (
          <p className="empty">
            Nothing extracted yet. &ldquo;Extract concepts&rdquo; has the model
            read each document and list the concepts, actors, data fields, rules
            and limits it states, each with the exact sentence it came from
            (anything without a verifiable quote is dropped). Relationships
            between them stay dashed until you confirm them.
          </p>
        )}
        {graph &&
          view === "documents" &&
          layout === "columns" &&
          groups.length > 1 && (
            <div className="row">
              <label>
                Show
                <select
                  value={group}
                  onChange={(e) => (
                    setGroup(e.target.value),
                    setSelected(null)
                  )}
                >
                  <option value="">
                    everything ({graph.nodes.length} sources)
                  </option>
                  {groups.map(([g, n]) => (
                    <option key={g} value={g}>
                      {g} ({n})
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={fit}
                  onChange={(e) => setFit(e.target.checked)}
                />{" "}
                fit to width
              </label>
            </div>
          )}
        {graph && (
          <div className="graph-layout">
            {layout === "network" ? (
              <NetworkView
                nodes={shown.nodes}
                edges={shown.edges}
                selected={selected?.id ?? null}
                onSelect={setSelected}
                emphasizeBundle={!!bundle}
              />
            ) : (
              <GraphView
                fit={fit}
                nodes={shown.nodes}
                edges={shown.edges}
                selected={selected?.id ?? null}
                onSelect={setSelected}
                emphasizeBundle={!!bundle}
              />
            )}
            {selected && graph && (
              <SourceDetail
                key={selected.id}
                project={project}
                node={selected}
                edges={graph.edges}
                nodes={graph.nodes}
                onChanged={() => void loadGraph()}
              />
            )}
          </div>
        )}
      </Step>

      <Step
        n={3}
        title="The embeddings"
        hint="Every piece of text as a point: texts the model finds alike sit close together."
        label="Embeddings"
      >
        {project ? (
          <EmbeddingMap
            project={project}
            refreshKey={graph?.nodes.length ?? 0}
          />
        ) : (
          <p className="empty">Choose or create a project.</p>
        )}
      </Step>

      <Step
        n={4}
        title="What should be built?"
        hint="Describe the work or paste a user story, preview the context the agent would get, then build."
        label="Context and build"
      >
        <label>
          Describe the work (or paste a user story)
          <textarea
            rows={4}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Implement the refund flow described in BF-PAY-02"
          />
        </label>
        <div className="row">
          <label>
            Repository folder to build in
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="C:/path/to/your/project"
            />
          </label>
          <button onClick={preview} disabled={busy} aria-busy={busy}>
            Preview the context
          </button>
          <button
            className="primary"
            onClick={build}
            disabled={busy || !project}
          >
            Build it with this knowledge
          </button>
        </div>
        <p className="help">
          With a repository folder filled in, its code and tests are indexed
          first (as they are when the task runs). The result page shows the
          context graph, the checks and an acceptance-criteria review you can
          sign off.
        </p>
        {bundle && (
          <div
            className={`banner ${bundle.status === "sufficient" ? "banner-ok" : "banner-warn"}`}
            role="status"
          >
            <span>
              <b>Context {bundle.status}</b> · bundle{" "}
              {bundle.bundle_id.slice(0, 12)} ·{" "}
              {Object.entries(bundle.slots)
                .filter(([, v]) => v.length)
                .map(([k, v]) => `${v.length} ${k}`)
                .join(", ")}
            </span>
            <details>
              <summary>How each item was found ({foundBy(bundle)})</summary>
              <ul className="evidence">
                {Object.values(bundle.slots)
                  .flat()
                  .map((i) => (
                    <li key={i.source_id}>
                      <b>{i.external_id}</b>{" "}
                      {(i.found_by ?? [])
                        .map((f) => FOUND_BY[f] ?? f)
                        .join(" + ")}
                    </li>
                  ))}
              </ul>
            </details>
            {bundle.sufficiency.missing.map((m) => (
              <span key={m}>Missing: {m}</span>
            ))}
            {bundle.unresolved_questions.map((q) => (
              <span key={q}>Question: {q}</span>
            ))}
            <span className="help">
              The highlighted boxes in the graph are what the agent would be
              given. Add what is missing, or answer the questions in the task
              text.
            </span>
          </div>
        )}
      </Step>

      {project && (
        <Step
          n={5}
          title="Context graphs"
          hint="Every context Harnix built for this project (previews and task runs): what the agent was given, drawn as a graph. Click a box to read exactly what the agent received for it."
          label="Context graphs"
        >
          <ContextGraphList
            project={project}
            focus={bundle?.project === project ? bundle.bundle_id : null}
          />
        </Step>
      )}
    </div>
  );
}
