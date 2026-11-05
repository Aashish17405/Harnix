/** A tiny in-process stand-in for the Forge HTTP API (same routes and SSE shape), for testing the extension logic. */
import * as http from "http";
import { AddressInfo } from "net";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type J = any;
export interface MockEvent {
  type: string;
  data: J;
}

export interface MockOptions {
  events: MockEvent[];
  health?: J;
  createError?: { status: number; code: string; message: string };
  dropAfter?: number; // destroy the first stream connection after this many events
  finalTask?: J;
  diff?: J;
  applyError?: { status: number; code: string; message: string };
  revertError?: { status: number; code: string; message: string };
  history?: J[]; // GET /tasks
  sessions?: J[]; // GET /sessions
  sessionDetail?: J; // GET /sessions/s1 (default: one turn built from finalTask)
  turn?: number; // the turn number a created task gets
  newSession?: boolean; // POST /tasks answers with a fresh session (default: s1)
  checks?: J[]; // GET /tasks/t1/verifications
  token?: string;
}

export class MockForge {
  calls: { method: string; path: string; body: J; auth?: string }[] = [];
  decisions: { id: string; decision: string }[] = [];
  applies: J[] = [];
  reverts: J[] = [];
  restores: J[] = [];
  renames: J[] = [];
  streamAfters: number[] = [];
  private server!: http.Server;
  private gate!: () => void;
  private gated = new Promise<void>((r) => (this.gate = r));
  private applied = false;
  url = "";

  constructor(public opts: MockOptions) {}

  async start(): Promise<this> {
    this.server = http.createServer((req, res) => this.handle(req, res));
    await new Promise<void>((r) => this.server.listen(0, "127.0.0.1", r));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
    return this;
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections();
    await new Promise<void>((r) => this.server.close(() => r()));
  }

  private send(res: http.ServerResponse, status: number, body: J) {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  }

  private err(res: http.ServerResponse, e: { status: number; code: string; message: string }) {
    this.send(res, e.status, { error: { code: e.code, message: e.message } });
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse) {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const raw = Buffer.concat(chunks).toString();
    const body = raw ? JSON.parse(raw) : undefined;
    const url = new URL(req.url!, "http://x");
    const path = url.pathname.replace(/^\/api/, "");
    const auth = req.headers.authorization;
    this.calls.push({ method: req.method!, path, body, auth });
    if (this.opts.token && path !== "/health" && auth !== `Bearer ${this.opts.token}`) {
      return this.err(res, { status: 401, code: "unauthorized", message: "Missing or invalid API token" });
    }
    const m = (re: RegExp) => path.match(re);

    if (path === "/health") {
      return this.send(res, 200, this.opts.health ?? { status: "ok", version: "t", execution_mode: "docker", sandbox: { available: true, detail: "" }, provider: { name: "openai", model: "gpt-6-luna", configured: true, problem: null }, spend: null });
    }
    if (path === "/config") return this.send(res, 200, { allowed_repo_roots: ["/repo"], approval_policy: "sensitive" });
    if (req.method === "POST" && path === "/tasks") {
      return this.opts.createError ? this.err(res, this.opts.createError) : this.send(res, 201, { id: "t1", status: "queued", session_id: "s1", turn: this.opts.turn ?? 1, title: "t" });
    }
    if (req.method === "POST" && m(/^\/tasks\/t1\/run$/)) return this.send(res, 202, { id: "t1", status: "running" });
    if (req.method === "POST" && m(/^\/tasks\/t1\/cancel$/)) {
      this.gate();
      return this.send(res, 202, { id: "t1", status: "running" });
    }
    const dec = m(/^\/approvals\/([^/]+)\/decision$/);
    if (req.method === "POST" && dec) {
      if (this.decisions.some((d) => d.id === dec[1])) return this.err(res, { status: 409, code: "conflict", message: "Approval is already decided" });
      this.decisions.push({ id: dec[1], decision: body.decision });
      this.gate();
      return this.send(res, 200, { id: dec[1], status: body.decision === "approve" ? "approved" : "rejected" });
    }
    if (req.method === "GET" && m(/^\/tasks\/t1$/)) {
      return this.send(res, 200, this.opts.finalTask ?? { id: "t1", status: "completed", outcome: "verified", error: null, iterations: 3, max_iterations: 30, tool_calls: 4, tool_failures: 0, policy_denials: 0, input_tokens: 100, output_tokens: 20, estimated_cost_usd: 0.001, duration_seconds: 5, workspace_path: "/ws/t1", report: { agent: { final_message: "All done." }, known_limitations: [], false_success_claim: false } });
    }
    if (req.method === "GET" && m(/^\/tasks\/t1\/diff$/)) {
      return this.send(res, 200, this.opts.diff ?? { diff: "diff --git a/x b/x\n-old\n+new\n", files: [{ path: "x", status: "M", additions: 1, deletions: 1 }], live: false });
    }
    if (req.method === "POST" && m(/^\/tasks\/t1\/apply$/)) {
      this.applies.push(body);
      if (this.opts.applyError && body.confirm) return this.err(res, this.opts.applyError);
      if (body.confirm && this.applied) return this.err(res, { status: 409, code: "conflict", message: "This task's changes were already applied" });
      if (body.confirm) this.applied = true;
      return this.send(res, 200, { applied: !!body.confirm, dry_run: !!body.dry_run, files: [{ path: "x", status: "M" }], verified: (this.opts.finalTask?.outcome ?? "verified") === "verified" });
    }
    if (req.method === "POST" && m(/^\/tasks\/t1\/revert$/)) {
      this.reverts.push(body);
      if (this.opts.revertError && body.confirm) return this.err(res, this.opts.revertError);
      return this.send(res, 200, { applied: false, dry_run: !!body.dry_run, files: [{ path: "x", status: "M" }], verified: true });
    }
    if (req.method === "GET" && path === "/sessions") return this.send(res, 200, { items: this.opts.sessions ?? [], total: (this.opts.sessions ?? []).length, limit: 50, offset: 0 });
    if (req.method === "GET" && m(/^\/sessions\/s1$/)) {
      const t = { id: "t1", status: "completed", outcome: "verified", title: "x", instructions: "ask", turn: 1, discarded: false, has_checkpoint: true, apply_state: null, images: [], iterations: 1, max_iterations: 30, tool_calls: 0, tool_failures: 0, policy_denials: 0, input_tokens: 1, output_tokens: 1, report: {}, ...(this.opts.finalTask ?? {}) };
      return this.send(res, 200, this.opts.sessionDetail ?? { id: "s1", title: "A conversation", turns: 1, items: [t] });
    }
    if (req.method === "PATCH" && m(/^\/sessions\/s1$/)) {
      this.renames.push(body);
      return this.send(res, 200, { id: "s1", title: body.title });
    }
    if (req.method === "POST" && m(/^\/sessions\/s1\/restore$/)) {
      this.restores.push(body);
      return this.send(res, 200, { turn: body.turn, restored: !!body.confirm, discarded_tasks: ["t9"], files: [{ path: "x", status: "M" }] });
    }
    if (req.method === "GET" && path === "/tasks") return this.send(res, 200, { items: this.opts.history ?? [], total: (this.opts.history ?? []).length, limit: 50, offset: 0 });
    if (req.method === "GET" && m(/^\/tasks\/t1\/verifications$/)) return this.send(res, 200, this.opts.checks ?? []);
    if (req.method === "GET" && m(/^\/tasks\/t1\/file$/)) {
      const version = url.searchParams.get("version");
      return this.send(res, 200, { path: url.searchParams.get("path"), version, exists: true, binary: true, truncated: false, size: version === "baseline" ? 90 : 120, content: "" });
    }
    if (req.method === "GET" && m(/^\/tasks\/t1\/images\/1$/)) {
      res.writeHead(200, { "Content-Type": "image/png" });
      return void res.end(Buffer.from("iVBORw0KGgo=", "base64"));
    }
    if (req.method === "GET" && m(/^\/tasks\/t1\/events$/)) {
      const after = Number(url.searchParams.get("after") ?? 0);
      return this.send(res, 200, this.opts.events.map((e, i) => ({ id: i + 1, task_id: "t1", ts: "2026-01-01T00:00:00Z", type: e.type, data: e.data })).filter((e) => e.id > after));
    }
    if (req.method === "GET" && m(/^\/tasks\/t1\/events\/stream$/)) return this.stream(url, res);
    this.err(res, { status: 404, code: "not_found", message: `no route ${path}` });
  }

  private async stream(url: URL, res: http.ServerResponse) {
    const after = Number(url.searchParams.get("after") ?? 0);
    this.streamAfters.push(after);
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(": keep-alive\n\n");
    const first = this.streamAfters.length === 1;
    let sent = 0;
    for (let i = after; i < this.opts.events.length; i++) {
      const e = this.opts.events[i];
      res.write(`id: ${i + 1}\ndata: ${JSON.stringify({ id: i + 1, task_id: "t1", ts: "2026-01-01T00:00:00Z", type: e.type, data: e.data })}\n\n`);
      sent++;
      if (first && this.opts.dropAfter !== undefined && sent >= this.opts.dropAfter) return void res.destroy();
      if (e.type === "approval.requested") await this.gated; // the task is blocked until a decision (or cancel) arrives
      await new Promise((r) => setTimeout(r, 5));
    }
    res.write("event: end\ndata: {}\n\n");
    res.end();
  }
}

export const ev = (type: string, data: J = {}): MockEvent => ({ type, data });
