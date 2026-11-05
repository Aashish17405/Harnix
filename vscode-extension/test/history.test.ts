import { afterEach, describe, expect, it } from "vitest";
import { ForgeApi, parseUnifiedDiff, relativeTime, toHistoryItem } from "../src/core";
import { FileChange, Session, SessionUi, ToPanel } from "../src/session";
import { getHtml } from "../src/webview";
import { MockForge, MockOptions, ev } from "./mockForge";

const servers: MockForge[] = [];
afterEach(async () => {
  while (servers.length) await servers.pop()!.stop();
});

const DIFF = [
  "diff --git a/calc.py b/calc.py",
  "index 1111111..2222222 100644",
  "--- a/calc.py",
  "+++ b/calc.py",
  "@@ -1,4 +1,5 @@",
  " def add(a, b):",
  "-    return a - b",
  "+    return a + b",
  " ",
  " def sub(a, b):",
  "+    # new comment",
  "\\ No newline at end of file",
  "diff --git a/new.txt b/new.txt",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/new.txt",
  "@@ -0,0 +1,2 @@",
  "+one",
  "+two",
  "diff --git a/gone.txt b/gone.txt",
  "deleted file mode 100644",
  "--- a/gone.txt",
  "+++ /dev/null",
  "@@ -1 +0,0 @@",
  "-bye",
  "diff --git a/img.png b/img.png",
  "Binary files a/img.png and b/img.png differ",
].join("\n");

describe("parseUnifiedDiff", () => {
  it("numbers lines on both sides and counts additions and deletions per file", () => {
    const files = parseUnifiedDiff(DIFF);
    expect(files.map((f) => [f.path, f.status, f.additions, f.deletions])).toEqual([
      ["calc.py", "M", 2, 1],
      ["new.txt", "A", 2, 0],
      ["gone.txt", "D", 0, 1],
      ["img.png", "M", 0, 0],
    ]);
    const lines = files[0].hunks[0].lines;
    expect(lines[0]).toEqual({ kind: "ctx", oldNo: 1, newNo: 1, text: "def add(a, b):" });
    expect(lines[1]).toEqual({ kind: "del", oldNo: 2, text: "    return a - b" });
    expect(lines[2]).toEqual({ kind: "add", newNo: 2, text: "    return a + b" });
    expect(lines[5]).toEqual({ kind: "add", newNo: 5, text: "    # new comment" });
    expect(lines[6]).toMatchObject({ kind: "note" });
    expect(files[3].binary).toBe(true);
  });

  it("caps very large files and still counts everything", () => {
    const body = Array.from({ length: 2000 }, (_, i) => `+line ${i}`);
    const [f] = parseUnifiedDiff(["diff --git a/big b/big", "new file mode 100644", "@@ -0,0 +1,2000 @@", ...body].join("\n"));
    expect(f.additions).toBe(2000);
    expect(f.truncated).toBe(true);
    expect(f.hunks[0].lines.length).toBeLessThan(700);
  });

  it("ignores garbage without throwing", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
    expect(parseUnifiedDiff("not a diff\n<script>alert(1)</script>")).toEqual([]);
  });
});

describe("history helpers", () => {
  it("builds a short title from the instructions and carries the apply state", () => {
    const h = toHistoryItem({ id: "a1", title: "t", instructions: "Fix the bug\nmore detail", status: "completed", outcome: "unverified", apply_state: "applied", created_at: "2026-01-01T00:00:00Z", estimated_cost_usd: 0.01, model: "m" });
    expect(h).toMatchObject({ id: "a1", title: "Fix the bug", status: "completed", outcome: "unverified", applyState: "applied", costUsd: 0.01 });
  });
  it("relativeTime reads naturally", () => {
    const now = Date.parse("2026-01-10T12:00:00Z");
    expect(relativeTime("2026-01-10T11:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-01-10T11:30:00Z", now)).toBe("30 min ago");
    expect(relativeTime("2026-01-10T07:00:00Z", now)).toBe("5 h ago");
    expect(relativeTime("2026-01-08T12:00:00Z", now)).toBe("2 d ago");
    expect(relativeTime("2025-12-01T12:00:00Z", now)).toBe("2025-12-01");
    expect(relativeTime("garbage", now)).toBe("");
  });
});

async function setup(opts: Partial<MockOptions> = {}, uiOver: Partial<SessionUi> = {}) {
  const m = await new MockForge({ events: [ev("workspace.created", { execution_mode: "docker" }), ev("task.finished", { status: "completed" })], ...opts }).start();
  servers.push(m);
  const posted: ToPanel[] = [];
  const confirmed: FileChange[][] = [];
  const ui: SessionUi = {
    post: (msg) => void posted.push(msg),
    notifyApproval: async () => undefined,
    confirmApply: async () => true,
    confirmRevert: async (files) => (confirmed.push(files), true),
    confirmRestore: async () => true,
    showDiff: async () => {},
    ...uiOver,
  };
  return { m, posted, confirmed, session: new Session(new ForgeApi({ baseUrl: m.url }), ui, { statsIntervalMs: 20 }) };
}

describe("Session history and rollback", () => {
  it("lists this folder's conversations and survives a backend that is down", async () => {
    const { session, posted, m } = await setup({ sessions: [{ id: "s1", title: "Fix add", turns: 3, last_status: "completed", last_outcome: "verified", last_prompt: "and the docs", updated_at: "2026-01-01T00:00:00Z" }] });
    const items = await session.loadHistory("/repo");
    expect(items.map((i) => [i.title, i.turns, i.lastPrompt])).toEqual([["Fix add", 3, "and the docs"]]);
    expect(m.calls.find((c) => c.path === "/sessions")).toBeTruthy();
    expect(posted.at(-1)).toMatchObject({ type: "history" });
    const down = new Session(new ForgeApi({ baseUrl: "http://127.0.0.1:9" }), { post: (x) => void posted.push(x), notifyApproval: async () => undefined, confirmApply: async () => true, confirmRevert: async () => true, confirmRestore: async () => true, showDiff: async () => {} });
    expect(await down.loadHistory("/repo")).toEqual([]);
    expect(posted.at(-1)).toMatchObject({ type: "history", items: [], error: expect.stringContaining("not running") });
  });

  it("replays a past session: message, timeline, checks, parsed diff and apply state", async () => {
    const events = [ev("workspace.created", { execution_mode: "docker" }), ev("model.response", { text: "I fixed it.", tool_calls: [] }), ev("task.finished", { status: "completed" })];
    const { session, posted } = await setup({
      events,
      finalTask: { id: "t1", status: "completed", outcome: "unverified", title: "Fix", instructions: "Fix add()", apply_state: "applied", iterations: 2, max_iterations: 30, tool_calls: 3, tool_failures: 0, policy_denials: 0, input_tokens: 10, output_tokens: 5, report: { agent: { final_message: "ok" } } },
      diff: { diff: DIFF, files: [{ path: "calc.py", status: "M", additions: 2, deletions: 1 }], live: false },
      checks: [
        { phase: "final", kind: "syntax", command: "python -m py_compile calc.py", status: "passed", exit_code: 0 },
        { phase: "final", kind: "test", name: "tests", status: "unavailable", note: "No test command detected" },
      ],
    });
    const r = await session.openSession("s1");
    expect(posted[0]).toEqual({ type: "clear" });
    expect(posted[1]).toMatchObject({ type: "sessionInfo", id: "s1" });
    expect(posted[2]).toEqual({ type: "turn", prompt: "Fix add()", turn: 1 });
    expect(r?.sessionId).toBe(null); // the mock task carries no session id: the field is read, not invented
    expect(posted.filter((p) => p.type === "item").map((p) => (p as { item: { text: string } }).item.text)).toContain("I fixed it.");
    expect(r?.applyState).toBe("applied");
    expect(r?.diffs.map((d) => d.path)).toEqual(["calc.py", "new.txt", "gone.txt", "img.png"]);
    expect(r?.checks.map((c) => [c.kind, c.status])).toEqual([["syntax", "passed"], ["test", "unavailable"]]);
    expect(posted.at(-1)).toMatchObject({ type: "status", busy: false });
  });

  it("reverts after a preview and a confirmation", async () => {
    const { session, posted, confirmed, m } = await setup();
    expect(await session.revertTask("t1")).toBe(true);
    expect(m.reverts).toEqual([{ confirm: false, dry_run: true }, { confirm: true, dry_run: false }]);
    expect(confirmed[0]).toEqual([{ path: "x", status: "M", additions: 0, deletions: 0 }]);
    expect(posted).toContainEqual({ type: "reverted", taskId: "t1" });
  });

  it("declining the revert confirmation writes nothing", async () => {
    const { session, m } = await setup({}, { confirmRevert: async () => false });
    expect(await session.revertTask("t1")).toBe(false);
    expect(m.reverts).toEqual([{ confirm: false, dry_run: true }]);
  });

  it("shows the server's refusal when you changed the files since", async () => {
    const { session, posted } = await setup({ revertError: { status: 409, code: "conflict", message: "Nothing was written. x: changed in your repository since the task was applied" } });
    expect(await session.revertTask("t1")).toBe(false);
    const n = posted.filter((p): p is Extract<ToPanel, { type: "notice" }> => p.type === "notice").at(-1);
    expect(n).toMatchObject({ kind: "error" });
    expect(n?.text).toContain("changed in your repository");
  });
});

describe("panel script", () => {
  it("is syntactically valid JavaScript", () => {
    const html = getHtml("n0nce", "vscode-resource:");
    const script = /<script nonce="n0nce">([\s\S]*)<\/script>/.exec(html)![1];
    expect(() => new Function("acquireVsCodeApi", "document", "window", script)).not.toThrow();
  });

  it("reads Markdown into data, never markup", () => {
    const html = getHtml("n0nce", "vscode-resource:");
    const script = /<script nonce="n0nce">([\s\S]*)<\/script>/.exec(html)![1];
    const parser = /\/\/ <md-parser>[\s\S]*\/\/ <\/md-parser>/.exec(script)![0];
    const parse = new Function(`${parser}\nreturn parseMd;`)() as (t: string) => any[];
    const [p] = parse("**Done.** Fixed `foo()` and *wrote* <b>tests</b>");
    expect(p.c[0]).toEqual({ k: "b", c: [{ k: "t", v: "Done." }] });
    expect(p.c.some((n: any) => n.k === "code" && n.v === "foo()")).toBe(true);
    expect(p.c.some((n: any) => n.k === "i")).toBe(true);
    expect(p.c.at(-1).v).toContain("<b>tests</b>"); // stays literal text
    const blocks = parse("## Plan\n- a\n- b\n\n```py\nx = '**no**'\n```\n\n| h |\n|---|\n| c |");
    expect(blocks.map((b) => b.t)).toEqual(["h", "ul", "code", "table"]);
    expect(blocks[2].text).toBe("x = '**no**'");
    expect(parse("snake_case_name and a * b")[0].c).toEqual([{ k: "t", v: "snake_case_name and a * b" }]);
  });
});

describe("binary files in the replay", () => {
  it("records how big a binary file was and is", async () => {
    const m = await new MockForge({
      events: [ev("task.finished", { status: "completed" })],
      finalTask: { id: "t1", status: "completed", outcome: "verified", title: "x", instructions: "x", iterations: 1, max_iterations: 30, tool_calls: 1, tool_failures: 0, policy_denials: 0, input_tokens: 1, output_tokens: 1, report: {} },
      diff: { diff: "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n", files: [{ path: "logo.png", status: "M", additions: 0, deletions: 0 }], live: false },
    }).start();
    servers.push(m);
    const posted: ToPanel[] = [];
    const api = new ForgeApi({ baseUrl: m.url });
    // the mock serves any file path with the same two sizes; what matters is that the session asks and records them
    const session = new Session(api, { post: (x) => void posted.push(x), notifyApproval: async () => undefined, confirmApply: async () => true, confirmRevert: async () => true, confirmRestore: async () => true, showDiff: async () => {} });
    const r = await session.openSession("s1");
    const fd = r?.diffs[0];
    expect(fd?.binary).toBe(true);
    expect(fd?.sizeBefore).toBe(90);
    expect(fd?.sizeAfter).toBe(120);
  });
});

describe("images", () => {
  it("sends attached pictures with the task and shows them in the message", async () => {
    const { session, posted, m } = await setup({ events: [ev("task.finished", { status: "completed" })] });
    await session.run({ repoPath: "/repo", prompt: "what is this?", images: [{ name: "shot.png", mediaType: "image/png", data: "iVBORw0KGgo=" }] });
    const created = m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!;
    expect(created.body.images).toEqual([{ name: "shot.png", media_type: "image/png", data: "iVBORw0KGgo=" }]);
    expect(posted[0]).toEqual({ type: "reset", prompt: "what is this?", images: [{ name: "shot.png", src: "data:image/png;base64,iVBORw0KGgo=" }] });
  });

  it("sends nothing extra when there are no pictures", async () => {
    const { session, posted, m } = await setup({ events: [ev("task.finished", { status: "completed" })] });
    await session.run({ repoPath: "/repo", prompt: "plain" });
    expect("images" in m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!.body).toBe(false);
    expect(posted[0]).toEqual({ type: "reset", prompt: "plain" });
  });

  it("brings the pictures back when an earlier session is opened", async () => {
    const { session, posted } = await setup({
      finalTask: { id: "t1", status: "completed", outcome: "verified", title: "x", instructions: "look", images: [{ index: 1, name: "a.png" }], iterations: 1, max_iterations: 30, tool_calls: 0, tool_failures: 0, policy_denials: 0, input_tokens: 1, output_tokens: 1, report: {} },
    });
    await session.openSession("s1");
    expect(posted[2]).toEqual({ type: "turn", prompt: "look", turn: 1, images: [{ name: "a.png", src: "data:image/png;base64,iVBORw0KGgo=" }] });
  });
});

describe("error messages", () => {
  it("reads FastAPI's detail field when the body has no error object", async () => {
    const server = (await import("http")).createServer((_, res) => { res.writeHead(404, { "Content-Type": "application/json" }); res.end(JSON.stringify({ detail: "Not Found" })); });
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
    const port = (server.address() as import("net").AddressInfo).port;
    try {
      await new ForgeApi({ baseUrl: `http://127.0.0.1:${port}` }).health();
      throw new Error("should have failed");
    } catch (e) {
      expect((e as Error).message).toBe("Not Found");
    } finally {
      server.close();
    }
  });
});

describe("one conversation across many messages", () => {
  it("continues the conversation: no clearing, the session id goes with the task", async () => {
    const { session, posted, m } = await setup({ events: [ev("task.finished", { status: "completed" })], turn: 2 });
    const r = await session.run({ repoPath: "/repo", prompt: "and now the docs", sessionId: "s1", sessionTitle: "Fix add" });
    const created = m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!;
    expect(created.body.session_id).toBe("s1");
    expect(posted[0]).toEqual({ type: "turn", prompt: "and now the docs" }); // added under what is on screen
    expect(posted.some((p) => p.type === "reset")).toBe(false);
    expect(posted).toContainEqual({ type: "sessionInfo", id: "s1", title: "Fix add", turns: 2 });
    expect(r?.sessionId).toBe(null); // (the mock's task detail has no session fields; the run itself is what matters here)
  });

  it("starts a new conversation (clears the panel) when there is no session id", async () => {
    const { session, posted, m } = await setup({ events: [ev("task.finished", { status: "completed" })] });
    await session.run({ repoPath: "/repo", prompt: "fresh" });
    expect("session_id" in m.calls.find((c) => c.path === "/tasks" && c.method === "POST")!.body).toBe(false);
    expect(posted[0]).toEqual({ type: "reset", prompt: "fresh" });
  });

  it("explains a busy conversation instead of a raw error", async () => {
    const { session, posted } = await setup({ createError: { status: 409, code: "conflict", message: "This session is still working on its previous message; wait for it or cancel it" } });
    await session.run({ repoPath: "/repo", prompt: "too soon", sessionId: "s1" });
    expect(posted.some((p) => p.type === "notice" && /still being worked on/.test(p.text))).toBe(true);
  });

  it("replays every message in order; rolled-back ones are shown dimmed and not replayed", async () => {
    const turn = (n: number, extra: object = {}) => ({ id: "t1", status: "completed", outcome: "verified", title: "x", instructions: `message ${n}`, turn: n, discarded: false, has_checkpoint: true, apply_state: null, images: [], iterations: 1, max_iterations: 30, tool_calls: 0, tool_failures: 0, policy_denials: 0, input_tokens: 1, output_tokens: 1, report: {}, ...extra });
    const { session, posted } = await setup({
      events: [ev("workspace.created", { execution_mode: "docker" }), ev("task.finished", { status: "completed" })],
      sessionDetail: { id: "s1", title: "Three messages", turns: 2, items: [turn(1), turn(2, { discarded: true }), turn(3)] },
    });
    const last = await session.openSession("s1");
    const turns = posted.filter((p): p is Extract<ToPanel, { type: "turn" }> => p.type === "turn");
    expect(turns.map((t) => [t.prompt, t.turn, !!t.discarded])).toEqual([["message 1", 1, false], ["message 2", 2, true], ["message 3", 3, false]]);
    expect(posted.filter((p) => p.type === "result")).toHaveLength(2); // the rolled-back message has no result card
    expect(posted[1]).toEqual({ type: "sessionInfo", id: "s1", title: "Three messages", turns: 2 });
    expect(last?.turn).toBe(1);
  });

  it("goes back to an earlier response after a preview and a confirmation, then shows the conversation again", async () => {
    const asked: [number, number, string[]][] = [];
    const { session, posted, m } = await setup({}, { confirmRestore: async (turn, discarded, files) => (asked.push([turn, discarded, files.map((f) => f.path)]), true) });
    expect(await session.restoreSession("s1", 1)).toBe(true);
    expect(m.restores).toEqual([{ turn: 1, confirm: false, dry_run: true }, { turn: 1, confirm: true, dry_run: false }]);
    expect(asked).toEqual([[1, 1, ["x"]]]);
    expect(posted.some((p) => p.type === "clear")).toBe(true); // the panel was refreshed
    expect(posted.some((p) => p.type === "notice" && /Back to response 1/.test(p.text))).toBe(true);
  });

  it("declining the confirmation changes nothing", async () => {
    const { session, m } = await setup({}, { confirmRestore: async () => false });
    expect(await session.restoreSession("s1", 0)).toBe(false);
    expect(m.restores).toEqual([{ turn: 0, confirm: false, dry_run: true }]);
  });

  it("renames a conversation", async () => {
    const { session, m } = await setup();
    expect(await session.renameSession("s1", "Docs work")).toBe(true);
    expect(m.renames).toEqual([{ title: "Docs work" }]);
  });
});
