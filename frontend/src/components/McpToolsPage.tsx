import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  KnowledgeProject,
  McpCallResult,
  McpConfig,
  McpProp,
  McpTool,
} from "../types";

/** Starting values so each tool can be tried with one click. */
const EXAMPLES: Record<string, Record<string, string>> = {
  knowledge_context: { task: "Let users mark accounts as favourites, at most ten" },
  knowledge_search: { query: "favourite accounts limit" },
  knowledge_get: { ref: "US-003", max_chars: "4000" },
  knowledge_relations: { ref: "US-003", depth: "1" },
  knowledge_trace: { ref: "US-003" },
  knowledge_criteria: { ref: "US-003" },
  knowledge_list: { kind: "requirement", limit: "50" },
  knowledge_impact: { ref: "US-003", depth: "2" },
  knowledge_path: { from: "US-003", to: "BRD-03" },
  knowledge_uses: { name: "favourite" },
  knowledge_gaps: {},
  knowledge_concepts: { type: "actor" },
  knowledge_query: { cypher: "MATCH (s:Story {project: $project})\nRETURN s.external_id AS story, s.title AS title LIMIT 25" },
}; // prettier-ignore

interface Call {
  id: number;
  tool: string;
  request: unknown;
  response?: unknown;
  result?: McpCallResult;
  error?: string;
  ms?: number;
}

/** The knowledge base's MCP (Model Context Protocol) tools: try them here exactly as an agent calls them, and copy the settings to plug them into another harness. */
export function McpToolsPage() {
  const [projects, setProjects] = useState<KnowledgeProject[]>([]);
  const [project, setProject] = useState("");
  const [tools, setTools] = useState<McpTool[]>([]);
  const [instructions, setInstructions] = useState("");
  const [tool, setTool] = useState("knowledge_context");
  const [calls, setCalls] = useState<Call[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.knowledgeProjects().then(
      (r) => (
        setProjects(r.projects),
        setProject((p) => p || r.projects[0]?.project || "")
      ),
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    (async () => {
      try {
        const init = await api.mcp<{ instructions?: string }>(
          project,
          "initialize",
          {
            protocolVersion: "2025-06-18",
            clientInfo: { name: "harnix-ui", version: "1" },
          },
        );
        const list = await api.mcp<{ tools: McpTool[] }>(project, "tools/list");
        if (!alive) return;
        setInstructions(init.result?.instructions ?? "");
        setTools(list.result?.tools ?? []);
        setError(list.error?.message ?? null);
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [project]);

  async function call(name: string, args: Record<string, unknown>) {
    const id = Date.now();
    const params = { name, arguments: args };
    const request = { jsonrpc: "2.0", id, method: "tools/call", params };
    setCalls((c) => [{ id, tool: name, request }, ...c].slice(0, 10));
    const t0 = performance.now();
    try {
      const response = await api.mcp<McpCallResult>(
        project,
        "tools/call",
        params,
      );
      const ms = Math.round(performance.now() - t0);
      setCalls((c) =>
        c.map((x) =>
          x.id === id
            ? {
                ...x,
                response,
                result: response.result,
                error: response.error?.message,
                ms,
              }
            : x,
        ),
      );
    } catch (e) {
      setCalls((c) =>
        c.map((x) => (x.id === id ? { ...x, error: (e as Error).message } : x)),
      );
    }
  }

  const current = tools.find((t) => t.name === tool);

  return (
    <div className="mcp-page">
      <section className="card">
        <h1>MCP tools</h1>
        <p className="help">
          The knowledge base speaks MCP (Model Context Protocol), so Harnix,
          Claude Code, Cursor, VS Code, Kiro or any MCP client gets the same
          read-only tools. Everything below goes through the real endpoint{" "}
          <code>POST /api/knowledge/mcp</code>, the same request an agent sends.
        </p>
        <label className="mcp-project">
          Project
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            {projects.map((p) => (
              <option key={p.project} value={p.project}>
                {p.project}
              </option>
            ))}
          </select>
        </label>
        {error && (
          <p className="banner banner-bad" role="alert">
            {error}
          </p>
        )}
        {!error && projects.length === 0 && (
          <p className="muted">
            No knowledge projects yet. Add documents on the Knowledge page
            first.
          </p>
        )}
      </section>

      {project && tools.length > 0 && (
        <div className="mcp-grid">
          <nav className="card mcp-tools" aria-label="Tools">
            {tools.map((t) => (
              <button
                key={t.name}
                type="button"
                aria-pressed={t.name === tool}
                onClick={() => setTool(t.name)}
              >
                <code>{t.name}</code>
                <span className="muted">{firstSentence(t.description)}</span>
              </button>
            ))}
          </nav>
          <div className="mcp-main">
            {current && (
              <ToolForm
                key={`${project}:${current.name}`}
                tool={current}
                onRun={(args) => void call(current.name, args)}
              />
            )}
            {calls.map((c) => (
              <CallResult
                key={c.id}
                c={c}
                onClose={() =>
                  setCalls((all) => all.filter((x) => x.id !== c.id))
                }
              />
            ))}
          </div>
        </div>
      )}

      {project && <Connect project={project} instructions={instructions} />}
    </div>
  );
}

function firstSentence(s: string): string {
  const i = s.indexOf(". ");
  return i > 0 ? s.slice(0, i + 1) : s;
}

function ToolForm({
  tool,
  onRun,
}: {
  tool: McpTool;
  onRun: (args: Record<string, unknown>) => void;
}) {
  // `project` is fixed by the page (the endpoint is scoped to it), so it is not a field.
  const props = Object.entries(tool.inputSchema.properties).filter(
    ([k]) => k !== "project",
  );
  const required = new Set(tool.inputSchema.required ?? []);
  const [values, setValues] = useState<Record<string, string | boolean>>(
    EXAMPLES[tool.name] ?? {},
  );
  const missing = [...required].filter((k) => !String(values[k] ?? "").trim());

  function args(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [k, p] of props) {
      const v = values[k];
      if (v === undefined || v === "" || v === false) continue;
      if (p.type === "integer" || p.type === "number") out[k] = Number(v);
      else if (p.type === "array")
        out[k] = String(v)
          .split(/[\n,]/)
          .map((x) => x.trim())
          .filter(Boolean);
      else out[k] = v;
    }
    return out;
  }

  return (
    <form
      className="card mcp-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!missing.length) onRun(args());
      }}
    >
      <h2>
        <code>{tool.name}</code>
      </h2>
      <p className="help">{tool.description}</p>
      {props.length === 0 && (
        <p className="muted">This tool takes no arguments.</p>
      )}
      {props.map(([k, p]) => (
        <Field
          key={k}
          name={k}
          prop={p}
          required={required.has(k)}
          value={values[k]}
          onChange={(v) => setValues((all) => ({ ...all, [k]: v }))}
        />
      ))}
      <div className="row">
        <button type="submit" className="primary" disabled={missing.length > 0}>
          Call tool
        </button>
        {missing.length > 0 && (
          <span className="muted">Needs {missing.join(", ")}</span>
        )}
      </div>
    </form>
  );
}

function Field({
  name,
  prop,
  required,
  value,
  onChange,
}: {
  name: string;
  prop: McpProp;
  required: boolean;
  value: string | boolean | undefined;
  onChange: (v: string | boolean) => void;
}) {
  const label = (
    <span>
      <code>{name}</code>
      {required ? " (required)" : ""}
      {prop.description ? (
        <span className="muted"> {prop.description}</span>
      ) : null}
    </span>
  );
  if (prop.type === "boolean")
    return (
      <label className="mcp-check">
        <input
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
    );
  if (prop.enum)
    return (
      <label>
        {label}
        <select
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">(any)</option>
          {prop.enum.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
      </label>
    );
  const long = name === "task" || name === "cypher" || prop.type === "array";
  const hint =
    prop.type === "array"
      ? `comma separated${prop.items?.enum ? `: ${prop.items.enum.join(", ")}` : ""}`
      : prop.minimum !== undefined || prop.maximum !== undefined
        ? `${prop.minimum ?? ""} to ${prop.maximum ?? ""}`
        : undefined;
  return (
    <label>
      {label}
      {long ? (
        <textarea
          value={String(value ?? "")}
          rows={name === "cypher" ? 4 : 2}
          spellCheck={name !== "cypher"}
          placeholder={hint}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          type={
            prop.type === "integer" || prop.type === "number"
              ? "number"
              : "text"
          }
          min={prop.minimum}
          max={prop.maximum}
          placeholder={hint}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}

function CallResult({ c, onClose }: { c: Call; onClose: () => void }) {
  const raw = c.result?.content?.[0]?.text ?? "";
  let parsed: unknown;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    parsed = null; // not JSON: shown as plain text
  }
  const rendered =
    parsed &&
    typeof parsed === "object" &&
    typeof (parsed as { text?: unknown }).text === "string"
      ? (parsed as { text: string }).text
      : null;
  return (
    <section className="card mcp-call" aria-label={`Result of ${c.tool}`}>
      <header className="qc-frame-head">
        <code>
          tools/call {c.tool}
          {c.ms !== undefined ? ` · ${c.ms} ms` : ""}
        </code>
        <button
          type="button"
          className="linklike"
          onClick={onClose}
          aria-label="Close this result"
        >
          ✕
        </button>
      </header>
      {!c.result && !c.error && <p className="muted">Calling…</p>}
      {c.error && <p className="banner banner-bad">{c.error}</p>}
      {c.result?.isError && (
        <p className="banner banner-bad">The tool refused or failed: {raw}</p>
      )}
      {c.result && !c.result.isError && (
        <>
          {rendered && (
            <details open>
              <summary>What the agent reads (rendered context)</summary>
              <pre className="mcp-pre">{rendered}</pre>
            </details>
          )}
          <details open={!rendered}>
            <summary>Result{rendered ? " as data" : ""}</summary>
            <pre className="mcp-pre">
              {parsed ? JSON.stringify(parsed, null, 2) : raw}
            </pre>
          </details>
        </>
      )}
      <details>
        <summary>JSON-RPC request and response</summary>
        <pre className="mcp-pre">{JSON.stringify(c.request, null, 2)}</pre>
        {c.response !== undefined && (
          <pre className="mcp-pre">{JSON.stringify(c.response, null, 2)}</pre>
        )}
      </details>
    </section>
  );
}

function Connect({
  project,
  instructions,
}: {
  project: string;
  instructions: string;
}) {
  const [cfg, setCfg] = useState<McpConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    api.mcpConfig(project).then(
      (r) => (setCfg(r), setError(null)),
      (e: Error) => setError(e.message),
    );
  }, [project]);

  function copy(key: string, text: string) {
    void navigator.clipboard?.writeText(text).then(
      () => setCopied(key),
      () => setCopied(""),
    );
  }

  return (
    <section className="card mcp-connect">
      <h2>Connect a harness</h2>
      {error && <p className="banner banner-bad">{error}</p>}
      {cfg && (
        <>
          <p className="help">
            HTTP endpoint for this project: <code>{cfg.url}</code>
            {cfg.auth_required
              ? ". It needs the same bearer token as this dashboard; the snippets show a placeholder, never the token."
              : ". This server has no API token set, so the endpoint is open to anyone who can reach it."}{" "}
            {cfg.note}
          </p>
          {cfg.clients.map((cl) => {
            const json = JSON.stringify(cl.config, null, 2);
            return (
              <div key={cl.client} className="mcp-client">
                <div className="row">
                  <strong>{cl.client}</strong>
                  <span className="muted">{cl.file}</span>
                  <button type="button" onClick={() => copy(cl.client, json)}>
                    {copied === cl.client ? "Copied" : "Copy JSON"}
                  </button>
                  {cl.command && (
                    <button
                      type="button"
                      onClick={() => copy(`${cl.client}-cmd`, cl.command!)}
                    >
                      {copied === `${cl.client}-cmd`
                        ? "Copied"
                        : "Copy command"}
                    </button>
                  )}
                </div>
                {cl.command && <pre className="mcp-pre">{cl.command}</pre>}
                <pre className="mcp-pre">{json}</pre>
              </div>
            );
          })}
        </>
      )}
      {instructions && (
        <details>
          <summary>What the server tells the agent when it connects</summary>
          <pre className="mcp-pre">{instructions}</pre>
        </details>
      )}
    </section>
  );
}
