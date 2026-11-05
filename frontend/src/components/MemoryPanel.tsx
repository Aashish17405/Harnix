import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { MemoryNote } from "../types";

/** Project memory of one repository: notes every future task on it starts with. */
export function MemoryPanel({ repo }: { repo: string }) {
  const [notes, setNotes] = useState<MemoryNote[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api.memory(repo).then(
        (n) => {
          setNotes(n);
          setError(null);
        },
        (e) => setError(e instanceof Error ? e.message : String(e)),
      ),
    [repo],
  );
  useEffect(() => {
    void load();
  }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    try {
      await api.addMemory(repo, draft.trim());
      setDraft("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteMemory(repo, id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="card" aria-label="Project memory">
      <h3>Project memory</h3>
      <p className="help">
        Notes every future task on this repository starts with. Agent notes are unverified and shown to the model as
        such; remove anything wrong.
      </p>
      {error && <div className="banner banner-warn">{error}</div>}
      {notes && notes.length === 0 && <p className="help">No notes yet.</p>}
      {notes && notes.length > 0 && (
        <ul className="memory">
          {notes.map((n) => (
            <li key={n.id}>
              <span className={`chip ${n.source === "agent" ? "chip-agent" : ""}`}>{n.source}</span> {n.content}{" "}
              <button type="button" className="link" onClick={() => remove(n.id)} aria-label={`Delete note ${n.content}`}>
                delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="row">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={500}
          placeholder="e.g. Integration tests need the database container running"
          aria-label="New memory note"
        />
        <button type="submit" disabled={!draft.trim()}>
          Add note
        </button>
      </form>
    </section>
  );
}
