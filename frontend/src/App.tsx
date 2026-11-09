import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import { ConfigBanner } from "./components/ConfigBanner";
import { IngestionPage } from "./components/IngestionPage";
import { KnowledgePage } from "./components/KnowledgePage";
import { McpToolsPage } from "./components/McpToolsPage";
import { TaskDetail } from "./components/TaskDetail";
import { TaskForm } from "./components/TaskForm";
import { TaskListView } from "./components/TaskListView";
import { Logo } from "./components/Logo";
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
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("harnix-theme");
    if (saved === "light" || saved === "dark") return saved;
    return "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("harnix-theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  };

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
          <Logo size={26} showText={false} />
          <span>Harnix</span>
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
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "10px" }}>
          {health && (
            <span className="env" title={`Provider: ${health.provider.name} (${health.provider.model})`}>
              {health.provider.name || "provider offline"} &bull; {health.execution_mode}
            </span>
          )}
          <button
            type="button"
            className="theme-btn"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            {theme === "dark" ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            )}
          </button>
        </div>
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
