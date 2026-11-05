// Drives a REAL, isolated VS Code window the way a person would (click, type, press buttons) and records screenshots.
//   node test/ui/drive.cjs <scratch-repo-dir> [scenario ...]        (needs a running Forge backend; real model => costs cents)
// Scenarios: panel, fix, approval, cancel, doctor, down, refs, agentic.   Screenshots go to %TEMP%\forge-ui-shots.
// `refs` (file references + insights) needs a scratch repo with rangesum.py (buggy range_sum), a test file and a .env,
// and it must run on an UNFIXED repo (the `fix` scenario applies the fix), so run it in its own invocation.
// VS Code is controlled through Electron's remote-debugging port with puppeteer-core (the window is visible on screen).
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const puppeteer = require("puppeteer-core");

const CODE = process.env.VSCODE_EXE || "C:\\Program Files\\Microsoft VS Code\\Code.exe";
const PORT = 9333;
const EXT = path.resolve(__dirname, "../..");
const SHOTS = path.join(os.tmpdir(), "forge-ui-shots");
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (name, ok, detail = "") => (results.push({ name, ok: !!ok, detail }), console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -- " + detail : ""}`));

async function main() {
  const repo = process.argv[2];
  const wanted = process.argv.slice(3);
  if (!repo) throw new Error("usage: node test/ui/drive.cjs <scratch-repo-dir> [scenario ...]");
  fs.rmSync(SHOTS, { recursive: true, force: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "forge-ui-"));
  fs.mkdirSync(path.join(profile, "user", "User"), { recursive: true });
  fs.writeFileSync(path.join(profile, "user", "User", "settings.json"), JSON.stringify({ "workbench.startupEditor": "none", "telemetry.telemetryLevel": "off", "update.mode": "none", "extensions.autoUpdate": false, "window.restoreWindows": "none", "workbench.tips.enabled": false, "security.workspace.trust.enabled": false, "window.dialogStyle": "custom" /* native OS dialogs are invisible to the page; the same API call renders in-page with "custom" */, "diffEditor.renderSideBySide": true, "forge.thinking": "high", ...(process.env.FORGE_UI_THEME ? { "workbench.colorTheme": process.env.FORGE_UI_THEME } : {}), ...(process.env.FORGE_UI_URL ? { "forge.serverUrl": process.env.FORGE_UI_URL } : {}), "diffEditor.useInlineViewWhenSpaceIsLimited": false /* so the two sides can be told apart in a narrow window */ }));
  const env = { ...process.env };
  for (const k of Object.keys(env)) if (k === "ELECTRON_RUN_AS_NODE" || k.startsWith("VSCODE_")) delete env[k];
  // FORGE_VSIX=<file.vsix>: test the PACKAGED extension, installed with `code --install-extension` into the throwaway profile
  // (what a user gets), instead of loading the source folder.
  const vsix = process.env.FORGE_VSIX;
  if (vsix) {
    const cli = path.join(path.dirname(CODE), "bin", "code.cmd");
    const q = (x) => `"${x}"`;
    const out = execFileSync(`${q(cli)} --install-extension ${q(vsix)} --force --extensions-dir=${q(path.join(profile, "ext"))} --user-data-dir=${q(path.join(profile, "user"))}`, { env, encoding: "utf8", shell: true });
    console.log("install:", out.trim().split("\n").slice(-2).join(" | "));
  }
  const child = spawn(CODE, [...(vsix ? [] : [`--extensionDevelopmentPath=${EXT}`]), `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(profile, "user")}`, `--extensions-dir=${path.join(profile, "ext")}`, "--disable-workspace-trust", "--new-window", repo], { env, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60 && !browser; i++) {
      await sleep(1000);
      try { browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${PORT}`, defaultViewport: null }); } catch { /* not up yet */ }
    }
    if (!browser) throw new Error("could not attach to VS Code's debug port");
    const page = await workbench(browser);
    await page.bringToFront();
    await page.waitForSelector(".monaco-workbench", { timeout: 60000 });
    await sleep(3000);
    const ctx = { browser, page, repo, shot: (n) => page.screenshot({ path: path.join(SHOTS, n + ".png") }) };
    const scenarios = { panel, fix, history, session, render, scroll, images, oldbackend, clipboard, pickdialog, approval, cancel, doctor, down, refs, agentic };
    for (const name of Object.keys(scenarios)) {
      if (wanted.length && !wanted.includes(name)) continue;
      if (name === "down" && !wanted.includes("down")) continue; // needs the backend stopped: only when asked for
      if (name === "refs" && !wanted.includes("refs")) continue; // needs an unfixed repo: only when asked for
      if (name === "session" && !wanted.includes("session")) continue; // real backend + real model: one conversation over several messages
      if (name === "clipboard" && !wanted.includes("clipboard")) continue; // real OS clipboard + real Ctrl+V (Windows only, takes over the clipboard)
      if (name === "pickdialog" && !wanted.includes("pickdialog")) continue; // drives the native Windows file dialog (Windows only)
      if (name === "oldbackend" && !wanted.includes("oldbackend")) continue; // run with FORGE_UI_URL=http://127.0.0.1:8012 (a stand-in backend without /file)
      if (name === "scroll" && !wanted.includes("scroll")) continue; // synthetic streaming, no backend needed
      if (name === "images" && !wanted.includes("images")) continue; // real backend + real model (costs cents)
      if (name === "render" && !wanted.includes("render")) continue; // synthetic panel states for visual review only (no backend needed)
      if (name === "history" && !wanted.includes("history")) continue; // needs a finished+applied `fix` session in the same repo (run `fix history`)
      if (name === "agentic" && !wanted.includes("agentic")) continue; // needs an unfixed repo and a backend with FORGE_SKILLS_DIRS
      console.log(`\n=== ${name}`);
      try { await scenarios[name](ctx); } catch (e) { check(`${name}: scenario ran without an exception`, false, e.message); await ctx.shot(`${name}-ERROR`).catch(() => {}); }
    }
    ctx.logs = readExtHostLogs(profile);
    const bad = ctx.logs.filter((l) => /forge|ForgeApi/i.test(l) && /error|exception|unhandled/i.test(l));
    check("no Forge-related errors in the extension host log", bad.length === 0, bad.slice(0, 3).join(" | "));
    browser.disconnect();
  } finally {
    try { execFileSync("taskkill", ["/T", "/F", "/PID", String(child.pid)], { stdio: "ignore" }); } catch { /* already gone */ }
    await sleep(1500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed. Screenshots: ${SHOTS}`);
  process.exit(failed.length ? 1 : 0);
}

async function workbench(browser) {
  for (let i = 0; i < 60; i++) {
    const pages = await browser.pages();
    const p = pages.find((x) => /workbench/.test(x.url()));
    if (p) return p;
    await sleep(1000);
  }
  throw new Error("workbench window not found");
}

/** The frame that holds the Forge panel's HTML (webviews are nested iframes). */
async function panelFrame(page, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    for (const f of page.frames()) {
      if (!f.url().startsWith("vscode-webview://") && !f.parentFrame()) continue;
      try { if (await f.$("#prompt")) return f; } catch { /* frame navigating */ }
    }
    await sleep(500);
  }
  throw new Error("Forge panel frame not found");
}

const text = (frame, sel) => frame.$eval(sel, (e) => e.textContent).catch(() => "");
const allText = (frame) => frame.evaluate(() => document.body.innerText);

async function openPanel(page) {
  // clicking the icon of an already-open container would TOGGLE it closed: only click when it is not the active one
  const open = await page.evaluate(() => [...document.querySelectorAll('[aria-label="Forge"]')].some((e) => e.closest(".action-item")?.classList.contains("checked") || e.classList.contains("checked")));
  if (!open) {
    const item = await page.$('[aria-label="Forge"]');
    if (item) await item.click();
    await sleep(800);
  }
  return panelFrame(page);
}

async function toast(page, contains, timeout = 90000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const found = await page.evaluate((needle) => [...document.querySelectorAll(".notifications-toasts .notification-list-item")].some((n) => n.innerText.includes(needle)), contains);
    if (found) return true;
    await sleep(500);
  }
  return false;
}

async function clickToastButton(page, label) {
  for (const b of await page.$$(".notifications-toasts .notification-list-item a.monaco-button")) {
    if ((await b.evaluate((e) => e.textContent.trim())) === label) return (await b.click(), true);
  }
  return false;
}

async function clickDialogButton(page, label, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    for (const b of await page.$$(".monaco-dialog-box .dialog-buttons a.monaco-button, .monaco-dialog-box .dialog-buttons .monaco-button")) {
      if ((await b.evaluate((e) => e.textContent.trim())) === label) return (await b.click(), true);
    }
    await sleep(300);
  }
  return false;
}

async function runPrompt(page, frame, prompt) {
  resultsBefore = await resultCards(frame);
  await frame.click("#prompt");
  await frame.type("#prompt", prompt);
  await page.keyboard.press("Enter"); // Enter submits (as a user would)
}

// A conversation keeps its earlier result cards on screen, so "a result card exists" is not enough: wait for a NEW one.
const resultCards = (frame) => frame.evaluate(() => [...document.querySelectorAll(".card h4")].filter((x) => /^(Completed|Failed|Cancelled)/.test(x.textContent)).length);
let resultsBefore = 0;
const resultTitle = (frame) => frame.evaluate(() => { const hs = [...document.querySelectorAll(".card h4")].filter((x) => /^(Completed|Failed|Cancelled)/.test(x.textContent)); return hs.length ? hs[hs.length - 1].textContent : ""; });
async function waitResult(frame, timeout = 150000) {
  await frame.waitForFunction((n) => [...document.querySelectorAll(".card h4")].filter((x) => /^(Completed|Failed|Cancelled)/.test(x.textContent)).length > n, { timeout }, resultsBefore);
  return resultTitle(frame);
}
const read = (f) => fs.readFileSync(path.join(process.argv[2], f), "utf8");

// ---- scenarios --------------------------------------------------------------------------------------------

async function panel({ page, shot }) {
  const frame = await openPanel(page);
  await sleep(1000);
  const hint = await frame.$eval("#bar .hint", (e) => e.textContent).catch(() => "");
  check("the panel shows the extension version in its footer", /v\d+\.\d+\.\d+/.test(hint), hint);
  check("the new header (New, History) and thinking selector are present", !!(await frame.$("#newbtn")) && !!(await frame.$("#histbtn")) && !!(await frame.$("#think")));
  check("panel opens from the activity bar and shows the composer", !!(await frame.$("#prompt")) && !!(await frame.$("#go")));
  check("panel starts idle with the isolation notice", /Ready/.test(await text(frame, "#status")) && /isolated copy/.test(await allText(frame)));
  await shot("01-panel-open");
}

let preFix = "";
async function fix({ page, shot }) {
  const before = read("rangesum.py");
  preFix = before;
  const frame = await openPanel(page);
  await runPrompt(page, frame, "range_sum(n) in rangesum.py gives wrong results. Find the cause, fix it, and run the tests.");
  await sleep(4000);
  check("status shows the agent working", /Starting|Working|Verifying/.test(await text(frame, "#status")), await text(frame, "#status"));
  check("button turns into Cancel while busy", (await text(frame, "#go")) === "Cancel");
  await shot("02-fix-running");
  const title = await waitResult(frame);
  await sleep(500);
  const body = await allText(frame);
  await shot("03-fix-result");
  await frame.evaluate(() => { document.getElementById("log").scrollTop = 0; });
  await sleep(400);
  await shot("03b-fix-timeline-top");
  await frame.evaluate(() => { document.getElementById("log").scrollTop = document.getElementById("log").scrollHeight; });
  check("timeline shows the agent's tool calls", /▶|edit_file|read_file/.test(body) && /edit_file/.test(body));
  check("result card says completed and verified", /Completed: verified/.test(title), title);
  check("changed files are listed", /(edited|new|deleted)\s+rangesum\.py/i.test(body));
  check("workspace file is untouched until Apply", read("rangesum.py") === before);
  check("button is back to Run when finished", (await text(frame, "#go")) === "Run");

  const diffBtn = await frame.evaluateHandle(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Raw patch"));
  await diffBtn.asElement().click();
  await sleep(2500);
  const tabs = await page.evaluate(() => [...document.querySelectorAll(".tabs-container .tab")].map((t) => t.getAttribute("aria-label") || t.textContent));
  const editorText = (await page.evaluate(() => [...document.querySelectorAll(".monaco-editor .view-line")].map((l) => l.textContent).join("\n"))).replace(/ /g, " ");
  await shot("04-diff-editor");
  check("Raw patch opens a named, highlighted diff document in the editor", tabs.some((t) => /forge-changes\.diff/.test(t)) && /return sum\(range\(1, n \+ 1\)\)/.test(editorText), JSON.stringify(tabs));
  check("the diff tab is read-only: no 'Untitled' tab and no unsaved-changes dot", !tabs.some((t) => /Untitled/.test(t)) && (await page.$$(".tabs-container .tab.dirty")).length === 0);

  const applyBtn = await frame.evaluateHandle(() => [...document.querySelectorAll("button")].find((b) => /^Apply to workspace/.test(b.textContent)));
  await applyBtn.asElement().click();
  let dialog = "";
  for (let i = 0; i < 20 && !dialog; i++) {
    await sleep(500);
    dialog = await page.evaluate(() => document.querySelector(".monaco-dialog-box")?.innerText || "");
  }
  await shot("05-apply-confirmation");
  if (!dialog) {
    const toasts = await page.evaluate(() => [...document.querySelectorAll(".notifications-toasts .notification-list-item")].map((n) => n.innerText));
    console.log("DIAG panel text tail:", (await allText(frame)).slice(-400).replace(/\n/g, " | "));
    console.log("DIAG toasts:", JSON.stringify(toasts));
  }
  check("Apply shows a modal confirmation listing the files", /Apply 1 file\(s\) to your workspace/.test(dialog) && /M rangesum\.py/.test(dialog), dialog.replace(/\n/g, " | "));
  check("nothing is written while the confirmation is open", read("rangesum.py") === before);
  check("confirming applies the change", await clickDialogButton(page, "Apply"));
  await sleep(3000);
  const after = read("rangesum.py");
  await shot("06-applied");
  check("the real file now contains the fix", /range\(1, n \+ 1\)/.test(after) && after !== before);
  check("the panel confirms what was applied", /Applied 1 file\(s\): M rangesum\.py/.test(await allText(frame)));
  const applyState = await frame.evaluate(() => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent === "Applied"); return b ? b.disabled : null; });
  check("the Apply button becomes a disabled 'Applied' button", applyState === true, String(applyState));
}

/** Layout/visual review: feeds the panel hand-made messages. These are FIXTURES to look at, never presented as real runs. */
async function render({ page, shot }) {
  const frame = await openPanel(page);
  const tag = process.env.FORGE_UI_THEME ? process.env.FORGE_UI_THEME.replace(/\W+/g, "-").toLowerCase() : "theme-default";
  const post = (m) => frame.evaluate((x) => window.postMessage(x, "*"), m);
  const diffLines = (rows) => rows.map(([kind, no, text]) => ({ kind, oldNo: kind === "add" ? undefined : no, newNo: kind === "del" ? undefined : no, text }));
  const result = {
    taskId: "fixture", title: "fixture", applyState: null, status: "completed", outcome: "unverified", verified: false, error: null,
    summary: "I changed `calc.py` and added a README. Tests were not available.", stats: "5 model calls · 11 tool calls · 12k/1.1k tokens · ~$0.0042", limitations: ["No test command was detected."], falseClaim: false, workspacePath: null,
    activity: { read: ["calc.py"], changed: ["calc.py", "README.md", "old.py", "logo.png"], commands: [{ command: "python -m py_compile calc.py", exit: 0 }], approved: 0, rejected: 0, refused: 0, failures: 0 },
    files: [
      { path: "src/calc.py", status: "M", additions: 3, deletions: 2 }, { path: "README.md", status: "A", additions: 20, deletions: 0 },
      { path: "old.py", status: "D", additions: 0, deletions: 4 }, { path: "assets/logo.png", status: "M", additions: 0, deletions: 0 },
    ],
    checks: [
      { phase: "final", kind: "syntax", command: "python -m py_compile src/calc.py", status: "passed", exitCode: 0, note: null },
      { phase: "final", kind: "agent_test", command: "python -m pytest -q", status: "passed", exitCode: 0, note: null },
      { phase: "final", kind: "lint", command: "python -m ruff check .", status: "failed", exitCode: 1, note: null },
      { phase: "final", kind: "test", command: "tests", status: "unavailable", exitCode: null, note: "No test command detected and no verification command supplied" },
      { phase: "baseline", kind: "test", command: "python -m pytest -q", status: "failed", exitCode: 1, note: null },
    ],
    diffs: [
      { path: "src/calc.py", status: "M", additions: 3, deletions: 2, binary: false, truncated: false, hunks: [
        { header: "@@ -10,6 +10,7 @@", lines: diffLines([["ctx", 10, "def add(a, b):"], ["del", 11, "    return a - b"], ["add", 11, "    return a + b"], ["ctx", 12, ""], ["ctx", 13, "def mul(a, b):"], ["del", 14, "    return a + b"], ["add", 14, "    # multiply, not add: the original code summed the two numbers instead"], ["add", 15, "    return a * b"]]) },
        { header: "@@ -40,3 +41,3 @@", lines: diffLines([["ctx", 40, "def main():"], ["ctx", 41, "    print(add(2, 3))"]]) },
      ] },
      { path: "README.md", status: "A", additions: 20, deletions: 0, binary: false, truncated: false, hunks: [{ header: "@@ -0,0 +1,20 @@", lines: diffLines(Array.from({ length: 20 }, (_, i) => ["add", i + 1, i === 0 ? "# Calculator" : "Line " + i + " of a new file that is long enough to wrap around in a narrow side panel."])) }] },
      { path: "old.py", status: "D", additions: 0, deletions: 4, binary: false, truncated: false, hunks: [{ header: "@@ -1,4 +0,0 @@", lines: diffLines([["del", 1, "# legacy"], ["del", 2, "def old():"], ["del", 3, "    pass"], ["del", 4, ""]]) }] },
      { path: "assets/logo.png", status: "M", additions: 0, deletions: 0, binary: true, truncated: false, hunks: [], sizeBefore: 11840, sizeAfter: 14096 },
    ],
  };
  await post({ type: "reset", prompt: "Fixture: make multiply work and add a README (visual review only)" });
  await post({ type: "status", text: "Working…", busy: true });
  await post({ type: "stats", text: "step 3/30 · 5 tool calls (1 failed, 0 refused) · 12k in / 1.1k out · context 3.6k · ~$0.0042 · 25s", stats: {} });
  await post({ type: "item", item: { kind: "info", text: "Isolated workspace ready (docker)" } });
  await post({ type: "item", item: { kind: "tool", text: "read_file path=src/calc.py", file: "src/calc.py" } });
  await post({ type: "item", item: { kind: "tool", text: "edit_file path=src/calc.py", file: "src/calc.py", diff: { path: "src/calc.py", old: "    return a + b", new: "    return a * b" } } });
  await post({ type: "item", item: { kind: "tool", text: "run_tests command=python -m pytest -q" } });
  await post({ type: "item", item: { kind: "error", text: "exit 1 in 0.9s", detail: "FAILED tests/test_calc.py::test_mul - assert 5 == 6" } });
  await post({ type: "item", item: { kind: "agent", text: "The test fails because `mul` adds. I changed it in `src/calc.py`." } });
  await post({ type: "item", item: { kind: "agent", text: ["## Summary", "**Fixed:** `mul` now multiplies.", "", "- *one* change", "- a <b>literal</b> tag", "", "```py", "return a * b", "```"].join(String.fromCharCode(10)) } });
  await new Promise((r) => setTimeout(r, 300));
  const md = await frame.evaluate(() => { const e = [...document.querySelectorAll(".say.md")].pop(); return { text: e.textContent, strong: !!e.querySelector("strong"), li: e.querySelectorAll("li").length, pre: !!e.querySelector("pre.mdcode"), b: !!e.querySelector("b") }; });
  if (md.text.includes("**") || md.text.includes("##") || !md.strong || md.li !== 2 || !md.pre || md.b) throw new Error("markdown not rendered: " + JSON.stringify(md));
  await post({ type: "notice", kind: "warn", text: "Fixture notice: a long path C:\\Users\\someone\\AppData\\Local\\Temp\\a-very-long-folder-name\\another-long-folder\\file.py wraps instead of scrolling." });
  await post({ type: "approval", approval: { id: "fx1", tool: "run_command", reason: "Arbitrary python execution requires approval", arguments: { command: "python -c print(6*7)", workdir: "." } } });
  await post({ type: "approval", approval: { id: "fx2", tool: "edit_file", reason: "Editing files outside the usual project folders needs approval", arguments: { path: "scripts/deploy.sh", old_text: "rm -rf build", new_text: "rm -rf build dist", replace_all: false } } });
  await post({ type: "approval", approval: { id: "fx3", tool: "delete_file", reason: "Destructive file operations require approval", arguments: { path: "old_notes.txt" } } });
  await post({ type: "approval", approval: { id: "fx4", tool: "run_command", reason: "Arbitrary python execution requires approval", arguments: { command: "python -c \"import os\nfor n in os.listdir('.'):\n    print(n)\" --verbose --some-very-long-flag=value-that-keeps-going-and-going", workdir: "subdir", timeout_seconds: 120 } } });
  await post({ type: "approvalResolved", id: "fx1", approved: true });
  await sleep(600);
  const ap = await frame.evaluate(() => ({ cards: document.querySelectorAll(".card.approval").length, resolved: document.querySelectorAll(".card.approval.resolved").length, cmds: [...document.querySelectorAll("pre.cmd")].map((p) => p.innerText.slice(0, 60)), overflow: [...document.querySelectorAll(".card.approval pre.cmd")].some((p) => p.scrollWidth > p.clientWidth + 1), titles: [...document.querySelectorAll(".card.approval h4")].map((h) => h.textContent), buttons: [...document.querySelectorAll(".card.approval:not(.resolved) button")].map((b) => b.textContent).join("|") }));
  check("approval cards say in words what Forge wants to do", ap.titles.join("|") === "Run this command?|Edit this file?|Delete this file?|Run this command?", JSON.stringify(ap.titles));
  check("a command is shown as a terminal line and never clipped", ap.cmds.length >= 2 && ap.cmds[0].startsWith("$ ") && ap.overflow === false, JSON.stringify(ap.cmds));
  check("a decided approval collapses to a result chip, the others keep Approve once / Reject / Copy", ap.resolved === 1 && /Approve once\|Reject/.test(ap.buttons), JSON.stringify({ r: ap.resolved, b: ap.buttons }));
  await frame.evaluate(() => { const l = document.getElementById("log"); const c = document.querySelector(".card.approval"); if (c) l.scrollTop = c.offsetTop - 10; });
  await sleep(300);
  await shot(`34-render-approvals-${tag}`);
  await frame.evaluate(() => { document.getElementById("log").scrollTop = 0; });
  await sleep(300);
  await shot(`30-render-timeline-${tag}`);
  await post({ type: "status", text: "Done: NOT verified", busy: false });
  await post({ type: "result", result });
  await sleep(600);
  await frame.evaluate(() => { document.getElementById("log").scrollTop = 1e6; });
  await sleep(300);
  await shot(`31-render-result-${tag}`);
  await frame.evaluate(() => { const l = document.getElementById("log"); const c = document.querySelector(".card:last-of-type"); if (c) l.scrollTop = c.offsetTop; });
  await sleep(300);
  await shot(`32-render-result-top-${tag}`);
  // open every file diff to inspect them
  await frame.evaluate(() => document.querySelectorAll("details.filediff").forEach((d) => (d.open = true)));
  await sleep(300);
  await frame.evaluate(() => { const c = document.querySelector("details.filediff"); if (c) document.getElementById("log").scrollTop = c.getBoundingClientRect().top + document.getElementById("log").scrollTop - 60; });
  await sleep(300);
  await shot(`33-render-diffs-${tag}`);
  const info = await frame.evaluate(() => ({
    binary: document.querySelector(".bininfo")?.innerText || "",
    sideBtns: [...document.querySelectorAll("details.filediff")].map((d) => ({ path: d.querySelector(".fpath")?.textContent, side: !![...d.querySelectorAll("button")].find((b) => b.textContent === "Side-by-side") })),
    overflowX: document.getElementById("log").scrollWidth > document.getElementById("log").clientWidth + 1,
    stepOpen: [...document.querySelectorAll("details.step")].map((d) => d.open),
    words: document.querySelectorAll(".dl .tx .w").length,
  }));
  check("a binary file shows its type and how its size changed", /Changed image file · 11\.6 KB → 13\.8 KB \(\+2\.2 KB\)/.test(info.binary), info.binary);
  check("a binary file has no Side-by-side button, text files do", info.sideBtns.find((f) => /logo\.png/.test(f.path))?.side === false && info.sideBtns.find((f) => /calc\.py/.test(f.path))?.side === true, JSON.stringify(info.sideBtns));
  check("long paths and lines never make the panel scroll sideways", info.overflowX === false);
  check("changed words are highlighted inside edited lines", info.words >= 2, String(info.words));
  check("the failed step opens by itself, the clean ones stay closed", info.stepOpen.some(Boolean), JSON.stringify(info.stepOpen));
}

/** One conversation over several messages: it remembers, it is not cleared, and "Restore to here" makes it forget. */
async function session({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(600);
  const cards = () => frame.evaluate(() => [...document.querySelectorAll(".card h4")].filter((x) => /^(Completed|Failed|Cancelled)/.test(x.textContent)).length);
  const ask = async (text) => {
    const before = await cards();
    await runPrompt(page, frame, text);
    await frame.waitForFunction((k) => [...document.querySelectorAll(".card h4")].filter((x) => /^(Completed|Failed|Cancelled)/.test(x.textContent)).length > k, { timeout: 240000 }, before);
    await sleep(600);
  };
  const lastSaid = () => frame.evaluate(() => { const s = [...document.querySelectorAll(".say")].filter((e) => !e.closest("details")); return s.length ? s[s.length - 1].innerText : ""; });
  const bar = () => frame.evaluate(() => (document.getElementById("sess").classList.contains("on") ? document.getElementById("sess").innerText.replace(/\n/g, " ") : ""));

  await ask("Remember this codeword: BLUEBIRD. Do not create or change any files. Reply with just: noted");
  check("the first message opens a conversation with a title bar", /1 message/.test(await bar()), await bar());
  check("the box now says replies continue the conversation", /remembers/.test(await frame.$eval("#prompt", (e) => e.placeholder)));
  await ask("What was the codeword I asked you to remember? Reply with just the word. Do not change any files.");
  const second = await lastSaid();
  check("the second message is answered from the first (the agent remembers)", /BLUEBIRD/i.test(second), second);
  const counts = await frame.evaluate(() => ({ users: document.querySelectorAll(".user").length, results: [...document.querySelectorAll(".card h4")].filter((x) => /^Completed|^Failed/.test(x.textContent)).length }));
  check("the panel was not cleared: both messages and both results are on screen", counts.users === 2 && counts.results === 2, JSON.stringify(counts));
  check("the title bar counts the messages", /2 messages/.test(await bar()), await bar());
  await ask("Also remember a second codeword: ORCHID. Reply with just: noted");
  await shot("70-conversation-three-messages");
  const bars = await frame.evaluate(() => [...document.querySelectorAll(".restorebar")].map((b) => b.style.display !== "none"));
  check("every response except the newest offers 'Restore to here'", bars.length === 3 && bars[0] && bars[1] && !bars[2], JSON.stringify(bars));

  // go back to the first response: the later messages are set aside
  const restore = await frame.evaluateHandle(() => [...document.querySelectorAll(".restorebar button")][0]);
  await restore.asElement().click();
  let dialog = "";
  for (let i = 0; i < 20 && !dialog; i++) {
    await sleep(500);
    dialog = await page.evaluate(() => document.querySelector(".monaco-dialog-box")?.innerText || "");
  }
  await shot("71-restore-confirmation");
  check("'Restore to here' asks first and says what will be forgotten", /Go back to response 1/.test(dialog) && /2 later message/.test(dialog), dialog.replace(/\n/g, " | "));
  check("confirming goes back", await clickDialogButton(page, "Go back"));
  await frame.waitForFunction(() => document.querySelectorAll(".user.discarded").length === 2, { timeout: 30000 });
  await sleep(500);
  await shot("72-after-restore");
  check("the later messages are shown dimmed as rolled back", (await frame.evaluate(() => document.querySelectorAll(".user.discarded").length)) === 2);
  check("the title bar now counts one message", /1 message(?!s)/.test(await bar()), await bar());

  await ask("List every codeword I have given you, separated by commas. Do not change any files.");
  const after = await lastSaid();
  await shot("73-forgot-after-restore");
  check("after going back the agent knows the first codeword but has forgotten the second", /BLUEBIRD/i.test(after) && !/ORCHID/i.test(after), after);

  // the conversation can be found and continued from History
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(800);
  check("a new conversation starts empty (title bar gone)", (await bar()) === "");
  await frame.evaluate(() => document.getElementById("histbtn").click());
  await sleep(1000);
  const rows = await frame.evaluate(() => [...document.querySelectorAll("#hist .sess")].map((e) => e.innerText.replace(/\n/g, " | ")));
  check("History lists the conversation with its message count", rows.some((r) => /BLUEBIRD/.test(r) && /\d+ messages?/.test(r)), JSON.stringify(rows));
  await frame.evaluate(() => [...document.querySelectorAll("#hist .sess")].find((e) => /BLUEBIRD/.test(e.innerText)).click());
  await frame.waitForFunction(() => document.getElementById("sess").classList.contains("on"), { timeout: 30000 });
  await sleep(1500);
  await ask("What is the first codeword again? Reply with just the word. Do not change any files.");
  check("an opened conversation can be continued (the agent still remembers)", /BLUEBIRD/i.test(await lastSaid()), await lastSaid());
}

/** A solid-colour PNG written without any image library. */
function writePng(file, w, h, [r, g, b]) {
  const zlib = require("zlib");
  const row = Buffer.alloc(1 + w * 3);
  for (let x = 0; x < w; x++) { row[1 + x * 3] = r; row[2 + x * 3] = g; row[3 + x * 3] = b; }
  const raw = Buffer.concat(Array.from({ length: h }, () => row));
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(Buffer.concat([Buffer.from(type), data])) >>> 0); return Buffer.concat([len, Buffer.from(type), data, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
}
const powershell = (script) => { const r = require("child_process").spawnSync("powershell", ["-STA", "-NoProfile", "-NonInteractive", "-Command", script], { encoding: "utf8", timeout: 60000 }); return (r.stdout || "") + (r.stderr ? " [stderr: " + r.stderr.trim().slice(0, 400) + "]" : ""); };

/** A physical Ctrl+V with a real image on the Windows clipboard, and with plain text (which must still paste as text). */
async function clipboard({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(500);
  const img = path.join(os.tmpdir(), "forge-clip-test.png");
  writePng(img, 120, 80, [200, 30, 30]);
  powershell(`Add-Type -AssemblyName System.Windows.Forms,System.Drawing; $i=[System.Drawing.Image]::FromFile('${img}'); [System.Windows.Forms.Clipboard]::SetImage($i); $i.Dispose()`);
  await frame.click("#prompt");
  await page.keyboard.down("Control"); await page.keyboard.press("KeyV"); await page.keyboard.up("Control");
  await sleep(1500);
  const st = await frame.evaluate(() => { const im = document.querySelector("#tray .thumb img"); return { thumbs: document.querySelectorAll("#tray .thumb").length, w: im ? im.naturalWidth : 0, h: im ? im.naturalHeight : 0, text: document.getElementById("prompt").value }; });
  await shot("60-real-ctrl-v-image");
  check("a real Ctrl+V with an image on the Windows clipboard attaches it", st.thumbs === 1 && st.w === 120 && st.h === 80, JSON.stringify(st));
  check("the pasted image did not leave junk text in the prompt box", st.text === "", JSON.stringify(st.text));
  // plain text must keep working as text
  powershell(`Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetText('hello from the clipboard')`);
  await frame.click("#prompt");
  await page.keyboard.down("Control"); await page.keyboard.press("KeyV"); await page.keyboard.up("Control");
  await sleep(800);
  const st2 = await frame.evaluate(() => ({ thumbs: document.querySelectorAll("#tray .thumb").length, text: document.getElementById("prompt").value }));
  check("a real Ctrl+V with plain text still pastes text and adds no image", st2.thumbs === 1 && /hello from the clipboard/.test(st2.text), JSON.stringify(st2));
  await frame.evaluate(() => { document.getElementById("prompt").value = ""; document.querySelectorAll("#tray .thumb .x").forEach((b) => b.click()); });
}

/** The "Image" button: the extension opens the native Windows file dialog; a script types a path and presses Enter. */
async function pickdialog({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(500);
  const file = path.join(os.tmpdir(), "forge-dialog-test.png");
  writePng(file, 90, 60, [30, 90, 200]);
  await frame.click("#attach");
  // The native dialog is driven with window messages (test/ui/native-dialog.ps1): no keystrokes, so it does not
  // matter which window currently has the keyboard focus.
  const r = require("child_process").spawnSync("powershell", ["-STA", "-NoProfile", "-NonInteractive", "-File", path.join(__dirname, "native-dialog.ps1"), "-File", file, "-OwnerLike", "[[]Extension Development Host*"], { encoding: "utf8", timeout: 60000 });
  const out = ((r.stdout || "").trim() + (r.stderr ? " [stderr: " + r.stderr.trim().slice(0, 300) + "]" : "")).trim();
  check("the native file dialog titled 'Attach images to Forge' opened", out === "dialog-driven", out);
  await sleep(2500);
  const st = await frame.evaluate(() => { const im = document.querySelector("#tray .thumb img"); return { thumbs: document.querySelectorAll("#tray .thumb").length, alt: im ? im.alt : "", w: im ? im.naturalWidth : 0, h: im ? im.naturalHeight : 0 }; });
  await shot("61-native-dialog-result");
  check("the file chosen in the native dialog is attached with its real size", st.thumbs === 1 && st.alt === "forge-dialog-test.png" && st.w === 90 && st.h === 60, JSON.stringify(st));
  await frame.evaluate(() => document.querySelectorAll("#tray .thumb .x").forEach((b) => b.click()));
}

/** A backend that predates the per-file view (no /file route): Review changes must explain and still show the patch. */
async function oldbackend({ page, shot }) {
  const http = require("http");
  const task = { id: "old1", title: "fix", instructions: "Fix add()", repo_path: process.argv[2], status: "completed", outcome: "verified", error: null, iterations: 2, max_iterations: 30, tool_calls: 2, tool_failures: 0, policy_denials: 0, input_tokens: 10, output_tokens: 5, estimated_cost_usd: 0.001, duration_seconds: 3, workspace_path: null, apply_state: null, images: [], model: "m", created_at: "2026-01-01T00:00:00Z", report: { agent: { final_message: "done" }, known_limitations: [] } };
  const patch = "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-a - b\n+a + b\n";
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, "http://x").pathname;
    const send = (code, body) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); };
    if (u === "/api/health") return send(200, { status: "ok", version: "0.0-old", execution_mode: "docker", sandbox: { available: true, detail: "" }, provider: { name: "openai", model: "m", configured: true, problem: null }, spend: null });
    if (u === "/api/config") return send(200, { allowed_repo_roots: [], approval_policy: "sensitive" });
    if (u === "/api/tasks") return send(200, { items: [task], total: 1, limit: 50, offset: 0 });
    if (u === "/api/sessions") return send(200, { items: [{ id: "s0ld", title: "fix", repo_path: process.argv[2], archived: false, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", turns: 1, last_status: "completed", last_outcome: "verified", last_prompt: "Fix add()" }], total: 1, limit: 50, offset: 0 });
    if (u === "/api/sessions/s0ld") return send(200, { id: "s0ld", title: "fix", turns: 1, items: [{ ...task, turn: 1, discarded: false, has_checkpoint: false }] });
    if (u === "/api/tasks/old1") return send(200, task);
    if (u === "/api/tasks/old1/events") return send(200, [{ id: 1, task_id: "old1", ts: "2026-01-01T00:00:00Z", type: "workspace.created", data: { execution_mode: "docker" } }]);
    if (u === "/api/tasks/old1/diff") return send(200, { diff: patch, files: [{ path: "x.py", status: "M", additions: 1, deletions: 1 }], live: false });
    if (u === "/api/tasks/old1/verifications") return send(200, []);
    return send(404, { detail: "Not Found" }); // exactly what an older Forge answers for the /file route
  });
  await new Promise((r) => server.listen(8012, "127.0.0.1", r));
  try {
    const frame = await openPanel(page);
    await frame.evaluate(() => document.getElementById("histbtn").click());
    await sleep(1500);
    await frame.evaluate(() => document.querySelector("#hist .sess").click());
    await frame.waitForFunction(() => [...document.querySelectorAll(".card h4")].some((x) => /^Completed/.test(x.textContent)), { timeout: 30000 });
    const rb = await frame.evaluateHandle(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Review changes"));
    await rb.asElement().click();
    await sleep(3000);
    await shot("50-old-backend-review");
    const text = await allText(frame);
    const tabs = await page.evaluate(() => [...document.querySelectorAll(".tabs-container .tab")].map((t) => t.getAttribute("aria-label") || t.textContent));
    check("with an older backend, Review explains why instead of 'HTTP 404'", /older than this extension/.test(text) && !/HTTP 404/.test(text), text.slice(-260).replace(/\n/g, " | "));
    check("and the raw patch is shown so the change can still be read", tabs.some((t) => /forge-changes\.diff/.test(t)), JSON.stringify(tabs));
  } finally {
    server.close();
  }
}

/** The reader must be able to scroll through the whole conversation while it is still running. */
async function scroll({ page, shot }) {
  const frame = await openPanel(page);
  const post = (m) => frame.evaluate((x) => window.postMessage(x, "*"), m);
  const metrics = () => frame.evaluate(() => { const l = document.getElementById("log"); return { top: Math.round(l.scrollTop), max: l.scrollHeight - l.clientHeight, latest: document.getElementById("latest").classList.contains("on") }; });
  await post({ type: "reset", prompt: "Fixture: a long running conversation (visual/behaviour review only)" });
  await post({ type: "status", text: "Working…", busy: true });
  for (let i = 0; i < 40; i++) await post({ type: "item", item: { kind: i % 3 === 0 ? "agent" : "info", text: "Fixture event " + (i + 1) + " " + "lorem ipsum ".repeat(4) } });
  await sleep(500);
  let m = await metrics();
  check("while at the bottom the view follows new output", m.max > 200 && m.top >= m.max - 4, JSON.stringify(m));
  // the reader scrolls up with the mouse wheel, like a person
  const box = await frame.evaluate(() => { const r = document.getElementById("log").getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
  const frameOffset = await page.evaluate(() => { const f = [...document.querySelectorAll("iframe")].find((i) => i.src.includes("vscode-webview")) || document.querySelector("iframe"); const r = f ? f.getBoundingClientRect() : { x: 0, y: 0 }; return { x: r.x, y: r.y }; });
  await page.mouse.move(frameOffset.x + box.x, frameOffset.y + box.y);
  await page.mouse.wheel({ deltaY: -4000 });
  await sleep(500);
  m = await metrics();
  check("a mouse-wheel scroll up works while the task is running", m.top < m.max - 300, JSON.stringify(m));
  const before = m.top;
  for (let i = 0; i < 10; i++) await post({ type: "item", item: { kind: "info", text: "Fixture event after scrolling up " + (i + 1) } });
  await sleep(700);
  m = await metrics();
  check("new output does not yank the reader back to the bottom", Math.abs(m.top - before) < 5, JSON.stringify({ before, m }));
  check("a 'Latest' button offers the way back", m.latest === true);
  await shot("40-scrolled-up");
  await frame.evaluate(() => document.getElementById("latest").click());
  await sleep(500);
  m = await metrics();
  check("Latest returns to the bottom and following resumes", m.top >= m.max - 4 && m.latest === false, JSON.stringify(m));
  await page.mouse.wheel({ deltaY: -600 });
  await sleep(300);
  await page.mouse.wheel({ deltaY: 600 });
  await sleep(300);
  m = await metrics();
  check("scrolling back down by wheel re-attaches to the bottom", m.top >= m.max - 60, JSON.stringify(m));
  await post({ type: "status", text: "Ready", busy: false }); // leave the panel idle for whatever runs next
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(400);
}

/** Pictures: paste, drop, remove, send, and a real model reads one. */
async function images({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(500);
  const makePng = (text) => frame.evaluate((t) => new Promise((resolve) => { const c = document.createElement("canvas"); c.width = 320; c.height = 180; const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, 320, 180); g.fillStyle = "#000"; g.font = "bold 120px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(t, 160, 95); resolve(c.toDataURL("image/png")); }), text);
  const pasteDataUrl = (dataUrl, name) => frame.evaluate(async (u, n) => { const mk = (u, n) => { const bin = atob(u.split(",")[1]); const arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); return new File([arr], n, { type: "image/png" }); }; const dt = new DataTransfer(); dt.items.add(mk(u, n)); document.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true })); }, dataUrl, name);
  await frame.click("#prompt");
  await pasteDataUrl(await makePng("42"), "answer.png");
  await sleep(700);
  let st = await frame.evaluate(() => ({ thumbs: document.querySelectorAll("#tray .thumb").length, tray: document.getElementById("tray").classList.contains("on"), note: document.getElementById("imgnote").classList.contains("on") }));
  check("pasting an image (Ctrl+V) puts it in the tray", st.thumbs === 1 && st.tray && st.note, JSON.stringify(st));
  // drop a second one
  await frame.evaluate(async (u) => { const mk = (u, n) => { const bin = atob(u.split(",")[1]); const arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); return new File([arr], n, { type: "image/png" }); }; const dt = new DataTransfer(); dt.items.add(mk(u, "second.png")); document.getElementById("box").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true })); }, await makePng("7"));
  await sleep(700);
  st = await frame.evaluate(() => document.querySelectorAll("#tray .thumb").length);
  check("dropping a file on the composer attaches it too", st === 2, String(st));
  // not an image: refused with a message, nothing added
  await frame.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(["hello"], "notes.txt", { type: "text/plain" })); document.getElementById("box").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true })); });
  await sleep(500);
  const notimg = await frame.evaluate(() => ({ thumbs: document.querySelectorAll("#tray .thumb").length, said: /notes\.txt is not a supported image/.test(document.body.innerText) }));
  check("a file that is not an image is refused with a reason", notimg.thumbs === 2 && notimg.said, JSON.stringify(notimg));
  await shot("41-images-tray");
  await frame.evaluate(() => document.querySelectorAll("#tray .thumb .x")[1].click());
  await sleep(300);
  st = await frame.evaluate(() => [...document.querySelectorAll("#tray .thumb img")].map((i) => i.alt));
  check("an attached image can be removed again", st.length === 1 && st[0] === "answer.png", JSON.stringify(st));
  await runPrompt(page, frame, "The attached image shows a number. Tell me which number in your final message. Do not edit or create any files.");
  await sleep(1500);
  const bubble = await frame.evaluate(() => ({ pics: document.querySelectorAll(".user .pics img").length, trayLeft: document.querySelectorAll("#tray .thumb").length }));
  check("the sent message shows the picture and the tray is cleared", bubble.pics === 1 && bubble.trayLeft === 0, JSON.stringify(bubble));
  await waitResult(frame, 180000);
  await sleep(500);
  const body = await allText(frame);
  await shot("42-images-result");
  check("the real model read the number from the image (42)", /number in the image is\W*42/i.test(body), body.slice(-300).replace(/\n/g, " | "));
  // history replay brings the picture back
  await frame.evaluate(() => document.getElementById("histbtn").click());
  await sleep(1000);
  await frame.evaluate(() => document.querySelector("#hist .sess").click());
  await sleep(2500);
  const replay = await frame.evaluate(() => document.querySelectorAll(".user .pics img").length);
  check("opening the session from History shows its picture again", replay === 1, String(replay));
  await shot("43-images-replayed");
}

async function history({ page, shot }) {
  const frame = await openPanel(page);
  // a fresh view, then the history list
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(1500);
  await shot("20-new-view-recent");
  const recent = await frame.evaluate(() => [...document.querySelectorAll(".welcome .sess .t")].map((e) => e.textContent));
  check("the empty view lists recent sessions of this workspace", recent.some((t) => /range_sum/.test(t)), JSON.stringify(recent));
  await frame.evaluate(() => document.getElementById("histbtn").click());
  await sleep(1000);
  const rows = await frame.evaluate(() => [...document.querySelectorAll("#hist .sess")].map((e) => e.innerText.replace(/\n/g, " | ")));
  await shot("21-history");
  check("History lists the conversation with its message count and verdict", rows.some((r) => /range_sum/.test(r) && /1 message/.test(r) && /verified/.test(r)), JSON.stringify(rows));
  await frame.evaluate(() => document.querySelector("#hist .sess").click());
  await frame.waitForFunction(() => [...document.querySelectorAll(".card h4")].some((x) => /^Completed/.test(x.textContent)), { timeout: 30000 });
  await sleep(800);
  await shot("22-opened-session");
  const info = await frame.evaluate(() => ({
    user: document.querySelector(".user")?.textContent || "",
    tools: document.querySelectorAll("details.step").length,
    file: document.querySelector("details.filediff .fpath")?.textContent || "",
    gutters: document.querySelectorAll("details.filediff .dl .ln").length,
    adds: document.querySelectorAll("details.filediff .dl.add").length,
    dels: document.querySelectorAll("details.filediff .dl.del").length,
    open: document.querySelector("details.filediff")?.open === true,
    checks: [...document.querySelectorAll(".check")].map((c) => c.innerText.replace(/\n/g, " ")),
    bar: document.getElementById("sess").classList.contains("on") ? document.getElementById("sess").innerText.replace(/\n/g, " ") : "",
    apply: [...document.querySelectorAll("button")].find((b) => /^Applied$|^Apply/.test(b.textContent))?.textContent,
    revertShown: ([...document.querySelectorAll("button")].find((b) => /^Revert applied/.test(b.textContent)) || {}).style?.display !== "none",
  }));
  check("opening a session restores the original request and the timeline", /range_sum/.test(info.user) && info.tools > 0, JSON.stringify({ user: info.user.slice(0, 60), tools: info.tools }));
  check("the file diff is shown inline, open, with line numbers and +/- rows", /rangesum\.py/.test(info.file) && info.open && info.gutters > 0 && info.adds > 0 && info.dels > 0, JSON.stringify(info));
  check("the independent checks are listed in the result", info.checks.some((c) => /tests/i.test(c) && /passed/i.test(c)) && info.checks.some((c) => /syntax/i.test(c)), JSON.stringify(info.checks));
  check("an opened conversation shows its title bar and 'Applied' with a Revert button", /1 message/.test(info.bar) && info.apply === "Applied" && info.revertShown, `${info.bar} ${info.apply} ${info.revertShown}`);
  // the native multi-file review
  const review = await frame.evaluateHandle(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Review changes"));
  await review.asElement().click();
  await sleep(3500);
  const tabs2 = await page.evaluate(() => [...document.querySelectorAll(".tabs-container .tab")].map((t) => t.getAttribute("aria-label") || t.textContent));
  await shot("22b-review-changes");
  check("Review changes opens VS Code's multi-file diff with the before/after of the file", tabs2.some((t) => /Forge changes: 1 file/.test(t)), JSON.stringify(tabs2));
  // roll it back
  const rev = await frame.evaluateHandle(() => [...document.querySelectorAll("button")].find((b) => /^Revert applied/.test(b.textContent)));
  await rev.asElement().click();
  let dialog = "";
  for (let i = 0; i < 20 && !dialog; i++) {
    await sleep(500);
    dialog = await page.evaluate(() => document.querySelector(".monaco-dialog-box")?.innerText || "");
  }
  await shot("23-revert-confirmation");
  check("Revert shows a modal confirmation listing the files", /Revert 1 file\(s\)/.test(dialog) && /rangesum\.py/.test(dialog), dialog.replace(/\n/g, " | "));
  check("nothing is written while the confirmation is open", read("rangesum.py") !== preFix);
  check("confirming reverts", await clickDialogButton(page, "Revert"));
  await sleep(3000);
  await shot("24-reverted");
  check("the real file is exactly as before the task", read("rangesum.py") === preFix);
  const st = await frame.evaluate(() => ({ apply: [...document.querySelectorAll("button")].find((b) => /^Apply/.test(b.textContent))?.textContent, chips: [...document.querySelectorAll(".card .actions .chip")].map((c) => c.textContent) }));
  check("the panel offers 'Apply again' and shows a reverted chip", /Apply again/.test(st.apply || "") && st.chips.includes("reverted"), JSON.stringify(st));
  await frame.evaluate(() => document.getElementById("histbtn").click());
  await sleep(1200);
  const rows2 = await frame.evaluate(() => [...document.querySelectorAll("#hist .sess")].map((e) => e.innerText.replace(/\n/g, " | ")));
  check("History still lists the conversation after the revert", rows2.some((r) => /range_sum/.test(r)), JSON.stringify(rows2));
}

async function approval({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(500);
  const prompt = 'Run exactly this command with run_command: python -c "print(6*7)" . Then tell me what it printed.';
  await runPrompt(page, frame, prompt);
  const gotToast = await toast(page, "Forge wants to run");
  await shot("07-approval");
  const card = await frame.$(".card.approval");
  check("an approval notification appears in VS Code", gotToast);
  check("the panel shows an approval card with the exact command", !!card && /python -c/.test(await allText(frame)) && /run_command/.test(await allText(frame)));
  const clipped = await frame.evaluate(() => [...document.querySelectorAll(".card.approval pre.cmd")].some((p) => p.scrollWidth > p.clientWidth + 1));
  check("the approval card shows the whole command (nothing clipped or hidden behind a scrollbar)", clipped === false);
  check("the task is waiting (status says so)", /approval/i.test(await text(frame, "#status")), await text(frame, "#status"));
  check("rejecting from the notification works", await clickToastButton(page, "Reject"));
  await frame.waitForFunction(() => /Rejected/.test(document.body.innerText), { timeout: 30000 }).catch(() => {});
  const title = await waitResult(frame);
  await shot("08-approval-rejected");
  check("the panel card shows it was rejected", /Rejected/.test(await allText(frame)));
  check("the command did not run and the task still ended cleanly", /Completed|Failed/.test(title), title);

  // second time: approve from the panel button
  await frame.evaluate(() => document.getElementById("newbtn").click()); // a separate conversation: this one must not remember the rejection
  await sleep(500);
  await runPrompt(page, frame, prompt);
  await frame.waitForFunction(() => [...document.querySelectorAll(".card.approval:not(.resolved) button")].some((b) => b.textContent === "Approve once"), { timeout: 90000 });
  await shot("09-approval-2");
  const approveBtn = await frame.evaluateHandle(() => [...document.querySelectorAll(".card.approval button")].find((b) => b.textContent === "Approve once"));
  await approveBtn.asElement().click();
  await sleep(1500);
  const stale = await page.evaluate(() => [...document.querySelectorAll(".notifications-toasts .notification-list-item")].filter((n) => n.innerText.includes("Forge wants to run")).length);
  check("answering in the panel removes the now-stale approval notification", stale === 0, `${stale} still visible`);
  await waitResult(frame);
  const body = await allText(frame);
  await shot("10-approval-approved");
  check("approving from the panel runs the command (output 42 is shown)", /Approved once/.test(body) && /42/.test(body));
}

async function cancel({ page, shot }) {
  const frame = await openPanel(page);
  await frame.evaluate(() => document.getElementById("newbtn").click());
  await sleep(500);
  await runPrompt(page, frame, 'Run exactly this command with run_command: python -c "print(1)" . Then stop.');
  await frame.waitForFunction(() => [...document.querySelectorAll(".card.approval:not(.resolved) button")].some((b) => b.textContent === "Approve once"), { timeout: 90000 });
  check("a task is blocked waiting for approval", true);
  await frame.click("#go"); // the button reads Cancel while busy
  await sleep(2500);
  const title = await waitResult(frame, 60000);
  await shot("11-cancelled");
  check("Cancel ends the task as cancelled", /Cancelled/.test(title), title);
  check("status line says cancelled and the button is Run again", /Cancelled/.test(await text(frame, "#status")) && (await text(frame, "#go")) === "Run");
}

async function doctor({ page, shot }) {
  await page.keyboard.press("F1");
  await sleep(800);
  await page.keyboard.type("Forge: Check Backend");
  await sleep(800);
  await shot("12-command-palette");
  await page.keyboard.press("Enter");
  const ok = await toast(page, "Forge 0.1.0", 20000);
  await shot("13-doctor");
  const t = await page.evaluate(() => [...document.querySelectorAll(".notifications-toasts .notification-list-item")].map((n) => n.innerText).join(" | "));
  check("Forge: Check Backend reports model, sandbox and spend", ok && /gpt-6-luna/.test(t) && /sandbox/.test(t), t.replace(/\n/g, " "));
}

async function down({ page, shot }) {
  const frame = await openPanel(page);
  console.log("(this scenario expects the backend to be STOPPED; pass it only then)");
  await runPrompt(page, frame, "hello");
  const ok = await toast(page, "Forge backend is not running", 20000);
  await shot("14-backend-down");
  check("an unreachable backend gets an offer to start it (not a crash)", ok);
  check("the notification offers Start and Cancel", (await page.evaluate(() => [...document.querySelectorAll(".notifications-toasts a.monaco-button")].map((b) => b.textContent.trim()))).join() .includes("Start"));
  await clickToastButton(page, "Cancel");
  await sleep(1000);
  check("after cancelling, the user's task text is back in the prompt box", (await frame.$eval("#prompt", (e) => e.value)) === "hello");
}

const chord = async (page, mod, key) => {
  await page.keyboard.down(mod);
  await page.keyboard.press(key);
  await page.keyboard.up(mod);
};
const tabLabels = (page) => page.evaluate(() => [...document.querySelectorAll(".tabs-container .tab")].map((t) => t.getAttribute("aria-label") || t.textContent));
async function until(fn, timeout = 15000, step = 300) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await fn();
    if (v || Date.now() > end) return v;
    await sleep(step);
  }
}

/** File references (@mentions, selection hint, Alt+K) and insights (stats bar, inline diffs, activity, native diff). */
async function refs({ page, shot }) {
  const before = read("rangesum.py");
  const frame = await openPanel(page);

  // open the file in the editor and select its first two lines
  await chord(page, "Control", "KeyP");
  await sleep(600);
  await page.keyboard.type("rangesum.py");
  await sleep(800);
  await page.keyboard.press("Enter");
  await until(async () => (await tabLabels(page)).some((t) => /rangesum\.py/.test(t)));
  await sleep(800);
  await page.click(".monaco-editor .view-lines");
  await chord(page, "Control", "Home");
  await page.keyboard.down("Shift");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.up("Shift");
  const hint = await until(async () => frame.evaluate(() => (document.getElementById("selhint").className === "on" ? document.getElementById("selref").textContent : "")));
  await shot("15-refs-selection-hint");
  check("selecting lines in the editor shows an @file#range hint in the panel", /^@rangesum\.py#1-2$/.test(hint), hint);

  // Alt+K puts the selection into the prompt box as a mention
  await chord(page, "Alt", "KeyK");
  const afterAltK = await until(() => frame.$eval("#prompt", (e) => e.value).then((v) => (v.includes("@rangesum.py#1-2") ? v : "")));
  check("Alt+K adds the selection to the prompt as @rangesum.py#1-2", !!afterAltK, afterAltK);

  // @ autocomplete: offers files, never secrets, accepts with Enter
  await frame.$eval("#prompt", (e) => { e.value = ""; });
  await frame.click("#prompt");
  await frame.type("#prompt", "explain @env");
  await sleep(1200);
  const envOptions = await frame.$$eval("#ac .opt", (rows) => rows.map((r) => r.title));
  check("@ autocomplete never offers the .env secret file", !envOptions.includes(".env"), JSON.stringify(envOptions));
  await frame.$eval("#prompt", (e) => { e.value = ""; });
  await frame.type("#prompt", "@rangesu");
  const shown = await until(() => frame.$$eval("#ac .opt", (rows) => rows.map((r) => r.title)).then((r) => (r.length ? r : null)));
  await shot("16-refs-autocomplete");
  check("typing @rangesu offers rangesum.py", Array.isArray(shown) && shown[0] === "rangesum.py", JSON.stringify(shown));
  await page.keyboard.press("Enter");
  await sleep(300);
  const accepted = await frame.$eval("#prompt", (e) => e.value);
  check("Enter accepts the suggestion and does NOT submit", accepted === "@rangesum.py " && (await text(frame, "#go")) === "Run", JSON.stringify(accepted));

  // run with a mention of a real file, a line range, and a secret file (which must be refused, visibly)
  await frame.$eval("#prompt", (e) => { e.value = ""; });
  await runPrompt(page, frame, "range_sum in @rangesum.py#1-3 returns wrong sums. Fix it and run the tests. Hints may be in @.env (do not rely on them).");
  await frame.waitForFunction(() => document.getElementById("stats").className === "on", { timeout: 60000 });
  const liveStats = await text(frame, "#stats");
  await shot("17-refs-running-stats");
  check("a live stats bar shows steps, tool calls, tokens and cost while running", /step \d+\/\d+ · \d+ tool calls · .* in \/ .* out/.test(liveStats), liveStats);
  await waitResult(frame);
  await sleep(500);
  const body = await allText(frame);
  await shot("18-refs-result");
  check("the user's message keeps the @mentions as typed", /@rangesum\.py#1-3/.test(await text(frame, ".user")));
  // lines 1-3 are the whole 3-line file, so the timeline names the plain path (a partial range would read rangesum.py:2-3)
  check("the timeline says which files were attached", /Attached 1 file\(s\): rangesum\.py(\s|$)/.test(body), body.split("\n").find((l) => /Attached|could not attach/.test(l)) || "");
  check("a referenced secret file is refused with a reason", /could not attach \.env: /.test(body));
  const finalStats = await text(frame, "#stats");
  check("the stats bar ends with the final numbers", /step \d+\/\d+/.test(finalStats) && /~\$|cost unknown/.test(finalStats), finalStats);

  // inline diff of the edit
  const edit = await frame.$("details.diff summary");
  check("the edit appears in the timeline with a collapsible inline diff", !!edit);
  if (edit) await edit.click();
  await sleep(400);
  const diffLines = await frame.$$eval("pre.diffbody span", (s) => s.map((x) => x.className + ": " + x.textContent));
  await shot("19-refs-inline-diff");
  check("the inline diff shows removed and added lines", diffLines.some((l) => l.startsWith("del")) && diffLines.some((l) => l.startsWith("add") && /n \+ 1/.test(l)), JSON.stringify(diffLines.slice(0, 4)));

  // activity receipt
  const activity = await text(frame, ".card details summary");
  check("the result card has an activity receipt (read / changed / ran counts)", /Activity: read \d+, changed 1, ran \d+/.test(await frame.evaluate(() => [...document.querySelectorAll(".card details summary")].map((s) => s.textContent).find((t) => t.startsWith("Activity")) || "")), activity);

  // clickable files
  const link = await frame.evaluateHandle(() => document.querySelector(".item.tool a.file"));
  await link.asElement().click();
  const opened = await until(async () => (await tabLabels(page)).some((t) => /rangesum\.py/.test(t) && !/Forge change/.test(t)));
  check("clicking a file name in the timeline opens it in the editor", opened);

  // native side-by-side diff
  const changed = await frame.evaluateHandle(() => [...document.querySelectorAll(".card li a.file")].find((a) => a.textContent === "rangesum.py"));
  await changed.asElement().click();
  const diffTab = await until(async () => (await tabLabels(page)).some((t) => /rangesum\.py \(Forge change\)/.test(t)), 20000);
  await sleep(1200);
  const sides = await page.evaluate(() => ({
    // Monaco renders spaces as U+00A0
    original: [...document.querySelectorAll(".monaco-diff-editor .editor.original .view-line")].map((l) => l.textContent).join("\n").replace(/ /g, " "),
    modified: [...document.querySelectorAll(".monaco-diff-editor .editor.modified .view-line")].map((l) => l.textContent).join("\n").replace(/ /g, " "),
  }));
  await shot("20-refs-native-diff");
  check("clicking a changed file opens VS Code's side-by-side diff", diffTab);
  check("the diff compares the user's file (left) with the agent's version (right)", /range\(1, n\)/.test(sides.original) && /range\(1, n \+ 1\)/.test(sides.modified), JSON.stringify(sides).slice(0, 300));
  check("nothing was written to the workspace", read("rangesum.py") === before);
}

/** forge.thinking, sub-agent and skill events in the timeline. Needs an unfixed repo; backend started with FORGE_SKILLS_DIRS. */
async function agentic({ page, shot }) {
  const frame = await openPanel(page);
  await runPrompt(page, frame, "range_sum gives wrong results. Delegate a quick look to an explore sub-agent, then fix it, load the matching skill, and run the tests.");
  await waitResult(frame, 240000);
  await sleep(500);
  const body = await allText(frame);
  await shot("21-agentic-timeline");
  const items = await frame.$$eval(".item", (n) => n.map((x) => x.className + "|" + x.textContent));
  check("the timeline shows the sub-agent being started with its profile", items.some((t) => /^item tool\|sub-agent explore-1:/.test(t)), items.find((t) => /sub-agent explore-1/.test(t)) || "");
  check("the timeline shows the sub-agent finishing", items.some((t) => /sub-agent explore-1 finished \(done, \d+ steps\)/.test(t)));
  check("the sub-agent's own text is tagged", items.some((t) => /\[explore-1\]/.test(t)));
  check("the skills available are listed", /Skills available: fix-tests/.test(body));
  check("the result card is verified", /Completed: verified/.test(await resultTitle(frame)), await resultTitle(frame));
  const tasks = await (await fetch("http://127.0.0.1:8000/api/tasks?limit=1")).json();
  check("the forge.thinking setting reached the task", tasks.items[0].thinking === "high", String(tasks.items[0].thinking));
}

function readExtHostLogs(profile) {
  const out = [];
  const walk = (d) => { for (const e of fs.existsSync(d) ? fs.readdirSync(d, { withFileTypes: true }) : []) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (/exthost.*\.log$|renderer.*\.log$/.test(e.name)) out.push(...fs.readFileSync(p, "utf8").split("\n")); } };
  walk(path.join(profile, "user", "logs"));
  return out;
}

main().catch((e) => (console.error("DRIVER FAILED:", e.message), process.exit(2)));
