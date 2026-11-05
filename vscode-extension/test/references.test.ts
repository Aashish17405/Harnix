import { describe, expect, it } from "vitest";
import { ActivityLog, Stats, describeEvent, extractReferences, formatStats, formatTokens, fuzzyFilter, looksSecret } from "../src/core";

const ev = (type: string, data: unknown) => ({ id: 1, task_id: "t", ts: "", type, data });
const files = new Set(["src/app.py", "src/utils/helpers.py", "README.md", "tests/test_app.py", "a b/space name.py"]);
const exists = (p: string) => files.has(p);

describe("extractReferences", () => {
  it("turns mentions of existing files (with optional line ranges) into references", () => {
    const r = extractReferences("explain @src/app.py#10-20, then fix @README.md. And @tests/test_app.py#5", exists);
    expect(r.refs).toEqual([
      { path: "src/app.py", start_line: 10, end_line: 20 },
      { path: "README.md" },
      { path: "tests/test_app.py", start_line: 5, end_line: 5 },
    ]);
    expect(r.text).toBe("explain src/app.py (lines 10-20), then fix README.md. And tests/test_app.py (lines 5-5)");
    expect(r.missing).toEqual([]);
  });

  it("leaves @names, emails, missing files and parent paths exactly as typed, and reports the missing ones", () => {
    const r = extractReferences("ping @alice (a@b.com) read @nope.py and @../etc/passwd", exists);
    expect(r.refs).toEqual([]);
    expect(r.text).toBe("ping @alice (a@b.com) read @nope.py and @../etc/passwd");
    expect(r.missing).toEqual(["alice", "nope.py", "../etc/passwd"]);
  });

  it("normalizes backslashes and removes duplicates", () => {
    const r = extractReferences("@src\\app.py and @src/app.py", exists);
    expect(r.refs).toEqual([{ path: "src/app.py" }]);
  });

  it("handles a mention at the very start and nothing to extract", () => {
    expect(extractReferences("@README.md summarize", exists).refs).toEqual([{ path: "README.md" }]);
    expect(extractReferences("nothing here", exists)).toEqual({ text: "nothing here", refs: [], missing: [] });
  });
});

describe("fuzzyFilter / looksSecret", () => {
  const all = ["src/app.py", "src/utils/helpers.py", "tests/test_app.py", "docs/application-notes.md", ".env", "keys/id_rsa", "cfg/server.pem", ".env.example", ".ssh/config"];
  it("ranks basename-prefix matches first and never suggests secret-looking files", () => {
    expect(fuzzyFilter(all, "app")).toEqual(["src/app.py", "docs/application-notes.md", "tests/test_app.py"]);
    const everything = fuzzyFilter(all, "", 20);
    for (const secret of [".env", "keys/id_rsa", "cfg/server.pem", ".ssh/config"]) expect(everything).not.toContain(secret);
    expect(everything).toContain(".env.example"); // placeholders are fine
  });
  it("matches subsequences and respects the limit", () => {
    expect(fuzzyFilter(all, "shlp")).toEqual(["src/utils/helpers.py"]);
    expect(fuzzyFilter(all, "py", 2)).toHaveLength(2);
    expect(fuzzyFilter(all, "zzzz")).toEqual([]);
  });
  it("classifies names like the backend", () => {
    for (const s of [".env", ".env.local", "a/b/id_rsa", "x.pem", "x.key", ".aws/credentials", "credentials"]) expect(looksSecret(s), s).toBe(true);
    for (const s of [".env.example", "src/env.py", "keyboard.py", "README.md"]) expect(looksSecret(s), s).toBe(false);
  });
});

describe("timeline items for files and edits", () => {
  it("edits carry the file and an inline diff; reads carry the file and start line", () => {
    const edit = describeEvent(ev("tool.proposed", { tool: "edit_file", arguments: { path: "a.py", old_text: "x - y", new_text: "x + y" } }));
    expect(edit).toMatchObject({ kind: "tool", text: "edit_file a.py", file: "a.py", diff: { path: "a.py", old: "x - y", new: "x + y" } });
    const write = describeEvent(ev("tool.proposed", { tool: "write_file", arguments: { path: "n.py", content: "a\nb\nc" } }));
    expect(write).toMatchObject({ file: "n.py", diff: { newFile: true, lines: 3, new: "a\nb\nc" } });
    expect(describeEvent(ev("tool.proposed", { tool: "read_file", arguments: { path: "a.py", start_line: 12 } }))).toMatchObject({ file: "a.py", line: 12, text: "read_file a.py (from line 12)" });
    expect(describeEvent(ev("tool.proposed", { tool: "delete_file", arguments: { path: "old.py" } }))?.file).toBe("old.py");
  });

  it("long edits are clipped, with a note", () => {
    const big = Array.from({ length: 100 }, (_, i) => `line ${i}`).join("\n");
    const d = describeEvent(ev("tool.proposed", { tool: "write_file", arguments: { path: "big.py", content: big } }))!.diff!;
    expect(d.new.split("\n")).toHaveLength(41);
    expect(d.new).toContain("(60 more lines)");
  });

  it("reports what was attached and what was skipped, and why", () => {
    const ok = describeEvent(ev("task.references", { included: [{ path: "a.py", start: 1, end: 5, truncated: false }, { path: "b.py", start: 10, end: 20, truncated: false }, { path: "big.py", start: 1, end: 400, truncated: true }], skipped: [] }));
    expect(ok).toEqual({ kind: "info", text: "Attached 3 file(s): a.py, b.py:10-20, big.py:1-400 (cut)", detail: undefined });
    const mixed = describeEvent(ev("task.references", { included: [], skipped: [{ path: ".env", reason: "looks like a secret or protected file" }] }));
    expect(mixed).toMatchObject({ kind: "warn", text: "No referenced file could be attached" });
    expect(mixed?.detail).toContain("could not attach .env: looks like a secret");
    expect(describeEvent(ev("task.references", { included: [], skipped: [] }))).toBeNull();
  });
});

describe("ActivityLog (the receipt)", () => {
  it("summarizes files read/changed, commands with exit codes, approvals and problems", () => {
    const log = new ActivityLog();
    const call = (id: string, tool: string, args: object, result: object) => {
      log.add(ev("tool.proposed", { call_id: id, tool, arguments: args }));
      log.add(ev("tool.result", { call_id: id, tool, ...result }));
    };
    call("1", "read_file", { path: "a.py" }, { ok: true });
    call("2", "read_file", { path: "a.py" }, { ok: true }); // same file twice: listed once
    call("3", "edit_file", { path: "a.py" }, { ok: true });
    call("4", "write_file", { path: "n.py" }, { ok: true });
    call("5", "run_tests", {}, { ok: true, data: { command: "python -m pytest -q", exit_code: 1 } });
    call("6", "run_command", { command: "python -c 1" }, { ok: false, error: "Not approved: rejected by human" });
    call("7", "read_file", { path: "../x" }, { ok: false, error: "Denied by policy: escapes" });
    call("8", "edit_file", { path: "z.py" }, { ok: false, error: "old_text not found" });
    log.add(ev("approval.decided", { approved: true }));
    log.add(ev("approval.decided", { approved: false }));
    const s = log.summary();
    expect(s.read).toEqual(["a.py"]);
    expect(s.changed).toEqual(["a.py", "n.py"]);
    expect(s.commands).toEqual([{ command: "python -m pytest -q", exit: 1 }]);
    expect(s).toMatchObject({ approved: 1, rejected: 1, refused: 2, failures: 1 });
  });

  it("returns a copy, not live state", () => {
    const log = new ActivityLog();
    const a = log.summary();
    a.read.push("x");
    expect(log.summary().read).toEqual([]);
  });
});

describe("stats formatting", () => {
  const base: Stats = { iterations: 3, maxIterations: 30, toolCalls: 5, toolFailures: 0, refused: 0, inputTokens: 5830, outputTokens: 196, contextTokens: 2100, costUsd: 0.00067, seconds: 11.6 };
  it("formats tokens compactly", () => {
    expect([formatTokens(999), formatTokens(1500), formatTokens(12000)]).toEqual(["999", "1.5k", "12k"]);
  });
  it("shows steps, tools, tokens, context, cost and time", () => {
    expect(formatStats(base)).toBe("step 3/30 · 5 tool calls · 5.8k in / 196 out · context 2.1k · ~$0.0007 · 12s");
    expect(formatStats({ ...base, toolFailures: 1, refused: 2 })).toContain("5 tool calls (1 failed, 2 refused)");
  });
  it("says the cost is unknown instead of showing zero", () => {
    expect(formatStats({ ...base, costUsd: null, contextTokens: null, seconds: null })).toBe("step 3/30 · 5 tool calls · 5.8k in / 196 out · cost unknown");
  });
});

describe("knowledge graph events", () => {
  it("shows what was indexed, that a partial index continues, and that an outage is not fatal", () => {
    const ok = describeEvent(ev("graph.indexed", { files: 101, symbols: 699, files_parsed: 3, files_unchanged: 98, seconds: 0.4, complete: true }));
    expect(ok).toEqual({ kind: "info", text: "Knowledge graph: 101 files, 699 symbols (3 re-indexed, 98 unchanged, 0.4s)" });
    const partial = describeEvent(ev("graph.indexed", { files: 10, symbols: 50, files_parsed: 10, files_unchanged: 0, seconds: 60, complete: false }));
    expect(partial).toMatchObject({ kind: "warn" });
    expect(partial?.text).toContain("partial, the rest continues on the next run");
    expect(describeEvent(ev("graph.indexed", { skipped: "another index run was in progress" }))).toEqual({ kind: "warn", text: "Knowledge graph not re-indexed: another index run was in progress" });
    const down = describeEvent(ev("graph.unavailable", { reason: "cannot reach Neo4j (refused)" }));
    expect(down).toEqual({ kind: "warn", text: "Knowledge graph unavailable: continuing without it", detail: "cannot reach Neo4j (refused)" });
    expect(describeEvent(ev("graph.context", { chars: 1461, seed_files: 2 }))).toEqual({ kind: "info", text: "Knowledge graph context added (1461 chars, 2 focus file(s))" });
  });
});

describe("thinking, sub-agent, skill, memory and context events", () => {
  it("shows the agent text, and a downgraded thinking level with its reason", () => {
    expect(describeEvent(ev("model.response", { text: "hi", thinking: { requested: "high", applied: "high" } }))).toEqual({ kind: "agent", text: "hi", detail: undefined });
    const down = describeEvent(ev("model.response", { text: "hi", thinking: { requested: "xhigh", applied: "none", reason: "no reasoning with tools" } }));
    expect(down?.detail).toBe("thinking xhigh -> none: no reasoning with tools");
    expect(describeEvent(ev("model.response", { text: "", thinking: { requested: "low", applied: "none", reason: "r" } }))).toEqual({ kind: "warn", text: "Thinking low -> none", detail: "r" });
    expect(describeEvent(ev("model.response", { text: "x", agent: "explore-1" }))?.text).toBe("[explore-1] x");
  });

  it("describes sub-agents, skills, memory and context management", () => {
    expect(describeEvent(ev("subagent.started", { agent: "explore-1", description: "find add", thinking: "low", max_steps: 15, tools: ["read_file"] }))).toEqual({
      kind: "tool", text: "sub-agent explore-1: find add", detail: "thinking low, at most 15 steps; tools: read_file",
    });
    expect(describeEvent(ev("subagent.finished", { agent: "explore-1", reason: "done", steps: 3 }))?.kind).toBe("ok");
    expect(describeEvent(ev("subagent.finished", { agent: "g-1", reason: "max_steps", steps: 15 }))?.kind).toBe("warn");
    expect(describeEvent(ev("skills.available", { skills: [{ id: "pdf", listed: true }, { id: "x", listed: false }] }))?.text).toBe("Skills available: pdf");
    expect(describeEvent(ev("skills.available", { skills: [{ id: "x", listed: false }] }))).toBeNull();
    expect(describeEvent(ev("skill.missing", { id: "nope" }))?.kind).toBe("warn");
    expect(describeEvent(ev("memory.context", { shown: 2, notes: 3 }))?.text).toBe("Project memory: 2 of 3 note(s) in context");
    expect(describeEvent(ev("context.compacted", { reason: "threshold", tokens_before: 9000, tokens_after: 3000 }))?.text).toBe("Context compacted (threshold): ~9000 -> ~3000 tokens");
    expect(describeEvent(ev("context.compaction_failed", { reason: "overflow", error: "x" }))?.kind).toBe("warn");
  });
});
