import { afterEach, describe, expect, it } from "vitest";
import { ForgeApi, ForgeApiError, SseParser, describeEvent, shortArgs } from "../src/core";
import { MockForge, ev } from "./mockForge";

const servers: MockForge[] = [];
afterEach(async () => {
  while (servers.length) await servers.pop()!.stop();
});
const mock = async (opts: ConstructorParameters<typeof MockForge>[0]) => {
  const m = await new MockForge(opts).start();
  servers.push(m);
  return m;
};
const event = (type: string, data: unknown) => ({ id: 1, task_id: "t", ts: "", type, data });

describe("SseParser", () => {
  it("parses events split across arbitrary chunk boundaries, CRLF and comments", () => {
    const p = new SseParser();
    const text = ': keep-alive\r\n\r\nid: 1\r\ndata: {"id": 1,\r\ndata: "type": "a"}\r\n\r\nevent: end\r\ndata: {}\r\n\r\n';
    const out = [];
    for (let i = 0; i < text.length; i += 3) out.push(...p.push(text.slice(i, i + 3))); // 3-char chunks
    expect(out).toEqual([
      { event: "message", data: { id: 1, type: "a" } },
      { event: "end", data: {} },
    ]);
  });

  it("returns null data for malformed JSON instead of throwing", () => {
    expect(new SseParser().push("data: {oops\n\n")).toEqual([{ event: "message", data: null }]);
  });

  it("emits nothing until the blank line", () => {
    const p = new SseParser();
    expect(p.push('data: {"id": 2}\n')).toEqual([]);
    expect(p.push("\n")).toEqual([{ event: "message", data: { id: 2 } }]);
  });
});

describe("describeEvent", () => {
  it("shows tool proposals, refusals, approvals, exits and verdicts", () => {
    expect(describeEvent(event("tool.proposed", { tool: "run_command", arguments: { command: "pytest -q", workdir: ".", timeout_seconds: null } }))).toEqual({ kind: "tool", text: "run_command command=pytest -q" });
    expect(describeEvent(event("tool.authorization", { decision: "allow", reason: "ok" }))).toBeNull();
    expect(describeEvent(event("tool.authorization", { decision: "deny", reason: "nope" }))).toEqual({ kind: "error", text: "Refused by policy: nope" });
    expect(describeEvent(event("tool.authorization", { decision: "approve", reason: "r" }))?.kind).toBe("warn");
    const failing = describeEvent(event("tool.result", { ok: true, data: { exit_code: 1, duration_seconds: 2, output: "a\n\nFAILED test_x\n" } }));
    expect(failing).toMatchObject({ kind: "warn", text: "exit 1 in 2s" });
    expect(failing?.detail).toContain("FAILED test_x");
    expect(describeEvent(event("tool.result", { ok: true, data: { exit_code: 0, duration_seconds: 1, output: "noise" } }))).toEqual({ kind: "ok", text: "exit 0 in 1s", detail: undefined });
    expect(describeEvent(event("tool.result", { ok: false, error: "boom" }))).toEqual({ kind: "error", text: "boom" });
    expect(describeEvent(event("tool.result", { ok: false, error: "Not approved: rejected by human" }))).toBeNull(); // approval.decided already says it
    expect(describeEvent(event("tool.result", { ok: false, error: "Denied by policy: x" }))?.kind).toBe("error");
    expect(describeEvent(event("verification.result", { outcome: "verified", reasons: [] }))?.kind).toBe("ok");
    expect(describeEvent(event("verification.result", { outcome: "verification_failed", reasons: ["x"] }))?.kind).toBe("error");
    expect(describeEvent(event("verification.result", { outcome: "unverified", reasons: [] }))?.kind).toBe("warn");
  });

  it("ignores noise and keeps text as plain strings (never markup)", () => {
    for (const t of ["model.request", "task.created", "task.finished", "approval.requested"]) expect(describeEvent(event(t, {}))).toBeNull();
    expect(describeEvent(event("model.response", { text: "<img src=x onerror=alert(1)>" }))?.text).toBe("<img src=x onerror=alert(1)>");
    expect(describeEvent(event("model.response", { text: "" }))).toBeNull();
    expect(shortArgs({ content: "x".repeat(500) }).length).toBeLessThanOrEqual(110);
  });
});

describe("ForgeApi", () => {
  it("maps API errors and unreachable servers to ForgeApiError", async () => {
    const m = await mock({ events: [] });
    const api = new ForgeApi({ baseUrl: m.url });
    await expect(api.getTask("missing")).rejects.toMatchObject({ status: 404, code: "not_found" });
    const dead = new ForgeApi({ baseUrl: "http://127.0.0.1:9" });
    const e = await dead.health().catch((x) => x);
    expect(e).toBeInstanceOf(ForgeApiError);
    expect((e as ForgeApiError).unreachable).toBe(true);
  });

  it("sends the bearer token when configured and surfaces 401 otherwise", async () => {
    const m = await mock({ events: [], token: "s3cret" });
    await expect(new ForgeApi({ baseUrl: m.url }).config()).rejects.toMatchObject({ status: 401 });
    await new ForgeApi({ baseUrl: m.url, getToken: async () => "s3cret" }).config();
    expect(m.calls.at(-1)?.auth).toBe("Bearer s3cret");
  });

  it("streams events until the end marker", async () => {
    const m = await mock({ events: [ev("workspace.created", { execution_mode: "docker" }), ev("task.finished", { status: "completed" })] });
    const got: string[] = [];
    for await (const e of new ForgeApi({ baseUrl: m.url }).streamEvents("t1")) got.push(e.type);
    expect(got).toEqual(["workspace.created", "task.finished"]);
  });

  it("reconnects after a dropped connection and resumes after the last event id (no gaps, no repeats)", async () => {
    const events = Array.from({ length: 6 }, (_, i) => ev("tool.proposed", { tool: `t${i}`, arguments: {} }));
    const m = await mock({ events, dropAfter: 2 });
    const ids: number[] = [];
    for await (const e of new ForgeApi({ baseUrl: m.url }).streamEvents("t1")) ids.push(e.id);
    expect(ids).toEqual([1, 2, 3, 4, 5, 6]); // nothing lost, nothing delivered twice
    // the second connection resumes right after the last event the client actually received (1 or 2: event 2 may
    // have been in flight when the socket was killed)
    expect(m.streamAfters).toHaveLength(2);
    expect(m.streamAfters[0]).toBe(0);
    expect([1, 2]).toContain(m.streamAfters[1]);
  });

  it("stops quietly when aborted", async () => {
    const m = await mock({ events: [ev("approval.requested", { approval_id: "a" })] }); // blocks until a decision
    const ac = new AbortController();
    const got: string[] = [];
    const run = (async () => {
      for await (const e of new ForgeApi({ baseUrl: m.url }).streamEvents("t1", ac.signal)) got.push(e.type);
    })();
    await new Promise((r) => setTimeout(r, 150));
    ac.abort();
    await run;
    expect(got).toEqual(["approval.requested"]);
  });
});
