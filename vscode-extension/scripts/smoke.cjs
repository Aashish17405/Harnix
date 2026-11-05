// Manual end-to-end check of the extension's client + session against a REAL running backend (and real model).
//   cd vscode-extension && npm run compile && node scripts/smoke.cjs <repo-dir-inside-an-authorized-root> "<instructions>" [--apply]
// It uses the compiled extension code with a console UI that auto-REJECTS approvals (safe) and, with --apply,
// applies the result if the task finished verified.
const { ForgeApi } = require("../out/core");
const { Session } = require("../out/session");

async function main() {
  const [repo, prompt, ...flags] = process.argv.slice(2);
  if (!repo || !prompt) throw new Error('usage: node scripts/smoke.cjs <repo> "<instructions>" [--apply]');
  const api = new ForgeApi({ baseUrl: process.env.FORGE_URL || "http://127.0.0.1:8000", getToken: () => process.env.FORGE_API_TOKEN });
  const log = [];
  const ui = {
    post: (m) => {
      log.push(m);
      if (m.type === "item") console.log(`  [${m.item.kind}] ${m.item.text}`);
      else if (m.type === "status") console.log(`  -- ${m.text}`);
      else if (m.type === "notice") console.log(`  !! ${m.kind}: ${m.text}`);
    },
    notifyApproval: async (a) => (console.log(`  approval requested for ${a.tool}: auto-rejecting`), false),
    confirmApply: async (files, verified) => (console.log(`  confirm apply ${files.length} file(s), verified=${verified}`), flags.includes("--apply") && verified),
    showDiff: async (d) => console.log(d),
  };
  const session = new Session(api, ui);
  const result = await session.run({ repoPath: repo, prompt });
  console.log("RESULT", JSON.stringify({ status: result?.status, outcome: result?.outcome, verified: result?.verified, files: result?.files, stats: result?.stats }));
  if (result && result.files.length && flags.includes("--apply")) console.log("APPLIED", await session.applyTask(result.taskId));
  process.exit(result && result.status === "completed" ? 0 : 1);
}
main().catch((e) => (console.error("SMOKE FAILED:", e.message), process.exit(2)));
