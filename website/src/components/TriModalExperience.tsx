"use client";

import React, { useState } from "react";
import {
  TerminalWindow,
  Browsers,
  Code,
  CheckCircle
} from "@phosphor-icons/react";

export function TriModalExperience() {
  const [activeTab, setActiveTab] = useState<"cli" | "web" | "vscode">("cli");

  return (
    <section className="py-24 bg-panel-subtle border-b border-panel-border transition-colors duration-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-10 pb-6 border-b border-panel-border">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-500 dark:text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-cyan-500" />
              <span>03 // Developer Interfaces</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-foreground">
              One harness engine. Three native interfaces.
            </h2>
          </div>
          <p className="text-neutral-600 dark:text-neutral-400 font-mono text-xs max-w-md">
            The FastAPI backend emits Server-Sent Events (SSE) consumed identically across terminal, web dashboard, and editor.
          </p>
        </div>

        {/* Interface Switcher Tabs */}
        <div className="flex flex-wrap gap-2 mb-6 font-mono text-xs">
          <button
            onClick={() => setActiveTab("cli")}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-xl transition-all cursor-pointer border ${
              activeTab === "cli"
                ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white border-neutral-700 shadow-sm font-semibold"
                : "bg-panel text-neutral-600 dark:text-neutral-400 border-panel-border hover:text-foreground hover:bg-neutral-100 dark:hover:bg-neutral-800/60"
            }`}
          >
            <TerminalWindow size={16} weight="duotone" className="text-emerald-500" />
            <span>01 // Terminal CLI (`harnix`)</span>
          </button>
          <button
            onClick={() => setActiveTab("web")}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-xl transition-all cursor-pointer border ${
              activeTab === "web"
                ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white border-neutral-700 shadow-sm font-semibold"
                : "bg-panel text-neutral-600 dark:text-neutral-400 border-panel-border hover:text-foreground hover:bg-neutral-100 dark:hover:bg-neutral-800/60"
            }`}
          >
            <Browsers size={16} weight="duotone" className="text-cyan-500" />
            <span>02 // Web Operations Console</span>
          </button>
          <button
            onClick={() => setActiveTab("vscode")}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-xl transition-all cursor-pointer border ${
              activeTab === "vscode"
                ? "bg-neutral-900 text-white dark:bg-neutral-800 dark:text-white border-neutral-700 shadow-sm font-semibold"
                : "bg-panel text-neutral-600 dark:text-neutral-400 border-panel-border hover:text-foreground hover:bg-neutral-100 dark:hover:bg-neutral-800/60"
            }`}
          >
            <Code size={16} weight="duotone" className="text-purple-500" />
            <span>03 // VS Code Extension</span>
          </button>
        </div>

        {/* Tab Preview Box */}
        <div className="sys-panel p-6 sm:p-7 border border-panel-border rounded-2xl font-mono text-xs bg-panel shadow-md">
          {activeTab === "cli" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-panel-border text-neutral-500 dark:text-neutral-400 text-[11px]">
                <span>CLI INTERFACE // PYTHON NATIVE RUNNER</span>
                <span className="text-emerald-600 dark:text-emerald-400">STREAMING STDOUT</span>
              </div>
              <div className="p-4 rounded-xl bg-neutral-950 text-neutral-200 space-y-2 overflow-x-auto leading-relaxed border border-neutral-800">
                <div className="text-emerald-400 font-semibold">$ harnix &quot;fix TypeError in decimal math&quot; --verify &quot;pytest tests/&quot;</div>
                <div className="text-neutral-400">[1/4] Baseline test execution: 1 failure confirmed</div>
                <div className="text-neutral-400">[2/4] Agent session initialized [model: gpt-6-luna]</div>
                <div className="text-neutral-300">[3/4] Tool call: edit_file(path=&apos;calc/math.py&apos;) -&gt; Policy ALLOWED</div>
                <div className="text-neutral-400">[4/4] Docker sandbox verification: 8/8 passed</div>
                <div className="text-emerald-400 font-bold">[SUCCESS] Verdict: VERIFIED. Patch ready in .harnix/patches/8f9a.patch</div>
                <div className="text-neutral-300">Run &apos;harnix apply 8f9a&apos; to merge patch to working tree.</div>
              </div>
            </div>
          )}

          {activeTab === "web" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-panel-border text-neutral-500 dark:text-neutral-400 text-[11px]">
                <span>WEB DASHBOARD // REACT 19 + FASTAPI SSE</span>
                <span className="text-cyan-600 dark:text-cyan-400">PORT: 5173</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded-xl bg-neutral-950 text-neutral-200 space-y-2 text-[11px] border border-neutral-800">
                  <div className="text-neutral-400 font-bold uppercase tracking-wider text-[10px]">Real-Time Event Stream</div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-300">
                    <span className="text-neutral-500">00:01.2s</span> task.created: issue_42
                  </div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-300">
                    <span className="text-neutral-500">00:03.8s</span> sandbox.docker: started container
                  </div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-emerald-400">
                    <span className="text-neutral-500">00:07.4s</span> policy.audit: 0 violations
                  </div>
                </div>
                <div className="p-4 rounded-xl bg-neutral-950 text-neutral-200 space-y-2 text-[11px] border border-neutral-800">
                  <div className="text-neutral-400 font-bold uppercase tracking-wider text-[10px]">Live Verification Inspector</div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-300">
                    <span className="text-cyan-400">DIFF</span> calc/math.py (+4, -1)
                  </div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-300">
                    <span className="text-neutral-400">TEST SUITE</span> 8 passed in 0.42s
                  </div>
                  <div className="p-2.5 rounded-lg bg-neutral-900 border border-neutral-800 text-purple-400">
                    <span className="text-neutral-400">KNOWLEDGE GRAPH</span> 2 Acceptance Criteria linked
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "vscode" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-panel-border text-neutral-500 dark:text-neutral-400 text-[11px]">
                <span>VS CODE EXTENSION // LOCAL IPC LENS</span>
                <span className="text-purple-600 dark:text-purple-400">INSPECTOR READY</span>
              </div>
              <div className="p-5 rounded-xl bg-neutral-950 text-neutral-200 space-y-2.5 text-xs border border-neutral-800">
                <div className="text-purple-400 font-bold">Harnix Inline Verification Lens</div>
                <p className="text-neutral-400 leading-relaxed">
                  Provides inline diff preview directly in the editor gutter with one-click sandbox replay, policy boundary warnings, and deterministic task logs.
                </p>
                <div className="pt-2 flex items-center gap-2 text-emerald-400 text-[11px]">
                  <CheckCircle size={14} weight="duotone" />
                  <span>Supports VS Code, Cursor, and Windsurf</span>
                </div>
              </div>
            </div>
          )}
        </div>

      </div>
    </section>
  );
}
