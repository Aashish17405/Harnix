import { useState } from "react";
import { api } from "../api";
import type {
  CoverageReport,
  FindingsReport,
  KnowledgeJob,
  MarkdownExport,
  ProjectPipeline as Data,
} from "../types";

const KIND_TEXT: Record<string, string> = {
  duplicate_id: "an id used twice (the second replaces the first)",
  unresolved_reference: "a reference to an id that does not exist",
  ontology_refused: "a declared link the ontology does not allow",
  duplicate_content: "different sources with identical text",
  rendering_differs: "an export that disagrees with the backlog",
  skipped: "a file that was cut or left out",
  failed: "a file that could not be read",
  unknown_kind: "a document of an unknown kind",
};

const when = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString() : "never";

/** Problems in the documents themselves, as the last ingestion of each folder found them. */
export function FindingsPanel({ report }: { report: FindingsReport | null }) {
  if (!report) return <p className="muted">Loading…</p>;
  return (
    <dl className="pipe-kv">
      <dt>What this is</dt>
      <dd>
        Mistakes in the documents, not in Harnix: noticed while indexing and kept
        per folder, so fixing a document and indexing again clears its findings.
      </dd>
      <dt>Now</dt>
      <dd>
        {report.count === 0 ? (
          "No problems found in the last ingestion."
        ) : (
          <>
            {report.count} found:{" "}
            {Object.entries(report.by_kind)
              .map(([k, n]) => `${n} × ${KIND_TEXT[k] ?? k}`)
              .join("; ")}
          </>
        )}
      </dd>
      {report.count > 0 && (
        <>
          <dt>Details</dt>
          <dd>
            <table className="pipe-table" aria-label="Findings">
              <thead>
                <tr>
                  <th>Problem</th>
                  <th>Where</th>
                  <th>What was found</th>
                </tr>
              </thead>
              <tbody>
                {report.findings.slice(0, 100).map((f, i) => (
                  <tr key={i}>
                    <td>{f.kind.replaceAll("_", " ")}</td>
                    <td>{f.where}</td>
                    <td>{f.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.count > 100 && (
              <p className="muted">
                Showing 100 of {report.count}; the CLI lists them all:{" "}
                <code>forge knowledge findings --project …</code>
              </p>
            )}
          </dd>
        </>
      )}
    </dl>
  );
}

/** Links the model infers from what documents say (depends on, contradicts, clarifies, flags a gap, uses a system). */
export function LinkPanel({
  project,
  d,
  running,
  onStarted,
}: {
  project: string;
  d: Data;
  running: boolean;
  onStarted: (job: KnowledgeJob) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const last = d.linking?.last?.detail;
  const by = d.linking?.by_status ?? {};
  async function start() {
    setErr(null);
    try {
      onStarted(await api.startLink(project));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <dl className="pipe-kv">
      <dt>What happens</dt>
      <dd>
        The model reads each document next to a list of the project&rsquo;s
        other items and proposes the links the text implies but does not name:
        depends on, contradicts, clarifies, flags a gap in, uses a system. Each
        needs an exact quote from the document and a confidence of at least 0.7,
        and stays a <b>proposal</b>: confirm or reject it on the graph. Pairs a
        document already links, or that you rejected, are left alone.
      </dd>
      <dt>Held now</dt>
      <dd>
        {Object.keys(by).length
          ? Object.entries(by)
              .map(([k, n]) => `${n} ${k}`)
              .join(", ")
          : "no inferred links yet"}
      </dd>
      <dt>Last run</dt>
      <dd>
        {d.linking?.last && last
          ? `${when(d.linking.last.at)} · ${last.sources} documents read, ${last.skipped_unchanged} unchanged, ${last.proposed} links proposed, ${last.already_linked} already linked${
              last.failed?.length
                ? `; ${last.failed.length} failed (first: ${last.failed[0]})`
                : ""
            }${
              last.rejected && Object.keys(last.rejected).length
                ? `; dropped: ${Object.entries(last.rejected)
                    .map(([k, n]) => `${n} ${k}`)
                    .join(", ")}`
                : ""
            }`
          : "never"}
      </dd>
      <dt>Run</dt>
      <dd>
        <button type="button" onClick={() => void start()} disabled={running}>
          Find implied links
        </button>{" "}
        <span className="muted">
          needs a model key; documents that did not change are not read again
        </span>
        {err && <p className="banner banner-bad">{err}</p>}
      </dd>
    </dl>
  );
}

/** Did everything the input files say reach the knowledge base? */
export function CoveragePanel({
  project,
  result,
  onResult,
}: {
  project: string;
  result: CoverageReport | null;
  onResult: (r: CoverageReport) => void;
}) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run() {
    setBusy(true);
    setErr(null);
    try {
      onResult(await api.knowledgeCoverage(project, path.trim() || undefined));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const i = result?.inputs;
  return (
    <dl className="pipe-kv">
      <dt>What happens</dt>
      <dd>
        The input files are read again, on their own, and every line and every
        value is looked for in what is stored. A value too short to identify
        itself (P1, yes) must be found next to its field name. Present is not
        the same as correct: a value can be stored under the wrong item.
      </dd>
      <dt>Input folder</dt>
      <dd>
        <input
          type="text"
          aria-label="Input folder to check"
          placeholder="the folder you indexed (optional)"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          style={{ width: "min(32rem, 100%)" }}
        />{" "}
        <button type="button" onClick={() => void run()} disabled={busy}>
          {busy
            ? "Checking…"
            : path.trim()
              ? "Check the files"
              : "Check stored data"}
        </button>
        {err && <p className="banner banner-bad">{err}</p>}
      </dd>
      {result && (
        <>
          {i && (
            <>
              <dt>Input files</dt>
              <dd>
                <b>
                  {i.mapped} of {i.datapoints}
                </b>{" "}
                values found (
                {(Math.floor((i.score ?? 0) * 1000) / 10).toFixed(1)}%),{" "}
                {i.missing_total} missing
                <table className="pipe-table" aria-label="Coverage by file">
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Values</th>
                      <th>Found</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(i.files).map(([f, v]) => (
                      <tr key={f}>
                        <td>{f}</td>
                        <td>{v.datapoints}</td>
                        <td>{v.error ? `unreadable: ${v.error}` : v.mapped}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </dd>
              {i.missing.length > 0 && (
                <>
                  <dt>Not found</dt>
                  <dd>
                    <ul>
                      {i.missing.slice(0, 40).map((m, k) => (
                        <li key={k}>
                          <code>{m.file}</code> {m.where}: {m.text}
                        </li>
                      ))}
                    </ul>
                    {i.missing_total > 40 && (
                      <p className="muted">Showing 40 of {i.missing_total}.</p>
                    )}
                  </dd>
                </>
              )}
            </>
          )}
          <dt>Chunks</dt>
          <dd>
            {result.chunks.uncovered === 0
              ? `every one of the ${result.chunks.lines} stored lines is inside a chunk`
              : `${result.chunks.uncovered} of ${result.chunks.lines} stored lines are in no chunk`}
            {result.chunks.examples.slice(0, 5).map((e, k) => (
              <div key={k} className="muted">
                {e.source}: {e.text}
              </div>
            ))}
          </dd>
          <dt>No links</dt>
          <dd>
            {result.orphans.count === 0
              ? "every story, feature, requirement and rule is linked to something"
              : `${result.orphans.count} not linked to anything (${Object.entries(
                  result.orphans.by_kind,
                )
                  .map(([k, n]) => `${n} ${k}`)
                  .join(
                    ", ",
                  )}): ${result.orphans.examples.slice(0, 10).join(", ")}`}
          </dd>
        </>
      )}
    </dl>
  );
}

/** A readable Markdown copy of the graph, to review and diff in git. */
export function ExportPanel({
  project,
  result,
  onResult,
}: {
  project: string;
  result: MarkdownExport | null;
  onResult: (r: MarkdownExport) => void;
}) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run() {
    setBusy(true);
    setErr(null);
    try {
      onResult(await api.exportMarkdown(project, path.trim()));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dl className="pipe-kv">
      <dt>What happens</dt>
      <dd>
        One Markdown file per source: its identity and version, its links as
        front matter and <code>[[wikilinks]]</code>, and its text. The output is
        the same every time, so a change in the knowledge is a small diff to
        review in git. The database stays the master: edits to these files are
        not read back.
      </dd>
      <dt>Folder</dt>
      <dd>
        <input
          type="text"
          aria-label="Folder for the Markdown copy"
          placeholder="a new or empty folder, or a previous export"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          style={{ width: "min(32rem, 100%)" }}
        />{" "}
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || !path.trim()}
        >
          {busy ? "Writing…" : "Write the Markdown copy"}
        </button>
        {err && <p className="banner banner-bad">{err}</p>}
      </dd>
      {result && (
        <>
          <dt>Result</dt>
          <dd>
            {result.files} files in <code>{result.folder}</code>:{" "}
            {result.written} written, {result.unchanged} unchanged,{" "}
            {result.removed} removed
          </dd>
        </>
      )}
    </dl>
  );
}
