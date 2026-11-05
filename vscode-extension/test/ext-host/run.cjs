// node test/ext-host/run.cjs <repo-dir inside an authorized root> "<prompt>"
// Launches a separate, isolated VS Code instance (own user-data/extensions dirs) with this extension loaded.
const path = require("path");
const os = require("os");
const fs = require("fs");
const { runTests } = require("@vscode/test-electron");

(async () => {
  // VS Code's own shells set these; left in place they would make Code.exe run as plain Node or attach to another instance.
  for (const k of Object.keys(process.env)) if (k === "ELECTRON_RUN_AS_NODE" || k.startsWith("VSCODE_")) delete process.env[k];
  const [repo, prompt] = process.argv.slice(2);
  if (!repo || !prompt) throw new Error('usage: node test/ext-host/run.cjs <repo-dir> "<prompt>"');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "forge-host-"));
  let code = 1;
  try {
    code = await runTests({
      vscodeExecutablePath: process.env.VSCODE_EXE || "C:\\Program Files\\Microsoft VS Code\\Code.exe",
      extensionDevelopmentPath: path.resolve(__dirname, "../.."),
      extensionTestsPath: path.resolve(__dirname, "suite.cjs"),
      extensionTestsEnv: { FORGE_TEST_PROMPT: prompt },
      launchArgs: [repo, "--disable-extensions", "--disable-workspace-trust", "--user-data-dir", path.join(tmp, "user"), "--extensions-dir", path.join(tmp, "ext")],
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true }); // also when the run failed
  }
  process.exit(code);
})().catch((e) => (console.error("HOST TEST FAILED:", e.message), process.exit(1)));
