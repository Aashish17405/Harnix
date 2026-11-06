"use client";

import React, { useState } from "react";
import { Copy, Check } from "@phosphor-icons/react";

export function QuickstartSection() {
  const [os, setOs] = useState<"win" | "bash">("win");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [copied, setCopied] = useState(false);

  const getCodeSnippet = () => {
    if (mode === "mock") {
      if (os === "win") {
        return `# 1. Clone repository & initialize backend venv
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix\\backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"

# 2. Initialize SQLite schema & run migrations
alembic upgrade head

# 3. Configure local mock environment (Zero API Keys Required)
$env:HARNIX_PROVIDER="scripted"
$env:HARNIX_SCRIPTED_FILE="examples/mock-script.json"
$env:HARNIX_EXECUTION_MODE="local"
$env:HARNIX_ALLOWED_REPO_ROOTS=(Resolve-Path examples/repos).Path

# 4. Start the Harnix API Daemon
python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 5. In a second terminal: Start the Web Dashboard
cd ..\\frontend
npm install
npm run dev     # Open http://127.0.0.1:5173`;
      } else {
        return `# 1. Clone repository & initialize backend venv
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Initialize SQLite schema & run migrations
alembic upgrade head

# 3. Configure local mock environment (Zero API Keys Required)
export HARNIX_PROVIDER=scripted
export HARNIX_SCRIPTED_FILE=examples/mock-script.json
export HARNIX_EXECUTION_MODE=local
export HARNIX_ALLOWED_REPO_ROOTS="$PWD/examples/repos"

# 4. Start the Harnix API Daemon
python3 -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 5. In a second terminal: Start the Web Dashboard
cd ../frontend
npm install
npm run dev     # Open http://127.0.0.1:5173`;
      }
    } else {
      if (os === "win") {
        return `# 1. Clone repository & initialize backend venv
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix\\backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"

# 2. Configure Live LLM & Docker sandbox
$env:HARNIX_PROVIDER="openai"            # or "anthropic"
$env:OPENAI_API_KEY="sk-..."             # your API key
$env:HARNIX_EXECUTION_MODE="docker"       # Hardened sandbox execution
$env:HARNIX_ALLOWED_REPO_ROOTS=(Resolve-Path .).Path

# 3. Run migrations & start server
alembic upgrade head
python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000`;
      } else {
        return `# 1. Clone repository & initialize backend venv
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure Live LLM & Docker sandbox
export HARNIX_PROVIDER=openai            # or "anthropic"
export OPENAI_API_KEY="sk-..."           # your API key
export HARNIX_EXECUTION_MODE=docker      # Hardened sandbox execution
export HARNIX_ALLOWED_REPO_ROOTS="$PWD"

# 3. Run migrations & start server
alembic upgrade head
python3 -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000`;
      }
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCodeSnippet());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section id="quickstart" className="py-24 bg-panel-subtle border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>05 // Quickstart</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Run locally in 60 seconds.
            </h2>
          </div>
          <p className="text-neutral-600 dark:text-neutral-400 font-mono text-xs max-w-md">
            Spin up the complete test harness locally. Zero external API keys needed in mock mode.
          </p>
        </div>

        {/* Console Box */}
        <div className="sys-panel overflow-hidden border border-panel-border rounded-2xl shadow-xl max-w-4xl mx-auto bg-panel">
          
          {/* Controls Bar */}
          <div className="p-4 bg-panel-subtle border-b border-panel-border flex flex-wrap items-center justify-between gap-4">
            
            {/* Mode Selector */}
            <div className="flex items-center gap-2 font-mono text-xs">
              <button
                onClick={() => setMode("mock")}
                className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                  mode === "mock"
                    ? "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-black font-bold shadow-xs"
                    : "bg-panel text-neutral-600 dark:text-neutral-400 hover:text-foreground border border-panel-border"
                }`}
              >
                Mock Demo (0 Keys)
              </button>
              <button
                onClick={() => setMode("live")}
                className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                  mode === "live"
                    ? "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-black font-bold shadow-xs"
                    : "bg-panel text-neutral-600 dark:text-neutral-400 hover:text-foreground border border-panel-border"
                }`}
              >
                Live LLM Mode
              </button>
            </div>

            {/* OS Selector & Copy */}
            <div className="flex items-center gap-3 font-mono text-xs">
              <div className="flex items-center gap-1 bg-neutral-200/60 dark:bg-neutral-800 p-1 rounded-lg">
                <button
                  onClick={() => setOs("win")}
                  className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                    os === "win"
                      ? "bg-white text-neutral-900 dark:bg-neutral-700 dark:text-white font-semibold shadow-xs"
                      : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
                  }`}
                >
                  PowerShell
                </button>
                <button
                  onClick={() => setOs("bash")}
                  className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                    os === "bash"
                      ? "bg-white text-neutral-900 dark:bg-neutral-700 dark:text-white font-semibold shadow-xs"
                      : "text-neutral-600 dark:text-neutral-400 hover:text-foreground"
                  }`}
                >
                  Bash / Zsh
                </button>
              </div>

              <button
                onClick={handleCopy}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 border border-panel-border transition-colors font-medium shadow-xs cursor-pointer"
                title="Copy instructions"
              >
                {copied ? (
                  <>
                    <Check size={14} weight="bold" className="text-emerald-500" />
                    <span className="text-emerald-500 text-xs">Copied</span>
                  </>
                ) : (
                  <>
                    <Copy size={14} weight="bold" />
                    <span className="text-xs">Copy</span>
                  </>
                )}
              </button>
            </div>

          </div>

          {/* Terminal Code Display */}
          <div className="p-6 bg-neutral-950 font-mono text-xs text-neutral-200 overflow-x-auto leading-relaxed max-h-[440px] overflow-y-auto">
            <pre className="whitespace-pre-wrap font-mono">
              {getCodeSnippet()}
            </pre>
          </div>

          {/* Bottom Bar */}
          <div className="p-3 bg-panel-subtle border-t border-panel-border flex items-center justify-between font-mono text-[11px] text-neutral-500 dark:text-neutral-400">
            <span>REQUIRES: PYTHON 3.11+ &bull; NODE.JS 18+</span>
            <span className="text-emerald-600 dark:text-emerald-400 font-semibold">DOCKER ENGINE OPTIONAL FOR LOCAL MOCK</span>
          </div>

        </div>

      </div>
    </section>
  );
}
