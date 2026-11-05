"use client";

import React, { useState } from "react";
import { TerminalIcon, CheckIcon, CopyIcon } from "./Icons";

export function QuickstartSection() {
  const [os, setOs] = useState<"win" | "bash">("win");
  const [provider, setProvider] = useState<"mock" | "openai" | "anthropic">("mock");
  const [copied, setCopied] = useState(false);

  const getCodeSnippet = () => {
    if (provider === "mock") {
      if (os === "win") {
        return `# 1. Backend Setup & Mock Script Run
cd backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head

# Run with scripted mock provider (no API key needed)
$env:HARNIX_PROVIDER="scripted"
$env:HARNIX_SCRIPTED_FILE="examples/mock-script.json"
$env:HARNIX_EXECUTION_MODE="local"
$env:HARNIX_ALLOWED_REPO_ROOTS=(Resolve-Path examples/repos).Path

python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 2. Frontend (in a second terminal)
cd frontend
npm install
npm run dev     # Open http://127.0.0.1:5173`;
      } else {
        return `# 1. Backend Setup & Mock Script Run
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head

# Run with scripted mock provider (no API key needed)
export HARNIX_PROVIDER=scripted
export HARNIX_SCRIPTED_FILE=examples/mock-script.json
export HARNIX_EXECUTION_MODE=local
export HARNIX_ALLOWED_REPO_ROOTS="$PWD/examples/repos"

uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 2. Frontend (in a second terminal)
cd frontend
npm install
npm run dev     # Open http://127.0.0.1:5173`;
      }
    }

    if (provider === "openai") {
      if (os === "win") {
        return `# Configure OpenAI in .env
cp .env.example .env
# Edit .env with your OPENAI_API_KEY and HARNIX_MODEL=gpt-5.4-nano

cd backend
.\\.venv\\Scripts\\alembic upgrade head
python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# Second terminal: Web Dashboard
cd frontend
npm run dev`;
      } else {
        return `# Configure OpenAI in .env
cp .env.example .env
# Edit .env with your OPENAI_API_KEY and HARNIX_MODEL=gpt-5.4-nano

cd backend
alembic upgrade head
uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# Second terminal: Web Dashboard
cd frontend
npm run dev`;
      }
    }

    // Anthropic
    return `# Configure Anthropic in .env
cp .env.example .env
# Set HARNIX_PROVIDER=anthropic, HARNIX_MODEL=claude-sonnet-5-5, ANTHROPIC_API_KEY=sk-ant-...

cd backend
uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# Second terminal: Web Dashboard
cd frontend && npm run dev`;
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCodeSnippet());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section id="quickstart" className="py-24 relative bg-grid-pattern">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-xs font-mono text-blue-400 mb-4">
            <TerminalIcon className="w-3.5 h-3.5" />
            <span>Developer Quickstart</span>
          </div>
          <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            Zero to verified demo in 60 seconds.
          </h2>
          <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
            Test the entire autonomous loop, human approvals, and verification engine right now without setting up API keys.
          </p>
        </div>

        {/* Option Selectors */}
        <div className="glass-panel p-6 sm:p-8 rounded-3xl border border-white/10 shadow-2xl">
          <div className="flex flex-wrap items-center justify-between gap-4 mb-6 pb-6 border-b border-white/10">
            {/* Mode Selector */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-slate-400 mr-2">Mode:</span>
              <button
                onClick={() => setProvider("mock")}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
                  provider === "mock"
                    ? "bg-blue-600 text-white font-bold shadow-md shadow-blue-600/30"
                    : "bg-white/5 text-slate-400 hover:text-white"
                }`}
              >
                60s Mock Demo (Free)
              </button>
              <button
                onClick={() => setProvider("openai")}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
                  provider === "openai"
                    ? "bg-blue-600 text-white font-bold shadow-md shadow-blue-600/30"
                    : "bg-white/5 text-slate-400 hover:text-white"
                }`}
              >
                Live OpenAI
              </button>
              <button
                onClick={() => setProvider("anthropic")}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
                  provider === "anthropic"
                    ? "bg-blue-600 text-white font-bold shadow-md shadow-blue-600/30"
                    : "bg-white/5 text-slate-400 hover:text-white"
                }`}
              >
                Live Anthropic
              </button>
            </div>

            {/* OS Selector */}
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-slate-400 mr-2">Shell:</span>
              <button
                onClick={() => setOs("win")}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
                  os === "win"
                    ? "bg-white/15 text-white font-bold"
                    : "bg-white/5 text-slate-400 hover:text-white"
                }`}
              >
                PowerShell (Windows)
              </button>
              <button
                onClick={() => setOs("bash")}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono transition-all ${
                  os === "bash"
                    ? "bg-white/15 text-white font-bold"
                    : "bg-white/5 text-slate-400 hover:text-white"
                }`}
              >
                Bash (Linux / macOS)
              </button>
            </div>
          </div>

          {/* Code Box */}
          <div className="relative rounded-2xl bg-[#06080B] border border-white/10 p-6 overflow-hidden">
            <div className="flex justify-between items-center mb-3">
              <span className="text-xs font-mono text-slate-400">
                {os === "win" ? "PowerShell" : "bash"}
              </span>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 hover:text-white border border-white/10 text-xs font-mono transition-colors"
              >
                {copied ? (
                  <>
                    <CheckIcon className="w-3.5 h-3.5 text-emerald-400" />
                    <span className="text-emerald-400">Copied</span>
                  </>
                ) : (
                  <>
                    <CopyIcon className="w-3.5 h-3.5" />
                    <span>Copy Snippet</span>
                  </>
                )}
              </button>
            </div>
            <pre className="text-slate-200 font-mono text-xs overflow-x-auto whitespace-pre leading-relaxed">
              {getCodeSnippet()}
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
}

