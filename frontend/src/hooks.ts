import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamUrl } from "./api";
import type { Approval, Diff, HarnixEvent, Task, Verification } from "./types";
import { isActive } from "./types";

export interface TaskLive {
  task: Task | null;
  events: HarnixEvent[];
  verifications: Verification[];
  approvals: Approval[];
  diff: Diff | null;
  error: string | null;
  connected: boolean;
  refresh: () => void;
}

/**
 * Loads a task and keeps it live: an SSE stream feeds the timeline; each batch of events
 * triggers a (debounced) refetch of the derived resources, so every view shows backend truth.
 */
export function useTaskLive(taskId: string): TaskLive {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<HarnixEvent[]>([]);
  const [verifications, setVerifications] = useState<Verification[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const lastId = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(
    () =>
      Promise.all([
        api.getTask(taskId),
        api.verifications(taskId),
        api.approvals(taskId),
        api.diff(taskId),
      ])
        .then(([t, v, a, d]) => {
          setTask(t);
          setVerifications(v);
          setApprovals(a);
          setDiff(d);
          setError(null);
        })
        .catch((e) => setError(e instanceof Error ? e.message : String(e))),
    [taskId],
  );

  const schedule = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(load, 300);
  }, [load]);

  useEffect(() => {
    lastId.current = 0; // state resets by remount: <TaskDetail key={taskId}>
    void load();
    const es = new EventSource(streamUrl(taskId, 0));
    es.onopen = () => setConnected(true);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as HarnixEvent;
      if (ev.id <= lastId.current) return; // reconnects can replay
      lastId.current = ev.id;
      setEvents((prev) => [...prev, ev]);
      schedule();
    };
    es.addEventListener("end", () => {
      es.close();
      setConnected(false);
      void load();
    });
    es.onerror = () => setConnected(false); // EventSource retries on its own, resuming via Last-Event-ID
    return () => {
      es.close();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [taskId, load, schedule]);

  // Safety net while a task is active: counters/status stay fresh even if the stream stalls.
  const active = task ? isActive(task.status) : false;
  useEffect(() => {
    if (!active) return;
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, [active, load]);

  return { task, events, verifications, approvals, diff, error, connected, refresh: load };
}
