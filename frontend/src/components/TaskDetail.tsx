import { KnowledgeTab } from "./KnowledgeTab";
import { TaskPipeline } from "./TaskPipeline";
import { useEffect, useState } from "react";
import { api, fetchImageUrl, reportUrl } from "../api";
import { formatDuration } from "../lib";
import { useTaskLive } from "../hooks";
import { type ImageInfo, type Task, isActive, isTerminal } from "../types";
import { MockBadge, OutcomeBadge, StatusBadge } from "./Badges";
import { DiffView } from "./DiffView";
import { MemoryPanel } from "./MemoryPanel";
import { THINKING } from "./TaskForm";
import {
  ApplyPanel,
  ApprovalPanel,
  Timeline,
  ToolCalls,
  VerificationPanel,
} from "./Panels";
import { Loading } from "./Spinner";

const TABS = [
  "Pipeline",
  "Timeline",
  "Tool calls",
  "Verification",
  "Changes",
  "Report",
  "Knowledge",
] as const;
type Tab = (typeof TABS)[number];

export function TaskDetail({ taskId }: { taskId: string }) {
  const live = useTaskLive(taskId);
  const { task } = live;
  const [tab, setTab] = useState<Tab>("Pipeline");
  const [actionError, setActionError] = useState<string | null>(null);

  if (!task) {
    return live.error ? (
      <div className="banner banner-bad" role="alert">
        {live.error}
      </div>
    ) : (
      <Loading>Loading…</Loading>
    );
  }

  async function act(fn: () => Promise<unknown>, after?: (r: unknown) => void) {
    setActionError(null);
    try {
      const r = await fn();
      after?.(r);
      live.refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }

  const verdict = task.report?.independent_verification?.outcome ?? null;
  const tokens = task.input_tokens + task.output_tokens;
  return (
    <div className="detail-page">
      <header className="detail-head">
        <div>
          <h2>{task.title}</h2>
          <div className="row">
            <StatusBadge status={task.status} />
            <OutcomeBadge outcome={task.outcome} />
            <MockBadge provider={task.provider} />
            {task.cancel_requested && isActive(task.status) && (
              <span className="badge outcome-warn">cancelling…</span>
            )}
            <span className="muted mono">{task.repo_path}</span>
          </div>
        </div>
        <div className="row">
          {task.status === "queued" && (
            <button
              className="primary"
              onClick={() => act(() => api.runTask(task.id))}
            >
              Run
            </button>
          )}
          {(task.status === "queued" || isActive(task.status)) && (
            <button
              className="danger"
              disabled={task.cancel_requested && isActive(task.status)}
              onClick={() => act(() => api.cancelTask(task.id))}
            >
              Cancel
            </button>
          )}
          {isTerminal(task.status) && (
            <button
              onClick={() =>
                act(
                  () => api.rerunTask(task.id),
                  (t) =>
                    (location.hash = `#/tasks/${(t as { id: string }).id}`),
                )
              }
            >
              Re-run
            </button>
          )}
          {task.report && (
            <>
              <a
                className="button"
                href={reportUrl(task.id, "md")}
                target="_blank"
                rel="noreferrer"
              >
                Report (md)
              </a>
              <a
                className="button"
                href={reportUrl(task.id, "json")}
                target="_blank"
                rel="noreferrer"
              >
                Report (json)
              </a>
            </>
          )}
        </div>
      </header>

      {(actionError || live.error) && (
        <div className="banner banner-bad" role="alert">
          {actionError ?? live.error}
        </div>
      )}
      {task.error && task.status !== "cancelled" && (
        <div className="banner banner-bad" role="alert">
          {task.error}
        </div>
      )}
      {task.report?.false_success_claim && (
        <div className="banner banner-bad" role="alert">
          The agent claimed the task was done, but independent verification
          failed.
        </div>
      )}
      {task.status === "completed" && task.outcome === "unverified" && (
        <div className="banner banner-warn" role="status">
          Finished, but not verified: no test or acceptance check could confirm
          the result. Review the changes yourself.
        </div>
      )}

      <ApprovalPanel approvals={live.approvals} onDecided={live.refresh} />
      {isTerminal(task.status) && task.session_id && !task.discarded && (
        <FollowUp task={task} />
      )}

      {task.session_id && (task.turn ?? 1) > 1 && (
        <p className="help" role="note">
          Message {task.turn} of a conversation: the agent remembered the
          earlier messages and worked in the same workspace.
        </p>
      )}
      {task.images && task.images.length > 0 && (
        <TaskImages taskId={task.id} images={task.images} />
      )}

      <dl className="stats" aria-label="Resource usage">
        <div>
          <dt>Duration</dt>
          <dd>{formatDuration(task.duration_seconds)}</dd>
        </div>
        <div>
          <dt>Iterations</dt>
          <dd>
            {task.iterations} / {task.max_iterations}
          </dd>
        </div>
        <div>
          <dt>Model calls</dt>
          <dd>{task.model_calls}</dd>
        </div>
        <div>
          <dt>Tool calls</dt>
          <dd>
            {task.tool_calls} ({task.tool_failures} failed,{" "}
            {task.policy_denials} denied)
          </dd>
        </div>
        <div>
          <dt>Recoveries</dt>
          <dd>{task.recovery_attempts}</dd>
        </div>
        <div>
          <dt>Tokens</dt>
          <dd>
            {tokens
              ? `${task.input_tokens} in / ${task.output_tokens} out`
              : "-"}
          </dd>
        </div>
        <div>
          <dt>Est. cost</dt>
          <dd>
            {task.estimated_cost_usd != null
              ? `$${task.estimated_cost_usd}`
              : "n/a (set pricing)"}
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>
            {task.provider ?? "-"}
            {task.model ? ` · ${task.model}` : ""}
          </dd>
        </div>
        <div>
          <dt>Thinking</dt>
          <dd>
            {task.thinking
              ? (THINKING.find((t) => t.value === task.thinking)?.label ??
                task.thinking)
              : "server default"}
          </dd>
        </div>
        {task.skills && task.skills.length > 0 && (
          <div>
            <dt>Skills</dt>
            <dd>{task.skills.join(", ")}</dd>
          </div>
        )}
        {task.knowledge_project && (
          <div>
            <dt>Knowledge</dt>
            <dd>{task.knowledge_project}</dd>
          </div>
        )}
      </dl>

      <div role="tablist" aria-label="Task views" className="tabs">
        {TABS.filter((t) => t !== "Knowledge" || task.knowledge_project).map(
          (t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={tab === t ? "tab active" : "tab"}
              onClick={() => setTab(t)}
            >
              {t}
              {t === "Changes" && live.diff?.files.length
                ? ` (${live.diff.files.length})`
                : ""}
            </button>
          ),
        )}
        <span className="muted live">
          {isActive(task.status)
            ? live.connected
              ? "● live"
              : "○ reconnecting"
            : ""}
        </span>
      </div>

      <div role="tabpanel" className="card">
        {tab === "Pipeline" && (
          <TaskPipeline task={task} events={live.events} />
        )}
        {tab === "Timeline" && (
          <Timeline events={live.events} live={isActive(task.status)} />
        )}
        {tab === "Tool calls" && <ToolCalls events={live.events} />}
        {tab === "Verification" && (
          <VerificationPanel items={live.verifications} verdict={verdict} />
        )}
        {tab === "Changes" && (
          <>
            {task.status === "completed" &&
              (live.diff?.files.length ?? 0) > 0 && (
                <ApplyPanel
                  key={task.id}
                  taskId={task.id}
                  verified={task.outcome === "verified"}
                  alreadyApplied={live.events.some(
                    (e) => e.type === "task.applied",
                  )}
                />
              )}
            <DiffView
              diff={live.diff?.diff ?? ""}
              files={live.diff?.files ?? []}
            />
          </>
        )}
        {tab === "Report" && <ReportView task={task} />}
        {tab === "Knowledge" && task.knowledge_project && (
          <KnowledgeTab task={task} />
        )}
      </div>

      <details className="card">
        <summary>Task definition</summary>
        <h4>Instructions</h4>
        <pre className="raw">{task.instructions}</pre>
        {task.acceptance_criteria.length > 0 && (
          <>
            <h4>Acceptance criteria</h4>
            <ul>
              {task.acceptance_criteria.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </>
        )}
        {task.verification_commands.length > 0 && (
          <>
            <h4>Verification commands</h4>
            <ul>
              {task.verification_commands.map((c) => (
                <li key={c}>
                  <code>{c}</code>
                </li>
              ))}
            </ul>
          </>
        )}
      </details>

      <MemoryPanel repo={task.repo_path} />
    </div>
  );
}

function ReportView({
  task,
}: {
  task: NonNullable<ReturnType<typeof useTaskLive>["task"]>;
}) {
  const r = task.report;
  if (!r)
    return (
      <p className="muted">The report is generated when the task finishes.</p>
    );
  return (
    <div>
      <p>
        Agent finish reason: <strong>{r.agent.finish_reason}</strong> · agent
        claimed done: <strong>{String(r.agent.claimed_done)}</strong> ·
        execution: <strong>{r.execution_mode}</strong>
      </p>
      {r.independent_verification && (
        <p>
          Independent verdict:{" "}
          <strong>{r.independent_verification.outcome}</strong>
          {r.independent_verification.reasons.length > 0 &&
            ` — ${r.independent_verification.reasons.join("; ")}`}
        </p>
      )}
      <h4>Known limitations</h4>
      <ul>
        {r.known_limitations.length ? (
          r.known_limitations.map((x) => <li key={x}>{x}</li>)
        ) : (
          <li>none recorded</li>
        )}
      </ul>
      <h4>
        Agent's final message{" "}
        <span className="muted">(a claim, not evidence)</span>
      </h4>
      <pre className="raw">{r.agent.final_message || "(none)"}</pre>
    </div>
  );
}

function TaskImages({
  taskId,
  images,
}: {
  taskId: string;
  images: ImageInfo[];
}) {
  const [urls, setUrls] = useState<Record<number, string>>({});
  useEffect(() => {
    let alive = true;
    const made: string[] = [];
    for (const im of images) {
      fetchImageUrl(taskId, im.index).then(
        (u) => {
          made.push(u);
          if (alive) setUrls((cur) => ({ ...cur, [im.index]: u }));
        },
        () => undefined, // a missing thumbnail is not worth an error
      );
    }
    return () => {
      alive = false;
      made.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [taskId, images]);
  return (
    <section aria-label="Attached images" className="images-attached">
      <h3>Attached images</h3>
      <ul className="thumbs">
        {images.map((im) => (
          <li key={im.index}>
            {urls[im.index] ? (
              <img
                src={urls[im.index]}
                alt={im.name}
                title={`${im.name}${im.width ? ` (${im.width}x${im.height})` : ""}`}
              />
            ) : (
              <span className="help">{im.name}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Another message in the same conversation: the agent remembers everything above and keeps the workspace. */
export function FollowUp({ task }: { task: Task }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const t = await api.createTask({
        repo_path: task.repo_path,
        instructions: text.trim(),
        acceptance_criteria: [],
        verification_commands: [],
        session_id: task.session_id ?? undefined,
      });
      await api.runTask(t.id);
      setText("");
      location.hash = `#/tasks/${t.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card followup" onSubmit={send} aria-label="Follow up">
      <label>
        Follow up{" "}
        <span className="help">
          (same conversation: the agent remembers this and keeps the workspace)
        </span>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Now also update the docs…"
        />
      </label>
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}
      <button className="primary" type="submit" disabled={busy || !text.trim()} aria-busy={busy}>
        {busy ? "Sending…" : "Send"}
      </button>
    </form>
  );
}
