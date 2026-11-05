import { afterEach, describe, expect, it } from "vitest";
import { ForgeApi } from "../src/core";
import { ApprovalInfo, FileChange, Session, SessionUi, ToPanel } from "../src/session";
import { MockForge, MockOptions, ev } from "./mockForge";

const servers: MockForge[] = [];
afterEach(async () => {
  while (servers.length) await servers.pop()!.stop();
});

const APPROVAL = ev("approval.requested", { approval_id: "ap1", tool: "run_command", reason: "Arbitrary python execution requires approval", arguments: { command: "python -c print(1)" } });
const DECIDED = ev("approval.decided", { approval_id: "ap1", approved: true, reason: "approved by human" });
const baseEvents = (...mid: ReturnType<typeof ev>[]) => [
  ev("workspace.created", { execution_mode: "docker" }),
  ev("model.response", { text: "Looking at it.", tool_calls: [] }),
  ev("tool.proposed", { tool: "edit_file", arguments: { path: "x" } }),
  ...mid,
  ev("verification.result", { outcome: "verified", reasons: [] }),
  ev("task.finished", { status: "completed" }),
];

async function setup(opts: Partial<MockOptions> = {}, uiOver: Partial<SessionUi> = {}) {
  const m = await new MockForge({ events: baseEvents(), ...opts }).start();
  servers.push(m);
  const posted: ToPanel[] = [];
  const confirmed: { files: FileChange[]; verified: boolean }[] = [];
  const shown: string[] = [];
  const ui: SessionUi = {
    post: (msg) => void posted.push(msg),
    notifyApproval: async () => undefined, // notification dismissed unless a test overrides it
    confirmApply: async (files, verified) => (confirmed.push({ files, verified }), true),
    showDiff: async (d) => void shown.push(d),
    ...uiOver,
  };
  const session = new Session(new ForgeApi({ baseUrl: m.url }), ui, { statsIntervalMs: 20 });
  return { m, session, posted, confirmed, shown };
}

const types = (posted: ToPanel[]) => posted.map((p) => p.type);
const notices = (posted: ToPanel[]) => posted.filter((p): p is Extract<ToPanel, { type: "notice" }> => p.type === "notice");

describe("Session.run", () => {
  it("streams a timeline and returns the verified result with changed files", async () => {
    const { session, posted, m } = await setup();
    const r = await session.run({ repoPath: "/repo", prompt: "fix it" });
    expect(r).toMatchObject({ taskId: "t1", status: "completed", verified: true, summary: "All done." });
    expect(r?.files).toEqual([{ path: "x", status: "M", additions: 1, deletions: 1 }]);
    expect(posted[0]).toEqual({ type: "reset", prompt: "fix it" });
    const items = posted.filter((p) => p.type === "item").map((p) => (p as { item: { text: string } }).item.text);
    expect(items.some((t) => t.includes("gpt-6-luna"))).toBe(true);
    expect(items).toContain("Looking at it.");
    expect(items).toContain("edit_file x");
    expect(posted.at(-1)).toEqual({ type: "status", text: "Done: verified", busy: false });
    const created = m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!;
    expect(created.body).toMatchObject({ repo_path: "/repo", instructions: "fix it" });
    expect(session.running).toBe(false);
  });

  it("approval answered from the notification is sent exactly once", async () => {
    const { session, posted, m } = await setup({ events: baseEvents(APPROVAL, DECIDED) }, { notifyApproval: async () => true });
    const r = await session.run({ repoPath: "/repo", prompt: "x" });
    expect(r?.status).toBe("completed");
    expect(m.decisions).toEqual([{ id: "ap1", decision: "approve" }]);
    expect(types(posted)).toContain("approval");
    expect(posted.find((p) => p.type === "approvalResolved")).toMatchObject({ id: "ap1", approved: true });
  });

  it("the first answer wins: panel click, then a late notification answer does nothing", async () => {
    let notify!: (v: boolean) => void;
    const late = new Promise<boolean>((r) => (notify = r));
    const { session, m } = await setup({ events: baseEvents(APPROVAL, DECIDED) }, { notifyApproval: () => late });
    const running = session.run({ repoPath: "/repo", prompt: "x" });
    await new Promise((r) => setTimeout(r, 200));
    await session.resolveApproval("ap1", true); // panel button
    notify(false); // notification answered afterwards with the opposite choice
    await running;
    await new Promise((r) => setTimeout(r, 50));
    expect(m.decisions).toEqual([{ id: "ap1", decision: "approve" }]);
  });

  it("a dismissed notification leaves the decision to the panel, and Reject is delivered", async () => {
    const { session, m } = await setup({ events: baseEvents(APPROVAL) });
    const running = session.run({ repoPath: "/repo", prompt: "x" });
    await new Promise((r) => setTimeout(r, 200));
    expect(m.decisions).toEqual([]); // nothing decided on the user's behalf
    await session.resolveApproval("ap1", false);
    await running;
    expect(m.decisions).toEqual([{ id: "ap1", decision: "reject" }]);
  });

  it("cancel reaches the server and the run ends as cancelled", async () => {
    const { session, posted, m } = await setup({
      events: [ev("workspace.created", {}), APPROVAL, ev("task.finished", { status: "cancelled" })],
      finalTask: { id: "t1", status: "cancelled", outcome: null, error: "Cancelled by user", iterations: 1, tool_calls: 0, input_tokens: 1, output_tokens: 1, report: { agent: { final_message: "" }, known_limitations: [] } },
    });
    const running = session.run({ repoPath: "/repo", prompt: "x" });
    await new Promise((r) => setTimeout(r, 200));
    await session.cancel();
    const r = await running;
    expect(m.calls.some((c) => c.path === "/tasks/t1/cancel")).toBe(true);
    expect(r).toMatchObject({ status: "cancelled", verified: false, files: [] });
    expect(posted.at(-1)).toEqual({ type: "status", text: "Cancelled", busy: false });
  });

  it("refuses a second run while one is active (only one task is created)", async () => {
    const { session, posted, m } = await setup({ events: baseEvents(APPROVAL) });
    const first = session.run({ repoPath: "/repo", prompt: "one" });
    const second = await session.run({ repoPath: "/repo", prompt: "two" });
    expect(second).toBeUndefined();
    expect(notices(posted)[0].text).toContain("already running");
    await new Promise((r) => setTimeout(r, 200));
    await session.resolveApproval("ap1", true);
    await first;
    expect(m.calls.filter((c) => c.method === "POST" && c.path === "/tasks")).toHaveLength(1);
  });

  it("unverified results are reported as such", async () => {
    const { session, posted } = await setup({
      finalTask: { id: "t1", status: "completed", outcome: "unverified", error: null, iterations: 2, tool_calls: 2, input_tokens: 1, output_tokens: 1, report: { agent: { final_message: "ok" }, known_limitations: ["No test command detected"] } },
    });
    const r = await session.run({ repoPath: "/repo", prompt: "x" });
    expect(r).toMatchObject({ verified: false, limitations: ["No test command detected"] });
    expect(posted.at(-1)).toEqual({ type: "status", text: "Done: NOT verified", busy: false });
  });

  it("explains setup problems without creating a task", async () => {
    const { session, posted, m } = await setup({ health: { provider: { configured: false, problem: "OPENAI_API_KEY is not set." }, sandbox: { available: true } } });
    expect(await session.run({ repoPath: "/repo", prompt: "x" })).toBeUndefined();
    expect(notices(posted)[0]).toMatchObject({ kind: "error" });
    expect(notices(posted)[0].text).toContain("OPENAI_API_KEY is not set.");
    expect(m.calls.some((c) => c.path === "/tasks")).toBe(false);
    expect(session.running).toBe(false);
  });

  it("an unauthorized folder gets the exact command to fix it", async () => {
    const { session, posted } = await setup({ createError: { status: 422, code: "invalid_request", message: "Repository is outside the authorized roots (FORGE_ALLOWED_REPO_ROOTS)" } });
    await session.run({ repoPath: "/my project", prompt: "x" });
    expect(notices(posted)[0].text).toContain('forge serve --allow "/my project"');
  });

  it("reports an unreachable backend and can run again afterwards", async () => {
    const session = new Session(new ForgeApi({ baseUrl: "http://127.0.0.1:9" }), { post: () => {}, notifyApproval: async () => undefined, confirmApply: async () => true, showDiff: async () => {} });
    const posted: ToPanel[] = [];
    (session as unknown as { ui: SessionUi }).ui.post = (m) => void posted.push(m);
    expect(await session.run({ repoPath: "/repo", prompt: "x" })).toBeUndefined();
    expect(notices(posted)[0].text).toContain("Cannot reach the Forge backend");
    expect(session.running).toBe(false);
  });
});

describe("Session references and insights", () => {
  const call = (id: string, tool: string, args: object) => ev("tool.proposed", { call_id: id, tool, arguments: args });
  const done = (id: string, tool: string, extra: object = { ok: true }) => ev("tool.result", { call_id: id, tool, ...extra });

  it("sends references to the server in the API's field names", async () => {
    const { session, m } = await setup();
    await session.run({ repoPath: "/repo", prompt: "explain src/a.py (lines 3-9)", references: [{ path: "src/a.py", start_line: 3, end_line: 9 }, { path: "README.md" }] });
    const created = m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!;
    expect(created.body.references).toEqual([{ path: "src/a.py", start_line: 3, end_line: 9 }, { path: "README.md" }]);
  });

  it("builds the activity receipt from the stream and returns it with the workspace path", async () => {
    const events = [
      ev("workspace.created", {}),
      call("c1", "read_file", { path: "a.py" }), done("c1", "read_file"),
      call("c2", "edit_file", { path: "a.py", old_text: "1", new_text: "2" }), done("c2", "edit_file"),
      call("c3", "run_tests", {}), done("c3", "run_tests", { ok: true, data: { command: "pytest -q", exit_code: 0 } }),
      ev("verification.result", { outcome: "verified", reasons: [] }),
      ev("task.finished", { status: "completed" }),
    ];
    const { session } = await setup({ events });
    const r = await session.run({ repoPath: "/repo", prompt: "x" });
    expect(r?.activity).toMatchObject({ read: ["a.py"], changed: ["a.py"], commands: [{ command: "pytest -q", exit: 0 }], refused: 0, failures: 0 });
    expect(r?.workspacePath).toBe("/ws/t1");
  });

  it("posts live stats while running and a final one before the result; context comes from the last model response", async () => {
    const events = [
      ev("workspace.created", {}),
      ev("model.response", { text: "", usage: { input: 2100, output: 5 }, tool_calls: [] }),
      APPROVAL, // holds the stream open so the poller gets to run
      DECIDED,
      ev("task.finished", { status: "completed" }),
    ];
    const { session, posted } = await setup({ events });
    const running = session.run({ repoPath: "/repo", prompt: "x" });
    await new Promise((r) => setTimeout(r, 250));
    await session.resolveApproval("ap1", true);
    await running;
    const stats = posted.filter((p): p is Extract<ToPanel, { type: "stats" }> => p.type === "stats");
    expect(stats.length).toBeGreaterThanOrEqual(3); // several polls while blocked + the final one
    expect(stats.at(-1)!.stats).toMatchObject({ iterations: 3, maxIterations: 30, toolCalls: 4, inputTokens: 100, outputTokens: 20, contextTokens: 2100, costUsd: 0.001 });
    expect(stats.at(-1)!.text).toBe("step 3/30 · 4 tool calls · 100 in / 20 out · context 2.1k · ~$0.0010 · 5s");
    const lastStats = posted.map((p) => p.type).lastIndexOf("stats");
    expect(lastStats).toBeLessThan(posted.map((p) => p.type).lastIndexOf("result")); // final numbers precede the result card
  });

  it("stops polling when the run ends", async () => {
    const { session, m } = await setup();
    await session.run({ repoPath: "/repo", prompt: "x" });
    const before = m.calls.filter((c) => c.path === "/tasks/t1").length;
    await new Promise((r) => setTimeout(r, 150));
    expect(m.calls.filter((c) => c.path === "/tasks/t1").length).toBe(before);
  });

  it("shows the attached files as a timeline item", async () => {
    const events = [ev("task.references", { included: [{ path: "a.py", start: 3, end: 9, truncated: false }], skipped: [] }), ev("task.finished", { status: "completed" })];
    const { session, posted } = await setup({ events });
    await session.run({ repoPath: "/repo", prompt: "x" });
    const texts = posted.filter((p) => p.type === "item").map((p) => (p as { item: { text: string } }).item.text);
    expect(texts).toContain("Attached 1 file(s): a.py:3-9");
  });
});

describe("Session.applyTask / showDiff", () => {
  it("previews, confirms with the verified flag, applies once, and refuses a repeat", async () => {
    const { session, posted, confirmed, m } = await setup();
    expect(await session.applyTask("t1")).toBe(true);
    expect(confirmed).toEqual([{ files: [{ path: "x", status: "M", additions: 0, deletions: 0 }], verified: true }]);
    expect(m.applies).toEqual([
      { confirm: false, dry_run: true },
      { confirm: true, dry_run: false },
    ]);
    expect(notices(posted).at(-1)?.text).toBe("Applied 1 file(s): M x");
    expect(posted).toContainEqual({ type: "applied", taskId: "t1" }); // lets the panel disable its Apply button
    expect(await session.applyTask("t1")).toBe(false);
    expect(m.applies).toHaveLength(2); // the second attempt never reached the server
  });

  it("declining the confirmation writes nothing", async () => {
    const { session, m } = await setup({}, { confirmApply: async () => false });
    expect(await session.applyTask("t1")).toBe(false);
    expect(m.applies).toEqual([{ confirm: false, dry_run: true }]);
  });

  it("surfaces a conflict from the server and does not mark the task applied", async () => {
    const { session, posted } = await setup({ applyError: { status: 409, code: "conflict", message: "Nothing was written. x: changed in your repository since the task started" } });
    expect(await session.applyTask("t1")).toBe(false);
    expect(notices(posted).at(-1)).toMatchObject({ kind: "error" });
    expect(notices(posted).at(-1)?.text).toContain("changed in your repository");
    expect(await session.applyTask("t1")).toBe(false); // may be retried after the user resolves the conflict
    expect(notices(posted).filter((n) => n.kind === "error")).toHaveLength(2);
  });

  it("warns about unverified results in the confirmation", async () => {
    const { session, confirmed } = await setup({ finalTask: { id: "t1", status: "completed", outcome: "unverified", report: {} } });
    await session.applyTask("t1");
    expect(confirmed[0].verified).toBe(false);
  });

  it("shows the diff text", async () => {
    const { session, shown } = await setup();
    await session.showDiff("t1");
    expect(shown[0]).toContain("+new");
  });
});

describe("Session thinking level", () => {
  it("sends the chosen level and leaves it out when the backend default is wanted", async () => {
    const { session, m } = await setup();
    await session.run({ repoPath: "/repo", prompt: "x", thinking: "xhigh" });
    await session.run({ repoPath: "/repo", prompt: "y" });
    const bodies = m.calls.filter((c) => c.path === "/tasks" && c.method === "POST").map((c) => c.body);
    expect(bodies[0].thinking).toBe("xhigh");
    expect(bodies[1]).not.toHaveProperty("thinking");
  });
});
