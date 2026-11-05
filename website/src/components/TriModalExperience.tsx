"use client";

import React, { useState } from "react";
import { MonitorIcon, TerminalIcon, CodeIcon } from "./Icons";

export function TriModalExperience() {
  const [activeTab, setActiveTab] = useState<"web" | "cli" | "vscode">("web");

  return (
    <section id="interfaces" className="py-24 relative bg-[#07080C] border-t border-b border-white/5">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-xs font-mono text-cyan-400 mb-4">
            <MonitorIcon className="w-3.5 h-3.5" />
            <span>Tri-Modal Developer Experience</span>
          </div>
          <h2 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            One engine. Any interface you prefer.
          </h2>
          <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
            Harnix powers a unified FastAPI backend that broadcasts real-time execution events seamlessly whether you work from a browser, a terminal, or VS Code.
          </p>

          {/* Interface Tabs */}
          <div className="inline-flex items-center p-1.5 rounded-2xl bg-white/[0.04] border border-white/10 mt-8 gap-2">
            <button
              onClick={() => setActiveTab("web")}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-mono transition-all ${
                activeTab === "web"
                  ? "bg-blue-600 text-white shadow-lg shadow-blue-600/30 font-bold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <MonitorIcon className="w-4 h-4" />
              <span>Web Dashboard</span>
            </button>
            <button
              onClick={() => setActiveTab("cli")}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-mono transition-all ${
                activeTab === "cli"
                  ? "bg-blue-600 text-white shadow-lg shadow-blue-600/30 font-bold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <TerminalIcon className="w-4 h-4" />
              <span>Terminal CLI (`harnix`)</span>
            </button>
            <button
              onClick={() => setActiveTab("vscode")}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-mono transition-all ${
                activeTab === "vscode"
                  ? "bg-blue-600 text-white shadow-lg shadow-blue-600/30 font-bold"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <CodeIcon className="w-4 h-4" />
              <span>VS Code Extension</span>
            </button>
          </div>
        </div>

        {/* Tab Showcase Preview */}
        <div className="glass-panel p-6 sm:p-8 rounded-3xl border border-white/10 shadow-2xl max-w-5xl mx-auto">
          {activeTab === "web" && (
            <div className="space-y-6 animate-fade-in">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <div>
                  <h3 className="text-lg font-bold text-white font-mono flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
                    Web Dashboard • React 19 + SSE Streaming
                  </h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Complete oversight with real-time timeline, interactive diff viewers, and Neo4j graph visualizer.
                  </p>
                </div>
                <span className="text-xs font-mono bg-blue-500/10 text-blue-400 px-2.5 py-1 rounded border border-blue-500/20">
                  http://127.0.0.1:5173
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-4 rounded-xl bg-black/40 border border-white/10 space-y-3 font-mono text-xs">
                  <div className="text-slate-400 font-bold uppercase text-[10px]">
                    Live Execution Timeline
                  </div>
                  <div className="p-2.5 rounded bg-blue-500/10 border border-blue-500/20 text-blue-300">
                    [00:02.1s] task.started: rangesum-repair
                  </div>
                  <div className="p-2.5 rounded bg-white/5 border border-white/10 text-slate-300">
                    [00:04.3s] tool.call: edit_file(path=&apos;rangesum.py&apos;)
                  </div>
                  <div className="p-2.5 rounded bg-yellow-500/10 border border-yellow-500/20 text-yellow-300">
                    [00:05.1s] approval.required: write permission requested
                  </div>
                  <div className="p-2.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-300">
                    [00:08.4s] verification.passed: pytest 2/2 OK
                  </div>
                </div>

                <div className="p-4 rounded-xl bg-black/40 border border-white/10 space-y-3 font-mono text-xs">
                  <div className="text-slate-400 font-bold uppercase text-[10px]">
                    Verified Git Diff Preview
                  </div>
                  <div className="p-3 rounded bg-[#06080B] border border-white/10 text-[11px] leading-relaxed">
                    <div className="text-slate-400">--- a/rangesum.py</div>
                    <div className="text-slate-400">+++ b/rangesum.py</div>
                    <div className="text-slate-400">@@ -1,3 +1,3 @@</div>
                    <div className="text-slate-400"> def range_sum(n: int) -&gt; int:</div>
                    <div className="text-red-400 bg-red-950/30 px-1">- return sum(range(1, n))</div>
                    <div className="text-emerald-400 bg-emerald-950/30 px-1">+ return sum(range(1, n + 1))</div>
                  </div>
                  <button className="w-full py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition-colors">
                    Apply Verified Diff to Workspace
                  </button>
                </div>
              </div>
            </div>
          )}

          {activeTab === "cli" && (
            <div className="space-y-6 animate-fade-in font-mono text-xs">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <div>
                  <h3 className="text-lg font-bold text-white font-mono flex items-center gap-2">
                    <TerminalIcon className="w-4 h-4 text-cyan-400" />
                    Unified CLI (`harnix`)
                  </h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Full terminal agent workflow. Standard library HTTP/SSE client with zero extra dependencies.
                  </p>
                </div>
                <span className="text-xs font-mono bg-cyan-500/10 text-cyan-400 px-2.5 py-1 rounded border border-cyan-500/20">
                  Terminal First
                </span>
              </div>

              <div className="p-5 rounded-xl bg-[#06080B] border border-white/10 text-slate-300 space-y-3 text-[11px] leading-relaxed overflow-x-auto">
                <div className="text-slate-400"># 1. Start the server authorizing target directory</div>
                <div className="text-white">$ harnix serve --allow /workspace/repos</div>
                <div className="text-slate-400 mt-2"># 2. Run a task with an explicit verification command</div>
                <div className="text-white">$ cd /workspace/repos/payment-service</div>
                <div className="text-white">$ harnix &quot;fix stripe webhook idempotency issue in webhooks.py&quot; -v &quot;pytest tests/test_webhooks.py&quot;</div>
                <div className="text-emerald-400 mt-2">[OK] Baseline tests executed: 1 failed</div>
                <div className="text-blue-400">[INFO] Agent proposed edit to webhooks.py</div>
                <div className="text-yellow-400">[?] Approve exact write operation to sandbox? [y/N]: y</div>
                <div className="text-emerald-400">[VERIFIED] 12/12 tests passed in container sandbox.</div>
                <div className="text-white">[?] Apply these changes to /workspace/repos/payment-service? [y/N]: y</div>
                <div className="text-emerald-400">[SUCCESS] Patch applied atomically. Diff saved to history.</div>
              </div>
            </div>
          )}

          {activeTab === "vscode" && (
            <div className="space-y-6 animate-fade-in">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <div>
                  <h3 className="text-lg font-bold text-white font-mono flex items-center gap-2">
                    <CodeIcon className="w-4 h-4 text-indigo-400" />
                    VS Code Companion Extension
                  </h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Native editor integration. Prompt with `@file` references, receive inline approval toasts, and review diffs.
                  </p>
                </div>
                <span className="text-xs font-mono bg-indigo-500/10 text-indigo-400 px-2.5 py-1 rounded border border-indigo-500/20">
                  vscode-extension/
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
                <div className="p-4 rounded-xl bg-black/40 border border-white/10 space-y-2">
                  <div className="text-white font-bold">@ Mention Context</div>
                  <p className="text-slate-400 text-[11px]">
                    Type `@` to pick files or line ranges (`@src/calc.py#10-30`). Auto-excludes secrets (`.env`, `.pem`).
                  </p>
                </div>

                <div className="p-4 rounded-xl bg-black/40 border border-white/10 space-y-2">
                  <div className="text-white font-bold">Inline Approval Cards</div>
                  <p className="text-slate-400 text-[11px]">
                    Risky file modifications and host commands require explicit click-to-approve confirmation in the webview.
                  </p>
                </div>

                <div className="p-4 rounded-xl bg-black/40 border border-white/10 space-y-2">
                  <div className="text-white font-bold">One-Click Diff Review</div>
                  <p className="text-slate-400 text-[11px]">
                    Opens VS Code&apos;s native side-by-side diff editor directly against your workspace baseline commit.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
