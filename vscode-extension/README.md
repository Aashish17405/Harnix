# Forge for VS Code

Use the Forge coding-agent harness from inside VS Code: describe a task in the **Forge** side panel, watch the agent work
live, approve risky actions inline, see the independent verification result, then review the diff and apply it.

* Works on an **isolated copy** of your workspace. Nothing is written to your files until you click **Apply**.
* The agent never decides it is done: Forge runs its own checks and the result says **verified** or **NOT verified**.
* Applying refuses to overwrite anything you changed since the task started (all-or-nothing).

## Setup

1. Install the backend (once): see the main README (`pip install -e backend`, configure `.env`, build the sandbox image).
2. Install this extension: `code --install-extension forge-vscode-0.5.1.vsix`.
3. Open a folder, run **Forge: Start Backend Server** (or `forge serve --allow <your folder>` in any terminal).
   If `forge` is not on your PATH, set `forge.command` to its full path.
4. Click the Forge icon in the activity bar, type a task, press Enter.

## Referring to files

* Type `@` in the prompt box for a file picker; `@path#10-20` attaches only those lines. Up/Down move, Enter or Tab accept, Esc closes.
* **Alt+K** in an editor adds the selection (or the whole file) as a mention; the panel also shows the current selection with an **Add** button. The same command is in the editor and Explorer context menus.
* Files are read by the backend from the isolated copy and given to the model as untrusted data. Secret-looking files (`.env`, keys) are never suggested and are refused if mentioned; the timeline tells you what was attached and what was refused. Names with spaces, `@` or `#` cannot be written as a mention.
* Forge reads the **saved** file: if a mentioned file has unsaved changes you are asked to save first.

## Insights

A live bar shows steps, tool calls, tokens, context size, estimated cost and time. File names in the timeline are clickable, edits have a collapsible red/green preview, and the result card has an **Activity** receipt (files read/changed, commands with exit codes, approvals, refused/failed calls). Click a changed file for VS Code's side-by-side diff of your file against the agent's version.

## Commands

`Forge: New Task…` (`Ctrl+Alt+F`) · `Forge: Ask About Selection…` (editor context menu) · `Forge: Add Selection (or File) to Prompt` (`Alt+K`) · `Forge: Cancel Running Task` ·
`Forge: Start Backend Server` · `Forge: Check Backend` · `Forge: Set API Token` (only if the backend uses `FORGE_API_TOKEN`).

## Settings

`forge.serverUrl` (default `http://127.0.0.1:8000`), `forge.command` (default `forge`), `forge.thinking` (`default`, `low`, `medium`, `high`, `xhigh` = extra high: how much the model reasons; the timeline shows when a model cannot honour it). Sub-agent work, skills, memory and context compaction appear in the timeline.
