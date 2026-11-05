import { parseDiff } from "../lib";
import type { ChangedFile } from "../types";

export function DiffView({ diff, files }: { diff: string; files: ChangedFile[] }) {
  if (!diff.trim()) return <p className="muted">No changes yet.</p>;
  const parsed = parseDiff(diff);
  return (
    <div>
      <ul className="file-list" aria-label="Changed files">
        {files.map((f) => (
          <li key={f.path}>
            <a href={`#file-${encodeURIComponent(f.path)}`}>
              <span className={`file-status fs-${f.status}`}>{f.status}</span> {f.path}
            </a>{" "}
            <span className="add">+{f.additions}</span> <span className="del">-{f.deletions}</span>
          </li>
        ))}
      </ul>
      {parsed.map((file) => (
        <section key={file.path} id={`file-${encodeURIComponent(file.path)}`} className="diff-file">
          <h4 className="mono">{file.path}</h4>
          <pre className="diff" aria-label={`Diff of ${file.path}`}>
            {file.lines.map((l, i) => (
              <span key={i} className={`dl dl-${l.kind}`}>
                {l.text}
                {"\n"}
              </span>
            ))}
          </pre>
        </section>
      ))}
    </div>
  );
}
