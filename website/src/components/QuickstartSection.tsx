"use client";

import React, { useState } from "react";
import { Copy, Check } from "@phosphor-icons/react";
import { BackgroundRippleEffect } from "@/components/ui/background-ripple-effect";

export function QuickstartSection() {
  const [os, setOs] = useState<"win" | "bash">("win");
  const [tier, setTier] = useState<"quick" | "server">("quick");
  const [copied, setCopied] = useState(false);

  const getCodeSnippet = () => {
    if (tier === "quick") {
      if (os === "win") {
        return `# 1. Install Harnix via pip
pip install harnix

# 2. Run a task with guaranteed test verification
harnix "fix calculate_total tax rounding" --verify "pytest tests/"

# 3. Inspect the verified patch and apply it
harnix status
harnix apply`;
      } else {
        return `# 1. Install Harnix via pip
pip install harnix

# 2. Run a task with guaranteed test verification
harnix "fix calculate_total tax rounding" --verify "pytest tests/"

# 3. Inspect the verified patch and apply it
harnix status
harnix apply`;
      }
    } else {
      if (os === "win") {
        return `# 1. Clone repository & install dependencies
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix\\backend
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"

# 2. Run database migrations & start daemon
alembic upgrade head
python -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 3. In another terminal: Start the Web Dashboard
cd ..\\frontend
npm install
npm run dev     # Dashboard opens at http://127.0.0.1:5173`;
      } else {
        return `# 1. Clone repository & install dependencies
git clone https://github.com/Aashish17405/Harnix.git
cd Harnix/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Run database migrations & start daemon
alembic upgrade head
python3 -m uvicorn harnix.api.app:app_factory --factory --host 127.0.0.1 --port 8000

# 3. In another terminal: Start the Web Dashboard
cd ../frontend
npm install
npm run dev     # Dashboard opens at http://127.0.0.1:5173`;
      }
    }
  };

  const handleCopy = () => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(getCodeSnippet());
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } else {
        // Fallback
        const textarea = document.createElement("textarea");
        textarea.value = getCodeSnippet();
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch {
      // Ignore
    }
  };

  return (
    <section id="quickstart" className="relative py-24 bg-panel-subtle border-b border-panel-border transition-colors duration-200 overflow-hidden">
      {/* Interactive Background Ripple Effect */}
      <BackgroundRippleEffect rows={14} cols={32} cellSize={50} className="opacity-90" />
      
      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pointer-events-none">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-12 pb-6 pointer-events-auto">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>Get Started</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              Run locally in 60 seconds.
            </h2>
          </div>
        </div>

        {/* Console Box */}
        <div className="sys-panel overflow-hidden border border-panel-border rounded-2xl shadow-xl max-w-4xl mx-auto bg-panel pointer-events-auto">
          
          {/* Controls Bar */}
          <div className="p-4 bg-panel-subtle border-b border-panel-border flex flex-wrap items-center justify-between gap-4">
            
            {/* Setup Selector */}
            <div className="flex items-center gap-2 font-mono text-xs">
              <button
                onClick={() => setTier("quick")}
                className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                  tier === "quick"
                    ? "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-black font-bold shadow-xs"
                    : "bg-panel text-neutral-600 dark:text-neutral-400 hover:text-foreground border border-panel-border"
                }`}
              >
                1-Minute CLI (Recommended)
              </button>
              <button
                onClick={() => setTier("server")}
                className={`px-3 py-1.5 rounded-lg transition-all cursor-pointer ${
                  tier === "server"
                    ? "bg-emerald-600 text-white dark:bg-emerald-500 dark:text-black font-bold shadow-xs"
                    : "bg-panel text-neutral-600 dark:text-neutral-400 hover:text-foreground border border-panel-border"
                }`}
              >
                Full Server &amp; UI Setup
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
          <div className="p-6 bg-neutral-950 font-mono text-xs text-neutral-200 overflow-x-auto leading-relaxed max-h-[360px] overflow-y-auto">
            <pre className="whitespace-pre-wrap font-mono">
              {getCodeSnippet()}
            </pre>
          </div>

          {/* Bottom Bar */}
          <div className="p-3 bg-panel-subtle border-t border-panel-border flex items-center justify-between font-mono text-[11px] text-neutral-500 dark:text-neutral-400">
            <span>PREREQUISITE: PYTHON 3.11+</span>
            <span className="text-emerald-600 dark:text-emerald-400 font-semibold">100% OPEN-SOURCE &bull; APACHE 2.0</span>
          </div>

        </div>

      </div>
    </section>
  );
}
