/**
 * HTML for the Harnix chat panel. All dynamic text is inserted with textContent (never as markup).
 * NOTE: this is a template literal. Backslashes in the embedded script must be doubled, and the script must not
 * contain backticks or dollar-brace sequences.
 *
 * Design: a quiet developer tool that borrows only VS Code's own theme colours (so it fits every theme), with
 * hairline borders, one accent, tabular numbers and mono chips. Tool calls are grouped with their result into
 * one collapsible step; the result is one verdict card; changes are shown as readable per-file diffs.
 */
export function getHtml(nonce: string, cspSource: string, version = ""): string {
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root {
    --fg: var(--vscode-foreground);
    --ok: var(--vscode-testing-iconPassed, #3fb950);
    --warn: var(--vscode-editorWarning-foreground, #d29922);
    --bad: var(--vscode-testing-iconFailed, var(--vscode-errorForeground, #f85149));
    --accent: var(--vscode-textLink-foreground, #4493f8);
    --line: var(--vscode-panel-border, color-mix(in srgb, var(--fg) 18%, transparent));
    --soft: color-mix(in srgb, var(--fg) 6%, transparent);
    --soft2: color-mix(in srgb, var(--fg) 11%, transparent);
    --muted: color-mix(in srgb, var(--fg) 62%, transparent);
    --mono: var(--vscode-editor-font-family, ui-monospace, monospace);
    --add-bg: var(--vscode-diffEditor-insertedLineBackground, color-mix(in srgb, var(--ok) 14%, transparent));
    --add-word: var(--vscode-diffEditor-insertedTextBackground, color-mix(in srgb, var(--ok) 38%, transparent));
    --del-bg: var(--vscode-diffEditor-removedLineBackground, color-mix(in srgb, var(--bad) 14%, transparent));
    --del-word: var(--vscode-diffEditor-removedTextBackground, color-mix(in srgb, var(--bad) 38%, transparent));
    --r: 6px;
  }
  * { box-sizing: border-box; }
  html { touch-action: manipulation; -webkit-tap-highlight-color: transparent; }
  body { margin: 0; padding: 0; display: flex; flex-direction: column; height: 100vh; font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--fg); line-height: 1.5; background: var(--vscode-sideBar-background, transparent); }
  :focus-visible { outline: 1px solid var(--vscode-focusBorder, var(--accent)); outline-offset: 1px; }
  button, summary, select, textarea, input { font-family: inherit; font-size: inherit; }
  code, .mono { font-family: var(--mono); font-size: 0.92em; }
  .num { font-variant-numeric: tabular-nums; }
  h3, h4, h5, p { text-wrap: pretty; }

  /* header */
  #top { display: flex; align-items: center; gap: 4px; padding: 6px 8px 6px 12px; border-bottom: 1px solid var(--line); }
  #status { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #status::before { content: ""; flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
  #status.busy::before { background: var(--accent); animation: pulse 1.3s ease-in-out infinite; }
  #status.bad::before { background: var(--bad); }
  #status.warn::before { background: var(--warn); }
  @keyframes pulse { 50% { opacity: 0.3; transform: scale(0.8); } }
  .tb { background: transparent; color: var(--fg); border: 1px solid transparent; padding: 3px 8px; border-radius: var(--r); cursor: pointer; white-space: nowrap; }
  .tb:hover { background: var(--vscode-toolbar-hoverBackground, var(--soft2)); }
  .tb[aria-pressed="true"] { background: var(--soft2); border-color: var(--line); }
  #stats { display: none; flex-wrap: wrap; gap: 2px 12px; padding: 5px 12px; border-bottom: 1px solid var(--line); font-size: 0.85em; color: var(--muted); }
  #stats.on { display: flex; }
  #sess { display: none; align-items: center; gap: 8px; padding: 4px 12px; border-bottom: 1px solid var(--line); font-size: 0.9em; }
  #sess.on { display: flex; }
  #sess .st { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; font-weight: 600; }
  #sess .stt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #sess .sn { flex: none; color: var(--muted); font-weight: 400; }
  .who { display: block; font-size: 0.75em; color: var(--muted); margin-bottom: 2px; text-transform: uppercase; letter-spacing: 0.05em; }
  .user.discarded { opacity: 0.5; border-left: 3px dashed var(--muted); }
  .user.discarded::after { content: "rolled back: the agent no longer remembers this message"; display: block; margin-top: 4px; font-size: 0.8em; color: var(--muted); }
  .restorebar { padding: 4px 8px; border-top: 1px solid var(--line); text-align: right; background: var(--soft); }
  #viewing { display: none; align-items: center; justify-content: space-between; gap: 8px; padding: 5px 12px; background: var(--soft); border-bottom: 1px solid var(--line); font-size: 0.9em; }
  #viewing.on { display: flex; }

  /* log */
  #logwrap { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
  #log { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 10px 12px 18px; scroll-behavior: auto; overscroll-behavior: contain; }
  #latest { position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%); display: none; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35); border-radius: 14px; padding: 3px 12px; font-size: 0.9em; }
  #latest.on { display: block; }
  .pics { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .pics img { max-height: 84px; max-width: 100%; border-radius: var(--r); border: 1px solid var(--line); cursor: zoom-in; object-fit: cover; }
  .pics img.big { max-height: none; width: 100%; cursor: zoom-out; object-fit: contain; }
  #tray { display: none; flex-wrap: wrap; gap: 10px 12px; padding: 14px 12px 2px; }
  #tray.on { display: flex; }
  .thumb { position: relative; width: 64px; }
  .thumb img { display: block; width: 64px; height: 64px; object-fit: cover; border-radius: var(--r); border: 1px solid var(--line); }
  .thumb .x { position: absolute; top: -9px; right: -9px; width: 22px; height: 22px; padding: 0; border-radius: 50%; line-height: 20px; font-size: 15px; font-weight: 700; cursor: pointer; z-index: 1; background: var(--vscode-badge-background, #555); color: var(--vscode-badge-foreground, #fff); border: 2px solid var(--vscode-sideBar-background, #222); }
  .thumb .x:hover { background: var(--bad); color: #fff; }
  .thumb .tn { display: block; margin-top: 2px; font-size: 0.72em; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .box.drop { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--vscode-input-background)); }
  #imgnote { padding: 2px 10px 0; font-size: 0.78em; color: var(--muted); display: none; }
  #imgnote.on { display: block; }
  .user { background: var(--soft2); border-radius: 8px; padding: 8px 10px; margin: 2px 0 14px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .say { margin: 10px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .say.md { white-space: normal; }
  .md > :first-child { margin-top: 0; } .md > :last-child { margin-bottom: 0; }
  .md p { margin: 0 0 8px; white-space: pre-wrap; }
  .md ul, .md ol { margin: 0 0 8px; padding-left: 22px; } .md li { margin: 2px 0; } .md li > ul, .md li > ol { margin: 2px 0 0; }
  .md strong { font-weight: 650; color: var(--vscode-foreground); }
  .md .mdh { font-weight: 650; margin: 12px 0 4px; } .md .mdh1 { font-size: 1.25em; } .md .mdh2 { font-size: 1.12em; } .md .mdh3, .md .mdh4 { font-size: 1em; }
  .md .mdlink { color: var(--vscode-textLink-foreground); text-decoration: underline dotted; cursor: default; }
  .md blockquote { margin: 0 0 8px; padding: 2px 10px; border-left: 3px solid var(--vscode-textBlockQuote-border, var(--soft2)); opacity: .9; }
  .md hr { border: 0; border-top: 1px solid var(--soft2); margin: 10px 0; }
  .md pre.mdcode { position: relative; margin: 0 0 8px; padding: 8px 10px; background: var(--soft2); border-radius: 5px; overflow-x: auto; white-space: pre; font-family: var(--vscode-editor-font-family, monospace); font-size: .92em; }
  .md pre.mdcode code { background: none; padding: 0; }
  .md pre.mdcode .lang { position: absolute; top: 2px; right: 8px; font-size: .8em; opacity: .6; }
  .md .mdtable { overflow-x: auto; margin: 0 0 8px; } .md table { border-collapse: collapse; } .md th, .md td { border: 1px solid var(--soft2); padding: 3px 8px; text-align: left; } .md th { font-weight: 650; background: var(--soft2); }
  .say code, .line code, .vsub code, .md code { background: var(--soft2); padding: 0 4px; border-radius: 3px; }
  .line { margin: 3px 0; padding-left: 18px; position: relative; color: var(--muted); font-size: 0.93em; overflow-wrap: anywhere; white-space: pre-wrap; }
  .line::before { content: ""; position: absolute; left: 5px; top: 0.6em; width: 5px; height: 5px; border-radius: 50%; background: currentColor; opacity: 0.7; }
  .line.ok { color: var(--ok); } .line.ok::before { content: "✓"; background: none; top: 0; left: 3px; width: auto; height: auto; font-size: 0.95em; opacity: 1; }
  .line.warn { color: var(--warn); } .line.warn::before { content: "!"; background: none; top: 0; left: 5px; width: auto; height: auto; font-weight: 700; opacity: 1; }
  .line.error { color: var(--bad); } .line.error::before { content: "✕"; background: none; top: 0; left: 3px; width: auto; height: auto; opacity: 1; }
  a.file { color: var(--accent); cursor: pointer; text-decoration: underline dotted; text-underline-offset: 2px; }
  a.file:hover { text-decoration: underline; }

  /* a tool call and its result, together */
  details.step { margin: 4px 0; border: 1px solid var(--line); border-radius: var(--r); background: transparent; }
  details.step > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; padding: 4px 8px; min-width: 0; }
  details.step > summary::-webkit-details-marker { display: none; }
  details.step > summary:hover { background: var(--soft); }
  .sico { flex: none; width: 14px; text-align: center; font-size: 0.95em; color: var(--muted); }
  .st-ok .sico { color: var(--ok); } .st-warn .sico { color: var(--warn); } .st-bad .sico { color: var(--bad); }
  .st-run .sico { width: 10px; height: 10px; border: 2px solid var(--line); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .sname { flex: none; font-weight: 600; font-family: var(--mono); font-size: 0.92em; }
  .sargs { flex: 1; min-width: 0; color: var(--muted); font-family: var(--mono); font-size: 0.88em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .smeta { flex: none; color: var(--muted); font-size: 0.85em; max-width: 45%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sbody { padding: 2px 8px 8px 30px; border-top: 1px solid var(--line); }
  .sbody .line { padding-left: 20px; }
  pre.detail { margin: 4px 0; padding: 6px 8px; background: var(--soft); border-radius: var(--r); white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--mono); font-size: 0.88em; max-height: 14em; overflow: auto; }

  /* approval request */
  .card.approval { border-color: color-mix(in srgb, var(--warn) 70%, transparent); }
  .card.approval.resolved { border-color: var(--line); }
  .ahead { display: flex; gap: 10px; align-items: flex-start; padding: 11px 12px 8px; }
  .ahead .aico { flex: none; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; background: color-mix(in srgb, var(--warn) 22%, transparent); color: color-mix(in srgb, var(--warn) 70%, var(--fg)); }
  .card.approval.resolved .aico { background: var(--soft2); color: var(--muted); }
  .ahead h4 { margin: 0; font-size: 1.04em; overflow-wrap: anywhere; }
  .ahead .asub { color: var(--muted); font-size: 0.9em; }
  .ahead .tool { margin-left: auto; flex: none; }
  .abody { padding: 0 12px 10px; }
  .cmdwrap { position: relative; }
  pre.cmd { margin: 0; padding: 9px 10px 9px 12px; background: var(--soft); border-left: 3px solid var(--warn); border-radius: 0 var(--r) var(--r) 0; font-family: var(--mono); font-size: 0.93em; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 16em; overflow: auto; }
  pre.cmd .ps { color: var(--muted); user-select: none; }
  .cmdwrap .copy { position: absolute; top: 3px; right: 3px; padding: 0 6px; font-size: 0.82em; background: var(--soft2); color: var(--fg); opacity: 0.8; }
  .cmdwrap .copy:hover { opacity: 1; }
  .kv { margin-top: 8px; display: grid; grid-template-columns: max-content 1fr; gap: 3px 12px; font-size: 0.92em; }
  .kv dt { color: var(--muted); }
  .kv dd { margin: 0; overflow-wrap: anywhere; }
  .why { margin-top: 9px; padding: 7px 10px; border-radius: var(--r); background: color-mix(in srgb, var(--warn) 12%, transparent); font-size: 0.93em; }
  .why b { font-weight: 600; }
  .safe { margin-top: 8px; font-size: 0.88em; color: var(--muted); }
  .aactions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; padding: 9px 12px; border-top: 1px solid var(--line); background: var(--soft); }
  .aactions .scope { margin-left: auto; font-size: 0.85em; color: var(--muted); }

  /* cards */
  .card { border: 1px solid var(--line); border-radius: 8px; margin: 14px 0; overflow: hidden; }
  .card.approval { border-color: var(--warn); }
  .card .pad { padding: 10px 12px; }
  .vhead { display: flex; gap: 10px; align-items: flex-start; padding: 12px; border-bottom: 1px solid var(--line); background: var(--soft); }
  .vicon { flex: none; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; color: var(--vscode-editor-background, #fff); }
  .good .vicon { background: var(--ok); } .caution .vicon { background: var(--warn); } .fail .vicon { background: var(--bad); }
  .vhead h4 { margin: 0; font-size: 1.04em; }
  .vsub { margin: 2px 0 0; color: var(--muted); font-size: 0.93em; }
  .sec { padding: 10px 12px; border-top: 1px solid var(--line); }
  .sec:first-of-type { border-top: 0; }
  .sec h5 { margin: 0 0 6px; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; display: flex; align-items: center; gap: 8px; }
  .sec h5 .grow { flex: 1; }
  .actions { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--line); background: var(--soft); }
  .actions .arow { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .actions .tools { justify-content: space-between; }
  .chips { display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
  .chip { display: inline-block; padding: 0 7px; border-radius: 9px; font-size: 0.8em; line-height: 1.65; background: var(--soft2); white-space: nowrap; }
  /* text is mixed toward the foreground so it stays readable on pale (light) and dark themes alike */
  .chip.ok { color: color-mix(in srgb, var(--ok) 62%, var(--fg)); background: color-mix(in srgb, var(--ok) 18%, transparent); }
  .chip.warn { color: color-mix(in srgb, var(--warn) 62%, var(--fg)); background: color-mix(in srgb, var(--warn) 18%, transparent); }
  .chip.bad { color: color-mix(in srgb, var(--bad) 62%, var(--fg)); background: color-mix(in srgb, var(--bad) 18%, transparent); }
  .chip.accent { color: color-mix(in srgb, var(--accent) 62%, var(--fg)); background: color-mix(in srgb, var(--accent) 18%, transparent); }
  button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: 1px solid transparent; padding: 4px 12px; cursor: pointer; border-radius: var(--r); }
  button:hover:not(:disabled) { background: var(--vscode-button-hoverBackground, var(--vscode-button-background)); }
  button.secondary { background: var(--vscode-button-secondaryBackground, var(--soft2)); color: var(--vscode-button-secondaryForeground, var(--fg)); }
  button.secondary:hover:not(:disabled) { background: var(--vscode-button-secondaryHoverBackground, var(--soft2)); }
  button.ghost { background: transparent; color: var(--accent); padding: 2px 6px; }
  button.ghost:hover:not(:disabled) { background: var(--soft); }
  button.danger { background: transparent; color: var(--bad); border-color: color-mix(in srgb, var(--bad) 55%, transparent); }
  button.danger:hover:not(:disabled) { background: color-mix(in srgb, var(--bad) 12%, transparent); }
  button:disabled { opacity: 0.5; cursor: default; }
  .notice { padding: 7px 10px; margin: 8px 0; border-radius: var(--r); white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--line); }
  .notice.error { background: var(--vscode-inputValidation-errorBackground); border-color: var(--vscode-inputValidation-errorBorder, var(--bad)); }
  .notice.warn { background: var(--vscode-inputValidation-warningBackground); border-color: var(--vscode-inputValidation-warningBorder, var(--warn)); }
  .notice.info { background: var(--vscode-inputValidation-infoBackground, var(--soft)); border-color: var(--vscode-inputValidation-infoBorder, var(--line)); }
  .muted { color: var(--muted); font-size: 0.92em; }
  ul { margin: 4px 0; padding-left: 18px; }
  details > summary { cursor: pointer; }

  /* checks */
  .check { display: flex; gap: 8px; padding: 3px 0; align-items: flex-start; }
  .check .mark { flex: none; width: 16px; height: 16px; border-radius: 50%; display: grid; place-items: center; font-size: 0.72em; font-weight: 700; margin-top: 3px; color: var(--vscode-editor-background, #fff); }
  .check.passed .mark { background: var(--ok); } .check.failed .mark { background: var(--bad); }
  .check.unavailable .mark, .check.skipped .mark { background: var(--warn); }
  .check .cmd { overflow-wrap: anywhere; }
  .check .cwhy { color: var(--muted); font-size: 0.9em; }

  /* file diffs */
  .sum { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .bar { display: inline-flex; gap: 1px; }
  .bar i { width: 7px; height: 7px; border-radius: 1px; background: var(--soft2); }
  .bar i.a { background: var(--ok); } .bar i.d { background: var(--bad); }
  details.filediff { border: 1px solid var(--line); border-radius: var(--r); margin: 6px 0; overflow: hidden; }
  details.filediff > summary { list-style: none; display: flex; gap: 8px; align-items: center; padding: 5px 8px; background: var(--soft); min-width: 0; }
  details.filediff > summary::-webkit-details-marker { display: none; }
  details.filediff > summary::before { content: "›"; flex: none; width: 10px; color: var(--muted); transition: transform 0.12s; }
  details.filediff[open] > summary::before { transform: rotate(90deg); }
  .kind { flex: none; font-size: 0.72em; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; padding: 0 5px; border-radius: 3px; }
  .kind.A { color: color-mix(in srgb, var(--ok) 62%, var(--fg)); background: color-mix(in srgb, var(--ok) 16%, transparent); }
  .kind.M { color: color-mix(in srgb, var(--warn) 62%, var(--fg)); background: color-mix(in srgb, var(--warn) 16%, transparent); }
  .kind.D { color: color-mix(in srgb, var(--bad) 62%, var(--fg)); background: color-mix(in srgb, var(--bad) 16%, transparent); }
  .fpath { flex: 1; min-width: 0; font-family: var(--mono); font-size: 0.9em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
  .fpath bdi { direction: ltr; unicode-bidi: isolate; }
  .fpath .dir { color: var(--muted); }
  .plus { color: var(--ok); } .minus { color: var(--bad); }
  .dbody { font-family: var(--mono); font-size: 0.88em; }
  .gap { padding: 1px 8px 1px 52px; color: var(--muted); font-size: 0.85em; background: var(--soft); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
  .dl { display: grid; grid-template-columns: 38px 16px 1fr; }
  .dl .ln { text-align: right; padding-right: 6px; color: var(--muted); opacity: 0.8; user-select: none; font-variant-numeric: tabular-nums; }
  .dl .sg { text-align: center; user-select: none; color: var(--muted); }
  .dl .tx { white-space: pre-wrap; overflow-wrap: anywhere; padding-right: 8px; min-width: 0; }
  .dl.add { background: var(--add-bg); } .dl.add .sg { color: var(--ok); }
  .dl.del { background: var(--del-bg); } .dl.del .sg { color: var(--bad); }
  .dl.note .tx { color: var(--muted); font-style: italic; }
  .dl .tx .w { border-radius: 2px; }
  .dl.add .tx .w { background: var(--add-word); } .dl.del .tx .w { background: var(--del-word); }
  .bininfo { padding: 8px 10px; font-family: var(--vscode-font-family); font-size: 1.02em; }
  .more { display: block; width: 100%; border-radius: 0; border-top: 1px solid var(--line); }
  .filetools { display: flex; gap: 4px; padding: 3px 6px; border-top: 1px solid var(--line); background: var(--soft); }

  /* history */
  #hist { display: none; flex: 1; overflow-y: auto; padding: 10px 12px; }
  #hist.on { display: block; }
  #histfilter { width: 100%; margin-bottom: 8px; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, var(--line)); border-radius: var(--r); padding: 5px 8px; }
  .sess { display: grid; grid-template-columns: 10px 1fr; gap: 0 9px; padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--r); margin: 5px 0; cursor: pointer; }
  .sess:hover, .sess:focus-visible { background: var(--soft); }
  .sess.current { border-color: var(--accent); }
  .sess .dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 7px; background: var(--muted); }
  .sess .dot.ok { background: var(--ok); } .sess .dot.warn { background: var(--warn); } .sess .dot.bad { background: var(--bad); } .sess .dot.accent { background: var(--accent); }
  .sess .t { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sess .m { grid-column: 2; display: flex; gap: 5px; flex-wrap: wrap; align-items: center; margin-top: 2px; color: var(--muted); font-size: 0.85em; }

  /* composer */
  #composer { position: relative; padding: 8px 10px 10px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  #selhint { display: none; align-items: center; gap: 6px; font-size: 0.85em; color: var(--muted); }
  #selhint.on { display: flex; }
  #selhint code { overflow-wrap: anywhere; color: var(--fg); }
  .box { border: 1px solid var(--vscode-input-border, var(--line)); border-radius: 8px; background: var(--vscode-input-background); }
  .box:focus-within { border-color: var(--vscode-focusBorder, var(--accent)); }
  textarea { display: block; width: 100%; resize: none; min-height: 4.6em; max-height: 40vh; background: transparent; color: var(--vscode-input-foreground); border: 0; padding: 8px 10px 2px; outline: none; }
  #bar { display: flex; align-items: center; gap: 8px; padding: 4px 6px 6px 8px; }
  #bar .hint { flex: 1; min-width: 0; font-size: 0.8em; color: var(--muted); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  select { background: transparent; color: var(--muted); border: 1px solid transparent; border-radius: var(--r); padding: 2px 4px; cursor: pointer; }
  select:hover { background: var(--soft); }
  select option { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); }
  #go { padding: 4px 14px; font-weight: 600; }
  #ac { display: none; position: absolute; left: 10px; right: 10px; bottom: 100%; max-height: 40vh; overflow-y: auto; z-index: 5; background: var(--vscode-editorSuggestWidget-background, var(--vscode-editorWidget-background)); border: 1px solid var(--vscode-editorSuggestWidget-border, var(--line)); border-radius: var(--r); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25); }
  #ac.on { display: block; }
  #ac .opt { padding: 4px 10px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #ac .opt.sel { background: var(--vscode-editorSuggestWidget-selectedBackground, var(--vscode-list-activeSelectionBackground)); color: var(--vscode-list-activeSelectionForeground, inherit); }
  #ac .base { font-weight: 600; }
  #ac .dir { opacity: 0.65; font-size: 0.9em; }

  /* empty state */
  .welcome h3 { margin: 4px 0 2px; font-size: 1.15em; }
  .welcome h5 { margin: 16px 0 6px; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }
  .welcome p { margin: 0 0 10px; }
  .suggest { display: flex; flex-direction: column; gap: 5px; }
  .suggest button { text-align: left; background: transparent; color: var(--fg); border: 1px solid var(--line); padding: 7px 10px; }
  .suggest button:hover { background: var(--soft); border-color: var(--accent); }
  kbd { font-family: var(--mono); font-size: 0.85em; padding: 0 5px; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; background: var(--soft); }
  .tips { margin-top: 14px; display: flex; flex-direction: column; gap: 4px; }

  @media (prefers-reduced-motion: reduce) { #status.busy::before, .st-run .sico { animation: none; } details.filediff > summary::before { transition: none; } }
</style></head>
<body>
  <div id="top">
    <div id="status" role="status" aria-live="polite">Ready</div>
    <button id="newbtn" class="tb" title="Start over with an empty view (earlier sessions stay in History)" aria-label="New session">+ New</button>
    <button id="histbtn" class="tb" title="Earlier sessions for this workspace" aria-label="Session history" aria-pressed="false">History</button>
  </div>
  <div id="sess" aria-label="This conversation"><span class="st" id="sesstitle"></span><button id="sessrename" class="ghost" title="Rename this conversation" aria-label="Rename this conversation">Rename</button><button id="sessstart" class="ghost" title="Put the conversation and workspace back to how they were at the start" aria-label="Go back to the start of this conversation">Back to start</button></div>
  <div id="stats" aria-label="Task statistics"></div>
  <div id="viewing"><span>Past session (read-only)</span><button id="viewnew" class="secondary">Start a new task</button></div>
  <div id="hist" aria-label="Session history"></div>
  <div id="logwrap"><div id="log" tabindex="0" role="log" aria-label="Conversation"></div><button id="latest" class="secondary" aria-label="Scroll to the latest output">↓ Latest</button></div>
  <div id="composer">
    <div id="ac" role="listbox" aria-label="Files"></div>
    <div id="selhint"><span>Selected:</span><code id="selref"></code><button id="seladd" class="secondary" title="Add this selection to the prompt (Alt+K)">Add</button></div>
    <div class="box" id="box">
      <div id="tray" aria-label="Attached images"></div>
      <div id="imgnote">Images are sent to the model provider as they are. Do not attach anything secret.</div>
      <textarea id="prompt" rows="3" aria-label="Task for the Harnix agent" aria-autocomplete="list" aria-controls="ac" placeholder="Describe a task, e.g. fix the failing test in @tests/test_parse.py…"></textarea>
      <div id="bar">
        <button id="attach" class="tb" title="Attach images (you can also paste with Ctrl+V or drop files here)" aria-label="Attach images">Image</button>
        <select id="think" aria-label="Thinking level" title="How much the model reasons before acting. Thinking = your setting (harnix.thinking)">
          <option value="default">Thinking</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">Extra high</option>
        </select>
        <span class="hint" title="Enter runs · Shift+Enter new line · @ attaches a file">v${version.replace(/[^0-9A-Za-z.+-]/g, "")}</span>
        <button id="go">Run</button>
      </div>
    </div>
  </div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log'), statusEl = document.getElementById('status'), statsEl = document.getElementById('stats');
  const input = document.getElementById('prompt'), go = document.getElementById('go');
  const ac = document.getElementById('ac'), selHint = document.getElementById('selhint'), selRef = document.getElementById('selref');
  const histEl = document.getElementById('hist'), histBtn = document.getElementById('histbtn'), viewing = document.getElementById('viewing');
  const think = document.getElementById('think');
  let busy = false, history = [], historyError = '', viewingId = '', showHistory = false, openStep = null;
  let sessionId = null, sessionTitle = '', sessionTurns = 0; // the conversation on screen: the next prompt continues it
  const restoreBars = [];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const logwrap = document.getElementById('logwrap'), latestBtn = document.getElementById('latest');
  // Follow the output while the reader is at the bottom; the moment they scroll up, leave the view alone
  // (so the whole conversation can be read while it is still running) and offer a way back.
  let following = true;
  const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 48;
  const toBottom = () => { log.scrollTop = log.scrollHeight; };
  // Only the reader's own scrolling (wheel, keys, touch, dragging the scrollbar) can switch following off or on.
  // A scroll event that the page itself causes (content growing, the log being cleared) says nothing about intent.
  let userUntil = 0, pointerDown = false;
  ['wheel', 'touchmove', 'keydown'].forEach((t) => log.addEventListener(t, () => { userUntil = performance.now() + 400; }, { passive: true }));
  log.addEventListener('pointerdown', () => { pointerDown = true; });
  window.addEventListener('pointerup', () => { if (pointerDown) { pointerDown = false; userUntil = performance.now() + 400; } });
  log.addEventListener('scroll', () => {
    if (pointerDown || performance.now() < userUntil) { following = atBottom(); latestBtn.classList.toggle('on', !following && busy); }
  });
  latestBtn.addEventListener('click', () => { following = true; toBottom(); latestBtn.classList.remove('on'); log.focus(); });
  let followQueued = false;
  new MutationObserver(() => {
    if (followQueued) return;
    followQueued = true;
    requestAnimationFrame(() => { followQueued = false; if (following) toBottom(); else latestBtn.classList.toggle('on', busy); });
  }).observe(log, { childList: true, subtree: true, characterData: true });
  const add = (n) => { log.appendChild(n); return n; };
  const button = (label, kind, onClick) => { const b = el('button', kind || '', label); b.addEventListener('click', onClick); return b; };
  const chip = (text, cls, title) => { const c = el('span', 'chip ' + (cls || ''), text); if (title) c.title = title; return c; };
  const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);

  // text with \`code\` spans, never as markup
  function richText(target, text) {
    text.split('\`').forEach((part, i) => { if (i % 2) target.appendChild(el('code', '', part)); else if (part) target.appendChild(document.createTextNode(part)); });
    return target;
  }

  // <md-parser>
  // A small Markdown reader for what a model writes: it produces plain data (no HTML is ever built from the text).
  function parseInline(s) {
    const out = [];
    let buf = '', i = 0;
    const flush = () => { if (buf) { out.push({ k: 't', v: buf }); buf = ''; } };
    const word = (ch) => /[A-Za-z0-9]/.test(ch || '');
    while (i < s.length) {
      const c = s[i];
      if (c === '\`') {
        let n = 1; while (s[i + n] === '\`') n++;
        const fence = '\`'.repeat(n), j = s.indexOf(fence, i + n);
        if (j > i + n - 1 && j !== -1) { flush(); out.push({ k: 'code', v: s.slice(i + n, j).replace(/^ (.*) $/, '$1') }); i = j + n; continue; }
      }
      if (c === '\\\\' && i + 1 < s.length && '\\\\\`*_{}[]()#+-.!~|>'.includes(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
      if ((s.startsWith('**', i) || s.startsWith('__', i)) && s[i + 2] && s[i + 2] !== ' ') {
        const m = s.substr(i, 2);
        if (!(m === '__' && word(s[i - 1]))) {
          let j = s.indexOf(m, i + 2);
          while (j !== -1 && s[j - 1] === ' ') j = s.indexOf(m, j + 1);
          if (j !== -1 && j > i + 2) { flush(); out.push({ k: 'b', c: parseInline(s.slice(i + 2, j)) }); i = j + 2; continue; }
        }
      }
      if (s.startsWith('~~', i) && s[i + 2] && s[i + 2] !== ' ') {
        const j = s.indexOf('~~', i + 2);
        if (j > i + 2) { flush(); out.push({ k: 'del', c: parseInline(s.slice(i + 2, j)) }); i = j + 2; continue; }
      }
      if ((c === '*' || c === '_') && s[i + 1] && s[i + 1] !== ' ' && s[i + 1] !== c && !(c === '_' && word(s[i - 1]))) {
        let j = i + 1, found = -1;
        while ((j = s.indexOf(c, j)) !== -1) {
          if (s[j - 1] !== ' ' && s[j + 1] !== c && !(c === '_' && word(s[j + 1]))) { found = j; break; }
          j++;
        }
        if (found > i + 1) { flush(); out.push({ k: 'i', c: parseInline(s.slice(i + 1, found)) }); i = found + 1; continue; }
      }
      if (c === '[') {
        const m = /^\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)/.exec(s.slice(i));
        if (m) { flush(); out.push({ k: 'link', c: parseInline(m[1]), href: m[2] }); i += m[0].length; continue; }
      }
      buf += c; i++;
    }
    flush();
    return out;
  }

  function parseMd(text) {
    const lines = String(text || '').replace(/\\r/g, '').split('\\n');
    const blocks = [];
    let i = 0;
    const isTableRow = (l) => /^\\s*\\|.*\\|\\s*$/.test(l);
    const isTableSep = (l) => /^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?\\s*$/.test(l) && l.includes('-');
    const cells = (l) => l.trim().replace(/^\\|/, '').replace(/\\|$/, '').split('|').map((x) => parseInline(x.trim()));
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = /^\\s*(\`{3,}|~{3,})\\s*([\\w+#.-]*)\\s*$/.exec(line);
      if (fence) {
        const body = []; i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) { body.push(lines[i]); i++; }
        i++; // the closing fence (a missing one just ends the block at the end of the text)
        blocks.push({ t: 'code', lang: fence[2] || '', text: body.join('\\n') });
        continue;
      }
      const h = /^(#{1,6})\\s+(.*?)\\s*#*\\s*$/.exec(line);
      if (h) { blocks.push({ t: 'h', level: Math.min(h[1].length, 4), c: parseInline(h[2]) }); i++; continue; }
      if (/^\\s*([-*_])(\\s*\\1){2,}\\s*$/.test(line)) { blocks.push({ t: 'hr' }); i++; continue; }
      if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const head = cells(line), rows = []; i += 2;
        while (i < lines.length && isTableRow(lines[i])) { rows.push(cells(lines[i])); i++; }
        blocks.push({ t: 'table', head, rows });
        continue;
      }
      if (/^\\s*>/.test(line)) {
        const q = [];
        while (i < lines.length && /^\\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\\s*>\\s?/, '')); i++; }
        blocks.push({ t: 'quote', blocks: parseMd(q.join('\\n')) });
        continue;
      }
      const li = /^(\\s*)([-*+]|\\d+[.)])\\s+(.*)$/.exec(line);
      if (li) {
        const ordered = /\\d/.test(li[2]), items = [];
        while (i < lines.length) {
          const m = /^(\\s*)([-*+]|\\d+[.)])\\s+(.*)$/.exec(lines[i]);
          if (m && /\\d/.test(m[2]) === ordered && m[1].length <= li[1].length + 1) { items.push({ c: parseInline(m[3]), sub: [] }); i++; continue; }
          if (m && m[1].length > li[1].length + 1 && items.length) { // a nested list
            const nested = [];
            while (i < lines.length && /^\\s+([-*+]|\\d+[.)])\\s+/.test(lines[i])) { nested.push(lines[i].replace(/^\\s{1,4}/, '')); i++; }
            items[items.length - 1].sub = parseMd(nested.join('\\n'));
            continue;
          }
          if (lines[i].trim() && /^\\s{2,}\\S/.test(lines[i]) && items.length && !m) { items[items.length - 1].c.push({ k: 't', v: ' ' }, ...parseInline(lines[i].trim())); i++; continue; } // a wrapped item
          break;
        }
        blocks.push({ t: ordered ? 'ol' : 'ul', start: ordered ? parseInt(li[2], 10) : 1, items });
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\\s*(\`{3,}|~{3,}|#{1,6}\\s|>|([-*+]|\\d+[.)])\\s)/.test(lines[i]) && !(isTableRow(lines[i]) && i + 1 < lines.length && isTableSep(lines[i + 1]))) { para.push(lines[i]); i++; }
      if (para.length) blocks.push({ t: 'p', c: parseInline(para.join('\\n')) });
      else { blocks.push({ t: 'p', c: parseInline(line) }); i++; }
    }
    return blocks;
  }
  // </md-parser>

  // <md-render>
  function renderInline(parent, nodes) {
    nodes.forEach((n) => {
      if (n.k === 't') parent.appendChild(document.createTextNode(n.v));
      else if (n.k === 'code') parent.appendChild(el('code', '', n.v));
      else if (n.k === 'b') renderInline(parent.appendChild(el('strong')), n.c);
      else if (n.k === 'i') renderInline(parent.appendChild(el('em')), n.c);
      else if (n.k === 'del') renderInline(parent.appendChild(el('del')), n.c);
      else if (n.k === 'link') { const a = el('span', 'mdlink'); a.title = n.href; renderInline(a, n.c); parent.appendChild(a); } // shown, never followed
    });
  }
  function renderBlocks(parent, blocks) {
    blocks.forEach((b) => {
      if (b.t === 'p') renderInline(parent.appendChild(el('p')), b.c);
      else if (b.t === 'h') renderInline(parent.appendChild(el('div', 'mdh mdh' + b.level)), b.c);
      else if (b.t === 'code') {
        const pre = el('pre', 'mdcode');
        if (b.lang) pre.appendChild(el('span', 'lang', b.lang));
        pre.appendChild(el('code', '', b.text));
        parent.appendChild(pre);
      } else if (b.t === 'ul' || b.t === 'ol') {
        const list = el(b.t);
        if (b.t === 'ol' && b.start !== 1) list.start = b.start;
        b.items.forEach((it) => { const li = el('li'); renderInline(li, it.c); if (it.sub && it.sub.length) renderBlocks(li, it.sub); list.appendChild(li); });
        parent.appendChild(list);
      } else if (b.t === 'quote') renderBlocks(parent.appendChild(el('blockquote')), b.blocks);
      else if (b.t === 'hr') parent.appendChild(el('hr'));
      else if (b.t === 'table') {
        const wrap = el('div', 'mdtable'), t = el('table'), head = el('tr');
        b.head.forEach((c) => renderInline(head.appendChild(el('th')), c));
        t.appendChild(head);
        b.rows.forEach((r) => { const tr = el('tr'); r.forEach((c) => renderInline(tr.appendChild(el('td')), c)); t.appendChild(tr); });
        wrap.appendChild(t); parent.appendChild(wrap);
      }
    });
  }
  function renderMarkdown(target, text) { renderBlocks(target, parseMd(text)); return target; }
  // </md-render>

  function setBusy(b) { busy = b; go.textContent = b ? 'Cancel' : 'Run'; go.className = b ? 'secondary' : ''; statusEl.classList.toggle('busy', b); }
  function setStatus(text, b) {
    statusEl.textContent = text; statusEl.title = text; setBusy(b);
    statusEl.classList.remove('bad', 'warn');
    if (/NOT verified|Cancelled|cancelled/.test(text)) statusEl.classList.add('warn');
    if (/^Failed|Failed to run|failed$/.test(text)) statusEl.classList.add('bad');
  }

  try { const st = vscode.getState(); if (st && st.think) think.value = st.think; } catch (e) { /* none */ }
  think.addEventListener('change', () => vscode.setState({ think: think.value }));

  // a clickable file name; the extension validates the path before opening anything
  function fileLink(text, onActivate) {
    const a = el('a', 'file', text);
    a.tabIndex = 0; a.setAttribute('role', 'link');
    a.addEventListener('click', onActivate);
    a.addEventListener('keydown', (e) => { if (e.key === 'Enter') onActivate(); });
    return a;
  }
  const openFile = (file, line) => vscode.postMessage({ type: 'open', file, line });

  // ---- history list and empty state ----
  const STATUS_CHIP = { completed: ['completed', 'ok'], failed: ['failed', 'bad'], cancelled: ['cancelled', 'warn'], running: ['running', 'accent'], queued: ['queued', 'accent'], awaiting_approval: ['needs approval', 'warn'], verifying: ['verifying', 'accent'] };
  function sessionRow(s) {
    const row = el('div', 'sess' + (s.id === sessionId ? ' current' : ''));
    row.tabIndex = 0; row.setAttribute('role', 'button'); row.setAttribute('aria-label', 'Open conversation: ' + s.title);
    const tone = s.status === 'completed' ? (s.outcome === 'verified' ? 'ok' : 'warn') : (STATUS_CHIP[s.status] || ['', ''])[1];
    row.appendChild(el('span', 'dot ' + tone));
    row.appendChild(el('div', 't', s.title));
    const m = el('div', 'm');
    m.appendChild(chip(s.turns + (s.turns === 1 ? ' message' : ' messages'), 'accent'));
    const sc = STATUS_CHIP[s.status] || [s.status || '', ''];
    if (s.status === 'completed') m.appendChild(s.outcome === 'verified' ? chip('verified', 'ok') : chip('not verified', 'warn'));
    else if (s.outcome === 'verification_failed') m.appendChild(chip('verification failed', 'bad'));
    else if (s.status) m.appendChild(chip(sc[0], sc[1]));
    m.appendChild(el('span', '', s.when));
    row.appendChild(m);
    const open = () => { showHistory = false; syncPanes(); vscode.postMessage({ type: 'openSession', sessionId: s.id }); };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    return row;
  }
  function renderHistory() {
    histEl.textContent = '';
    const filter = el('input'); filter.id = 'histfilter'; filter.type = 'search'; filter.placeholder = 'Filter sessions…'; filter.setAttribute('aria-label', 'Filter sessions'); filter.autocomplete = 'off'; filter.spellcheck = false;
    histEl.appendChild(filter);
    const list = el('div');
    histEl.appendChild(list);
    const draw = () => {
      list.textContent = '';
      const q = filter.value.trim().toLowerCase();
      const items = history.filter((s) => !q || s.title.toLowerCase().includes(q) || s.status.includes(q));
      if (historyError) list.appendChild(el('div', 'notice warn', historyError));
      else if (!history.length) list.appendChild(el('div', 'muted', 'No earlier sessions for this workspace yet. Run a task and it will appear here.'));
      else if (!items.length) list.appendChild(el('div', 'muted', 'No session matches the filter.'));
      items.forEach((s) => list.appendChild(sessionRow(s)));
    };
    filter.addEventListener('input', draw);
    draw();
  }
  const SUGGESTIONS = ['Fix the failing tests', 'Explain how this project is structured', 'Add tests for the selected code'];
  function welcome() {
    const w = el('div', 'welcome');
    w.appendChild(el('h3', '', 'What should Harnix do?'));
    w.appendChild(el('p', 'muted', 'Each task runs on an isolated copy of your workspace. Nothing is written to your files until you click Apply, and you can revert an apply later.'));
    const sug = el('div', 'suggest');
    SUGGESTIONS.forEach((t) => sug.appendChild(button(t, '', () => { input.value = t + (t.indexOf('selected') >= 0 ? ' ' : ''); input.focus(); input.setSelectionRange(input.value.length, input.value.length); })));
    w.appendChild(sug);
    if (history.length) {
      w.appendChild(el('h5', '', 'Recent sessions'));
      history.slice(0, 5).forEach((s) => w.appendChild(sessionRow(s)));
      if (history.length > 5) w.appendChild(button('All ' + history.length + ' sessions…', 'ghost', () => { showHistory = true; syncPanes(); }));
    }
    const tips = el('div', 'tips muted');
    const tip = (parts) => { const d = el('div'); parts.forEach((p) => d.appendChild(p.k ? el('kbd', '', p.k) : document.createTextNode(p.t))); return d; };
    tips.appendChild(tip([{ k: '@' }, { t: ' attach a file, or ' }, { k: '@path#10-20' }, { t: ' for lines' }]));
    tips.appendChild(tip([{ k: 'Alt' }, { t: ' + ' }, { k: 'K' }, { t: ' add the editor selection' }]));
    tips.appendChild(tip([{ t: 'Results are checked by the harness (tests, syntax), never by the model\\'s own claim.' }]));
    w.appendChild(tips);
    return w;
  }
  let emptyShown = true;
  function showWelcome() { log.textContent = ''; openStep = null; log.appendChild(welcome()); emptyShown = true; }
  const sessBar = document.getElementById('sess'), sessTitle = document.getElementById('sesstitle');
  function showSession(id, title, turns) {
    sessionId = id; sessionTitle = title || ''; sessionTurns = turns || 0;
    sessBar.className = id ? 'on' : '';
    sessTitle.textContent = '';
    const tt = el('span', 'stt', sessionTitle); tt.title = sessionTitle;
    sessTitle.appendChild(tt);
    sessTitle.appendChild(el('span', 'sn', sessionTurns + (sessionTurns === 1 ? ' message' : ' messages')));
    input.placeholder = id ? 'Reply… the agent remembers this whole conversation' : 'Describe a task, e.g. fix the failing test in @tests/test_parse.py…';
  }
  document.getElementById('sessrename').addEventListener('click', () => vscode.postMessage({ type: 'renameSession', turns: sessionTurns }));
  document.getElementById('sessstart').addEventListener('click', () => vscode.postMessage({ type: 'restoreSession', turn: 0 }));
  // "Restore to here" belongs on every response except the newest one
  function refreshRestore() { restoreBars.forEach((b, i) => { b.style.display = i === restoreBars.length - 1 ? 'none' : ''; }); }
  function userBubble(m) {
    const u = el('div', 'user' + (m.discarded ? ' discarded' : ''));
    if (m.turn && m.turn > 1) u.appendChild(el('span', 'who', 'Message ' + m.turn));
    u.appendChild(document.createTextNode(m.prompt));
    if (m.images && m.images.length) { const pics = el('div', 'pics'); m.images.forEach((p) => { const im = el('img'); im.src = p.src; im.alt = p.name; im.title = p.name + ' (click to enlarge)'; im.addEventListener('click', () => im.classList.toggle('big')); pics.appendChild(im); }); u.appendChild(pics); }
    return u;
  }
  function syncPanes() {
    histEl.className = showHistory ? 'on' : '';
    logwrap.style.display = showHistory ? 'none' : '';
    histBtn.setAttribute('aria-pressed', String(showHistory));
    if (showHistory) renderHistory();
    viewing.className = viewingId && !busy ? 'on' : '';
  }
  histBtn.addEventListener('click', () => { showHistory = !showHistory; if (showHistory) vscode.postMessage({ type: 'history' }); syncPanes(); });
  document.getElementById('newbtn').addEventListener('click', () => {
    if (busy) return;
    viewingId = ''; showHistory = false; statsEl.className = ''; statsEl.textContent = '';
    setStatus('Ready', false);
    showSession(null, '', 0); restoreBars.length = 0;
    vscode.postMessage({ type: 'newSession' });
    showWelcome(); syncPanes(); vscode.postMessage({ type: 'history' }); input.focus();
  });
  document.getElementById('viewnew').addEventListener('click', () => document.getElementById('newbtn').click());

  // ---- images the model should look at: paste, drop, or the Image button ----
  const MAX_IMAGES = 5, MAX_SIDE = 2048, MAX_BYTES = 4500000;
  const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
  const tray = document.getElementById('tray'), imgNote = document.getElementById('imgnote'), box = document.getElementById('box');
  let pending = [];
  function note(kind, text) { add(el('div', 'notice ' + kind, text)); }
  function renderTray() {
    tray.textContent = '';
    pending.forEach((p, i) => {
      const t = el('div', 'thumb');
      const img = el('img'); img.src = p.url; img.alt = p.name; img.title = p.name + ' (' + fmtSize(p.bytes) + ')';
      t.appendChild(img);
      const x = el('button', 'x', '×'); x.setAttribute('aria-label', 'Remove ' + p.name); x.addEventListener('click', () => { pending.splice(i, 1); renderTray(); input.focus(); });
      t.appendChild(x);
      t.appendChild(el('span', 'tn', p.name));
      tray.appendChild(t);
    });
    tray.className = pending.length ? 'on' : '';
    imgNote.className = pending.length ? 'on' : '';
  }
  function fmtSize(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
  // big screenshots are shrunk first: the model looks at no more than ~2000 px anyway, and the upload stays small
  function ingest(name, type, dataUrl) {
    return new Promise((resolve) => {
      if (IMAGE_TYPES.indexOf(type) < 0) { note('warn', name + ' is not a supported image (PNG, JPEG, GIF or WebP).'); resolve(); return; }
      if (pending.length >= MAX_IMAGES) { note('warn', 'At most ' + MAX_IMAGES + ' images can be attached to one task.'); resolve(); return; }
      const img = new Image();
      img.onload = () => {
        let url = dataUrl, mediaType = type;
        const big = Math.max(img.naturalWidth, img.naturalHeight) > MAX_SIDE;
        const heavy = Math.ceil((dataUrl.length - dataUrl.indexOf(',') - 1) * 3 / 4) > MAX_BYTES;
        if ((big || heavy) && type !== 'image/gif') {
          const k = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
          const c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          url = c.toDataURL(type === 'image/png' ? 'image/png' : 'image/jpeg', 0.92); mediaType = type === 'image/png' ? 'image/png' : 'image/jpeg';
          if (Math.ceil((url.length - url.indexOf(',') - 1) * 3 / 4) > MAX_BYTES) { url = c.toDataURL('image/jpeg', 0.85); mediaType = 'image/jpeg'; }
        }
        const bytes = Math.ceil((url.length - url.indexOf(',') - 1) * 3 / 4);
        if (bytes > MAX_BYTES) { note('warn', name + ' is too large even after shrinking (limit ' + fmtSize(MAX_BYTES) + ').'); resolve(); return; }
        if (pending.length >= MAX_IMAGES) { note('warn', 'At most ' + MAX_IMAGES + ' images can be attached to one task.'); resolve(); return; }
        pending.push({ name, mediaType, data: url.slice(url.indexOf(',') + 1), url, bytes });
        renderTray(); resolve();
      };
      img.onerror = () => { note('warn', name + ' could not be read as an image.'); resolve(); };
      img.src = dataUrl;
    });
  }
  function addFiles(files) {
    Array.from(files).forEach((f) => {
      const r = new FileReader();
      r.onload = () => ingest(f.name || ('pasted-image.' + (f.type.split('/')[1] || 'png')), f.type, String(r.result));
      r.onerror = () => note('error', 'Could not read ' + (f.name || 'the image') + '.');
      r.readAsDataURL(f);
    });
  }
  document.addEventListener('paste', (e) => {
    const items = e.clipboardData ? Array.from(e.clipboardData.items || []) : [];
    const files = items.filter((i) => i.kind === 'file' && i.type.indexOf('image/') === 0).map((i) => i.getAsFile()).filter(Boolean);
    if (!files.length) return; // plain text paste is left alone
    e.preventDefault();
    addFiles(files);
  });
  box.addEventListener('dragover', (e) => { if (e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf('Files') >= 0) { e.preventDefault(); box.classList.add('drop'); } });
  box.addEventListener('dragleave', () => box.classList.remove('drop'));
  box.addEventListener('drop', (e) => { box.classList.remove('drop'); if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } });
  document.getElementById('attach').addEventListener('click', () => vscode.postMessage({ type: 'pickImages' }));

  function submit() {
    if (busy) { vscode.postMessage({ type: 'cancel' }); return; }
    const prompt = input.value.trim();
    if (!prompt) { if (pending.length) note('info', 'Add a few words about what to do with the image' + (pending.length > 1 ? 's' : '') + ', then press Enter.'); return; }
    vscode.postMessage({ type: 'run', prompt, thinking: think.value, images: pending.map((p) => ({ name: p.name, mediaType: p.mediaType, data: p.data })) });
    input.value = '';
    pending = []; renderTray();
    closeAc();
  }
  go.addEventListener('click', submit);

  // ---- @ file autocomplete ----
  let acItems = [], acIndex = 0, acQuery = null;
  function currentMention() {
    const before = input.value.slice(0, input.selectionStart);
    const m = /(?:^|\\s)@([^\\s@#]*)$/.exec(before);
    return m ? { query: m[1], start: before.length - m[1].length - 1 } : null;
  }
  function closeAc() { ac.className = ''; ac.textContent = ''; acItems = []; acQuery = null; }
  function refreshAc() {
    const m = currentMention();
    if (!m) { closeAc(); return; }
    acQuery = m.query;
    vscode.postMessage({ type: 'files', query: m.query });
  }
  function markAc() {
    Array.from(ac.children).forEach((c, i) => { c.classList.toggle('sel', i === acIndex); c.setAttribute('aria-selected', String(i === acIndex)); });
    if (ac.children[acIndex]) ac.children[acIndex].scrollIntoView({ block: 'nearest' });
  }
  function acceptAc(i) {
    const m = currentMention(), f = acItems[i];
    if (!m || !f) return;
    const end = input.selectionStart;
    input.value = input.value.slice(0, m.start) + '@' + f + ' ' + input.value.slice(end);
    const pos = m.start + f.length + 2;
    input.setSelectionRange(pos, pos);
    closeAc();
    input.focus();
  }
  function renderAc(results) {
    ac.textContent = '';
    acItems = results; acIndex = 0;
    if (!results.length) { ac.className = ''; return; }
    results.forEach((f, i) => {
      const row = el('div', 'opt'); row.setAttribute('role', 'option'); row.title = f;
      const slash = f.lastIndexOf('/');
      row.appendChild(el('span', 'base', f.slice(slash + 1)));
      if (slash >= 0) row.appendChild(el('span', 'dir', '  ' + f.slice(0, slash)));
      row.addEventListener('mousedown', (e) => { e.preventDefault(); acceptAc(i); });
      ac.appendChild(row);
    });
    ac.className = 'on';
    markAc();
  }
  input.addEventListener('input', refreshAc);
  input.addEventListener('click', refreshAc);
  input.addEventListener('blur', closeAc);
  input.addEventListener('keydown', (e) => {
    if (ac.className === 'on') {
      if (e.key === 'ArrowDown') { e.preventDefault(); acIndex = (acIndex + 1) % acItems.length; markAc(); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); acIndex = (acIndex + acItems.length - 1) % acItems.length; markAc(); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); acceptAc(acIndex); return; }
      if (e.key === 'Escape') { e.preventDefault(); closeAc(); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });

  function insertAtCursor(text) {
    const s = input.selectionStart, e = input.selectionEnd, before = input.value.slice(0, s);
    const lead = before && !/\\s$/.test(before) ? ' ' : '';
    input.value = before + lead + text + ' ' + input.value.slice(e);
    const pos = (before + lead + text + ' ').length;
    input.setSelectionRange(pos, pos);
    input.focus();
  }
  document.getElementById('seladd').addEventListener('click', () => { if (selRef.textContent) insertAtCursor(selRef.textContent); });

  const approvals = new Map();
  const applyButtons = new Map(), revertButtons = new Map(), stateChips = new Map();

  // ---- diff rendering (shared by edit previews and the result card) ----
  // highlight only what changed inside a line: common start and end are left plain
  function wordSplit(a, b) {
    let p = 0; const max = Math.min(a.length, b.length);
    while (p < max && a[p] === b[p]) p++;
    let s = 0;
    while (s < max - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    if (p + s === 0 || (p + s < 2 && a.length > 6 && b.length > 6)) return null; // nothing shared worth showing
    return { pre: a.slice(0, p), a: a.slice(p, a.length - s), b: b.slice(p, b.length - s), post: a.slice(a.length - s) };
  }
  function lineText(target, text, mark) {
    if (!mark || (mark.mid === '' )) { target.textContent = text === '' ? ' ' : text; return; }
    if (mark.pre) target.appendChild(document.createTextNode(mark.pre));
    target.appendChild(el('span', 'w', mark.mid));
    if (mark.post) target.appendChild(document.createTextNode(mark.post));
  }
  // rows: [{kind:'add'|'del'|'ctx'|'note', no, text}]; pairs a run of deletions with the additions that follow it
  function diffRows(rows) {
    const out = [];
    let i = 0;
    while (i < rows.length) {
      if (rows[i].kind !== 'del') { out.push({ r: rows[i] }); i++; continue; }
      let j = i; while (j < rows.length && rows[j].kind === 'del') j++;
      let k = j; while (k < rows.length && rows[k].kind === 'add') k++;
      const dels = rows.slice(i, j), adds = rows.slice(j, k);
      const pair = dels.length === adds.length;
      dels.forEach((d, n) => { const w = pair ? wordSplit(d.text, adds[n].text) : null; out.push({ r: d, mark: w ? { pre: w.pre, mid: w.a, post: w.post } : null }); });
      adds.forEach((a, n) => { const w = pair ? wordSplit(dels[n].text, a.text) : null; out.push({ r: a, mark: w ? { pre: w.pre, mid: w.b, post: w.post } : null }); });
      i = k;
    }
    return out;
  }
  function diffLine(r, mark) {
    const row = el('div', 'dl ' + r.kind);
    row.appendChild(el('span', 'ln', r.no != null ? String(r.no) : ''));
    row.appendChild(el('span', 'sg', r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ''));
    const tx = el('span', 'tx');
    lineText(tx, r.text, mark);
    row.appendChild(tx);
    return row;
  }
  function renderRows(container, rows, limit) {
    const lines = diffRows(rows);
    const shown = limit && lines.length > limit + 10 ? limit : lines.length;
    lines.slice(0, shown).forEach((x) => container.appendChild(diffLine(x.r, x.mark)));
    if (shown < lines.length) {
      const more = button('Show ' + plural(lines.length - shown, 'more line', 'more lines'), 'ghost more', () => {
        more.remove();
        lines.slice(shown).forEach((x) => container.appendChild(diffLine(x.r, x.mark)));
      });
      container.appendChild(more);
    }
  }
  // the small before/after the agent proposed for one edit (inside a step)
  function editPreview(df) {
    const oldLines = df.old ? df.old.split('\\n') : [], newLines = df.new ? df.new.split('\\n') : [];
    const rows = [];
    oldLines.forEach((t) => rows.push({ kind: t.indexOf('… (') === 0 ? 'note' : 'del', text: t }));
    newLines.forEach((t) => rows.push({ kind: t.indexOf('… (') === 0 ? 'note' : 'add', text: t }));
    const box = el('div', 'dbody');
    box.style.border = '1px solid var(--line)'; box.style.borderRadius = 'var(--r)'; box.style.overflow = 'hidden'; box.style.margin = '4px 0';
    renderRows(box, rows, 30);
    return box;
  }

  // ---- timeline: a tool call and its results form one step ----
  function splitTool(text) {
    const i = text.indexOf(' ');
    return i < 0 ? { name: text, args: '' } : { name: text.slice(0, i), args: text.slice(i + 1) };
  }
  function closeStep() {
    if (!openStep) return;
    if (openStep.state === 'run') { openStep.el.classList.remove('st-run'); openStep.el.classList.add('st-ok'); openStep.ico.textContent = '✓'; } // no failure was reported: it finished
    openStep = null;
  }
  function startStep(item) {
    closeStep();
    const t = splitTool(item.text);
    const d = el('details', 'step st-run');
    const sum = el('summary');
    const ico = el('span', 'sico', '');
    sum.appendChild(ico);
    sum.appendChild(el('span', 'sname', t.name));
    const args = el('span', 'sargs', t.args);
    if (item.file) { args.textContent = ''; args.appendChild(fileLink(item.file, (e) => { e.preventDefault(); openFile(item.file, item.line); })); const rest = t.args.replace(item.file, '').replace(/^path=/, '').trim(); if (rest) args.appendChild(document.createTextNode(' ' + rest)); }
    args.title = t.args;
    sum.appendChild(args);
    const meta = el('span', 'smeta', '');
    sum.appendChild(meta);
    d.appendChild(sum);
    const body = el('div', 'sbody');
    d.appendChild(body);
    if (item.diff) { body.appendChild(editPreview(item.diff)); d.open = true; }
    if (item.detail) body.appendChild(el('pre', 'detail', item.detail));
    openStep = { el: d, ico, meta, body, state: 'run', hasBody: !!item.diff || !!item.detail };
    return add(d);
  }
  function stepResult(item) {
    const s = openStep;
    if (/^Needs your approval/.test(item.text)) { s.meta.textContent = 'waiting for your approval'; s.meta.title = item.text; return; } // the approval card below says it all
    const tone = item.kind === 'ok' ? 'ok' : item.kind === 'warn' ? 'warn' : 'bad';
    s.state = tone;
    s.el.classList.remove('st-run', 'st-ok', 'st-warn', 'st-bad');
    s.el.classList.add('st-' + tone);
    s.ico.textContent = tone === 'ok' ? '✓' : tone === 'warn' ? '!' : '✕';
    s.meta.textContent = item.text.split('\\n')[0];
    s.meta.title = item.text;
    const ln = el('div', 'line ' + item.kind);
    richText(ln, item.text);
    s.body.appendChild(ln);
    if (item.detail) s.body.appendChild(el('pre', 'detail', item.detail));
    if (item.diff) s.body.appendChild(editPreview(item.diff));
    if (tone !== 'ok' || item.diff) s.el.open = true;
  }
  function itemNode(item) {
    if (item.kind === 'tool') { startStep(item); return null; }
    if (openStep && (item.kind === 'ok' || item.kind === 'warn' || item.kind === 'error')) { stepResult(item); return null; }
    closeStep();
    if (item.kind === 'agent') {
      const d = el('div', 'say md');
      renderMarkdown(d, item.text);
      return d;
    }
    const d = el('div', 'line ' + item.kind);
    const i = item.file ? item.text.indexOf(item.file) : -1;
    if (i >= 0) {
      d.appendChild(document.createTextNode(item.text.slice(0, i)));
      d.appendChild(fileLink(item.file, () => openFile(item.file, item.line)));
      d.appendChild(document.createTextNode(item.text.slice(i + item.file.length)));
    } else {
      richText(d, item.text);
    }
    if (item.diff) d.appendChild(editPreview(item.diff));
    if (item.detail) d.appendChild(el('pre', 'detail', item.detail));
    return d;
  }

  // ---- result card ----
  function activityNode(a, r) {
    const changedByHarnix = new Set(r.files.map((f) => f.path));
    const d = el('details');
    const parts = ['read ' + a.read.length, 'changed ' + a.changed.length, 'ran ' + a.commands.length];
    if (a.approved || a.rejected) parts.push('approvals ' + a.approved + '/' + (a.approved + a.rejected));
    if (a.refused) parts.push('refused ' + a.refused);
    if (a.failures) parts.push('failed ' + a.failures);
    d.appendChild(el('summary', 'muted', 'Activity: ' + parts.join(', ')));
    const section = (label, nodes) => {
      if (!nodes.length) return;
      d.appendChild(el('div', 'muted', label));
      const ul = el('ul');
      nodes.forEach((n) => { const li = el('li'); li.appendChild(n); ul.appendChild(li); });
      d.appendChild(ul);
    };
    section('Files read', a.read.map((f) => fileLink(f, () => openFile(f))));
    section('Files changed (click for a side-by-side diff)', a.changed.map((f) => fileLink(f, () => (changedByHarnix.has(f) ? vscode.postMessage({ type: 'fileDiff', taskId: r.taskId, file: f }) : openFile(f)))));
    section('Commands run', a.commands.map((c) => el('code', '', c.command + (c.exit === null ? '' : '  → exit ' + c.exit))));
    if (a.approved || a.rejected) d.appendChild(el('div', 'muted', 'Approvals: ' + a.approved + ' approved, ' + a.rejected + ' rejected'));
    return d;
  }

  const CHECK_MARK = { passed: '✓', failed: '✕', unavailable: '–', skipped: '–' };
  const KIND_LABEL = { test: 'Tests', agent_test: 'Agent-written tests', syntax: 'Syntax', lint: 'Lint', typecheck: 'Types', acceptance: 'Your check', criterion: 'Criterion', build: 'Build' };
  function checkRow(c, dim) {
    const row = el('div', 'check ' + c.status);
    if (dim) row.style.opacity = '0.6';
    row.appendChild(el('span', 'mark', CHECK_MARK[c.status] || '?'));
    const body = el('div');
    const top = el('div');
    top.appendChild(chip(KIND_LABEL[c.kind] || c.kind, c.status === 'failed' ? 'bad' : c.status === 'passed' ? 'ok' : 'warn'));
    top.appendChild(document.createTextNode(' '));
    top.appendChild(el('code', 'cmd', c.command || '(none)'));
    body.appendChild(top);
    const why = c.status === 'passed' ? (c.kind === 'agent_test' ? 'Passed. The agent wrote these tests, so this is not independent evidence.' : 'Passed')
      : (c.status === 'failed' ? 'Failed' + (c.exitCode != null ? ' (exit ' + c.exitCode + ')' : '') : (c.status === 'unavailable' ? 'Could not run' : 'Skipped')) + (c.note ? ': ' + c.note : '');
    body.appendChild(el('div', 'cwhy', why));
    row.appendChild(body);
    return row;
  }
  function checksSection(r) {
    const finals = (r.checks || []).filter((c) => c.phase === 'final');
    const before = (r.checks || []).filter((c) => c.phase !== 'final');
    if (!finals.length && !before.length) return null;
    const sec = el('div', 'sec');
    sec.appendChild(el('h5', '', 'Independent checks'));
    finals.forEach((c) => sec.appendChild(checkRow(c, false)));
    if (!finals.length) sec.appendChild(el('div', 'muted', 'No checks ran at the end of this task.'));
    if (before.length) {
      const d = el('details');
      d.appendChild(el('summary', 'muted', 'Before the task started (' + before.length + ')'));
      before.forEach((c) => d.appendChild(checkRow(c, true)));
      sec.appendChild(d);
    }
    return sec;
  }

  const KIND_WORD = { A: 'New', M: 'Edited', D: 'Deleted' };
  function fmtBytes(n) {
    if (n == null) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 102400 ? 1 : 0) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }
  function binaryKind(path) {
    const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
    const map = { png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', ico: 'image', bmp: 'image', pdf: 'document', zip: 'archive', gz: 'archive', tar: 'archive', woff: 'font', woff2: 'font', ttf: 'font', otf: 'font', mp3: 'audio', mp4: 'video', wasm: 'binary module', pyc: 'compiled Python', so: 'library', dll: 'library', exe: 'program', db: 'database', sqlite: 'database' };
    return map[ext] || 'binary';
  }
  function binaryInfo(fd) {
    const kind = binaryKind(fd.path);
    if (fd.status === 'A') return 'New ' + kind + ' file' + (fd.sizeAfter != null ? ' · ' + fmtBytes(fd.sizeAfter) : '');
    if (fd.status === 'D') return 'Deleted ' + kind + ' file' + (fd.sizeBefore != null ? ' · was ' + fmtBytes(fd.sizeBefore) : '');
    if (fd.sizeBefore != null && fd.sizeAfter != null) {
      const d = fd.sizeAfter - fd.sizeBefore;
      return 'Changed ' + kind + ' file · ' + fmtBytes(fd.sizeBefore) + ' → ' + fmtBytes(fd.sizeAfter) + (d === 0 ? ' (same size)' : ' (' + (d > 0 ? '+' : '−') + fmtBytes(Math.abs(d)) + ')');
    }
    return 'Changed ' + kind + ' file';
  }
  function statBar(add, del) {
    const bar = el('span', 'bar'); bar.setAttribute('aria-hidden', 'true');
    const total = add + del;
    const cells = 5, a = total ? Math.round(cells * add / total) : 0;
    for (let i = 0; i < cells; i++) bar.appendChild(el('i', total === 0 ? '' : (i < a ? 'a' : 'd')));
    return bar;
  }
  function fileDiffNode(fd, r, open) {
    const det = el('details', 'filediff');
    if (open) det.open = true;
    const sum = el('summary');
    sum.appendChild(el('span', 'kind ' + fd.status, KIND_WORD[fd.status] || fd.status));
    const slash = fd.path.lastIndexOf('/');
    const fp = el('span', 'fpath');
    fp.title = fd.path;
    const bdi = el('bdi');
    if (slash >= 0) bdi.appendChild(el('span', 'dir', fd.path.slice(0, slash + 1)));
    bdi.appendChild(document.createTextNode(fd.path.slice(slash + 1)));
    fp.appendChild(bdi);
    sum.appendChild(fp);
    if (fd.binary) sum.appendChild(chip('binary', ''));
    else { sum.appendChild(el('span', 'plus num', '+' + fd.additions)); sum.appendChild(el('span', 'minus num', '−' + fd.deletions)); }
    det.appendChild(sum);
    const body = el('div', 'dbody');
    det.appendChild(body);
    if (fd.binary) {
      det.open = true;
      const info = el('div', 'bininfo');
      info.appendChild(el('div', '', binaryInfo(fd)));
      info.appendChild(el('div', 'muted', 'The content is not shown for binary files. Use Open file to look at it' + (fd.status === 'A' ? ' after you apply the change.' : '.')));
      body.appendChild(info);
    }
    let prevEnd = null;
    fd.hunks.forEach((h) => {
      const rows = h.lines.map((l) => ({ kind: l.kind, no: l.kind === 'del' ? l.oldNo : l.newNo, text: l.text }));
      const first = h.lines.find((l) => l.newNo != null || l.oldNo != null);
      const start = first ? (first.newNo != null ? first.newNo : first.oldNo) : null;
      if (prevEnd !== null && start !== null && start > prevEnd + 1) body.appendChild(el('div', 'gap', '··· ' + plural(start - prevEnd - 1, 'unchanged line', 'unchanged lines') + ' ···'));
      else if (prevEnd === null && start !== null && start > 1 && fd.status === 'M') body.appendChild(el('div', 'gap', '··· from line ' + start + ' ···'));
      renderRows(body, rows, fd.status === 'A' ? 14 : 70);
      const last = [...h.lines].reverse().find((l) => l.newNo != null);
      if (last) prevEnd = last.newNo;
    });
    if (fd.truncated) body.appendChild(el('div', 'pad muted', 'Large change: only the first part is shown here. Use Review changes for all of it.'));
    const tools = el('div', 'filetools');
    if (!fd.binary) tools.appendChild(button('Side-by-side', 'ghost', () => vscode.postMessage({ type: 'fileDiff', taskId: r.taskId, file: fd.path })));
    if (fd.status !== 'D') tools.appendChild(button('Open file', 'ghost', () => openFile(fd.path)));
    det.appendChild(tools);
    return det;
  }
  function changesSection(r) {
    const sec = el('div', 'sec');
    const adds = (r.diffs || []).reduce((n, f) => n + f.additions, 0), dels = (r.diffs || []).reduce((n, f) => n + f.deletions, 0);
    const h = el('h5', '');
    h.appendChild(document.createTextNode(plural(r.files.length, 'file', 'files') + ' changed'));
    h.appendChild(el('span', 'grow'));
    if (r.diffs && r.diffs.length) { h.appendChild(el('span', 'plus num', '+' + adds)); h.appendChild(el('span', 'minus num', '−' + dels)); h.appendChild(statBar(adds, dels)); }
    sec.appendChild(h);
    if (r.diffs && r.diffs.length) {
      const openAll = r.diffs.length <= 2 && adds + dels <= 40;
      r.diffs.forEach((fd) => sec.appendChild(fileDiffNode(fd, r, openAll)));
    } else {
      const ul = el('ul');
      r.files.forEach((f) => {
        const li = el('li');
        li.appendChild(document.createTextNode((KIND_WORD[f.status] || f.status) + ' '));
        li.appendChild(fileLink(f.path, () => vscode.postMessage({ type: 'fileDiff', taskId: r.taskId, file: f.path })));
        li.appendChild(document.createTextNode('  +' + f.additions + ' −' + f.deletions));
        ul.appendChild(li);
      });
      sec.appendChild(ul);
    }
    return sec;
  }

  function resultCard(r) {
    const completed = r.status === 'completed';
    const tone = completed ? (r.verified ? 'good' : 'caution') : r.status === 'cancelled' ? 'caution' : 'fail';
    const card = el('div', 'card');
    const head = el('div', 'vhead ' + tone);
    head.appendChild(el('span', 'vicon', tone === 'good' ? '✓' : tone === 'caution' ? '!' : '✕'));
    const hd = el('div');
    const title = completed ? (r.verified ? 'Completed: verified by independent checks' : 'Completed, but NOT verified') : (r.status === 'cancelled' ? 'Cancelled' : 'Failed');
    hd.appendChild(el('h4', '', title));
    let sub = '';
    if (r.falseClaim) sub = 'The agent claimed success, but independent verification failed.';
    else if (completed && r.verified) sub = 'The repository\\'s own checks passed after the change.';
    else if (completed) sub = 'No test or acceptance check confirmed this result. Review the changes yourself.';
    else if (r.error) sub = r.error;
    if (sub) hd.appendChild(el('p', 'vsub', sub));
    const chips = el('div', 'chips'); chips.style.marginTop = '6px';
    r.stats.split(' · ').forEach((p) => chips.appendChild(chip(p)));
    hd.appendChild(chips);
    head.appendChild(hd);
    card.appendChild(head);
    const checks = checksSection(r);
    if (checks) card.appendChild(checks);
    if (r.files.length) card.appendChild(changesSection(r));
    const more = el('div', 'sec');
    let any = false;
    if (r.activity) { more.appendChild(activityNode(r.activity, r)); any = true; }
    if (r.limitations.length) { const d = el('details'); d.appendChild(el('summary', 'muted', 'Limitations (' + r.limitations.length + ')')); const ul = el('ul'); r.limitations.slice(0, 6).forEach((l) => ul.appendChild(el('li', 'muted', l))); d.appendChild(ul); more.appendChild(d); any = true; }
    if (r.summary) { const d = el('details'); d.appendChild(el('summary', 'muted', "Agent's summary (its own claim, not evidence)")); const t = el('div', 'say md'); renderMarkdown(t, r.summary); d.appendChild(t); more.appendChild(d); any = true; }
    if (any) card.appendChild(more);
    if (r.files.length) {
      const row = el('div', 'actions');
      const main = el('div', 'arow'), tools = el('div', 'arow tools');
      row.append(main, tools);
      main.appendChild(button('Review changes', '', () => vscode.postMessage({ type: 'review', taskId: r.taskId })));
      if (completed) {
        const ab = button('Apply to workspace…', 'secondary', () => vscode.postMessage({ type: 'apply', taskId: r.taskId }));
        const rb = button('Revert applied changes…', 'danger', () => vscode.postMessage({ type: 'revert', taskId: r.taskId }));
        rb.title = 'Put your files back exactly as they were before this task was applied (refused if you edited them since)';
        const sc = el('span');
        applyButtons.set(r.taskId, ab); revertButtons.set(r.taskId, rb); stateChips.set(r.taskId, sc);
        setApplyState(r.taskId, r.applyState);
        main.append(ab, rb);
        tools.appendChild(sc);
      } else {
        tools.appendChild(el('span'));
      }
      const links = el('span', 'arow');
      links.appendChild(button('Raw patch', 'ghost', () => vscode.postMessage({ type: 'diff', taskId: r.taskId })));
      links.appendChild(button('Copy patch', 'ghost', () => vscode.postMessage({ type: 'copyDiff', taskId: r.taskId })));
      tools.appendChild(links);
      card.appendChild(row);
    }
    if (r.canRestore && r.sessionId) {
      const bar = el('div', 'restorebar');
      const rb2 = button('Restore to here', 'ghost', () => vscode.postMessage({ type: 'restoreSession', turn: r.turn }));
      rb2.title = 'Put the conversation and the workspace back to how they were right after this response (your real files are not touched)';
      bar.appendChild(rb2);
      card.appendChild(bar);
      restoreBars.push(bar);
    }
    return card;
  }
  function setApplyState(taskId, state) {
    const ab = applyButtons.get(taskId), rb = revertButtons.get(taskId), sc = stateChips.get(taskId);
    if (!ab || !rb || !sc) return;
    const applied = state === 'applied';
    ab.disabled = applied; ab.textContent = applied ? 'Applied' : (state === 'reverted' ? 'Apply again…' : 'Apply to workspace…');
    rb.style.display = applied ? '' : 'none';
    sc.textContent = '';
    if (applied) sc.appendChild(chip('in your workspace', 'accent'));
    if (state === 'reverted') sc.appendChild(chip('reverted', ''));
  }

  function renderStats(text) {
    statsEl.textContent = '';
    text.split(' · ').forEach((p) => statsEl.appendChild(el('span', 'num', p)));
    statsEl.className = 'on';
  }

  // ---- approval request: say what will happen in plain words, then show the exact action ----
  const TOOL_TITLE = { run_command: 'Run this command?', run_tests: 'Run the tests?', edit_file: 'Edit this file?', write_file: 'Write this file?', delete_file: 'Delete this file?', remember: 'Save a project memory note?', spawn_agent: 'Start a sub-agent?' };
  function kvList(pairs) {
    const dl = el('dl', 'kv');
    pairs.forEach((p) => { if (p[1] === null || p[1] === undefined || p[1] === '' || p[1] === false) return; dl.appendChild(el('dt', '', p[0])); const dd = el('dd'); if (p[2]) dd.appendChild(el('code', '', String(p[1]))); else dd.textContent = String(p[1]); dl.appendChild(dd); });
    return dl.children.length ? dl : null;
  }
  function cmdBlock(command, taskTool) {
    const wrap = el('div', 'cmdwrap');
    const pre = el('pre', 'cmd');
    pre.appendChild(el('span', 'ps', '$ '));
    pre.appendChild(document.createTextNode(command));
    wrap.appendChild(pre);
    const cp = button('Copy', 'copy', () => { vscode.postMessage({ type: 'copyText', text: command }); cp.textContent = 'Copied'; setTimeout(() => { cp.textContent = 'Copy'; }, 1500); });
    cp.setAttribute('aria-label', 'Copy the command');
    wrap.appendChild(cp);
    return wrap;
  }
  function approvalBody(a, body) {
    const g = a.arguments || {};
    if (a.tool === 'run_command') {
      body.appendChild(cmdBlock(String(g.command || '')));
      const kv = kvList([['Where', !g.workdir || g.workdir === '.' ? 'project root' : g.workdir, true], ['Time limit', g.timeout_seconds ? g.timeout_seconds + ' s' : null]]);
      if (kv) body.appendChild(kv);
    } else if (a.tool === 'run_tests') {
      body.appendChild(cmdBlock(String(g.command || 'the project\\'s detected test command')));
    } else if (a.tool === 'edit_file') {
      const f = el('div'); f.style.margin = '0 0 6px'; f.appendChild(document.createTextNode('In ')); f.appendChild(el('code', '', String(g.path || ''))); if (g.replace_all) f.appendChild(document.createTextNode(' (every occurrence)')); body.appendChild(f);
      body.appendChild(editPreview({ path: g.path, old: String(g.old_text || ''), new: String(g.new_text || '') }));
    } else if (a.tool === 'write_file') {
      const f = el('div'); f.style.margin = '0 0 6px'; f.appendChild(document.createTextNode('Creates or overwrites ')); f.appendChild(el('code', '', String(g.path || ''))); body.appendChild(f);
      const lines = String(g.content || '').split('\\n');
      body.appendChild(editPreview({ path: g.path, old: '', new: lines.slice(0, 40).join('\\n') + (lines.length > 40 ? '\\n… (' + (lines.length - 40) + ' more lines)' : ''), newFile: true }));
    } else if (a.tool === 'delete_file') {
      const f = el('div'); f.appendChild(document.createTextNode('Deletes ')); f.appendChild(el('code', '', String(g.path || ''))); body.appendChild(f);
    } else if (a.tool === 'remember') {
      const pre = el('pre', 'cmd'); pre.textContent = String(g.note || ''); body.appendChild(pre);
    } else if (a.tool === 'spawn_agent') {
      const kv = kvList([['Agent', g.agent, true], ['Task', g.description], ['Runs in the background', g.background ? 'yes' : null]]);
      if (kv) body.appendChild(kv);
      if (g.prompt) { const d = el('details'); d.appendChild(el('summary', 'muted', 'Its full instructions')); d.appendChild(el('pre', 'detail', String(g.prompt))); body.appendChild(d); }
    } else {
      const kv = kvList(Object.keys(g).map((k) => [k, typeof g[k] === 'string' ? g[k] : JSON.stringify(g[k]), true]));
      if (kv) body.appendChild(kv);
    }
  }
  function approvalCard(a) {
    const card = el('div', 'card approval');
    const head = el('div', 'ahead');
    head.appendChild(el('span', 'aico', '!'));
    const t = el('div');
    t.appendChild(el('h4', '', TOOL_TITLE[a.tool] || ('Allow ' + a.tool + '?')));
    t.appendChild(el('div', 'asub', 'Waiting for you'));
    head.appendChild(t);
    head.appendChild(chip(a.tool, 'warn'));
    card.appendChild(head);
    const body = el('div', 'abody');
    approvalBody(a, body);
    const why = el('div', 'why');
    why.appendChild(el('b', '', 'Why you are asked: '));
    why.appendChild(document.createTextNode(a.reason));
    body.appendChild(why);
    body.appendChild(el('div', 'safe', 'Allows only this exact action, once. Your real files are untouched until you Apply.'));
    card.appendChild(body);
    const row = el('div', 'aactions');
    const yes = button('Approve once', '', () => vscode.postMessage({ type: 'approval', id: a.id, approve: true }));
    const no = button('Reject', 'secondary', () => vscode.postMessage({ type: 'approval', id: a.id, approve: false }));
    row.append(yes, no);
    card.appendChild(row);
    return { card, row };
  }

  window.addEventListener('message', (event) => {
    const m = event.data;
    switch (m.type) {
      case 'reset': log.textContent = ''; restoreBars.length = 0; openStep = null; emptyShown = false; viewingId = ''; showHistory = false; statsEl.className = ''; statsEl.textContent = ''; following = true; latestBtn.classList.remove('on'); add(userBubble(m)); syncPanes(); break;
      case 'turn': closeStep(); emptyShown = false; showHistory = false; following = true; latestBtn.classList.remove('on'); add(userBubble(m)); syncPanes(); break;
      case 'clear': log.textContent = ''; restoreBars.length = 0; openStep = null; emptyShown = false; showHistory = false; statsEl.className = ''; statsEl.textContent = ''; following = true; syncPanes(); break;
      case 'sessionInfo': showSession(m.id, m.title, m.turns); break;
      case 'images': (m.items || []).reduce((chain, it) => chain.then(() => ingest(it.name, it.mediaType, 'data:' + it.mediaType + ';base64,' + it.data)), Promise.resolve()); break;
      case 'status': setStatus(m.text, m.busy); if (m.busy) { viewingId = ''; } syncPanes(); break;
      case 'stats': renderStats(m.text); break;
      case 'item': { const n = itemNode(m.item); if (n) add(n); break; }
      case 'notice': closeStep(); add(el('div', 'notice ' + m.kind, m.text)); break;
      case 'result': closeStep(); add(resultCard(m.result)); refreshRestore(); break;
      case 'restore': if (!input.value) input.value = m.prompt; break;
      case 'applied': setApplyState(m.taskId, 'applied'); break;
      case 'reverted': setApplyState(m.taskId, 'reverted'); break;
      case 'history': {
        const now = Date.now();
        history = (m.items || []).map((s) => Object.assign({}, s, { when: relTime(s.createdAt, now) }));
        historyError = m.error || '';
        if (showHistory) renderHistory();
        if (emptyShown && !busy) showWelcome();
        break;
      }
      case 'files': if (m.query === acQuery) renderAc(m.results); break;
      case 'selection': selRef.textContent = m.ref || ''; selHint.className = m.ref ? 'on' : ''; break;
      case 'insert': insertAtCursor(m.text); break;
      case 'approval': {
        const c = approvalCard(m.approval); // the step stays open: its result arrives after the decision
        approvals.set(m.approval.id, c);
        add(c.card); break;
      }
      case 'approvalResolved': {
        const a = approvals.get(m.id);
        if (a) {
          a.card.classList.add('resolved');
          const ico = a.card.querySelector('.aico'); if (ico) ico.textContent = m.approved ? '✓' : '✕';
          const sub = a.card.querySelector('.asub'); if (sub) sub.textContent = m.approved ? 'Approved once. Harnix continued.' : 'Rejected. Harnix was told not to do this.';
          const why = a.card.querySelector('.why'); if (why) why.remove();
          const safe = a.card.querySelector('.safe'); if (safe) safe.remove();
          a.row.replaceChildren(chip(m.approved ? 'Approved once' : 'Rejected', m.approved ? 'ok' : 'bad'));
          approvals.delete(m.id);
        }
        break;
      }
    }
  });

  function relTime(iso, now) {
    const t = Date.parse(iso);
    if (isNaN(t)) return '';
    const sec = Math.max(0, Math.round((now - t) / 1000));
    if (sec < 45) return 'just now';
    if (sec < 3600) return Math.max(1, Math.round(sec / 60)) + ' min ago';
    if (sec < 86400) return Math.round(sec / 3600) + ' h ago';
    if (sec < 7 * 86400) return Math.round(sec / 86400) + ' d ago';
    return new Date(t).toISOString().slice(0, 10);
  }

  showWelcome();
  syncPanes();
  vscode.postMessage({ type: 'history' });
</script>
</body></html>`;
}
