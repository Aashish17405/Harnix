import * as fs from "fs";
import * as path from "path";
import { randomBytes } from "crypto";
import * as vscode from "vscode";
import { HarnixApi, HarnixApiError, extractReferences, fuzzyFilter, looksSecret } from "./core";
import { ApprovalInfo, AttachedImage, FileChange, Session, SessionUi, ToPanel } from "./session";
import { getHtml } from "./webview";

const TOKEN_KEY = "harnix.apiToken";
const INDEX_EXCLUDE = "{**/node_modules/**,**/.git/**,**/.venv/**,**/venv/**,**/__pycache__/**,**/dist/**,**/out/**,**/build/**,**/.forge/**}";
const MAX_INDEXED_FILES = 5000;

/** `rel` joined onto `root`, or undefined when it is absolute, climbs out with "..", or is empty. Never trust webview strings. */
function resolveInside(root: string, rel: string): string | undefined {
  if (!rel || path.isAbsolute(rel) || rel.split(/[\\/]/).includes("..")) return undefined;
  const full = path.join(root, rel);
  return full.startsWith(root + path.sep) ? full : undefined;
}

const isFile = (p: string | undefined): boolean => {
  try {
    return !!p && fs.statSync(p).isFile();
  } catch {
    return false;
  }
};

class Panel implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  version = "";
  private queue: ToPanel[] = [];
  onMessage: (m: { type: string } & Record<string, unknown>) => void = () => {};
  onReady: () => void = () => {};

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = getHtml(randomBytes(16).toString("base64"), view.webview.cspSource, this.version);
    view.webview.onDidReceiveMessage((m) => this.onMessage(m));
    view.onDidDispose(() => (this.view = undefined));
    for (const m of this.queue.splice(0)) void view.webview.postMessage(m);
    this.onReady();
  }

  post(msg: ToPanel): void {
    if (this.view) void this.view.webview.postMessage(msg);
    else if (this.queue.length < 500) this.queue.push(msg); // shown when the panel is first opened
  }

  reveal(): Thenable<unknown> {
    return vscode.commands.executeCommand("harnix.chat.focus");
  }
}

export interface HarnixExtensionApi {
  /** Runs a task in the first workspace folder; `ui` replaces the default UI (used by tests). */
  runPrompt(prompt: string, ui?: Partial<SessionUi>): Promise<import("./session").RunResult | undefined>;
  /** Applies a finished task's changes to the workspace (the same path as the panel's Apply button). */
  applyTask(taskId: string, ui?: Partial<SessionUi>): Promise<boolean>;
}

export function activate(ctx: vscode.ExtensionContext): HarnixExtensionApi {
  const output = vscode.window.createOutputChannel("Harnix");
  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  bar.command = "harnix.newTask";
  bar.text = "$(tools) Harnix";
  bar.tooltip = "Harnix coding agent: new task";
  bar.show();
  const panel = new Panel();
  panel.version = String(ctx.extension.packageJSON.version ?? "");
  let session: Session | undefined;

  const cfg = () => vscode.workspace.getConfiguration("forge");
  const makeApi = () => new HarnixApi({ baseUrl: cfg().get<string>("serverUrl", "http://127.0.0.1:8000"), getToken: () => ctx.secrets.get(TOKEN_KEY) });

  function workspaceFolder(): vscode.WorkspaceFolder | undefined {
    const active = vscode.window.activeTextEditor?.document.uri;
    return (active && vscode.workspace.getWorkspaceFolder(active)) || vscode.workspace.workspaceFolders?.[0];
  }

  // diffs are shown as read-only virtual documents: no "Untitled" tab, no unsaved-changes dot, no save prompt on close
  const diffs = new Map<string, string>();
  let diffSeq = 0;
  ctx.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("forge-diff", { provideTextDocumentContent: (uri) => diffs.get(uri.path) ?? "" }),
  );
  const emptyDoc = vscode.Uri.from({ scheme: "forge-diff", path: "/empty" }); // never stored, so it reads as ""
  /** Registers read-only text; the name's extension gives the right syntax highlighting. */
  function virtualDoc(name: string, text: string): vscode.Uri {
    const key = `/${++diffSeq}/${name}`;
    diffs.set(key, text);
    if (diffs.size > 60) diffs.delete(diffs.keys().next().value as string);
    return vscode.Uri.from({ scheme: "forge-diff", path: key });
  }

  const relIn = (folder: vscode.WorkspaceFolder, uri: vscode.Uri) => path.relative(folder.uri.fsPath, uri.fsPath).split(path.sep).join("/");

  // ---- file index for @-autocomplete (never contains secret-looking files) ----
  let fileIndex: { root: string; files: string[] } | undefined;
  async function listFiles(folder: vscode.WorkspaceFolder): Promise<string[]> {
    if (fileIndex?.root === folder.uri.fsPath) return fileIndex.files;
    const uris = await vscode.workspace.findFiles(new vscode.RelativePattern(folder, "**/*"), INDEX_EXCLUDE, MAX_INDEXED_FILES);
    // names with spaces, @ or # cannot be written as a mention, so they are not offered
    const files = uris.map((u) => relIn(folder, u)).filter((f) => !f.startsWith("..") && !/[\s@#]/.test(f) && !looksSecret(f));
    fileIndex = { root: folder.uri.fsPath, files };
    return files;
  }
  const watcher = vscode.workspace.createFileSystemWatcher("**/*", false, true, false);
  watcher.onDidCreate(() => (fileIndex = undefined));
  watcher.onDidDelete(() => (fileIndex = undefined));

  // ---- the editor selection as an @mention ----
  function selectionRef(): string | null {
    const ed = vscode.window.activeTextEditor;
    if (!ed || ed.document.uri.scheme !== "file" || ed.selection.isEmpty) return null;
    const folder = vscode.workspace.getWorkspaceFolder(ed.document.uri);
    if (!folder) return null;
    const rel = relIn(folder, ed.document.uri);
    if (looksSecret(rel) || /[\s@#]/.test(rel)) return null;
    const a = ed.selection.start.line + 1;
    let b = ed.selection.end.line + 1;
    if (ed.selection.end.character === 0 && b > a) b--; // a selection that ends at the start of a line does not include that line
    return `@${rel}#${a}${b > a ? `-${b}` : ""}`;
  }
  /** The selection, or else the whole active file. */
  function activeRef(): string | null {
    const sel = selectionRef();
    if (sel) return sel;
    const ed = vscode.window.activeTextEditor;
    const folder = ed && ed.document.uri.scheme === "file" ? vscode.workspace.getWorkspaceFolder(ed.document.uri) : undefined;
    if (!ed || !folder) return null;
    const rel = relIn(folder, ed.document.uri);
    return looksSecret(rel) || /[\s@#]/.test(rel) ? null : `@${rel}`;
  }
  let lastHint: string | null | undefined;
  let hintTimer: NodeJS.Timeout | undefined;
  const postHint = () => {
    const ref = selectionRef();
    if (ref === lastHint) return;
    lastHint = ref;
    panel.post({ type: "selection", ref });
  };
  panel.onReady = () => {
    lastHint = undefined;
    postHint();
    void refreshHistory();
    if (conversation.id && !session?.running) {
      const folder = workspaceFolder();
      if (folder) currentRepo = folder.uri.fsPath;
      void new Session(makeApi(), defaultUi).openSession(conversation.id, true); // quietly: the backend may not be up yet
    }
  };

  // what the panel may open or diff: only files of finished tasks that Harnix itself reported as changed
  const results = new Map<string, { repo: string; workspace: string | null; files: Map<string, string> }>();
  let currentRepo = "";
  // the conversation on screen: the next prompt continues it (the agent remembers it); remembered across reloads
  const SESSION_KEY = "harnix.conversation";
  let conversation: { id?: string; title: string } = ctx.workspaceState.get(SESSION_KEY, { title: "" });
  const setConversation = (c: { id?: string; title: string }) => {
    conversation = c;
    void ctx.workspaceState.update(SESSION_KEY, c);
  };

  async function openFile(rel: string, line?: number): Promise<void> {
    const folder = workspaceFolder();
    const full = folder && resolveInside(folder.uri.fsPath, rel);
    if (!full || !isFile(full)) return void vscode.window.showWarningMessage(`Harnix: ${rel} is not a file in this workspace.`);
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(full));
    const at = new vscode.Position(Math.max(0, (line ?? 1) - 1), 0);
    await vscode.window.showTextDocument(doc, { selection: new vscode.Range(at, at), preview: true });
  }

  /** The two sides of one changed file, from the backend (so they are right even after the change was applied). */
  async function sides(taskId: string, rel: string): Promise<{ left: vscode.Uri; right: vscode.Uri } | string> {
    const api = makeApi();
    const [before, after] = await Promise.all([api.fileVersion(taskId, rel, "baseline"), api.fileVersion(taskId, rel, "final")]);
    if (before.binary || after.binary) return `${rel} is a binary file: there is no text diff.`;
    return {
      left: before.exists ? virtualDoc(rel, before.content) : emptyDoc,
      right: after.exists ? virtualDoc(rel, after.content) : emptyDoc,
    };
  }

  /** Native side-by-side diff of one file: how it was before the task, and how the agent left it. */
  async function openFileDiff(taskId: string, rel: string): Promise<void> {
    try {
      const s = await sides(taskId, rel);
      if (typeof s === "string") return void panel.post({ type: "notice", kind: "warn", text: s });
      await vscode.commands.executeCommand("vscode.diff", s.left, s.right, `${rel}: before ↔ after`);
    } catch (e) {
      const old404 = e instanceof HarnixApiError && e.status === 404;
      panel.post({ type: "notice", kind: old404 ? "warn" : "error", text: old404 ? "Your Harnix backend is older than this extension (no per-file view). Restart it with forge serve --allow <folder>." : `Could not open the diff for ${rel}: ${(e as Error).message}` });
    }
  }

  /** All changed files in VS Code's multi-file diff editor: the readable way to review a whole change. */
  async function reviewChanges(taskId: string): Promise<void> {
    const r = results.get(taskId);
    if (!r || !r.files.size) return void panel.post({ type: "notice", kind: "warn", text: "There are no changes to review." });
    try {
      const resources: [vscode.Uri, vscode.Uri, vscode.Uri][] = [];
      for (const rel of r.files.keys()) {
        const s = await sides(taskId, rel);
        if (typeof s !== "string") resources.push([vscode.Uri.file(path.join(r.repo || "/", rel)), s.left, s.right]);
      }
      if (!resources.length) return void panel.post({ type: "notice", kind: "warn", text: "None of the changed files has a text diff." });
      await vscode.commands.executeCommand("vscode.changes", `Harnix changes: ${resources.length} ${resources.length === 1 ? "file" : "files"}`, resources);
    } catch (e) {
      if (e instanceof HarnixApiError && e.status === 404) {
        panel.post({ type: "notice", kind: "warn", text: "Your Harnix backend is older than this extension (it has no per-file view yet), so the side-by-side review is unavailable. Restart the backend (forge serve --allow <folder>) to get it. Showing the raw patch instead." });
        await (session ?? new Session(makeApi(), defaultUi)).showDiff(taskId);
        return;
      }
      panel.post({ type: "notice", kind: "error", text: `Could not open the review: ${(e as Error).message}` });
    }
  }

  const defaultUi: SessionUi = {
    post(msg) {
      panel.post(msg);
      if (msg.type === "result") {
        results.set(msg.result.taskId, { repo: currentRepo, workspace: msg.result.workspacePath, files: new Map(msg.result.files.map((f) => [f.path, f.status])) });
        if (results.size > 20) results.delete(results.keys().next().value as string);
      }
      if (msg.type === "sessionInfo") setConversation(msg.id ? { id: msg.id, title: msg.title } : { title: "" });
      if (msg.type === "status") bar.text = `$(${msg.busy ? "sync~spin" : "tools"}) Harnix: ${msg.text}`;
      // the approval notification cannot be closed from code; hide popups once the question is settled elsewhere
      // (panel button, cancel, timeout) so a stale "Approve" button is not left on screen
      if (msg.type === "approvalResolved") void vscode.commands.executeCommand("notifications.hideToasts");
    },
    async notifyApproval(a: ApprovalInfo) {
      const args = Object.entries(a.arguments ?? {}).filter(([, v]) => v !== null && v !== "." && v !== false).map(([k, v]) => `${k}=${String(v).slice(0, 80)}`).join(", ");
      const pick = await vscode.window.showWarningMessage(`Harnix wants to run ${a.tool}: ${a.reason}\n${args}`, "Approve", "Reject");
      return pick === undefined ? undefined : pick === "Approve";
    },
    async confirmApply(files: FileChange[], verified: boolean) {
      const detail = files.map((f) => `${f.status} ${f.path}`).join("\n") + (verified ? "" : "\n\nThis result was NOT independently verified.");
      return (await vscode.window.showWarningMessage(`Apply ${files.length} file(s) to your workspace?`, { modal: true, detail }, "Apply")) === "Apply";
    },
    async confirmRestore(turn: number, discarded: number, files: FileChange[]) {
      const detail = [
        turn === 0 ? "The workspace goes back to how it was when this conversation started." : `The workspace goes back to how it was right after response ${turn}.`,
        discarded ? `${discarded} later message(s) are set aside: the agent will not remember them.` : "",
        files.length ? `Files that change in the workspace copy: ${files.map((f) => f.path).slice(0, 12).join(", ")}${files.length > 12 ? ", …" : ""}` : "No files change.",
        "",
        "Your real project files are not touched. If you applied any of the later changes, revert them separately.",
      ].filter((x) => x !== "").join("\n");
      return (await vscode.window.showWarningMessage(turn === 0 ? "Go back to the start of this conversation?" : `Go back to response ${turn}?`, { modal: true, detail }, "Go back")) === "Go back";
    },
    async confirmRevert(files: FileChange[]) {
      const detail = files.map((f) => `${f.status === "A" ? "delete" : f.status === "D" ? "restore" : "restore original of"} ${f.path}`).join("\n") +
        "\n\nFiles you changed since the apply are never touched: if any were, nothing is reverted.";
      return (await vscode.window.showWarningMessage(`Revert ${files.length} file(s) to how they were before this task was applied?`, { modal: true, detail }, "Revert")) === "Revert";
    },
    async showDiff(diff: string) {
      const doc = await vscode.workspace.openTextDocument(virtualDoc("forge-changes.diff", diff));
      await vscode.window.showTextDocument(doc, { preview: true });
    },
  };

  async function startServer(folder: vscode.WorkspaceFolder): Promise<void> {
    // run the CLI directly (no shell), so paths with spaces need no quoting
    const term = vscode.window.createTerminal({ name: "Harnix server", cwd: folder.uri.fsPath, shellPath: cfg().get<string>("command", "forge"), shellArgs: ["serve", "--allow", folder.uri.fsPath] });
    term.show(true);
  }

  async function ensureServer(api: HarnixApi, folder: vscode.WorkspaceFolder): Promise<boolean> {
    try {
      await api.health();
      return true;
    } catch (e) {
      if (!(e instanceof HarnixApiError && e.unreachable)) throw e;
    }
    const pick = await vscode.window.showInformationMessage("The Harnix backend is not running. Start it for this workspace?", "Start", "Cancel");
    if (pick !== "Start") return false;
    await startServer(folder);
    return vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Starting the Harnix backend…" }, async () => {
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          await api.health();
          return true;
        } catch {
          /* keep waiting */
        }
      }
      void vscode.window.showErrorMessage("The Harnix backend did not start. Check the 'Harnix server' terminal (is `forge` on your PATH? see the harnix.command setting).");
      return false;
    });
  }

  /** Images from the panel: only well-formed entries, at most five; the backend checks the bytes again. */
  function cleanImages(raw: unknown): AttachedImage[] | undefined {
    if (!Array.isArray(raw)) return undefined;
    const out: AttachedImage[] = [];
    for (const r of raw.slice(0, 5)) {
      if (r && typeof r.data === "string" && typeof r.name === "string" && typeof r.mediaType === "string" && r.data.length <= 8_000_000) out.push({ name: r.name.slice(0, 120), mediaType: r.mediaType.slice(0, 40), data: r.data });
    }
    return out.length ? out : undefined;
  }

  /** The "Attach image" button: a native file dialog (reliable in every VS Code build), then the bytes go to the panel. */
  async function pickImages(): Promise<void> {
    const picked = await vscode.window.showOpenDialog({ canSelectMany: true, title: "Attach images to Harnix", openLabel: "Attach", filters: { Images: ["png", "jpg", "jpeg", "gif", "webp"] } });
    if (!picked?.length) return;
    const types: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp" };
    const items: { name: string; mediaType: string; data: string }[] = [];
    for (const uri of picked.slice(0, 5)) {
      const ext = path.extname(uri.fsPath).slice(1).toLowerCase();
      try {
        const st = fs.statSync(uri.fsPath);
        if (st.size > 20 * 1024 * 1024) {
          panel.post({ type: "notice", kind: "warn", text: `${path.basename(uri.fsPath)} is larger than 20 MB and was not attached.` });
          continue;
        }
        items.push({ name: path.basename(uri.fsPath), mediaType: types[ext] ?? "", data: fs.readFileSync(uri.fsPath).toString("base64") });
      } catch (e) {
        panel.post({ type: "notice", kind: "error", text: `Could not read ${path.basename(uri.fsPath)}: ${(e as Error).message}` });
      }
    }
    if (items.length) panel.post({ type: "images", items } as unknown as ToPanel);
  }

  /** Sends this workspace's earlier sessions to the panel (empty list + reason if the backend is down). */
  async function refreshHistory(): Promise<void> {
    const folder = workspaceFolder();
    if (!folder) return void panel.post({ type: "history", items: [], error: "Open a folder to see its Harnix sessions." });
    await new Session(makeApi(), defaultUi).loadHistory(folder.uri.fsPath);
  }

  async function runPrompt(prompt: string, uiOverride?: Partial<SessionUi>, thinkingOverride?: string, images?: AttachedImage[]) {
    const folder = workspaceFolder();
    if (!folder) {
      void vscode.window.showErrorMessage("Open a folder first: Harnix works on the files in your workspace.");
      return undefined;
    }
    if (session?.running) {
      void vscode.window.showWarningMessage("A Harnix task is already running. Cancel it first.");
      return undefined;
    }
    const api = makeApi();
    const ui: SessionUi = { ...defaultUi, ...uiOverride } as SessionUi;
    if (!uiOverride) await panel.reveal();
    const root = folder.uri.fsPath;
    const mentions = extractReferences(prompt, (rel) => isFile(resolveInside(root, rel)));
    if (!uiOverride) {
      const looksLikePath = mentions.missing.filter((p) => /[./]/.test(p));
      if (looksLikePath.length) void vscode.window.showWarningMessage(`Harnix: no such file in this workspace: ${looksLikePath.join(", ")}. It is sent as plain text.`);
      // the agent works on the files as saved on disk; unsaved editor changes would silently be missing
      const dirty = vscode.workspace.textDocuments.filter((d) => d.isDirty && mentions.refs.some((r) => r.path === relIn(folder, d.uri)));
      if (dirty.length) {
        const names = dirty.map((d) => relIn(folder, d.uri)).join(", ");
        const pick = await vscode.window.showWarningMessage(`${names} ${dirty.length > 1 ? "have" : "has"} unsaved changes. Harnix reads the saved file.`, "Save and run", "Run anyway");
        if (pick === undefined) return void ui.post({ type: "restore", prompt });
        if (pick === "Save and run") await Promise.all(dirty.map((d) => d.save()));
      }
    }
    if (!(await ensureServer(api, folder))) {
      ui.post({ type: "restore", prompt }); // do not make the user retype a long task
      return undefined;
    }
    currentRepo = root;
    session = new Session(api, ui);
    const chosen = thinkingOverride && thinkingOverride !== "default" ? thinkingOverride : cfg().get<string>("thinking", "default");
    const result = await session.run({
      repoPath: root, prompt: mentions.text, displayPrompt: prompt, references: mentions.refs,
      sessionId: conversation.id, sessionTitle: conversation.title || undefined,
      thinking: chosen === "default" || !["low", "medium", "high", "xhigh"].includes(chosen) ? undefined : chosen,
          images,
    });
    if (!uiOverride) void refreshHistory();
    return result;
  }

  panel.onMessage = (m) => {
    const taskId = String(m.taskId ?? "");
    switch (m.type) {
      case "run":
        void runPrompt(String(m.prompt), undefined, typeof m.thinking === "string" ? m.thinking : undefined, cleanImages(m.images));
        break;
      case "history":
        void refreshHistory();
        break;
      case "pickImages":
        void pickImages();
        break;
      case "openSession": {
        const folder = workspaceFolder();
        if (folder) currentRepo = folder.uri.fsPath;
        void new Session(makeApi(), defaultUi).openSession(String(m.sessionId ?? ""));
        break;
      }
      case "newSession":
        setConversation({ title: "" });
        void refreshHistory();
        break;
      case "restoreSession": {
        if (!conversation.id) break;
        void new Session(makeApi(), defaultUi).restoreSession(conversation.id, Number(m.turn) || 0).then(() => refreshHistory());
        break;
      }
      case "renameSession": {
        const id = conversation.id;
        if (!id) break;
        void vscode.window.showInputBox({ title: "Rename this conversation", value: conversation.title, ignoreFocusOut: true }).then(async (title) => {
          if (title?.trim() && (await new Session(makeApi(), defaultUi).renameSession(id, title.trim()))) {
            setConversation({ id, title: title.trim() });
            panel.post({ type: "sessionInfo", id, title: title.trim(), turns: Number(m.turns) || 0 });
            void refreshHistory();
          }
        });
        break;
      }
      case "revert":
        void (session ?? new Session(makeApi(), defaultUi)).revertTask(taskId).then((ok) => (ok ? refreshHistory() : undefined));
        break;
      case "copyDiff":
        void makeApi().diff(taskId).then(async (d) => {
          await vscode.env.clipboard.writeText(String(d.diff ?? ""));
          void vscode.window.showInformationMessage("Harnix: diff copied to the clipboard.");
        }, (e) => void vscode.window.showErrorMessage(`Harnix: could not load the diff: ${(e as Error).message}`));
        break;
      case "cancel":
        void session?.cancel();
        break;
      case "approval":
        void session?.resolveApproval(String(m.id), m.approve === true);
        break;
      case "diff":
        void session?.showDiff(taskId);
        break;
      case "apply":
        void (session ?? new Session(makeApi(), defaultUi)).applyTask(taskId).then((ok) => (ok ? refreshHistory() : undefined));
        break;
      case "files": {
        const folder = workspaceFolder();
        const query = String(m.query ?? "").slice(0, 200);
        if (folder) void listFiles(folder).then((all) => panel.post({ type: "files", query, results: fuzzyFilter(all, query) }));
        break;
      }
      case "open":
        void openFile(String(m.file ?? ""), Number(m.line) || undefined);
        break;
      case "copyText":
        if (typeof m.text === "string" && m.text.length <= 20000) void vscode.env.clipboard.writeText(m.text);
        break;
      case "review":
        void reviewChanges(taskId);
        break;
      case "fileDiff":
        void openFileDiff(taskId, String(m.file ?? ""));
        break;
    }
  };

  ctx.subscriptions.push(
    output,
    bar,
    vscode.window.registerWebviewViewProvider("harnix.chat", panel, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.commands.registerCommand("harnix.newTask", async () => {
      const prompt = await vscode.window.showInputBox({ title: "Harnix: new task", prompt: "What should the agent do?", ignoreFocusOut: true });
      if (prompt?.trim()) await runPrompt(prompt.trim());
    }),
    vscode.commands.registerCommand("harnix.askAboutSelection", async () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed || ed.selection.isEmpty) return void vscode.window.showInformationMessage("Select some code first.");
      const ref = selectionRef();
      if (!ref) return void vscode.window.showWarningMessage("Harnix can only reference selections in files of this workspace (not secrets or unsaved buffers).");
      const prompt = await vscode.window.showInputBox({ title: "Harnix: ask about the selection", prompt: `What should the agent do with ${ref}?`, ignoreFocusOut: true });
      if (prompt?.trim()) await runPrompt(`${prompt.trim()} ${ref}`);
    }),
    vscode.commands.registerCommand("harnix.addSelectionToPrompt", async (uri?: vscode.Uri) => {
      // from the explorer's context menu VS Code passes the clicked file
      const fromExplorer = uri instanceof vscode.Uri;
      const folder = fromExplorer ? vscode.workspace.getWorkspaceFolder(uri) : undefined;
      const rel = fromExplorer && folder ? relIn(folder, uri) : "";
      const mentionable = !!rel && isFile(uri instanceof vscode.Uri ? uri.fsPath : undefined) && !looksSecret(rel) && !/[\s@#]/.test(rel);
      const ref = fromExplorer ? (mentionable ? `@${rel}` : null) : activeRef();
      if (!ref) return void vscode.window.showInformationMessage("Open a file of this workspace (or select some lines) first.");
      await panel.reveal();
      panel.post({ type: "insert", text: ref });
    }),
    watcher,
    vscode.window.onDidChangeTextEditorSelection(() => {
      clearTimeout(hintTimer);
      hintTimer = setTimeout(postHint, 150);
    }),
    vscode.window.onDidChangeActiveTextEditor(postHint),
    vscode.commands.registerCommand("harnix.cancel", () => session?.cancel()),
    vscode.commands.registerCommand("harnix.startServer", async () => {
      const folder = workspaceFolder();
      if (folder) await startServer(folder);
      else void vscode.window.showErrorMessage("Open a folder first.");
    }),
    vscode.commands.registerCommand("harnix.doctor", async () => {
      try {
        const [h, c] = await Promise.all([makeApi().health(), makeApi().config()]);
        const spend = h.spend ? ` · spent $${Number(h.spend.spent_usd).toFixed(4)}` : "";
        const problems = [h.provider.configured ? "" : `Model: ${h.provider.problem}`, h.sandbox.available ? "" : `Sandbox: ${h.sandbox.detail}`, c.allowed_repo_roots.length ? "" : "No repository roots authorized (forge serve --allow <dir>)"].filter(Boolean);
        const msg = `Harnix extension ${ctx.extension.packageJSON.version} / backend ${h.version}: ${h.provider.name} ${h.provider.model} · ${h.execution_mode} sandbox${spend}`;
        if (problems.length) void vscode.window.showWarningMessage(`${msg}\n${problems.join("\n")}`);
        else void vscode.window.showInformationMessage(msg);
      } catch (e) {
        void vscode.window.showErrorMessage(`${(e as Error).message}. Start it with "Harnix: Start Backend Server".`);
      }
    }),
    vscode.commands.registerCommand("harnix.setApiToken", async () => {
      const token = await vscode.window.showInputBox({ title: "Harnix API token", prompt: "FORGE_API_TOKEN of your backend (stored in VS Code's secret storage)", password: true, ignoreFocusOut: true });
      if (token !== undefined) await ctx.secrets.store(TOKEN_KEY, token);
    }),
    { dispose: () => session?.dispose() },
  );

  return {
    runPrompt,
    applyTask: (taskId, uiOverride) => new Session(makeApi(), { ...defaultUi, ...uiOverride } as SessionUi).applyTask(taskId),
  };
}

export function deactivate(): void {}
