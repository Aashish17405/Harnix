import { useState } from "react";
import { api } from "../api";
import { deriveToolCalls, describeEvent, formatDuration } from "../lib";
import type { Approval, HarnixEvent, Verification } from "../types";
import { CheckStatus } from "./Badges";

export function Timeline({ events, live }: { events: HarnixEvent[]; live: boolean }) {
  if (!events.length) return <p className="muted">{live ? "Waiting for events…" : "No events recorded."}</p>;
  return (
    <ol className="timeline" aria-label="Execution timeline">
      {events.map((e) => {
        const d = describeEvent(e);
        return (
          <li key={e.id} className={`tl tl-${d.category}`}>
            <time dateTime={e.ts}>{new Date(e.ts).toLocaleTimeString()}</time>
            <div>
              <strong>{d.label}</strong>
              {d.detail && <span className="detail"> {d.detail}</span>}
              <details>
                <summary>raw</summary>
                <pre className="raw">{JSON.stringify(e.data, null, 2)}</pre>
              </details>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const STATE_TEXT = {
  proposed: "proposed / running",
  denied: "denied",
  awaiting_approval: "awaiting approval",
  executed: "executed",
  failed: "failed",
} as const;

export function ToolCalls({ events }: { events: HarnixEvent[] }) {
  const rows = deriveToolCalls(events);
  if (!rows.length) return <p className="muted">The agent has not proposed any tool calls yet.</p>;
  return (
    <table className="table" aria-label="Tool call history">
      <thead>
        <tr>
          <th>#</th>
          <th>Tool</th>
          <th>Arguments</th>
          <th>Policy</th>
          <th>Outcome</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.callId}>
            <td>{i + 1}</td>
            <td className="mono">{r.tool}</td>
            <td>
              <code className="args">{JSON.stringify(r.args)}</code>
            </td>
            <td title={r.reason}>{r.decision ?? "-"}</td>
            <td>
              <span className={`badge tc-${r.state}`}>{STATE_TEXT[r.state]}</span>
              {r.error && <div className="detail">{r.error}</div>}
              {r.output != null && (
                <details>
                  <summary>output</summary>
                  <pre className="raw">{JSON.stringify(r.output, null, 2)}</pre>
                </details>
              )}
            </td>
            <td>{r.durationSeconds != null ? formatDuration(r.durationSeconds) : "-"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function VerificationPanel({ items, verdict }: { items: Verification[]; verdict?: string | null }) {
  if (!items.length) return <p className="muted">No verification results yet.</p>;
  const phases = (["baseline", "final"] as const).map((p) => ({ p, rows: items.filter((v) => v.phase === p) }));
  return (
    <div>
      {verdict && (
        <p>
          Independent verdict: <strong>{verdict}</strong>
        </p>
      )}
      {phases.map(
        ({ p, rows }) =>
          rows.length > 0 && (
            <section key={p}>
              <h4>{p === "baseline" ? "Before the agent (baseline)" : "After the agent (final)"}</h4>
              <table className="table" aria-label={`${p} checks`}>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Kind</th>
                    <th>Command</th>
                    <th>Exit</th>
                    <th>Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((v) => (
                    <tr key={v.id}>
                      <td>
                        <CheckStatus status={v.status} />
                      </td>
                      <td>
                        {v.kind}
                        {v.attempt > 0 && <span className="muted"> #{v.attempt}</span>}
                      </td>
                      <td>
                        <code>{v.command ?? v.name}</code>
                        {v.note && <div className="detail">{v.note}</div>}
                        {v.output && (
                          <details>
                            <summary>output</summary>
                            <pre className="raw">{v.output}</pre>
                          </details>
                        )}
                      </td>
                      <td>{v.exit_code ?? "-"}</td>
                      <td>{formatDuration(v.duration_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ),
      )}
    </div>
  );
}

/** Copies a completed task's changes into the real repository (after an explicit confirmation). */
export function ApplyPanel({ taskId, verified, alreadyApplied }: { taskId: string; verified: boolean; alreadyApplied: boolean }) {
  const [msg, setMsg] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(alreadyApplied);

  async function apply() {
    setBusy(true);
    setMsg(null);
    try {
      const preview = await api.apply(taskId, { dry_run: true });
      if (!preview.files.length) {
        setMsg({ kind: "ok", text: "Nothing to apply." });
        return;
      }
      const list = preview.files.map((f) => `${f.status} ${f.path}`).join("\n");
      const warn = preview.verified ? "" : "\n\nThis result was NOT independently verified.";
      if (!window.confirm(`Apply ${preview.files.length} file(s) to your repository?\n\n${list}${warn}`)) return;
      const res = await api.apply(taskId, { confirm: true });
      setDone(true);
      setMsg({ kind: "ok", text: `Applied ${res.files.length} file(s) to your repository.` });
    } catch (e) {
      setMsg({ kind: "bad", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="row" style={{ marginBottom: 12 }}>
      <button className="primary" disabled={busy || done} aria-busy={busy} onClick={apply}>
        {done ? "Applied to your repository" : "Apply to my repository…"}
      </button>
      {!verified && !done && <span className="badge outcome-warn">not verified</span>}
      {msg && (
        <span className={msg.kind === "bad" ? "field-error" : "muted"} role={msg.kind === "bad" ? "alert" : "status"}>
          {msg.text}
        </span>
      )}
    </div>
  );
}

export function ApprovalPanel({ approvals, onDecided }: { approvals: Approval[]; onDecided: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = approvals.filter((a) => a.status === "pending");
  if (!pending.length) return null;

  async function decide(a: Approval, d: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await api.decide(a.id, d, note);
      setNote("");
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card approval" aria-label="Pending approvals" role="alert">
      <h3>Approval required</h3>
      {pending.map((a) => (
        <div key={a.id} className="approval-item">
          <p>
            The agent wants to run <strong className="mono">{a.tool}</strong>: {a.reason}
          </p>
          <pre className="raw" aria-label="Exact action">{JSON.stringify(a.arguments, null, 2)}</pre>
          <p className="help">Approving authorizes only this exact action with these exact arguments.</p>
          <label>
            Note (optional)
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="row">
            <button className="primary" disabled={busy} aria-busy={busy} onClick={() => decide(a, "approve")}>
              Approve this action
            </button>
            <button className="danger" disabled={busy} aria-busy={busy} onClick={() => decide(a, "reject")}>
              Reject
            </button>
          </div>
        </div>
      ))}
      {error && <div className="banner banner-bad" role="alert">{error}</div>}
    </section>
  );
}
