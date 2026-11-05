import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import {
  type Attached,
  IMAGE_TYPES,
  MAX_IMAGES,
  prepareImage,
  sizeLabel,
} from "../images";
import type {
  HarnixConfig,
  KnowledgeProject,
  Skill,
  Task,
  TaskCreate,
  ThinkingLevel,
} from "../types";

export const THINKING: { value: ThinkingLevel; label: string }[] = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
];

const lines = (s: string) =>
  s
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

export function TaskForm({
  config,
  onCreated,
}: {
  config: HarnixConfig | null;
  onCreated: (t: Task) => void;
}) {
  const [repo, setRepo] = useState("");
  const [instructions, setInstructions] = useState("");
  const [criteria, setCriteria] = useState("");
  const [commands, setCommands] = useState("");
  const [maxIter, setMaxIter] = useState("");
  const [maxDur, setMaxDur] = useState("");
  const [startNow, setStartNow] = useState(true);
  const [thinking, setThinking] = useState<ThinkingLevel | "">("");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [kprojects, setKprojects] = useState<KnowledgeProject[]>([]);
  const [kproject, setKproject] = useState("");
  const [images, setImages] = useState<Attached[]>([]);
  const [imageNote, setImageNote] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    Promise.resolve()
      .then(() => api.skills())
      .then(
        (s) => setSkills(s?.items ?? []),
        () => setSkills([]),
      ); // optional: no skills configured is fine
    Promise.resolve()
      .then(() => api.knowledgeProjects())
      .then(
        (p) => setKprojects(p?.projects ?? []),
        () => setKprojects([]),
      ); // optional
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function addImages(files: File[]) {
    setImageNote(null);
    for (const f of files) {
      try {
        const a = await prepareImage(f);
        setImages((cur) => {
          if (cur.length >= MAX_IMAGES) {
            setImageNote(
              `At most ${MAX_IMAGES} images can be attached to one task.`,
            );
            return cur;
          }
          return [...cur, a];
        });
      } catch (err) {
        setImageNote(err instanceof Error ? err.message : String(err));
      }
    }
  }
  const imageFiles = (items: DataTransferItemList | undefined): File[] =>
    Array.from(items ?? [])
      .filter((i) => i.kind === "file" && i.type.startsWith("image/"))
      .map((i) => i.getAsFile())
      .filter((f): f is File => !!f);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (!repo.trim()) local.repo_path = "Repository path is required";
    if (!instructions.trim())
      local.instructions = "Describe what the agent should do";
    setFieldErrors(local);
    setError(null);
    if (Object.keys(local).length) return;
    const body: TaskCreate = {
      repo_path: repo.trim(),
      instructions: instructions.trim(),
      acceptance_criteria: lines(criteria),
      verification_commands: lines(commands),
    };
    if (thinking) body.thinking = thinking;
    if (chosen.length) body.skills = chosen;
    if (kproject) body.knowledge_project = kproject;
    if (images.length)
      body.images = images.map((i) => ({
        name: i.name,
        media_type: i.mediaType,
        data: i.data,
      }));
    if (maxIter) body.max_iterations = Number(maxIter);
    if (maxDur) body.max_duration_seconds = Number(maxDur);
    setBusy(true);
    try {
      let task = await api.createTask(body);
      if (startNow) {
        try {
          task = await api.runTask(task.id);
        } catch (err) {
          // Task exists; show it so the user can fix configuration and press Run.
          setError(
            `Task created but not started: ${err instanceof Error ? err.message : String(err)}`,
          );
          onCreated(task);
          return;
        }
      }
      onCreated(task);
    } catch (err) {
      if (err instanceof ApiError && err.details) {
        setFieldErrors(
          Object.fromEntries(err.details.map((d) => [d.field, d.message])),
        );
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card form"
      onSubmit={submit}
      aria-label="Create task"
      noValidate
    >
      <h2>New task</h2>
      <label>
        Repository
        <input
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder={
            config?.allowed_repo_roots[0] &&
            config.allowed_repo_roots[0] !== "*"
              ? `${config.allowed_repo_roots[0]}/my-project`
              : "/path/to/repository"
          }
          aria-invalid={!!fieldErrors.repo_path}
          aria-describedby="repo-help"
        />
        {fieldErrors.repo_path && (
          <span className="field-error">{fieldErrors.repo_path}</span>
        )}
      </label>
      {kprojects.length > 0 && (
        <label>
          Knowledge{" "}
          <span className="help">
            (optional: the agent gets that project&apos;s BRDs, stories, design,
            code and tests as context)
          </span>
          <select
            value={kproject}
            onChange={(e) => setKproject(e.target.value)}
          >
            <option value="">none</option>
            {kprojects.map((p) => (
              <option key={p.project} value={p.project}>
                {p.project} ({p.sources} sources)
              </option>
            ))}
          </select>
        </label>
      )}
      <div id="repo-help" className="help">
        Authorized roots:{" "}
        {config?.allowed_repo_roots.length ? (
          config.allowed_repo_roots.map((r) =>
            r === "*" ? (
              <span key={r} className="chip">
                any folder
              </span>
            ) : (
              <button
                type="button"
                key={r}
                className="chip"
                onClick={() => setRepo(r + "/")}
              >
                {r}
              </button>
            ),
          )
        ) : (
          <em>none configured (HARNIX_ALLOWED_REPO_ROOTS)</em>
        )}
        . Forge works on an isolated copy; your repository is never modified.
      </div>
      <label>
        Instructions
        <textarea
          rows={5}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="Inspect the repository, find why test_x fails, fix it, and run the tests."
          aria-invalid={!!fieldErrors.instructions}
          onPaste={(e) => {
            const files = imageFiles(e.clipboardData?.items);
            if (files.length) {
              e.preventDefault();
              void addImages(files);
            }
          }}
        />
        {fieldErrors.instructions && (
          <span className="field-error">{fieldErrors.instructions}</span>
        )}
      </label>
      <div
        className={dragging ? "images dropping" : "images"}
        onDragOver={(e) => {
          if (Array.from(e.dataTransfer.types).includes("Files")) {
            e.preventDefault();
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          setDragging(false);
          if (e.dataTransfer.files.length) {
            e.preventDefault();
            void addImages(Array.from(e.dataTransfer.files));
          }
        }}
      >
        <label className="button">
          Attach images
          <input
            type="file"
            accept={IMAGE_TYPES.join(",")}
            multiple
            hidden
            aria-label="Attach images"
            onChange={(e) => {
              void addImages(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </label>
        <span className="help">
          {" "}
          or paste (Ctrl+V) or drop them here. Up to {MAX_IMAGES}; the model
          looks at them with the task.
        </span>
        {images.length > 0 && (
          <ul className="thumbs" aria-label="Attached images">
            {images.map((im, i) => (
              <li key={`${im.name}-${i}`}>
                <img
                  src={im.url}
                  alt={im.name}
                  title={`${im.name} (${sizeLabel(im.bytes)})`}
                />
                <button
                  type="button"
                  className="x"
                  aria-label={`Remove ${im.name}`}
                  onClick={() => setImages(images.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        {images.length > 0 && (
          <div className="help">
            Images are sent to the model provider as they are. Do not attach
            anything secret.
          </div>
        )}
        {imageNote && (
          <div className="field-error" role="alert">
            {imageNote}
          </div>
        )}
      </div>
      <label>
        Acceptance criteria{" "}
        <span className="help">(one per line, optional)</span>
        <textarea
          rows={2}
          value={criteria}
          onChange={(e) => setCriteria(e.target.value)}
        />
      </label>
      <label>
        Verification commands{" "}
        <span className="help">
          (one per line, optional; auto-detected tests always run)
        </span>
        <textarea
          rows={2}
          value={commands}
          onChange={(e) => setCommands(e.target.value)}
          placeholder="python -m pytest tests/test_x.py"
        />
        {fieldErrors.verification_commands && (
          <span className="field-error">
            {fieldErrors.verification_commands}
          </span>
        )}
      </label>
      <label>
        Thinking{" "}
        <span className="help">(how much the model reasons before acting)</span>
        <select
          value={thinking}
          onChange={(e) => setThinking(e.target.value as ThinkingLevel | "")}
        >
          <option value="">Server default</option>
          {THINKING.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </label>
      {skills.length > 0 && (
        <fieldset className="skills">
          <legend>
            Skills to load up front{" "}
            <span className="help">
              (the agent can also load listed skills itself)
            </span>
          </legend>
          {skills.map((s) => (
            <label key={s.id} className="check" title={s.description}>
              <input
                type="checkbox"
                checked={chosen.includes(s.id)}
                onChange={(e) =>
                  setChosen(
                    e.target.checked
                      ? [...chosen, s.id]
                      : chosen.filter((x) => x !== s.id),
                  )
                }
              />{" "}
              {s.name}{" "}
              <span className="help">
                {s.description || "(only when chosen here)"}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      <details>
        <summary>Limits</summary>
        <div className="row">
          <label>
            Max iterations
            <input
              type="number"
              min={1}
              value={maxIter}
              onChange={(e) => setMaxIter(e.target.value)}
              placeholder={String(config?.limits.max_iterations ?? "")}
            />
          </label>
          <label>
            Max duration (s)
            <input
              type="number"
              min={10}
              value={maxDur}
              onChange={(e) => setMaxDur(e.target.value)}
              placeholder={String(config?.limits.max_duration_seconds ?? "")}
            />
          </label>
        </div>
      </details>
      <label className="check">
        <input
          type="checkbox"
          checked={startNow}
          onChange={(e) => setStartNow(e.target.checked)}
        />{" "}
        Start immediately
      </label>
      {error && (
        <div className="banner banner-bad" role="alert">
          {error}
        </div>
      )}
      <button className="primary" type="submit" disabled={busy} aria-busy={busy}>
        {busy ? "Creating…" : startNow ? "Create and run" : "Create task"}
      </button>
    </form>
  );
}
