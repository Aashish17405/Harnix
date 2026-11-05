// Runs INSIDE a real VS Code extension host (launched by run.cjs). Needs a running Forge backend.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

exports.run = async function run() {
  try {
    await body();
  } catch (e) {
    console.error("[host] TEST FAILED:", e && e.stack ? e.stack : e); // test-electron does not surface this itself
    throw e;
  }
};

async function body() {
  // a fresh profile may still be opening the folder when the extension tests start
  for (let i = 0; i < 100 && !vscode.workspace.workspaceFolders?.length; i++) await new Promise((r) => setTimeout(r, 100));
  const ext = vscode.extensions.getExtension("forge-local.forge-vscode");
  assert.ok(ext, "the extension is installed in the extension host");
  const api = await ext.activate();
  assert.ok(ext.isActive, "extension activated");

  const commands = await vscode.commands.getCommands(true);
  for (const c of ["forge.newTask", "forge.askAboutSelection", "forge.addSelectionToPrompt", "forge.cancel", "forge.startServer", "forge.doctor", "forge.setApiToken"]) {
    assert.ok(commands.includes(c), `command ${c} is registered`);
  }
  await vscode.commands.executeCommand("forge.chat.focus"); // creates the webview view and loads its HTML without throwing
  console.log("[host] activated; commands registered; panel view resolved");

  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  assert.ok(folder, "a workspace folder is open");
  const file = path.join(folder, "rangesum.py");
  const before = fs.readFileSync(file, "utf8");

  const msgs = [];
  const ui = { post: (m) => msgs.push(m), notifyApproval: async () => false, confirmApply: async () => true, showDiff: async () => {} };
  const result = await api.runPrompt(process.env.FORGE_TEST_PROMPT, ui);
  console.log("[host] run result:", JSON.stringify({ status: result?.status, outcome: result?.outcome, files: result?.files, stats: result?.stats }));
  assert.ok(result, `the run produced a result; messages: ${JSON.stringify(msgs.filter((m) => m.type === "notice"))}`);
  assert.strictEqual(result.status, "completed");
  assert.strictEqual(result.verified, true, "verified by independent checks");
  assert.deepStrictEqual(result.files.map((f) => f.path), ["rangesum.py"]);
  assert.strictEqual(fs.readFileSync(file, "utf8"), before, "the workspace is untouched until Apply");
  assert.ok(msgs.some((m) => m.type === "item" && m.item.kind === "tool"), "timeline items were produced");

  assert.strictEqual(await api.applyTask(result.taskId, { post: (m) => msgs.push(m), confirmApply: async () => true }), true);
  const after = fs.readFileSync(file, "utf8");
  assert.notStrictEqual(after, before, "Apply changed the workspace file");
  assert.ok(after.includes("n + 1"), "the fix landed in the real file");
  const doc = await vscode.workspace.openTextDocument(file); // the editor sees the change
  assert.ok(doc.getText().includes("n + 1"));
  console.log("[host] applied: file now contains the fix");
}
