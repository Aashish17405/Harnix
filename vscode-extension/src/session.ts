/** One agent run from the editor's point of view. Talks to an abstract UI so it can be tested without VS Code. */
import { ActivityLog, ActivitySummary, FileDiff, FileRef, HarnixApi, HarnixApiError, HarnixEvent, Json, SessionItem, Stats, TimelineItem, describeEvent, formatStats, parseUnifiedDiff, toSessionItem } from "./core";

export interface ApprovalInfo {
  id: string;
  tool: string;
  reason: string;
  arguments: Json;
}

export interface FileChange {
  path: string;
  status: string;
  additions: number;
  deletions: number;
}

export interface CheckInfo {
  phase: string;
  kind: string;
  command: string;
  status: string; // passed | failed | skipped | unavailable
  exitCode: number | null;
  note: string | null;
}

export interface RunResult {
  taskId: string;
  /** The conversation this response belongs to, and its position in it (1 = the first message). */
  sessionId: string | null;
  turn: number;
  /** True when the workspace can be put back to how it was right after this response. */
  canRestore: boolean;
  title: string;
  /** applied | reverted | null: whether the changes are currently in the user's workspace */
  applyState: string | null;
  /** Final checks (and what was already failing before the task), as the harness ran them. */
  checks: CheckInfo[];
  /** The change, parsed per file for the in-panel diff viewer. */
  diffs: FileDiff[];
  status: string;
  outcome: string | null;
  verified: boolean;
  error: string | null;
  files: FileChange[];
  summary: string;
  stats: string;
  limitations: string[];
  falseClaim: boolean;
  activity: ActivitySummary;
  /** The isolated copy the agent worked in (needed to diff individual files natively). */
  workspacePath: string | null;
}

/** Messages sent to the chat panel. */
export type ToPanel =
  | { type: "reset"; prompt: string; images?: { name: string; src: string }[] } // a new conversation: clear, then show the first message
  | { type: "turn"; prompt: string; turn?: number; discarded?: boolean; images?: { name: string; src: string }[] } // the next message of this conversation
  | { type: "clear" }
  | { type: "sessionInfo"; id: string | null; title: string; turns: number } // the conversation on screen (null = none yet)
  | { type: "status"; text: string; busy: boolean }
  | { type: "item"; item: TimelineItem }
  | { type: "approval"; approval: ApprovalInfo }
  | { type: "approvalResolved"; id: string; approved: boolean }
  | { type: "result"; result: RunResult }
  | { type: "applied"; taskId: string }
  | { type: "reverted"; taskId: string }
  | { type: "history"; items: SessionItem[]; error?: string } // this workspace's conversations, most recent first
  | { type: "stats"; text: string; stats: Stats } // live counters while the task runs
  | { type: "files"; query: string; results: string[] } // @ autocomplete answer
  | { type: "selection"; ref: string | null } // "@path#a-b" for the current editor selection, null = none
  | { type: "insert"; text: string } // put text (an @mention) into the prompt box
  | { type: "restore"; prompt: string } // the run did not start: put the user's text back in the box
  | { type: "notice"; kind: "info" | "warn" | "error"; text: string };

export interface SessionUi {
  post(msg: ToPanel): void;
  /** Non-blocking attention grab (notification). Resolves true/false when answered, undefined if dismissed. */
  notifyApproval(a: ApprovalInfo): Promise<boolean | undefined>;
  /** Final confirmation before the repository is modified. */
  confirmApply(files: FileChange[], verified: boolean): Promise<boolean>;
  showDiff(diff: string): Promise<void>;
  /** Final confirmation before an earlier apply is undone. */
  confirmRevert(files: FileChange[]): Promise<boolean>;
  /** Final confirmation before the conversation and workspace are put back to an earlier response. */
  confirmRestore(turn: number, discarded: number, files: FileChange[]): Promise<boolean>;
}

export interface RunOptions {
  repoPath: string;
  prompt: string;
  /** What the panel shows as the user's message when it differs from what is sent (mentions are rewritten as paths). */
  displayPrompt?: string;
  criteria?: string[];
  verify?: string[];
  references?: FileRef[];
  /** low | medium | high | xhigh (extra high); undefined = the backend's default */
  thinking?: string;
  /** Pictures for the model to look at: base64 file bytes (no data: prefix). */
  images?: AttachedImage[];
  /** Continue this conversation (the agent remembers it); omitted = start a new one. */
  sessionId?: string;
  /** Title of that conversation, for the header. */
  sessionTitle?: string;
}

export interface AttachedImage {
  name: string;
  mediaType: string;
  data: string;
}

export interface SessionOptions {
  /** How often live counters are refreshed while a task runs. */
  statsIntervalMs?: number;
}

export class Session {
  private current?: { taskId: string; abort: AbortController; activity: ActivityLog; contextTokens: number | null };
  private statsIntervalMs: number;
  private decided = new Set<string>();
  private applied = new Set<string>();

  constructor(
    private api: HarnixApi,
    private ui: SessionUi,
    options: SessionOptions = {},
  ) {
    this.statsIntervalMs = options.statsIntervalMs ?? 2000;
  }

  get running(): boolean {
    return this.current !== undefined;
  }

  /** Runs one task to its end (completed / failed / cancelled). Expected problems are reported to the UI, not thrown. */
  async run(opts: RunOptions): Promise<RunResult | undefined> {
    if (this.current) {
      this.ui.post({ type: "notice", kind: "warn", text: "A task is already running. Cancel it first." });
      return undefined;
    }
    const abort = new AbortController();
    const current = { taskId: "", abort, activity: new ActivityLog(), contextTokens: null as number | null };
    this.current = current; // reserve the slot immediately (no double start while awaiting the API)
    const shown = { prompt: opts.displayPrompt ?? opts.prompt, ...(opts.images?.length ? { images: opts.images.map((i) => ({ name: i.name, src: `data:${i.mediaType};base64,${i.data}` })) } : {}) };
    // a follow-up is added under what is already on screen; only a new conversation starts from an empty panel
    this.ui.post(opts.sessionId ? { type: "turn", ...shown } : { type: "reset", ...shown });
    this.ui.post({ type: "status", text: "Starting…", busy: true });
    let ticker: ReturnType<typeof setInterval> | undefined;
    try {
      const health = await this.api.health();
      if (!health.provider.configured) return this.fail(`Model provider is not configured: ${health.provider.problem}`);
      if (!health.sandbox.available) return this.fail(`Execution sandbox unavailable: ${health.sandbox.detail}`);
      const task = await this.api.createTask({
        repo_path: opts.repoPath,
        instructions: opts.prompt,
        acceptance_criteria: opts.criteria ?? [],
        verification_commands: opts.verify ?? [],
        references: opts.references ?? [],
        ...(opts.thinking ? { thinking: opts.thinking } : {}),
        ...(opts.images?.length ? { images: opts.images.map((i) => ({ name: i.name, media_type: i.mediaType, data: i.data })) } : {}),
        ...(opts.sessionId ? { session_id: opts.sessionId } : {}),
      });
      const taskId = task.id as string;
      current.taskId = taskId;
      this.ui.post({ type: "sessionInfo", id: task.session_id ?? null, title: opts.sessionTitle ?? String(task.title ?? opts.prompt).slice(0, 160), turns: task.turn ?? 1 });
      await this.api.runTask(taskId);
      this.ui.post({ type: "item", item: { kind: "info", text: `Task ${taskId} · ${health.provider.name} ${health.provider.model} · ${health.execution_mode} sandbox` } });
      this.ui.post({ type: "status", text: "Working…", busy: true });
      ticker = setInterval(() => void this.postStats(taskId, current), this.statsIntervalMs);
      for await (const ev of this.api.streamEvents(taskId, abort.signal)) this.onEvent(ev);
      clearInterval(ticker);
      ticker = undefined;
      if (abort.signal.aborted) return undefined;
      const result = await this.summarize(taskId, current.activity.summary());
      await this.postStats(taskId, current); // final numbers, so the bar matches the result card
      this.ui.post({ type: "result", result });
      this.ui.post({ type: "status", text: this.headline(result), busy: false });
      return result;
    } catch (e) {
      if (e instanceof HarnixApiError && e.status === 409 && /previous message/i.test(e.message)) {
        return this.fail("The previous message is still being worked on. Wait for it to finish, or cancel it.");
      }
      if (e instanceof HarnixApiError && e.status === 422 && /authorized/i.test(e.message)) {
        return this.fail(`${e.message}\nRestart the backend with this folder allowed:  forge serve --allow "${opts.repoPath}"`);
      }
      return this.fail(e instanceof Error ? e.message : String(e));
    } finally {
      if (ticker) clearInterval(ticker);
      this.current = undefined;
    }
  }

  /** Refreshes the live counters; a failed poll is skipped (the event stream is the source of truth, this is garnish). */
  private async postStats(taskId: string, current: NonNullable<Session["current"]>): Promise<void> {
    try {
      const t = await this.api.getTask(taskId);
      if (this.current !== current) return; // the run ended while the request was in flight
      const stats: Stats = {
        iterations: t.iterations,
        maxIterations: t.max_iterations,
        toolCalls: t.tool_calls,
        toolFailures: t.tool_failures,
        refused: t.policy_denials,
        inputTokens: t.input_tokens,
        outputTokens: t.output_tokens,
        contextTokens: current.contextTokens,
        costUsd: t.estimated_cost_usd ?? null,
        seconds: t.duration_seconds ?? null,
      };
      this.ui.post({ type: "stats", text: formatStats(stats), stats });
    } catch {
      /* ignore */
    }
  }

  private fail(text: string): undefined {
    this.ui.post({ type: "notice", kind: "error", text });
    this.ui.post({ type: "status", text: "Failed to run", busy: false });
    return undefined;
  }

  private onEvent(ev: HarnixEvent): void {
    this.current?.activity.add(ev);
    if (ev.type === "model.response" && this.current && typeof ev.data?.usage?.input === "number") this.current.contextTokens = ev.data.usage.input;
    const item = describeEvent(ev);
    if (item) this.ui.post({ type: "item", item });
    if (ev.type === "task.state") {
      const text = ({ awaiting_approval: "Waiting for your approval", verifying: "Verifying…", running: "Working…" } as Record<string, string>)[ev.data.to];
      if (text) this.ui.post({ type: "status", text, busy: true });
    }
    if (ev.type === "approval.requested") {
      const approval: ApprovalInfo = { id: ev.data.approval_id, tool: ev.data.tool, reason: ev.data.reason, arguments: ev.data.arguments };
      this.ui.post({ type: "approval", approval });
      void this.ui.notifyApproval(approval).then((answer) => {
        if (answer !== undefined) void this.resolveApproval(approval.id, answer);
      });
    }
    if (ev.type === "approval.decided") this.ui.post({ type: "approvalResolved", id: ev.data.approval_id, approved: !!ev.data.approved });
  }

  /** Called by the panel buttons or the notification; the first answer wins, later ones are ignored. */
  async resolveApproval(id: string, approve: boolean): Promise<void> {
    if (this.decided.has(id)) return;
    this.decided.add(id);
    try {
      await this.api.decide(id, approve);
    } catch (e) {
      if (!(e instanceof HarnixApiError && e.status === 409)) {
        this.decided.delete(id);
        this.ui.post({ type: "notice", kind: "error", text: `Could not send your decision: ${(e as Error).message}` });
      }
    }
  }

  async cancel(): Promise<void> {
    if (!this.current?.taskId) return;
    try {
      await this.api.cancelTask(this.current.taskId); // the stream keeps running until the server reports the end
      this.ui.post({ type: "status", text: "Cancelling…", busy: true });
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: `Could not cancel: ${(e as Error).message}` });
    }
  }

  /** Stop listening (extension deactivated). The task keeps running on the server unless it was cancelled. */
  dispose(): void {
    this.current?.abort.abort();
  }

  headline(r: RunResult): string {
    if (r.status === "completed") return r.verified ? "Done: verified" : "Done: NOT verified";
    return r.status === "cancelled" ? "Cancelled" : "Failed";
  }

  private async summarize(taskId: string, activity: ActivitySummary): Promise<RunResult> {
    const task = await this.api.getTask(taskId);
    const report = task.report ?? {};
    let files: FileChange[] = [];
    let diffs: FileDiff[] = [];
    if (task.status === "completed") {
      const d = await this.api.diff(taskId);
      files = d.files;
      diffs = parseUnifiedDiff(String(d.diff ?? ""));
      // a binary file has no text diff: say how big it was and is instead
      await Promise.all(
        diffs
          .filter((f) => f.binary)
          .map(async (f) => {
            try {
              const [b, a] = await Promise.all([this.api.fileVersion(taskId, f.path, "baseline"), this.api.fileVersion(taskId, f.path, "final")]);
              f.sizeBefore = b.exists ? b.size : null;
              f.sizeAfter = a.exists ? a.size : null;
            } catch {
              /* sizes are garnish */
            }
          }),
      );
    }
    let checks: CheckInfo[] = [];
    try {
      checks = ((await this.api.verifications(taskId)) as Json[]).map((v) => ({
        phase: v.phase, kind: v.kind, command: v.command ?? v.name ?? "", status: v.status, exitCode: v.exit_code ?? null, note: v.note ?? null,
      }));
    } catch {
      /* the checks list is garnish; the verdict itself comes from the task */
    }
    const cost = task.estimated_cost_usd;
    return {
      taskId,
      sessionId: task.session_id ?? null,
      turn: Number(task.turn ?? 1),
      canRestore: !!task.has_checkpoint && !task.discarded,
      title: String(task.title ?? ""),
      applyState: task.apply_state ?? null,
      checks,
      diffs,
      status: task.status,
      outcome: task.outcome,
      verified: task.outcome === "verified",
      error: task.error,
      files,
      summary: (report.agent?.final_message ?? "").trim(),
      stats: `${task.iterations} model calls · ${task.tool_calls} tool calls · ${task.input_tokens}/${task.output_tokens} tokens${cost != null ? ` · ~$${Number(cost).toFixed(4)}` : ""}`,
      limitations: report.known_limitations ?? [],
      falseClaim: !!report.false_success_claim,
      activity,
      workspacePath: task.workspace_path ?? null,
    };
  }

  async showDiff(taskId: string): Promise<void> {
    try {
      const d = await this.api.diff(taskId);
      await this.ui.showDiff(d.diff || "(no changes)");
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: `Could not load the diff: ${(e as Error).message}` });
    }
  }

  /** Posts this workspace's conversations (from the backend's database, so they survive restarts). */
  async loadHistory(repoPath: string): Promise<SessionItem[]> {
    try {
      const items = (await this.api.listSessions(repoPath)).items.map(toSessionItem);
      this.ui.post({ type: "history", items });
      return items;
    } catch (e) {
      this.ui.post({ type: "history", items: [], error: e instanceof HarnixApiError && e.unreachable ? "The Harnix backend is not running, so earlier sessions cannot be listed." : (e as Error).message });
      return [];
    }
  }

  private async pictures(task: Json): Promise<{ name: string; src: string }[]> {
    const pics = await Promise.all(
      ((task.images ?? []) as Json[]).map(async (i) => {
        try {
          const d = await this.api.imageData(task.id, i.index);
          return { name: String(i.name), src: `data:${d.mediaType};base64,${d.data}` };
        } catch {
          return null; // a missing thumbnail is not worth failing the replay
        }
      }),
    );
    return pics.filter((p): p is { name: string; src: string } => !!p);
  }

  /** Opens a conversation on screen: every message with its timeline, checks and diff. The next prompt continues it. */
  async openSession(sessionId: string, quiet = false): Promise<RunResult | undefined> {
    if (this.current) {
      this.ui.post({ type: "notice", kind: "warn", text: "A message is being worked on. Cancel it before opening another conversation." });
      return undefined;
    }
    try {
      const s = await this.api.getSession(sessionId);
      this.ui.post({ type: "clear" });
      this.ui.post({ type: "sessionInfo", id: s.id, title: s.title, turns: s.turns });
      let last: RunResult | undefined;
      for (const task of s.items as Json[]) {
        const pics = await this.pictures(task);
        const msg = { prompt: String(task.instructions ?? task.title ?? ""), turn: Number(task.turn ?? 1), ...(pics.length ? { images: pics } : {}) };
        if (task.discarded) {
          this.ui.post({ type: "turn", ...msg, discarded: true });
          continue; // set aside by a restore: shown dimmed, not replayed
        }
        this.ui.post({ type: "turn", ...msg });
        const activity = new ActivityLog();
        for (const ev of await this.api.allEvents(task.id)) {
          activity.add(ev);
          const item = describeEvent(ev);
          if (item) this.ui.post({ type: "item", item });
        }
        if (["queued", "running", "awaiting_approval", "verifying"].includes(task.status)) {
          this.ui.post({ type: "notice", kind: "warn", text: "This message is still being worked on by the backend; this is what it had done so far." });
          continue;
        }
        last = await this.summarize(task.id, activity.summary());
        this.ui.post({ type: "result", result: last });
      }
      this.ui.post({ type: "status", text: last ? this.headline(last) : "Ready", busy: false });
      return last;
    } catch (e) {
      if (!quiet) this.ui.post({ type: "notice", kind: "error", text: `Could not open that conversation: ${(e as Error).message}` });
      return undefined;
    }
  }

  /** Puts the conversation and the workspace back to how they were after response `turn` (0 = the start), after a preview. */
  async restoreSession(sessionId: string, turn: number): Promise<boolean> {
    if (this.current) {
      this.ui.post({ type: "notice", kind: "warn", text: "A message is being worked on. Cancel it before going back." });
      return false;
    }
    try {
      const preview = await this.api.restoreSession(sessionId, turn, { dry_run: true });
      const files: FileChange[] = preview.files.map((f: Json) => ({ ...f, additions: 0, deletions: 0 }));
      if (!(await this.ui.confirmRestore(turn, preview.discarded_tasks.length, files))) return false;
      await this.api.restoreSession(sessionId, turn, { confirm: true });
      await this.openSession(sessionId); // show the conversation as it is now
      this.ui.post({ type: "notice", kind: "info", text: turn === 0 ? "Back to the start of this conversation." : `Back to response ${turn}. Later messages were set aside; the agent no longer remembers them.` });
      return true;
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: (e as Error).message });
      return false;
    }
  }

  async renameSession(sessionId: string, title: string): Promise<boolean> {
    try {
      await this.api.renameSession(sessionId, title);
      return true;
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: `Could not rename: ${(e as Error).message}` });
      return false;
    }
  }

  /** Undoes an earlier apply, after a preview and an explicit confirmation. Refuses if you edited the files since. */
  async revertTask(taskId: string): Promise<boolean> {
    try {
      const preview = await this.api.revert(taskId, { dry_run: true });
      const files: FileChange[] = preview.files.map((f: Json) => ({ ...f, additions: 0, deletions: 0 }));
      if (!files.length) {
        this.ui.post({ type: "notice", kind: "info", text: "Nothing to revert: the files are already back to how they were." });
        return false;
      }
      if (!(await this.ui.confirmRevert(files))) return false;
      const done = await this.api.revert(taskId, { confirm: true });
      this.applied.delete(taskId);
      this.ui.post({ type: "reverted", taskId });
      this.ui.post({ type: "notice", kind: "info", text: `Reverted ${done.files.length} file(s): ${done.files.map((f: Json) => `${f.status} ${f.path}`).join(", ")}` });
      return true;
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: (e as Error).message });
      return false;
    }
  }

  /** Copies the task's changes into the real workspace, after an explicit confirmation. */
  async applyTask(taskId: string): Promise<boolean> {
    if (this.applied.has(taskId)) {
      this.ui.post({ type: "notice", kind: "info", text: "These changes were already applied." });
      return false;
    }
    try {
      const preview = await this.api.apply(taskId, { dry_run: true });
      const files: FileChange[] = preview.files.map((f: Json) => ({ ...f, additions: 0, deletions: 0 }));
      if (!files.length) {
        this.ui.post({ type: "notice", kind: "info", text: "Nothing to apply." });
        return false;
      }
      if (!(await this.ui.confirmApply(files, preview.verified))) return false;
      const done = await this.api.apply(taskId, { confirm: true });
      this.applied.add(taskId);
      this.ui.post({ type: "applied", taskId });
      this.ui.post({ type: "notice", kind: "info", text: `Applied ${done.files.length} file(s): ${done.files.map((f: Json) => `${f.status} ${f.path}`).join(", ")}` });
      return true;
    } catch (e) {
      this.ui.post({ type: "notice", kind: "error", text: (e as Error).message });
      return false;
    }
  }
}
