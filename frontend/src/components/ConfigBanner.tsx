import type { HarnixConfig, Health } from "../types";

/** Surfaces setup problems with the exact fix, so a blocked run is never a mystery. */
export function ConfigBanner({ health, config }: { health: Health | null; config: HarnixConfig | null }) {
  if (!health) return null;
  const notes: { level: "bad" | "warn"; text: string }[] = [];
  if (!health.provider.configured)
    notes.push({ level: "bad", text: `Model provider not configured: ${health.provider.problem}` });
  if (!health.sandbox.available)
    notes.push({ level: "bad", text: `Execution sandbox unavailable: ${health.sandbox.detail}` });
  if (config && config.allowed_repo_roots.length === 0)
    notes.push({
      level: "bad",
      text: "No repository roots are authorized. Set HARNIX_ALLOWED_REPO_ROOTS to the directory that contains your repositories and restart the backend.",
    });
  if (health.mock_provider)
    notes.push({ level: "warn", text: "Mock provider active: runs replay a fixed script and do not reflect real model behaviour." });
  if (health.execution_mode === "local")
    notes.push({
      level: "warn",
      text: "Local execution mode: agent commands run on this host without container isolation. Use it only with repositories you trust.",
    });
  if (!notes.length) return null;
  return (
    <div className="banners" role="region" aria-label="Configuration notices">
      {notes.map((n, i) => (
        <div key={i} className={`banner banner-${n.level}`} role={n.level === "bad" ? "alert" : "status"}>
          {n.text}
        </div>
      ))}
    </div>
  );
}
