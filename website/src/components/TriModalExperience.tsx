"use client";

import React, { useState } from "react";
import { MonitorIcon, TerminalIcon, CodeIcon } from "./Icons";

export function TriModalExperience() {
  const [activeTab, setActiveTab] = useState<"cli" | "web" | "vscode">("cli");

  return (
    <section className="py-20 bg-[#0B0D12] border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-10 pb-6 border-b border-white/10">
          <div>
            <div className="flex items-center gap-2 font-mono text-xs text-neutral-400 uppercase tracking-wider mb-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>Developer Interfaces</span>
            </div>
            <h2 className="text-2xl sm:text-4xl font-bold tracking-tight text-white">
              One harness engine. Three native interfaces.
            </h2>
          </div>
          <p className="text-neutral-400 font-mono text-xs max-w-md">
            The FastAPI backend emits Server-Sent Events (SSE) consumed identically across terminal, web dashboard, and editor.
          </p>
        </div>

        {/* Interface Switcher Tabs */}
        <div className="flex flex-wrap gap-2 mb-6 font-mono text-xs">
          <button
            onClick={() => setActiveTab("cli")}
            className={`flex items-center gap-2 px-4 py-2 rounded transition-colors border ${
              activeTab === "cli"
                ? "bg-neutral-800 text-white border-neutral-600 font-semibold"
                : "bg-neutral-900/60 text-neutral-400 border-white/5 hover:text-white"
            }`}
          >
            <TerminalIcon className="w-3.5 h-3.5 text-emerald-400" />
            <span>01 // Terminal CLI (`harnix`)</span>
          </button>
          <button
            onClick={() => setActiveTab("web")}
            className={`flex items-center gap-2 px-4 py-2 rounded transition-colors border ${
              activeTab === "web"
                ? "bg-neutral-800 text-white border-neutral-600 font-semibold"
                : "bg-neutral-900/60 text-neutral-400 border-white/5 hover:text-white"
            }`}
          >
            <MonitorIcon className="w-3.5 h-3.5 text-cyan-400" />
            <span>02 // Web Operations Console</span>
          </button>
          <button
            onClick={() => setActiveTab("vscode")}
            className={`flex items-center gap-2 px-4 py-2 rounded transition-colors border ${
              activeTab === "vscode"
                ? "bg-neutral-800 text-white border-neutral-600 font-semibold"
                : "bg-neutral-900/60 text-neutral-400 border-white/5 hover:text-white"
            }`}
          >
            <CodeIcon className="w-3.5 h-3.5 text-purple-400" />
            <span>03 // VS Code Extension</span>
          </button>
        </div>

        {/* Tab Preview Box */}
        <div className="sys-panel p-6 border border-neutral-700/80 font-mono text-xs">
          {activeTab === "cli" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-white/5 text-neutral-400 text-[11px]">
                <span>CLI INTERFACE // HARNIX PYTHON RUNNER</span>
                <span>HEADLESS OR INTERACTIVE</span>
              </div>
              <div className="p-4 rounded bg-[#090A0E] text-neutral-200 space-y-2 overflow-x-auto leading-relaxed">
                <div className="text-emerald-400">$ harnix &quot;fix TypeError in decimal math&quot; --verify &quot;pytest tests/&quot;</div>
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
              <div className="flex items-center justify-between pb-3 border-b border-white/5 text-neutral-400 text-[11px]">
                <span>WEB DASHBOARD // REACT 19 + FASTAPI SSE</span>
                <span>DEFAULT PORT: 5173</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded bg-[#090A0E] space-y-2 text-[11px]">
                  <div className="text-neutral-400 font-bold">REAL-TIME TIMELINE STREAM</div>
                  <div className="p-2 rounded bg-neutral-900 border border-white/5 text-neutral-300">
                    <span className="text-neutral-500">00:01.2s</span> task.created: issue_42
                  </div>
                  <div className="p-2 rounded bg-neutral-900 border border-white/5 text-neutral-300">
                    <span className="text-neutral-500">00:03.8s</span> sandbox.docker: started container
                  </div>
                  <div className="p-2 rounded bg-neutral-900 border border-emerald-500/20 text-emerald-300">
                    <span className="text-neutral-500">00:09.1s</span> verification.passed: pytest 100%
                  </div>
                </div>

                <div className="p-4 rounded bg-[#090A0E] space-y-2 text-[11px]">
                  <div className="text-neutral-400 font-bold">SPEC GRAPH &amp; AUDIT INSPECTOR</div>
                  <div className="p-2 rounded bg-neutral-900 border border-white/5 text-neutral-300">
                    <div>BRD: BRD-04 (Virtual Accounts)</div>
                    <div className="text-neutral-400 text-[10px]">&darr; IMPLEMENTS</div>
                    <div>UserStory: US-039 (Idempotent Posting)</div>
                  </div>
                  <div className="text-[10px] text-neutral-400">
                    Audited DB records: 14 events &bull; 0 token violations
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "vscode" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-white/5 text-neutral-400 text-[11px]">
                <span>VS CODE EXTENSION // INLINE TEST HEALING</span>
                <span>INTEGRATION: LANGUAGE SERVER PROTOCOL</span>
              </div>
              <div className="p-4 rounded bg-[#090A0E] text-neutral-200 space-y-2 overflow-x-auto leading-relaxed">
                <div className="text-purple-400">{"// Diagnostic provider triggers Harnix on test failure:"}</div>
                <div className="text-neutral-300">&gt; Test failed: test_rangesum.py (AssertionError)</div>
                <div className="text-neutral-300">&gt; [Harnix Code Action] &quot;Attempt autonomous verified fix in container&quot;</div>
                <div className="text-neutral-400">&gt; Spawning background harness daemon...</div>
                <div className="text-emerald-400">&gt; Verified patch generated with 0 host disruptions. Preview side-by-side diff.</div>
              </div>
            </div>
          )}
        </div>

      </div>
    </section>
  );
}
