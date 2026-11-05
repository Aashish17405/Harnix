import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import { ConfigBanner } from "./components/ConfigBanner";
import { IngestionPage } from "./components/IngestionPage";
import { KnowledgePage } from "./components/KnowledgePage";
import { McpToolsPage } from "./components/McpToolsPage";
import { TaskDetail } from "./components/TaskDetail";
import { TaskForm } from "./components/TaskForm";
import { TaskListView } from "./components/TaskListView";
import type { HarnixConfig, Health } from "./types";

function useHashRoute(): string {
  const [hash, setHash] = useState(location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(location.hash || "#/");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export default function App() {
  const hash = useHashRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [config, setConfig] = useState<HarnixConfig | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const h = await api.health();
        if (!alive) return;
        setHealth(h);
        if (h.auth_required && !getToken()) {
          setNeedToken(true);
          return;
        }
        setNeedToken(false);
        setConfig(await api.config());
        setFatal(null);
      } catch (e) {
        if (alive) setFatal(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [nonce]);

  const m = /^#\/tasks\/([\w-]+)/.exec(hash);
  const onKnowledge = hash.startsWith("#/knowledge");
  const onTools = hash.startsWith("#/tools");
  const onIngest = hash.startsWith("#/ingest");

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="mark" aria-hidden="true">
            H
          </span>
          Harnix
        </a>
        <nav aria-label="Main">
          <a
            href="#/"
            aria-current={
              onKnowledge || onTools || onIngest ? undefined : "page"
            }
          >
            Tasks
          </a>
          <a href="#/knowledge" aria-current={onKnowledge ? "page" : undefined}>
            Knowledge
          </a>
          <a href="#/ingest" aria-current={onIngest ? "page" : undefined}>
            Ingestion
          </a>
          <a href="#/tools" aria-current={onTools ? "page" : undefined}>
            MCP tools
          </a>
        </nav>
      </header>
      <main id="main">
        {fatal && (
          <div className="banner banner-bad" role="alert">
            {fatal}
          </div>
        )}
        {needToken && (
          <form
            className="card"
            onSubmit={(e) => {
              e.preventDefault();
              setToken(tokenInput);
              setNonce((n) => n + 1);
            }}
          >
            <label>
              This Harnix server requires an API token (HARNIX_API_TOKEN)
              <input
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoComplete="off"
              />
            </label>
            <button className="primary" type="submit">
              Continue
            </button>
          </form>
        )}
        <ConfigBanner health={health} config={config} />
        {!needToken &&
          !fatal &&
          (onIngest ? (
            <IngestionPage />
          ) : onTools ? (
            <McpToolsPage />
          ) : onKnowledge ? (
            <KnowledgePage />
          ) : m ? (
            <TaskDetail key={m[1]} taskId={m[1]} />
          ) : (
            <div className="home">
              <TaskForm
                config={config}
                onCreated={(t) => (location.hash = `#/tasks/${t.id}`)}
              />
              <TaskListView />
            </div>
          ))}
      </main>
    </div>
  );
}
