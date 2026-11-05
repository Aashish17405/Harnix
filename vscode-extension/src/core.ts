/** Harnix API client + event helpers. No `vscode` import here, so it is unit-testable in plain Node. */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export interface HarnixEvent {
  id: number;
  task_id: string;
  ts: string;
  type: string;
  data: Json;
}

export class HarnixApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
  /** status 0: the server could not be reached at all */
  get unreachable(): boolean {
    return this.status === 0;
  }
}

/** Incremental Server-Sent-Events parser: feed it text chunks, get complete events back. */
export class SseParser {
  private buf = "";
  private name = "message";
  private data: string[] = [];

  push(chunk: string): { event: string; data: Json }[] {
    this.buf += chunk;
    const out: { event: string; data: Json }[] = [];
    let nl: number;
    while ((nl = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, nl).replace(/\r$/, "");
      this.buf = this.buf.slice(nl + 1);
      if (line === "") {
        if (this.data.length) {
          const text = this.data.join("\n");
          try {
            out.push({ event: this.name, data: JSON.parse(text) });
          } catch {
            out.push({ event: this.name, data: null });
          }
        }
        this.name = "message";
        this.data = [];
      } else if (line.startsWith(":")) {
        continue;
      } else if (line.startsWith("event:")) {
        this.name = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        this.data.push(line.slice(5).replace(/^ /, ""));
      }
    }
    return out;
  }
}

export interface ApiOptions {
  baseUrl: string;
  getToken?: () => PromiseLike<string | undefined> | string | undefined;
  fetchImpl?: typeof fetch;
}

export class HarnixApi {
  private base: string;
  private fetchImpl: typeof fetch;

  constructor(private opts: ApiOptions) {
    this.base = opts.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  private async request(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const token = await this.opts.getToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.base}/api${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      });
    } catch (e) {
      if (signal?.aborted) throw e;
      throw new HarnixApiError(0, "unreachable", `Cannot reach the Harnix backend at ${this.base}`);
    }
    if (!res.ok) {
      let code = "error";
      let message = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        const err = body.error ?? {};
        code = err.code ?? code;
        message = err.message ?? (typeof body.detail === "string" ? body.detail : message);
      } catch {
        /* non-JSON error body */
      }
      throw new HarnixApiError(res.status, code, message);
    }
    return res;
  }

  private async json<T = Json>(method: string, path: string, body?: unknown): Promise<T> {
    return (await this.request(method, path, body)).json() as Promise<T>;
  }

  health = () => this.json("GET", "/health");
  config = () => this.json("GET", "/config");
  createTask = (body: Json) => this.json("POST", "/tasks", body);
  getTask = (id: string) => this.json("GET", `/tasks/${id}`);
  runTask = (id: string) => this.json("POST", `/tasks/${id}/run`);
  cancelTask = (id: string) => this.json("POST", `/tasks/${id}/cancel`);
  decide = (approvalId: string, approve: boolean) =>
    this.json("POST", `/approvals/${approvalId}/decision`, { decision: approve ? "approve" : "reject" });
  diff = (id: string) => this.json("GET", `/tasks/${id}/diff`);
  verifications = (id: string) => this.json<Json[]>("GET", `/tasks/${id}/verifications`);
  apply = (id: string, opts: { confirm?: boolean; dry_run?: boolean }) =>
    this.json("POST", `/tasks/${id}/apply`, { confirm: false, dry_run: false, ...opts });
  /** An image attached to a task, as base64 plus its type (for thumbnails of past sessions). */
  async imageData(id: string, index: number): Promise<{ mediaType: string; data: string }> {
    const res = await this.request("GET", `/tasks/${id}/images/${index}`);
    return { mediaType: res.headers.get("content-type") ?? "image/png", data: Buffer.from(await res.arrayBuffer()).toString("base64") };
  }
  /** One file as the task started ("baseline") or left it ("final"), for side-by-side review. */
  fileVersion = (id: string, path: string, version: "baseline" | "final") =>
    this.json<{ path: string; exists: boolean; binary: boolean; truncated: boolean; size: number; content: string }>(
      "GET",
      `/tasks/${id}/file?path=${encodeURIComponent(path)}&version=${version}`,
    );
  revert = (id: string, opts: { confirm?: boolean; dry_run?: boolean }) =>
    this.json("POST", `/tasks/${id}/revert`, { confirm: false, dry_run: false, ...opts });
  /** The newest tasks for one repository folder (the session history), newest first. */
  listTasks = (repoPath: string, limit = 50) =>
    this.json<{ items: Json[]; total: number }>("GET", `/tasks?limit=${limit}&repo_path=${encodeURIComponent(repoPath)}`);
  /** This workspace's conversations (sessions), most recently active first. */
  listSessions = (repoPath: string, limit = 50) =>
    this.json<{ items: Json[]; total: number }>("GET", `/sessions?limit=${limit}&repo_path=${encodeURIComponent(repoPath)}`);
  getSession = (id: string) => this.json("GET", `/sessions/${id}`);
  renameSession = (id: string, title: string) => this.json("PATCH", `/sessions/${id}`, { title });
  /** Go back to the end of turn `turn` (0 = the very start). Preview with dry_run, then confirm. */
  restoreSession = (id: string, turn: number, opts: { confirm?: boolean; dry_run?: boolean }) =>
    this.json("POST", `/sessions/${id}/restore`, { turn, confirm: false, dry_run: false, ...opts });
  /** All stored events of a task, oldest first (used to replay a past session). */
  async allEvents(taskId: string, maxEvents = 6000): Promise<HarnixEvent[]> {
    const out: HarnixEvent[] = [];
    let after = 0;
    while (out.length < maxEvents) {
      const page = await this.json<HarnixEvent[]>("GET", `/tasks/${taskId}/events?after=${after}&limit=1000`);
      if (!page.length) break;
      out.push(...page);
      after = page[page.length - 1].id;
    }
    return out;
  }

  /** Yields events until the server signals the end; reconnects (resuming after the last id) if the stream drops. */
  async *streamEvents(taskId: string, signal?: AbortSignal, after = 0): AsyncGenerator<HarnixEvent> {
    let last = after;
    let failures = 0;
    for (;;) {
      try {
        const res = await this.request("GET", `/tasks/${taskId}/events/stream?after=${last}`, undefined, signal);
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        const parser = new SseParser();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const m of parser.push(decoder.decode(value, { stream: true }))) {
            if (m.event === "end") return;
            if (m.data) {
              last = Math.max(last, m.data.id);
              failures = 0;
              yield m.data as HarnixEvent;
            }
          }
        }
      } catch (e) {
        if (signal?.aborted) return;
        if (e instanceof HarnixApiError && e.status !== 0) throw e;
      }
      if (signal?.aborted) return;
      if (++failures > 5) throw new HarnixApiError(0, "stream", "Lost the connection to the Harnix backend");
      await new Promise((r) => setTimeout(r, Math.min(failures, 3) * 500));
    }
  }
}

// ---- presentation helpers -----------------------------------------------------------------------

export type ItemKind = "agent" | "tool" | "ok" | "warn" | "error" | "info";
export interface DiffPreview {
  path: string;
  old: string;
  new: string;
  newFile?: boolean;
  lines?: number;
}
export interface TimelineItem {
  kind: ItemKind;
  text: string;
  detail?: string;
  file?: string; // workspace-relative path: the panel makes it clickable
  line?: number;
  diff?: DiffPreview; // inline preview of an edit the agent made
}

const clip = (s: unknown, max = 40): string => {
  const lines = String(s ?? "").split("\n");
  return lines.length <= max ? lines.join("\n") : lines.slice(0, max).join("\n") + `\n… (${lines.length - max} more lines)`;
};

export function shortArgs(args: Json, limit = 110): string {
  const text = Object.entries(args ?? {})
    .filter(([, v]) => v !== null && v !== "." && v !== false)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ")
    .replace(/\n/g, "\\n");
  return text.length <= limit ? text : text.slice(0, limit - 1) + "…";
}

/** Short, human-readable timeline entries; null = not worth showing. Never trusts event text as markup. */
export function describeEvent(e: HarnixEvent): TimelineItem | null {
  const d = e.data ?? {};
  switch (e.type) {
    case "workspace.created":
      return { kind: "info", text: `Isolated workspace ready (${d.execution_mode})` };
    case "project.inspected":
      return { kind: "info", text: `Detected ${(d.types ?? []).join(", ") || "unknown project"}; test command: ${d.commands?.test ?? "none"}` };
    case "model.response": {
      const th = d.thinking;
      const downgraded = th && th.applied && th.applied !== th.requested;
      const who = d.agent ? `[${d.agent}] ` : "";
      if (d.text) return { kind: "agent", text: who + String(d.text).trim().slice(0, 1500), detail: downgraded ? `thinking ${th.requested} -> ${th.applied}: ${th.reason ?? ""}` : undefined };
      return downgraded ? { kind: "warn", text: `Thinking ${th.requested} -> ${th.applied}`, detail: th.reason } : null;
    }
    case "task.references": {
      const inc = (d.included ?? []).map((i: Json) => `${i.path}${i.truncated || i.start !== 1 ? `:${i.start}-${i.end}` : ""}${i.truncated ? " (cut)" : ""}`);
      const skipped = (d.skipped ?? []).map((s: Json) => `could not attach ${s.path}: ${s.reason}`);
      if (!inc.length && !skipped.length) return null;
      return { kind: skipped.length ? "warn" : "info", text: inc.length ? `Attached ${inc.length} file(s): ${inc.join(", ")}` : "No referenced file could be attached", detail: skipped.join("\n") || undefined };
    }
    case "graph.indexed":
      if (d.skipped) return { kind: "warn", text: `Knowledge graph not re-indexed: ${d.skipped}` };
      return {
        kind: d.complete ? "info" : "warn",
        text: `Knowledge graph: ${d.files} files, ${d.symbols} symbols (${d.files_parsed} re-indexed, ${d.files_unchanged} unchanged, ${d.seconds}s)${d.complete ? "" : ": partial, the rest continues on the next run"}`,
      };
    case "graph.unavailable":
      return { kind: "warn", text: "Knowledge graph unavailable: continuing without it", detail: String(d.reason ?? "") || undefined };
    case "skills.available": {
      const listed = (d.skills ?? []).filter((k: Json) => k.listed).map((k: Json) => k.id);
      return listed.length ? { kind: "info", text: `Skills available: ${listed.join(", ")}` } : null;
    }
    case "skill.loaded":
      return { kind: "info", text: `Skill loaded: ${d.id} (${d.how})` };
    case "skill.missing":
      return { kind: "warn", text: `Skill not found: ${d.id}` };
    case "memory.context":
      return { kind: "info", text: `Project memory: ${d.shown} of ${d.notes} note(s) in context` };
    case "memory.saved":
      return { kind: "info", text: "Saved a project-memory note" };
    case "subagent.started":
      return { kind: "tool", text: `sub-agent ${d.agent}: ${d.description}`, detail: `thinking ${d.thinking}, at most ${d.max_steps} steps; tools: ${(d.tools ?? []).join(", ")}` };
    case "subagent.finished":
      return { kind: d.reason === "done" ? "ok" : "warn", text: `sub-agent ${d.agent} finished (${d.reason}, ${d.steps} steps)` };
    case "subagent.delivered":
      return { kind: "info", text: `Background report(s) delivered: ${(d.agents ?? []).join(", ")}` };
    case "subagent.waiting":
      return { kind: "info", text: `Waiting for ${d.running} background sub-agent(s) before finishing` };
    case "subagent.stopped":
      return { kind: "warn", text: "Background sub-agent(s) stopped: the task ended" };
    case "context.pruned":
      return { kind: "info", text: `Context pruned: ${d.results_pruned} old tool output(s), ~${d.tokens_reclaimed} tokens` };
    case "context.compacted":
      return { kind: "info", text: `Context compacted (${d.reason}): ~${d.tokens_before} -> ~${d.tokens_after} tokens` };
    case "context.compaction_failed":
      return { kind: "warn", text: `Context compaction failed (${d.reason}): ${d.error}` };
    case "graph.context":
      return { kind: "info", text: `Knowledge graph context added (${d.chars} chars, ${d.seed_files} focus file(s))` };
    case "tool.proposed": {
      const a = d.arguments ?? {};
      if (d.tool === "edit_file") return { kind: "tool", text: `edit_file ${a.path}`, file: a.path, diff: { path: a.path, old: clip(a.old_text), new: clip(a.new_text) } };
      if (d.tool === "write_file") return { kind: "tool", text: `write_file ${a.path}`, file: a.path, diff: { path: a.path, old: "", new: clip(a.content), newFile: true, lines: String(a.content ?? "").split("\n").length } };
      if (d.tool === "read_file") return { kind: "tool", text: `read_file ${a.path}${a.start_line ? ` (from line ${a.start_line})` : ""}`, file: a.path, line: a.start_line };
      if (d.tool === "delete_file") return { kind: "tool", text: `delete_file ${a.path}`, file: a.path };
      return { kind: "tool", text: `${d.tool} ${shortArgs(a)}`.trim() };
    }
    case "tool.authorization":
      if (d.decision === "deny") return { kind: "error", text: `Refused by policy: ${d.reason}` };
      if (d.decision === "approve") return { kind: "warn", text: `Needs your approval: ${d.reason}` };
      return null;
    case "tool.result": {
      // "Not approved: ..." is already shown by the approval.decided entry just before it
      if (!d.ok && String(d.error ?? "").startsWith("Not approved")) return null;
      if (!d.ok) return { kind: "error", text: String(d.error ?? "failed") };
      const x = d.data ?? {};
      if (x.exit_code !== undefined) {
        const tail = x.exit_code !== 0 ? String(x.output ?? "").split("\n").filter((l: string) => l.trim()).slice(-8).join("\n") : undefined;
        return { kind: x.exit_code === 0 ? "ok" : "warn", text: `exit ${x.exit_code} in ${x.duration_seconds ?? "?"}s`, detail: tail };
      }
      return null;
    }
    case "approval.decided":
      return { kind: d.approved ? "ok" : "warn", text: `${d.approved ? "Approved" : "Not approved"}: ${d.reason}` };
    case "verification.check":
      return { kind: d.status === "passed" ? "ok" : d.status === "failed" ? "error" : "warn", text: `[${d.status}] ${d.kind}: ${d.command} (${d.phase})` };
    case "verification.result":
      return { kind: d.outcome === "verified" ? "ok" : d.outcome === "verification_failed" ? "error" : "warn", text: `Verification: ${d.outcome}${d.reasons?.length ? " - " + d.reasons.join("; ") : ""}` };
    case "retry.decision":
      return { kind: "warn", text: d.kind === "verification_recovery" ? "Verification failed: asking the agent to fix it" : `Retrying (${d.kind})` };
    case "error":
      return { kind: "error", text: String(d.message ?? "error") };
    case "task.state":
      return d.to === "verifying" ? { kind: "info", text: "Running independent verification…" } : null;
    default:
      return null;
  }
}

// ---- file references (@path, @path#10-20) ---------------------------------------------------------------

export interface FileRef {
  path: string;
  start_line?: number;
  end_line?: number;
}

const MENTION = /(?<!\S)@([^\s@#]+?)(?:#(\d+)(?:-(\d+))?)?([.,;:!?)]*)(?=\s|$)/g;

/**
 * Turn `@path` / `@path#10-20` mentions of EXISTING files into references. The mention becomes the plain path in
 * the text; anything that is not an existing file (`@alice`, an email address) is left exactly as typed.
 */
export function extractReferences(prompt: string, exists: (rel: string) => boolean): { text: string; refs: FileRef[]; missing: string[] } {
  const refs: FileRef[] = [];
  const missing: string[] = [];
  const text = prompt.replace(MENTION, (whole, p: string, a?: string, b?: string, trail = "") => {
    const rel = p.replace(/\\/g, "/");
    if (rel.split("/").includes("..") || !exists(rel)) {
      missing.push(rel);
      return whole;
    }
    const ref: FileRef = { path: rel };
    if (a) {
      ref.start_line = Number(a);
      ref.end_line = Number(b ?? a);
    }
    if (!refs.some((r) => r.path === ref.path && r.start_line === ref.start_line && r.end_line === ref.end_line)) refs.push(ref);
    return `${rel}${a ? ` (lines ${a}-${b ?? a})` : ""}${trail}`;
  });
  return { text, refs, missing };
}

const SECRET_NAME = /^(\.env(\..+)?|.*\.(pem|key|pfx)|id_rsa.*|id_ed25519.*|\.npmrc|\.pypirc|credentials)$/i;
const PLACEHOLDER = /^\.env\.(example|sample|template)$/i;
const SECRET_DIRS = new Set([".ssh", ".aws", ".gnupg", ".kube"]);

/** Same idea as the backend's rule: such files are not suggested, and the backend refuses to read them anyway. */
export function looksSecret(rel: string): boolean {
  const parts = rel.split("/");
  const name = parts[parts.length - 1];
  return parts.some((p) => SECRET_DIRS.has(p)) || (!PLACEHOLDER.test(name) && SECRET_NAME.test(name));
}

/** Fuzzy file search for the @ autocomplete: basename matches first, then path substring, then subsequence. */
export function fuzzyFilter(files: string[], query: string, limit = 8): string[] {
  const q = query.toLowerCase().replace(/\\/g, "/");
  const scored: { f: string; s: number }[] = [];
  for (const f of files) {
    if (looksSecret(f)) continue;
    const lf = f.toLowerCase();
    const base = lf.slice(lf.lastIndexOf("/") + 1);
    let s = 0;
    if (!q) s = 1;
    else if (base.startsWith(q)) s = 100;
    else if (base.includes(q)) s = 80;
    else if (lf.includes(q)) s = 60;
    else {
      let i = 0;
      for (const ch of lf) if (ch === q[i]) i++;
      if (i === q.length) s = 30;
    }
    if (s > 0) scored.push({ f, s: s - Math.min(f.length, 60) / 100 }); // shorter paths win ties
  }
  return scored.sort((a, b) => b.s - a.s || a.f.localeCompare(b.f)).slice(0, limit).map((x) => x.f);
}

// ---- insights ------------------------------------------------------------------------------------------

export interface ActivitySummary {
  read: string[];
  changed: string[];
  commands: { command: string; exit: number | null }[];
  approved: number;
  rejected: number;
  refused: number;
  failures: number;
}

/** A "receipt" of what the agent did, built from the event stream (like a session activity log). */
export class ActivityLog {
  private calls = new Map<string, { tool: string; args: Json }>();
  private s: ActivitySummary = { read: [], changed: [], commands: [], approved: 0, rejected: 0, refused: 0, failures: 0 };

  add(e: HarnixEvent): void {
    const d = e.data ?? {};
    if (e.type === "tool.proposed") this.calls.set(d.call_id, { tool: d.tool, args: d.arguments ?? {} });
    else if (e.type === "approval.decided") d.approved ? this.s.approved++ : this.s.rejected++;
    else if (e.type === "tool.result") {
      const call = this.calls.get(d.call_id);
      if (!d.ok) {
        /^(Denied|Not approved)/.test(String(d.error ?? "")) ? this.s.refused++ : this.s.failures++;
        return;
      }
      const path = call?.args?.path as string | undefined;
      const push = (list: string[]) => path && !list.includes(path) && list.push(path);
      if (call?.tool === "read_file") push(this.s.read);
      else if (call && ["edit_file", "write_file", "delete_file"].includes(call.tool)) push(this.s.changed);
      else if (call && ["run_command", "run_tests"].includes(call.tool)) {
        this.s.commands.push({ command: String(d.data?.command ?? call.args?.command ?? call.tool), exit: d.data?.exit_code ?? null });
      }
    }
  }

  summary(): ActivitySummary {
    return JSON.parse(JSON.stringify(this.s));
  }
}

export interface Stats {
  iterations: number;
  maxIterations: number;
  toolCalls: number;
  toolFailures: number;
  refused: number;
  inputTokens: number;
  outputTokens: number;
  contextTokens: number | null; // size of the last prompt sent to the model
  costUsd: number | null; // null = prices not configured (shown as unknown, never as free)
  seconds: number | null;
}

export const formatTokens = (n: number): string => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));

export function formatStats(s: Stats): string {
  const cost = s.costUsd === null ? "cost unknown" : `~$${s.costUsd < 0.01 ? s.costUsd.toFixed(4) : s.costUsd.toFixed(3)}`;
  return [
    `step ${s.iterations}/${s.maxIterations}`,
    `${s.toolCalls} tool calls${s.toolFailures || s.refused ? ` (${s.toolFailures} failed, ${s.refused} refused)` : ""}`,
    `${formatTokens(s.inputTokens)} in / ${formatTokens(s.outputTokens)} out`,
    ...(s.contextTokens ? [`context ${formatTokens(s.contextTokens)}`] : []),
    cost,
    ...(s.seconds !== null ? [`${Math.round(s.seconds)}s`] : []),
  ].join(" · ");
}


// ---- unified diff parsing (for the result card's diff viewer) --------------------------------------

export interface DiffLine {
  kind: "add" | "del" | "ctx" | "note";
  oldNo?: number;
  newNo?: number;
  text: string;
}
export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}
export interface FileDiff {
  path: string;
  status: string; // A | M | D
  additions: number;
  deletions: number;
  binary: boolean;
  hunks: DiffHunk[];
  truncated: boolean;
  /** Binary files only: bytes before and after (null = the file did not exist on that side). */
  sizeBefore?: number | null;
  sizeAfter?: number | null;
}

const MAX_DIFF_LINES_PER_FILE = 600;

/** Parses `git diff` output into per-file hunks with line numbers. Unknown lines are ignored, never executed or rendered as markup. */
export function parseUnifiedDiff(text: string): FileDiff[] {
  const files: FileDiff[] = [];
  let file: FileDiff | undefined;
  let hunk: DiffHunk | undefined;
  let oldNo = 0;
  let newNo = 0;
  let shown = 0;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.startsWith("diff --git ")) {
      const m = /^diff --git a\/(.*) b\/(.*)$/.exec(line);
      file = { path: m ? m[2] : line.slice(11), status: "M", additions: 0, deletions: 0, binary: false, hunks: [], truncated: false };
      files.push(file);
      hunk = undefined;
      shown = 0;
      continue;
    }
    if (!file) continue;
    if (line.startsWith("new file mode")) file.status = "A";
    else if (line.startsWith("deleted file mode")) file.status = "D";
    else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) file.binary = true;
    else if (line.startsWith("@@")) {
      const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
      oldNo = m ? Number(m[1]) : 0;
      newNo = m ? Number(m[2]) : 0;
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
    } else if (hunk) {
      if (shown >= MAX_DIFF_LINES_PER_FILE) {
        file.truncated = true;
        if (line.startsWith("+") && !line.startsWith("+++")) file.additions++;
        else if (line.startsWith("-") && !line.startsWith("---")) file.deletions++;
        continue;
      }
      if (line.startsWith("+")) {
        hunk.lines.push({ kind: "add", newNo: newNo++, text: line.slice(1) });
        file.additions++;
        shown++;
      } else if (line.startsWith("-")) {
        hunk.lines.push({ kind: "del", oldNo: oldNo++, text: line.slice(1) });
        file.deletions++;
        shown++;
      } else if (line.startsWith("\\")) {
        hunk.lines.push({ kind: "note", text: line.slice(1).trim() });
      } else if (line.startsWith(" ")) {
        hunk.lines.push({ kind: "ctx", oldNo: oldNo++, newNo: newNo++, text: line.slice(1) });
        shown++;
      }
    }
  }
  return files;
}

// ---- history helpers -----------------------------------------------------------------------------

export interface HistoryItem {
  id: string;
  title: string;
  status: string;
  outcome: string | null;
  applyState: string | null; // applied | reverted | null
  createdAt: string;
  costUsd: number | null;
  model: string | null;
}

export function toHistoryItem(t: Json): HistoryItem {
  const firstLine = String(t.instructions ?? t.title ?? "").split("\n")[0].trim();
  return {
    id: String(t.id),
    title: (firstLine || String(t.title ?? t.id)).slice(0, 160),
    status: String(t.status),
    outcome: t.outcome ?? null,
    applyState: t.apply_state ?? null,
    createdAt: String(t.created_at ?? ""),
    costUsd: t.estimated_cost_usd ?? null,
    model: t.model ?? null,
  };
}

export interface SessionItem {
  id: string;
  title: string;
  turns: number;
  status: string | null; // of the last live turn
  outcome: string | null;
  lastPrompt: string;
  updatedAt: string;
}

export function toSessionItem(s: Json): SessionItem {
  return {
    id: String(s.id),
    title: String(s.title ?? s.last_prompt ?? s.id).slice(0, 160),
    turns: Number(s.turns ?? 0),
    status: s.last_status ?? null,
    outcome: s.last_outcome ?? null,
    lastPrompt: String(s.last_prompt ?? ""),
    updatedAt: String(s.updated_at ?? ""),
  };
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", else the date. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.round((now - t) / 1000));
  if (sec < 45) return "just now";
  if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  if (sec < 7 * 86400) return `${Math.round(sec / 86400)} d ago`;
  return new Date(t).toISOString().slice(0, 10);
}
