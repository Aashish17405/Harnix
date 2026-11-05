"use client";

import React, { useState } from "react";
import { CheckIcon, CopyIcon } from "./Icons";

export function QuickstartSection() {
  const [os, setOs] = useState<"win" | "bash">("win");
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [copied, setCopied] = useState(false);

  const getCodeSnippet = () => {
    if (mode === "mock") {
      if (os === "win") {
        return `# 1. Clone repository & initialize backend venv
git clone https://github.com/your-org/harnix.git
cd harnix\\backend
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
git clone https://github.com/your-org/harnix.git
cd harnix/backend
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
    }

    // Live LLM Provider Mode
    if (os === "win") {
      return `# 1. Backend environment setup
cd backend
.\\.venv\\Scripts\\Activate.ps1
cp .env.example .env

# 2. Configure provider & budget cap in .env:
# HARNIX_PROVIDER=openai
# HARNIX_MODEL=gpt-6-luna
# OPENAI_API_KEY=sk-...
# HARNIX_EXECUTION_MODE=docker

# 3. Run a verified task directly from the CLI
harnix "fix failing test in rangesum.py" --verify "pytest tests/test_rangesum.py"`;
    } else {
      return `# 1. Backend environment setup
cd backend
source .venv/bin/activate
cp .env.example .env

# 2. Configure provider & budget cap in .env:
# export HARNIX_PROVIDER=openai
# export HARNIX_MODEL=gpt-6-luna
# export OPENAI_API_KEY=sk-...
# export HARNIX_EXECUTION_MODE=docker

# 3. Run a verified task directly from the CLI
harnix "fix failing test in rangesum.py" --verify "pytest tests/test_rangesum.py"`;
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCodeSnippet());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section id="quickstart" className="py-20 bg-[#0B0D12] border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-10 pb-6 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>05 // Quickstart</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-white">
              Get running in under 60 seconds.
            </h2>
          </div>
          <p className="text-neutral-400 font-mono text-xs max-w-md">
            Test the entire deterministic verification engine locally using mock mode without an API key or spend.
          </p>
        </div>

        {/* Console Box */}
        <div className="sys-panel overflow-hidden border border-neutral-700/80 max-w-4xl mx-auto">
          
          {/* Controls Bar */}
          <div className="p-4 bg-neutral-900 border-b border-white/10 flex flex-wrap items-center justify-between gap-4">
            
            {/* Mode Selector */}
            <div className="flex items-center gap-2 font-mono text-xs">
              <button
                onClick={() => setMode("mock")}
                className={`px-3 py-1.5 rounded transition-colors ${
                  mode === "mock"
                    ? "bg-emerald-500 text-black font-bold"
                    : "bg-neutral-800 text-neutral-300 hover:text-white"
                }`}
              >
                Mock Demo (0 Keys)
              </button>
              <button
                onClick={() => setMode("live")}
                className={`px-3 py-1.5 rounded transition-colors ${
                  mode === "live"
                    ? "bg-emerald-500 text-black font-bold"
                    : "bg-neutral-800 text-neutral-300 hover:text-white"
                }`}
              >
                Live LLM (OpenAI / Anthropic)
              </button>
            </div>

            {/* OS Selector & Copy */}
            <div className="flex items-center gap-3 font-mono text-xs">
              <div className="flex items-center gap-1 bg-neutral-800 p-1 rounded">
                <button
                  onClick={() => setOs("win")}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    os === "win"
                      ? "bg-neutral-700 text-white font-semibold"
                      : "text-neutral-400 hover:text-white"
                  }`}
                >
                  PowerShell
                </button>
                <button
                  onClick={() => setOs("bash")}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    os === "bash"
                      ? "bg-neutral-700 text-white font-semibold"
                      : "text-neutral-400 hover:text-white"
                  }`}
                >
                  Bash / Zsh
                </button>
              </div>

              <button
                onClick={handleCopy}
                className="flex items-center gap-1 px-3 py-1.5 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-300 hover:text-white border border-neutral-700 transition-colors"
                title="Copy instructions"
              >
                {copied ? (
                  <>
                    <CheckIcon className="w-3.5 h-3.5 text-emerald-400" />
                    <span className="text-emerald-400 text-xs">Copied</span>
                  </>
                ) : (
                  <>
                    <CopyIcon className="w-3.5 h-3.5" />
                    <span className="text-xs">Copy</span>
                  </>
                )}
              </button>
            </div>

          </div>

          {/* Terminal Code Display */}
          <div className="p-6 bg-[#090A0E] font-mono text-xs text-neutral-200 overflow-x-auto leading-relaxed max-h-[460px] overflow-y-auto">
            <pre className="whitespace-pre-wrap font-mono">
              {getCodeSnippet()}
            </pre>
          </div>

          {/* Bottom Bar */}
          <div className="p-3 bg-neutral-900 border-t border-white/10 flex items-center justify-between font-mono text-[11px] text-neutral-400">
            <span>REQUIRES: PYTHON 3.11+ &bull; NODE.JS 18+</span>
            <span className="text-neutral-300">DOCKER ENGINE OPTIONAL FOR LOCAL MOCK</span>
          </div>

        </div>

      </div>
    </section>
  );
}
