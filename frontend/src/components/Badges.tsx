import type { Task } from "../types";

const LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Awaiting approval",
  verifying: "Verifying",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge status-${status}`} data-testid="status-badge">
      {LABEL[status] ?? status}
    </span>
  );
}

const OUTCOME: Record<string, { text: string; cls: string; title: string }> = {
  verified: {
    text: "Verified",
    cls: "ok",
    title: "Independent checks passed (a test or acceptance check ran and passed)",
  },
  unverified: {
    text: "Not verified",
    cls: "warn",
    title: "The task finished but no test/acceptance check could confirm it",
  },
  verification_failed: {
    text: "Verification failed",
    cls: "bad",
    title: "Independent checks failed",
  },
};

export function OutcomeBadge({ outcome }: { outcome: Task["outcome"] }) {
  if (!outcome) return null;
  const o = OUTCOME[outcome];
  return (
    <span className={`badge outcome-${o.cls}`} title={o.title}>
      {o.text}
    </span>
  );
}

export function MockBadge({ provider }: { provider: string | null }) {
  if (provider !== "scripted-mock") return null;
  return (
    <span className="badge outcome-warn" title="Scripted mock provider: not a real model">
      MOCK PROVIDER
    </span>
  );
}

export function CheckStatus({ status }: { status: string }) {
  const cls = status === "passed" ? "ok" : status === "failed" ? "bad" : "warn";
  return <span className={`badge outcome-${cls}`}>{status}</span>;
}
