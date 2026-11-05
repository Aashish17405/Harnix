import { useEffect, useState } from "react";
import { api } from "../api";
import { formatDuration } from "../lib";
import type { Task } from "../types";
import { isActive } from "../types";
import { MockBadge, OutcomeBadge, StatusBadge } from "./Badges";

export function TaskListView() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await api.listTasks(50, 0);
        if (!alive) return;
        setTasks(r.items);
        setTotal(r.total);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const i = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(i);
    };
  }, []);

  return (
    <section className="card" aria-label="Tasks">
      <h2>
        Tasks <span className="muted">({total})</span>
      </h2>
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}
      {tasks && tasks.length === 0 && <p className="muted">No tasks yet. Create one with the form.</p>}
      <ul className="task-list">
        {tasks?.map((t) => (
          <li key={t.id}>
            <a href={`#/tasks/${t.id}`} className="task-link">
              <span className="task-title">{t.title}</span>
              <span className="task-meta">
                <StatusBadge status={t.status} />
                <OutcomeBadge outcome={t.outcome} />
                <MockBadge provider={t.provider} />
                <span className="muted">
                  {t.repo_path.split(/[\\/]/).pop()} · {isActive(t.status) || t.finished_at ? formatDuration(t.duration_seconds) : "not started"}
                </span>
              </span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
