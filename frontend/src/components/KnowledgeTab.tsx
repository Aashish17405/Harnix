import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type {
  EvalCriterion,
  KnowledgeEdgeRow,
  Task,
  TaskEvaluation,
} from "../types";
import { ContextGraphView } from "./ContextGraphView";
import { Loading } from "./Spinner";

const msg = (e: unknown) =>
  e instanceof ApiError || e instanceof Error ? e.message : String(e);
const ICON: Record<string, string> = {
  pass: "✓",
  fail: "✗",
  warn: "!",
  info: "i",
};

function Criterion({
  c,
  onReview,
}: {
  c: EvalCriterion;
  onReview: (id: string, v: "pass" | "fail" | "unsure", note: string) => void;
}) {
  const [note, setNote] = useState(c.review?.note ?? "");
  return (
    <li className="criterion">
      <div>
        <b>{c.source}</b>
        {c.source_version ? (
          <span className="chip">v{c.source_version}</span>
        ) : null}{" "}
        {c.text}
        {c.test && (
          <div className={`help crit-test ${c.test.likely ? "likely" : ""}`}>
            {c.test.likely
              ? "Likely covered by"
              : "Closest test (not a clear match)"}
            : <code>{c.test.file}</code> · <code>{c.test.name}</code>{" "}
            (similarity {c.test.similarity.toFixed(2)})
          </div>
        )}
      </div>
      <div className="row">
        {(["pass", "fail", "unsure"] as const).map((v) => (
          <button
            key={v}
            className={c.review?.verdict === v ? `primary v-${v}` : "secondary"}
            aria-pressed={c.review?.verdict === v}
            onClick={() => onReview(c.id, v, note)}
          >
            {v === "pass" ? "Meets it" : v === "fail" ? "Does not" : "Not sure"}
          </button>
        ))}
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
          aria-label={`Note for ${c.id}`}
        />
        {c.review && <span className="help">reviewed by {c.review.by}</span>}
      </div>
    </li>
  );
}

/** What the agent was given (context graph) and how to judge what it produced. */
export function KnowledgeTab({ task }: { task: Task }) {
  const project = task.knowledge_project ?? "";
  const [ev, setEv] = useState<TaskEvaluation | null>(null);
  const [proposals, setProposals] = useState<KnowledgeEdgeRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const e = await api.taskEvaluation(task.id);
      setEv(e);
      setProposals(
        (await api.knowledgeEdges(project, "proposed")).edges.filter(
          (x) => x.task_id === task.id,
        ),
      );
      setError(null);
    } catch (x) {
      setError(msg(x));
    }
  }, [task.id, project]);
  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load, task.status]);

  async function review(
    id: string,
    v: "pass" | "fail" | "unsure",
    note: string,
  ) {
    try {
      setEv(await api.reviewCriterion(task.id, id, v, note));
    } catch (x) {
      setError(msg(x));
    }
  }
  async function decide(id: string, d: "promote" | "reject") {
    try {
      await api.decideEdge(id, d);
      await load();
    } catch (x) {
      setError(msg(x));
    }
  }

  return (
    <div className="knowledge-tab">
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}
      {!ev && !error && <Loading>Loading…</Loading>}
      {ev && (
        <>
          <div
            className={`banner ${ev.verdict === "pass" ? "banner-ok" : ev.verdict === "fail" ? "banner-bad" : "banner-warn"}`}
            role="status"
          >
            <b>Evaluation: {ev.verdict}</b> · {ev.criteria_summary.pass} of{" "}
            {ev.criteria.length} acceptance criteria passed by a person,{" "}
            {ev.criteria_summary.fail} failed, {ev.criteria_summary.unreviewed}{" "}
            not reviewed yet
            <div className="help">{ev.note}</div>
          </div>

          <h3>Context graph</h3>
          <p className="muted">
            Exactly what the agent was given for this task. Click a box to read
            the content it received for it.
          </p>
          {ev.bundle_id ? (
            <ContextGraphView bundleId={ev.bundle_id} />
          ) : (
            <p className="muted">No context was built for this task.</p>
          )}

          <h3>Automatic checks</h3>
          <ul className="plain checks">
            {ev.checks.map((c) => (
              <li key={c.name} className={`check c-${c.status}`}>
                <span className="ci" aria-hidden>
                  {ICON[c.status]}
                </span>
                <b>{c.name}</b> <span className="muted">{c.detail}</span>
              </li>
            ))}
          </ul>

          <h3>Traceability: requirement → code → tests</h3>
          <table className="trace">
            <thead>
              <tr>
                <th>Requirement</th>
                <th>Linked code and tests</th>
                <th>Code changed</th>
                <th>Test linked</th>
              </tr>
            </thead>
            <tbody>
              {ev.traceability.map((t) => (
                <tr key={t.requirement}>
                  <td>
                    <b>{t.requirement}</b>{" "}
                    <span className="muted">
                      {t.title.replace(/^[^:]*:\s*/, "").slice(0, 50)}
                    </span>
                  </td>
                  <td>
                    {t.links.length === 0
                      ? "—"
                      : t.links.map((l) => (
                          <span
                            key={l.external_id + l.type}
                            className={`chip st-${l.status}`}
                          >
                            {l.external_id}
                            {l.changed ? " (changed)" : ""}
                          </span>
                        ))}
                  </td>
                  <td>
                    {t.code_changed
                      ? "yes"
                      : t.links.some((l) => l.kind === "code")
                        ? "no"
                        : "—"}
                  </td>
                  <td>{t.test_linked ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Acceptance criteria: your review</h3>
          {ev.criteria.length === 0 ? (
            <p className="muted">
              The context holds no acceptance criteria (a story or ticket with
              an “Acceptance criteria” list).
            </p>
          ) : (
            <ul className="plain">
              {ev.criteria.map((c) => (
                <Criterion
                  key={c.id + (c.review?.at ?? "")}
                  c={c}
                  onReview={review}
                />
              ))}
            </ul>
          )}

          <h3>Relationships the agent proposes</h3>
          {proposals.length === 0 ? (
            <p className="muted">
              None (only verified work proposes relationships).
            </p>
          ) : (
            <ul className="plain">
              {proposals.map((p) => (
                <li key={p.edge_id}>
                  {p.from} <b>{p.type.replace("_", " ")}</b> {p.to}{" "}
                  <span className="help">{p.evidence}</span>
                  <span className="row">
                    <button onClick={() => decide(p.edge_id, "promote")}>
                      Confirm
                    </button>
                    <button
                      className="secondary"
                      onClick={() => decide(p.edge_id, "reject")}
                    >
                      Reject
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
